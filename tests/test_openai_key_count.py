#!/usr/bin/env python3
"""Tests for the privacy-safe OpenAI credential count helper."""

import importlib.util
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).parents[1] / "contents/code/hermes_openai_keys.py"
SPEC = importlib.util.spec_from_file_location("hermes_openai_keys", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_counts_available_openai_oauth_credentials():
    output = """copilot (1 credentials):
  #1  GITHUB_TOKEN api_key env:GITHUB_TOKEN

openai-codex (3 credentials):
  #1  device_code oauth device_code
  #2  openai-2 oauth device_code rate-limited usage_limit_reached (429)
  #3  openai-codex-oauth-3 oauth device_code

openrouter (1 credentials):
  #1  OPENROUTER_API_KEY api_key env:OPENROUTER_API_KEY
"""
    assert MODULE.count_openai_credentials(output) == (2, 3)


def test_excludes_dead_and_exhausted_credentials():
    output = """openai-codex (3 credentials):
  #1  first oauth device_code dead (401)
  #2  second oauth device_code exhausted (429)
  #3  third oauth device_code
"""
    assert MODULE.count_openai_credentials(output) == (1, 3)


def test_excludes_credentials_explicitly_on_cooldown():
    output = """openai-codex (3 credentials):
  #1  first oauth device_code cooldown (2m left)
  #2  second oauth device_code
  #3  third oauth device_code
"""
    assert MODULE.count_openai_credentials(output) == (2, 3)


def test_missing_provider_reports_zero_credentials():
    assert MODULE.count_openai_credentials("nous (1 credentials):\n  #1 device_code oauth\n") == (0, 0)


def test_format_drift_is_signaled_as_unparseable(monkeypatch, tmp_path, capsys):
    profile_home = tmp_path / ".hermes" / "profiles" / "coordinator"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_MONITOR_PROFILE", raising=False)
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    class Result:
        stdout = "openai-codex (2 credentials):\n  unexpected future format line\n"

    monkeypatch.setattr(MODULE.subprocess, "run", lambda *a, **k: Result())

    assert MODULE.main() == 0
    assert capsys.readouterr().out.strip() == "-1 0"


def test_monitor_pins_no_profile_without_an_explicit_override(monkeypatch, tmp_path):
    """Unset HERMES_MONITOR_PROFILE must not pin a profile name.

    ``hermes auth list`` resolves the credential store the Hermes instance
    actually uses. A hardcoded default profile showed 0/0 whenever that
    profile was absent (parked, renamed, or never created on this machine),
    because ``hermes auth list`` against a missing HERMES_HOME prints nothing.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_MONITOR_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "inherited-home"))

    env = MODULE.hermes_monitor_env()

    assert env["HERMES_HOME"] == str(tmp_path / "inherited-home")


def test_main_skips_auth_list_when_configured_profile_directory_is_missing(monkeypatch, tmp_path, capsys):
    """An explicitly configured profile that does not exist is drift, not a count."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "does-not-exist")
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    called = []
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *a, **k: called.append(a))

    assert MODULE.main() == 0
    assert called == []
    assert capsys.readouterr().out.strip() == "-1 0"


def test_main_invokes_hermes_auth_list_in_configured_profile(monkeypatch, tmp_path, capsys):
    profile_home = tmp_path / ".hermes" / "profiles" / "coordinator"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "coordinator")
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    seen = {}

    class Result:
        stdout = "openai-codex (3 credentials):\n  #1  first oauth device_code\n  #2  second oauth device_code rate-limited\n  #3  third oauth device_code\n"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    assert MODULE.main() == 0
    assert seen["args"] == ["/usr/bin/hermes", "auth", "list"]
    assert seen["env"]["HERMES_HOME"] == str(profile_home)
    assert capsys.readouterr().out.strip() == "2 3"


def test_main_prints_drift_signal_when_hermes_executable_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "coordinator")
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: None)

    assert MODULE.main() == 0
    assert capsys.readouterr().out.strip() == "-1 0"


def test_main_prints_drift_signal_on_subprocess_timeout(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "coordinator")
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    def failing_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="hermes", timeout=15)

    monkeypatch.setattr(MODULE.subprocess, "run", failing_run)

    assert MODULE.main() == 0
    assert capsys.readouterr().out.strip() == "-1 0"


def test_main_prints_drift_signal_when_auth_list_invocation_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "coordinator")
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    def failing_run(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd="hermes")

    monkeypatch.setattr(MODULE.subprocess, "run", failing_run)

    assert MODULE.main() == 0
    assert capsys.readouterr().out.strip() == "-1 0"


def test_main_prints_drift_signal_when_selected_profile_directory_is_absent(monkeypatch, tmp_path, capsys):
    """Missing profile before the call yields -1 0 even if auth list would work."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "absent-profile")
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    class Result:
        stdout = "openai-codex (2 credentials):\n  #1  first oauth device_code\n  #2  second oauth device_code\n"

    monkeypatch.setattr(MODULE.subprocess, "run", lambda *a, **k: Result())

    assert MODULE.main() == 0
    assert capsys.readouterr().out.strip() == "-1 0"


def test_empty_profile_env_is_normalized_to_unset(monkeypatch, tmp_path, capsys):
    """Explicitly empty/whitespace HERMES_MONITOR_PROFILE must not act as a profile.

    os.environ.get(name, default) returns "" (not "coordinator") when the
    variable is set but empty; Path.home()/".hermes"/"profiles"/"" is the
    existing profile root, which made profile_ready pass and Hermes' internal
    default fallback look like a trusted count.

    Semantics: empty or whitespace-only values are normalized to unset, so the
    monitor queries the profile Hermes resolves by itself — identical to leaving
    the variable unset. (Chosen over a -1 0 drift signal: unset already means
    "no override", so this keeps one consistent meaning per input state.)

    The assertion can only mean "no HERMES_HOME was injected" when the ambient
    variable is absent: hermes_monitor_env() inherits os.environ by design, so
    an exported HERMES_HOME (any Hermes session) is passed through and is not
    evidence of a profile override.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(MODULE, "hermes_executable", lambda: "/usr/bin/hermes")

    for empty_value in ("", "   ", "\t"):
        monkeypatch.setenv("HERMES_MONITOR_PROFILE", empty_value)
        assert "HERMES_HOME" not in MODULE.hermes_monitor_env()

    seen = {}

    class Result:
        stdout = "openai-codex (2 credentials):\n  #1  first oauth device_code\n  #2  second oauth device_code rate-limited\n"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    monkeypatch.setenv("HERMES_MONITOR_PROFILE", "")
    assert MODULE.main() == 0
    assert seen["args"] == ["/usr/bin/hermes", "auth", "list"]
    assert "HERMES_HOME" not in seen["env"]
    assert capsys.readouterr().out.strip() == "1 2"
