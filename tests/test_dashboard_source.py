#!/usr/bin/env python3
"""Static regression checks for the Skybox Plasma live monitor."""
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "contents/ui/main.qml"


def source():
    return SOURCE.read_text()


def test_operational_status_explains_normal_state_and_thresholds():
    text = source()
    assert 'root.currentHealth.level === 0 ? " · 0 ALERTS"' in text
    assert '"0 ALERTS · VRAM "' in text
    assert '"SYSTEM ATTENTION"' in text
    assert '"SYSTEM ALERT"' in text
    assert 'root.vramPercent() >= 95' in text


def test_header_and_footer_disclose_freshness():
    text = source()
    assert 'property string lastRefresh' in text
    assert '"LIVE SYSTEM · " + root.dataStatus' in text
    assert '"DEBIAN 13 · PLASMA 6 · REFRESH " + root.lastRefresh' in text
    assert 'function refreshClock()' in text


def test_gpu_card_prioritizes_vram_and_active_workload_context():
    text = source()
    assert 'function shortProcessName(name)' in text
    assert 'function compactProcessValue(metricLabel, value)' in text
    assert 'function gpuCauseSummary()' in text
    assert 'root.gpuCauseSummary(), root.critical' in text
    assert 'detail:"VRAM " + Math.round(root.vramPercent())' in text
    assert 'topGpuSource' in text
    assert '--query-compute-apps=process_name,used_memory' in text
    assert 'gpuProcessCount' in text
    assert 'gpuTopProcess' in text


def test_cpu_card_shows_top_two_processes_in_its_detail_area():
    text = source()
    assert 'property var topCpuProcesses' in text
    assert 'id: topCpuSource' in text
    assert 'ps -eo pcpu=,comm= --sort=-pcpu | head -2' in text
    assert 'property string heading: "TOP PROCESSES"' in text
    assert 'property var processes: metricLabel === "CPU" ? root.topCpuProcesses' in text
    assert 'processes.length < 2' in text
    assert 'onTriggered: topCpuSource.connectSource(topCpuSource.command)' in text


def test_ram_card_shows_top_two_processes_in_its_detail_area():
    text = source()
    assert 'property var topRamProcesses' in text
    assert 'id: topRamSource' in text
    assert 'ps -eo rss=,comm= --sort=-rss | head -2' in text
    assert 'metricLabel === "RAM" ? root.topRamProcesses' in text
    assert 'processes.length < 2' in text
    assert 'onTriggered: topRamSource.connectSource(topRamSource.command)' in text


def test_gpu_card_shows_top_two_processes_but_counts_all_workloads():
    """The card is capped at two rows while its workload count remains exact."""
    text = source()
    assert 'property var topGpuProcesses: []' in text
    assert 'id: topGpuSource' in text
    assert '--query-compute-apps=process_name,used_memory' in text
    gpu_source = text[text.index('id: topGpuSource'):text.index('id: netDetectSource')]
    assert '| head -2' not in gpu_source
    assert 'validProcessCount++' in text
    assert 'if (processes.length < 2)' in text
    assert 'root.gpuProcessCount = validProcessCount' in text
    assert 'root.fmtVram(candidates[j].mib)' in text
    assert 'root.topGpuProcesses = processes' in text
    assert 'property string heading: "TOP PROCESSES"' in text
    assert 'metricLabel === "GPU"' in text
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
    assert 'root.vramReleaseStatus' in text
    assert 'id: releaseModelsSource' in text
    assert 'ollama stop' in text
    assert 'CONFIRM UNLOAD' in text
    assert 'UNLOAD OLLAMA' in text
    assert 'docker stop aeon-vllm honcho-api-1 honcho-deriver-1' not in text
    assert 'api/ps' in text
    assert 'arbitrary GPU processes are never terminated' in text
    assert 'root.vramReleaseInProgress = true' in text
    assert 'id: releaseRefreshTimer' in text


def test_metric_cards_reserve_a_real_kpi_column_without_overlap():
    text = source()
    assert 'id: metricKpi' in text
    assert 'width: Math.max(116, parent.width * 0.34)' in text
    assert 'id: metricDivider' in text
    assert 'anchors.left: metricDivider.right' in text
    assert 'anchors.right: parent.right' in text
    assert 'property string heading: "TOP PROCESSES"' in text
    assert 'function fmtCompactCapacity' in text
    assert 'detail:root.fmtCompactCapacity(root.ramUsedBytes)' in text
    assert 'root.fmtCompactVram(root.gpuVramUsedMiB)' in text


def test_compute_chart_has_dedicated_graph_and_timeline_space():
    text = source()
    assert 'Layout.preferredHeight: 218' in text
    assert 'anchors.bottom: computeTimeline.top' in text
    assert 'anchors.bottomMargin: 10' in text
    assert 'height: 20' in text


def test_network_header_separates_live_values_from_chart_metadata():
    text = source()
    assert 'id: networkLiveValues' in text
    assert 'id: networkMetadata' in text
    block = text[text.index('id: networkDataBlock'):text.index('// Download sub-chart')]
    assert 'anchors.top: networkLiveValues.bottom' not in block
    assert 'text: "SCALE 0–" + root.fmtRate(root.netPeak())' in text


def test_footer_uses_explicit_disk_and_uptime_labels():
    text = source()
    assert 'text: "DISK USED"' in text
    assert 'text: "UPTIME"' in text
    assert 'text: "SYSTEM"' not in text


def test_unload_control_is_visually_and_accessibly_an_action():
    text = source()
    assert 'Layout.preferredHeight: 44' in text
    assert 'Accessible.role: Accessible.Button' in text
    assert 'Accessible.name: "Unload Ollama models from GPU memory"' in text
    assert 'Keys.onSpacePressed:' in text
    assert 'Keys.onReturnPressed:' in text


def test_health_state_always_exposes_a_safe_cause_string():
    text = source()
    assert 'property var currentHealth: root.healthState()' in text
    assert 'visible: root.currentHealth.cause.length > 0' in text
    assert 'function makeHealthState(level, label, detail, cause, color)' in text
    assert 'cause: cause || ""' in text
    assert 'root.healthState().cause.length' not in text


def test_freshness_tracks_each_data_domain_not_the_chart_timer():
    text = source()
    timer_block = text[text.index('interval: 1000'):text.index('    // Current top', text.index('interval: 1000'))]
    assert 'root.lastRefresh = root.refreshClock()' not in timer_block
    assert 'function markDataFresh(domain)' in text
    for domain in ('cpu', 'gpu', 'memory', 'network', 'disk', 'system'):
        assert f'root.markDataFresh("{domain}")' in text
    assert 'MonitorLogic.staleDomains' in text
    assert 'property string dataStatus: "WAITING FOR DATA"' in text
    assert 'STALE · ' in text


def test_ollama_unload_distinguishes_dependencies_connection_and_partial_failure():
    text = source()
    assert 'for dep in curl jq ollama' in text
    assert 'command -v \\\"$dep\\\"' in text
    assert 'OLLAMA UNREACHABLE' in text
    assert 'UNLOAD PARTIAL' in text
    assert 'MISSING DEPENDENCY' in text
    assert 'data["exit code"]' in text


def test_chart_series_are_distinguishable_without_color_alone():
    text = source()
    assert 'ctx.setLineDash(dashed ? [8, 5] : [])' in text
    assert 'ctx.setLineDash([])' in text


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
