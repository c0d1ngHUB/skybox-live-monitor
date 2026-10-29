#!/usr/bin/env python3
"""Behavior tests for the read-only Hermes details collector (synthetic data only)."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import sqlite3
import subprocess
import threading

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contents/code/hermes_details.py"
NOW = 1_800_000_000.0


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def make_root(tmp_path, jobs, executions=()):
    root = tmp_path / "hermes"
    (root / "cron").mkdir(parents=True)
    c = sqlite3.connect(root / "state.db")
    c.executescript("""CREATE TABLE session_model_usage (session_id TEXT, model TEXT, input_tokens INT,
        output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, estimated_cost_usd REAL, last_seen REAL);""")
    c.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?)", [
        ("s1", "claude-opus", 100, 50, 800, 50, 1.5, NOW - 60),
        ("s2", "claude-opus", 0, 0, 0, 0, 0.5, NOW - 3600),
        ("s3", "fb-model", 10, 10, 0, 0, 0.0, NOW - 100),
        ("old", "claude-opus", 10**9, 0, 0, 0, 999, NOW - 90000),
    ])
    c.commit(); c.close()
    (root / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))
    e = sqlite3.connect(root / "cron" / "executions.db")
    e.execute("CREATE TABLE executions (id TEXT, job_id TEXT, status TEXT, claimed_at TEXT)")
    e.executemany("INSERT INTO executions VALUES (?,?,?,?)", executions)
    e.commit(); e.close()
    return root


def job(jid, enabled=True, next_run=NOW + 600, status="ok", last=NOW - 600):
    return {"id": jid, "name": "job " + jid, "enabled": enabled, "next_run_at": iso(next_run) if next_run else None,
            "last_run_at": iso(last), "last_status": status}


def run(tmp_path, root, *extra, api=""):
    out = subprocess.run(["python3", "-I", str(SCRIPT), "--root", str(root), "--now", str(NOW),
                          "--expected-file", str(tmp_path / "expected.json"), "--hindsight-api", api, *extra],
                         text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


def test_tokens_window_and_cache_rate(tmp_path):
    root = make_root(tmp_path, [job("a")])
    t = run(tmp_path, root)["tokens"]
    assert t["available"] is True
    assert t["total"] == 1000 + 20  # the 25 h old session is excluded
    assert t["cache_hit_pct"] == round(100 * 800 / (100 + 800 + 50 + 10))
    assert t["models"][0]["model"] == "claude-opus" and t["models"][0]["sessions"] == 2
    assert t["cost_usd"] == 2.0


def test_tokens_unknown_without_database(tmp_path):
    root = make_root(tmp_path, [job("a")])
    (root / "state.db").unlink()
    assert run(tmp_path, root)["tokens"]["available"] is False


def test_cron_baseline_missing_paused_and_accept(tmp_path):
    root = make_root(tmp_path, [job("a"), job("b", next_run=NOW + 60), job("c")])
    first = run(tmp_path, root)["cron"]
    assert first["baseline_new"] is True and first["enabled"] == 3 and first["expected"] == 3
    assert first["next_run_at"] == pytest.approx(NOW + 60) and first["next_name"] == "job b"
    assert first["failed_24h"] == 0 and first["missing"] == []
    # An update drops job c and pauses job b: both are reported, not silently accepted.
    (root / "cron" / "jobs.json").write_text(json.dumps({"jobs": [job("a"), job("b", enabled=False)]}))
    second = run(tmp_path, root)["cron"]
    assert second["baseline_new"] is False and second["expected"] == 3
    assert second["missing"] == ["job c"] and second["disabled"] == ["job b"]
    # Explicit acceptance makes the new state the target.
    accepted = run(tmp_path, root, "--accept-cron")["cron"]
    assert accepted["expected"] == 1 and accepted["missing"] == [] and accepted["disabled"] == []


def test_cron_failures_from_jobs_and_executions(tmp_path):
    root = make_root(tmp_path, [job("a", status="error"), job("b", status="error", last=NOW - 90000)],
                     executions=[("x1", "b", "failed", iso(NOW - 100)), ("x2", "a", "completed", iso(NOW - 50)),
                                 ("x3", "a", "failed", iso(NOW - 90000))])
    c = run(tmp_path, root)["cron"]
    assert c["failed_24h"] == 2  # job a last_status + execution x1; old entries excluded
    assert c["failed_names"] == ["job a", "job b"]


def test_cron_unreadable_is_unknown(tmp_path):
    root = make_root(tmp_path, [])
    (root / "cron" / "jobs.json").write_text("{broken")
    assert run(tmp_path, root)["cron"] == {"available": False, "error": "jobs_unreadable"}


class FakeHindsight(BaseHTTPRequestHandler):
    stats = {}
    ops = []

    def do_GET(self):
        if self.path.endswith("/stats"):
            body = self.stats
        elif "/operations" in self.path:
            offset = int(self.path.rsplit("offset=", 1)[1])
            body = {"operations": self.ops[offset:offset + 100]}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def hindsight():
    server = HTTPServer(("127.0.0.1", 0), FakeHindsight)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield FakeHindsight, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_hindsight_retains_and_consolidation(tmp_path, hindsight):
    handler, api = hindsight
    handler.stats = {"last_consolidated_at": iso(NOW - 600), "last_memory_write_at": iso(NOW - 30),
                     "pending_consolidation": 4, "failed_consolidation": 0, "failed_operations": 2}
    handler.ops = [{"created_at": iso(NOW - 60 * i)} for i in range(1, 8)] + [{"created_at": iso(NOW - 7200)}]
    h = run(tmp_path, make_root(tmp_path, [job("a")]), api=api)["hindsight"]
    assert h["available"] is True and h["retains_60m"] == 7 and h["retains_capped"] is False
    assert h["consolidation_age_s"] == 600 and h["last_write_age_s"] == 30 and h["warn"] is False


def test_hindsight_stuck_consolidation_warns_and_paging(tmp_path, hindsight):
    handler, api = hindsight
    handler.stats = {"last_consolidated_at": iso(NOW - 8 * 3600), "pending_consolidation": 3, "failed_consolidation": 0}
    handler.ops = [{"created_at": iso(NOW - 1)} for _ in range(600)]
    h = run(tmp_path, make_root(tmp_path, [job("a")]), api=api)["hindsight"]
    assert h["warn"] is True
    assert h["retains_60m"] is None and h["retains_capped"] is True  # never a fake capped count


def test_hindsight_unreachable_is_unknown(tmp_path):
    h = run(tmp_path, make_root(tmp_path, [job("a")]), api="http://127.0.0.1:9")["hindsight"]
    assert h["available"] is False
