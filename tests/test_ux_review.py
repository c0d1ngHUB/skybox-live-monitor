"""Regression checks for the portrait dashboard's information hierarchy."""
from pathlib import Path
import json
import subprocess

import pytest

QML = (Path(__file__).resolve().parents[1] / "contents/ui/main.qml")
LOGIC = QML.parents[1] / "code/monitor_logic.js"


def test_process_names_get_space_without_losing_exact_values():
    source = QML.read_text()
    assert 'readonly property int kpiColumn: Math.max(60, Math.round(parent.width * 0.35))' in source
    assert 'mainText: parent.process.name' in source
    assert 'mainText: parent.fullValue' in source
    assert 'text: processValue.parent.displayValue' in source
    assert 'processDetails.metricKind === "gpu" ? "VRAM" : processDetails.metricKind.toUpperCase()' in source
    assert 'text: root.shortProcessName(parent.mainText)' in source
    assert 'mainText: cardData.powerText || ""' in source
    assert 'text: (cardData.powerText || "").replace("POWER ", "")' in source


def test_compact_process_values_preserve_full_units_in_tooltips():
    source = QML.read_text()
    helper = source[source.index('    function compactProcessValue('):source.index('    function metricBorderColor(')]
    probe = helper + "\nconsole.log(JSON.stringify([" + ",".join([
        'compactProcessValue("RAM", "919.9 MiB")',
        'compactProcessValue("RAM", "1536 MiB")',
        'compactProcessValue("VRAM", "3.7 GiB")',
        'compactProcessValue("VRAM", "250 MiB")',
        'compactProcessValue("CPU %", "39.3%")',
    ]) + "]))"
    result = subprocess.run(["node", "-e", probe], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ["920M", "1.5G", "3.7G", "250M", "39.3%"]
    assert 'mainText: parent.fullValue' in source
    assert 'mainText: parent.process.name' in source


def test_fast_telemetry_age_tracks_the_oldest_successful_source():
    script = LOGIC.read_text() + "\nconsole.log(JSON.stringify([" + ",".join([
        "telemetryAgeSeconds(100000, 0, 95000, 99000)",
        "telemetryAgeSeconds(100000, 99000, 80000, 95000)",
        "telemetryAgeSeconds(100000, 101000, 99000, 99500)",
    ]) + "]))"
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [None, 20, 1]
    source = QML.read_text()
    assert 'text: root.telemetryAgeLabel()' in source
    assert 'root.lastCpuTelemetryMs = now' in source
    assert 'root.lastGpuTelemetryMs = Date.now()' in source
    assert 'root.lastNetworkTelemetryMs = now' in source


def test_gpu_process_tooltip_retains_unrounded_mib_reading():
    source = QML.read_text()
    formatter = source[source.index('    function fmtVram('):source.index('    function fmtCompactVram(')]
    rows = source[source.index('    function gpuProcessRows('):source.index('    // The card title column')]
    script = 'const MonitorLogic=require(' + json.dumps(str(LOGIC)) + ');\n' + formatter + rows + '''
var root = {fmtVram: fmtVram};
var processes = gpuProcessRows({processes: [
    {name: "a", used_mib: 3801}, {name: "b", used_mib: 3810}
]});
console.log(JSON.stringify(processes));
'''
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    processes = json.loads(result.stdout)
    assert [row["gpu"] for row in processes] == ["3.7 GiB", "3.7 GiB"]
    assert [row["gpuExact"] for row in processes] == ["3801 MiB", "3810 MiB"]
    assert 'processDetails.metricKind === "gpu" ? process.gpuExact' in source
    assert 'processDetails.metricKind === "gpu" ? process.gpu : (processDetails.metricKind === "ram" ? process.ram : fullValue)' in source
    assert 'mainText: parent.fullValue' in source


def test_each_gpu_vram_bar_tooltip_receives_live_used_and_total_readings():
    source = QML.read_text()
    cards = source.split('// --- Three-GPU row', 1)[1].split('x: index', 1)[0]
    for index in range(3):
        row = next(line for line in cards.splitlines() if '{kind:"gpu", label:"GPU ' + str(index) in line)
        assert f'vramUsedMiB:root.gpu{index}VramUsedMiB' in row
        assert f'vramTotalMiB:root.gpu{index}VramTotalMiB' in row
    assert 'root.fmtVram(cardData.vramUsedMiB || 0) + " / " + root.fmtVram(cardData.vramTotalMiB || 0)' in source


