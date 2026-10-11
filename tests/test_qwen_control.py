"""Qwen control contracts: unit truth, safe handoff scripts and errors."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "contents/code/qwen_control.py"


def load_helper():
    assert MODULE.is_file(), "The manual Qwen control helper is missing"
    spec = importlib.util.spec_from_file_location("qwen_control", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("load,active,props,want", [
    ("loaded", "inactive", None, "stopped"),
    ("loaded", "active", {"is_sleeping": False}, "running"),
    ("loaded", "active", {"is_sleeping": True}, "sleeping"),
    ("loaded", "active", None, "running"),
    ("loaded", "activating", None, "starting"),
    ("loaded", "deactivating", None, "stopping"),
    ("loaded", "failed", None, "failed"),
    ("not-found", "inactive", None, "unavailable"),
    ("loaded", "unexpected", None, "unknown"),
])
def test_status_uses_qwen_server_not_the_persistent_gateway(monkeypatch, load, active, props, want):
    helper = load_helper()

    def capture(command, **kwargs):
        assert "qwen38-llama-server.service" in command
        assert "qwen38-gateway.service" not in command
        return subprocess.CompletedProcess(command, 0,
            f"LoadState={load}\nActiveState={active}\nSubState=dead\n", "")

    monkeypatch.setattr(helper.subprocess, "run", capture)
    monkeypatch.setattr(helper, "fetch_props", lambda: props)
    assert helper.qwen_status()["state"] == want


def test_status_failure_is_unknown_not_stopped(monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, "", "No user bus"))
    assert helper.qwen_status()["state"] == "unknown"


@pytest.fixture
def scripts(tmp_path, monkeypatch):
    home = tmp_path / "home with spaces"
    directory = home / ".local/bin"
    directory.mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    state = tmp_path / "state"
    calls = tmp_path / "calls"
    state.write_text("stopped")
    for action, target in [("start", "running"), ("stop", "stopped")]:
        script = directory / f"qwen38-{action}"
        script.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                          f"with Path({str(calls)!r}).open('a') as stream: stream.write({action!r}+'\\n')\n"
                          f"Path({str(state)!r}).write_text({target!r})\n")
        script.chmod(0o755)
    return directory, state, calls


@pytest.mark.parametrize("action,initial,want", [("start", "stopped", "running"),
                                                 ("stop", "running", "stopped")])
def test_actions_execute_existing_handoff_script_and_read_back_real_result(scripts, monkeypatch, action, initial, want):
    directory, state, calls = scripts
    state.write_text(initial)
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": state.read_text(), "detail": ""})
    result = helper.run_action(action)
    assert result["ok"] is True
    assert result["state"] == want
    assert calls.read_text().splitlines() == [action]


def test_failed_script_is_reported_even_if_server_changed_state(scripts, monkeypatch):
    directory, state, calls = scripts
    script = directory / "qwen38-start"
    script.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                      f"Path({str(state)!r}).write_text('running')\n"
                      "import sys\nsys.stderr.write('gateway did not start\\n')\nsys.exit(7)\n")
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": state.read_text(), "detail": ""})
    result = helper.run_action("start")
    assert result["ok"] is False
    assert result["state"] == "running"
    assert "7" in result["error"]
    assert "gateway did not start" in result["error"]


def test_successful_script_without_expected_service_state_is_not_success(scripts, monkeypatch):
    directory, state, calls = scripts
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": "stopped", "detail": ""})
    assert helper.run_action("start")["ok"] is False


def test_missing_script_is_a_visible_error(scripts, monkeypatch):
    directory, state, calls = scripts
    (directory / "qwen38-start").unlink()
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": "stopped", "detail": ""})
    result = helper.run_action("start")
    assert result["ok"] is False
    assert "qwen38-start" in result["error"]
    assert not calls.exists()


def test_timeout_is_reported_without_claiming_qwen_is_off(scripts, monkeypatch):
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": "running", "detail": ""})
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(helper.subprocess, "run", timeout)
    result = helper.run_action("stop")
    assert result["ok"] is False
    assert result["state"] == "running"
    assert result["error"]


def test_untrusted_action_cannot_become_a_shell_command(scripts, monkeypatch):
    directory, state, calls = scripts
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": "stopped", "detail": ""})
    result = helper.run_action("start; touch injected")
    assert result["ok"] is False
    assert not calls.exists()


def test_parallel_widget_actions_are_rejected_without_running_a_second_script(scripts, monkeypatch):
    import fcntl
    import os
    directory, state, calls = scripts
    helper = load_helper()
    monkeypatch.setattr(helper, "qwen_status", lambda: {"state": "stopped", "detail": ""})
    with (Path(os.environ["XDG_RUNTIME_DIR"]) / "skybox-qwen-control.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = helper.run_action("start")
    assert result["ok"] is False
    assert result["error"]
    assert not calls.exists()


def test_status_cli_emits_json_without_starting_qwen(scripts, tmp_path):
    assert MODULE.is_file(), "The manual Qwen control helper is missing"
    directory, state, calls = scripts
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(f"#!{sys.executable}\nprint('LoadState=loaded\\nActiveState=inactive\\nSubState=dead')\n")
    systemctl.chmod(0o755)
    import os
    result = subprocess.run([sys.executable, str(MODULE), "status", "--request-id", "8"],
                            env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "stopped"
    assert not calls.exists()
