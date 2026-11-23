"""Exercise the real CodexUsage QML with synthetic payloads, no production polls."""
import json
import os
from pathlib import Path
import re
import subprocess

from qml_support import qml_runner

ROOT = Path(__file__).parents[1]


def test_codex_usage_states(tmp_path):
    runner = qml_runner(tmp_path)
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 id:root; width:860; height:120
 CodexUsage { id:u; width:parent.width; height:implicitHeight; pollingEnabled:false }
 TestCase {
  name:"CodexUsage"; when:windowShown
  function base(t) {
   return {available:true,stamp:t,reason:null,plan:"plus",tone:"ok",
     session:{pct:23,resets_at:t+3600,window_s:18000},
     weekly:{pct:19,resets_at:t+5*86400,window_s:604800},
     limit_reached:false,extra:[],credits:{has_credits:false,unlimited:false,balance:"0"},
     reset_credits:3,token_expires_in_s:299087,thresholds:{warn_pct:75,critical_pct:90}}
  }
  function test_usage() {
   var heading = findChild(u, "codexHeading"), unknown = findChild(u, "codexUnknown")
   // No data yet: unknown, never 0 %.
   compare(u.fresh, false); compare(u.tone, "unknown")
   verify(unknown.visible && unknown.text.indexOf("SITZUNG --") >= 0 && unknown.text.indexOf("0%") < 0, unknown.text)
   verify(unknown.text.indexOf("noch keine Messung") >= 0, unknown.text)
   verify(u.border.color == u.warning)
   compare(heading.text, "CODEX")
   var t = Date.now() / 1000
   u.nowMs = t * 1000
   u.payload = base(t)
   var s = findChild(u, "codexSession"), w = findChild(u, "codexWeekly")
   compare(u.fresh, true); compare(unknown.visible, false)
   compare(heading.text, "CODEX PLUS")
   verify(s.text.indexOf("SITZUNG 23%") === 0 && s.text.indexOf(" ↻ ") > 0, s.text)
   verify(w.text.indexOf("WOCHE 19%") === 0, w.text)
   verify(/↻ (So|Mo|Di|Mi|Do|Fr|Sa) \\d\\d:\\d\\d$/.test(w.text), w.text)
   // Same row form as the CLAUDE row: quiet border while ok, muted heading.
   compare(u.implicitHeight, 28); compare(u.radius, 9)
   verify(u.border.color == u.quietBorder && heading.color == u.muted)
   verify(s.color == u.accent && w.color == u.accent)
   // Reset times are cut to the minute like Codex' own "resets hh:mm".
   var r = new Date(); r.setDate(r.getDate() + 6); r.setHours(12, 59, 59, 0)
   var p0 = base(t); p0.weekly.resets_at = Math.floor(r.getTime() / 1000); u.payload = p0
   verify(/↻ (So|Mo|Di|Mi|Do|Fr|Sa) 12:59$/.test(w.text), w.text)
   // Today: time only.
   var today = new Date(); today.setHours(23, 58, 30, 0)
   var p1 = base(t); p1.session.resets_at = Math.floor(today.getTime() / 1000); u.payload = p1
   compare(s.text, "SITZUNG 23% ↻ 23:58")
   // No reset time from the API: no "↻ ?".
   p1 = base(t); p1.session.resets_at = null; u.payload = p1
   compare(s.text, "SITZUNG 23%")
   // Plan names: underscores become spaces, an unknown plan is plain "CODEX".
   var p2 = base(t); p2.plan = "pro_lite"; u.payload = p2
   compare(heading.text, "CODEX PRO LITE")
   p2 = base(t); p2.plan = null; u.payload = p2
   compare(heading.text, "CODEX")
   p2 = base(t); p2.plan = "  "; u.payload = p2
   compare(heading.text, "CODEX")
   // Tooltip: window lengths, reset dates, credits and banked resets.
   u.payload = base(t)
   var tip = u.detailText()
   verify(tip.indexOf("Plans PLUS") >= 0, tip)
   verify(tip.indexOf("Sitzung (5 h): 23% · Reset ") >= 0 && tip.indexOf("Woche (7 Tage): 19% · Reset ") >= 0, tip)
   verify(tip.indexOf("Credits: keine") >= 0 && tip.indexOf("Gespeicherte Limit-Resets: 3") >= 0, tip)
   verify(tip.indexOf("LIMIT ERREICHT") < 0, tip)
   verify(tip.indexOf("undefined") < 0 && tip.indexOf("%%") < 0 && tip.indexOf("NaN") < 0 && tip.indexOf("null") < 0, tip)
   var p3 = base(t)
   p3.extra = [{name:"GPT-Spark",pct:40,resets_at:t+1800,window_s:18000},{name:"Code-Review",pct:5,resets_at:null,window_s:604800}]
   p3.credits = {has_credits:true,unlimited:false,balance:"12.50"}; p3.reset_credits = 0
   u.payload = p3
   tip = u.detailText()
   verify(tip.indexOf("GPT-Spark (5 h): 40% · Reset ") >= 0 && tip.indexOf("Code-Review (7 Tage): 5%") >= 0, tip)
   verify(tip.indexOf("Credits: Guthaben 12.50") >= 0 && tip.indexOf("Limit-Resets") < 0, tip)
   verify(tip.indexOf("undefined") < 0 && tip.indexOf("null") < 0, tip)
   p3 = base(t); p3.credits = {has_credits:true,unlimited:true,balance:null}; u.payload = p3
   verify(u.detailText().indexOf("Credits: unbegrenzt") >= 0, u.detailText())
   // Extra limits only inform: they never colour the row.
   compare(u.border.color, u.quietBorder)
   // Thresholds: yellow at 75 %, red at 90 % (per limit); the border follows the tone.
   var p = base(t); p.session.pct = 80; p.tone = "warn"
   u.payload = p
   verify(s.color == u.warning && w.color == u.accent && u.border.color == u.warning)
   p = base(t); p.weekly.pct = 95; p.tone = "critical"
   u.payload = p
   verify(w.color == u.critical && u.border.color == u.critical && heading.color == u.muted)
   // A reached limit is red even when the numbers look harmless, and says so.
   p = base(t); p.limit_reached = true; p.tone = "critical"
   u.payload = p
   verify(u.border.color == u.critical, "limit reached must colour the border")
   verify(u.detailText().indexOf("LIMIT ERREICHT") >= 0, u.detailText())
   // A weekly-only plan: no session window is not a fault. Muted "--", quiet
   // border, and the tooltip says so instead of "unbekannt".
   p = base(t); p.session = null
   u.payload = p
   compare(s.text, "SITZUNG --")
   verify(s.color == u.muted && w.color == u.accent && u.border.color == u.quietBorder)
   verify(u.detailText().indexOf("Sitzung: kein Limit gemeldet") >= 0 && u.detailText().indexOf("Woche (7 Tage): 19%") >= 0, u.detailText())
   // Expired token or missing login: explicit reason, no stale numbers.
   u.payload = {available:false,stamp:t,reason:"token_expired",plan:"plus",tone:"unknown",session:null,weekly:null,extra:[]}
   verify(unknown.visible && unknown.text.indexOf("Token abgelaufen") >= 0, unknown.text)
   compare(heading.text, "CODEX PLUS")
   verify(u.detailText().indexOf("Token abgelaufen") >= 0 && u.detailText().indexOf("$CODEX_HOME/auth.json (Standard ~/.codex/auth.json)") >= 0, u.detailText())
   verify(u.detailText().indexOf("undefined") < 0)
   u.payload = {available:false,stamp:t,reason:"no_credentials",plan:null,tone:"unknown",session:null,weekly:null}
   verify(unknown.text.indexOf("keine Codex-Anmeldung") >= 0, unknown.text)
   u.payload = {available:false,stamp:t,reason:"http_429",plan:null,tone:"unknown",session:null,weekly:null}
   verify(unknown.text.indexOf("HTTP 429") >= 0, unknown.text)
   u.payload = {available:false,stamp:t,reason:"network",plan:null,tone:"unknown",session:null,weekly:null}
   verify(unknown.text.indexOf("nicht erreichbar") >= 0, unknown.text)
   u.payload = {available:false,stamp:t,reason:"bad_payload",plan:null,tone:"unknown",session:null,weekly:null}
   verify(unknown.text.indexOf("Antwort unbekannt") >= 0, unknown.text)
   u.payload = {available:false,stamp:t,reason:"internal_error",plan:null,tone:"unknown",session:null,weekly:null}
   verify(unknown.text.indexOf("interner Fehler") >= 0, unknown.text)
   // Stale after 2.5 poll intervals (5 min at 2 min).
   u.payload = base(t)
   u.nowMs = (t + 4 * 60) * 1000
   compare(u.fresh, true)
   u.nowMs = (t + 6 * 60) * 1000
   compare(u.fresh, false)
   verify(unknown.text.indexOf("Messung veraltet") >= 0, unknown.text)
   u.payload = null
  }
 }
}
'''
    (tmp_path / 'tst_codex.qml').write_text(code)
    result = subprocess.run([runner, '-input', str(tmp_path / 'tst_codex.qml')],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CodexUsage::test_usage()' in result.stdout, result.stdout + result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr


def test_poll_interval_is_two_minutes_and_helper_is_wired():
    qml = (ROOT / 'contents/ui/CodexUsage.qml').read_text()
    assert re.search(r'property int pollIntervalMs: 120000\b', qml)
    assert 'Qt.resolvedUrl("../code/codex_usage.py")' in qml


def test_codex_row_sits_directly_under_the_claude_row():
    """KI-DIENSTE order: service cards -> VERDICHTUNG -> CLAUDE -> CODEX, and the
    block grows by exactly one 28-px row plus the 8-px column spacing."""
    text = (ROOT / 'contents/ui/main.qml').read_text()
    block = text[text.index('// --- AI SERVICES section ---'):text.index('// --- SYSTEM LOAD section ---')]
    assert block.count('CodexUsage {') == 1 and block.count('ClaudeUsage {') == 1
    assert block.index('id: obsCondenseCard') < block.index('id: claudeUsage') < block.index('id: codexUsage')
    codex = block[block.index('CodexUsage {'):]
    codex = codex[:codex.index('}') + 1]
    for line in ('width: parent.width', 'accent: root.cyan', 'warning: root.warning',
                 'critical: root.critical', 'quietBorder: root.quietBorder'):
        assert line in codex, line
    assert 'Layout.preferredHeight: 214' in block and 'Layout.minimumHeight: 210' in block
