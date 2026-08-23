#!/usr/bin/env python3
"""Behavior tests for the local, token-free Hermes turn-duration metric."""

from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contents/code/hermes_max_think.py"


class HermesThinkTimeTests(unittest.TestCase):
    def make_db(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                parent_session_id TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                timestamp REAL NOT NULL
            )"""
        )
        return connection

    def run_metric(self, db: Path, now: int = 200000) -> str:
        result = subprocess.run(
            ["python3", str(SCRIPT), "--db", str(db), "--now", str(now)],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def test_reports_longest_completed_user_turn_in_last_24_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            connection = self.make_db(db)
            connection.executemany(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                [("a", "telegram", None), ("b", "telegram", None)],
            )
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "a", "user", "quick", 199000),
                    (2, "a", "assistant", "done", 199012),
                    (3, "a", "user", "long", 199100),
                    (4, "a", "assistant", "", 199110),
                    (5, "a", "tool", "result", 199140),
                    (6, "a", "assistant", "final", 199225),
                    (7, "b", "user", "medium", 199300),
                    (8, "b", "assistant", "final", 199360),
                ],
            )
            connection.commit()
            connection.close()
            self.assertEqual(self.run_metric(db), "125")

    def test_ignores_unfinished_and_older_than_24_hour_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            connection = self.make_db(db)
            connection.execute("INSERT INTO sessions VALUES ('a', 'telegram', NULL)")
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "a", "user", "old", 100000),
                    (2, "a", "assistant", "done", 100500),
                    (3, "a", "user", "unfinished", 199900),
                    (4, "a", "assistant", "", 199950),
                ],
            )
            connection.commit()
            connection.close()
            self.assertEqual(self.run_metric(db), "0")

    def test_finishes_turn_at_first_nonempty_assistant_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            connection = self.make_db(db)
            connection.execute("INSERT INTO sessions VALUES ('a', 'telegram', NULL)")
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "a", "user", "question", 199000),
                    (2, "a", "assistant", "", 199010),
                    (3, "a", "tool", "result", 199020),
                    (4, "a", "assistant", "final", 199030),
                    (5, "a", "assistant", "later housekeeping", 199400),
                ],
            )
            connection.commit()
            connection.close()
            self.assertEqual(self.run_metric(db), "30")

    def test_pairs_turn_across_parent_lineage_and_excludes_subagents(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            connection = self.make_db(db)
            connection.executemany(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                [
                    ("root", "telegram", None),
                    ("child", "telegram", "root"),
                    ("delegate", "subagent", "root"),
                ],
            )
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "root", "user", "question", 199000),
                    (2, "child", "assistant", "final", 199045),
                    (3, "delegate", "user", "task", 199100),
                    (4, "delegate", "assistant", "result", 199900),
                ],
            )
            connection.commit()
            connection.close()
            self.assertEqual(self.run_metric(db), "45")


if __name__ == "__main__":
    unittest.main(verbosity=2)
