"""Exercise the real Qt button and executable source with isolated user scripts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_qwen_button_executes_start_stop_handles_failure_and_blocks_duplicate_clicks(tmp_path):
    component = REPO / "contents/ui/QwenControl.qml"
    assert component.is_file(), "The manual Qwen button is missing"
    runner = shutil.which("qmltestrunner") or "/usr/lib/qt6/bin/qmltestrunner"
    if not Path(runner).is_file():
        pytest.skip("Qt Quick Test runner not installed")
    home = tmp_path / "home with spaces"
    commands = home / ".local/bin"
    commands.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    state = tmp_path / "state"
    calls = tmp_path / "calls"
    state.write_text("inactive")
    ctl = fake_bin / "systemctl"
    ctl.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                   f"print('LoadState=loaded\\nActiveState='+Path({str(state)!r}).read_text()+'\\nSubState=dead')\n")
    ctl.chmod(0o755)
    for action, target in [("start", "active"), ("stop", "inactive")]:
        script = commands / f"qwen38-{action}"
        script.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys,time\n"
                          f"with Path({str(calls)!r}).open('a') as f:f.write({action!r}+'\\n')\n"
                          "time.sleep(0.5)\n"
                          f"if {action!r} == 'start' and Path({str(calls)!r}).read_text().splitlines().count('start') > 1:sys.stderr.write('deliberate test failure\\n');sys.exit(9)\n"
                          f"Path({str(state)!r}).write_text({target!r})\n")
        script.chmod(0o755)
    qml = f'''import QtQuick
import QtTest
import {json.dumps((REPO / "contents/ui").as_uri())} as Dashboard
Item {{
    id: window; width: 300; height: 80
    Dashboard.QwenControl {{ id: control; anchors.centerIn: parent }}
    TestCase {{
        name: "SkyboxQwenButton"; when: windowShown
        function test_start_stop_and_double_click() {{
            tryCompare(control, "state", "stopped", 10000)
            var button = findChild(control, "qwenToggleButton")
            verify(button !== null)
            verify(button.enabled)
            verify(button.text.indexOf("starten") >= 0)
            mouseClick(button)
            verify(control.actionBusy)
            verify(!button.enabled)
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 10000)
            verify(control.modelOn, "The actual helper must report Qwen ON")
            verify(button.text.indexOf("stoppen") >= 0)
            button.forceActiveFocus()
            keyClick(Qt.Key_Space)
            tryCompare(control, "state", "stopped", 10000)
            tryCompare(control, "actionBusy", false, 10000)
            compare(control.errorMessage, "")
            // Unknown or external transitions cannot dispatch a command.
            var states = ["unknown", "unavailable", "starting", "stopping"]
            for (var i=0;i<states.length;i++) {{
                control.state = states[i]
                verify(!button.enabled)
                mouseClick(button)
                verify(!control.actionBusy)
            }}
            control.state = "sleeping"
            verify(button.enabled)
            verify(button.text.indexOf("stoppen") >= 0, "Sleeping Qwen is ON, not OFF")
            // The second start fails through the real subprocess boundary.
            control.state = "stopped"
            mouseClick(button)
            tryCompare(control, "actionBusy", false, 10000)
            verify(control.errorMessage.indexOf("deliberate test failure") >= 0)
            compare(control.state, "stopped")
            verify(button.enabled)
            verify(control.toolTipText.indexOf(control.errorMessage) >= 0)
            // A disconnected older request must not overwrite current truth.
            control.state = "running"
            control.handleData("old disconnected command", {{"stdout": '{{"state":"stopped"}}', "exit code": 0}})
            compare(control.state, "running")
        }}
    }}
}}'''
    testdir = tmp_path / "qmltests"
    testdir.mkdir()
    (testdir / "tst_qwen.qml").write_text(qml)
    result = subprocess.run([runner, "-input", str(testdir)],
                            env={**os.environ, "HOME": str(home),
                                 "XDG_CACHE_HOME": str(tmp_path / "cache"),
                                 "XDG_RUNTIME_DIR": str(runtime),
                                 "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                                 "QT_QPA_PLATFORM": "offscreen"},
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    # A duplicate click must not start a second Qwen load; keyboard stop must
    # select the stop script, not merely paint an OFF label.
    assert calls.read_text().splitlines() == ["start", "stop", "start"]
