#!/usr/bin/env python3
"""Print the longest completed Hermes response duration in the last 24 hours.

This reads the local Hermes SQLite session database only. It never invokes a
model or any Hermes API, so polling it does not consume inference tokens.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import stat
import time


def longest_completed_turn(db_path: Path, now: float) -> int:
    if not db_path.is_file():
        return 0

    cutoff = now - 24 * 60 * 60
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        # Older databases predate these columns; retain their plain-response
        # behavior without hiding tool preambles in current schemas.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
        tool_calls_column = "messages.tool_calls" if "tool_calls" in columns else "NULL"
        finish_column = "messages.finish_reason" if "finish_reason" in columns else "NULL"
        rows = connection.execute(
            f"""WITH RECURSIVE lineage(session_id, root_id, source) AS (
                   SELECT id, id, source
                   FROM sessions
                   WHERE parent_session_id IS NULL
                   UNION ALL
                   SELECT child.id, lineage.root_id, child.source
                   FROM sessions AS child
                   JOIN lineage ON child.parent_session_id = lineage.session_id
               )
               SELECT lineage.root_id, messages.role, messages.timestamp, {tool_calls_column}, {finish_column}
               FROM messages
               JOIN lineage ON lineage.session_id = messages.session_id
               WHERE messages.timestamp >= ? AND messages.timestamp <= ?
                 AND (messages.role != 'assistant' OR (messages.content IS NOT NULL AND trim(messages.content) != ''))
                 AND lineage.source != 'subagent'
               ORDER BY messages.timestamp, messages.id""",
            (cutoff, now),
        )

        active_turn = {}
        longest = 0.0
        for lineage_id, role, timestamp, tool_calls, finish_reason in rows:
            if role == "user":
                active_turn[lineage_id] = timestamp
                continue
            if role != "assistant" or lineage_id not in active_turn:
                continue
            if str(finish_reason or "").lower() not in {"", "stop", "end_turn"}:
                continue
            if tool_calls:
                try:
                    calls = json.loads(tool_calls)
                except (ValueError, TypeError):
                    # Malformed call metadata cannot prove completion.
                    continue
                if calls:
                    continue
            duration = timestamp - active_turn.pop(lineage_id)
            if duration >= 0:
                longest = max(longest, duration)
        return max(0, round(longest))
    finally:
        connection.close()


def service_label(db_path: Path) -> str:
    if db_path.parent.parent.name == "profiles":
        return db_path.parent.name
    return "default"


def discover_databases(root: Path, failed_profiles: list[str] | None = None) -> list[Path]:
    databases = [root / "state.db"]
    failures = failed_profiles if failed_profiles is not None else []
    profiles = root / "profiles"
    try:
        directories = sorted(profiles.iterdir())
    except FileNotFoundError:
        return databases
    except OSError:
        failures.append("profiles")
        return databases
    for profile in directories:
        try:
            if stat.S_ISDIR(profile.stat().st_mode):
                databases.append(profile / "state.db")
        except OSError:
            failures.append(profile.name)
    return databases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--root", default=str(Path.home() / ".hermes"))
    parser.add_argument("--now", type=float, default=None)
    args = parser.parse_args()

    value = 0
    service = ""
    readable = 0
    failed_profiles = []
    try:
        databases = [Path(args.db).expanduser()] if args.db else discover_databases(Path(args.root).expanduser(), failed_profiles)
        current_time = args.now if args.now is not None else time.time()
        for database in databases:
            try:
                # stat raises on permission errors; is_file/glob can silently
                # suppress them and falsely report a complete profile maximum.
                database.stat()
                candidate = longest_completed_turn(database, current_time)
            except FileNotFoundError:
                continue
            except (OSError, sqlite3.Error):
                failed_profiles.append(service_label(database))
                continue
            readable += 1
            if candidate > value:
                value = candidate
                service = service_label(database)
    except (OSError, sqlite3.Error):
        failed_profiles.append("discovery")
    payload = {"seconds": value if readable else None, "service": service}
    if failed_profiles:
        payload.update(partial=bool(readable), failed_profiles=failed_profiles)
    if not readable:
        payload["error"] = "database_unavailable"
    print(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    main()
