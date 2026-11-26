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


def run_case(tmp_path, code, case):
    runner = qml_runner(tmp_path)
    path = tmp_path / ('tst_' + case.split('::')[0].lower() + '.qml')
    path.write_text(code)
    # QtTest prints a failed compare() of "·" strings as Latin-1: never let the
    # decoder hide the real failure message.
    result = subprocess.run([str(runner), '-input', str(path)], env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        text=True, errors='replace', capture_output=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert case in result.stdout, result.stdout + result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr, result.stderr


GO_PAYLOAD = '''
  function go(t) {
   return {available:true,stamp:t,reason:null,plan:"go",tone:"ok",session:null,
     weekly:{pct:0,resets_at:t+20*86400,window_s:2592000},
     limit_reached:false,extra:[],credits:{has_credits:false,unlimited:false,balance:"0"},
     reset_credits:null,token_expires_in_s:863000,thresholds:{warn_pct:75,critical_pct:90}}
  }
'''


def test_a_window_of_eight_days_or_more_is_a_month(tmp_path):
    """The Go plan publishes one 30-day limit: MONAT, never WOCHE."""
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 id:root; width:860; height:120
 CodexUsage { id:u; width:parent.width; height:implicitHeight; pollingEnabled:false }
 TestCase {
  name:"CodexMonth"; when:windowShown
''' + GO_PAYLOAD + '''
  function test_month() {
   var heading = findChild(u, "codexHeading"), unknown = findChild(u, "codexUnknown")
   var t = Date.now() / 1000
   u.nowMs = t * 1000
   u.payload = go(t)
   var s = findChild(u, "codexSession"), w = findChild(u, "codexWeekly")
   compare(heading.text, "CODEX GO")
   compare(s.text, "SITZUNG --")
   verify(s.color == u.muted && w.color == u.accent && u.border.color == u.quietBorder)
   // More than 7 days ahead: the reset shows a date, not a weekday.
   verify(/^MONAT 0% ↻ \\d\\d\\.\\d\\d\\. \\d\\d:\\d\\d$/.test(w.text), w.text)
   var tip = u.detailText()
   verify(tip.indexOf("Plans GO") >= 0 && tip.indexOf("Monat (30 Tage): 0% · Reset ") >= 0, tip)
   verify(tip.indexOf("Sitzung: kein Limit gemeldet") >= 0 && tip.indexOf("Woche") < 0, tip)
   verify(tip.indexOf("undefined") < 0 && tip.indexOf("null") < 0 && tip.indexOf("NaN") < 0, tip)
   // Boundary: 8 days and more is a month, 7 days stays a week.
   var p = go(t); p.weekly.window_s = 8 * 86400; u.payload = p
   verify(w.text.indexOf("MONAT 0%") === 0, w.text)
   p = go(t); p.weekly.window_s = 7 * 86400; p.weekly.resets_at = t + 3 * 86400; u.payload = p
   verify(w.text.indexOf("WOCHE 0%") === 0, w.text)
   verify(u.detailText().indexOf("Woche (7 Tage): 0%") >= 0, u.detailText())
   // Unknown keeps the last published length, so the row still says MONAT.
   u.payload = go(t)
   u.payload = {available:false,stamp:t,reason:"token_expired",plan:"go",tone:"unknown",session:null,weekly:null,extra:[]}
   compare(unknown.text, "SITZUNG -- · MONAT -- · Token abgelaufen")
   compare(heading.text, "CODEX GO")
   // A fresh answer without the long limit: muted "--" under the known name.
   p = go(t); p.weekly = null; p.session = {pct:5,resets_at:t+3600,window_s:18000}; u.payload = p
   compare(w.text, "MONAT --")
   verify(w.color == u.muted, "a missing limit is muted")
   verify(u.detailText().indexOf("Monat: kein Limit gemeldet") >= 0, u.detailText())
   u.payload = null
  }
 }
}
'''
    run_case(tmp_path, code, 'CodexMonth::test_month()')


def test_pool_row_passes_only_the_entry_id_and_names_its_source(tmp_path):
    """poolEntry switches the helper to the Hermes pool; without it the row,
    its command and every text stay as they were."""
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 id:root; width:860; height:120
 CodexUsage { id:u; width:parent.width; height:implicitHeight; pollingEnabled:false }
 CodexUsage { id:g; y:40; width:parent.width; height:implicitHeight; pollingEnabled:false; poolEntry:"1449ff" }
 TestCase {
  name:"CodexPool"; when:windowShown
''' + GO_PAYLOAD + '''
  function test_pool() {
   // The plain row keeps its command; the pool row only adds the quoted entry id.
   var plain = findChild(u, "codexSource").command, pool = findChild(g, "codexSource").command
   verify(/^python3 '[^']*\\/codex_usage\\.py' # skybox-request-0$/.test(plain), plain)
   compare(pool, plain.replace(" # skybox-request-", " --hermes-pool-entry '1449ff' # skybox-request-"))
   var t = Date.now() / 1000
   u.nowMs = t * 1000; g.nowMs = t * 1000
   // Unknown: the pool row names the pool, the plain row reads exactly as before.
   var none = {available:false,stamp:t,reason:"no_credentials",plan:null,tone:"unknown",session:null,weekly:null}
   u.payload = none; g.payload = none
   compare(findChild(g, "codexUnknown").text, "SITZUNG -- · WOCHE -- · kein Hermes-Pool-Eintrag")
   compare(findChild(u, "codexUnknown").text, "SITZUNG -- · WOCHE -- · keine Codex-Anmeldung")
   compare(u.detailText(), "Codex-Nutzung UNBEKANNT: keine Codex-Anmeldung.\\n"
       + "Quelle: chatgpt.com/backend-api/wham/usage mit dem Token aus $CODEX_HOME/auth.json (Standard ~/.codex/auth.json).\\n"
       + "Der Token wird hier nie erneuert; Codex erneuert ihn beim nächsten Aufruf.")
   var tip = g.detailText()
   verify(tip.indexOf("Codex-Nutzung UNBEKANNT: kein Hermes-Pool-Eintrag.") === 0, tip)
   verify(tip.indexOf("Hermes-Pool (Eintrag 1449ff") >= 0 && tip.indexOf("hermes auth refresh") >= 0, tip)
   verify(tip.indexOf("CODEX_HOME") < 0 && tip.indexOf("Codex erneuert") < 0, tip)
   g.payload = {available:false,stamp:t,reason:"token_expired",plan:"go",tone:"unknown",session:null,weekly:null}
   verify(findChild(g, "codexUnknown").text.indexOf("Token abgelaufen") >= 0, findChild(g, "codexUnknown").text)
   verify(g.detailText().indexOf("Token abgelaufen") >= 0 && g.detailText().indexOf("hermes auth refresh") >= 0, g.detailText())
   compare(findChild(g, "codexHeading").text, "CODEX GO")
   // Fresh: the tooltip says which account the numbers belong to.
   g.payload = go(t)
   compare(findChild(g, "codexHeading").text, "CODEX GO")
   verify(findChild(g, "codexWeekly").text.indexOf("MONAT 0%") === 0, findChild(g, "codexWeekly").text)
   verify(g.detailText().indexOf("Hermes-Pool-Eintrag 1449ff") >= 0, g.detailText())
   verify(g.detailText().indexOf("undefined") < 0 && g.detailText().indexOf("null") < 0, g.detailText())
   u.payload = go(t)
   verify(u.detailText().indexOf("Hermes") < 0, u.detailText())
   g.payload = null; u.payload = null
  }
 }
}
'''
    run_case(tmp_path, code, 'CodexPool::test_pool()')


def test_pool_entry_reaches_the_helper_as_one_shell_quoted_argument(tmp_path):
    """The real helperArgs/command expressions under /bin/sh, as the Plasma
    executable engine runs them; an empty poolEntry adds nothing."""
    qml = (ROOT / 'contents/ui/CodexUsage.qml').read_text()
    args_match = re.search(r'property string helperArgs:\s*([^\n]+)', qml)
    command_match = re.search(r'property string command:\s*([^\n]+)', qml)
    assert args_match and command_match
    args_expression, command_expression = args_match[1], command_match[1]
    helper = tmp_path / 'codex_usage.py'
    helper.write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    for entry in ('', '1449ff', "x' ; touch pwned # $(touch pwned) `touch pwned`"):
        js = f'''
const MonitorLogic=require({json.dumps(str(ROOT / 'contents/code/monitor_logic.js'))});
const usage={{poolEntry:{json.dumps(entry)}}}, scriptPath={json.dumps(str(helper))}, requestId=6;
const helperArgs={args_expression};
console.log({command_expression});
'''
        command = subprocess.check_output(['node', '-e', js], text=True).strip()
        assert command.endswith(' # skybox-request-2'), command
        run = subprocess.run(['/bin/sh', '-c', command], cwd=tmp_path, text=True, capture_output=True)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout) == (['--hermes-pool-entry', entry] if entry else [])
    assert not (tmp_path / 'pwned').exists()


def test_poll_interval_is_two_minutes_and_helper_is_wired():
    qml = (ROOT / 'contents/ui/CodexUsage.qml').read_text()
    assert re.search(r'property int pollIntervalMs: 120000\b', qml)
    assert 'Qt.resolvedUrl("../code/codex_usage.py")' in qml


def test_codex_and_ollama_rows_keep_their_order_and_layout():
    """Replace only Magdi's Go row; leave all other rows in place."""
    text = (ROOT / 'contents/ui/main.qml').read_text()
    block = text[text.index('// --- AI SERVICES section ---'):text.index('// --- SYSTEM LOAD section ---')]
    assert block.count('CodexUsage {') == 2 and block.count('ClaudeUsage {') == 1
    assert block.count('OllamaCloudUsage {') == 1
    order = [block.index(s) for s in ('id: obsCondenseCard', 'id: claudeUsage', 'id: codexUsage\n',
                                      'id: ollamaCloudUsage', 'id: codexUsageMarion')]
    assert order == sorted(order), order
    assert block.count('poolEntry:') == 1 and 'poolEntry: "e4b59d"' in block
    assert 'MAGDII' not in block and '1449ff' not in block
    rows = re.findall(r'(?:CodexUsage|OllamaCloudUsage) \{[^}]*\}', block)
    expected = [('FUSSI', None), (None, None), ('MARION', 'e4b59d')]
    for row, (label, entry) in zip(rows, expected):
        assert (f'accountLabel: "{label}"' in row) if label else ('accountLabel:' not in row), row
        assert (f'poolEntry: "{entry}"' in row) if entry else ('poolEntry:' not in row), row
        for line in ('width: parent.width', 'headingWidth: 180', 'ink: root.ink', 'muted: root.muted',
                     'accent: root.cyan', 'warning: root.warning', 'critical: root.critical',
                     'quietBorder: root.quietBorder'):
            assert line in row, (line, row)
    assert 'longWindowS' not in ''.join(rows)
    # The CLAUDE row shares the heading width so the limit columns line up.
    claude = block[block.index('ClaudeUsage {'):block.index('}', block.index('ClaudeUsage {')) + 1]
    assert 'headingWidth: 180' in claude
    assert 'Layout.preferredHeight: 286' in block and 'Layout.minimumHeight: 282' in block


def test_account_label_names_the_row_without_changing_an_unlabelled_one(tmp_path):
    """accountLabel follows the plan in the heading and names the account in the
    tooltip; headingWidth sizes the heading column. Without them nothing changes."""
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 id:root; width:860; height:160
 CodexUsage { id:u; width:parent.width; height:implicitHeight; pollingEnabled:false }
 CodexUsage { id:f; y:40; width:parent.width; height:implicitHeight; pollingEnabled:false; accountLabel:"fussi"; headingWidth:180 }
 CodexUsage { id:m; y:80; width:parent.width; height:implicitHeight; pollingEnabled:false; poolEntry:"e4b59d"; accountLabel:"MARION"; headingWidth:180 }
 TestCase {
  name:"CodexAccount"; when:windowShown
''' + GO_PAYLOAD + '''
  function test_account() {
   compare(findChild(u, "codexHeading").width, 130)
   compare(findChild(f, "codexHeading").width, 180)
   compare(findChild(u, "codexHeading").text, "CODEX")
   compare(findChild(f, "codexHeading").text, "CODEX · FUSSI")
   var t = Date.now() / 1000
   u.nowMs = t * 1000; f.nowMs = t * 1000; m.nowMs = t * 1000
   var p = go(t); p.plan = "plus"
   u.payload = p; f.payload = p; m.payload = p
   compare(findChild(u, "codexHeading").text, "CODEX PLUS")
   compare(findChild(f, "codexHeading").text, "CODEX PLUS · FUSSI")
   compare(findChild(m, "codexHeading").text, "CODEX PLUS · MARION")
   // The heading fits its column in the board font, no elision.
   var h = findChild(m, "codexHeading")
   verify(h.implicitWidth <= h.width, h.implicitWidth + " > " + h.width)
   verify(u.detailText().indexOf("Konto:") < 0, u.detailText())
   verify(f.detailText().indexOf("Konto: FUSSI · Codex-Anmeldung") >= 0, f.detailText())
   verify(m.detailText().indexOf("Konto: MARION · Hermes-Pool-Eintrag e4b59d") >= 0, m.detailText())
   var none = {available:false,stamp:t,reason:"token_expired",plan:null,tone:"unknown",session:null,weekly:null}
   m.payload = none; u.payload = none
   verify(m.detailText().indexOf("Codex-Nutzung MARION UNBEKANNT: Token abgelaufen.") === 0, m.detailText())
   verify(u.detailText().indexOf("Codex-Nutzung UNBEKANNT: Token abgelaufen.") === 0, u.detailText())
   u.payload = null; f.payload = null; m.payload = null
  }
 }
}
'''
    run_case(tmp_path, code, 'CodexAccount::test_account()')
