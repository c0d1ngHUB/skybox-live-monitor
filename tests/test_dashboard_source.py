#!/usr/bin/env python3
"""Static regression checks for the Skybox Plasma live monitor."""
import re
import struct
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "contents/ui/main.qml"
AI_HELPER = Path(__file__).parents[1] / "contents/code/ai_services_status.py"
GPU_HELPER = Path(__file__).parents[1] / "contents/code/gpu_telemetry.py"
CI_WORKFLOW = Path(__file__).parents[1] / ".github/workflows/ci.yml"


def source():
    return SOURCE.read_text()



def test_global_status_banner_and_service_summary_are_removed():
    text = source()
    assert 'function refreshClock()' in text
    for removed in (
        'id: telemetryStatus',
        'aiServicesSummary',
        'statusSeverity',
        'statusTone',
        'statusBackground',
        'statusReason',
        'statusLabel',
        'dataStatusAgeText',
        'dataStatus',
        'lastRefresh',
        'metricUpdateMs',
        'markMetricFresh',
        'updateDataStatus',
    ):
        assert removed not in text, f"removed status machinery still present: {removed}"


def test_qwen_service_is_removed_from_the_dashboard_and_the_helper():
    text = source()
    assert 'QWEN' not in text
    assert 'localLlm' not in text
    helper = AI_HELPER.read_text()
    assert '11435' not in helper
    assert 'local_llm' not in helper


def test_gateway_and_hindsight_cards_fill_the_service_row():
    """Two cards replace three, so each must span half the row minus the gap."""
    text = source()
    section = text[text.index('// --- AI SERVICES section ---'):text.index('// --- SYSTEM LOAD section ---')]
    assert section.count('width: (parent.width - 10) / 2') == 2
    assert 'width: (parent.width - 20) / 3' not in section
    assert 'GATEWAY' in section and 'HINDSIGHT' in section


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


def test_cpu_card_shows_top_four_processes_in_its_detail_area():
    text = source()
    assert 'property var topCpuProcesses' in text
    assert 'id: topCpuSource' in text
    assert 'cpu_process_snapshot.py' in text
    assert 'MonitorLogic.cpuProcessRates' in text
    assert 'property string heading: metricKind === "cpu" ? root.cpuProcessHeading() : (metricKind === "gpu" ? "TOP · VRAM" : "TOP · RAM")' in text
    assert 'function cpuProcessHeading()' in text
    assert 'function cpuDetailLabel()' in text
    assert 'cpu/all/coreCount' in text
    assert 'processes:root.topCpuProcesses' in text
    assert 'MonitorLogic.cpuProcessRates(root.previousCpuSamples, samples, elapsedMs, 4)' in text
    assert 'onTriggered: topCpuSource.connectSource(topCpuSource.command)' in text


def test_ram_card_shows_top_four_processes_in_its_detail_area():
    text = source()
    assert 'property var topRamProcesses' in text
    assert 'id: topRamSource' in text
    assert 'ps -eo rss=,comm= --sort=-rss | head -4' in text
    assert 'processes:root.topRamProcesses' in text
    assert 'if (isFinite(mib) && mib >= 1024) return (mib / 1024).toFixed(1) + " GiB"' in text
    assert 'processes.length < 4' in text
    assert 'onTriggered: topRamSource.connectSource(topRamSource.command)' in text


def test_gpu_card_shows_top_four_processes_but_counts_all_workloads():
    """The card is capped at four rows while its workload count remains exact."""
    text = source()
    helper = GPU_HELPER.read_text()
    assert 'property var topGpu0Processes: []' in text
    assert 'property var topGpu1Processes: []' in text
    assert 'gpu_uuid,pid,process_name,used_memory' in helper
    assert 'process_count' in helper
    assert 'processes[:4]' in helper
    assert 'service_name_for_pid' in helper
    assert 'processes:root.topGpu0Processes' in text
    assert 'processes:root.topGpu1Processes' in text
    assert 'onTriggered: gpuTelemetrySource.connectSource(gpuTelemetrySource.command)' in text


def _match_int(pattern, text):
    match = re.search(pattern, text)
    assert match, f"pattern not found in the QML source: {pattern}"
    return int(match.group(1))


def _grid_geometry(cards):
    """Grid metrics of the four process cards, read from the QML source."""
    grid = cards[cards.index("Grid {"):cards.index("Repeater {")]
    return {
        "height": _match_int(r"Layout\.preferredHeight:\s*(\d+)", grid),
        "columns": _match_int(r"\bcolumns:\s*(\d+)", grid),
        "rows": _match_int(r"\brows:\s*(\d+)", grid),
        "columnSpacing": _match_int(r"columnSpacing:\s*(\d+)", grid),
        "rowSpacing": _match_int(r"rowSpacing:\s*(\d+)", grid),
    }


def _delegate_geometry(cards):
    delegate = cards[cards.index("delegate: Rectangle {"):cards.index("id: metricKpi")]
    return {
        "columnGap": _match_int(r"width:\s*\(parent\.width\s*-\s*(\d+)\)", delegate),
        "columns": _match_int(r"width:\s*\(parent\.width\s*-\s*\d+\)\s*/\s*(\d+)", delegate),
        "rowGap": _match_int(r"height:\s*\(parent\.height\s*-\s*(\d+)\)", delegate),
        "rows": _match_int(r"height:\s*\(parent\.height\s*-\s*\d+\)\s*/\s*(\d+)", delegate),
        "margins": _match_int(r"anchors\.margins:\s*(\d+)", delegate),
    }


def _coupled_process_row_count(text):
    """The per-card row cap is duplicated in four places; they must agree.

    A silent mismatch (e.g. a raised ``head`` without the matching QML cap)
    renders fewer rows than the layout reserves.
    """
    limits = {
        "cpuProcessRates": re.search(
            r"cpuProcessRates\(root\.previousCpuSamples, samples, elapsedMs, (\d+)\)", text
        ),
        "ps head": re.search(r"--sort=-rss \| head -(\d+)", text),
        "ps loop cap": re.search(r"processes\.length < (\d+)", text),
        "gpu helper slice": re.search(r"processes\[:(\d+)\]", GPU_HELPER.read_text()),
    }
    assert all(limits.values()), f"row cap not found in: {[k for k, v in limits.items() if not v]}"
    values = {int(match.group(1)) for match in limits.values()}
    assert len(values) == 1, f"row caps disagree: { {k: m.group(1) for k, m in limits.items()} }"
    return values.pop()


def _dejavu_mono_metrics():
    """Ascender/descender of the real font, parsed from the TTF (stdlib only)."""
    candidates = sorted(Path("/usr/share/fonts").glob("**/DejaVuSansMono.ttf"))
    if not candidates:
        pytest.skip("DejaVu Sans Mono is not installed")
    data = candidates[0].read_bytes()
    tables = {}
    for index in range(struct.unpack(">H", data[4:6])[0]):
        entry = 12 + index * 16
        tables[data[entry:entry + 4]] = struct.unpack(">I", data[entry + 8:entry + 12])[0]
    upm = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
    ascender = struct.unpack(">h", data[tables[b"hhea"] + 4:tables[b"hhea"] + 6])[0]
    descender = struct.unpack(">h", data[tables[b"hhea"] + 6:tables[b"hhea"] + 8])[0]
    return (ascender - descender) / upm


def test_process_cards_keep_their_height_while_showing_four_rows():
    """Four process rows plus the heading must fit the body the grid reserves.

    Every constant is read from the QML source and from the real font metrics.
    Restating the literals here would make the check a tautology that cannot
    notice a card resize, a taller row or a fifth row.
    """
    text = source()
    cards = text[text.index("// --- Dual-GPU row"):text.index("// --- NETWORK section")]
    grid = _grid_geometry(cards)
    delegate = _delegate_geometry(cards)

    assert delegate["columns"] == grid["columns"], "card delegate and Grid columns disagree"
    assert delegate["rows"] == grid["rows"], "card delegate and Grid rows disagree"
    assert grid["columns"] * grid["rows"] == len(re.findall(r'\{kind:"', cards)), (
        "the grid does not have one cell per card"
    )

    card_height = (grid["height"] - delegate["rowGap"]) / grid["rows"]
    inner_height = card_height - 2 * delegate["margins"]

    process_details = cards[cards.index("id: processDetails"):]
    row_height = _match_int(r"height:\s*(\d+)", process_details)
    spacing = _match_int(r"spacing:\s*(\d+)", process_details)
    font_size = _match_int(r"font\.pixelSize:\s*(\d+)", process_details)
    row_count = _coupled_process_row_count(text)

    assert row_count == 4
    needed = _dejavu_mono_metrics() * font_size + row_count * row_height + row_count * spacing
    assert needed <= inner_height, (
        f"{row_count} rows need {needed:.1f} px but the card body offers {inner_height:.0f} px"
    )


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


def test_gpu_chart_marks_the_now_point_without_the_removed_banner():
    text = source()
    assert 'function statusReason()' not in text
    assert 'var currentIndex = data.length - 1' in text
    assert 'ctx.arc(currentX, currentY, 5, 0, Math.PI * 2)' in text


def test_compact_cards_preserve_legible_operational_detail():
    text = source()
    assert 'Layout.preferredHeight: 278' in text
    assert 'columns: 2' in text and 'rows: 2' in text
    assert 'Layout.preferredHeight: 94' in text
    assert 'font.pixelSize: 13' in text
    assert 'font.pixelSize: 14' in text
    assert 'mainText: modelData.label' in text
    assert 'Text { anchors.fill: parent; text: modelData.label' in text
    assert 'elide: Text.ElideMiddle' in text
    assert 'elide: Text.ElideRight' in text
    assert '"SYSTEM DISK /"' in text
    assert 'text: "LOAD 1M"' in text
    assert 'text: "PROCESSES"' in text






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
    # The axis unit, the tick factor and the live rate all come from one decision
    # in MonitorLogic (peak in the window + current rate), never from the retained
    # hysteresis ceiling.
    assert 'function networkAxisUnit(direction)' in text
    assert 'MonitorLogic.networkAxisUnit(root.networkPeak(direction), root.networkLive(direction))' in text
    assert 'function networkAxisFactor(direction)' in text
    assert 'return root.networkUseMbit(direction) ? 1 : 1000' in text
    assert text.count('var axisFactor = root.networkAxisFactor(') == 2
    assert 'var gridStepMbit = root.downloadGridStepMbit' in text
    assert 'var gridStepMbit = root.uploadGridStepMbit' in text
    assert 'Math.round(maxMbit / gridStepMbit)' in text
    assert '(maxMbit - i * gridStepMbit) * axisFactor' in text
    assert text.count('var plotLeft = 44') >= 2
    assert text.count('var chartWidth = width - plotLeft') >= 2
    # Axis labels must not be clipped by Canvas left edge (gutter fix)
    assert text.count('ctx.fillText(root.fmtNetworkAxisValue(') >= 2


def test_network_live_values_scale_to_kbit_and_are_labeled_with_direction():
    """1250 B/s must render as 10 KBIT/S (×1000), not 0.01 KBIT/S.

    The unit decision lives in MonitorLogic.networkRateLabel so the summary line
    and the axis can no longer disagree; test_monitor_behavior pins the numbers.
    """
    text = source()
    assert 'function networkLiveLabel(direction)' in text
    assert 'MonitorLogic.networkRateLabel(root.networkLive(direction), root.networkUseMbit(direction))' in text
    assert 'fmtNetworkLive' not in text
    # Live values are unambiguously labeled with ↓ (download) / ↑ (upload).
    assert 'text: "↓ " + root.networkLiveLabel("down")' in text
    assert 'text: "↑ " + root.networkLiveLabel("upload")' in text


def test_footer_uses_explicit_disk_and_uptime_labels():
    text = source()
    assert 'text: "SYSTEM DISK /"' in text
    assert 'text: "UPTIME"' in text
    assert 'text: root.fmtUptime(root.uptimeSeconds)' in text
    assert 'text: "LOAD 1M"' in text
    assert 'text: root.loadAverage.toFixed(2)' in text
    assert 'text: "PROCESSES"' in text
    assert 'text: root.processCount' in text
    # The disk summary line carries df semantics plus the ext4 reserve; it stays
    # in the 12px tier because the label is longer than the old FREE/USED pair.
    assert 'text: root.fmtDiskSummary()' in text
    assert 'font.pixelSize: 12' in text


def test_disk_card_uses_the_df_helper_instead_of_the_plasma_sensors():
    """The sensors cannot express df's "used": disk/all/used counts the ext4 root
    reserve as used and disk/all/free is f_bavail, so the card read
    "USED 858.0 GiB / 24%" where df reports "674G / 20%"."""
    text = source()
    assert 'id: diskUsageSource' in text
    assert 'disk_usage.py' in text
    assert 'onTriggered: diskUsageSource.connectSource(diskUsageSource.command)' in text
    assert 'root.diskUsedBytesDf = used' in text
    assert 'root.diskPercentDf = percent' in text
    assert 'function diskPercent() { return root.diskPercentDf }' in text
    assert 'function diskUsedBytes() { return root.diskUsedBytesDf }' in text
    # A df view needs f_bfree, which no Plasma disk sensor exposes.
    assert 'root.diskFreeBytesRaw' not in text
    assert 'root.diskUsedPercent(' not in text


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
    assert 'text: "PROCESSES"' in text
    assert 'sessionLabel: "HERMES-SESSION"' in text
    assert 'root.openAiActiveKeys + "/" + root.openAiTotalKeys + " KEYS"' in text
    assert 'OPENAI 0AUTH' not in text
    assert 'id: openAiOauthCard' in text
    assert 'root.openAiOauthLabel()' in text
    assert 'root.openAiOauthTone()' in text
    assert 'function openAiOauthTone() { return root.serviceToneColor(root.openAiOauthState()) }' in text
    assert 'payload.openai_oauth_available' in text
    assert 'payload.openai_oauth_total' in text
    assert 'font.pixelSize: 14' in text
    assert 'Layout.preferredHeight: 178' in text



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


def test_header_shows_a_live_clock_centered_at_two_and_a_half_times_size():
    """18 px * 2.5 = 45 px for the header clock."""
    text = source()
    assert 'property string currentTime: refreshClock()' in text
    assert 'id: headerClock' in text
    assert 'text: root.currentTime' in text
    assert 'font.pixelSize: 45' in text
    assert 'font.pixelSize: 22' not in text
    assert 'anchors.horizontalCenter: parent.horizontalCenter' in text
    assert 'font.pixelSize: 28' in text
    assert 'root.currentTime = root.refreshClock()' in text


def test_header_clock_uses_hours_and_minutes_without_seconds():
    text = source()
    clock = text[text.index('function refreshClock()'):text.index('    function currentTimezoneLabel')]
    assert 'now.getHours()' in clock
    assert 'now.getMinutes()' in clock
    assert 'now.getSeconds()' not in clock


def test_removed_unload_control_has_no_visual_or_accessible_action():
    text = source()
    assert 'Accessible.name: "Unload Ollama models from GPU memory"' not in text
    assert 'Keys.onSpacePressed:' not in text
    assert 'Keys.onReturnPressed:' not in text



def test_freshness_timer_only_advances_the_clock_and_charts():
    text = source()
    # The 1 s chart timer keeps only the clock and the history buffers: the
    # removed freshness bookkeeping must not come back through the timer path.
    timer_block = text[text.index('\n                root.currentTime = root.refreshClock()'):text.index('// df(1) view of the local root')]
    assert 'root.currentTime = root.refreshClock()' in timer_block
    assert 'computeGraph.requestPaint()' in timer_block
    assert 'root.lastRefresh' not in timer_block
    assert 'markMetricFresh' not in text
    assert 'updateDataStatus' not in text


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



def test_each_sensor_and_source_matches_the_removed_freshness_tracking():
    text = source()
    assert "metricUpdateMs" not in text
    assert "root.markMetricFresh" not in text
    assert "domainUpdateMs" not in text
    # The telemetry sources still deliver their values, just without the
    # freshness bookkeeping that only fed the removed status banner.
    assert 'root.cpu = root.clamp(parseFloat(value))' in text
    assert 'root.ram = root.clamp(parseFloat(value))' in text


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


def test_oauth_state_still_labels_the_card_without_the_global_banner():
    text = source()
    assert 'root.openAiOauthLabel()' in text
    assert text.count('root.statusLabel()') == 0
    assert 'id: telemetryStatus' not in text


def test_status_cards_use_symbols_and_quiet_healthy_borders():
    text = source()
    assert 'function serviceBorderColor(rawState)' in text
    assert text.count('border.color: root.serviceBorderColor(') == 2
    assert 'border.color: root.openAiOauthBorderColor()' in text
    assert 'function openAiOauthBorderColor()' in text


def test_system_load_graph_plots_both_gpus_without_cpu_or_motion():
    text = source()
    # Legend names come from the live telemetry, never from literals: a hardcoded
    # "RTX PRO 4000" kept claiming that GPU after nvidia-smi went away.
    assert 'root.gpu0Name' in text and 'root.gpu1Name' in text
    assert 'RTX PRO 4000' not in text
    assert 'RTX 3060 Ti' not in text
    assert 'UNAVAILABLE")' in text
    assert 'CPU LOAD' not in text
    assert 'plot(root.cpuHistory' not in text
    assert 'plot(root.gpu0History, root.violet' in text
    assert 'plot(root.gpu1History, root.cyan' in text
    assert 'var plotHeight = Math.max(1, height - 4)' in text
    assert 'height - plotTop - (root.clamp(data[j]) / 100) * plotHeight' in text
    assert 'NumberAnimation' not in text


def test_system_load_chart_has_live_values_five_ticks_and_non_color_line_styles():
    text = source()
    assert 'Math.round(root.gpu0Usage) + "%"' in text
    assert 'Math.round(root.gpu1Usage) + "%"' in text
    assert 'for (var i = 0; i < 5; i++)' in text
    for label in ('"100%"', '"75%"', '"50%"', '"25%"', '"0%"'):
        assert f'ctx.fillText({label}' in text
    assert 'ctx.setLineDash(dashed ? [8, 5] : [])' in text
    assert 'plot(root.gpu1History, root.cyan, "rgba(150,245,246,0.07)", false)' in text


def test_elided_model_process_and_system_texts_expose_full_tooltips():
    text = source()
    assert text.count('PlasmaCore.ToolTipArea {') >= 4
    assert 'mainText: parent.process.name' in text
    assert 'mainText: modelData.label' in text
    assert 'sessionLabel: "HERMES-SESSION"' in text
    assert '"Längste abgeschlossene Hermes-Antwort der letzten 24 h" + profileHint' in text


def test_normal_frames_are_quiet_while_alert_borders_remain_semantic():
    text = source()
    assert 'property color quietBorder:' in text
    assert 'border.color: root.quietBorder' in text
    assert 'if (state === "DEGRADED") return root.warning' in text
    assert 'if (state === "OFFLINE") return root.critical' in text


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
    assert '11435' not in text
    assert 'nvidia-smi' not in text
    assert 'http://' in text and '127.0.0.1' in text
    assert 'requests' not in text


def test_ai_services_and_dual_gpu_power_are_rendered_compactly():
    text = source()
    assert 'AI SERVICES' in text
    assert 'root.hermesGatewayState' in text
    assert 'root.hindsightState' in text
    assert 'QWEN' not in text
    assert 'localLlm' not in text
    assert 'POWER ' in text
    assert 'root.gpu0PowerDrawWatts' in text and 'root.gpu1PowerDrawWatts' in text
    assert 'root.gpu0PowerLimitWatts' in text and 'root.gpu1PowerLimitWatts' in text
    assert 'elide: Text.ElideRight' in text
    assert 'height: 44' in text
    assert 'Layout.preferredHeight: 178' in text
    assert 'Layout.minimumHeight: 174' in text
    assert 'id: openAiOauthCard' in text
    assert 'id: obsHealthCard' in text
    assert 'root.obsHealthLabel()' in text
    assert 'root.obsHealthTone()' in text


def test_gpu_temperature_uses_warning_at_85_and_critical_at_90():
    text = source()
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


def test_openai_keys_source_resets_to_unknown_on_invalid_helper_output():
    """A helper failure must override a previously valid count, not keep it stale."""
    text = source()
    block = text[text.index("id: openAiKeysSource"):text.index("id: aiServicesSource")]
    assert 'buffer.trim().match(/^(-?' + chr(92) + 'd+)' + chr(92) + 's+(' + chr(92) + 'd+)$/' in block
    assert 'if (Number(data["exit code"]) !== 0 || !match) {' in block
    assert 'root.openAiActiveKeys = -1' in block
    assert 'root.openAiTotalKeys = 0' in block
    assert 'root.openAiActiveKeys = parseInt(match[1])' in block
    assert 'root.openAiTotalKeys = parseInt(match[2])' in block


def test_openai_keys_polls_again_while_availability_is_unknown():
    text = source()
    assert 'running: root.openAiActiveKeys < 0 || root.openAiTotalKeys < 0' in text
    assert 'onTriggered: openAiKeysSource.connectSource(openAiKeysSource.command)' in text
    assert text.count('onTriggered: openAiKeysSource.connectSource(openAiKeysSource.command)') == 2


def test_ai_services_source_never_keeps_stale_oauth_counts():
    text = source()
    block = text[text.index("id: aiServicesSource"):text.index("id: gpuTelemetrySource")]
    assert 'if (Number(payload.openai_oauth_available) >= 0) root.openAiActiveKeys' in block
    assert 'if (Number(payload.openai_oauth_total) >= 0) root.openAiTotalKeys' in block
    assert 'if (Number(payload.openai_oauth_available) < 0 || Number(payload.openai_oauth_total) < 0) {' in block
    assert 'root.openAiActiveKeys = -1' in block
    assert 'root.openAiTotalKeys = 0' in block


def test_oauth_label_and_state_cover_every_helper_failure_mode():
    text = source()
    label = text[text.index('function openAiOauthLabel()'):text.index('function openAiOauthTone()')]
    assert '?/? KEYS' in label
    logic = (Path(__file__).parents[1] / "contents/code/monitor_logic.js").read_text()
    logic_block = logic[logic.index('function openAiOauthState('):logic.index('function openAiOauthSymbol(')]
    assert 'active < 0 || total < 0' in logic_block
    assert 'return "UNKNOWN"' in logic_block


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
