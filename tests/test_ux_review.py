"""Regression checks for the portrait dashboard's information hierarchy."""
from pathlib import Path
import json
import subprocess

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
    assert 'mainText: modelData.powerText || ""' in source
    assert 'text: (modelData.powerText || "").replace("POWER ", "")' in source


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


def test_metric_denominators_and_short_labels_have_explanations():
    source = QML.read_text()
    assert 'Anteil eines CPU-Threads' in source
    assert 'CPU-Gesamtwert: Durchschnitt' in source
    assert 'text: "SKALEN UNABHÄNGIG"' in source
    assert 'mainText: root.diskReserveDetail()' in source
    assert 'NEU OHNE TAGS ' in source


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
