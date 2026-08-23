#!/usr/bin/env python3
"""Print the longest completed Hermes response duration in the last 24 hours.

This reads the local Hermes SQLite session database only. It never invokes a
model or any Hermes API, so polling it does not consume inference tokens.
"""

import argparse
from pathlib import Path
import sqlite3
import time


def longest_completed_turn(db_path: Path, now: float) -> int:
    if not db_path.is_file():
        return 0

    cutoff = now - 24 * 60 * 60
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        rows = connection.execute(
            """SELECT session_id, role, content, timestamp
               FROM messages
               WHERE timestamp >= ? AND timestamp <= ?
               ORDER BY session_id, timestamp, id""",
            (cutoff, now),
        )

        active_turn = {}
        longest = 0.0
        for session_id, role, content, timestamp in rows:
            if role == "user":
                active_turn[session_id] = timestamp
                continue
            if role != "assistant" or session_id not in active_turn:
                continue
            if not content or not content.strip():
                continue
            duration = timestamp - active_turn[session_id]
            if duration >= 0:
                longest = max(longest, duration)
        return max(0, round(longest))
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path.home() / ".hermes/state.db"))
    parser.add_argument("--now", type=float, default=None)
    args = parser.parse_args()

    try:
        value = longest_completed_turn(Path(args.db).expanduser(), args.now or time.time())
    except (OSError, sqlite3.Error):
        value = 0
    print(value)


if __name__ == "__main__":
    main()
