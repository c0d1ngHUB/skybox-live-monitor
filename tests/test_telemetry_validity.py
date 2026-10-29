"""Regression coverage for unknown telemetry and elapsed history slots."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / 'contents/ui/main.qml'
LOGIC = ROOT / 'contents/code/monitor_logic.js'


def qml_function(name):
    source = QML.read_text().split(f'    function {name}(', 1)[1]
    return f'function {name}(' + source.split('\n    }', 1)[0] + '\n}'


def node(script):
    prelude = f'const MonitorLogic=require({json.dumps(str(LOGIC))});\n'
    return json.loads(subprocess.check_output(['node', '-e', prelude + script], text=True))


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('power,expected', [(None, 'POWER --/115 W'), (0, 'POWER 0/115 W'), (12.6, 'POWER 13/115 W')])
def test_gpu_power_missing_does_not_invent_zero_or_hide_other_metrics(index, power, expected):
    script = 'const root={gpuProcessRows:()=>[]};\n' + qml_function('applyGpuTelemetry') + '\n' + qml_function('gpuPowerText')
    entry = dict(short_name='Test', temperature_c=40, memory_used_mib=200, memory_total_mib=1000,
                 power_draw_w=power, power_limit_w=115, process_count=0, processes=[])
    script += f'\napplyGpuTelemetry({json.dumps(entry)},{index});\n'
    script += f'console.log(JSON.stringify([gpuPowerText(root.gpu{index}PowerDrawWatts,root.gpu{index}PowerLimitWatts),root.gpu{index}Available,root.gpu{index}Temp,root.gpu{index}VramUsedMiB]));'
    assert node(script) == [expected, True, 40, 200]


def history_ticks(times):
    source = QML.read_text()
    body = 'root.telemetryNowMs = Date.now()' + source.split('root.telemetryNowMs = Date.now()', 1)[1].split('computeGraph.requestPaint()', 1)[0]
    script = '''const root={cpu:20,ram:0,cpuHistory:[],ramHistory:[],gpu0History:[],gpu1History:[],downHistory:[],upHistory:[],historySeconds:120,historyLastTickMs:0,lastServiceTelemetryMs:0,networkAvailable:true,down:0,up:40};
''' + qml_function('push') + '\nroot.push=push;\n'
    script += f'for(const now of {json.dumps(times)}){{Date.now=()=>now;root.lastNetworkTelemetryMs=now;root.gpuLoadStates={{"0":{{available:true,usage:20,stampMs:now}},"1":{{available:true,usage:0,stampMs:now}}}};\n' + body + '\n}\nconsole.log(JSON.stringify([root.gpu0History,root.gpu1History,root.downHistory,root.upHistory]));'
    return node(script)


def test_history_inserts_unobserved_one_second_slots_and_preserves_zero():
    assert history_ticks([10000, 11050, 14000]) == [
        [20, 20, None, None, 20], [0, 0, None, None, 0],
        [0, 0, None, None, 0], [40, 40, None, None, 40]]


def test_ten_minute_pause_keeps_only_last_two_minutes_and_current_sample():
    histories = history_ticks([10000, 610000])
    assert histories == [[None] * 119 + [v] for v in [20, 0, 0, 40]]


def test_history_clock_reversal_discards_future_slots_and_resumes_normally():
    assert history_ticks([10000, 11000, 2000, 3000]) == [[20, 20], [0, 0], [0, 0], [40, 40]]


def test_history_bucket_alignment_does_not_accumulate_timer_drift():
    assert history_ticks([10050, 11900, 12800, 13010])[0] == [20, 20, 20, 20]


def test_future_telemetry_is_unknown_after_clock_reversal():
    assert node('console.log(JSON.stringify([MonitorLogic.telemetrySample(0,true,20000,10000,5000),MonitorLogic.gpuLoadSample({available:true,usage:90,stampMs:20000},10000)]));') == [None, None]


def system_sensor_script(sensor_id, field):
    # Execute the production receive handler, including its ready-status check.
    line = next(line for line in QML.read_text().splitlines() if 'Sensors.Sensor {' in line and f'sensorId: "{sensor_id}"' in line)
    handler = line.split('onValueChanged:', 1)[1].split('; onStatusChanged:', 1)[0].rsplit('}', 1)[0].strip()
    script = '''let now=10000; Date.now=()=>now;
const Sensors={Sensor:{Ready:2}},root={cpu:88,cpuTemp:90,ram:92,ramUsedBytes:100,ramTotalBytes:200,systemSensorStates:{},telemetryNowMs:now,clamp:v=>Math.max(0,Math.min(100,v||0))};
'''
    for name in ('recordSystemSensor', 'systemSensorValue'):
        if f'    function {name}(' in QML.read_text():
            script += qml_function(name) + f'\nroot.{name}={name};\n'
    script += f'function receive(value,status=2,readyForSamples=true){{ {handler} }};\n'
    script += f'function read(){{return root.systemSensorValue ? root.systemSensorValue({json.dumps(field)}) : root[{json.dumps(field)}];}}\n'
    return script


@pytest.mark.parametrize('sensor_id,field', [('cpu/all/usage','cpu'), ('cpu/all/averageTemperature','cpuTemp'), ('memory/physical/usedPercent','ram'), ('memory/physical/used','ramUsedBytes'), ('memory/physical/total','ramTotalBytes')])
@pytest.mark.parametrize('bad', [None, '', 'not available', '42oops'])
def test_invalid_system_sensor_sample_is_unknown(sensor_id, field, bad):
    script = system_sensor_script(sensor_id, field) + f'receive({json.dumps(bad)});console.log(JSON.stringify(read()));'
    assert node(script) is None


@pytest.mark.parametrize('sensor_id,field', [('cpu/all/usage','cpu'), ('cpu/all/averageTemperature','cpuTemp'), ('memory/physical/usedPercent','ram'), ('memory/physical/used','ramUsedBytes')])
def test_zero_sensor_samples_are_valid_and_repeated_identical_samples_renew_freshness(sensor_id, field):
    script = system_sensor_script(sensor_id, field) + '''receive(0);const values=[read()];
now=15000;root.telemetryNowMs=now;receive(0);now=19000;root.telemetryNowMs=now;values.push(read());
now=21000;root.telemetryNowMs=now;values.push(read());console.log(JSON.stringify(values));'''
    assert node(script) == [0, 0, None]


@pytest.mark.parametrize('status', [0, 1, 3, 4])
def test_nonready_sensor_cannot_masquerade_as_valid_zero(status):
    script = system_sensor_script('cpu/all/usage','cpu') + f'receive(0,{status});console.log(JSON.stringify(read()));'
    assert node(script) is None


def test_ram_capacity_formatter_preserves_valid_zero_used_memory():
    script = qml_function('fmtMemoryPair') + '\n' + qml_function('fmtMemoryPairExact')
    assert node(script + '\nconsole.log(JSON.stringify([fmtMemoryPair(0,1073741824),fmtMemoryPairExact(0,1073741824)]));') == ['0 / 1 GiB', '0.0 / 1.0 GiB']


def test_multiple_ticks_in_same_wall_clock_second_replace_instead_of_stretching_time():
    assert history_ticks([10050, 10950, 11000])[0] == [20, 20]
