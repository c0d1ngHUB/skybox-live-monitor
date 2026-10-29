#!/usr/bin/env python3
"""Behavior tests for the read-only Hermes live/fallback collector (synthetic data only)."""

from pathlib import Path
import json
import sqlite3
import subprocess

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contents/code/hermes_live.py"
NOW = 1_000_000.0

CONFIG = """model:
  default: primary-model
  provider: primary-provider
fallback_providers:
  - provider: cloud-fallback
    model: fb-1
"""


def make_db(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, started_at REAL,
        ended_at REAL, last_activity_at REAL, last_activity_description TEXT, parent_session_id TEXT);
    CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT,
        task TEXT, api_call_count INTEGER, last_seen REAL);
    CREATE TABLE session_turn_leases (conversation_id TEXT, holder TEXT, acquired_at REAL, expires_at REAL);
    CREATE TABLE worker_executions (execution_id TEXT, session_id TEXT, kind TEXT, status TEXT);
    CREATE TABLE async_delegations (delegation_id TEXT, state TEXT, dispatched_at REAL, completed_at REAL);
    """)
    return c


def run(tmp_path, db, errors=None, config=CONFIG, extra=()):
    cfg = tmp_path / "config.yaml"
    if config is not None:
        cfg.write_text(config)
    log = tmp_path / "errors.log"
    log.write_text(errors or "")
    out = subprocess.run(
        ["python3", "-I", str(SCRIPT), "--db", str(db), "--config", str(cfg),
         "--errors-log", str(log), "--now", str(NOW), *extra],
        text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


def test_idle_system_reports_ok_with_last_activity(tmp_path):
    db = tmp_path / "state.db"
    c = make_db(db)
    c.execute("INSERT INTO sessions VALUES ('s1','telegram','primary-model',100,200,NOW-12,'done',NULL)".replace("NOW", str(NOW)))
    c.commit()
    p = run(tmp_path, db)
    assert p["available"] is True and p["tone"] == "ok"
    assert p["running_turns"] == 0 and p["workers"] == 0
    assert p["last_activity_age_s"] == 12 and p["last_source"] == "telegram"
    assert p["model"] == "primary-model" and p["fallback_24h"] == 0 and p["limit_429_24h"] == 0


def test_running_turns_workers_and_delegations_are_counted(tmp_path):
    db = tmp_path / "state.db"
    c = make_db(db)
    c.execute("INSERT INTO sessions VALUES ('s1','telegram','primary-model',?,NULL,?, 'tool',NULL)", (NOW - 100, NOW - 5))
    c.execute("INSERT INTO session_turn_leases VALUES ('s1','h',?,?)", (NOW - 90, NOW + 200))
    c.execute("INSERT INTO session_turn_leases VALUES ('old','h',?,?)", (NOW - 900, NOW - 600))  # expired
    c.executemany("INSERT INTO worker_executions VALUES (?,?,?,?)",
                  [("w1", "s1", "compute", "running"), ("w2", "s1", "compute", "terminal")])
    c.executemany("INSERT INTO async_delegations VALUES (?,?,?,?)",
                  [("d1", "running", NOW - 60, None), ("d2", "completed", NOW - 90, NOW - 30),
                   ("d3", "running", NOW - 10 * 86400, None)])  # abandoned long ago
    c.commit()
    p = run(tmp_path, db)
    assert p["running_turns"] == 1 and p["longest_turn_s"] == 90
    assert p["workers"] == 1 and p["delegations"] == 1
    assert p["tone"] == "ok"


def test_running_turn_without_recent_activity_warns_as_possibly_stuck(tmp_path):
    db = tmp_path / "state.db"
    c = make_db(db)
    c.execute("INSERT INTO sessions VALUES ('s1','telegram','primary-model',?,NULL,?,'x',NULL)", (NOW - 2000, NOW - 900))
    c.execute("INSERT INTO session_turn_leases VALUES ('s1','h',?,?)", (NOW - 1000, NOW + 200))
    c.commit()
    p = run(tmp_path, db)
    assert p["stalled"] is True and p["tone"] == "warn"


def test_fallback_use_in_last_24h_ignores_aux_tasks_and_primary_provider(tmp_path):
    db = tmp_path / "state.db"
    c = make_db(db)
    c.execute("INSERT INTO sessions VALUES ('s1','telegram','primary-model',1,2,?,'',NULL)", (NOW - 50,))
    rows = [
        ("s1", "primary-model", "primary-provider", "", 5, NOW - 100),   # normal
        ("s1", "gpt-oss", "cloud-fallback", "title_generation", 1, NOW - 100),  # aux on fallback provider: ignore
        ("s1", "fb-1", "cloud-fallback", "", 3, NOW - 3600),             # real fallback
        ("s0", "fb-1", "cloud-fallback", "", 3, NOW - 3 * 86400),        # too old
    ]
    c.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?)", rows)
    c.commit()
    p = run(tmp_path, db)
    assert p["fallback_24h"] == 1 and p["fallback_calls_24h"] == 3
    assert p["fallback_models"] == ["fb-1"] and p["tone"] == "warn"


def test_unknown_config_makes_fallback_unknown_not_zero(tmp_path):
    db = tmp_path / "state.db"
    make_db(db).commit()
    p = run(tmp_path, db, config="not: [valid")
    assert p["fallback_24h"] is None and p["tone"] == "warn"


def test_429_events_counted_once_and_only_inside_window(tmp_path):
    db = tmp_path / "state.db"
    make_db(db).commit()
    # 1_000_000 s epoch = 1970-01-12 13:46:40 local-independent via explicit year-less parsing is fragile,
    # so the collector is given the window start via --errors-since.
    log = (
        "1970-01-11 10:00:00,000 ERROR [x] agent.chat_completion_helpers: Streaming failed before delivery: status 429 old\n"
        "Traceback...\n  ClaudeAPIError: status 429 continuation\n"
        "1970-01-12 12:00:00,000 ERROR [x] agent.chat_completion_helpers: Streaming failed before delivery: status 429 new\n"
        "1970-01-12 12:00:00,010 WARNING [x] agent.conversation_loop: API call failed status 429\n"
        "1970-01-12 12:05:00,000 ERROR [x] agent.chat_completion_helpers: Streaming failed before delivery: status 500\n"
    )
    p = run(tmp_path, db, errors=log, extra=("--errors-since", "1970-01-12 00:00:00"))
    assert p["limit_429_24h"] == 1 and p["tone"] == "warn"


def test_missing_database_is_unavailable_not_zero(tmp_path):
    p = run(tmp_path, tmp_path / "missing.db")
    assert p["available"] is False and p["tone"] == "unknown"
    assert p.get("running_turns") is None


def test_database_is_opened_read_only(tmp_path):
    db = tmp_path / "state.db"
    make_db(db).commit()
    before = db.read_bytes()
    run(tmp_path, db)
    assert db.read_bytes() == before
    assert "mode=ro" in SCRIPT.read_text()
