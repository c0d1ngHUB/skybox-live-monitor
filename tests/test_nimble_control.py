"""Nimble v3 residency (auto/hold): nimble-ctl policy, widget bridge and real Qt button.

All boundaries are fakes: a local HTTP server stands in for Ollama, systemctl
and the scheduler are stubbed. No production service is touched.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

REPO = Path(__file__).resolve().parents[1]
CTL = REPO / "runtime/nimble-ctl"
BRIDGE = REPO / "contents/code/nimble_control.py"
MODEL = "nimble-v3:9b-nvfp4"


class FakeOllama:
    def __init__(self):
        self.loaded = False
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, payload, code=200):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                models = [{"name": MODEL, "size": 15 << 30, "size_vram": 15 << 30}] if outer.loaded else []
                self._send({"models": models})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, payload.get("keep_alive")))
                if self.path == "/v1/systemone":
                    outer.loaded = True
                elif self.path == "/api/generate" and payload.get("keep_alive") == 0:
                    outer.loaded = False
                self._send({"done": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def env(tmp_path):
    ollama = FakeOllama()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    qwen_state = tmp_path / "qwen_state"
    qwen_state.write_text("inactive")
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
        f"if 'qwen38-ninfer.service' in sys.argv:print(Path({str(qwen_state)!r}).read_text())\n"
        "else:print('active')\n")
    systemctl.chmod(0o755)
    environment = {**os.environ, "NIMBLE_URL": ollama.url,
                   "NIMBLE_MODE_FILE": str(tmp_path / "cfg/mode"),
                   "NIMBLE_SYSTEMCTL": str(systemctl),
                   "NIMBLE_SCHEDULER_STATUS": "http://127.0.0.1:9/status",
                   "NIMBLE_LOAD_TIMEOUT": "10"}
    yield {"ollama": ollama, "env": environment, "qwen": qwen_state, "tmp": tmp_path}
    ollama.close()


def ctl(env, *args):
    result = subprocess.run([sys.executable, str(CTL), *args], capture_output=True, text=True,
                            timeout=30, env=env["env"])
    return result.returncode, (json.loads(result.stdout) if args[0] != "mode" else result.stdout.strip())


def test_default_mode_is_auto_and_apply_never_loads(env):
    rc, out = ctl(env, "status")
    assert rc == 0 and out["mode"] == "auto" and out["loaded"] is False
    rc, out = ctl(env, "apply")
    assert rc == 0 and out["loaded"] is False
    assert env["ollama"].requests == []


def test_hold_loads_pins_and_persists_then_auto_unloads(env):
    rc, out = ctl(env, "hold")
    assert rc == 0 and out["mode"] == "hold" and out["loaded"] is True
    assert out["vram_mib"] == 15 * 1024
    assert ("/v1/systemone", -1) in env["ollama"].requests
    assert not any(path == "/api/generate" and keep != 0 for path, keep in env["ollama"].requests)
    assert ctl(env, "mode")[1] == "hold"
    env["ollama"].loaded = False  # e.g. after a reboot or MOSS window
    rc, out = ctl(env, "apply")
    assert rc == 0 and out["loaded"] is True
    rc, out = ctl(env, "auto")
    assert rc == 0 and out["mode"] == "auto" and out["loaded"] is False
    assert env["ollama"].requests[-1] == ("/api/generate", 0)
    assert ctl(env, "mode")[1] == "auto"


def test_hold_never_loads_while_qwen_owns_the_blackwell(env):
    env["qwen"].write_text("active")
    rc, out = ctl(env, "hold")
    assert rc == 0 and out["mode"] == "hold" and out["loaded"] is False
    assert "Qwen" in out["blocker"]
    rc, out = ctl(env, "apply")
    assert out["loaded"] is False
    assert env["ollama"].requests == []


def test_unreachable_server_reports_unknown_residency(env):
    env["env"]["NIMBLE_URL"] = "http://127.0.0.1:9"
    rc, out = ctl(env, "status")
    assert rc == 0 and out["loaded"] is None


def test_bridge_reports_missing_ctl_as_json(tmp_path):
    result = subprocess.run([sys.executable, str(BRIDGE), "status"], capture_output=True, text=True,
                            env={**os.environ, "NIMBLE_CTL": str(tmp_path / "absent")}, timeout=10)
    payload = json.loads(result.stdout)
    assert result.returncode == 1 and payload["ok"] is False and payload["service"] == "unavailable"


def test_main_places_nimble_button_left_of_qwen():
    main = (REPO / "contents/ui/main.qml").read_text()
    nimble = main.index("NimbleControl {")
    qwen = main.index("QwenControl {")
    assert nimble < qwen
    assert "anchors.right: qwenControl.left" in main[nimble:qwen]


def test_nimble_button_toggles_hold_and_auto_through_real_helper(env):
    runner = shutil.which("qmltestrunner") or "/usr/lib/qt6/bin/qmltestrunner"
    if not Path(runner).is_file():
        pytest.skip("Qt Quick Test runner not installed")
    tmp = env["tmp"]
    home = tmp / "home"
    (home / ".local/bin").mkdir(parents=True)
    installed = home / ".local/bin/nimble-ctl"
    installed.write_text(f"#!{sys.executable}\n" + CTL.read_text().split("\n", 1)[1])
    installed.chmod(0o755)
    runtime = tmp / "runtime"
    runtime.mkdir(mode=0o700)
    qml = f'''import QtQuick
import QtTest
import {json.dumps((REPO / "contents/ui").as_uri())} as Dashboard
Item {{
    width: 300; height: 80
    Dashboard.NimbleControl {{ id: control; anchors.centerIn: parent }}
    TestCase {{
        name: "SkyboxNimbleButton"; when: windowShown
        function test_hold_then_auto() {{
            tryCompare(control, "mode", "auto", 10000)
            var button = findChild(control, "nimbleToggleButton")
            verify(button.enabled)
            verify(button.text.indexOf("laden") >= 0)
            mouseClick(button)
            verify(control.actionBusy)
            verify(!button.enabled)
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 15000)
            compare(control.mode, "hold")
            compare(control.loaded, true)
            verify(button.text.indexOf("entladen") >= 0)
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 15000)
            compare(control.mode, "auto")
            compare(control.loaded, false)
            compare(control.errorMessage, "")
            control.mode = "unknown"
            verify(!button.enabled)
        }}
    }}
}}'''
    testdir = tmp / "qmltests"
    testdir.mkdir()
    (testdir / "tst_nimble.qml").write_text(qml)
    result = subprocess.run([runner, "-input", str(testdir)],
                            env={**env["env"], "HOME": str(home),
                                 "XDG_CACHE_HOME": str(tmp / "cache"),
                                 "XDG_RUNTIME_DIR": str(runtime),
                                 "QT_QPA_PLATFORM": "offscreen"},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    loads = [r for r in env["ollama"].requests if r[0] == "/v1/systemone"]
    assert len(loads) == 1, env["ollama"].requests  # the double click did not load twice
