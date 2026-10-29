#!/usr/bin/env python3
"""Read-only Hermes live status: running turns/workers, last activity, model, fallbacks.

Only the local SQLite state database (mode=ro), config.yaml and errors.log are read.
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


def fallback_providers(config: Path):
    """Return (primary_provider, {fallback providers}) or None when unknown."""
    try:
        import yaml
        data = yaml.safe_load(config.read_text())
        primary = data["model"]["provider"]
        fallbacks = {str(item["provider"]) for item in data.get("fallback_providers") or []}
        return str(primary), fallbacks
    except Exception:
        return None


def count_429(log: Path, since: str):
    """Count distinct 429 error events (log header lines) since the given timestamp."""
    try:
        size = log.stat().st_size
        with log.open("rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    seconds = set()
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
            "SELECT acquired_at FROM session_turn_leases WHERE expires_at > ?", (now,)).fetchall()
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
    stalled = bool(running and (age is None or age > STALL_AFTER_S))

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


def main() -> None:
    home = Path.home() / ".hermes"
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(home / "state.db"))
    parser.add_argument("--config", default=str(home / "config.yaml"))
    parser.add_argument("--errors-log", default=str(home / "logs" / "errors.log"))
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--errors-since", default=None)
    args = parser.parse_args()
    now = args.now if args.now is not None else time.time()
    since = args.errors_since or datetime.fromtimestamp(now - 86400).strftime("%Y-%m-%d %H:%M:%S")
    result = collect(Path(args.db).expanduser(), Path(args.config).expanduser(),
                     Path(args.errors_log).expanduser(), now, since)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
