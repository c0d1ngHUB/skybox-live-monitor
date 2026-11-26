"""Read-only quota cache adapter and real Plasma usage row."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_codex_usage_qml import run_case

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'contents/code/ollama_cloud_usage.py'
STAMP = 1795708800  # 2026-11-26T16:00:00Z


@pytest.fixture
def state(tmp_path):
    p = tmp_path / 'state/ollama_quota_watch.json'
    p.parent.mkdir()
    return p


def read_state(state):
    cmd = ['python3', str(SCRIPT)]
    env = dict(os.environ, HERMES_HOME=str(state.parent.parent))
    return json.loads(subprocess.check_output(cmd, text=True, env=env))


def test_latest_sample_converted_to_used_without_rewriting(state):
    data = {'history': [{'t': '2026-11-26T15:30:00Z', 's': 96, 'w': 72},
                        {'t': '2026-11-26T16:00:00Z', 's': 91.88, 'w': 67.49}],
            'alerted': {'session': {'reset': '2026-11-26T18:00:00Z'},
                        'weekly': {'reset': '2026-11-30T00:00:00Z'}},
            'fail': 1, 'secret': 'not-for-the-widget'}
    raw = json.dumps(data)
    state.write_text(raw)
    p = read_state(state)
    assert p['available'] is True and p['stamp'] == STAMP and p['tone'] == 'ok'
    assert p['session'] == {'pct': 8, 'resets_at': STAMP + 7200}
    assert p['weekly'] == {'pct': 33, 'resets_at': STAMP + 288000}
    assert 'not-for-the-widget' not in json.dumps(p)
    assert state.read_text() == raw


@pytest.mark.parametrize('bad', [None, True, -1, 101, float('nan'), float('inf'), '35', []])
def test_single_bad_window_is_unknown_not_zero(state, bad):
    state.write_text(json.dumps({'history': [{'t': '2026-11-26T16:00:00+00:00', 's': bad, 'w': 5}],
                                 'alerted': {'weekly': {'reset': 'invalid'}}}))
    p = read_state(state)
    assert p['available'] and p['session'] is None
    assert p['weekly'] == {'pct': 95, 'resets_at': None}
    assert p['tone'] == 'critical'


def test_real_zero_and_warning_are_distinct(state):
    state.write_text(json.dumps({'history': [{'t': '2026-11-26T16:00:00Z', 's': 100, 'w': 23}]}))
    p = read_state(state)
    assert p['session']['pct'] == 0 and p['weekly']['pct'] == 77
    assert p['tone'] == 'warn'


@pytest.mark.parametrize('reset', ['2026-11-26T15:59:59Z', '2026-11-26T16:00:00Z'])
def test_reset_at_or_before_sample_does_not_invalidate_usage(state, reset):
    state.write_text(json.dumps({'history': [{'t': '2026-11-26T16:00:00Z', 's': 100, 'w': 55}],
                                 'alerted': {'session': {'reset': reset}}}))
    p = read_state(state)
    assert p['session'] == {'pct': 0, 'resets_at': None}
    assert p['weekly']['pct'] == 45


@pytest.mark.parametrize('raw', ['{', '[]', '{}', '{"history": [null]}',
                                 '{"history":[{"t":"2026-11-26T16:00:00","s":90,"w":25}]}',
                                 '{"history":[{"t":"2026-11-26T16:00:00Z","s":null,"w":null}]}'])
def test_invalid_cache(state, raw):
    state.write_text(raw)
    p = read_state(state)
    assert p['available'] is False and p['reason'] == 'bad_payload'
    assert p['session'] is None and p['weekly'] is None


def test_missing_cache(state):
    assert read_state(state)['reason'] == 'cache_missing'


def test_ollama_row_is_provider_specific_and_handles_stale_samples(tmp_path):
    code = '''import QtQuick
import QtTest
import ''' + json.dumps((ROOT / 'contents/ui').as_uri()) + '''
Item {
 width: 980; height: 80
 OllamaCloudUsage { id:u; width:parent.width; height:implicitHeight; pollingEnabled:false; headingWidth:180 }
 TestCase {
  name:"OllamaCloudUsage"; when:windowShown
  function test_quota() {
   var stamp = 1795708800
   var h = findChild(u, "codexHeading")
   compare(h.text, "OLLAMA CLOUD"); verify(h.implicitWidth <= h.width)
   var cmd = findChild(u, "codexSource").command
   verify(cmd.indexOf("ollama_cloud_usage.py") >= 0, cmd)
   verify(cmd.indexOf("--hermes-pool-entry") < 0, cmd)
   u.nowMs = (stamp + 31 * 60) * 1000
   u.payload = {available:true, stamp:stamp, tone:"ok", session:{pct:8,resets_at:stamp+3600}, weekly:{pct:33,resets_at:stamp+86400}}
   verify(u.fresh)
   verify(findChild(u, "codexSession").text.indexOf("SITZUNG 8% ↻ ") === 0)
   verify(findChild(u, "codexWeekly").text.indexOf("WOCHE 33% ↻ ") === 0)
   compare(u.fill("weekly"), 0.33)
   verify(u.detailText().indexOf("31 min") >= 0 && u.detailText().indexOf("Codex") < 0, u.detailText())
   u.nowMs = (stamp + 3601) * 1000
   compare(findChild(u, "codexSession").text, "SITZUNG --")
   verify(u.fresh && u.detailText().indexOf("keine aktuelle Messung") >= 0)
   u.nowMs = (stamp + 75 * 60 + 1) * 1000
   verify(!u.fresh)
   verify(findChild(u, "codexUnknown").text.indexOf("Messung veraltet") >= 0)
   u.payload = {available:false, reason:"cache_missing"}
   verify(findChild(u, "codexUnknown").text.indexOf("kein Kontingentstand") >= 0)
  }
 }
}
'''
    run_case(tmp_path, code, 'OllamaCloudUsage::test_quota()')
