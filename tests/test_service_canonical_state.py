"""Canonical service states must survive the QML color pipeline."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('state', ['OPERATIONAL', 'DEGRADED', 'OFFLINE', 'NOT_CONFIGURED', 'UNKNOWN'])
def test_canonical_normalization_is_idempotent(state):
    js = f'''const m=require({json.dumps(str(ROOT/'contents/code/monitor_logic.js'))});
console.log(JSON.stringify(m.normalizeServiceState(m.normalizeServiceState({json.dumps(state)}))));'''
    assert json.loads(subprocess.check_output(['node', '-e', js], text=True)) == state


def test_healthy_oauth_uses_cyan_through_real_qml_function():
    source = (ROOT/'contents/ui/main.qml').read_text()
    function = 'function serviceToneColor(' + source.split('function serviceToneColor(', 1)[1].split('\n    }', 1)[0] + '\n}'
    js = f'''const MonitorLogic=require({json.dumps(str(ROOT/'contents/code/monitor_logic.js'))});
const root={{cyan:'cyan',warning:'warning',critical:'critical',muted:'muted'}};
{function}
console.log(JSON.stringify(serviceToneColor(MonitorLogic.openAiOauthState(3,3))));'''
    assert json.loads(subprocess.check_output(['node', '-e', js], text=True)) == 'cyan'
