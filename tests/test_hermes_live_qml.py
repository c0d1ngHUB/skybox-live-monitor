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
   // Partial measurement (one profile unreadable) is shown and warns.
   p = base(t); p.partial = true; p.failed_profiles = ["broken"]; p.profiles = ["default"]; p.tone = "warn"
   h.payload = p
   compare(h.warn, true)
   verify(heading.text.indexOf("TEILWEISE") >= 0, heading.text)
   verify(h.detailText().indexOf("broken") >= 0)
   // Activity from a named profile names that profile.
   p = base(t); p.last_profile = "elisabeth"; p.profiles = ["default", "elisabeth"]
   h.payload = p
   verify(summary.text.indexOf("telegram/elisabeth") >= 0, summary.text)
   verify(h.detailText().indexOf("default, elisabeth") >= 0)
   // Stale data becomes unknown.
   h.payload = base(t)
   h.nowMs = (t + 40) * 1000
   compare(h.fresh, false)
   verify(heading.text.indexOf("UNBEKANNT") >= 0, heading.text)
   verify(h.detailText().indexOf("undefined") < 0)
  }
  function details(t) {
   return {stamp:t,
    tokens:{available:true,partial:false,failed_profiles:[],total:134000000,output:800000,cache_hit_pct:95,cost_usd:92.2,
      models:[{model:"claude-opus-5-5",total:128000000,input:1,output:2,cache_read:3,cache_write:4,sessions:28,cost_usd:90.4},
              {model:"fb-1",total:6000000,input:1,output:2,cache_read:3,cache_write:4,sessions:2,cost_usd:1.8}]},
    cron:{available:true,enabled:8,expected:8,baseline_new:false,missing:[],disabled:[],next_run_at:t+600,next_name:"job",
      failed_24h:0,failed_names:[],expected_file:"/x/expected.json"},
    hindsight:{available:true,bank:"hermes",retains_60m:12,retains_capped:false,consolidation_age_s:583,last_write_age_s:30,
      pending_consolidation:16,failed_consolidation:0,failed_operations:2,warn:false}}
  }
  function test_dropdown() {
   var t = Date.now() / 1000
   h.nowMs = t * 1000
   h.payload = base(t)
   h.expanded = false
   var collapsed = h.implicitHeight
   var toggle = findChild(h, "hermesToggle")
   // No details yet: the closed toggle warns instead of hiding an unknown state.
   compare(h.detailsWarn, true)
   verify(toggle.color == h.warning)
   verify(toggle.text.indexOf("▸") >= 0, toggle.text)
   h.details = details(t)
   compare(h.detailsWarn, false)
   mouseClick(findChild(h, "hermesToggleArea"), 20, 20)
   compare(h.expanded, true)
   verify(h.implicitHeight > collapsed)
   verify(toggle.text.indexOf("▾") >= 0, toggle.text)
   var tokens = findChild(h, "hermesTokens"), cron = findChild(h, "hermesCron"), hs = findChild(h, "hermesHindsight")
   verify(tokens.text.indexOf("134 M") >= 0 && tokens.text.indexOf("Cache 95%") >= 0 && tokens.text.indexOf("opus-5-5 96%") >= 0, tokens.text)
   verify(cron.text.indexOf("CRON · 8/8") >= 0 && cron.text.indexOf("Fehler 24h: 0") >= 0, cron.text)
   verify(hs.text.indexOf("Retains 60m: 12") >= 0 && hs.text.indexOf("9 min 43 s") >= 0 && hs.text.indexOf("offen 16") >= 0, hs.text)
   // A job missing after an update turns the cron row and the toggle yellow.
   var d = details(t); d.cron.missing = ["job c"]; d.cron.enabled = 7
   h.details = d
   compare(h.cronTone(), "warn"); compare(h.detailsWarn, true)
   verify(cron.text.indexOf("7/8") >= 0 && cron.text.indexOf("FEHLT 1") >= 0, cron.text)
   verify(h.cronDetail().indexOf("job c") >= 0)
   // One broken section stays local and unknown, never a zero.
   d = details(t); d.hindsight = {available:false, error:"api_unreachable"}; d.cron.failed_24h = null
   h.details = d
   verify(hs.text.indexOf("UNBEKANNT") >= 0, hs.text)
   compare(h.cronTone(), "unknown")
   verify(cron.text.indexOf("Fehler 24h: ?") >= 0, cron.text)
   verify(tokens.text.indexOf("134 M") >= 0, tokens.text)
   d = details(t); d.hindsight.retains_60m = null
   h.details = d
   verify(hs.text.indexOf(">500") >= 0, hs.text)
   // Stale details become unknown.
   h.details = details(t - 200)
   verify(tokens.text.indexOf("UNBEKANNT") >= 0, tokens.text)
   var all = h.tokensDetail() + h.cronDetail() + h.hindsightDetail()
   verify(all.indexOf("undefined") < 0 && all.indexOf("%%") < 0, all)
   mouseClick(findChild(h, "hermesToggleArea"), 20, 20)
   compare(h.expanded, false); compare(h.implicitHeight, collapsed)
   h.payload = null; h.details = null
  }
 }
}
'''
    (tmp_path / 'tst_hermes.qml').write_text(code)
    result = subprocess.run([runner, '-input', str(tmp_path / 'tst_hermes.qml')],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'HermesLive::test_hermes()' in result.stdout, result.stdout + result.stderr
    assert 'HermesLive::test_dropdown()' in result.stdout, result.stdout + result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr
