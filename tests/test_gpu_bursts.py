"""GPU burst sampling: real JS helpers and QML handler integration."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1]
LOGIC = ROOT / "contents/code/monitor_logic.js"
QML = ROOT / "contents/ui/main.qml"


def node(script):
    result = subprocess.run(
        ["node", "-e", f"const m=require({json.dumps(str(LOGIC))});\n" + script],
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_gpu_peak_retains_burst_without_changing_live_load_then_expires():
    actual = node("""
let state=m.nextGpuLoadState(null,{uuid:'GPU-a',usage:100},1000);
state=m.nextGpuLoadState(state,{uuid:'GPU-a',usage:0},1500);
const afterBurst=[state.usage,m.gpuLoadPeak(state,1500)];
state=m.nextGpuLoadState(state,{uuid:'GPU-a',usage:0},31001);
console.log(JSON.stringify([afterBurst,state.usage,m.gpuLoadPeak(state,31001)]));
""")
    assert actual == [[0, 100], 0, 0]


def test_gpu_peak_is_unknown_on_failure_staleness_or_device_change():
    actual = node("""
const full=m.nextGpuLoadState(null,{uuid:'GPU-a',usage:100},1000);
const failed=m.nextGpuLoadState(full,null,1500);
const replaced=m.nextGpuLoadState(full,{uuid:'GPU-b',usage:2},1500);
console.log(JSON.stringify([
 m.gpuLoadPeak(full,2501),m.gpuLoadPeak(failed,1500),
 m.gpuLoadPeak(replaced,1500),
 m.nextGpuLoadState(full,{uuid:'GPU-a',usage:NaN},1500).available,
 m.nextGpuLoadState(full,{uuid:'GPU-a',usage:null},1500).available
]));
""")
    assert actual == [None, None, 2, False, False]


def test_gpu_peak_survives_failed_polls_then_expires_or_resets_on_replacement():
    actual = node("""
let state=m.nextGpuLoadState(null,{uuid:'GPU-a',usage:100},1000);
state=m.nextGpuLoadState(state,null,1500);
const duringFailure=m.gpuLoadPeak(state,1500);
state=m.nextGpuLoadState(state,null,1750);
const recovered=m.nextGpuLoadState(state,{uuid:'GPU-a',usage:0},2000);
const replaced=m.nextGpuLoadState(state,{uuid:'GPU-b',usage:2},2000);
const expired=m.nextGpuLoadState(state,null,31001);
const afterExpiry=m.nextGpuLoadState(expired,{uuid:'GPU-a',usage:0},31500);
console.log(JSON.stringify([
 duringFailure,recovered.usage,m.gpuLoadPeak(recovered,2000),
 m.gpuLoadPeak(replaced,2000),m.gpuLoadPeak(expired,31001),
 m.gpuLoadPeak(afterExpiry,31500)
]));
""")
    assert actual == [None, 0, 100, 2, None, 0]


def test_fast_gpu_parser_uses_real_indices_and_rejects_unavailable_readings():
    actual = node("""
console.log(JSON.stringify(m.parseGpuLoads('2, GPU-c, 94\\n0, GPU-a, 0\\n1, GPU-b, [N/A]\\n3, GPU-d, 101\\n4, GPU-e, NaN\\n5, GPU-f, \\n6, , 10')));
""")
    assert actual == {"2": {"uuid": "GPU-c", "usage": 94}, "0": {"uuid": "GPU-a", "usage": 0}}


def test_role_label_only_applies_to_hindsight_api_not_hindsight_llm():
    actual = node("""
console.log(JSON.stringify(['hindsight-api','hindsight-ollama','python3','qwen38-llama-server'].map(m.gpuProcessRole)));
""")
    assert actual == ["EMBED / RERANK", "", "", ""]


def qml_function(name):
    source = QML.read_text().split(f"    function {name}(", 1)[1]
    return f"function {name}(" + source.split("\n    }", 1)[0] + "\n}"


def test_qml_fast_handler_maps_each_card_and_clears_all_on_failure():
    qml = QML.read_text()
    block = qml.split("id: gpuLoadSource", 1)[1].split("id: topCpuSource", 1)[0]
    body = block.split("onNewData: function(source, data) {", 1)[1].rsplit("\n        }", 1)[0]
    actual = node("""
const MonitorLogic=m;
var root={gpuLoadStates:{}};
""" + qml_function("applyGpuLoads") + """
root.applyGpuLoads=applyGpuLoads;
var buffer='',source='';function disconnectSource(){};
function handle(data){
""" + body + """
}
handle({'stdout':'2, GPU-c, 94\\n0, GPU-a, 0','exit code':0});
const valid=[root.gpuLoadStates['0'].usage,root.gpuLoadStates['1'].available,root.gpuLoadStates['2'].usage];
handle({'stdout':'','exit code':1});
console.log(JSON.stringify([valid,[0,1,2].map(i=>root.gpuLoadStates[String(i)].available)]));
""")
    assert actual == [[0, False, 94], [False, False, False]]


def test_qml_role_binding_preserves_exact_vram_value():
    actual = node("""
const MonitorLogic=m;var root={fmtVram:v=>String(v)+' MiB'};
""" + qml_function("gpuProcessRows") + """
console.log(JSON.stringify(gpuProcessRows({processes:[{name:'hindsight-api',used_mib:4458}]})));
""")
    assert actual == [{"name": "hindsight-api", "gpu": "4458 MiB", "gpuExact": "4458 MiB", "role": "EMBED / RERANK"}]


def test_qml_gpu_load_is_fast_but_process_poll_stays_two_seconds():
    import re
    qml = QML.read_text()
    assert re.search(r"interval: 500\s+running: true\s+repeat: true\s+onTriggered: gpuLoadSource.connectSource", qml)
    assert re.search(r"interval: 2000\s+running: true\s+repeat: true\s+onTriggered: gpuTelemetrySource.connectSource", qml)
    assert "--query-gpu=index,uuid,utilization.gpu" in qml
    assert '"PEAK 30s · "' in qml
    assert "text: parent.process.role ||" in qml
    assert "Layout.preferredHeight: 342" in qml
    assert "root.gpu0Usage =" not in qml  # Slow full poll must not overwrite fast load.


def test_card_height_fits_four_processes_peak_and_hindsight_role():
    import re
    block = QML.read_text().split("// --- Three-GPU row", 1)[1]
    height_match = re.search(r"Layout.preferredHeight: (\d+)", block)
    spacing_match = re.search(r"cardSpacing: (\d+)", block)
    assert height_match and spacing_match
    height = int(height_match.group(1))
    spacing = int(spacing_match.group(1))
    available = (height - spacing) / 2 - 2 * 12  # Card content margins.
    required = 18 + 17 + 4 * 18 + 14 + 5 * 3  # Heading, peak, rows, role, gaps.
    assert available >= required
