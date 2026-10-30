"""Executable DataSource names must come from a small, bounded set.

Plasma5Support never forgets a source name: every DataSource attached to the
"executable" engine keeps one QQmlPropertyMap key per name ever connected (also
DataSources that never used that name), and each new key rebuilds the whole
meta-object. A per-poll counter in the name therefore made plasmashell's main
thread stall for 0.6-1.9 s after a few hours, which froze the desktop and left
null slots (gaps) in the 1-s history graph.
"""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).parents[1]
UI = ROOT / 'contents/ui'
MAX_NAMES = 4


def command_expressions():
    """Every executable source name: `command` properties and the imperative
    `activeCommand = ...` assignment in QwenControl."""
    rows = []
    for qml in sorted(UI.glob('*.qml')):
        text = qml.read_text()
        for match in re.finditer(r'property string command:\s*([^\n]+)', text):
            rows.append((qml.name, match[1].strip()))
        for match in re.finditer(r'^\s*activeCommand\s*=\s*("python3[^\n]+)', text, re.M):
            rows.append((qml.name, match[1].strip()))
    return rows


def test_request_counter_commands_rotate_through_a_bounded_set():
    rows = [row for row in command_expressions() if 'requestId' in row[1]]
    assert {name for name, _ in rows} >= {'HermesLive.qml', 'QwenControl.qml'}, rows
    for component, expression in rows:
        js = f'''
const MonitorLogic={{shellQuote:s=>"'"+s+"'"}};
const scriptPath="/x/helper.py", quotedPath="'/x/helper.py'", operation="status";
const names=new Set();
let previous=null, consecutiveRepeat=false;
for (let requestId=0; requestId<5000; requestId++) {{
  const command={expression};
  if (command===previous) consecutiveRepeat=true;
  names.add(command); previous=command;
}}
console.log(JSON.stringify({{count:names.size, consecutiveRepeat}}));
'''
        result = json.loads(subprocess.check_output(['node', '-e', js], text=True))
        assert result['count'] <= MAX_NAMES, (component, expression, result)
        # A stalled request is abandoned by bumping requestId; the next name must
        # differ so its late output is still rejected by the name guard.
        assert result['consecutiveRepeat'] is False, (component, expression)


def test_no_command_embeds_a_clock_or_random_value():
    for component, expression in command_expressions():
        assert 'Date.now' not in expression and 'Math.random' not in expression, (component, expression)
