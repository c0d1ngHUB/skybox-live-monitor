#!/usr/bin/env python3
"""Static regression checks for the Skybox Plasma live monitor."""
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "contents/ui/main.qml"
AI_HELPER = Path(__file__).parents[1] / "contents/code/ai_services_status.py"
GPU_HELPER = Path(__file__).parents[1] / "contents/code/gpu_telemetry.py"
CI_WORKFLOW = Path(__file__).parents[1] / ".github/workflows/ci.yml"


def source():
    return SOURCE.read_text()



def test_freshness_is_tracked_per_metric_and_compact_status_is_visible():
    text = source()
    assert 'property string lastRefresh' in text
    assert 'property var metricUpdateMs' in text
    assert 'id: telemetryStatus' in text
    assert 'text: root.statusLabel() + root.dataStatusAgeText()' in text
    assert 'visible: true' in text
    assert 'function dataStatusAgeText()' in text
    assert 'root.dataStatus = "LIVE"' in text
    assert '"DEBIAN 13 · PLASMA 6 · REFRESH " + root.lastRefresh' not in text
    assert 'function refreshClock()' in text


def test_telemetry_status_text_is_width_bounded_for_elision():
    text = source()
    block = text[text.index("id: telemetryStatus"):text.index("// --- SYSTEM LOAD section")]
    assert "width: parent.width - 28" in block
    assert "horizontalAlignment: Text.AlignHCenter" in block
    assert "elide: Text.ElideRight" in block


def test_process_sources_distinguish_empty_results_from_command_failures():
    text = source()
    gpu = text[text.index("id: gpuTelemetrySource"):text.index("id: topCpuSource")]
    cpu = text[text.index("id: topCpuSource"):text.index("id: topRamSource")]
    ram = text[text.index("id: topRamSource"):text.index("id: netDetectSource")]
    assert 'root.cpuProcessUnavailable = Number(data["exit code"]) !== 0' in cpu
    assert 'root.ramProcessUnavailable = Number(data["exit code"]) !== 0' in ram
    assert 'var fullUnavailable = Number(data["exit code"]) !== 0 || !payload || !payload.gpus' in gpu
    assert 'if (!fullUnavailable) fullUnavailable = !!payload.gpu_error || (!!payload.error && !payload.process_error)' in gpu
    assert 'root.gpuProcessUnavailable = fullUnavailable || payload === null || payload.processes_available === false' in gpu
    assert 'root.applyGpuTelemetry(null, 0)' in gpu
    assert 'root.applyGpuTelemetry(null, 1)' in gpu
    assert '"NO ACTIVE WORKLOAD"' in text


def test_gpu_cards_separate_full_telemetry_failure_from_process_failure():
    text = source()
    assert 'property bool gpuProcessUnavailable: false' in text
    assert 'gpuTelemetryUnavailable' not in text
    gpu_cards = text[text.index('{kind:"gpu", label:"GPU 0'):text.index('{kind:"cpu"')]
    assert 'processUnavailable:!root.gpu0Available || root.gpuProcessUnavailable' in gpu_cards
    assert 'processUnavailable:!root.gpu1Available || root.gpuProcessUnavailable' in gpu_cards
    assert 'gpuTelemetryUnavailable ||' not in gpu_cards


def test_ci_executes_all_pytest_suites_instead_of_importing_test_files():
    workflow = CI_WORKFLOW.read_text()
    assert "python3 -m pytest -q" in workflow
    assert "python3 tests/test_openai_key_count.py" not in workflow


def test_gpu_card_prioritizes_vram_and_active_workload_context():
    text = source()
    assert 'function shortProcessName(name)' in text
    assert 'function compactProcessValue(metricLabel, value)' in text
    assert 'detail:root.gpu0Available ? Math.round(root.gpu0Temp) + "°C" : "UNAVAILABLE"' in text
    assert 'detail:root.gpu1Available ? Math.round(root.gpu1Temp) + "°C" : "UNAVAILABLE"' in text
    assert 'powerText:root.gpuPowerText(root.gpu0PowerDrawWatts, root.gpu0PowerLimitWatts)' in text
    assert 'powerText:root.gpuPowerText(root.gpu1PowerDrawWatts, root.gpu1PowerLimitWatts)' in text
    assert 'text: modelData.powerText || ""' in text
    assert 'vramFill:root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB)' in text
    assert 'vramFill:root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB)' in text
    assert 'text: "VRAM " + Math.round(modelData.vramFill || 0) + "%"' in text
    assert 'gpuFill:' not in text
    assert 'id: gpuTelemetrySource' in text
    assert 'gpu_telemetry.py' in text
    assert 'gpu0ProcessCount' in text and 'gpu1ProcessCount' in text


def test_cpu_card_shows_top_two_processes_in_its_detail_area():
    text = source()
    assert 'property var topCpuProcesses' in text
    assert 'id: topCpuSource' in text
    assert 'cpu_process_snapshot.py' in text
    assert 'MonitorLogic.cpuProcessRates' in text
    assert 'property string heading: metricKind === "cpu" ? "TOP · CPU %" : (metricKind === "gpu" ? "TOP · VRAM" : "TOP · RAM")' in text
    assert 'processes:root.topCpuProcesses' in text
    assert 'MonitorLogic.cpuProcessRates(root.previousCpuSamples, samples, elapsedMs, 2)' in text
    assert 'onTriggered: topCpuSource.connectSource(topCpuSource.command)' in text


def test_ram_card_shows_top_two_processes_in_its_detail_area():
    text = source()
    assert 'property var topRamProcesses' in text
    assert 'id: topRamSource' in text
    assert 'ps -eo rss=,comm= --sort=-rss | head -2' in text
    assert 'processes:root.topRamProcesses' in text
    assert 'if (isFinite(mib) && mib >= 1024) return (mib / 1024).toFixed(1) + " GiB"' in text
    assert 'processes.length < 2' in text
    assert 'onTriggered: topRamSource.connectSource(topRamSource.command)' in text


def test_gpu_card_shows_top_two_processes_but_counts_all_workloads():
    """The card is capped at two rows while its workload count remains exact."""
    text = source()
    helper = GPU_HELPER.read_text()
    assert 'property var topGpu0Processes: []' in text
    assert 'property var topGpu1Processes: []' in text
    assert 'gpu_uuid,pid,process_name,used_memory' in helper
    assert 'process_count' in helper
    assert 'processes[:2]' in helper
    assert 'service_name_for_pid' in helper
    assert 'processes:root.topGpu0Processes' in text
    assert 'processes:root.topGpu1Processes' in text
    assert 'onTriggered: gpuTelemetrySource.connectSource(gpuTelemetrySource.command)' in text


def test_charts_are_two_minute_and_visually_readable():
    text = source()
    assert 'property int historySeconds: 120' in text
    assert text.count('text: "−2 MIN"') == 3
    assert text.count('text: "−1 MIN"') == 3
    assert text.count('var midTick = plotLeft + chartWidth / 2') == 3
    assert 'id: computeTimeline' in text
    assert 'id: downloadTimeline' in text
    assert 'id: uploadTimeline' in text
    assert 'DOWNLOAD · ' in text
    assert 'UPLOAD · ' in text
    assert 'networkTimeline' not in text


def test_warning_banner_names_the_primary_cause_and_gpu_chart_marks_now():
    text = source()
    assert 'function statusReason()' in text
    assert 'return "GPU 0 VRAM " + Math.round(gpu0Vram) + "%"' in text
    assert 'return "GPU 1 TEMP " + Math.round(root.gpu1Temp) + "°C"' in text
    assert 'return serviceNames[i] + " " + state' in text
    assert 'return (severity === "WARNING" ? "▲ " : "✕ ") + root.statusReason()' in text
    assert 'var currentIndex = data.length - 1' in text
    assert 'ctx.arc(currentX, currentY, 5, 0, Math.PI * 2)' in text


def test_compact_cards_preserve_legible_operational_detail():
    text = source()
    assert 'Layout.preferredHeight: 278' in text
    assert 'columns: 2' in text and 'rows: 2' in text
    assert 'Layout.preferredHeight: 94' in text
    assert 'font.pixelSize: 13' in text
    assert 'font.pixelSize: 14' in text
    assert 'Text { width: parent.width - 4; text: modelData.label' in text
    assert 'elide: Text.ElideMiddle' in text
    assert 'elide: Text.ElideRight' in text
    assert '"SYSTEM DISK /"' in text
    assert 'text: "LOAD 1M"' in text
    assert 'text: "PROZESSE"' in text






def test_compute_chart_has_dedicated_graph_and_timeline_space():
    text = source()
    assert 'Layout.preferredHeight: 190' in text
    assert 'anchors.bottom: computeTimeline.top' in text
    assert 'anchors.bottomMargin: 10' in text
    assert 'height: 20' in text


def test_network_uses_dynamic_scale_ceilings_without_metadata_labels():
    text = source()
    assert 'id: networkLiveValues' in text
    assert 'id: networkMetadata' not in text
    assert 'MonitorLogic.adaptiveNetworkScale(root.downHistory, "download")' in text
    assert 'MonitorLogic.adaptiveNetworkScale(root.upHistory, "upload")' in text
    assert 'Math.min(1, d[j] / root.downloadScaleBytesPerSecond)' in text
    assert 'Math.min(1, d[j] / root.uploadScaleBytesPerSecond)' in text
    assert 'Layout.preferredHeight: 288' in text


def test_network_axes_follow_adaptive_kbit_and_mbit_steps():
    text = source()
    assert 'function networkAxisUnit(maxMbit)' in text
    assert 'return maxMbit < 1 ? "KBIT/S" : "MBIT/S"' in text
    assert 'var gridStepMbit = root.downloadGridStepMbit' in text
    assert 'var gridStepMbit = root.uploadGridStepMbit' in text
    assert 'Math.round(maxMbit / gridStepMbit)' in text
    assert '(maxMbit - i * gridStepMbit) * axisFactor' in text
    assert text.count('var plotLeft = 44') >= 2
    assert text.count('var chartWidth = width - plotLeft') >= 2
    # Axis labels must not be clipped by Canvas left edge (gutter fix)
    assert text.count('ctx.fillText(root.fmtNetworkAxisValue(') >= 2


def test_network_live_values_scale_to_kbit_and_are_labeled_with_direction():
    """1250 B/s must render as 10 KBIT/S (×1000), not 0.01 KBIT/S."""
    text = source()
    assert 'if (mbit < 1) return root.fmtNetworkAxisValue(mbit * 1000) + " KBIT/S"' in text
    assert 'return root.fmtNetworkAxisValue(mbit) + " MBIT/S"' in text
    assert 'root.fmtNetworkAxisValue(mbit) + " " + root.networkAxisUnit(mbit)' not in text
    # Live values are unambiguously labeled with ↓ (download) / ↑ (upload).
    assert 'text: "↓ " + root.fmtNetworkLive(root.down, root.downloadScaleBytesPerSecond)' in text
    assert 'text: "↑ " + root.fmtNetworkLive(root.up, root.uploadScaleBytesPerSecond)' in text


def test_footer_uses_explicit_disk_and_uptime_labels():
    text = source()
    assert 'text: "SYSTEM DISK /"' in text
    assert 'text: "UPTIME"' in text
    assert 'text: root.fmtUptime(root.uptimeSeconds)' in text
    assert 'text: "LOAD 1M"' in text
    assert 'text: root.loadAverage.toFixed(2)' in text
    assert 'text: "PROZESSE"' in text
    assert 'text: root.processCount' in text
    assert 'font.pixelSize: 12' not in text


def test_system_and_ai_service_rows_place_related_status_together():
    text = source()
    assert 'property int processCount: 0' in text
    assert 'property real hermesMaxThinkSeconds: 0' in text
    assert 'property string hermesMaxThinkService: ""' in text
    assert 'property int openAiActiveKeys: -1' in text
    assert 'property int openAiTotalKeys: -1' in text
    assert 'function fmtDuration(seconds)' in text
    assert 'id: processCountSource' in text
    assert 'ps -e --no-headers | wc -l' in text
    assert 'id: hermesThinkSource' in text
    assert 'hermes_max_think.py' in text
    assert 'id: openAiKeysSource' in text
    assert 'hermes_openai_keys.py' in text
    assert 'id: systemMetaGrid' in text
    assert 'columns: 2' in text and 'rows: 2' in text
    assert 'text: "LOAD 1M"' in text
    assert 'text: "PROZESSE"' in text
    assert 'text: "KI-RUN" + (root.hermesMaxThinkService.length > 0 ? " · " + root.hermesMaxThinkService : "")' in text
    assert 'return root.openAiActiveKeys + "/" + root.openAiTotalKeys + " BEREIT"' in text
    assert 'OPENAI 0AUTH' not in text
    assert 'id: openAiOauthCard' in text
    assert 'root.openAiOauthLabel()' in text
    assert 'root.openAiOauthTone()' in text
    assert 'function openAiOauthTone() { return root.serviceToneColor(root.openAiOauthState()) }' in text
    assert 'payload.openai_oauth_available' in text
    assert 'payload.openai_oauth_total' in text
    assert 'font.pixelSize: 14' in text
    assert 'Layout.preferredHeight: 148' in text



def test_gpu_card_uses_primary_gpu_value_and_one_unambiguous_vram_bar():
    text = source()
    assert 'property color orange: "#FF9F43"' in text
    assert 'color: (modelData.vramFill || 0) >= 85 ? root.critical : root.cyan' in text
    assert text.count('vramFill:root.vramPercent(') == 2
    assert 'text: "VRAM " + Math.round(modelData.vramFill || 0) + "%"' in text
    assert 'Math.max(0, Math.min(1, (modelData.vramFill || 0) / 100))' in text
    assert '(modelData.vramFill || 0) >= 85 ? root.critical : root.cyan' in text
    assert 'gpuFill:' not in text
    assert 'function fmtMemoryPair(usedBytes, totalBytes)' in text
    assert 'detail:root.fmtMemoryPair(root.ramUsedBytes, root.ramTotalBytes)' in text
    assert 'property real gpuVramUsedMiB' not in text


def test_dashboard_uses_the_full_available_height_without_clipping_content():
    text = source()
    assert 'opacity: 0.71' in text
    assert 'opacity: 0.56' not in text
    assert 'anchors.fill: parent' in text
    assert 'anchors.margins: 8' in text
    assert 'height: Math.min(parent.height - 24, content.implicitHeight + 68)' not in text
    assert 'anchors.fill: frame' in text
    assert 'anchors.topMargin: 20' in text
    assert 'anchors.bottomMargin: 20' in text
    assert 'height: implicitHeight' not in text
    assert 'Layout.fillHeight: true' in text
    assert 'Layout.minimumHeight: 160' in text
    assert 'Layout.minimumHeight: 230' in text


def test_header_shows_a_live_clock_centered_at_skybox_font_size():
    text = source()
    assert 'property string currentTime: refreshClock()' in text
    assert 'id: headerClock' in text
    assert 'text: root.currentTime' in text
    assert 'font.pixelSize: 18' in text
    assert 'font.pixelSize: 22' not in text
    assert 'statusLabel()' in text
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
    timer_block = text[text.index('interval: 1000', text.index('root.currentTime = root.refreshClock()')):text.index('    // Re-detect network interface every 30s in case of hotplug', text.index('interval: 1000', text.index('root.currentTime = root.refreshClock()')))]
    assert 'root.lastRefresh = root.refreshClock()' not in timer_block
    assert 'function markMetricFresh(metric)' in text
    for metric in ('cpuUsage', 'cpuTemperature', 'gpu0Telemetry', 'gpu1Telemetry',
                   'memoryPercent', 'memoryUsed', 'memoryTotal',
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
    """Charts show fill progress and NOW without hiding the time scale."""
    text = source()
    assert 'function historyFillProgress()' in text
    assert 'function historyFilling()' in text
    assert '% FILLED' in text
    assert '"NOW · "' in text
    assert 'historyFillProgress() * 100' in text
    assert 'visible: !root.historyFilling()' not in text
    assert text.count('text: "−2 MIN"') == 3
    assert text.count('text: "−1 MIN"') == 3



def test_each_sensor_marks_its_own_metric_fresh():
    text = source()
    expected = {
        "cpuUsage", "cpuTemperature", "gpu0Telemetry", "gpu1Telemetry",
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
    assert text.count('text: "−1 MIN"') == 3
    assert 'text: "1 MIN"' not in text


def test_ai_services_precede_system_load_and_use_normalized_accessible_states():
    text = source()
    assert text.index('// --- AI SERVICES section ---') < text.index('// --- SYSTEM LOAD section ---')
    assert 'root.serviceStateLabel(root.hermesGatewayState)' in text
    assert 'root.serviceStateLabel(root.hindsightState)' in text
    assert 'text: root.hindsightState;' not in text
    assert 'font.pixelSize: 11' not in text


def test_oauth_state_affects_global_status_and_header_has_one_status_source():
    text = source()
    severity = text[text.index('function statusSeverity()'):text.index('function statusTone()')]
    assert 'var oauthState = root.openAiOauthState()' in severity
    assert 'if (oauthState === "OFFLINE") return "CRITICAL"' in severity
    assert 'if (oauthState === "DEGRADED") return "WARNING"' in severity
    assert text.count('root.statusLabel()') == 1
    telemetry = text[text.index('id: telemetryStatus'):text.index('// --- AI SERVICES section ---')]
    assert 'font.pixelSize: 15' in telemetry


def test_system_load_graph_plots_both_gpus_without_cpu_or_motion():
    text = source()
    assert 'text: "━━ GPU 0 · RTX PRO 4000"' in text
    assert 'text: "━━ GPU 1 · RTX 3060 Ti"' in text
    assert 'CPU LOAD' not in text
    assert 'plot(root.cpuHistory' not in text
    assert 'plot(root.gpu0History, root.violet' in text
    assert 'plot(root.gpu1History, root.cyan' in text
    assert 'var plotHeight = Math.max(1, height - 4)' in text
    assert 'height - plotTop - (root.clamp(data[j]) / 100) * plotHeight' in text
    assert 'NumberAnimation' not in text


def test_timelines_remain_labeled_while_history_is_filling():
    text = source()
    assert 'visible: !root.historyFilling()' not in text
    assert text.count('text: "−2 MIN"') == 3
    assert text.count('text: "−1 MIN"') == 3


def test_gpu_power_uses_a_compact_bounded_draw_over_limit_label():
    text = source()
    assert 'function gpuPowerText(drawValue, limitValue)' in text
    assert 'return "POWER " + draw.toFixed(0) + "/" + limit.toFixed(0) + " W"' in text
    power_block = text[text.index('visible: modelData.kind === "gpu" && !!modelData.powerText'):text.index('// --- CPU card:')]
    assert 'width: parent.width - 4' in power_block
    assert 'elide: Text.ElideRight' in power_block


def test_process_rows_reserve_a_right_aligned_value_column():
    text = source()
    assert "id: processValue" in text
    assert "anchors.right: parent.right" in text
    assert "anchors.right: processValue.left" in text


def test_ai_services_helper_is_local_only_and_null_safe_for_weekly_usage():
    text = AI_HELPER.read_text()
    assert 'hermes-gateway.service' in text
    assert '127.0.0.1:9177/health' in text
    assert '127.0.0.1:11435/health' in text
    assert '127.0.0.1:11435/v1/models' in text
    assert 'nvidia-smi' not in text
    assert 'http://' in text and '127.0.0.1' in text
    assert 'requests' not in text


def test_ai_services_and_dual_gpu_power_are_rendered_compactly():
    text = source()
    assert 'AI SERVICES' in text
    assert 'root.hermesGatewayState' in text
    assert 'root.hindsightState' in text
    assert 'root.localLlmState' in text
    assert 'root.localLlmModelName' in text
    assert 'QWEN 3.8' in text
    assert 'POWER ' in text
    assert 'root.gpu0PowerDrawWatts' in text and 'root.gpu1PowerDrawWatts' in text
    assert 'root.gpu0PowerLimitWatts' in text and 'root.gpu1PowerLimitWatts' in text
    assert 'elide: Text.ElideRight' in text
    assert 'height: 44' in text
    assert 'Layout.preferredHeight: 148' in text
    assert 'Layout.minimumHeight: 144' in text
    assert 'id: openAiOauthCard' in text


def test_gpu_temperature_uses_warning_at_85_and_critical_at_90():
    text = source()
    severity = text[text.index('function statusSeverity()'):text.index('function statusTone()')]
    assert 'root.gpu0Temp >= 90' in severity and 'root.gpu1Temp >= 90' in severity
    assert 'root.gpu0Temp >= 85' in severity and 'root.gpu1Temp >= 85' in severity
    assert 'root.gpu0Temp >= 75' not in severity and 'root.gpu1Temp >= 75' not in severity
    assert severity.index('if (state === "OFFLINE") return "CRITICAL"') < severity.index('root.cpuTemp >= 75')
    assert severity.index('if (oauthState === "OFFLINE") return "CRITICAL"') < severity.index('root.cpuTemp >= 75')
    assert 'function gpuTempColor(value, normalColor)' in text
    gpu_cards = text[text.index('{kind:"gpu", label:"GPU 0'):text.index('{kind:"cpu"')]
    assert 'root.gpu0Temp >= 90' in gpu_cards and 'root.gpu1Temp >= 90' in gpu_cards
    assert 'root.gpu0Temp >= 85' in gpu_cards and 'root.gpu1Temp >= 85' in gpu_cards
    assert 'root.gpu0Temp >= 75' not in gpu_cards and 'root.gpu1Temp >= 75' not in gpu_cards


def test_dual_gpu_helper_is_local_and_maps_processes_by_uuid():
    helper = GPU_HELPER.read_text()
    assert 'nvidia-smi' in helper
    assert 'gpu_uuid,pid,process_name,used_memory' in helper
    assert 'by_uuid.get(gpu["uuid"], [])' in helper
    assert '/proc' in helper or 'proc_root' in helper
    assert 'http://' not in helper and 'https://' not in helper


def test_removed_ollama_service_has_no_stale_references():
    assert 'ollama' not in source().lower()
    assert 'ollama' not in AI_HELPER.read_text().lower()


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
