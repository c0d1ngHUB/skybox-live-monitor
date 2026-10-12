"""The manual Qwen path must target the NInfer unit after the engine switch.

Every subprocess boundary (systemctl, curl) is intercepted with recording
fakes; no production service and no real endpoint is touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _write_fake(tmp_path: Path, ninfer_active: bool) -> tuple[Path, Path]:
    """Recording fakes for systemctl and curl; returns (fake_bin, calls)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.jsonl"
    record = (
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"with open({str(calls)!r}, 'a') as f:\n"
        "    f.write(json.dumps(sys.argv) + '\\n')\n"
    )
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        record
        + "if 'is-active' in sys.argv:\n"
        + "    units = [a for a in sys.argv if a.endswith('.service')]\n"
        + f"    print('active' if ({ninfer_active!r} or not units or units[0] != 'qwen38-ninfer.service') else 'inactive')\n"
        + "elif 'show' in sys.argv:\n"
        + "    print('LoadState=loaded\\nActiveState=inactive\\nSubState=dead')\n"
    )
    systemctl.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text(record + "print('{\"models\": []}')\n")
    curl.chmod(0o755)
    docker = fake_bin / "docker"
    docker.write_text(record + ("print('running')\n" if ninfer_active else ""))
    docker.chmod(0o755)
    return fake_bin, calls


def _run(script: Path, fake_bin: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script)],
        env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, timeout=30,
    )


def _commands(calls: Path) -> list[list[str]]:
    return [json.loads(line) for line in calls.read_text().splitlines()]


def test_qwen38_start_targets_the_ninfer_unit(tmp_path):
    start = Path.home() / ".local/bin/qwen38-start"
    if not start.is_file():
        pytest.skip("Local Qwen start script is required")
    fake_bin, calls = _write_fake(tmp_path, ninfer_active=True)
    result = _run(start, fake_bin)
    commands = _commands(calls)
    assert any("start" in c and "qwen38-ninfer.service" in c for c in commands), commands
    assert not any("qwen38-llama-server.service" in c for c in commands), commands
    assert result.returncode == 0, result.stdout + result.stderr


def test_qwen38_stop_targets_the_ninfer_unit(tmp_path):
    stop = Path.home() / ".local/bin/qwen38-stop"
    if not stop.is_file():
        pytest.skip("Local Qwen stop script is required")
    fake_bin, calls = _write_fake(tmp_path, ninfer_active=False)
    result = _run(stop, fake_bin)
    commands = _commands(calls)
    assert any("stop" in c and "qwen38-ninfer.service" in c for c in commands), commands
    assert not any("qwen38-llama-server.service" in c for c in commands), commands
    assert any("hindsight-ollama.service" in c and "start" in c for c in commands), commands
    assert result.returncode == 0, result.stdout + result.stderr
