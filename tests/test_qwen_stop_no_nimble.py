"""The manual Qwen stop path must not revive unused Nimble.

All subprocess boundaries are intercepted. No production services or network
requests are touched in this contract test.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_qwen_stop_does_not_start_check_or_probe_decommissioned_nimble(tmp_path):
    stop = Path.home() / ".local/bin/qwen38-stop"
    if not stop.is_file():
        pytest.skip("Local Qwen handoff script is required")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.jsonl"
    prefix = (f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
              f"with Path({str(calls)!r}).open('a') as f:f.write(json.dumps(sys.argv)+'\\n')\n")
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(prefix + "if 'is-active' in sys.argv:print('inactive' if 'qwen38-llama-server.service' in sys.argv else 'active')\n")
    systemctl.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text(prefix + "print(json.dumps({'models':[]}))\n")
    curl.chmod(0o755)
    result = subprocess.run([str(stop)], env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
                            capture_output=True, text=True, timeout=10)
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    assert commands
    assert not any("ollama-nimble.service" in command or any(":11440" in arg for arg in command)
                   for command in commands), commands
    assert any("hindsight-ollama.service" in command and "start" in command for command in commands)
    assert any("qwen38-gateway.service" in command and "is-active" in command for command in commands)
    assert result.returncode == 0, result.stdout + result.stderr
