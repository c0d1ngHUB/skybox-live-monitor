"""Exercise the real HermesLive QML with clearly synthetic inputs, no production polls."""
import json
import os
from pathlib import Path
import subprocess
import shutil

import pytest

ROOT = Path(__file__).parents[1]


def qml_runner(tmp_path):
    """Probe dependencies only; actual component/test failures must never skip."""
    runner = shutil.which('qmltestrunner') or '/usr/lib/qt6/bin/qmltestrunner'
    probe = tmp_path / 'tst_dependencies.qml'
    probe.write_text('''import QtQuick
import QtTest
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as PlasmaSupport
TestCase { name: "Dependencies"; function test_imports() { verify(true) } }
''')
    try:
        result = subprocess.run([runner, '-input', str(probe)],
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
            text=True, capture_output=True, timeout=25)
    except OSError as error:
        reason = str(error)
    else:
        if result.returncode == 0:
            if 'Dependencies::test_imports()' not in result.stdout:
                pytest.fail('Qt dependency probe did not execute its test: ' + result.stdout + result.stderr)
            return runner
        reason = result.stdout + result.stderr
    message = 'Qt Quick/Plasma integration dependencies unavailable: ' + reason
    if os.environ.get('SKYBOX_REQUIRE_QML') == '1':
        pytest.fail(message)
    pytest.skip(message)


def test_hermes_live_states_staleness_and_warning_indicator(tmp_path):
    runner = qml_runner(tmp_path)
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 id:root; width:860; height:300
 HermesLive { id:h; width:parent.width; height:implicitHeight; pollingEnabled:false }
 TestCase {
  name:"HermesLive"; when:windowShown
  function base(t) {
   return {available:true,tone:"ok",stamp:t,running_turns:0,longest_turn_s:0,workers:0,delegations:0,
     last_activity_age_s:12,last_source:"telegram",last_description:"done",model:"m-1",stalled:false,
     fallback_24h:0,fallback_calls_24h:0,fallback_models:[],limit_429_24h:0}
  }
  function test_hermes() {
   var heading = findChild(h, "hermesHeading")
   var summary = findChild(h, "hermesSummary")
   // No data yet: unknown, never an invented zero.
   compare(h.fresh, false)
   verify(heading.text.indexOf("UNBEKANNT") >= 0, heading.text)
   verify(h.border.color == h.warning)
   var t = Date.now() / 1000
   h.nowMs = t * 1000
   h.payload = base(t)
   compare(h.fresh, true); compare(h.warn, false)
   verify(heading.text.indexOf("BEREIT") >= 0, heading.text)
   verify(summary.text.indexOf("12 s") >= 0 && summary.text.indexOf("telegram") >= 0 && summary.text.indexOf("m-1") >= 0, summary.text)
   verify(h.border.color != h.warning)
   // Running turn + worker.
   var p = base(t); p.running_turns = 1; p.longest_turn_s = 95; p.workers = 2
   h.payload = p
   verify(heading.text.indexOf("1 TURN AKTIV") >= 0 && heading.text.indexOf("1 min 35 s") >= 0, heading.text)
   verify(heading.text.indexOf("2 WORKER") >= 0, heading.text)
   // Stalled turn warns.
   p = base(t); p.running_turns = 1; p.stalled = true; p.tone = "warn"; p.last_activity_age_s = 900
   h.payload = p
   compare(h.warn, true); verify(heading.text.indexOf("HÄNGT?") >= 0, heading.text)
   verify(h.border.color == h.warning)
   // Fallback in 24 h is the warning indicator.
   p = base(t); p.fallback_24h = 2; p.fallback_calls_24h = 7; p.fallback_models = ["fb-1"]; p.tone = "warn"
   h.payload = p
   compare(h.warn, true)
   verify(summary.text.indexOf("FALLBACK 24h: 2") >= 0, summary.text)
   verify(h.detailText().indexOf("fb-1") >= 0)
   // Unknown values stay unknown.
   p = base(t); p.fallback_24h = null; p.limit_429_24h = null; p.tone = "warn"
   h.payload = p
   verify(summary.text.indexOf("FALLBACK 24h: ?") >= 0 && summary.text.indexOf("429 24h: ?") >= 0, summary.text)
   // Stale data becomes unknown.
   h.payload = base(t)
   h.nowMs = (t + 40) * 1000
   compare(h.fresh, false)
   verify(heading.text.indexOf("UNBEKANNT") >= 0, heading.text)
   verify(h.detailText().indexOf("undefined") < 0)
  }
 }
}
'''
    (tmp_path / 'tst_hermes.qml').write_text(code)
    result = subprocess.run([runner, '-input', str(tmp_path / 'tst_hermes.qml')],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'HermesLive::test_hermes()' in result.stdout, result.stdout + result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr
