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


def test_config_is_read_without_pyyaml(tmp_path):
    """The CI image ships a bare python3 without PyYAML; the collector must not depend on it."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("hermes_live_noyaml", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""# comment
model:
  default: primary-model
  provider: "primary-provider"  # inline
  aliases:
    x:
      provider: not-me
fallback_providers:
  - provider: cloud-fallback
    model: fb-1
  - provider: 'second'
    model: fb-2
toolsets:
  - provider: also-not-me
""")
    real = sys.modules.get("yaml")
    sys.modules["yaml"] = None  # makes `import yaml` raise ImportError
    try:
        assert module.fallback_providers(cfg) == ("primary-provider", {"cloud-fallback", "second"})
        cfg.write_text("nothing: here\n")
        assert module.fallback_providers(cfg) is None
    finally:
        if real is not None:
            sys.modules["yaml"] = real
        else:
            sys.modules.pop("yaml", None)


def run_root(tmp_path, root, extra=()):
    out = subprocess.run(
        ["python3", "-I", str(SCRIPT), "--root", str(root), "--now", str(NOW), *extra],
        text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


def make_profile(home: Path, config=CONFIG, errors="", errors_old=None):
    home.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(exist_ok=True)
    (home / "config.yaml").write_text(config)
    (home / "logs" / "errors.log").write_text(errors)
    if errors_old is not None:
        (home / "logs" / "errors.log.1").write_text(errors_old)
    return make_db(home / "state.db")


def test_stuck_turn_is_flagged_even_while_another_session_is_active(tmp_path):
    db = tmp_path / "state.db"
    c = make_db(db)
    c.execute("INSERT INTO sessions VALUES ('tg','telegram','m',?,NULL,?,'tool',NULL)", (NOW - 1300, NOW - 1200))
    c.execute("INSERT INTO session_turn_leases VALUES ('tg','h',?,?)", (NOW - 1250, NOW + 200))
    c.execute("INSERT INTO sessions VALUES ('cr','cron','m',?,NULL,?,'x',NULL)", (NOW - 10, NOW - 3))
    c.commit()
    p = run(tmp_path, db)
    assert p["stalled"] is True and p["tone"] == "warn"
    assert p["last_source"] == "cron"


def test_active_turn_uses_its_own_session_activity(tmp_path):
    db = tmp_path / "state.db"
    c = make_db(db)
    c.execute("INSERT INTO sessions VALUES ('tg','telegram','m',?,NULL,?,'tool',NULL)", (NOW - 1300, NOW - 4))
    c.execute("INSERT INTO session_turn_leases VALUES ('tg','h',?,?)", (NOW - 1250, NOW + 200))
    c.execute("INSERT INTO sessions VALUES ('old','cron','m',?,?,?,'x',NULL)", (NOW - 5000, NOW - 4000, NOW - 4000))
    c.commit()
    p = run(tmp_path, db)
    assert p["stalled"] is False and p["tone"] == "ok"


def test_429_from_rotated_log_inside_window_is_still_counted(tmp_path):
    root = tmp_path / "hermes"
    old = "1970-01-12 08:00:00,000 ERROR [x] agent.chat_completion_helpers: Streaming failed before delivery: status 429 a\n"
    new = "1970-01-12 12:00:00,000 ERROR [x] agent.chat_completion_helpers: Streaming failed before delivery: status 429 b\n"
    make_profile(root, errors=new, errors_old=old).commit()
    p = run_root(tmp_path, root, ("--errors-since", "1970-01-12 00:00:00"))
    assert p["limit_429_24h"] == 2


def test_all_profiles_are_aggregated(tmp_path):
    root = tmp_path / "hermes"
    c = make_profile(root)
    c.execute("INSERT INTO sessions VALUES ('a','telegram','primary-model',1,2,?,'',NULL)", (NOW - 30,))
    c.commit()
    other_cfg = "model:\n  provider: other-primary\nfallback_providers:\n  - provider: other-fb\n"
    e = make_profile(root / "profiles" / "elisabeth", config=other_cfg,
                     errors="1970-01-12 12:00:00,000 ERROR [y] agent.chat_completion_helpers: Streaming failed before delivery: status 429\n")
    e.execute("INSERT INTO sessions VALUES ('b','telegram','eli-model',?,NULL,?,'',NULL)", (NOW - 60, NOW - 5))
    e.execute("INSERT INTO session_turn_leases VALUES ('b','h',?,?)", (NOW - 50, NOW + 100))
    e.execute("INSERT INTO session_model_usage VALUES ('b','fb','other-fb','',4,?)", (NOW - 100,))
    e.commit()
    (root / "profiles" / "coordinator").mkdir()  # profile without state.db: unused, not a failure
    p = run_root(tmp_path, root, ("--errors-since", "1970-01-12 00:00:00"))
    assert p["available"] is True and p["partial"] is False
    assert p["running_turns"] == 1 and p["fallback_24h"] == 1 and p["limit_429_24h"] == 1
    assert p["last_source"] == "telegram" and p["last_profile"] == "elisabeth" and p["model"] == "eli-model"
    assert p["profiles"] == ["default", "elisabeth"] and p["tone"] == "warn"


def test_unreadable_profile_marks_partial_and_warns(tmp_path):
    root = tmp_path / "hermes"
    make_profile(root).commit()
    bad = root / "profiles" / "broken"
    bad.mkdir(parents=True)
    (bad / "state.db").write_text("not a database")
    p = run_root(tmp_path, root)
    assert p["available"] is True and p["partial"] is True
    assert p["failed_profiles"] == ["broken"] and p["tone"] == "warn"
