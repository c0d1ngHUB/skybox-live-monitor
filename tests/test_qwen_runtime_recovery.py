"""Exercise the actual handoff scripts with isolated service boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def runtime_script(name):
    versioned = ROOT / 'runtime' / name
    assert versioned.is_file(), 'Runtime tests must exercise the versioned deployment source'
    return versioned


def run_start(tmp_path, *, failure, backend='active', engine='inactive', controller_timeout=None, monkeypatch=None):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'engine': engine, 'backend': backend, 'gateway': 'active',
                                 'failure': failure, 'calls': []}))
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    ctl = bindir / 'systemctl'
    ctl.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
p=Path({str(state)!r});s=json.loads(p.read_text());args=sys.argv[1:]
verb=next((a for a in args if a in ('is-active','start','stop','status','show')),None)
unit=next((a for a in args if a.endswith('.service')),'')
key={{'qwen38-ninfer.service':'engine','hindsight-ollama.service':'backend','qwen38-gateway.service':'gateway'}}.get(unit)
s['calls'].append([verb,unit]);rc=0
if verb=='is-active':
 value=s.get(key,'unknown')
 if key=='backend' and s['failure']=='snapshot':value='unknown'
 print(value);rc=0 if value=='active' else 3
elif verb=='show':
 print('LoadState=loaded\\nActiveState='+s.get(key,'unknown')+'\\nSubState=dead\\nJob=0')
elif verb=='start':
 if key=='engine' and s['engine']!='active':
  s['backend']='inactive' # Simulate successful ExecStartPre releasing Blackwell.
  s['engine']='failed' if s['failure']=='engine' else 'active'
  if s['failure']=='engine':rc=7
 elif key=='gateway' and s['failure']=='gateway':rc=8
 elif key=='backend' and s['failure']=='restore':rc=9
 elif key:s[key]='active'
elif verb=='stop':
 if s['failure']=='stop':rc=10
 elif key:s[key]='inactive'
p.write_text(json.dumps(s));sys.exit(rc)
''')
    ctl.chmod(0o755)
    curl = bindir / 'curl'
    curl.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
s=json.loads(Path({str(state)!r}).read_text())
if any('/props' in a for a in sys.argv) and s['failure'] in ('readiness','stop','restore','container','docker'):sys.exit(22)
print('{{"models":[]}}')
''')
    curl.chmod(0o755)
    docker = bindir / 'docker'
    docker.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
s=json.loads(Path({str(state)!r}).read_text())
if s['failure']=='docker':sys.exit(1)
if s['engine']=='active' or s['failure']=='container':print('running')
''')
    docker.chmod(0o755)
    if controller_timeout is not None:
        home=tmp_path/'home';scripts=home/'.local/bin';scripts.mkdir(parents=True)
        for name in ('qwen38-start','qwen38-stop'):
            p=scripts/name;p.write_text(runtime_script(name).read_text());p.chmod(0o755)
        monkeypatch.setenv('HOME',str(home))
        monkeypatch.setenv('XDG_RUNTIME_DIR',str(tmp_path/'runtime'))
        monkeypatch.setenv('PATH',str(bindir)+os.pathsep+os.environ['PATH'])
        helper=load_controller(controller_timeout)
        result=helper.run_action('start')
        return result,json.loads(state.read_text()),helper,state
    script = tmp_path / 'qwen38-start'
    script.write_text(runtime_script('qwen38-start').read_text().replace('READY_TIMEOUT=180', 'READY_TIMEOUT=0'))
    result = subprocess.run(['/bin/bash', str(script)], capture_output=True, text=True, timeout=10,
        env={**os.environ, 'PATH': str(bindir)+os.pathsep+os.environ['PATH']})
    return result, json.loads(state.read_text())


def load_controller(timeout):
    spec=importlib.util.spec_from_file_location('recovery_controller',ROOT/'contents/code/qwen_control.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    helper.CONTROL_TIMEOUT=timeout;helper.TERMINATE_GRACE=0.3;helper.KILL_GRACE=0.2;helper.RECOVERY_TIMEOUT=3
    return helper


@pytest.mark.parametrize('failure', ['engine', 'readiness', 'gateway'])
def test_failed_start_restores_previously_active_backend_after_engine_is_stopped(tmp_path, failure):
    result, state = run_start(tmp_path, failure=failure)
    assert result.returncode != 0
    assert state['backend'] == 'active', result.stderr
    assert state['engine'] == 'inactive'
    stop = state['calls'].index(['stop', 'qwen38-ninfer.service'])
    restore = state['calls'].index(['start', 'hindsight-ollama.service'])
    assert stop < restore


def test_failed_start_preserves_previously_inactive_backend(tmp_path):
    result, state = run_start(tmp_path, failure='engine', backend='inactive')
    assert result.returncode != 0
    assert state['backend'] == 'inactive'
    assert ['start', 'hindsight-ollama.service'] not in state['calls']
    assert state['engine'] == 'inactive'


def test_rollback_does_not_restore_backend_until_engine_stop_succeeds(tmp_path):
    result, state = run_start(tmp_path, failure='stop')
    assert result.returncode != 0
    assert state['engine'] == 'active'
    assert ['stop', 'qwen38-ninfer.service'] in state['calls']
    assert ['start', 'hindsight-ollama.service'] not in state['calls']
    assert 'recovery' in result.stderr.lower() or 'rollback' in result.stderr.lower()


def test_failed_restore_is_visible_in_original_start_error(tmp_path):
    result, state = run_start(tmp_path, failure='restore')
    assert result.returncode != 0
    assert state['engine'] == 'inactive'
    assert ['start', 'hindsight-ollama.service'] in state['calls']
    assert 'recovery' in result.stderr.lower() or 'rollback' in result.stderr.lower()


@pytest.mark.parametrize('failure', ['stop','restore','container','docker'])
def test_unsettled_start_cleanup_has_distinct_recovery_exit(tmp_path,failure):
    result,state=run_start(tmp_path,failure=failure)
    assert result.returncode==75


@pytest.mark.parametrize('backend', ['active','inactive'])
def test_controller_timeout_preserves_prior_backend_state(tmp_path,monkeypatch,backend):
    result,state,helper,path=run_start(tmp_path,failure='readiness',backend=backend,
        controller_timeout=0.4,monkeypatch=monkeypatch)
    assert result['ok'] is False
    assert state['engine']=='inactive'
    assert state['backend']==backend
    assert not helper.recovery_barrier().exists()


def test_retry_after_failed_timeout_cleanup_retains_original_off_policy(tmp_path,monkeypatch):
    first,state,helper,path=run_start(tmp_path,failure='stop',backend='inactive',
        controller_timeout=0.4,monkeypatch=monkeypatch)
    assert first['state']=='unknown'
    assert helper.recovery_barrier().exists()
    state['failure']='readiness';path.write_text(json.dumps(state))
    fresh=load_controller(0.4)
    result=fresh.run_action('start')
    state=json.loads(path.read_text())
    assert result['ok'] is False
    assert state['engine']=='inactive'
    assert state['backend']=='inactive'
    assert ['start','hindsight-ollama.service'] not in state['calls']


@pytest.mark.parametrize('failure', ['container','docker'])
def test_rollback_does_not_reload_backend_without_confirmed_container_cleanup(tmp_path, failure):
    result,state=run_start(tmp_path,failure=failure)
    assert result.returncode != 0
    assert ['start','hindsight-ollama.service'] not in state['calls']
    assert 'recovery' in result.stderr.lower() or 'rollback' in result.stderr.lower()


def test_unreadable_previous_backend_state_prevents_any_mutation(tmp_path):
    result, state = run_start(tmp_path, failure='snapshot')
    assert result.returncode != 0
    assert not any(action in ('start','stop') for action,unit in state['calls'])


def test_idempotent_start_failure_preserves_existing_qwen_session(tmp_path):
    result, state = run_start(tmp_path, failure='gateway', backend='inactive', engine='active')
    assert result.returncode != 0
    assert state['engine'] == 'active'
    assert ['stop', 'qwen38-ninfer.service'] not in state['calls']


def test_stop_never_reloads_hindsight_after_unsettled_engine_stop(tmp_path):
    calls = tmp_path/'calls'
    bindir = tmp_path/'bin'
    bindir.mkdir()
    ctl = bindir/'systemctl'
    ctl.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
        +f'with Path({str(calls)!r}).open("a") as f:f.write(" ".join(sys.argv[1:])+"\\n")\n'
        +'if "stop" in sys.argv:sys.exit(1)\n'
        +'if "is-active" in sys.argv:print("active")\n')
    ctl.chmod(0o755)
    curl = bindir/'curl'
    curl.write_text('#!/bin/sh\nprintf \'{"models":[]}\\n\'\n')
    curl.chmod(0o755)
    result = subprocess.run(['/bin/bash',str(runtime_script('qwen38-stop'))], capture_output=True,text=True,
        timeout=10,env={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH']})
    assert result.returncode != 0
    assert 'start hindsight-ollama.service' not in calls.read_text()


@pytest.mark.parametrize('docker_output,docker_exit', [('running',0),('',1)])
def test_stop_requires_confirmed_container_absence_before_hindsight_reload(tmp_path,docker_output,docker_exit):
    calls=tmp_path/'calls';bindir=tmp_path/'bin';bindir.mkdir()
    ctl=bindir/'systemctl'
    ctl.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
        +f'with Path({str(calls)!r}).open("a") as f:f.write(" ".join(sys.argv[1:])+"\\n")\n'
        +'if "is-active" in sys.argv:print("inactive" if "qwen38-ninfer.service" in sys.argv else "active")\n')
    ctl.chmod(0o755)
    docker=bindir/'docker';docker.write_text(f'#!/bin/sh\nprintf "%s\\n" "{docker_output}"\nexit {docker_exit}\n');docker.chmod(0o755)
    curl=bindir/'curl';curl.write_text('#!/bin/sh\nprintf \'{"models":[]}\\n\'\n');curl.chmod(0o755)
    result=subprocess.run(['/bin/bash',str(runtime_script('qwen38-stop'))],capture_output=True,text=True,timeout=10,
        env={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH']})
    assert result.returncode != 0
    assert 'start hindsight-ollama.service' not in calls.read_text()


def test_gateway_error_cannot_hide_an_unsettled_unit_recovery_result(tmp_path):
    bindir=tmp_path/'bin';bindir.mkdir()
    ctl=bindir/'systemctl'
    ctl.write_text(f'#!{sys.executable}\nimport sys\n'
        +'if "is-active" in sys.argv:print("inactive" if "qwen38-gateway.service" in sys.argv else "active")\n')
    ctl.chmod(0o755)
    docker=bindir/'docker';docker.write_text('#!/bin/sh\nexit 0\n');docker.chmod(0o755)
    curl=bindir/'curl';curl.write_text('#!/bin/sh\nprintf \'{"models":[]}\\n\'\n');curl.chmod(0o755)
    result=subprocess.run(['/bin/bash',str(runtime_script('qwen38-stop'))],capture_output=True,text=True,timeout=10,
        env={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH']})
    assert result.returncode==75
