"""Replay delayed executable events through the actual QML network handlers."""
import json
from pathlib import Path
import subprocess

import pytest


QML = Path(__file__).parents[1] / "contents/ui/main.qml"


def network_harness():
    qml = QML.read_text()
    reset = qml.split("    function resetNetwork(", 1)[1].split("\n    }", 1)[0]
    detect = qml.split("id: netDetectSource", 1)[1].split("id: networkCountersSource", 1)[0]
    counters = qml.split("id: networkCountersSource", 1)[1].split("Component.onCompleted:", 1)[0]

    def body(block):
        return block.split("onNewData: function(source, data) {", 1)[1].rsplit("\n        }", 1)[0]

    command = counters.split("property string command: ", 1)[1].split("\n", 1)[0]
    return 'const MonitorLogic=require(' + json.dumps(str(QML.parent.parent / 'code/monitor_logic.js')) + ');\n' + """
var now=1000; Date.now=()=>now;
var root={netIf:'eth0',down:0,up:0,networkAvailable:false,
 previousRxBytes:-1,previousTxBytes:-1,previousNetworkSampleMs:0,lastNetworkTelemetryMs:0};
var disconnected=[], connected=[];
""" + "function resetNetwork(" + reset + "\n}\nroot.resetNetwork=resetNetwork;\n" + """
var networkCountersSource=(function(){
 var buffer='',scriptPath='/widget/code/network_counters.sh';
 function disconnectSource(source){disconnected.push(source);}
 return {
  generation:0,
  get command(){return COMMAND;},
  get buffer(){return buffer;},set buffer(value){buffer=value;},
  disconnectSource:disconnectSource,
  connectSource:function(source){connected.push(source);},
  handle:function(source,data){COUNTERS}
 };
})();
var detect=(function(){var buffer='';function disconnectSource(){};
 return function(source,data){DETECT};})();
function switchTo(iface){detect('detect',{'stdout':iface,'exit code':0});}
function poll(source,rx,tx){networkCountersSource.handle(source,{'stdout':rx+' '+tx,'exit code':0});}
""".replace("COMMAND", command).replace("COUNTERS", body(counters)).replace("DETECT", body(detect))


def run(script):
    return json.loads(subprocess.check_output(["node", "-e", network_harness() + script], text=True))


@pytest.mark.parametrize("late", [
    {"stdout": "1000 1000", "exit code": 0},
    {"stdout": "", "exit code": 1},
    {"stdout": "1000 1000"},
])
def test_interface_switch_ignores_old_events_and_resets_partial_output(late):
    actual = run("""
var old=networkCountersSource.command;
networkCountersSource.handle(old,{'stdout':'1000 '});
switchTo('tun0');
var current=networkCountersSource.command;
networkCountersSource.handle(old,LATE);
poll(current,100000000,100000000);
var first=[root.networkAvailable,root.down,root.up];
now=2000;poll(current,100000100,100000200);
networkCountersSource.handle(old,{'stdout':'','exit code':1});
console.log(JSON.stringify([first,root.networkAvailable,root.down,root.up,disconnected.includes(old)]));
""".replace("LATE", json.dumps(late)))
    assert actual == [[False, 0, 0], True, 100, 200, True]


def test_returning_to_same_interface_rejects_its_previous_generation():
    actual = run("""
var old=networkCountersSource.command;
switchTo('tun0');switchTo('eth0');
var current=networkCountersSource.command;
poll(current,100000000,100000000);
networkCountersSource.handle(old,{'stdout':'1000 1000','exit code':0});
now=2000;poll(current,100000100,100000200);
console.log(JSON.stringify([root.networkAvailable,root.down,root.up]));
""")
    assert actual == [True, 100, 200]
