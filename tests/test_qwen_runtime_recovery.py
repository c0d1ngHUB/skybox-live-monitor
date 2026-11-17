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


def run_start(tmp_path, *, failure, engine='inactive', controller_timeout=None, monkeypatch=None):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'engine': engine, 'gateway': 'active',
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
key={{'qwen38-ninfer.service':'engine','qwen38-gateway.service':'gateway'}}.get(unit)
s['calls'].append([verb,unit]);rc=0
if verb=='is-active':
 value=s.get(key,'unknown')
 print(value);rc=0 if value=='active' else 3
elif verb=='show':
 print('LoadState=loaded\\nActiveState='+s.get(key,'unknown')+'\\nSubState=dead\\nJob=0')
elif verb=='start':
 if key=='engine' and s['engine']!='active':
  s['engine']='failed' if s['failure']=='engine' else 'active'
  if s['failure']=='engine':rc=7
 elif key=='gateway' and s['failure']=='gateway':rc=8
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
if any('/props' in a for a in sys.argv) and s['failure'] in ('readiness','stop','container','docker'):sys.exit(22)
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


def no_local_backend_touched(calls):
    """Hindsight runs on Ollama Cloud: only the Qwen engine and gateway exist."""
    return all(unit in ('', 'qwen38-ninfer.service', 'qwen38-gateway.service') for _verb, unit in calls)


def load_controller(timeout):
    spec=importlib.util.spec_from_file_location('recovery_controller',ROOT/'contents/code/qwen_control.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    helper.CONTROL_TIMEOUT=timeout;helper.TERMINATE_GRACE=0.3;helper.KILL_GRACE=0.2;helper.RECOVERY_TIMEOUT=3
    return helper


@pytest.mark.parametrize('failure', ['engine', 'readiness', 'gateway'])
def test_failed_start_stops_the_engine_and_touches_no_other_backend(tmp_path, failure):
    result, state = run_start(tmp_path, failure=failure)
    assert result.returncode not in (0, 75), result.stderr
    assert state['engine'] == 'inactive'
    assert ['stop', 'qwen38-ninfer.service'] in state['calls']
    assert no_local_backend_touched(state['calls']), state['calls']


def test_rollback_reports_an_unsettled_engine_stop(tmp_path):
    result, state = run_start(tmp_path, failure='stop')
    assert result.returncode == 75
    assert state['engine'] == 'active'
    assert ['stop', 'qwen38-ninfer.service'] in state['calls']
    assert 'rollback' in result.stderr.lower()


@pytest.mark.parametrize('failure', ['stop','container','docker'])
def test_unsettled_start_cleanup_has_distinct_recovery_exit(tmp_path,failure):
    result,state=run_start(tmp_path,failure=failure)
    assert result.returncode==75
    assert no_local_backend_touched(state['calls']), state['calls']


def test_start_script_has_no_local_hindsight_backend_logic():
    source = runtime_script('qwen38-start').read_text()
    for stale in ('nimble', 'restore_hindsight', 'gpt-oss', 'Hindsight not reloaded'):
        assert stale not in source, stale


def test_controller_timeout_stops_engine_and_clears_barrier(tmp_path,monkeypatch):
    result,state,helper,path=run_start(tmp_path,failure='readiness',
        controller_timeout=0.4,monkeypatch=monkeypatch)
    assert result['ok'] is False
    assert state['engine']=='inactive'
    assert not helper.recovery_barrier().exists()
    assert no_local_backend_touched(state['calls']), state['calls']


def test_retry_after_failed_timeout_cleanup_reconciles_before_next_start(tmp_path,monkeypatch):
    first,state,helper,path=run_start(tmp_path,failure='stop',
        controller_timeout=0.4,monkeypatch=monkeypatch)
    assert first['state']=='unknown'
    assert helper.recovery_barrier().exists()
    state['failure']='readiness';path.write_text(json.dumps(state))
    fresh=load_controller(0.4)
    result=fresh.run_action('start')
    state=json.loads(path.read_text())
    assert result['ok'] is False
    assert state['engine']=='inactive'
    assert not fresh.recovery_barrier().exists()
    assert no_local_backend_touched(state['calls']), state['calls']


@pytest.mark.parametrize('legacy_restore', [True, False])
def test_marker_from_previous_version_is_reconciled_without_restore_flag(tmp_path,monkeypatch,legacy_restore):
    """Older controllers stored restore_hindsight and passed --no-restore."""
    _,state,helper,path=run_start(tmp_path,failure='readiness',
        controller_timeout=0.4,monkeypatch=monkeypatch)
    barrier=helper.recovery_barrier()
    barrier.write_text(json.dumps({'processes_settled':True,'restore_hindsight':legacy_restore}))
    state['failure']='none';state['engine']='active';path.write_text(json.dumps(state))
    result=load_controller(5).run_action('stop')
    assert result['ok'] is True, result
    assert not barrier.exists()
    assert json.loads(path.read_text())['engine']=='inactive'


@pytest.mark.parametrize('failure', ['container','docker'])
def test_rollback_without_confirmed_container_cleanup_is_reported(tmp_path, failure):
    result,state=run_start(tmp_path,failure=failure)
    assert result.returncode == 75
    assert 'rollback' in result.stderr.lower()


def test_idempotent_start_failure_preserves_existing_qwen_session(tmp_path):
    result, state = run_start(tmp_path, failure='gateway', engine='active')
    assert result.returncode != 0
    assert state['engine'] == 'active'
    assert ['stop', 'qwen38-ninfer.service'] not in state['calls']


def test_unsettled_engine_stop_is_a_recovery_exit(tmp_path):
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
    assert result.returncode == 75
    assert 'nimble' not in calls.read_text()


@pytest.mark.parametrize('docker_output,docker_exit', [('running',0),('',1)])
def test_stop_requires_confirmed_container_absence(tmp_path,docker_output,docker_exit):
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
    assert result.returncode == 75
    assert 'nimble' not in calls.read_text()


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



def _stop_with(tmp_path, *, gateway, args=()):
    calls=tmp_path/'calls';bindir=tmp_path/'bin';bindir.mkdir()
    ctl=bindir/'systemctl'
    ctl.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
        +f'with Path({str(calls)!r}).open("a") as f:f.write(" ".join(sys.argv[1:])+"\\n")\n'
        +'if "is-active" in sys.argv:print("inactive" if "qwen38-ninfer.service" in sys.argv else '+repr(gateway)+')\n')
    ctl.chmod(0o755)
    docker=bindir/'docker';docker.write_text('#!/bin/sh\nexit 0\n');docker.chmod(0o755)
    result=subprocess.run(['/bin/bash',str(runtime_script('qwen38-stop')),*args],capture_output=True,text=True,
        timeout=10,env={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH']})
    return result,(calls.read_text() if calls.exists() else '')


def test_stop_settles_engine_and_reports_gateway(tmp_path):
    result,calls=_stop_with(tmp_path,gateway='active')
    assert result.returncode==0, result.stderr
    assert 'qwen38-ninfer.service      inactive' in result.stdout
    assert 'qwen38-gateway.service     active' in result.stdout
    assert 'nimble' not in calls and 'hindsight' not in calls.lower()


def test_inactive_gateway_is_warned_but_does_not_block_recovery(tmp_path):
    result,_=_stop_with(tmp_path,gateway='inactive')
    assert result.returncode==0
    assert 'qwen38-gateway.service is inactive' in result.stderr


@pytest.mark.parametrize('args', [('--no-restore',), ('extra',)])
def test_stop_rejects_arguments_without_touching_units(tmp_path,args):
    result,calls=_stop_with(tmp_path,gateway='active',args=args)
    assert result.returncode==64
    assert calls==''


def test_start_names_a_unit_that_is_not_active_instead_of_aborting_silently(tmp_path):
    """`set -e` must not turn the final is-active check into a silent exit 3."""
    # Prepare the fake runtime with a normal start, then make the gateway's
    # start "succeed" without the unit staying active.
    result, state = run_start(tmp_path, failure='none')
    assert result.returncode == 0, result.stderr
    ctl = tmp_path / 'bin' / 'systemctl'
    ctl.write_text(ctl.read_text().replace(
        " elif key:s[key]='active'", " elif key=='gateway':s[key]='inactive'\n elif key:s[key]='active'"))
    state.update(engine='inactive', gateway='inactive', calls=[])
    (tmp_path / 'state.json').write_text(json.dumps(state))
    result = subprocess.run(['/bin/bash', str(tmp_path / 'qwen38-start')], capture_output=True, text=True,
        timeout=10, env={**os.environ, 'PATH': str(tmp_path / 'bin') + os.pathsep + os.environ['PATH']})
    state = json.loads((tmp_path / 'state.json').read_text())
    assert result.returncode == 1, result.stderr
    assert 'qwen38-gateway.service     inactive' in result.stdout
    assert 'qwen38-gateway.service is inactive' in result.stderr
    assert state['engine'] == 'inactive'  # The rollback still stops the engine.


def _stop_with_failed_unit(tmp_path, *, reset_works):
    calls = tmp_path / 'calls'; marker = tmp_path / 'reset'; bindir = tmp_path / 'bin'; bindir.mkdir()
    ctl = bindir / 'systemctl'
    ctl.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
        + f'with Path({str(calls)!r}).open("a") as f:f.write(" ".join(sys.argv[1:])+"\\n")\n'
        + f'm=Path({str(marker)!r})\n'
        + f'if "reset-failed" in sys.argv:\n    {"m.write_text(chr(49))" if reset_works else "sys.exit(1)"}\n'
        + 'if "is-active" in sys.argv:\n'
        + '    if "qwen38-ninfer.service" in sys.argv:print("inactive" if m.exists() else "failed");sys.exit(3)\n'
        + '    print("active")\n')
    ctl.chmod(0o755)
    docker = bindir / 'docker'; docker.write_text('#!/bin/sh\nexit 0\n'); docker.chmod(0o755)
    result = subprocess.run(['/bin/bash', str(runtime_script('qwen38-stop'))], capture_output=True, text=True,
        timeout=10, env={**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH']})
    return result, calls.read_text()


@pytest.mark.parametrize('reset_works,shown', [(True, 'inactive'), (False, 'failed')])
def test_stop_treats_a_failed_unit_as_settled_like_the_controller(tmp_path, reset_works, shown):
    """qwen_control.handoff_settled accepts failed; exit 75 here would lock the button."""
    result, calls = _stop_with_failed_unit(tmp_path, reset_works=reset_works)
    assert result.returncode == 0, result.stderr
    assert 'reset-failed qwen38-ninfer.service' in calls
    assert f'qwen38-ninfer.service      {shown}' in result.stdout
    assert 'nimble' not in calls
