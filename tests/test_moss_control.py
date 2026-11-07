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
    # systemd ends the unit's control group: a fake MOSS child of the unit goes with it,
    # unless the test simulates a survivor.
    kill = D / "kill_on_stop"
    if kill.exists() and not (D / "survivor").exists():
        import shutil
        shutil.rmtree(kill.read_text().strip(), ignore_errors=True)
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
        self.resident = None  # None: old scheduler without resident mode; else a "moss" dict
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
                status = {
                    "ok": True, "version": "0.1.0",
                    "state": "MOSS_GENERATING" if busy else outer.sched_state,
                    "current_job": "job-1" if busy else None,
                    "queue_len": outer.queue_len, "uptime_s": 12.0,
                    "gpu_free_mib": outer.free_mib, "model_loaded": True, "hold_enabled": True}
                if outer.resident is not None:
                    moss = dict(outer.resident() if callable(outer.resident) else outer.resident)
                    status.update({"moss_resident": True, "moss": moss,
                                   "moss_loaded": moss.get("state") == "loaded"})
                self._send(200, status)

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
        empty_proc = tmp_path / "proc-empty"  # never the host's /proc: a real MOSS may run here
        empty_proc.mkdir()
        self.env = {**os.environ,
                    "MOSS_SCHEDULER_URL": self.scheduler.url,
                    "MOSS_SYSTEMCTL": str(systemctl),
                    "MOSS_CONFIG": str(tmp_path / "scheduler-config.json"),
                    "MOSS_STATE_FILE": str(tmp_path / "scheduler-state.json"),  # absent: no recent job
                    "MOSS_PROC_ROOT": str(empty_proc),
                    "MOSS_NVIDIA_SMI": str(tmp_path / "no-nvidia-smi"),  # absent: no memory figure
                    "MOSS_POLL": "0.1", "MOSS_START_WAIT": "5", "MOSS_STOP_WAIT": "5",
                    "MOSS_LOAD_WAIT": "5", "MOSS_UNLOAD_WAIT": "1"}

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
    ["/usr/bin/vim", "/home/m3kky/src/hermes-gpu-scheduler/scripts/moss_tts_client.py"],
    ["/usr/bin/bash", "-c", "python3 moss_tts_client.py --help"],
    ["/usr/bin/python3", "-m", "pytest", "tests/test_moss_tts_client.py"],
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


# The Hermes command provider runs this client per synthesis; it asks the scheduler for the
# result only every 5 s.
CLIENT_ARGV = ["/usr/bin/python3", "/home/m3kky/src/hermes-gpu-scheduler/scripts/moss_tts_client.py",
               "--text-file", "/tmp/in.txt", "--out", "/tmp/out.wav", "--language", "German"]


def test_status_counts_a_tts_client_that_still_waits_for_its_result_as_busy(moss):
    env = fake_proc(moss, CLIENT_ARGV)
    _, out, _ = moss.run("status", **env)
    assert out["busy"] is True


def test_stop_waits_for_a_tts_client_to_collect_its_result(moss):
    """Live finding: the job was done, but the client polls every 5 s. Stopping the scheduler in
    between left the client polling a dead API until its 900 s timeout and lost the reply."""
    env = fake_proc(moss, CLIENT_ARGV)
    rc, out, _ = moss.run("stop", MOSS_STOP_WAIT="1", **env)
    assert rc == 1 and out["ok"] is False and "läuft noch" in out["error"]
    assert out["mode"] == "on" and moss.calls() == []


def write_activity(moss, seconds_ago):
    """The scheduler persists {"state", "ts"} at every transition (scheduler-state.json)."""
    path = moss.tmp / "scheduler-state.json"
    path.write_text(json.dumps({"state": "HINDSIGHT_ACTIVE", "ts": time.time() - seconds_ago}))
    return {"MOSS_STATE_FILE": str(path)}


def test_stop_waits_out_the_quiet_window_after_the_last_voice_activity(moss):
    """A long reply is several jobs in a row with short gaps, and the client collects each
    result only every 5 s: do not stop right after a job ended."""
    env = write_activity(moss, 0.2)
    rc, out, elapsed = moss.run("stop", MOSS_QUIET="3", MOSS_STOP_WAIT="20", **env)
    assert rc == 0 and out["ok"] is True and out["mode"] == "off"
    assert elapsed >= 2.0, elapsed
    assert moss.calls() == ["stop busy=0"]


def test_stop_gives_up_when_voice_activity_never_calms_down(moss):
    env = write_activity(moss, 0.0)
    rc, out, _ = moss.run("stop", MOSS_QUIET="60", MOSS_STOP_WAIT="1", **env)
    assert rc == 1 and out["ok"] is False and "läuft noch" in out["error"]
    assert out["mode"] == "on" and moss.calls() == []


def test_stop_does_not_wait_when_the_last_voice_activity_is_long_ago(moss):
    env = write_activity(moss, 3600)
    rc, out, elapsed = moss.run("stop", MOSS_QUIET="30", **env)
    assert rc == 0 and out["mode"] == "off" and elapsed < 3, elapsed


@pytest.mark.parametrize("content", ["{not json", "[]", '{"ts": "gestern"}', '{"ts": null}', '{}'])
def test_stop_ignores_a_corrupt_state_file(moss, content):
    (moss.tmp / "scheduler-state.json").write_text(content)
    rc, out, elapsed = moss.run("stop", MOSS_QUIET="30")
    assert rc == 0 and out["mode"] == "off" and elapsed < 3, (content, elapsed)


def test_stop_does_not_trust_a_timestamp_from_the_future(moss):
    env = write_activity(moss, -3600)  # clock jump: never wait for it
    rc, out, elapsed = moss.run("stop", MOSS_QUIET="30", **env)
    assert rc == 0 and out["mode"] == "off" and elapsed < 3, elapsed


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


# --- resident mode (scheduler keeps MOSS loaded while the unit runs, since 2026-11-07) ----------

LOADED = {"state": "loaded", "reason": "", "alive": True, "pid": 4242, "vram_mib": 17012}


def resident_child(moss):
    """A loaded MOSS child of the unit; the fake `systemctl stop` removes it like systemd."""
    env = fake_proc(moss, SERVICE_ARGV)
    moss.set("kill_on_stop", moss.tmp / "proc" / "4242")
    return env


def test_resident_status_reports_a_loaded_moss_as_idle_not_busy(moss):
    moss.scheduler.resident = LOADED
    moss.scheduler.free_mib = 7300  # MOSS itself holds the memory: that is no shortage
    env = resident_child(moss)
    rc, out, _ = moss.run("status", **env)
    assert rc == 0 and out["mode"] == "on" and out["ready"] is True
    assert out["resident"] is True and out["loaded"] is True
    assert out["busy"] is False, "a loaded, idle MOSS is not a running voice reply"
    assert out["blocker"] == ""
    assert "geladen" in out["note"] and "16,6 GiB" in out["note"]


def test_resident_status_names_the_qwen_wait(moss):
    moss.scheduler.resident = {"state": "waiting", "reason": "qwen", "alive": False, "pid": None}
    moss.scheduler.free_mib = 1200
    rc, out, _ = moss.run("status")
    assert rc == 0 and out["loaded"] is False and out["moss_state"] == "waiting"
    assert out["moss_reason"] == "qwen" and "Qwen" in out["note"]


def test_resident_status_without_api_still_counts_a_moss_child_as_busy(moss):
    env = resident_child(moss)
    _, out, _ = moss.run("status", MOSS_SCHEDULER_URL="http://127.0.0.1:9", **env)
    assert out["busy"] is True and out["resident"] is False


def test_resident_stop_unloads_and_confirms_the_freed_memory(moss):
    moss.scheduler.resident = LOADED
    env = resident_child(moss)
    smi = moss.tmp / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 'GPU-ad1c68f7-9555-3617-9230-04937ac158be, 3900'\n"
                   "echo 'GPU-2bc81341-8b1a-3a2e-679b-7a2bb75c6d9e, 24045'\n")
    smi.chmod(0o755)
    rc, out, _ = moss.run("stop", MOSS_NVIDIA_SMI=str(smi), **env)
    assert rc == 0 and out["ok"] is True and out["mode"] == "off"
    assert moss.calls() == ["stop busy=0"], "a loaded, idle MOSS must not delay the stop"
    assert not (moss.tmp / "proc" / "4242").exists()
    assert "23,5 GiB frei" in out["note"]


def test_resident_stop_reports_a_moss_process_that_survives(moss):
    moss.scheduler.resident = LOADED
    env = resident_child(moss)
    moss.set("survivor", "1")
    rc, out, _ = moss.run("stop", **env)
    assert rc == 1 and out["ok"] is False and out["mode"] == "off"
    assert "MOSS-Prozess läuft noch" in out["error"]


def test_resident_start_waits_until_moss_is_loaded(moss):
    moss.set("state", "inactive")
    loaded_at = time.time() + 1.2
    moss.scheduler.resident = lambda: LOADED if time.time() >= loaded_at else \
        {"state": "loading", "reason": "", "alive": True, "pid": 4242}
    rc, out, elapsed = moss.run("start")
    assert rc == 0 and out["ok"] is True and out["loaded"] is True
    assert elapsed >= 1.0, "start must not report success before MOSS is in memory"
    assert out["note"] == "MOSS ist geladen und bereit."


def test_resident_start_returns_with_the_reason_when_moss_must_wait(moss):
    moss.set("state", "inactive")
    moss.scheduler.resident = {"state": "waiting", "reason": "qwen", "alive": False, "pid": None}
    rc, out, _ = moss.run("start")
    assert rc == 0 and out["ok"] is True and out["loaded"] is False
    assert "Qwen" in out["note"]


def test_resident_start_reports_a_load_error(moss):
    moss.set("state", "inactive")
    moss.scheduler.resident = {"state": "error", "reason": "RuntimeError: CUDA out of memory",
                               "alive": False, "pid": None}
    rc, out, _ = moss.run("start")
    assert rc == 1 and out["ok"] is False and "CUDA out of memory" in out["error"]


def test_resident_start_gives_up_when_loading_never_ends(moss):
    moss.set("state", "inactive")
    moss.scheduler.resident = {"state": "loading", "reason": "", "alive": True, "pid": 4242}
    rc, out, _ = moss.run("start", MOSS_LOAD_WAIT="1")
    assert rc == 1 and "noch nicht geladen" in out["error"]


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
            compare(button.text, "● MOSS STOPPEN")
            mouseClick(button)
            verify(control.actionBusy)
            verify(button.text.indexOf("◐ MOSS STOPPT") === 0, button.text)
            verify(!button.enabled)
            mouseClick(button)  // disabled while busy: must not dispatch a second stop
            tryCompare(control, "actionBusy", false, 15000)
            compare(control.mode, "off")
            compare(button.text, "○ MOSS STARTEN")
            compare(control.errorMessage, "")
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 15000)
            compare(control.mode, "on")
            compare(control.ready, true)
            compare(button.text, "● MOSS STOPPEN")
            compare(control.errorMessage, "")
            control.mode = "unknown"
            verify(!button.enabled)
            compare(button.text, "? MOSS UNBEKANNT")
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
            compare(button.text, "▲ MOSS FEHLER · STOPPEN")
            verify(button.enabled)
            compare(control.mode, "on")
            verify(control.toolTipText.indexOf("Access denied") >= 0)
        }''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_moss_button_says_when_moss_runs_but_is_not_loaded(moss):
    moss.scheduler.resident = {"state": "loading", "reason": "", "alive": True, "pid": 4242}
    result = run_qml(moss, '''
        function test_not_loaded_labels() {
            var button = findChild(control, "mossToggleButton")
            tryCompare(control, "mossState", "loading", 10000)
            compare(button.text, "◐ MOSS LÄDT …")
            verify(button.enabled, "a click must still stop MOSS")
            control.mossState = "waiting"; control.mossReason = "qwen"
            compare(button.text, "◐ MOSS WARTET (QWEN)")
            control.mossState = "error"; control.mossReason = "CUDA out of memory"
            compare(button.text, "▲ MOSS FEHLER · STOPPEN")
            control.mossState = "loaded"; control.loaded = true
            compare(button.text, "● MOSS STOPPEN")
        }''')
    assert result.returncode == 0, result.stdout + result.stderr
