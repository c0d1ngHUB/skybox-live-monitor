"""MOSS-TTS on/off control: JSON helper against fakes, widget placement and the real Qt button.

Every boundary is a fake: a local HTTP server stands in for the GPU scheduler API
(hermes-gpu-scheduler, port 8097) and a script stands in for systemctl. No
production service is touched.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "contents/code/moss_control.py"
UNIT = "hermes-gpu-scheduler.service"

FAKE_SYSTEMCTL = '''#!{python}
import sys
import time
from pathlib import Path

D = Path({directory!r})
args = sys.argv[1:]
if args and args[0] == "--user":
    args = args[1:]
cmd, unit = args[0], args[-1]
state = (D / "state").read_text().strip()
fail = (D / "fail").read_text().strip() if (D / "fail").exists() else ""


def log(line):
    with (D / "calls").open("a") as handle:
        handle.write(line + "\\n")


if cmd == "is-active":
    if unit != {unit!r}:
        print("inactive")
        sys.exit(4)
    print(state)
    sys.exit(0 if state == "active" else 3)
if cmd == "start":
    log("start")
    if fail == "start":
        sys.stderr.write("Failed to start hermes-gpu-scheduler.service: Unit not found.\\n")
        sys.exit(5)
    (D / "state").write_text("active")
    delay = float((D / "start_delay").read_text())
    (D / "ready_at").write_text(str(time.time() + delay))
    sys.exit(0)
if cmd == "stop":
    busy = time.time() < float((D / "busy_until").read_text())
    log("stop busy=%d" % busy)
    if fail == "stop":
        sys.stderr.write("Failed to stop hermes-gpu-scheduler.service: Access denied\\n")
        sys.exit(1)
    (D / "state").write_text("inactive")
    sys.exit(0)
sys.exit(2)
'''


class FakeScheduler:
    """Answers GET /status like the scheduler: 503 unless the fake unit is active and ready."""

    def __init__(self, directory):
        self.directory = directory
        self.sched_state = "HINDSIGHT_ACTIVE"
        self.free_mib = 24035
        self.queue_len = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                active = (outer.directory / "state").read_text().strip() == "active"
                ready = time.time() >= float((outer.directory / "ready_at").read_text())
                if self.path != "/status" or not (active and ready):
                    return self._send(503, {"ok": False})
                busy = time.time() < float((outer.directory / "busy_until").read_text())
                self._send(200, {
                    "ok": True, "version": "0.1.0",
                    "state": "MOSS_GENERATING" if busy else outer.sched_state,
                    "current_job": "job-1" if busy else None,
                    "queue_len": outer.queue_len, "uptime_s": 12.0,
                    "gpu_free_mib": outer.free_mib, "model_loaded": True, "hold_enabled": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        for name, value in {"state": "active", "busy_until": "0", "ready_at": "0",
                            "start_delay": "0"}.items():
            (self.dir / name).write_text(value)
        systemctl = tmp_path / "systemctl"
        systemctl.write_text(FAKE_SYSTEMCTL.format(python=sys.executable,
                                                   directory=str(self.dir), unit=UNIT))
        systemctl.chmod(0o755)
        self.scheduler = FakeScheduler(self.dir)
        self.env = {**os.environ,
                    "MOSS_SCHEDULER_URL": self.scheduler.url,
                    "MOSS_SYSTEMCTL": str(systemctl),
                    "MOSS_CONFIG": str(tmp_path / "scheduler-config.json"),
                    "MOSS_POLL": "0.1", "MOSS_START_WAIT": "5", "MOSS_STOP_WAIT": "5"}

    def set(self, name, value):
        (self.dir / name).write_text(str(value))

    def busy_for(self, seconds):
        self.set("busy_until", time.time() + seconds)

    def calls(self):
        path = self.dir / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def run(self, action, **overrides):
        env = {**self.env, **overrides}
        started = time.monotonic()
        result = subprocess.run([sys.executable, str(HELPER), action], capture_output=True,
                                text=True, timeout=60, env=env)
        lines = result.stdout.strip().splitlines()
        assert len(lines) == 1, f"expected exactly one JSON line: {result.stdout!r} {result.stderr!r}"
        return result.returncode, json.loads(lines[0]), time.monotonic() - started


@pytest.fixture
def moss(tmp_path):
    env = Env(tmp_path)
    yield env
    env.scheduler.close()


# --- status ---------------------------------------------------------------------------------

def test_status_idle_service_is_on_ready_and_has_free_vram(moss):
    rc, out, _ = moss.run("status")
    assert rc == 0 and out["ok"] is True
    assert out["mode"] == "on" and out["service"] == "active"
    assert out["ready"] is True and out["busy"] is False
    assert out["state"] == "HINDSIGHT_ACTIVE" and out["free_mib"] == 24035
    assert out["blocker"] == "" and out["error"] == ""
    assert moss.calls() == []  # read-only


def test_status_reports_a_running_voice_job_as_busy(moss):
    moss.busy_for(60)
    rc, out, _ = moss.run("status")
    assert rc == 0 and out["busy"] is True and out["state"] == "MOSS_GENERATING"


def test_status_counts_a_queued_job_as_busy(moss):
    moss.scheduler.queue_len = 1
    _, out, _ = moss.run("status")
    assert out["busy"] is True and out["queue_len"] == 1


def test_status_when_stopped_is_off_and_never_queries_the_api(moss):
    moss.set("state", "inactive")
    rc, out, _ = moss.run("status", MOSS_SCHEDULER_URL="http://127.0.0.1:9")
    assert rc == 0 and out["ok"] is True
    assert out["mode"] == "off" and out["service"] == "inactive"
    assert out["ready"] is False and out["busy"] is False and out["free_mib"] is None


def test_status_of_a_failed_unit_is_off_with_a_hint(moss):
    moss.set("state", "failed")
    _, out, _ = moss.run("status")
    assert out["mode"] == "off" and out["service"] == "failed"
    assert "fehlgeschlagen" in out["note"]


def test_status_active_unit_without_api_is_on_but_not_ready(moss):
    rc, out, _ = moss.run("status", MOSS_SCHEDULER_URL="http://127.0.0.1:9")
    assert rc == 0 and out["ok"] is True
    assert out["mode"] == "on" and out["ready"] is False and out["free_mib"] is None
    assert "bereit" in out["note"]


def test_status_names_missing_vram_as_blocker_unless_a_job_holds_it(moss):
    moss.scheduler.free_mib = 4000
    _, out, _ = moss.run("status")
    assert "Grafikspeicher" in out["blocker"] and out["required_mib"] == 19000
    moss.busy_for(60)  # the running job owns the memory: not a blocker
    assert moss.run("status")[1]["blocker"] == ""


def test_status_reads_the_required_vram_from_the_scheduler_config(moss):
    moss.scheduler.free_mib = 4000
    (moss.tmp / "scheduler-config.json").write_text(json.dumps({"moss_required_free_mib": 3000}))
    _, out, _ = moss.run("status")
    assert out["required_mib"] == 3000 and out["blocker"] == ""


# The scheduler starts MOSS as exactly [<venv python>, <script>] (moss_proc.py).
SERVICE_ARGV = ["/home/m3kky/src/MOSS-TTS/.venv/bin/python",
                "/home/m3kky/src/hermes-gpu-scheduler/hermes_gpu_scheduler/moss_service.py"]


def fake_proc(moss, argv):
    """A /proc look-alike with one process; the helper scans MOSS_PROC_ROOT."""
    root = moss.tmp / "proc"
    (root / "4242").mkdir(parents=True)
    (root / "4242" / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    (root / "self").mkdir()  # non-numeric entries must be ignored
    return {"MOSS_PROC_ROOT": str(root)}


def test_status_counts_a_running_moss_process_as_busy_even_when_the_api_is_silent(moss):
    env = fake_proc(moss, SERVICE_ARGV)
    _, out, _ = moss.run("status", MOSS_SCHEDULER_URL="http://127.0.0.1:9", **env)
    assert out["mode"] == "on" and out["ready"] is False and out["busy"] is True


@pytest.mark.parametrize("argv", [
    # A shell whose command text merely contains both names (this was a real false positive:
    # the stop waited forever for the shell that ran the test command).
    ["/usr/bin/bash", "-c", "pgrep -f 'hermes-gpu-scheduler.*moss_service.py'"],
    ["/usr/bin/vim", "/home/m3kky/src/hermes-gpu-scheduler/hermes_gpu_scheduler/moss_service.py"],
    ["/usr/bin/grep", "-rn", "moss_service.py", "/home/m3kky/src/hermes-gpu-scheduler"],
    ["/usr/bin/python3", "-m", "pytest", "tests/hermes-gpu-scheduler-moss_service.py"],
])
def test_status_ignores_processes_that_merely_mention_the_moss_service(moss, argv):
    env = fake_proc(moss, argv)
    _, out, _ = moss.run("status", MOSS_SCHEDULER_URL="http://127.0.0.1:9", **env)
    assert out["busy"] is False, argv


def test_stop_never_interrupts_a_moss_process_when_the_api_is_silent(moss):
    env = fake_proc(moss, SERVICE_ARGV)
    rc, out, _ = moss.run("stop", MOSS_SCHEDULER_URL="http://127.0.0.1:9", MOSS_STOP_WAIT="1", **env)
    assert rc == 1 and out["ok"] is False and "läuft noch" in out["error"]
    assert out["mode"] == "on" and moss.calls() == []


def test_stop_is_not_blocked_by_a_shell_that_merely_mentions_the_moss_service(moss):
    env = fake_proc(moss, ["/usr/bin/bash", "-c", "echo hermes-gpu-scheduler moss_service.py"])
    rc, out, _ = moss.run("stop", MOSS_STOP_WAIT="3", **env)
    assert rc == 0 and out["ok"] is True and out["mode"] == "off"
    assert moss.calls() == ["stop busy=0"]


def test_helper_prints_json_even_when_systemctl_is_missing(moss):
    rc, out, _ = moss.run("status", MOSS_SYSTEMCTL=str(moss.tmp / "absent"))
    assert rc == 1 and out["ok"] is False and out["mode"] == "unknown" and out["error"]


# --- start ----------------------------------------------------------------------------------

def test_start_launches_the_unit_and_waits_for_the_api(moss):
    moss.set("state", "inactive")
    moss.set("start_delay", 1.0)
    rc, out, elapsed = moss.run("start")
    assert rc == 0 and out["ok"] is True
    assert out["mode"] == "on" and out["ready"] is True
    assert moss.calls() == ["start"] and elapsed >= 0.8


def test_start_is_idempotent_when_the_service_already_runs(moss):
    rc, out, _ = moss.run("start")
    assert rc == 0 and out["ok"] is True and out["mode"] == "on"
    assert moss.calls() == []


def test_start_reports_when_the_api_never_comes_up(moss):
    moss.set("state", "inactive")
    moss.set("start_delay", 600)
    rc, out, _ = moss.run("start", MOSS_START_WAIT="1")
    assert rc == 1 and out["ok"] is False
    assert out["mode"] == "on" and out["ready"] is False
    assert "nicht bereit" in out["error"]


def test_start_reports_a_systemctl_failure(moss):
    moss.set("state", "inactive")
    moss.set("fail", "start")
    rc, out, _ = moss.run("start")
    assert rc == 1 and out["ok"] is False and out["mode"] == "off"
    assert "Unit not found" in out["error"]


# --- stop -----------------------------------------------------------------------------------

def test_stop_idle_service(moss):
    rc, out, _ = moss.run("stop")
    assert rc == 0 and out["ok"] is True
    assert out["mode"] == "off" and out["ready"] is False
    assert moss.calls() == ["stop busy=0"]


def test_stop_waits_for_a_running_voice_job_before_stopping(moss):
    moss.busy_for(1.5)
    rc, out, elapsed = moss.run("stop", MOSS_STOP_WAIT="20")
    assert rc == 0 and out["ok"] is True and out["mode"] == "off"
    assert moss.calls() == ["stop busy=0"], "the unit was stopped while a voice job was running"
    assert elapsed >= 1.0


def test_stop_gives_up_and_keeps_the_service_when_the_job_never_ends(moss):
    moss.busy_for(600)
    rc, out, elapsed = moss.run("stop", MOSS_STOP_WAIT="1")
    assert rc == 1 and out["ok"] is False
    assert "läuft noch" in out["error"]
    assert out["mode"] == "on" and moss.calls() == []
    assert elapsed < 10


def test_stop_is_idempotent_when_already_stopped(moss):
    moss.set("state", "inactive")
    rc, out, _ = moss.run("stop")
    assert rc == 0 and out["ok"] is True and out["mode"] == "off"
    assert moss.calls() == []


def test_stop_reports_a_systemctl_failure_and_the_real_state(moss):
    moss.set("fail", "stop")
    rc, out, _ = moss.run("stop")
    assert rc == 1 and out["ok"] is False and out["mode"] == "on"
    assert "Access denied" in out["error"]


# --- widget wiring --------------------------------------------------------------------------

def test_main_places_the_moss_button_left_of_qwen_and_nimble_is_gone():
    main = (REPO / "contents/ui/main.qml").read_text()
    moss_at, qwen_at = main.index("MossControl {"), main.index("QwenControl {")
    assert moss_at < qwen_at
    assert "anchors.right: qwenControl.left" in main[moss_at:qwen_at]
    assert "Nimble" not in main and "nimble" not in main
    for leftover in ("contents/ui/NimbleControl.qml", "contents/code/nimble_control.py",
                     "runtime/nimble-ctl", "docs/NIMBLE_CONTROL.md"):
        assert not (REPO / leftover).exists(), leftover


def run_qml(moss, test_body):
    runner = shutil.which("qmltestrunner") or "/usr/lib/qt6/bin/qmltestrunner"
    if not Path(runner).is_file():
        pytest.skip("Qt Quick Test runner not installed")
    runtime = moss.tmp / "runtime"
    runtime.mkdir(mode=0o700)
    qml = f'''import QtQuick
import QtTest
import {json.dumps((REPO / "contents/ui").as_uri())} as Dashboard
Item {{
    width: 300; height: 80
    Dashboard.MossControl {{ id: control; anchors.centerIn: parent }}
    TestCase {{
        name: "SkyboxMossButton"; when: windowShown
{test_body}
    }}
}}'''
    testdir = moss.tmp / "qmltests"
    testdir.mkdir()
    (testdir / "tst_moss.qml").write_text(qml)
    return subprocess.run([runner, "-input", str(testdir)],
                          env={**moss.env, "XDG_CACHE_HOME": str(moss.tmp / "cache"),
                               "XDG_RUNTIME_DIR": str(runtime), "QT_QPA_PLATFORM": "offscreen"},
                          capture_output=True, text=True, timeout=90)


def test_moss_button_stops_and_starts_the_service_through_the_real_helper(moss):
    result = run_qml(moss, '''
        function test_stop_then_start() {
            tryCompare(control, "mode", "on", 10000)
            var button = findChild(control, "mossToggleButton")
            verify(button.enabled)
            verify(button.text.indexOf("beenden") >= 0)
            mouseClick(button)
            verify(control.actionBusy)
            verify(!button.enabled)
            mouseClick(button)  // disabled while busy: must not dispatch a second stop
            tryCompare(control, "actionBusy", false, 15000)
            compare(control.mode, "off")
            verify(button.text.indexOf("starten") >= 0)
            compare(control.errorMessage, "")
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 15000)
            compare(control.mode, "on")
            compare(control.ready, true)
            verify(button.text.indexOf("beenden") >= 0)
            compare(control.errorMessage, "")
            control.mode = "unknown"
            verify(!button.enabled)
        }''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert moss.calls() == ["stop busy=0", "start"], moss.calls()


def test_moss_button_shows_the_helper_error_and_keeps_the_state(moss):
    moss.set("fail", "stop")
    result = run_qml(moss, '''
        function test_failed_stop() {
            tryCompare(control, "mode", "on", 10000)
            var button = findChild(control, "mossToggleButton")
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 15000)
            verify(control.errorMessage.indexOf("Access denied") >= 0)
            verify(button.text.indexOf("Fehler") === 0)
            verify(button.enabled)
            compare(control.mode, "on")
            verify(control.toolTipText.indexOf("Access denied") >= 0)
        }''')
    assert result.returncode == 0, result.stdout + result.stderr
