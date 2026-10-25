"""Behavioral reproductions of the October 4 monitor review findings."""
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / 'contents/ui/main.qml'
LOGIC = ROOT / 'contents/code/monitor_logic.js'


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'contents/code/{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def qml_function(name):
    source = QML.read_text().split(f'    function {name}(', 1)[1]
    return f'function {name}(' + source.split('\n    }', 1)[0] + '\n}'


def node(script):
    return json.loads(subprocess.check_output(['node', '-e', script], text=True))


def handler(source_id, next_id):
    source = QML.read_text().split(f'id: {source_id}', 1)[1].split(next_id if next_id.startswith('Component.') else f'id: {next_id}', 1)[0]
    body = source.split('onNewData: function(source, data) {', 1)[1].rsplit('\n        }', 1)[0]
    return 'function handle(data) {' + body + '\n}'


@pytest.mark.parametrize('final', [False, True])
def test_hermes_tool_preamble_does_not_complete_turn(tmp_path, final):
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE sessions(id TEXT,source TEXT,parent_session_id TEXT); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,timestamp REAL,tool_calls TEXT,finish_reason TEXT);')
        c.execute("INSERT INTO sessions VALUES('s','telegram',NULL)")
        c.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', [(1,'s','user','Go',1000,None,None),(2,'s','assistant','Working','1002','[{"name":"tool"}]','tool_calls')])
        if final:
            c.execute("INSERT INTO messages VALUES(3,'s','assistant','Done',1600,'[]','stop')")
    assert load_helper('hermes_max_think').longest_completed_turn(db, 2000) == (600 if final else 0)


@pytest.mark.parametrize('route,expected', [
    ('default via 192.0.2.1 dev enp0 proto dhcp', 'enp0'),
    ('default dev tun0 scope link', 'tun0'),
    ('default dev ppp0 proto kernel scope link', 'ppp0'),
])
def test_route_selection_uses_dev_token(tmp_path, route, expected):
    # Run the real helper against a controlled route provider and sysfs inventory.
    import os
    executable = tmp_path / 'ip'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_ROUTE"\n')
    executable.chmod(0o755)
    interfaces = tmp_path / 'net'
    interfaces.mkdir()
    for name in ('enp0', 'tun0', 'ppp0'):
        (interfaces / name).mkdir()
    helper = ROOT / 'contents/code/network_interface.py'
    result = subprocess.run([sys.executable, str(helper), '--sys-net', str(interfaces)], capture_output=True, text=True, env=dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH'], TEST_ROUTE=route))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_http_disconnect_is_unavailable():
    class Disconnect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.close_connection = True
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Disconnect)
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        assert load_helper('ai_services_status').fetch_json(f'http://127.0.0.1:{server.server_port}/health') is None
    finally:
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize('data', [{'stdout':'','exit code':1}, {'stdout':'{}','exit code':0}, {'stdout':'{"gateway":"running","hindsight":"healthy"}','exit code':1}])
def test_service_failure_clears_previous_health(data):
    script = '''var root={hermesGatewayState:'RUNNING',hindsightState:'HEALTHY',openAiActiveKeys:3,openAiTotalKeys:3};
var buffer='',source=''; function disconnectSource(){};
''' + qml_function('resetServices') + '\nroot.resetServices=resetServices;\n' + handler('aiServicesSource','gpuTelemetrySource') + '\nhandle('+json.dumps(data)+'); console.log(JSON.stringify(root));'
    result = node(script)
    assert result['hermesGatewayState'] == result['hindsightState'] == 'UNKNOWN'
    assert result['openAiActiveKeys'] == -1


def test_failed_network_poll_invalidates_rate():
    script = '''var root={down:12500000,up:500000,networkAvailable:true,previousNetworkSampleMs:1000};
var buffer='',source='network';var networkCountersSource={command:'network'};function disconnectSource(){};
''' + qml_function('resetNetwork') + '\nroot.resetNetwork=resetNetwork;\n' + handler('networkCountersSource','Component.onCompleted:') + '\nhandle({"stdout":"","exit code":1});console.log(JSON.stringify(root));'
    result = node(script)
    assert result['networkAvailable'] is False
    assert result['previousNetworkSampleMs'] == 0


def test_history_segments_preserve_gaps_and_zero_measurements():
    script = LOGIC.read_text()+'''\nconsole.log(JSON.stringify(historySegments([0,10,null,20,30,null,null,40])));'''
    assert node(script) == [{'start':0,'end':1},{'start':3,'end':4},{'start':7,'end':7}]


def test_history_samples_require_available_recent_measurements():
    script = LOGIC.read_text()+'''\nconsole.log(JSON.stringify([
telemetrySample(87,false,1000,2000,5000),telemetrySample(87,true,1000,20000,5000),
telemetrySample(0,true,1000,2000,5000),telemetrySample(87,true,0,2000,5000)]));'''
    assert node(script) == [None,None,0,None]


def test_history_timer_records_missing_samples():
    source = QML.read_text()
    body = source.split('root.telemetryNowMs = Date.now()',1)[1].split('computeGraph.requestPaint()',1)[0]
    script = LOGIC.read_text()+'''
var MonitorLogic={telemetrySample:telemetrySample,gpuLoadSample:gpuLoadSample};
var root={cpu:0,ram:0,cpuHistory:[],ramHistory:[],gpu0History:[],gpu1History:[],gpu2History:[],downHistory:[],upHistory:[],gpu0Usage:87,gpu1Usage:0,gpu2Usage:10,gpu0Available:false,gpu1Available:true,gpu2Available:true,networkAvailable:false,down:12000000,up:100000,lastGpuTelemetryMs:Date.now(),lastNetworkTelemetryMs:Date.now(),telemetryNowMs:Date.now(),lastServiceTelemetryMs:Date.now()};
root.gpuLoadStates={};root.push=function(h,v){return h.concat([v]);};root.resetServices=function(){};
''' + body + '\nconsole.log(JSON.stringify({gpu:root.gpu0History,network:root.downHistory}));'
    assert node(script) == {'gpu':[None],'network':[None]}


def test_disk_total_has_only_root_filesystem_producer():
    # Execute every QML sensor assignment that writes diskTotalBytes. Aggregate
    # sensor events must not alter the total used for the root reserve.
    source = QML.read_text()
    assignments = re.findall(r'Sensors.Sensor \{[^\n]*onValueChanged: (root.diskTotalBytes = [^}]+)',source)
    script = 'var root={diskTotalBytes:1000},value=2000;'+ ';'.join(assignments)+';console.log(JSON.stringify(root.diskTotalBytes));'
    assert node(script) == 1000


def test_service_null_counts_do_not_claim_health():
    data = {'stdout':json.dumps({'gateway':'running','hindsight':'healthy','openai_oauth_available':None,'openai_oauth_total':None}),'exit code':0}
    script = "var root={hermesGatewayState:'RUNNING',hindsightState:'HEALTHY',openAiActiveKeys:3,openAiTotalKeys:3};var buffer='',source='';function disconnectSource(){};"+qml_function('resetServices')+';root.resetServices=resetServices;'+handler('aiServicesSource','gpuTelemetrySource')+';handle('+json.dumps(data)+');console.log(JSON.stringify(root.hermesGatewayState));'
    assert node(script) == 'UNKNOWN'


def test_compute_chart_does_not_connect_or_mark_missing_sample():
    source = QML.read_text()
    plot = source.split('                        function plot(data, color',1)[1].split('                        plot(root.gpu0History',1)[0]
    plot = 'function plot(data, color'+plot
    script = LOGIC.read_text()+'''
var MonitorLogic={historySegments:historySegments,historyX:historyX};
var width=120,height=100,plotLeft=0,chartWidth=120,plotTop=2,plotHeight=96;
var root={historySeconds:7,clamp:function(v){return v;}};
var paths=[],path=[],dots=[];
var ctx={beginPath:function(){path=[];},moveTo:function(x,y){path.push([x,y]);},lineTo:function(x,y){path.push([x,y]);},closePath:function(){},fill:function(){},stroke:function(){paths.push(path);},setLineDash:function(){},arc:function(x,y){dots.push([x,y]);}};
''' + plot + '''
plot([10,20,null,30,40,null,null],'red','red',false);
console.log(JSON.stringify({paths:paths.map(function(p){return p.map(function(v){return v[0];});}),dots:dots}));
'''
    assert node(script) == {'paths':[[0,20],[60,80]],'dots':[]}


@pytest.mark.parametrize('age,expected', [(61000,['UNKNOWN','UNKNOWN']),(15000,['RUNNING','HEALTHY'])])
def test_stalled_service_poll_expires_without_expiring_fresh_services(age, expected):
    source = QML.read_text()
    body = 'root.telemetryNowMs = Date.now()'+source.split('root.telemetryNowMs = Date.now()',1)[1].split('computeGraph.requestPaint()',1)[0]
    script = LOGIC.read_text()+'''
var MonitorLogic={telemetrySample:telemetrySample,gpuLoadSample:gpuLoadSample};
var root={hermesGatewayState:'RUNNING',hindsightState:'HEALTHY',openAiActiveKeys:3,openAiTotalKeys:3,cpu:0,ram:0,cpuHistory:[],ramHistory:[],gpu0History:[],gpu1History:[],gpu2History:[],gpuLoadStates:{},downHistory:[],upHistory:[],lastServiceTelemetryMs:Date.now()-61000};
root.push=function(h,v){return h.concat([v]);};
''' .replace('Date.now()-61000','Date.now()-'+str(age)) + qml_function('resetServices')+';root.resetServices=resetServices;'+body+';console.log(JSON.stringify([root.hermesGatewayState,root.hindsightState]));'
    assert node(script) == expected
