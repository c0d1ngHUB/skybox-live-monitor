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
            """WITH RECURSIVE lineage(session_id, root_id, source) AS (
                   SELECT id, id, source
                   FROM sessions
                   WHERE parent_session_id IS NULL
                   UNION ALL
                   SELECT child.id, lineage.root_id, child.source
                   FROM sessions AS child
                   JOIN lineage ON child.parent_session_id = lineage.session_id
               )
               SELECT lineage.root_id, messages.role, messages.content,
                      messages.timestamp, messages.id
               FROM messages
               JOIN lineage ON lineage.session_id = messages.session_id
               WHERE messages.timestamp >= ? AND messages.timestamp <= ?
                 AND lineage.source != 'subagent'
               ORDER BY messages.timestamp, messages.id""",
            (cutoff, now),
        )

        active_turn = {}
        longest = 0.0
        for lineage_id, role, content, timestamp, _message_id in rows:
            if role == "user":
                active_turn[lineage_id] = timestamp
                continue
            if role != "assistant" or lineage_id not in active_turn:
                continue
            if not content or not content.strip():
                continue
            duration = timestamp - active_turn.pop(lineage_id)
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
