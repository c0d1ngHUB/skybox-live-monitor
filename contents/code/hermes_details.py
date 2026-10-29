#!/usr/bin/env python3
"""Read-only detail sections for the Hermes live panel dropdown.

Three independent sections, each with its own ``available`` flag so one broken
source never blanks the others and never turns into an invented zero:

* tokens    - token use and cache-hit rate of sessions active in the last 24 h
              (state.db, mode=ro; default + named profiles)
* cron      - enabled jobs vs. an accepted target set, next run, failures 24 h
              (cron/jobs.json + cron/executions.db)
* hindsight - retains in the last 60 min, time since last consolidation,
              pending/failed consolidation (local Hindsight HTTP API, GET only)

No model is called, so polling consumes no tokens.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.request

HINDSIGHT_API = os.environ.get("HINDSIGHT_API_URL", "http://127.0.0.1:9177").rstrip("/")
HINDSIGHT_BANK = os.environ.get("HINDSIGHT_HEALTH_BANK", "hermes")
CONSOLIDATION_WARN_S = 6 * 3600
RETAIN_PAGES = 5
FAILED_EXEC = ("failed", "unknown")


def parse_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def profile_homes(root: Path):
    homes = [("default", root)]
    try:
        named = sorted(p for p in (root / "profiles").iterdir() if p.is_dir())
    except OSError:
        named = []
    return homes + [(p.name, p) for p in named]


# --- tokens -----------------------------------------------------------------

def collect_tokens(root: Path, now: float) -> dict:
    """Session-level totals: Hermes stores no per-message token counts, so a
    session that was active in the window counts with its full total."""
    models: dict[str, dict] = {}
    failed, read = [], []
    for name, home in profile_homes(root):
        db = home / "state.db"
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                rows = con.execute(
                    "SELECT model, COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
                    "COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cache_write_tokens),0), "
                    "COALESCE(SUM(estimated_cost_usd),0), COUNT(DISTINCT session_id) "
                    "FROM session_model_usage WHERE last_seen > ? GROUP BY model",
                    (now - 86400,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            failed.append(name)
            continue
        read.append(name)
        for model, inp, out, cr, cw, cost, sessions in rows:
            m = models.setdefault(model or "?", {"model": model or "?", "input": 0, "output": 0,
                                                 "cache_read": 0, "cache_write": 0, "cost_usd": 0.0,
                                                 "sessions": 0})
            m["input"] += inp; m["output"] += out; m["cache_read"] += cr
            m["cache_write"] += cw; m["cost_usd"] += cost; m["sessions"] += sessions
    if not read:
        return {"available": False, "failed_profiles": failed}
    rows = sorted(models.values(), key=lambda m: -(m["input"] + m["output"] + m["cache_read"] + m["cache_write"]))
    for m in rows:
        m["total"] = m["input"] + m["output"] + m["cache_read"] + m["cache_write"]
        m["cost_usd"] = round(m["cost_usd"], 2)
    prompt = sum(m["input"] + m["cache_read"] + m["cache_write"] for m in rows)
    total = sum(m["total"] for m in rows)
    return {
        "available": True,
        "partial": bool(failed),
        "failed_profiles": failed,
        "total": total,
        "output": sum(m["output"] for m in rows),
        "cache_hit_pct": round(100 * sum(m["cache_read"] for m in rows) / prompt) if prompt else None,
        "cost_usd": round(sum(m["cost_usd"] for m in rows), 2),
        "models": rows,
    }


# --- cron -------------------------------------------------------------------

def load_expected(path: Path):
    try:
        data = json.loads(path.read_text())
        return {str(k): str(v) for k, v in data["jobs"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def save_expected(path: Path, jobs: dict, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"accepted_at": now, "jobs": jobs}, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def collect_cron(root: Path, now: float, expected_file: Path, accept: bool = False) -> dict:
    jobs_file = root / "cron" / "jobs.json"
    try:
        jobs = json.loads(jobs_file.read_text())["jobs"]
        if not isinstance(jobs, list):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False, "error": "jobs_unreadable"}
    enabled = {str(j.get("id")): str(j.get("name") or j.get("id")) for j in jobs if j.get("enabled")}
    present = {str(j.get("id")) for j in jobs}

    baseline_new = False
    expected = None if accept else load_expected(expected_file)
    if expected is None:
        try:
            save_expected(expected_file, enabled, now)
            expected, baseline_new = dict(enabled), True
        except OSError:
            expected = None
    missing = sorted(name for jid, name in (expected or {}).items() if jid not in present)
    disabled = sorted(name for jid, name in (expected or {}).items() if jid in present and jid not in enabled)

    next_run, next_name = None, None
    failures = []
    for j in jobs:
        if j.get("enabled"):
            ts = parse_ts(j.get("next_run_at"))
            if ts is not None and (next_run is None or ts < next_run):
                next_run, next_name = ts, j.get("name")
        last = parse_ts(j.get("last_run_at"))
        if last is not None and now - last <= 86400 and j.get("last_status") not in (None, "ok"):
            failures.append(str(j.get("name")))

    exec_failed = None
    try:
        con = sqlite3.connect(f"file:{root / 'cron' / 'executions.db'}?mode=ro", uri=True, timeout=2)
        try:
            names = {str(j.get("id")): str(j.get("name")) for j in jobs}
            exec_failed = 0
            for job_id, claimed in con.execute(
                    "SELECT job_id, claimed_at FROM executions WHERE status IN (?,?)", FAILED_EXEC):
                ts = parse_ts(claimed)
                if ts is not None and now - ts <= 86400:
                    exec_failed += 1
                    failures.append(names.get(job_id, job_id))
        finally:
            con.close()
    except sqlite3.Error:
        exec_failed = None

    return {
        "available": True,
        "enabled": len(enabled),
        "expected": len(expected) if expected is not None else None,
        "baseline_new": baseline_new,
        "missing": missing,
        "disabled": disabled,
        "next_run_at": next_run,
        "next_name": next_name,
        "failed_24h": None if exec_failed is None else len(failures),
        "failed_names": sorted(set(failures))[:5],
        "expected_file": str(expected_file),
    }


# --- hindsight --------------------------------------------------------------

def fetch_json(url: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_hindsight(api: str, bank: str, now: float) -> dict:
    try:
        stats = fetch_json(f"{api}/v1/default/banks/{bank}/stats")
    except (OSError, ValueError, urllib.error.URLError):
        return {"available": False, "error": "api_unreachable"}
    retains = 0
    complete = False
    try:
        for page in range(RETAIN_PAGES):
            data = fetch_json(f"{api}/v1/default/banks/{bank}/operations?type=retain"
                              f"&exclude_parents=true&limit=100&offset={page * 100}")
            ops = data.get("operations") or []
            older = False
            for op in ops:
                ts = parse_ts(op.get("created_at"))
                if ts is None:
                    continue
                if now - ts <= 3600:
                    retains += 1
                else:
                    older = True
            if older or len(ops) < 100:
                complete = True
                break
    except (OSError, ValueError, urllib.error.URLError, AttributeError):
        complete = False
    last_cons = parse_ts(stats.get("last_consolidated_at"))
    last_write = parse_ts(stats.get("last_memory_write_at"))
    pending = stats.get("pending_consolidation")
    failed = stats.get("failed_consolidation")
    cons_age = None if last_cons is None else max(0, round(now - last_cons))
    warn = (failed is None or failed > 0
            or (cons_age is None)
            or (pending and cons_age > CONSOLIDATION_WARN_S))
    return {
        "available": True,
        "bank": bank,
        "retains_60m": retains if complete else None,
        "retains_capped": not complete,
        "consolidation_age_s": cons_age,
        "last_write_age_s": None if last_write is None else max(0, round(now - last_write)),
        "pending_consolidation": pending,
        "failed_consolidation": failed,
        "failed_operations": stats.get("failed_operations"),
        "warn": bool(warn),
    }


def main(argv=None) -> int:
    home = Path.home() / ".hermes"
    default_expected = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) \
        / "skybox-live-monitor" / "hermes_cron_expected.json"
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(home))
    parser.add_argument("--expected-file", default=str(default_expected))
    parser.add_argument("--accept-cron", action="store_true",
                        help="accept the current enabled cron jobs as the target set")
    parser.add_argument("--hindsight-api", default=HINDSIGHT_API)
    parser.add_argument("--bank", default=HINDSIGHT_BANK)
    parser.add_argument("--now", type=float, default=None)
    args = parser.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    root = Path(args.root).expanduser()
    payload = {
        "stamp": now,
        "tokens": collect_tokens(root, now),
        "cron": collect_cron(root, now, Path(args.expected_file).expanduser(), args.accept_cron),
        "hindsight": collect_hindsight(args.hindsight_api.rstrip("/"), args.bank, now)
        if args.hindsight_api else {"available": False, "error": "disabled"},
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
