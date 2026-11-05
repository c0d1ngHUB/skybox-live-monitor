"""Behavioral regressions for the 2026-10-25 review (HEAD 3566225)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / 'contents/ui/main.qml'
QWEN_QML = ROOT / 'contents/ui/QwenControl.qml'
sys.path.insert(0, str(Path(__file__).parent))
from test_review_fixes import handler, node, qml_function  # noqa: E402
from qml_support import qml_runner  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name + '_r1025', ROOT / f'contents/code/{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module here
    spec.loader.exec_module(module)
    return module


def qml_property(source_id, name):
    block = QML.read_text().split(f'id: {source_id}', 1)[1]
    line = block.split(f'property string {name}: ', 1)[1].split('\n', 1)[0]
    return json.loads(line)


# --- AI services poll -------------------------------------------------------

def test_service_poll_reports_only_gateway_and_hindsight(monkeypatch, capsys):
    ai = load('ai_services_status')
    calls = []
    monkeypatch.setattr(ai.subprocess, 'run', lambda command, *a, **k: calls.append(command))
    monkeypatch.setattr(ai, 'hermes_gateway_status', lambda: ai.ServiceStatus('running'))
    monkeypatch.setattr(ai, 'hindsight_status', lambda: ai.ServiceStatus('healthy'))
    assert ai.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {'gateway': 'running', 'hindsight': 'healthy'}
    assert calls == []


def test_service_payload_sets_gateway_and_hindsight_state():
    data = {'stdout': json.dumps({'gateway': 'running', 'hindsight': 'healthy'}), 'exit code': 0}
    script = ("var root={hermesGatewayState:'UNKNOWN',hindsightState:'UNKNOWN',lastServiceTelemetryMs:0};"
              "var buffer='',source='';function disconnectSource(){};" + qml_function('resetServices')
              + ';root.resetServices=resetServices;' + handler('aiServicesSource', 'gpuTelemetrySource')
              + ';handle(' + json.dumps(data) + ');console.log(JSON.stringify([root.hermesGatewayState,root.hindsightState]));')
    assert node(script) == ['RUNNING', 'HEALTHY']


# --- Fast GPU path and process count ----------------------------------------

def test_fast_gpu_query_is_bounded_by_a_timeout():
    command = qml_property('gpuLoadSource', 'command')
    assert command.startswith('timeout ')
    result = subprocess.run(['sh', '-c', command.replace('nvidia-smi', 'sleep 5 # ', 1)],
                            capture_output=True, timeout=10)
    assert result.returncode != 0  # killed by timeout, not waiting 5 s


def test_process_count_excludes_its_own_measurement_processes():
    command = qml_property('processCountSource', 'command')
    # The executable engine runs commands through /bin/sh -c (verified with a
    # live Plasma DataSource probe); emulate exactly that.
    before = sum(1 for p in os.listdir('/proc') if p.isdigit())
    counted = int(subprocess.check_output(['/bin/sh', '-c', command], text=True).strip())
    after = sum(1 for p in os.listdir('/proc') if p.isdigit())
    # This interpreter is already counted in before/after; only the shell is extra.
    assert min(before, after) - 3 <= counted <= max(before, after) + 3
    assert 'ps ' not in command and 'wc ' not in command


# --- Qwen control ------------------------------------------------------------

def test_qwen_control_does_not_shadow_item_state():
    text = QWEN_QML.read_text()
    assert 'property string state:' not in text
    assert 'property string qwenState:' in text
