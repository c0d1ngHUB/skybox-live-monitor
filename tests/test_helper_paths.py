"""Execute actual QML helper command expressions against harmless fixture files."""
import json
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
LOGIC = ROOT / 'contents/code/monitor_logic.js'
SOURCES = []
for component in ('main.qml', 'HermesLive.qml', 'ClaudeUsage.qml'):
    source = (ROOT / 'contents/ui' / component).read_text()
    for match in re.finditer(r'property string scriptPath:\s*([^\n]+)\n\s*(?:(?!property string command:)[^\n]*\n)*?\s*property string command:\s*([^\n]+)', source):
        name = re.search(r'\.\./code/([^"\s]+)', match[1]).group(1)
        SOURCES.append((component, name, match[1], match[2]))


def test_all_helper_paths_are_covered():
    assert len(SOURCES) == 12
    assert {row[1] for row in SOURCES} == {
        'hermes_live.py', 'hermes_details.py', 'disk_usage.py', 'hermes_max_think.py',
        'hindsight_observation_health.py', 'hermes_openai_keys.py',
        'ai_services_status.py', 'gpu_telemetry.py', 'cpu_process_snapshot.py',
        'network_interface.py', 'network_counters.sh', 'claude_usage.py',
    }


@pytest.mark.parametrize('component,name,path_expression,command_expression', SOURCES, ids=[row[1] for row in SOURCES])
@pytest.mark.parametrize('folder', ['normal', "user name ä ' % # ; $(false)"])
def test_helper_command_decodes_and_shell_quotes(tmp_path, component, name, path_expression, command_expression, folder):
    helper = tmp_path / folder / name
    helper.parent.mkdir()
    if name.endswith('.py'):
        helper.write_text('import sys\nprint(sys.argv[0])\n')
    else:
        helper.write_text('printf "%s\\n" "$0"\n')
    js = f'''
const MonitorLogic=require({json.dumps(str(LOGIC))});
const Qt={{resolvedUrl:()=>{json.dumps(helper.as_uri())}}};
const root={{netIf:'eth0'}}, networkCountersSource={{generation:7}},health={{session:'123-456'}},requestId=1;
const scriptPath={path_expression};
const command={command_expression};
console.log(JSON.stringify({{scriptPath,command}}));
'''
    result = json.loads(subprocess.check_output(['node', '-e', js], text=True))
    assert result['scriptPath'] == str(helper)
    run = subprocess.run(result['command'], shell=True, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == str(helper)
