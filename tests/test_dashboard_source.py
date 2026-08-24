#!/usr/bin/env python3
"""Static regression checks for the Skybox Plasma live monitor."""
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "contents/ui/main.qml"


def source():
    return SOURCE.read_text()



def test_freshness_is_tracked_per_metric_and_compact_status_is_visible():
    text = source()
    assert 'property string lastRefresh' in text
    assert 'property var metricUpdateMs' in text
    assert 'id: telemetryStatus' in text
    assert 'text: root.dataStatus' in text
    assert 'visible: root.dataStatus.length > 0' in text
    assert '"DEBIAN 13 · PLASMA 6 · REFRESH " + root.lastRefresh' not in text
    assert 'function refreshClock()' in text


def test_gpu_card_prioritizes_vram_and_active_workload_context():
    text = source()
    assert 'function shortProcessName(name)' in text
    assert 'function compactProcessValue(metricLabel, value)' in text
    assert 'detail:"VRAM " + Math.round(root.vramPercent())' in text
    assert 'topGpuSource' in text
    assert '--query-compute-apps=process_name,used_memory' in text
    assert 'gpuProcessCount' in text
    assert 'gpuTopProcess' in text


def test_cpu_card_shows_top_two_processes_in_its_detail_area():
    text = source()
    assert 'property var topCpuProcesses' in text
    assert 'id: topCpuSource' in text
    assert 'cpu_process_snapshot.py' in text
    assert 'MonitorLogic.cpuProcessRates' in text
    assert 'property string heading: "TOP PROCESSES"' in text
    assert 'property var processes: metricLabel === "CPU" ? root.topCpuProcesses' in text
    assert 'MonitorLogic.cpuProcessRates(root.previousCpuSamples, samples, elapsedMs, 2)' in text
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


def test_charts_are_two_minute_and_visually_readable():
    text = source()
    assert 'property int historySeconds: 120' in text
    assert text.count('text: "−2 MIN"') == 2
    assert text.count('text: "−1 MIN"') == 2
    assert text.count('var midTick = plotLeft + chartWidth / 2') == 3
    assert 'var tick1 =' not in text
    assert '−5 MIN' not in text
    assert '2.5 MIN' not in text
    assert 'LAST 5 MIN · FIXED SCALE · 0–100%' not in text
    assert 'LAST 5 MIN · AUTO SCALE' not in text
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






def test_compute_chart_has_dedicated_graph_and_timeline_space():
    text = source()
    assert 'Layout.preferredHeight: 218' in text
    assert 'anchors.bottom: computeTimeline.top' in text
    assert 'anchors.bottomMargin: 10' in text
    assert 'height: 20' in text


def test_network_uses_dynamic_scale_ceilings_without_metadata_labels():
    text = source()
    assert 'id: networkLiveValues' in text
    assert 'id: networkMetadata' not in text
    assert 'MonitorLogic.networkScaleMbit(root.downHistory, 50, 600)' in text
    assert 'MonitorLogic.networkScaleMbit(root.upHistory, 5, 50)' in text
    assert 'Math.min(1, d[j] / root.downloadScaleBytesPerSecond)' in text
    assert 'Math.min(1, d[j] / root.uploadScaleBytesPerSecond)' in text
    assert 'Layout.preferredHeight: 288' in text


def test_network_axes_follow_dynamic_ceilings_in_50_and_5_mbit_steps():
    text = source()
    assert 'text: "↓ MBIT/S"' in text
    assert 'text: "↑ MBIT/S"' in text
    assert 'Math.round(maxMbit / 50)' in text  # download grid: 50 Mbit steps
    assert 'maxMbit - i * 50' in text
    assert 'Math.round(maxMbit / 5)' in text   # upload grid: 5 Mbit steps
    assert 'maxMbit - i * 5' in text
    assert text.count('var plotLeft = 44') >= 2
    assert text.count('var chartWidth = width - plotLeft') >= 2
    # Axis labels must not be clipped by Canvas left edge (gutter fix)
    assert 'ctx.fillText(String(maxMbit - i * 50), 40 - 6' in text
    assert 'ctx.fillText(String(maxMbit - i * 5), 40 - 6' in text


def test_footer_uses_explicit_disk_and_uptime_labels():
    text = source()
    assert 'text: "DISK USED"' in text
    assert 'text: "UPTIME"' in text
    assert 'text: "SYSTEM"' not in text


def test_uptime_card_shows_load_proc_max_think_and_openai_keys():
    text = source()
    assert 'property int processCount: 0' in text
    assert 'property real hermesMaxThinkSeconds: 0' in text
    assert 'property int openAiActiveKeys: -1' in text
    assert 'property int openAiTotalKeys: -1' in text
    assert 'function fmtDuration(seconds)' in text
    assert 'id: processCountSource' in text
    assert 'ps -e --no-headers | wc -l' in text
    assert 'id: hermesThinkSource' in text
    assert 'hermes_max_think.py' in text
    assert 'id: openAiKeysSource' in text
    assert 'hermes_openai_keys.py' in text
    assert 'id: systemMetaRow' in text
    assert 'text: "LOAD " + root.loadAverage.toFixed(2); color: root.muted' in text
    assert 'text: "PROC " + root.processCount; color: root.muted' in text
    assert 'text: "MAX THINK: " + root.fmtDuration(root.hermesMaxThinkSeconds); color: root.muted' in text
    assert 'text: "KEYS " + root.openAiActiveKeys + "/' in text
    assert 'interval: 900000\n        running: true\n        repeat: true\n        onTriggered: openAiKeysSource.connectSource(openAiKeysSource.command)' in text
    assert 'text: "MAX THINK 24H "' not in text


def test_odysseus_toggle_is_wired_to_native_app_process():
    text = source()
    # UI: toggle next to the clock header
    assert 'id: odysseusToggle' in text
    assert 'anchors.right: parent.right' in text
    assert 'ODYSSEUS' in text
    # State properties
    assert 'property bool odysseusRunning: false' in text
    assert 'property bool odysseusTogglePending: false' in text
    # Status poller via pgrep
    assert 'id: odysseusStatusSource' in text
    assert "pgrep -f '[o]dysseus/venv/bin/python.*app.py'" in text
    # Toggle source: start with venv python, stop via pkill
    assert 'id: odysseusToggleSource' in text
    assert '/venv/bin/python' in text
    assert "pkill -f '[o]dysseus/venv/bin/python.*app.py'" in text
    # Status polled periodically and once at startup
    assert 'onTriggered: odysseusStatusSource.connectSource(odysseusStatusSource.command)' in text
    assert text.count('odysseusStatusSource.connectSource(odysseusStatusSource.command)') >= 2


def test_vram_fill_bar_replaces_vram_text_and_uses_orange_track():
    text = source()
    # Orange colour token exists for the VRAM bar
    assert 'property color orange: "#FF9F43"' in text
    # GPU card carries a vramFill field instead of the long "used / total" text
    assert 'vramFill: root.vramPercent()' in text
    assert '"VRAM " + Math.round(root.vramPercent()) + "%"' in text
    # Bar renders when vramFill is present, orange by default, red at ≥85%
    assert 'modelData.vramFill !== undefined' in text
    assert 'Math.min(1, (modelData.vramFill || 0) / 100)' in text
    assert '(modelData.vramFill || 0) >= 85 ? root.critical : root.orange' in text
    # Old verbose VRAM text with used / total is gone
    assert 'fmtCompactVram(root.gpuVramUsedMiB) + " / " + root.fmtCompactVram(root.gpuVramTotalMiB)' not in text


def test_dashboard_uses_the_full_available_height_without_clipping_content():
    text = source()
    assert 'anchors.fill: parent' in text
    assert 'anchors.margins: 8' in text
    assert 'height: Math.min(parent.height - 24, content.implicitHeight + 68)' not in text
    assert 'anchors.fill: frame' in text
    assert 'anchors.topMargin: 20' in text
    assert 'anchors.bottomMargin: 20' in text
    assert 'height: implicitHeight' not in text
    assert 'Layout.fillHeight: true' in text
    assert 'Layout.minimumHeight: 180' in text
    assert 'Layout.minimumHeight: 230' in text


def test_header_shows_a_live_clock_centered_at_skybox_font_size():
    text = source()
    assert 'property string currentTime: refreshClock()' in text
    assert 'id: headerClock' in text
    assert 'text: root.currentTime' in text
    assert 'anchors.horizontalCenter: parent.horizontalCenter' in text
    assert 'font.pixelSize: 28' in text
    assert 'root.currentTime = root.refreshClock()' in text


def test_header_clock_uses_hours_and_minutes_without_seconds():
    text = source()
    clock = text[text.index('function refreshClock()'):text.index('function markMetricFresh', text.index('function refreshClock()'))]
    assert 'property string lastRefresh: "--:--"' in text
    assert 'now.getHours()' in clock
    assert 'now.getMinutes()' in clock
    assert 'now.getSeconds()' not in clock


def test_removed_unload_control_has_no_visual_or_accessible_action():
    text = source()
    assert 'Accessible.name: "Unload Ollama models from GPU memory"' not in text
    assert 'Keys.onSpacePressed:' not in text
    assert 'Keys.onReturnPressed:' not in text



def test_freshness_tracks_each_data_domain_not_the_chart_timer():
    text = source()
    timer_block = text[text.index('interval: 1000'):text.index('    // Current top', text.index('interval: 1000'))]
    assert 'root.lastRefresh = root.refreshClock()' not in timer_block
    assert 'function markMetricFresh(metric)' in text
    for metric in ('cpuUsage', 'cpuTemperature', 'gpuUsage', 'gpuTemperature',
                   'gpuVram', 'memoryPercent', 'memoryUsed', 'memoryTotal',
                   'network', 'diskPercent', 'diskUsed', 'diskTotal',
                   'uptime', 'loadAverage'):
        assert f'root.markMetricFresh("{metric}")' in text
    fn_start = text.index('function markMetricFresh(metric)')
    fn_end = text.index('function updateDataStatus', fn_start)
    fn_body = text[fn_start:fn_end]
    assert fn_body.count('root.lastRefresh = root.refreshClock()') == 1
    # The refresh must be conditional — preceded by an if-guard, not bare.
    assert 'if (stale.length === 0) root.lastRefresh = root.refreshClock()' in fn_body


def test_charts_show_filling_indicator_until_history_is_full():
    """P0#2 + P1#3: charts show FILLING % and hide time labels while warming up."""
    text = source()
    assert 'function historyFillProgress()' in text
    assert 'function historyFilling()' in text
    assert 'FILLING' in text
    assert 'historyFillProgress() * 100' in text
    # Time labels must be gated on !historyFilling()
    assert 'visible: !root.historyFilling()' in text



def test_each_sensor_marks_its_own_metric_fresh():
    text = source()
    expected = {
        "cpuUsage", "cpuTemperature", "gpuUsage", "gpuTemperature", "gpuVram",
        "memoryPercent", "memoryUsed", "memoryTotal", "network", "diskPercent",
        "diskUsed", "diskTotal", "uptime", "loadAverage",
    }
    assert "property var metricUpdateMs" in text
    for metric in expected:
        assert f'root.markMetricFresh("{metric}")' in text
    assert "property var domainUpdateMs" not in text


def test_cpu_processes_are_computed_from_interval_samples():
    text = source()
    assert 'cpu_process_snapshot.py' in text
    assert "MonitorLogic.cpuProcessRates" in text
    assert 'ps -eo pcpu=' not in text


def test_timelines_label_the_midpoint_as_past_time():
    text = source()
    assert text.count('text: "−1 MIN"') == 2
    assert 'text: "1 MIN"' not in text


def test_process_rows_reserve_a_right_aligned_value_column():
    text = source()
    assert "id: processValue" in text
    assert "anchors.right: parent.right" in text
    assert "anchors.right: processValue.left" in text


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
