"""Exercise the real ClaudeUsage QML with synthetic payloads, no production polls."""
import json
import os
from pathlib import Path
import re
import subprocess

from test_hermes_live_qml import qml_runner

ROOT = Path(__file__).parents[1]


def test_claude_usage_states(tmp_path):
    runner = qml_runner(tmp_path)
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 id:root; width:860; height:120
 ClaudeUsage { id:u; width:parent.width; height:implicitHeight; pollingEnabled:false }
 TestCase {
  name:"ClaudeUsage"; when:windowShown
  function base(t) {
   return {available:true,stamp:t,reason:null,plan:"max",tone:"ok",
     session:{pct:16,resets_at:t+3600,severity:"normal"},
     weekly:{pct:3,resets_at:t+5*86400,severity:"normal"},
     scoped:[{name:"Fable",pct:0,resets_at:t+5*86400}],
     breakdown:[{name:"Claude Code",pct:100},{name:"Chats",pct:0}],
     extra_usage_enabled:false,token_expires_in_s:1200,thresholds:{warn_pct:75,critical_pct:90}}
  }
  function test_usage() {
   var heading = findChild(u, "claudeHeading"), unknown = findChild(u, "claudeUnknown")
   // No data yet: unknown, never 0 %.
   compare(u.fresh, false); compare(u.tone, "unknown")
   verify(unknown.visible && unknown.text.indexOf("SESSION --") >= 0 && unknown.text.indexOf("0 %") < 0, unknown.text)
   verify(u.border.color == u.warning)
   var t = Date.now() / 1000
   u.nowMs = t * 1000
   u.payload = base(t)
   var s = findChild(u, "claudeSession"), w = findChild(u, "claudeWeekly")
   compare(u.fresh, true); compare(unknown.visible, false)
   compare(heading.text, "CLAUDE MAX")
   verify(s.text.indexOf("SESSION 16 %") === 0 && s.text.indexOf("↻") > 0, s.text)
   verify(w.text.indexOf("WOCHE 3 %") === 0, w.text)
   verify(/↻(So|Mo|Di|Mi|Do|Fr|Sa) \\d\\d:\\d\\d/.test(w.text), w.text)
   // hh:59:59 rounds up to the full hour, 6.9 days ahead still shows a weekday.
   var r = new Date(); r.setDate(r.getDate() + 6); r.setHours(12, 59, 59, 990)
   var p0 = base(t); p0.weekly.resets_at = r.getTime() / 1000 + 0.0; u.payload = p0
   verify(/↻(So|Mo|Di|Mi|Do|Fr|Sa) 13:00$/.test(w.text), w.text)
   u.payload = base(t)
   verify(s.color == u.accent && u.border.color != u.warning)
   var tip = u.detailText()
   verify(tip.indexOf("Fable: 0 %") >= 0 && tip.indexOf("Claude Code 100 %") >= 0 && tip.indexOf("Extra-Usage: aus") >= 0, tip)
   verify(tip.indexOf("undefined") < 0 && tip.indexOf("%%") < 0 && tip.indexOf("NaN") < 0, tip)
   // Thresholds: yellow at 75 %, red at 90 % (per limit) and the card border follows the tone.
   var p = base(t); p.session.pct = 80; p.tone = "warn"
   u.payload = p
   verify(s.color == u.warning && w.color == u.accent && u.border.color == u.warning)
   p = base(t); p.weekly.pct = 95; p.tone = "critical"
   u.payload = p
   verify(w.color == u.critical && u.border.color == u.critical && heading.color == u.critical)
   p = base(t); p.session.severity = "critical"; p.tone = "critical"
   u.payload = p
   verify(s.color == u.critical)
   // Expired token: explicit reason, no stale numbers.
   u.payload = {available:false,stamp:t,reason:"token_expired",plan:"max",tone:"unknown",session:null,weekly:null,scoped:[],breakdown:[]}
   verify(unknown.visible && unknown.text.indexOf("Token abgelaufen") >= 0, unknown.text)
   verify(u.detailText().indexOf("Token abgelaufen") >= 0 && u.detailText().indexOf("undefined") < 0)
   u.payload = {available:false,stamp:t,reason:"http_429",plan:null,tone:"unknown",session:null,weekly:null}
   verify(unknown.text.indexOf("HTTP 429") >= 0, unknown.text)
   // Stale after 2.5 poll intervals (25 min at 10 min).
   u.payload = base(t)
   u.nowMs = (t + 24 * 60) * 1000
   compare(u.fresh, true)
   u.nowMs = (t + 26 * 60) * 1000
   compare(u.fresh, false)
   verify(unknown.text.indexOf("Messung veraltet") >= 0, unknown.text)
   u.payload = null
  }
 }
}
'''
    (tmp_path / 'tst_claude.qml').write_text(code)
    result = subprocess.run([runner, '-input', str(tmp_path / 'tst_claude.qml')],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ClaudeUsage::test_usage()' in result.stdout, result.stdout + result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr


def test_poll_interval_is_ten_minutes():
    qml = (ROOT / 'contents/ui/ClaudeUsage.qml').read_text()
    assert re.search(r'property int pollIntervalMs: 600000\b', qml)
    assert 'ClaudeUsage {' in (ROOT / 'contents/ui/main.qml').read_text()
