#!/usr/bin/env python3
"""Static regression checks for the Skybox Plasma live monitor."""
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "contents/ui/main.qml"


def source():
    return SOURCE.read_text()


def test_operational_status_explains_normal_state_and_thresholds():
    text = source()
    assert 'root.healthState().level === 0 ? " · 0 ALERTS"' in text
    assert '"0 ALERTS · VRAM "' in text
    assert '"SYSTEM ATTENTION"' in text
    assert '"SYSTEM ALERT"' in text
    assert 'root.vramPercent() >= 95' in text


def test_header_and_footer_disclose_freshness():
    text = source()
    assert 'property string lastRefresh' in text
    assert '"LIVE SYSTEM · UPDATED " + root.lastRefresh' in text
    assert '"DEBIAN 13 · PLASMA 6 · REFRESH " + root.lastRefresh' in text
    assert 'function refreshClock()' in text


def test_gpu_card_prioritizes_vram_and_active_workload_context():
    text = source()
    assert 'function shortProcessName(name)' in text
    assert 'function compactProcessValue(metricLabel, value)' in text
    assert 'function gpuCauseSummary()' in text
    assert 'cause: root.gpuCauseSummary()' in text
    assert 'detail:"VRAM " + Math.round(root.vramPercent())' in text
    assert 'topGpuSource' in text
    assert '--query-compute-apps=process_name,used_memory' in text
    assert 'gpuProcessCount' in text
    assert 'gpuTopProcess' in text


def test_cpu_card_shows_top_five_processes_in_its_detail_area():
    text = source()
    assert 'property var topCpuProcesses' in text
    assert 'id: topCpuSource' in text
    assert 'ps -eo pcpu=,comm= --sort=-pcpu | head -2' in text
    assert 'property string heading: "TOP 2 " + metricLabel + " PROCESSES"' in text
    assert 'property var processes: metricLabel === "CPU" ? root.topCpuProcesses' in text
    assert 'processes.length < 2' in text
    assert 'onTriggered: topCpuSource.connectSource(topCpuSource.command)' in text


def test_ram_card_shows_top_five_processes_in_its_detail_area():
    text = source()
    assert 'property var topRamProcesses' in text
    assert 'id: topRamSource' in text
    assert 'ps -eo rss=,comm= --sort=-rss | head -2' in text
    assert 'metricLabel === "RAM" ? root.topRamProcesses' in text
    assert 'processes.length < 2' in text
    assert 'onTriggered: topRamSource.connectSource(topRamSource.command)' in text


def test_gpu_card_shows_top_five_processes_in_its_detail_area():
    """GPU should match CPU/RAM: two consumers and their allocated VRAM in MiB."""
    text = source()
    assert 'property var topGpuProcesses: []' in text
    assert 'id: topGpuSource' in text
    assert '--query-compute-apps=process_name,used_memory' in text
    assert 'root.fmtVram(mib)' in text
    assert 'root.topGpuProcesses = processes' in text
    assert 'property string heading: "TOP 2 " + metricLabel + " PROCESSES"' in text
    assert 'metricLabel === "GPU"' in text  # Compact GPU VRAM values explicitly.
    assert 'onTriggered: topGpuSource.connectSource(topGpuSource.command)' in text


def test_charts_are_five_minute_and_visually_readable():
    text = source()
    assert 'property int historySeconds: 300' in text
    assert 'LAST 5 MIN · FIXED SCALE · 0–100%' in text
    assert 'LAST 5 MIN · AUTO SCALE' in text
    assert 'ctx.fillStyle = fillColor' in text
    assert 'ctx.fillStyle = "rgba(150,245,246,0.15)"' in text
    assert 'ctx.fillStyle = "rgba(219,145,255,0.15)"' in text
    assert 'id: computeTimeline' in text
    assert 'id: networkTimeline' in text


def test_compact_cards_preserve_legible_operational_detail():
    text = source()
    assert 'Layout.preferredHeight: 126' in text
    assert 'Layout.preferredHeight: 94' in text
    assert 'font.pixelSize: 13' in text
    assert 'elide: Text.ElideRight' in text
    assert '"FREE " + root.fmtDisk(root.diskFreeBytes()) + " · USED "' in text


def test_header_exposes_a_guarded_llm_vram_release_button():
    text = source()
    assert 'id: releaseVramButton' in text
    assert 'text: root.vramReleaseStatus' in text
    assert 'id: releaseModelsSource' in text
    assert 'ollama stop' in text
    assert 'CONFIRM UNLOAD' in text
    assert 'UNLOAD OLLAMA' in text
    assert 'docker stop aeon-vllm honcho-api-1 honcho-deriver-1' not in text
    assert 'api/ps' in text
    assert 'arbitrary GPU processes are never terminated' in text
    assert 'root.vramReleaseInProgress = true' in text
    assert 'id: releaseRefreshTimer' in text


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as error:
            failures.append(f"FAIL {test.__name__}: {error}")
    print("\n".join(failures))
    raise SystemExit(bool(failures))
