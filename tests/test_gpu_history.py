"""Execute the real history timer: fast GPU load owns its availability/age."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "contents/ui/main.qml"
LOGIC = ROOT / "contents/code/monitor_logic.js"


def qml_function(name):
    source = QML.read_text().split(f"    function {name}(", 1)[1]
    return f"function {name}(" + source.split("\n    }", 1)[0] + "\n}"


def history_tick(states, slow_available=True, slow_stamp=19900):
    source = QML.read_text()
    body = "root.telemetryNowMs = Date.now()" + source.split(
        "root.telemetryNowMs = Date.now()", 1
    )[1].split("computeGraph.requestPaint()", 1)[0]
    script = f"const MonitorLogic=require({json.dumps(str(LOGIC))});\n" + """
Date.now = () => 20000;
const root = {
    cpu: 0, ram: 0, cpuHistory: [], ramHistory: [],
    gpu0History: [], gpu1History: [], gpu2History: [],
    downHistory: [], upHistory: [], down: 0, up: 0,
    networkAvailable: false, lastNetworkTelemetryMs: 0,
    lastServiceTelemetryMs: 19900, historySeconds: 120,
    telemetryNowMs: 20000
};
""" + f"root.gpuLoadStates={json.dumps(states)};\n" + f"""
root.lastGpuTelemetryMs = {slow_stamp};
for (let i = 0; i < 3; i++) {{
    root['gpu' + i + 'Available'] = {json.dumps(slow_available)};
    Object.defineProperty(root, 'gpu' + i + 'Usage', {{
        get: () => root.gpuLoadStates[String(i)] ? root.gpuLoadStates[String(i)].usage : 0
    }});
}}
""" + qml_function("push") + "\n" + qml_function("gpuLoadAvailable") + """
root.push = push;
root.gpuLoadAvailable = gpuLoadAvailable;
""" + body + """
console.log(JSON.stringify([root.gpu0History, root.gpu1History, root.gpu2History]));
"""
    result = subprocess.run(
        ["node", "-e", script], text=True, capture_output=True, check=True
    )
    return json.loads(result.stdout)


def load_state(usage=93, stamp=19900, available=True):
    return {
        "available": available,
        "uuid": "GPU-test",
        "usage": usage,
        "stampMs": stamp,
        "samples": [{"usage": usage, "stampMs": stamp}],
    }


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("slow_available,slow_stamp", [
    (False, 19900),  # Failed slow snapshot must not erase a fresh fast sample.
    (True, 4000),   # Nor may a slow snapshot older than its 15-second budget.
    (False, 0),     # Fast load can arrive before the first slow snapshot.
    (True, 19900),
])
def test_fresh_gpu_history_is_independent_of_slow_snapshot(index, slow_available, slow_stamp):
    actual = history_tick({str(index): load_state()}, slow_available, slow_stamp)
    expected: list[list[int | None]] = [[None], [None], [None]]
    expected[index] = [93]
    assert actual == expected


@pytest.mark.parametrize("state,expected", [
    (load_state(usage=0), 0),
    (load_state(stamp=15000), 93),  # Exactly 5000 ms old is still fresh.
    (load_state(stamp=14999), None),
    (load_state(stamp=0), None),
    (load_state(available=False), None),
    (None, None),
])
@pytest.mark.parametrize("index", [0, 1, 2])
def test_history_keeps_real_zero_but_not_failed_stale_or_missing_fast_load(index, state, expected):
    actual = history_tick({str(index): state} if state is not None else {})
    want = [[None], [None], [None]]
    want[index] = [expected]
    assert actual == want


def test_history_preserves_each_fast_devices_own_age_and_value():
    actual = history_tick({
        "0": load_state(usage=93),
        "1": load_state(usage=0),
        "2": load_state(usage=34, stamp=14999),
    }, slow_available=False, slow_stamp=0)
    assert actual == [[93], [0], [None]]


@pytest.mark.parametrize("age_ms", [1501, 1509, 2000, 3500, 5000])
@pytest.mark.parametrize("index", [0, 1, 2])
def test_history_tolerates_delayed_plasma_timer_without_inventing_a_gap(index, age_ms):
    # Live reproduction: the history timer ran before the next process result,
    # 1509 ms after a successful read. The GPU source had never failed.
    actual = history_tick({str(index): load_state(usage=37, stamp=20000 - age_ms)})
    expected = [[None], [None], [None]]
    expected[index] = [37]
    assert actual == expected
