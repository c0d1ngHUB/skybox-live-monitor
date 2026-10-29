#!/usr/bin/env python3
"""Read-only Hermes live status: running turns/workers, last activity, model, fallbacks.

Only the local SQLite state databases (mode=ro), config.yaml and errors.log(.1) of the
default and every named profile are read.
No model, Hermes API or network is touched, so polling consumes no tokens.
Unknown sources are reported as null, never as zero.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time

STALL_AFTER_S = 300          # running turn without any activity for this long
DELEGATION_MAX_AGE_S = 6 * 3600
TAIL_BYTES = 4 * 1024 * 1024
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ ")


def _scalar(text: str) -> str:
    text = text.split(" #", 1)[0].strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'" else text


def parse_providers(text: str):
    """Dependency-free reader for the two config keys we need (bare python3 has no PyYAML)."""
    primary = None
    fallbacks = set()
    section = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if indent == 0:
            section = line.rstrip(":") if line.endswith(":") else None
            continue
        if section == "model" and indent == 2 and line.startswith("provider:"):
            primary = _scalar(line.split(":", 1)[1])
        elif section == "fallback_providers":
            item = line[2:].strip() if line.startswith("- ") else line
            if item.startswith("provider:"):
                fallbacks.add(_scalar(item.split(":", 1)[1]))
    return (primary, fallbacks) if primary else None


def fallback_providers(config: Path):
    """Return (primary_provider, {fallback providers}) or None when unknown."""
    try:
        text = config.read_text()
    except OSError:
        return None
    try:
        import yaml
        data = yaml.safe_load(text)
        primary = data["model"]["provider"]
        fallbacks = {str(item["provider"]) for item in data.get("fallback_providers") or []}
        return str(primary), fallbacks
    except ImportError:
        return parse_providers(text)
    except Exception:
        return None


def _tail(log: Path):
    size = log.stat().st_size
    with log.open("rb") as handle:
        if size > TAIL_BYTES:
            handle.seek(size - TAIL_BYTES)
        return handle.read().decode("utf-8", "replace")


def count_429(log: Path, since: str):
    """Count distinct 429 error events since the timestamp, including the rotated log.

    Hermes rotates errors.log by size, so a 24 h window can straddle errors.log.1.
    A missing current log is unknown; a missing rotated log just means no rotation yet.
    """
    try:
        texts = [_tail(log)]
    except OSError:
        return None
    try:
        texts.append(_tail(log.with_name(log.name + ".1")))
    except OSError:
        pass
    seconds = set()
    for text in texts:
        for line in text.splitlines():
            match = LOG_LINE.match(line)
            if not match or match[1] < since:
                continue
            if " ERROR " in line and "Streaming failed before delivery" in line and "status 429" in line:
                seconds.add(match[1])
    return len(seconds)


def collect(db: Path, config: Path, errors_log: Path, now: float, errors_since: str) -> dict:
    payload = {"available": False, "tone": "unknown", "stamp": now}
    try:
        db.stat()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    except (OSError, sqlite3.Error):
        payload["error"] = "database_unavailable"
        return payload
    try:
        leases = con.execute(
            "SELECT l.acquired_at, MAX(l.acquired_at, COALESCE(s.last_activity_at, l.acquired_at)) "
            "FROM session_turn_leases l LEFT JOIN sessions s ON s.id = l.conversation_id "
            "WHERE l.expires_at > ?", (now,)).fetchall()
        workers = con.execute(
            "SELECT COUNT(*) FROM worker_executions WHERE status NOT IN ('terminal','completed','failed','cancelled')"
        ).fetchone()[0]
        delegations = con.execute(
            "SELECT COUNT(*) FROM async_delegations WHERE completed_at IS NULL "
            "AND state NOT IN ('completed','error','cancelled') AND dispatched_at > ?",
            (now - DELEGATION_MAX_AGE_S,)).fetchone()[0]
        last = con.execute(
            "SELECT source, model, last_activity_at, last_activity_description FROM sessions "
            "WHERE last_activity_at IS NOT NULL AND source != 'subagent' "
            "ORDER BY last_activity_at DESC LIMIT 1").fetchone()
        usage = con.execute(
            "SELECT session_id, model, billing_provider, api_call_count FROM session_model_usage "
            "WHERE last_seen > ? AND COALESCE(task,'') = ''", (now - 86400,)).fetchall()
    except sqlite3.Error:
        payload["error"] = "query_failed"
        return payload
    finally:
        con.close()

    running = len(leases)
    longest = round(now - min(row[0] for row in leases)) if leases else 0
    age = max(0, round(now - last[2])) if last else None
    # A turn is stuck when *its own* session shows no activity, regardless of other sessions.
    stalled = any(now - row[1] > STALL_AFTER_S for row in leases)

    providers = fallback_providers(config)
    if providers is None:
        fb_sessions = fb_calls = None
        fb_models = []
    else:
        primary, fallbacks = providers
        hits = [row for row in usage if row[2] in fallbacks and row[2] != primary]
        fb_sessions = len({row[0] for row in hits})
        fb_calls = sum(row[3] or 0 for row in hits)
        fb_models = sorted({row[1] for row in hits})

    limits = count_429(errors_log, errors_since)
    warn = stalled or fb_sessions is None or fb_sessions > 0 or limits is None or limits > 0
    payload.update({
        "available": True,
        "tone": "warn" if warn else "ok",
        "_last_at": last[2] if last else None,
        "running_turns": running,
        "longest_turn_s": longest,
        "workers": workers,
        "delegations": delegations,
        "last_activity_age_s": age,
        "last_source": last[0] if last else None,
        "last_description": (last[3] or "")[:80] if last else None,
        "model": last[1] if last else None,
        "stalled": stalled,
        "fallback_24h": fb_sessions,
        "fallback_calls_24h": fb_calls,
        "fallback_models": fb_models,
        "limit_429_24h": limits,
    })
    return payload


def discover_profiles(root: Path):
    """Default profile plus every named profile that has a state database."""
    found = [("default", root)]
    try:
        named = sorted(p for p in (root / "profiles").iterdir() if p.is_dir())
    except OSError:
        named = []
    found += [(p.name, p) for p in named if (p / "state.db").exists()]
    return found


def collect_all(root: Path, now: float, errors_since: str) -> dict:
    results, failed = [], []
    for name, home in discover_profiles(root):
        item = collect(home / "state.db", home / "config.yaml", home / "logs" / "errors.log", now, errors_since)
        if item.get("available"):
            results.append((name, item))
        else:
            failed.append(name)
    if not results:
        return {"available": False, "tone": "unknown", "stamp": now,
                "error": "database_unavailable", "failed_profiles": failed}

    def total(key):
        values = [item[key] for _, item in results]
        return None if any(v is None for v in values) else sum(values)

    latest_name, latest = max(results, key=lambda r: r[1]["_last_at"] or float("-inf"))
    has_last = latest["_last_at"] is not None
    payload = {
        "available": True,
        "stamp": now,
        "profiles": [name for name, _ in results],
        "partial": bool(failed),
        "failed_profiles": failed,
        "running_turns": total("running_turns"),
        "longest_turn_s": max(item["longest_turn_s"] for _, item in results),
        "workers": total("workers"),
        "delegations": total("delegations"),
        "last_activity_age_s": latest["last_activity_age_s"],
        "last_source": latest["last_source"],
        "last_profile": latest_name if has_last else None,
        "last_description": latest["last_description"],
        "model": latest["model"],
        "stalled": any(item["stalled"] for _, item in results),
        "fallback_24h": total("fallback_24h"),
        "fallback_calls_24h": total("fallback_calls_24h"),
        "fallback_models": sorted({m for _, item in results for m in item["fallback_models"]}),
        "limit_429_24h": total("limit_429_24h"),
    }
    warn = bool(failed) or any(item["tone"] != "ok" for _, item in results)
    payload["tone"] = "warn" if warn else "ok"
    return payload


def main() -> None:
    home = Path.home() / ".hermes"
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None, help="Hermes home; aggregates default + named profiles")
    parser.add_argument("--db", default=None, help="single-profile mode (tests)")
    parser.add_argument("--config", default=str(home / "config.yaml"))
    parser.add_argument("--errors-log", default=str(home / "logs" / "errors.log"))
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--errors-since", default=None)
    args = parser.parse_args()
    now = args.now if args.now is not None else time.time()
    since = args.errors_since or datetime.fromtimestamp(now - 86400).strftime("%Y-%m-%d %H:%M:%S")
    if args.db is not None:
        result = collect(Path(args.db).expanduser(), Path(args.config).expanduser(),
                         Path(args.errors_log).expanduser(), now, since)
    else:
        result = collect_all(Path(args.root).expanduser() if args.root else home, now, since)
    result.pop("_last_at", None)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
