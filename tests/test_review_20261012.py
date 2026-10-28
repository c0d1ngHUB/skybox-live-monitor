"""Regression cases from the four October 12 findings; no real GPU handoff."""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / f"contents/code/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("container,props,want", [
    ("", None, "failed"),
    ("exited", None, "failed"),
    ("running", {"is_sleeping": True}, "sleeping"),
    ("running", {"is_sleeping": False}, "running"),
    ("running", None, "running"),
    (None, None, "unknown"),
])
def test_active_oneshot_uses_container_liveness(monkeypatch, container, props, want):
    helper = load("qwen_control")

    def run(command, **kwargs):
        if command[0] == "systemctl":
            return subprocess.CompletedProcess(command, 0,
                "LoadState=loaded\nActiveState=active\nSubState=exited\n", "")
        assert command[0] == "docker"
        assert "^/qwen38-ninfer$" in " ".join(command)
        return subprocess.CompletedProcess(command, 1 if container is None else 0,
            "" if container is None else container + "\n", "")

    monkeypatch.setattr(helper.subprocess, "run", run)
    monkeypatch.setattr(helper, "fetch_props", lambda: props)
    assert helper.qwen_status()["state"] == want


@pytest.mark.parametrize("code,out,want", [
    (1, "", "unknown"), (0, "unexpected", "unknown"),
    (0, "active", "running"), (3, "inactive", "down"),
    (3, "failed", "down"), (4, "unknown", "unknown"),
])
def test_gateway_query_only_reports_confirmed_states(monkeypatch, code, out, want):
    helper = load("ai_services_status")
    monkeypatch.setattr(helper.subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, code, out, ""))
    assert helper.hermes_gateway_status().state == want


def make_db(path, duration):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE sessions(id TEXT,source TEXT,parent_session_id TEXT);"
            "CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,timestamp REAL);")
        if duration is not None:
            connection.execute("INSERT INTO sessions VALUES('s','telegram',NULL)")
            connection.executemany("INSERT INTO messages VALUES(?,?,?,?,?)", [
                (1, 's', 'user', 'q', 199000), (2, 's', 'assistant', 'a', 199000 + duration)])


def timing(root):
    result = subprocess.run([sys.executable, str(REPO / "contents/code/hermes_max_think.py"),
        "--root", str(root), "--now", "200000"], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_broken_profile_preserves_valid_maximum(tmp_path):
    make_db(tmp_path / "state.db", 125)
    broken = tmp_path / "profiles/broken/state.db"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not sqlite")
    result = timing(tmp_path)
    assert result["seconds"] == 125
    assert result["service"] == "default"
    assert result["partial"] is True


def test_broken_default_does_not_hide_valid_profile(tmp_path):
    (tmp_path / "state.db").write_bytes(b"not sqlite")
    make_db(tmp_path / "profiles/coder/state.db", 90)
    result = timing(tmp_path)
    assert result["seconds"] == 90
    assert result["service"] == "coder"
    assert result["partial"] is True


def test_unreadable_databases_are_not_a_zero_duration(tmp_path):
    (tmp_path / "state.db").write_bytes(b"not sqlite")
    result = timing(tmp_path)
    assert result["seconds"] is None
    assert result["error"]


def test_readable_empty_database_is_a_real_zero(tmp_path):
    make_db(tmp_path / "state.db", None)
    assert timing(tmp_path) == {"seconds": 0, "service": ""}


@pytest.mark.parametrize("blocked", ["profile", "profiles"])
def test_unreadable_profile_directories_mark_maximum_partial(tmp_path, blocked):
    if os.geteuid() == 0:
        pytest.skip("Root bypasses directory permissions")
    make_db(tmp_path / "state.db", 125)
    make_db(tmp_path / "profiles/blocked/state.db", 200)
    target = tmp_path / ("profiles/blocked" if blocked == "profile" else "profiles")
    target.chmod(0)
    try:
        result = timing(tmp_path)
    finally:
        target.chmod(0o700)
    assert result["seconds"] == 125
    assert result["partial"] is True


@pytest.mark.parametrize("payload,unavailable,partial", [
    ({"seconds": None, "error": "database_unavailable"}, True, False),
    ({"seconds": 125, "service": "default", "partial": True}, False, True),
])
def test_timing_payload_marks_unavailable_and_partial(payload, unavailable, partial):
    source = (REPO / "contents/ui/main.qml").read_text()
    block = source.split("id: hermesThinkSource", 1)[1].split("id: obsHealthSource", 1)[0]
    body = block.split("onNewData: function(source, data) {", 1)[1].rsplit("\n        }", 1)[0]
    script = "var root={hermesMaxThinkSeconds:999,hermesMaxThinkService:'old',hermesMaxThinkUnavailable:false,hermesMaxThinkPartial:false};var buffer='',source='';function disconnectSource(){};"
    script += "function handle(data){" + body + "}\nhandle(" + json.dumps({"stdout": json.dumps(payload), "exit code": 0}) + ");console.log(JSON.stringify(root));"
    result = json.loads(subprocess.check_output(["node", "-e", script], text=True))
    assert result["hermesMaxThinkUnavailable"] is unavailable
    assert result["hermesMaxThinkPartial"] is partial


def fake_runtime(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "container"
    state.write_text("absent")
    docker = bindir / "docker"
    docker.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
        f"state=Path({str(state)!r})\n"
        "action=sys.argv[1]\n"
        "if action in ('rm','stop'): state.write_text('absent')\n"
        "elif action=='run': state.write_text('running');print('fake-id')\n"
        "elif action=='inspect': print('true' if state.read_text()=='running' else 'false')\n"
        "elif action=='container' and state.read_text()!='absent':print(state.read_text())\n")
    docker.chmod(0o755)
    curl = bindir / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n")
    curl.chmod(0o755)
    return bindir, state


def test_ninfer_readiness_failure_removes_started_container(tmp_path):
    wrapper = Path.home() / ".local/bin/qwen38-ninfer"
    if not wrapper.is_file():
        pytest.skip("Local NInfer wrapper required")
    bindir, state = fake_runtime(tmp_path)
    copy = tmp_path / "wrapper"
    copy.write_bytes(wrapper.read_bytes())
    result = subprocess.run(["/usr/bin/bash", str(copy)], capture_output=True, text=True,
        env={**os.environ, "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
             "QWEN_READY_TIMEOUT": "0"}, timeout=10)
    assert result.returncode != 0
    assert "not ready" in result.stderr
    assert state.read_text() == "absent"


def test_unit_failed_start_executes_container_cleanup(tmp_path):
    unit = Path.home() / ".config/systemd/user/qwen38-ninfer.service"
    if not unit.is_file() or shutil.which("systemd-run") is None:
        pytest.skip("Local NInfer unit and systemd required")
    probe = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True)
    if probe.returncode:
        pytest.skip("User systemd manager unavailable")
    bindir, state = fake_runtime(tmp_path)
    state.write_text("running")
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read(unit)
    properties = []
    for key in ("Type", "RemainAfterExit", "ExecStop", "ExecStopPost"):
        value = config["Service"].get(key)
        if value:
            value = value.replace("/usr/bin/docker", str(bindir / "docker"))
            properties += ["--property", key + "=" + value]
    result = subprocess.run(["systemd-run", "--user", "--wait", "--collect",
        "--unit=skybox-cleanup-test-" + uuid.uuid4().hex, *properties, "/usr/bin/false"],
        capture_output=True, text=True, timeout=15)
    assert result.returncode != 0  # The isolated startup deliberately fails.
    assert state.read_text() == "absent", result.stdout + result.stderr


def test_start_recovers_active_unit_with_missing_container(tmp_path):
    script = Path(__file__).resolve().parents[1] / "runtime/qwen38-start"
    if not script.is_file():
        pytest.skip("Local Qwen start script required")
    bindir, state = fake_runtime(tmp_path)
    unit = tmp_path / "unit"
    unit.write_text("active")
    ctl = bindir / "systemctl"
    ctl.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
        f"unit=Path({str(unit)!r});state=Path({str(state)!r})\n"
        "if 'qwen38-ninfer.service' in sys.argv:\n"
        " if 'is-active' in sys.argv:print(unit.read_text());sys.exit(0 if unit.read_text()=='active' else 3)\n"
        " if 'stop' in sys.argv:unit.write_text('inactive');state.write_text('absent')\n"
        " if 'start' in sys.argv and unit.read_text()!='active':unit.write_text('active');state.write_text('running')\n"
        "elif 'is-active' in sys.argv:print('active')\n")
    ctl.chmod(0o755)
    curl = bindir / "curl"
    curl.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
        f"sys.exit(0 if Path({str(state)!r}).read_text()=='running' else 22)\n")
    curl.chmod(0o755)
    copy = tmp_path / "start"
    # Shorten only the deadline; all state/control logic is the real script.
    copy.write_text(script.read_text().replace("READY_TIMEOUT=180", "READY_TIMEOUT=0"))
    result = subprocess.run(["/usr/bin/bash", str(copy)], capture_output=True, text=True,
        env={**os.environ, "PATH": str(bindir) + os.pathsep + os.environ["PATH"]}, timeout=10)
    assert result.returncode == 0, result.stderr
    assert state.read_text() == "running"
