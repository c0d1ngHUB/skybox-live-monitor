import QtQuick 2.15

import QtQuick.Layouts 1.15
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid 2.0
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport
import org.kde.ksysguard.sensors 1.0 as Sensors
import "../code/monitor_logic.js" as MonitorLogic

PlasmoidItem {
    id: root
    property real cpu: root.systemSensorValue("cpu")
    property real ram: root.systemSensorValue("ram")
    property real ramUsedBytes: root.systemSensorValue("ramUsedBytes")
    property real ramTotalBytes: root.systemSensorValue("ramTotalBytes")
    property real cpuTemp: root.systemSensorValue("cpuTemp")
    property var systemSensorStates: ({})
    property bool gpu0Available: false
    property string gpu0Name: "GPU 0"
    property real gpu0Usage: root.gpuLoadStates["0"] ? root.gpuLoadStates["0"].usage : 0
    property real gpu0Temp: 0
    property real gpu0VramUsedMiB: 0
    property real gpu0VramTotalMiB: 0
    property real gpu0PowerDrawWatts: NaN
    property real gpu0PowerLimitWatts: 0
    property bool gpu1Available: false
    property string gpu1Name: "GPU 1"
    property real gpu1Usage: root.gpuLoadStates["1"] ? root.gpuLoadStates["1"].usage : 0
    property real gpu1Temp: 0
    property real gpu1VramUsedMiB: 0
    property real gpu1VramTotalMiB: 0
    property real gpu1PowerDrawWatts: NaN
    property real gpu1PowerLimitWatts: 0
    // Each GPU keeps its own scalar properties because QML does not notify
    // bindings on mutations inside a var array.
    property real down: 0
    property real up: 0
    // Dynamic network scales with hysteresis: grow immediately when the peak
    // exceeds the current ceiling, but only shrink when the peak drops below
    // 70% of it. Prevents axis flicker from transient spikes.
    property real downloadScaleBytesPerSecond: 0
    property real uploadScaleBytesPerSecond: 0
    property real downloadGridStepMbit: 0.0025
    property real uploadGridStepMbit: 0.0025
    onDownHistoryChanged: {
        var scale = MonitorLogic.adaptiveNetworkScale(root.downHistory, "download")
        var target = scale.ceilingMbit * 1000000 / 8
        if (target >= root.downloadScaleBytesPerSecond || target < root.downloadScaleBytesPerSecond * 0.7) {
            root.downloadScaleBytesPerSecond = target
            root.downloadGridStepMbit = scale.stepMbit
        }
    }
    onUpHistoryChanged: {
        var scale = MonitorLogic.adaptiveNetworkScale(root.upHistory, "upload")
        var target = scale.ceilingMbit * 1000000 / 8
        if (target >= root.uploadScaleBytesPerSecond || target < root.uploadScaleBytesPerSecond * 0.7) {
            root.uploadScaleBytesPerSecond = target
            root.uploadGridStepMbit = scale.stepMbit
        }
    }
    property real previousRxBytes: -1
    property real previousTxBytes: -1
    property double previousNetworkSampleMs: 0
    property real uptimeSeconds: 0
    property real loadAverage: 0
    property int processCount: 0
    property real hermesMaxThinkSeconds: 0
    property string hermesMaxThinkService: ""
    property bool hermesMaxThinkUnavailable: true
    property bool hermesMaxThinkPartial: false
    property int openAiActiveKeys: -1
    property int openAiTotalKeys: -1
    property int openAiFetchFailures: 0
    property string hermesGatewayState: "UNKNOWN"
    property string hindsightState: "UNKNOWN"
    // HINDSIGHT VERDICHTUNG row, read by contents/code/hindsight_observation_health.py.
    // Only the condensation numbers are displayed; the helper still ships the full
    // health payload (backlog, scope drift, orphan audit) for its CLI.
    property int obsCount: 0
    property real obsMergePct: -1
    property int obsMerged: -1
    property int obsOrphans: -1
    property int obsRawFacts: -1
    // Share of raw facts referenced by an observation and the mean proof_count
    // per observation (informational, no health gate).
    property real obsReferencedPct: -1
    property real obsAvgProofs: -1
    property bool obsHealthUnavailable: true

    // df(1) view, read by contents/code/disk_usage.py. The Plasma sensors cannot
    // express it: disk/all/used counts the filesystem reserve as used and
    // disk/all/free is f_bavail, so neither yields df's "used".
    property real diskTotalBytes: 0
    property real diskUsedBytesDf: 0
    property real diskAvailBytesDf: 0
    property real diskPercentDf: 0
    // Logical processors, for labelling per-process CPU percentages.
    property int cpuCoreCount: 0
    property string currentTime: refreshClock()
    // Fast sensors only. Service health and disk are on slower, separate cadences.
    property double telemetryNowMs: Date.now()
    property double lastCpuTelemetryMs: 0
    property double lastGpuTelemetryMs: 0
    property double lastNetworkTelemetryMs: 0
    function telemetryAge() {
        return MonitorLogic.telemetryAgeSeconds(root.telemetryNowMs, root.lastCpuTelemetryMs,
                                                root.lastGpuTelemetryMs, root.lastNetworkTelemetryMs)
    }
    function telemetryAgeLabel() {
        var age = root.telemetryAge()
        if (age === null) return "ABFRAGEN: WARTEN"
        return age > 15 ? "ABFRAGEN: ALT " + age + "S" : "ABFRAGEN: VOR " + age + "S"
    }
    // Four largest CPU, RAM, and GPU consumers, sampled every five seconds.
    property var topCpuProcesses: []
    property var previousCpuSamples: ({})
    property double previousCpuSampleMs: 0
    property bool cpuProcessUnavailable: false
    property var topRamProcesses: []
    property bool ramProcessUnavailable: false
    property var topGpu0Processes: []
    property int gpu0ProcessCount: 0
    property var topGpu1Processes: []
    property int gpu1ProcessCount: 0
    property bool gpuProcessUnavailable: false
    // 500-ms utilization samples are independent of 2-s VRAM/process snapshots.
    property var gpuLoadStates: ({})
    function applyGpuLoads(entries, nowMs) {
        var next = ({})
        for (var i = 0; i < 2; i++) {
            var key = String(i)
            next[key] = MonitorLogic.nextGpuLoadState(root.gpuLoadStates[key], entries[key] || null, nowMs)
        }
        root.gpuLoadStates = next
    }
    function gpuLoadAvailable(index) {
        return MonitorLogic.gpuLoadPeak(root.gpuLoadStates[String(index)], root.telemetryNowMs) !== null
    }
    function gpuPeakLabel(index) {
        var peak = MonitorLogic.gpuLoadPeak(root.gpuLoadStates[String(index)], root.telemetryNowMs)
        return "PEAK 30s · " + (peak === null ? "--" : Math.round(peak) + "%")
    }
    property int historySeconds: 120
    property double historyLastTickMs: 0
    property var cpuHistory: []
    property var gpu0History: []
    property var gpu1History: []
    property var ramHistory: []
    property var downHistory: []
    property var upHistory: []
    // Prefer the default-route interface; fall back to the first physical interface.
    property string netIf: ""
    property bool networkAvailable: false
    property double lastServiceTelemetryMs: 0

    // OAuth counts come from their own source (openAiKeysSource) and have
    // their own failure handling; a service-poll failure must not wipe them.
    function resetServices() {
        root.hermesGatewayState = "UNKNOWN"
        root.hindsightState = "UNKNOWN"
    }
    function resetNetwork() {
        root.networkAvailable = false
        root.down = 0
        root.up = 0
        root.previousRxBytes = -1
        root.previousTxBytes = -1
        root.previousNetworkSampleMs = 0
    }

    // P2b: Darker muted for better contrast hierarchy
    property color ink: "#E8F7FF"
    property color muted: "#A0C8D8"
    property color cyan: "#96F5F6"
    property color violet: "#DB91FF"
    property color blue: "#4FC3F7"
    property color orange: "#FF9F43"
    property color warning: "#FFD166"
    property color critical: "#FF6B6B"
    // Healthy structure stays quiet; warning and critical states retain full semantic color.
    property color quietBorder: Qt.rgba(0.63, 0.78, 0.85, 0.28)

    preferredRepresentation: fullRepresentation
    // Let the wallpaper show through outside the dashboard's own translucent frame.
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground

    function clamp(v) { return Math.max(0, Math.min(100, v || 0)) }
    function tempColor(value, normalColor) {
        if (value >= 85) return root.critical
        if (value >= 75) return root.warning
        return normalColor
    }
    function gpuTempColor(value, normalColor) {
        if (value >= 90) return root.critical
        if (value >= 85) return root.warning
        return normalColor
    }
    function fmtRate(v) {
        if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + " MB/s"
        if (v >= 1024) return (v / 1024).toFixed(1) + " KB/s"
        return Math.round(v) + " B/s"
    }
    function networkPeak(direction) {
        var history = direction === "upload" ? root.upHistory : root.downHistory
        var peak = 0
        for (var i = 0; i < history.length; i++) {
            var value = Number(history[i]) || 0
            if (value > peak) peak = value
        }
        return peak
    }
    function networkLive(direction) {
        return direction === "upload" ? root.up : root.down
    }
    function networkUseMbit(direction) {
        return MonitorLogic.networkPanelUsesMbit(root.networkPeak(direction), root.networkLive(direction))
    }
    function networkAxisUnit(direction) {
        return MonitorLogic.networkAxisUnit(root.networkPeak(direction), root.networkLive(direction))
    }
    // Tick numbers follow the same decision as the axis label: Kbit ceilings are
    // plotted in Kbit, Mbit ceilings in Mbit.
    function networkAxisFactor(direction) {
        return root.networkUseMbit(direction) ? 1 : 1000
    }
    function networkLiveLabel(direction) {
        if (!root.networkAvailable || root.telemetryNowMs - root.lastNetworkTelemetryMs > 5000) return "UNAVAILABLE"
        return MonitorLogic.networkRateLabel(root.networkLive(direction), root.networkUseMbit(direction))
    }
    function fmtNetworkAxisValue(value) {
        if (value >= 100) return Math.round(value).toString()
        if (value >= 10) return value.toFixed(1).replace(".0", "")
        return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")
    }
    function fmtGiB(v) {
        if (!v || v < 1) return "--"
        return (v / 1024 / 1024 / 1024).toFixed(1) + " GiB"
    }
    function fmtMemoryPair(usedBytes, totalBytes) {
        if (!isFinite(usedBytes) || usedBytes < 0 || !isFinite(totalBytes) || totalBytes <= 0) return "-- / -- GiB"
        var divisor = 1024 * 1024 * 1024
        // Whole GiB in the card: two decimals of "11.5 / 30.7 GiB" overflow the
        // narrow KPI column. The exact pair stays in the tooltip.
        return Math.round(usedBytes / divisor) + " / " + Math.round(totalBytes / divisor) + " GiB"
    }
    function fmtMemoryPairExact(usedBytes, totalBytes) {
        if (!isFinite(usedBytes) || usedBytes < 0 || !isFinite(totalBytes) || totalBytes <= 0) return "-- / -- GiB"
        var divisor = 1024 * 1024 * 1024
        return (usedBytes / divisor).toFixed(1) + " / " + (totalBytes / divisor).toFixed(1) + " GiB"
    }
    function fmtCompactCapacity(v) {
        if (!v || v < 1) return "--"
        return (v / 1024 / 1024 / 1024).toFixed(1) + "G"
    }
    // P0: Fixed VRAM formatting — use MiB directly from nvidia-smi, convert to GiB only above 1024
    function fmtVram(mib) {
        if (!mib || mib < 1) return "--"
        if (mib >= 1024) return (mib / 1024).toFixed(1) + " GiB"
        return Math.round(mib) + " MiB"
    }
    function fmtCompactVram(mib) {
        if (!mib || mib < 1) return "--"
        return mib >= 1024 ? (mib / 1024).toFixed(1) + "G" : Math.round(mib) + "M"
    }
    function fmtDisk(v) {
        if (!v || v < 1) return "--"
        if (v >= 1024 * 1024 * 1024 * 1024) return (v / 1024 / 1024 / 1024 / 1024).toFixed(1) + " TiB"
        return (v / 1024 / 1024 / 1024).toFixed(1) + " GiB"
    }
    function fmtUptime(seconds) {
        var days = Math.floor(seconds / 86400)
        var hours = Math.floor((seconds % 86400) / 3600)
        var mins = Math.floor((seconds % 3600) / 60)
        if (days > 0) return days + "d " + hours + "h"
        if (hours > 0) return hours + "h " + mins + "m"
        return mins + "m"
    }
    function fmtDuration(seconds) {
        var total = Math.max(0, Math.round(seconds || 0))
        var hours = Math.floor(total / 3600)
        var mins = Math.floor((total % 3600) / 60)
        var secs = total % 60
        if (hours > 0) return hours + "h " + ("0" + mins).slice(-2) + "m"
        if (mins > 0) return mins + "m " + ("0" + secs).slice(-2) + "s"
        return secs + "s"
    }

    // Keep typical idle and low-bandwidth traffic visible while preventing a zero-range chart.
    function historyPeak(history) {
        var peak = 0
        for (var n = 0; n < history.length; n++) peak = Math.max(peak, history[n] || 0)
        return peak
    }
    function peakAge(history) {
        if (history.length < 1) return 0
        var peak = root.historyPeak(history)
        for (var n = history.length - 1; n >= 0; n--) {
            if ((history[n] || 0) === peak) return history.length - 1 - n
        }
        return 0
    }
    function fmtAge(seconds) {
        if (seconds < 60) return Math.max(0, Math.round(seconds)) + "s"
        return Math.round(seconds / 60) + "m"
    }
    function networkIdle() {
        return root.networkAvailable && root.telemetryNowMs - root.lastNetworkTelemetryMs <= 5000 && root.historyPeak(root.downHistory) < 1 && root.historyPeak(root.upHistory) < 1
    }
    function historyFillProgress() {
        return Math.min(1, root.cpuHistory.length / root.historySeconds)
    }
    function historyFilling() {
        return root.cpuHistory.length < root.historySeconds
    }
    function refreshClock() {
        var now = new Date()
        return ("0" + now.getHours()).slice(-2) + ":" + ("0" + now.getMinutes()).slice(-2)
    }
    function currentTimezoneLabel() {
        // Prefer the OS timezone abbreviation (CEST/CET). Some locales spell the
        // zone out in the local language instead ("Mitteleuropäische Sommerzeit"),
        // so fall back to the numeric UTC offset — never a stale hardcoded label.
        var match = new Date().toString().match(/\(([A-Z]{2,5})\)/)
        if (match) return match[1]
        var offsetMinutes = -new Date().getTimezoneOffset()
        var hours = Math.trunc(Math.abs(offsetMinutes) / 60)
        var minutes = Math.abs(offsetMinutes) % 60
        return "UTC" + (offsetMinutes >= 0 ? "+" : "-") + hours + (minutes ? ":" + ("0" + minutes).slice(-2) : "")
    }
    function serviceToneColor(rawState) {
        var tone = MonitorLogic.serviceTone(MonitorLogic.normalizeServiceState(rawState))
        if (tone === "cyan") return root.cyan
        if (tone === "warning") return root.warning
        if (tone === "critical") return root.critical
        return root.muted
    }
    function serviceBorderColor(rawState) {
        var state = MonitorLogic.normalizeServiceState(rawState)
        if (state === "DEGRADED") return root.warning
        if (state === "OFFLINE") return root.critical
        return root.quietBorder
    }
    function serviceStateLabel(rawState) {
        var state = MonitorLogic.normalizeServiceState(rawState)
        return MonitorLogic.serviceSymbol(state) + " " + state
    }
    // Per-process CPU percentages are shares of one core (top(1) convention), so
    // the card says how many cores the aggregate "CPU" figure refers to. Without
    // it, "4%" next to "chromium 52.3%" reads like a contradiction.
    function cpuProcessHeading() {
        // Three cards per row leave ~136 px for the process column, so the
        // per-core denominator moved to the KPI detail line ("39°C · 20T"), which
        // has room for it and sits directly under the aggregate percentage.
        return "TOP · CPU %"
    }
    function cpuDetailLabel() {
        var temp = isFinite(root.cpuTemp) ? Math.round(root.cpuTemp) + "°C" : "--°C"
        // "20 THREADS" does not fit the narrow KPI column; "20T" does and keeps
        // the thread count that disambiguates the per-process percentages.
        return root.cpuCoreCount > 0 ? temp + " · " + root.cpuCoreCount + "T" : temp
    }
    function openAiOauthState() { return MonitorLogic.openAiOauthState(root.openAiActiveKeys, root.openAiTotalKeys) }
    function openAiOauthLabel() {
        var state = root.openAiOauthState()
        if (state === "NOT_CONFIGURED") return MonitorLogic.serviceSymbol(state) + " " + state + " · 0 KEYS"
        if (root.openAiActiveKeys < 0 || root.openAiTotalKeys < 0) return MonitorLogic.serviceSymbol(state) + " " + state + " · ?/? KEYS"
        return MonitorLogic.serviceSymbol(state) + " " + state + " · " + root.openAiActiveKeys + "/" + root.openAiTotalKeys + " KEYS"
    }
    function openAiOauthTone() { return root.serviceToneColor(root.openAiOauthState()) }
    function openAiOauthBorderColor() { return root.serviceBorderColor(root.openAiOauthState()) }

    // --- Hindsight VERDICHTUNG -------------------------------------------
    function fmtPct1(value) {
        if (value === null || value === undefined || value < 0 || !isFinite(value)) return "--"
        return (Math.round(value * 10) / 10).toFixed(1) + "%"
    }

    // Whole percent: a health indicator does not need a decimal, and every extra
    // glyph costs card width on the narrow side monitor.
    function fmtPct0(value) {
        if (value === null || value === undefined || value < 0 || !isFinite(value)) return "--"
        return Math.round(value) + "%"
    }

    function obsInt(value) {
        if (value === null || value === undefined || value < 0 || !isFinite(value)) return "?"
        return String(Math.round(value))
    }

    // HINDSIGHT VERDICHTUNG: how much of the raw input ends up condensed.
    // "IN OBS" = raw facts referenced by >= 1 observation, MERGE = observations
    // with >= 2 proofs, Ø = mean proofs per observation. Informational only.
    function obsCondenseLabel() {
        if (root.obsHealthUnavailable) return "? OBS"
        var avg = (root.obsAvgProofs < 0 || !isFinite(root.obsAvgProofs)) ? "--" : root.obsAvgProofs.toFixed(2)
        return "IN OBS " + root.fmtPct0(root.obsReferencedPct) + " · MERGE " + root.fmtPct0(root.obsMergePct)
            + " · Ø " + avg
    }

    function obsCondenseDetail() {
        if (root.obsHealthUnavailable) return "API NICHT ERREICHBAR"
        var avg = (root.obsAvgProofs < 0 || !isFinite(root.obsAvgProofs)) ? "--" : root.obsAvgProofs.toFixed(2)
        var referenced = (root.obsRawFacts >= 0 && root.obsOrphans >= 0) ? root.obsRawFacts - root.obsOrphans : -1
        return [
            "IN OBS " + root.fmtPct1(root.obsReferencedPct) + " (" + root.obsInt(referenced) + " VON "
                + root.obsInt(root.obsRawFacts) + " ROHFAKTEN IN MIND. 1 OBSERVATION)",
            "MERGE " + root.fmtPct1(root.obsMergePct) + " (" + root.obsInt(root.obsMerged) + " VON "
                + root.obsInt(root.obsCount) + " OBS MIT PROOF >= 2)",
            "Ø " + avg + " BELEGE PRO OBSERVATION",
            "HINWEIS: BEIM MERGEN VERLIERT DER UNTERLEGENE FAKT SEINE REFERENZ -- IN OBS IST EINE UNTERGRENZE"
        ].join("\n")
    }

    // One reset path for both failure modes (unreachable API, malformed payload):
    // every metric goes back to "unknown" so no stale number survives a failed poll.
    function resetObsHealth() {
        root.obsHealthUnavailable = true
        root.obsCount = 0
        root.obsMergePct = -1
        root.obsMerged = -1
        root.obsOrphans = -1
        root.obsRawFacts = -1
        root.obsReferencedPct = -1
        root.obsAvgProofs = -1
    }

    function gpuPowerText(drawValue, limitValue) {
        var draw = MonitorLogic.sensorNumber(drawValue)
        var limit = Number(limitValue)
        if (!isFinite(limit) || limit <= 0) return ""
        return "POWER " + (isFinite(draw) ? draw.toFixed(0) : "--") + "/" + limit.toFixed(0) + " W"
    }

    // nvidia-smi reports MiB; the visible GB label uses decimal gigabytes.
    function gpuVramUsedText(usedMiB, available) {
        var used = Number(usedMiB)
        if (!available || !isFinite(used) || used < 0) return "VRAM --"
        var gb = used * 1048576 / 1000000000
        return "VRAM " + gb.toFixed(gb > 0 && gb < 0.1 ? 2 : 1) + "GB"
    }
    function vramPercent(usedMiB, totalMiB) {
        if (totalMiB <= 0) return 0
        return 100 * usedMiB / totalMiB
    }
    function gpuProcessRows(entry) {
        var rows = []
        var processes = entry && entry.processes ? entry.processes : []
        for (var i = 0; i < processes.length; i++) {
            rows.push({ name: processes[i].name || "GPU PROCESS", gpu: root.fmtVram(Number(processes[i].used_mib) || 0), gpuExact: String(processes[i].used_mib) + " MiB", role: MonitorLogic.gpuProcessRole(processes[i].name) })
        }
        return rows
    }
    // The card title column is ~150 px wide, which fits "GPU 0 · RTX 3050" (15
    // characters at 13 px monospace) but not the 20-character form of a longer
    // nvidia-smi name. The model name therefore rides along only when it fits;
    // the full nvidia-smi name stays reachable in the card tooltip either way.
    // Never hardcode a model name here: it would keep claiming that card after
    // the hardware changed.
    function cardGpuLabel(index, name) {
        // The card title column is ~105 px, which fits "GPU 0" but not
        // "GPU 0 · RTX 3050": mid-eliding a model name produced output like
        // "GPU 0 ⋯X 3050". The index plus the series colour identifies the card;
        // the full nvidia-smi name is in the card tooltip and in the legend.
        return "GPU " + index
    }
    function cardGpuTooltip(index, name) {
        var text = String(name || "").trim()
        return text ? "GPU " + index + " · " + text : "GPU " + index
    }
    // One scalar property per GPU index, one branch per index. A var array would
    // be shorter, but QML only notifies bindings when the property itself is
    // reassigned, and a computed name like root["gpu" + i + "Usage"] would
    // silently create a shadowing JS property on a typo instead of failing.
    function applyGpuTelemetry(entry, index) {
        var available = !!entry
        if (index === 0) {
            root.gpu0Available = available
            if (!available) { root.topGpu0Processes = []; root.gpu0ProcessCount = 0; return }
            root.gpu0Name = entry.short_name || entry.name || "GPU 0"
            root.gpu0Temp = Number(entry.temperature_c) || 0
            root.gpu0VramUsedMiB = Number(entry.memory_used_mib) || 0
            root.gpu0VramTotalMiB = Number(entry.memory_total_mib) || 0
            root.gpu0PowerDrawWatts = MonitorLogic.sensorNumber(entry.power_draw_w)
            root.gpu0PowerLimitWatts = Number(entry.power_limit_w) || 0
            root.gpu0ProcessCount = Number(entry.process_count) || 0
            root.topGpu0Processes = root.gpuProcessRows(entry)
            return
        }
        if (index === 1) {
            root.gpu1Available = available
            if (!available) { root.topGpu1Processes = []; root.gpu1ProcessCount = 0; return }
            root.gpu1Name = entry.short_name || entry.name || "GPU 1"
            root.gpu1Temp = Number(entry.temperature_c) || 0
            root.gpu1VramUsedMiB = Number(entry.memory_used_mib) || 0
            root.gpu1VramTotalMiB = Number(entry.memory_total_mib) || 0
            root.gpu1PowerDrawWatts = MonitorLogic.sensorNumber(entry.power_draw_w)
            root.gpu1PowerLimitWatts = Number(entry.power_limit_w) || 0
            root.gpu1ProcessCount = Number(entry.process_count) || 0
            root.topGpu1Processes = root.gpuProcessRows(entry)
            return
        }
    }
    function diskUsedBytes() { return root.diskUsedBytesDf }
    function diskPercent() { return root.diskPercentDf }
    function diskReservedBytes() { return Math.max(0, root.diskTotalBytes - root.diskUsedBytesDf - root.diskAvailBytesDf) }
    // df(1) semantics come straight from the helper, so the card can be checked
    // against df by hand. The filesystem reserve (~185 GiB on this root) is not
    // used and not available; it is shown so the total still adds up on screen.
    function fmtDiskSummary() {
        var reserved = root.diskReservedBytes()
        var suffix = reserved > 1024 * 1024 * 1024 ? " (+" + root.fmtDisk(reserved) + " RES)" : ""
        return "USED " + root.fmtDisk(root.diskUsedBytes()) + " · FREE " + root.fmtDisk(root.diskAvailBytesDf) + suffix
    }
    function diskReserveDetail() {
        return "Belegt: " + root.fmtDisk(root.diskUsedBytes()) + "\nVerfügbar: " + root.fmtDisk(root.diskAvailBytesDf)
            + "\nVom Dateisystem reserviert (nicht frei verfügbar): " + root.fmtDisk(root.diskReservedBytes())
    }

    function usageColor(value, normalColor) {
        if (value >= 95) return root.critical
        if (value >= 85) return root.warning
        return normalColor
    }
    function shortProcessName(name) {
        return (name || "PROCESS").split("/").pop()
    }
    function compactProcessValue(metricLabel, value) {
        var text = String(value || "")
        // Narrow cards reserve the process column for names. Hover retains the
        // exact binary unit and precision; G/M are display-only abbreviations.
        if (metricLabel === "RAM" || metricLabel === "VRAM") {
            var number = parseFloat(text)
            if (!isFinite(number)) return text
            if (text.endsWith(" GiB")) return number.toFixed(1) + "G"
            if (text.endsWith(" MiB")) {
                if (metricLabel === "RAM" && number >= 1024) return (number / 1024).toFixed(1) + "G"
                return Math.round(number) + "M"
            }
        }
        return text
    }
    function metricBorderColor(metric) {
        if (metric.healthLevel >= 2) return root.critical
        if (metric.healthLevel >= 1) return root.warning
        return root.quietBorder
    }
    // libksysguard 6.3 emits valueChanged for each delivered sample, even if
    // its numeric value is unchanged. Ready alone survives a daemon disconnect;
    // receipt age detects lost reads. Status transitions are never heartbeats.
    function recordSystemSensor(key, value, ready) {
        var number = ready ? MonitorLogic.sensorNumber(value) : NaN
        if ((key === "cpu" || key === "ram") && number > 100) number = NaN
        if (key === "ramTotalBytes" && number === 0) number = NaN
        var states = Object.assign({}, root.systemSensorStates)
        states[key] = {value: number, available: isFinite(number), stampMs: Date.now()}
        root.systemSensorStates = states
    }
    function systemSensorValue(key) {
        var state = root.systemSensorStates[key]
        if (!state) return NaN
        var sample = MonitorLogic.telemetrySample(state.value, state.available, state.stampMs, Math.max(root.telemetryNowMs, Date.now()), 5000)
        return sample === null ? NaN : sample
    }
    function push(hist, value) {
        return MonitorLogic.appendHistory(hist, value, root.historyLastTickMs, root.telemetryNowMs, root.historySeconds)
    }

    // P3c: Removed dead detectNetIf() — netDetectSource handles detection

    fullRepresentation: Item {
        anchors.fill: parent
        clip: true

        // Use the complete plasmoid area so the dashboard starts directly below
        // the Telegram window and no fixed-height content is clipped.
        Rectangle {
            id: frame
            anchors.fill: parent
            anchors.margins: 8
            radius: 30
            color: "#000000"
            border.width: 1
            border.color: root.quietBorder
            // 15 percentage points less transparent: 29% transparency (71% opacity).
            opacity: 0.71
        }

        Rectangle {
            anchors.centerIn: frame
            width: frame.width - 14
            height: frame.height - 14
            radius: 24
            color: "transparent"
            border.width: 1
            border.color: root.quietBorder
            opacity: 0.55
        }

        // Fit the content into the available height. The charts absorb height
        // changes while cards and labels retain their readable dimensions.
        ColumnLayout {
            id: content
            anchors.fill: frame
            anchors.leftMargin: 34
            anchors.rightMargin: 34
            anchors.topMargin: 20
            anchors.bottomMargin: 20
            spacing: 10

            // --- Header ---
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                Column {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 0
                    Text { text: "SKYBOX"; color: root.cyan; font.family: "DejaVu Sans"; font.bold: true; font.pixelSize: 26; font.letterSpacing: 3 }
                    Text { text: "AIEX · LOCAL · " + root.currentTimezoneLabel(); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                }
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 0
                    Text { id: headerClock; text: root.currentTime; color: root.ink; font.family: "DejaVu Sans"; font.bold: true; font.pixelSize: 45; font.letterSpacing: 2 }
                }
                PlasmaCore.ToolTipArea {
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    width: telemetryLabel.implicitWidth; height: 24
                    mainText: "Älteste erfolgreiche Abfrage von CPU-Prozessen, GPU und Netzwerk. Dienste, übrige Sensoren und Datenträger sind nicht enthalten."
                    Text {
                        id: telemetryLabel
                        anchors.centerIn: parent
                        text: root.telemetryAgeLabel()
                        color: root.telemetryAge() === null || root.telemetryAge() > 15 ? root.warning : root.cyan
                        font.family: "DejaVu Sans Mono"; font.pixelSize: 12; font.bold: true
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.bottomMargin: -8; color: root.cyan; opacity: 0.45 }

            // --- AI SERVICES section ---
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 178
                Layout.minimumHeight: 174
                clip: true
                Column {
                    anchors.fill: parent
                    anchors.topMargin: 4
                    spacing: 8
                    Item {
                        width: parent.width
                        height: 38
                        Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "AI SERVICES"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                        QwenControl {
                            objectName: "qwenControl"
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            ink: root.ink
                            muted: root.muted
                            accent: root.cyan
                            warning: root.warning
                        }
                    }
                    Row {
                        width: parent.width
                        spacing: 10
                        Rectangle {
                            width: (parent.width - 10) / 2
                            height: 44
                            radius: 12
                            color: Qt.rgba(0, 0, 0, 0.22)
                            border.width: 1
                            border.color: root.serviceBorderColor(root.hermesGatewayState)
                            Column {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2
                                Text { text: "GATEWAY"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Text { text: root.serviceStateLabel(root.hermesGatewayState); color: root.serviceToneColor(root.hermesGatewayState); font.family: "DejaVu Sans Mono"; font.pixelSize: 17; font.bold: true }
                            }
                        }
                        Rectangle {
                            width: (parent.width - 10) / 2
                            height: 44
                            radius: 12
                            color: Qt.rgba(0, 0, 0, 0.22)
                            border.width: 1
                            border.color: root.serviceBorderColor(root.hindsightState)
                            Column {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2
                                Text { text: "HINDSIGHT"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Text { text: root.serviceStateLabel(root.hindsightState); color: root.serviceToneColor(root.hindsightState); font.family: "DejaVu Sans Mono"; font.pixelSize: 17; font.bold: true }
                            }
                        }
                    }
                    Rectangle {
                        id: openAiOauthCard
                        width: parent.width
                        height: 28
                        radius: 9
                        color: Qt.rgba(0, 0, 0, 0.22)
                        border.width: 1
                        border.color: root.openAiOauthBorderColor()
                        Text {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: "OPENAI OAUTH"
                            color: root.muted
                            font.family: "DejaVu Sans Mono"
                            font.pixelSize: 14
                            font.bold: true
                        }
                        Text {
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.openAiOauthLabel()
                            color: root.openAiOauthTone()
                            font.family: "DejaVu Sans Mono"
                            font.pixelSize: 14
                            font.bold: true
                            elide: Text.ElideRight
                        }
                    }
                    Rectangle {
                        id: obsCondenseCard
                        width: parent.width
                        height: 28
                        radius: 9
                        color: Qt.rgba(0, 0, 0, 0.22)
                        border.width: 1
                        border.color: root.obsHealthUnavailable ? root.muted : root.cyan
                        PlasmaCore.ToolTipArea {
                            anchors.fill: parent
                            mainText: root.obsCondenseDetail()
                        }
                        Text {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: "HINDSIGHT VERDICHTUNG"
                            color: root.muted
                            font.family: "DejaVu Sans Mono"
                            font.pixelSize: 14
                            font.bold: true
                        }
                        Text {
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.obsCondenseLabel()
                            color: root.obsHealthUnavailable ? root.muted : root.cyan
                            font.family: "DejaVu Sans Mono"
                            font.pixelSize: 13
                            font.bold: true
                            elide: Text.ElideRight
                        }
                    }
                }
            }


            // --- SYSTEM LOAD section ---
            // P1d: All sections use fillHeight + preferredHeight ratio — no fixed heights
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: 190
                Layout.minimumHeight: 160
                clip: true

                Text { id: headline; anchors.left: parent.left; anchors.top: parent.top; text: "SYSTEM LOAD"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                Row {
                    id: computeLegend
                    anchors.left: parent.left; anchors.top: headline.bottom; anchors.topMargin: 26; spacing: 18
                    Text { text: "━━ GPU 0 · " + root.gpu0Name + " · " + (root.gpu0Available && root.gpuLoadAvailable(0) ? Math.round(root.gpu0Usage) + "%" : "UNAVAILABLE"); color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "━━ GPU 1 · " + root.gpu1Name + " · " + (root.gpu1Available && root.gpuLoadAvailable(1) ? Math.round(root.gpu1Usage) + "%" : "UNAVAILABLE"); color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                }

                // P0a: Y-axis labels positioned INSIDE the graph area, not with negative margins
                // P1: Minimum 12px for axis labels
                Canvas {
                    id: computeGraph
                    anchors.left: parent.left; anchors.leftMargin: 44
                    anchors.right: parent.right
                    anchors.top: computeLegend.bottom; anchors.topMargin: 10
                    anchors.bottom: computeTimeline.top
                    anchors.bottomMargin: 10

                    // P0a: Draw axis labels inside onPaint so they're always visible
                    onPaint: {
                        var ctx = getContext("2d"); ctx.reset()
                        // P3: Enable smoothing
                        ctx.imageSmoothingEnabled = true

                        // Reserve a left gutter for labels; Canvas clips negative x coordinates.
                        var plotLeft = 40
                        var chartWidth = width - plotLeft
                        // Keep 0%/100% traces inside the canvas; otherwise a zero-load
                        // GPU is clipped into the lower border and appears missing.
                        var plotTop = 2
                        var plotHeight = Math.max(1, height - 4)
                        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(160,200,216,0.25)"
                        for (var i = 0; i < 5; i++) { var y = height * i / 4; ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(width, y); ctx.stroke() }
                        ctx.strokeStyle = "rgba(160,200,216,0.15)"; ctx.lineWidth = 1
                        var midTick = plotLeft + chartWidth / 2
                        ctx.beginPath(); ctx.moveTo(midTick, 0); ctx.lineTo(midTick, height); ctx.stroke()


                        // Labels live inside the Canvas gutter so they are never clipped.
                        ctx.fillStyle = root.muted.toString()
                        ctx.font = "12px 'DejaVu Sans Mono'"
                        ctx.textAlign = "right"
                        ctx.fillText("100%", plotLeft - 6, 10)
                        ctx.fillText("75%", plotLeft - 6, height / 4 + 4)
                        ctx.fillText("50%", plotLeft - 6, height / 2 + 4)
                        ctx.fillText("25%", plotLeft - 6, height * 3 / 4 + 4)
                        ctx.fillText("0%", plotLeft - 6, height - 2)

                        function plot(data, color, fillColor, dashed) {
                            if (data.length < 2) return
                            var segments = MonitorLogic.historySegments(data)
                            for (var segmentIndex = 0; segmentIndex < segments.length; segmentIndex++) {
                                var segment = segments[segmentIndex]
                                var firstX = MonitorLogic.historyX(segment.start, data.length, chartWidth, root.historySeconds) + plotLeft
                                var lastX = MonitorLogic.historyX(segment.end, data.length, chartWidth, root.historySeconds) + plotLeft
                                ctx.beginPath()
                                for (var f = segment.start; f <= segment.end; f++) {
                                    var fx = plotLeft + MonitorLogic.historyX(f, data.length, chartWidth, root.historySeconds)
                                    var fy = height - plotTop - (root.clamp(data[f]) / 100) * plotHeight
                                    if (f === segment.start) ctx.moveTo(fx, fy); else ctx.lineTo(fx, fy)
                                }
                                ctx.lineTo(lastX, height)
                                ctx.lineTo(firstX, height)
                                ctx.closePath()
                                ctx.fillStyle = fillColor
                                ctx.fill()
                                ctx.strokeStyle = color; ctx.lineWidth = 3
                                ctx.setLineDash(dashed ? [8, 5] : [])
                                ctx.beginPath()
                                for (var j = segment.start; j <= segment.end; j++) {
                                    var x = plotLeft + MonitorLogic.historyX(j, data.length, chartWidth, root.historySeconds)
                                    var y = height - plotTop - (root.clamp(data[j]) / 100) * plotHeight
                                    if (j === segment.start) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                }
                                ctx.stroke()
                                ctx.setLineDash([])

                            }

                            // Emphasize the newest sample so the history-to-card
                            // transition is immediately readable at the NOW edge.
                            var currentIndex = data.length - 1
                            if (data[currentIndex] === null || data[currentIndex] === undefined) return
                            var currentX = plotLeft + MonitorLogic.historyX(currentIndex, data.length, chartWidth, root.historySeconds)
                            var currentY = height - plotTop - (root.clamp(data[currentIndex]) / 100) * plotHeight
                            ctx.fillStyle = color
                            ctx.beginPath()
                            ctx.arc(currentX, currentY, 5, 0, Math.PI * 2)
                            ctx.fill()
                        }
                        plot(root.gpu0History, root.violet, "rgba(219,145,255,0.10)", false)
                        plot(root.gpu1History, root.cyan, "rgba(150,245,246,0.07)", false)
                    }
                }
                Item {
                    id: computeTimeline
                    anchors.left: computeGraph.left
                    anchors.right: computeGraph.right
                    anchors.bottom: parent.bottom
                    height: 20
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−2 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "−1 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14 }
                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "NOW · " + Math.round(root.historyFillProgress() * 100) + "% FILLED" : "NOW"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14 }
                }
            }

            HermesLive {
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                Layout.minimumHeight: implicitHeight
                ink: root.ink
                muted: root.muted
                accent: root.violet
                warning: root.warning
                critical: root.critical
            }

            // --- Two-GPU row followed by two full-width CPU/RAM cards ---
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 342
                readonly property int cardSpacing: 16
                readonly property int gpuColumns: 2
                readonly property int lowerColumns: 2
                readonly property real gpuCardWidth: (width - (gpuColumns - 1) * cardSpacing) / gpuColumns
                readonly property real lowerCardWidth: (width - (lowerColumns - 1) * cardSpacing) / lowerColumns
                readonly property real cardHeight: (height - cardSpacing) / 2

                Repeater {
                    // Stable delegates keep their hover timers across telemetry changes.
                    model: 4
                    delegate: Rectangle {
                        readonly property var cardData: [
                            {kind:"gpu", label:"GPU 0 · " + root.gpu0Name, gpuName:root.gpu0Name, gpuIndex:0, labelText: root.cardGpuLabel(0, root.gpu0Name), labelTooltip: root.cardGpuTooltip(0, root.gpu0Name), available:root.gpu0Available, value:root.gpu0Available && root.gpuLoadAvailable(0) ? Math.round(root.gpu0Usage) + "%" : "--", detail:root.gpu0Available ? Math.round(root.gpu0Temp) + "°C" : "UNAVAILABLE", color:root.violet, detailColor:root.gpuTempColor(root.gpu0Temp, root.muted), healthLevel:!root.gpu0Available ? 2 : ((root.gpu0Temp >= 90 || root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB) >= 95) ? 2 : ((root.gpu0Temp >= 85 || root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB) >= 85) ? 1 : 0)), vramFill:root.gpu0Available ? root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB) : 0, vramUsedMiB:root.gpu0VramUsedMiB, vramTotalMiB:root.gpu0VramTotalMiB, powerText:root.gpu0Available ? root.gpuPowerText(root.gpu0PowerDrawWatts, root.gpu0PowerLimitWatts) : "", processes:root.topGpu0Processes, processCount:root.gpu0ProcessCount, processUnavailable:!root.gpu0Available || root.gpuProcessUnavailable},
                            {kind:"gpu", label:"GPU 1 · " + root.gpu1Name, gpuName:root.gpu1Name, gpuIndex:1, labelText: root.cardGpuLabel(1, root.gpu1Name), labelTooltip: root.cardGpuTooltip(1, root.gpu1Name), available:root.gpu1Available, value:root.gpu1Available && root.gpuLoadAvailable(1) ? Math.round(root.gpu1Usage) + "%" : "--", detail:root.gpu1Available ? Math.round(root.gpu1Temp) + "°C" : "UNAVAILABLE", color:root.cyan, detailColor:root.gpuTempColor(root.gpu1Temp, root.muted), healthLevel:!root.gpu1Available ? 2 : ((root.gpu1Temp >= 90 || root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB) >= 95) ? 2 : ((root.gpu1Temp >= 85 || root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB) >= 85) ? 1 : 0)), vramFill:root.gpu1Available ? root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB) : 0, vramUsedMiB:root.gpu1VramUsedMiB, vramTotalMiB:root.gpu1VramTotalMiB, powerText:root.gpu1Available ? root.gpuPowerText(root.gpu1PowerDrawWatts, root.gpu1PowerLimitWatts) : "", processes:root.topGpu1Processes, processCount:root.gpu1ProcessCount, processUnavailable:!root.gpu1Available || root.gpuProcessUnavailable},
                            {kind:"cpu", label:"CPU", value:isFinite(root.cpu) ? Math.round(root.cpu) + "%" : "--", detail:root.cpuDetailLabel(), color:root.blue, detailColor:root.tempColor(root.cpuTemp, root.muted), healthLevel:root.cpuTemp >= 85 ? 2 : (root.cpuTemp >= 75 ? 1 : 0), processes:root.topCpuProcesses, processCount:root.topCpuProcesses.length, processUnavailable:root.cpuProcessUnavailable},
                            {kind:"ram", label:"RAM", value:isFinite(root.ram) ? Math.round(root.ram) + "%" : "--", detail:root.fmtMemoryPair(root.ramUsedBytes, root.ramTotalBytes), color:root.orange, detailColor:root.ram >= 85 ? root.warning : root.muted, healthLevel:root.ram >= 95 ? 2 : (root.ram >= 85 ? 1 : 0), processes:root.topRamProcesses, processCount:root.topRamProcesses.length, processUnavailable:root.ramProcessUnavailable}
                        ][index]
                        x: index < parent.gpuColumns ? index * (parent.gpuCardWidth + parent.cardSpacing) : (index - parent.gpuColumns) * (parent.lowerCardWidth + parent.cardSpacing)
                        y: index < parent.gpuColumns ? 0 : parent.cardHeight + parent.cardSpacing
                        width: index < parent.gpuColumns ? parent.gpuCardWidth : parent.lowerCardWidth
                        height: parent.cardHeight; radius: 16
                        clip: true
                        color: Qt.rgba(0.035, 0.22, 0.34, 0.82); border.width: cardData.healthLevel > 0 ? 2 : 1; border.color: root.metricBorderColor(cardData); opacity: 0.95

                        // A fixed KPI column prevents large percentages from colliding
                        // with process names at narrow dashboard widths.
                        Item {
                            anchors.fill: parent; anchors.margins: 12
                            // The KPI column and its gap scale with the card width;
                            // fixed literals clipped the process value column away
                            // and let POWER elide on narrow cards.
                            readonly property int kpiColumn: Math.max(60, Math.round(parent.width * 0.35))
                            readonly property int kpiGap: Math.max(8, Math.round(parent.width * 0.045))
                            Column {
                                id: metricKpi
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: parent.kpiColumn; spacing: 4
                                PlasmaCore.ToolTipArea {
                                    width: parent.width - 4
                                    height: 18
                                    mainText: cardData.labelTooltip || cardData.label
                                    Text { anchors.fill: parent; text: cardData.labelText || cardData.label; color: cardData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideMiddle }
                                }

                                // --- GPU card: utilization as the large metric + temperature secondary ---
                                Text {
                                    visible: cardData.kind === "gpu"
                                    text: cardData.value
                                    color: root.ink
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: Math.max(18, Math.min(26, Math.round(metricKpi.width * 0.42)))
                                    font.bold: true
                                }
                                Text {
                                    visible: cardData.kind === "gpu"
                                    text: cardData.detail
                                    color: cardData.detailColor
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                // VRAM value stays on the card background; the bar conveys capacity.
                                Item {
                                    width: parent.width - 4
                                    height: 26
                                    visible: cardData.kind === "gpu"
                                    PlasmaCore.ToolTipArea {
                                        anchors.fill: parent
                                        mainText: cardData.available ? "VRAM " + root.fmtVram(cardData.vramUsedMiB || 0) + " / " + root.fmtVram(cardData.vramTotalMiB || 0)
                                            + "\n" + cardData.vramUsedMiB + " MiB / " + cardData.vramTotalMiB + " MiB · " + Math.round(cardData.vramFill || 0) + "% · 1 GB = 10⁹ Bytes" : "VRAM UNAVAILABLE"
                                    }
                                    Rectangle {
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                        height: 6
                                        radius: 3
                                        color: Qt.rgba(1, 1, 1, 0.08)
                                    }
                                    Rectangle {
                                        anchors.left: parent.left; anchors.bottom: parent.bottom
                                        height: 6
                                        width: parent.width * Math.max(0, Math.min(1, (cardData.vramFill || 0) / 100))
                                        radius: 3
                                        color: (cardData.vramFill || 0) >= 95 ? root.critical : ((cardData.vramFill || 0) >= 85 ? root.warning : root.cyan)
                                    }
                                    Text {
                                        anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter
                                        text: root.gpuVramUsedText(cardData.vramUsedMiB, cardData.available)
                                        color: root.ink
                                        font.family: "DejaVu Sans Mono"
                                        font.pixelSize: 13
                                        font.bold: true
                                    }
                                }
                                PlasmaCore.ToolTipArea {
                                    visible: cardData.kind === "gpu" && !!cardData.powerText
                                    width: parent.width - 4
                                    height: 17
                                    mainText: cardData.powerText || ""
                                    Text {
                                        anchors.fill: parent
                                        text: (cardData.powerText || "").replace("POWER ", "")
                                        color: root.muted
                                        font.family: "DejaVu Sans Mono"
                                        font.pixelSize: 13
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }
                                }

                                // --- CPU card: utilization as the large metric + temperature secondary ---
                                Text {
                                    visible: cardData.kind === "cpu"
                                    text: cardData.value
                                    color: root.ink
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: 28
                                    font.bold: true
                                }
                                Text {
                                    visible: cardData.kind === "cpu"
                                    text: cardData.detail
                                    color: cardData.detailColor
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                // --- RAM card: original layout ---
                                Text { visible: cardData.kind === "ram"; text: cardData.value; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 28; font.bold: true }
                                PlasmaCore.ToolTipArea {
                                    visible: cardData.kind === "ram"
                                    width: parent.width - 4
                                    height: 18
                                    mainText: root.fmtMemoryPairExact(root.ramUsedBytes, root.ramTotalBytes)
                                    Text { anchors.fill: parent; text: cardData.detail; color: cardData.detailColor; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                }
                                // VRAM fill bar — only on GPU card (original position, kept for non-GPU safety)
                                Item {
                                    width: parent.width - 4
                                    height: 8
                                    visible: cardData.kind !== "gpu" && cardData.vramFill !== undefined
                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 4
                                        color: Qt.rgba(1, 1, 1, 0.08)
                                    }
                                    Rectangle {
                                        height: parent.height
                                        width: parent.width * Math.min(1, (cardData.vramFill || 0) / 100)
                                        radius: 4
                                        color: (cardData.vramFill || 0) >= 85 ? root.critical : root.cyan
                                    }
                                }
                            }
                            Rectangle { id: metricDivider; anchors.left: metricKpi.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 1; color: cardData.color; opacity: 0.24 }
                            Column {
                                id: processDetails
                                anchors.left: metricDivider.right; anchors.leftMargin: parent.kpiGap
                                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                                spacing: 3
                                property string metricKind: cardData.kind
                                property var processes: cardData.processes || []
                                property string heading: metricKind === "cpu" ? root.cpuProcessHeading() : (metricKind === "gpu" ? cardData.gpuName : "TOP · RAM")
                                // A fifth GPU workload is capped, so say how many exist.
                                property string headingSuffix: metricKind === "gpu" && cardData.processCount > processDetails.processes.length ? " (+" + (cardData.processCount - processDetails.processes.length) + ")" : ""
                                PlasmaCore.ToolTipArea {
                                    width: parent.width; height: 18
                                    mainText: processDetails.metricKind === "cpu"
                                        ? "Pro Prozess: Anteil eines CPU-Threads. CPU-Gesamtwert: Durchschnitt über " + root.cpuCoreCount + " Threads."
                                        : processDetails.metricKind === "gpu" ? cardData.labelTooltip + " · TOP · VRAM\nVideospeicher pro Prozess (M = MiB, G = GiB)" : "Arbeitsspeicher pro Prozess (M = MiB, G = GiB)"
                                    Text { anchors.fill: parent; text: processDetails.heading + processDetails.headingSuffix; color: cardData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideRight }
                                }
                                PlasmaCore.ToolTipArea {
                                    visible: processDetails.metricKind === "gpu"
                                    width: parent.width; height: visible ? 17 : 0
                                    mainText: "Höchste gemessene GPU-Auslastung der letzten 30 Sekunden · Messung alle 500 ms.\nGesamte GPU, nicht nur Hindsight; -- bei fehlenden/alten Messwerten.\nEMBED / RERANK bezeichnet die Hindsight-Rolle, nicht momentane Rechenaktivität."
                                    Text {
                                        anchors.fill: parent
                                        text: root.gpuPeakLabel(cardData.gpuIndex)
                                        color: cardData.color
                                        font.family: "DejaVu Sans Mono"; font.pixelSize: 12
                                        font.bold: true; elide: Text.ElideRight
                                    }
                                }
                                Repeater {
                                    model: parent.processes
                                    delegate: Item {
                                        width: parent.width
                                        height: process.role ? 32 : 18
                                        property var process: modelData
                                        property string fullValue: processDetails.metricKind === "cpu" ? process.cpu + "%" : (processDetails.metricKind === "gpu" ? process.gpuExact : (processDetails.metricKind === "ram" ? process.ramExact : ""))
                                        property string displayValue: root.compactProcessValue(processDetails.metricKind === "gpu" ? "VRAM" : processDetails.metricKind.toUpperCase(), processDetails.metricKind === "gpu" ? process.gpu : (processDetails.metricKind === "ram" ? process.ram : fullValue))
                                        PlasmaCore.ToolTipArea {
                                            anchors.left: parent.left
                                            anchors.right: processValue.left
                                            anchors.rightMargin: 6
                                            height: 18
                                            mainText: parent.process.name
                                            Text {
                                                anchors.fill: parent
                                                text: root.shortProcessName(parent.mainText)
                                                color: index === 0 ? root.ink : root.muted
                                                font.family: "DejaVu Sans Mono"; font.pixelSize: 13
                                                elide: Text.ElideRight
                                            }
                                        }
                                        PlasmaCore.ToolTipArea {
                                            id: processValue
                                            anchors.right: parent.right
                                            width: valueText.implicitWidth
                                            height: 18
                                            mainText: parent.fullValue
                                            Text {
                                                id: valueText
                                                anchors.right: parent.right
                                                text: processValue.parent.displayValue
                                                color: index === 0 ? root.ink : root.muted
                                                font.family: "DejaVu Sans Mono"; font.pixelSize: 13
                                                font.bold: true
                                                horizontalAlignment: Text.AlignRight
                                            }
                                        }
                                        Text {
                                            anchors.left: parent.left; anchors.right: parent.right
                                            anchors.top: parent.top; anchors.topMargin: 18
                                            height: 12; visible: !!parent.process.role
                                            text: parent.process.role || ""
                                            color: root.cyan
                                            font.family: "DejaVu Sans Mono"; font.pixelSize: 12
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                                Text {
                                    visible: parent.processes.length === 0
                                    width: parent.width
                                    text: cardData.processUnavailable ? "UNAVAILABLE" : (cardData.processCount === 0 ? (processDetails.metricKind === "gpu" ? "NO ACTIVE WORKLOAD" : "SAMPLING…") : "SAMPLING…")
                                    color: root.muted
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }

            // --- NETWORK section with split sub-charts ---
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                // About 25% taller than the previous 230 px network panel.
                Layout.preferredHeight: 288
                Layout.minimumHeight: 230
                clip: true

                Text { id: networkTitle; anchors.left: parent.left; anchors.top: parent.top; text: "NETWORK"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                Text { anchors.right: parent.right; anchors.verticalCenter: networkTitle.verticalCenter; text: "SKALEN UNABHÄNGIG"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12 }
                Column {
                    id: networkDataBlock
                    anchors.right: parent.right; anchors.top: networkTitle.bottom; anchors.topMargin: 22; spacing: 4
                    Row {
                        id: networkLiveValues
                        anchors.right: parent.right; spacing: 16
                        Text { text: "↓ " + root.networkLiveLabel("down"); color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
                        Text { text: "↑ " + root.networkLiveLabel("upload"); color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
                    }
                }

                Row {
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: networkDataBlock.bottom; anchors.topMargin: 10
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 24
                    spacing: 12

                    // Download sub-chart (P3: filled-area style for better visibility)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "DOWNLOAD · " + root.networkAxisUnit("down"); color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                        Canvas {
                            id: downGraph
                            anchors.fill: parent
                            anchors.topMargin: 16
                            anchors.bottomMargin: 24
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                ctx.imageSmoothingEnabled = true
                                var plotLeft = 44
                                var chartWidth = width - plotLeft
                                var maxMbit = root.downloadScaleBytesPerSecond * 8 / 1000000
                                var gridStepMbit = root.downloadGridStepMbit
                                var axisFactor = root.networkAxisFactor("down")
                                var gridDivisions = Math.max(1, Math.round(maxMbit / gridStepMbit))
                                ctx.strokeStyle = "rgba(160,200,216,0.22)"; ctx.lineWidth = 1
                                ctx.fillStyle = root.muted.toString()
                                ctx.font = "11px 'DejaVu Sans Mono'"
                                ctx.textAlign = "right"
                                for (var i = 0; i <= gridDivisions; i++) {
                                    var y = height * i / gridDivisions
                                    ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(width, y); ctx.stroke()
                                    ctx.fillText(root.fmtNetworkAxisValue((maxMbit - i * gridStepMbit) * axisFactor), 40 - 6, Math.max(10, Math.min(height - 2, y + 4)))
                                }
                                ctx.strokeStyle = "rgba(160,200,216,0.12)"; ctx.lineWidth = 1
                                var midTick = plotLeft + chartWidth / 2
                                ctx.beginPath(); ctx.moveTo(midTick, 0); ctx.lineTo(midTick, height); ctx.stroke()
                                var d = root.downHistory
                                if (d.length < 2) return
                                var segments = MonitorLogic.historySegments(d)
                                for (var segmentIndex = 0; segmentIndex < segments.length; segmentIndex++) {
                                    var segment = segments[segmentIndex]
                                    var firstX = plotLeft + MonitorLogic.historyX(segment.start, d.length, chartWidth, root.historySeconds)
                                    var lastX = plotLeft + MonitorLogic.historyX(segment.end, d.length, chartWidth, root.historySeconds)
                                    // P3: Filled area under the line
                                    ctx.beginPath()
                                    for (var j = segment.start; j <= segment.end; j++) {
                                        var x = plotLeft + MonitorLogic.historyX(j, d.length, chartWidth, root.historySeconds)
                                        var y = height - Math.min(1, d[j] / root.downloadScaleBytesPerSecond) * height
                                        if (j === segment.start) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                    }
                                    ctx.lineTo(lastX, height)
                                    ctx.lineTo(firstX, height)
                                    ctx.closePath()
                                    ctx.fillStyle = "rgba(150,245,246,0.15)"
                                    ctx.fill()
                                    // Line on top
                                    ctx.strokeStyle = root.cyan; ctx.lineWidth = 2.5; ctx.beginPath()
                                    for (var k = segment.start; k <= segment.end; k++) {
                                        var x2 = plotLeft + MonitorLogic.historyX(k, d.length, chartWidth, root.historySeconds)
                                        var y2 = height - Math.min(1, d[k] / root.downloadScaleBytesPerSecond) * height
                                        if (k === segment.start) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2)
                                    }
                                    ctx.stroke()
                                }
                            }
                        }
                        Item {
                            id: downloadTimeline
                            anchors.left: parent.left
                            anchors.leftMargin: 44
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 20
                            Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−2 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "−1 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "NOW · " + Math.round(root.historyFillProgress() * 100) + "% FILLED" : "NOW"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                        }
                    }

                    // Upload sub-chart (P3: filled-area style)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "UPLOAD · " + root.networkAxisUnit("upload"); color: root.violet; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                        Canvas {
                            id: upGraph
                            anchors.fill: parent
                            anchors.topMargin: 16
                            anchors.bottomMargin: 24
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                ctx.imageSmoothingEnabled = true
                                var plotLeft = 44
                                var chartWidth = width - plotLeft
                                var maxMbit = root.uploadScaleBytesPerSecond * 8 / 1000000
                                var gridStepMbit = root.uploadGridStepMbit
                                var axisFactor = root.networkAxisFactor("upload")
                                var gridDivisions = Math.max(1, Math.round(maxMbit / gridStepMbit))
                                ctx.strokeStyle = "rgba(160,200,216,0.22)"; ctx.lineWidth = 1
                                ctx.fillStyle = root.muted.toString()
                                ctx.font = "11px 'DejaVu Sans Mono'"
                                ctx.textAlign = "right"
                                for (var i = 0; i <= gridDivisions; i++) {
                                    var y = height * i / gridDivisions
                                    ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(width, y); ctx.stroke()
                                    ctx.fillText(root.fmtNetworkAxisValue((maxMbit - i * gridStepMbit) * axisFactor), 40 - 6, Math.max(10, Math.min(height - 2, y + 4)))
                                }
                                ctx.strokeStyle = "rgba(160,200,216,0.12)"; ctx.lineWidth = 1
                                var midTick = plotLeft + chartWidth / 2
                                ctx.beginPath(); ctx.moveTo(midTick, 0); ctx.lineTo(midTick, height); ctx.stroke()
                                var d = root.upHistory
                                if (d.length < 2) return
                                var segments = MonitorLogic.historySegments(d)
                                for (var segmentIndex = 0; segmentIndex < segments.length; segmentIndex++) {
                                    var segment = segments[segmentIndex]
                                    var firstX = plotLeft + MonitorLogic.historyX(segment.start, d.length, chartWidth, root.historySeconds)
                                    var lastX = plotLeft + MonitorLogic.historyX(segment.end, d.length, chartWidth, root.historySeconds)
                                    // P3: Filled area
                                    ctx.beginPath()
                                    for (var j = segment.start; j <= segment.end; j++) {
                                        var x = plotLeft + MonitorLogic.historyX(j, d.length, chartWidth, root.historySeconds)
                                        var y = height - Math.min(1, d[j] / root.uploadScaleBytesPerSecond) * height
                                        if (j === segment.start) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                    }
                                    ctx.lineTo(lastX, height)
                                    ctx.lineTo(firstX, height)
                                    ctx.closePath()
                                    ctx.fillStyle = "rgba(219,145,255,0.15)"
                                    ctx.fill()
                                    // Line on top
                                    ctx.strokeStyle = root.violet; ctx.lineWidth = 2.5; ctx.beginPath()
                                    for (var k = segment.start; k <= segment.end; k++) {
                                        var x2 = plotLeft + MonitorLogic.historyX(k, d.length, chartWidth, root.historySeconds)
                                        var y2 = height - Math.min(1, d[k] / root.uploadScaleBytesPerSecond) * height
                                        if (k === segment.start) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2)
                                    }
                                    ctx.stroke()
                                }
                            }
                        }
                        Item {
                            id: uploadTimeline
                            anchors.left: parent.left
                            anchors.leftMargin: 44
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 20
                            Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−2 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "−1 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "NOW · " + Math.round(root.historyFillProgress() * 100) + "% FILLED" : "NOW"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                        }
                    }
                }
                Text {
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: 14
                    visible: root.networkIdle()
                    text: "IDLE · NO TRANSFER IN LAST 2 MIN"
                    color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true
                }
            }

            // --- DISK + SYSTEM footer cards ---
            Row {
                Layout.fillWidth: true
                Layout.preferredHeight: 94
                spacing: 16
                Rectangle {
                    width: (parent.width - 16) / 2; height: parent.height; radius: 12
                    clip: true
                    color: Qt.rgba(0.035, 0.22, 0.34, 0.82); border.width: 1; border.color: root.quietBorder; opacity: 0.95
                    Column {
                        anchors.fill: parent; anchors.margins: 10; spacing: 2
                        Item {
                            width: parent.width
                            height: 26
                            Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "SYSTEM DISK /"; color: root.blue; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: Math.round(root.diskPercent()) + "%"; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 20; font.bold: true }
                        }
                        // df semantics plus the ext4 reserve, elided if the card is narrow.
                        PlasmaCore.ToolTipArea {
                            width: parent.width; height: 16
                            mainText: root.diskReserveDetail()
                            Text { anchors.fill: parent; elide: Text.ElideRight; text: root.fmtDiskSummary(); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12 }
                        }
                        Rectangle {
                            width: parent.width; height: 6; radius: 3; color: Qt.rgba(0.5, 0.5, 0.5, 0.3)
                            Rectangle {
                                width: parent.width * Math.min(1, root.diskPercent() / 100); height: parent.height; radius: 3; color: root.blue
                            }
                        }
                    }
                }
                Rectangle {
                    width: (parent.width - 16) / 2; height: parent.height; radius: 12
                    clip: true
                    color: Qt.rgba(0.035, 0.22, 0.34, 0.82); border.width: 1; border.color: root.quietBorder; opacity: 0.95
                    Column {
                        anchors.fill: parent; anchors.margins: 10; spacing: 4
                        Text { text: "SYSTEM /"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                        Grid {
                            id: systemMetaGrid
                            width: parent.width
                            columns: 2
                            rows: 2
                            columnSpacing: 12
                            rowSpacing: 3
                            Item {
                                width: (parent.width - 12) / 2; height: 24
                                Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "UPTIME"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                                Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.fmtUptime(root.uptimeSeconds); color: root.ink; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                            }
                            Item {
                                width: (parent.width - 12) / 2; height: 24
                                Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "LOAD 1M"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                                Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.loadAverage.toFixed(2); color: root.ink; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                            }
                            Item {
                                width: (parent.width - 12) / 2; height: 24
                                Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "PROCESSES"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                                Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.processCount; color: root.ink; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                            }
                            Item {
                                width: (parent.width - 12) / 2; height: 24
                                PlasmaCore.ToolTipArea {
                                    anchors.left: parent.left
                                    anchors.right: thinkDuration.left
                                    anchors.rightMargin: 8
                                    height: parent.height
                                    property string sessionLabel: "ANTW. MAX24H"
                                    property string profileHint: root.hermesMaxThinkService.length > 0 ? " · Profil: " + root.hermesMaxThinkService.toLowerCase() : ""
                                    mainText: "Längste abgeschlossene Hermes-Antwort der letzten 24 h" + profileHint
                                        + (root.hermesMaxThinkUnavailable ? "\nAntwortzeiten nicht lesbar."
                                            : root.hermesMaxThinkPartial ? "\nTeilabfrage: Mindestens diese Dauer; einzelne Profile sind nicht lesbar." : "")
                                    Text { anchors.fill: parent; verticalAlignment: Text.AlignVCenter; text: parent.sessionLabel; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                }
                                Text { id: thinkDuration; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.hermesMaxThinkUnavailable ? "--" : (root.hermesMaxThinkPartial ? "≥ " : "") + root.fmtDuration(root.hermesMaxThinkSeconds); color: root.hermesMaxThinkUnavailable || root.hermesMaxThinkPartial ? root.warning : root.ink; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
                            }
                        }
                    }
                }
            }
        }

        Timer {
            interval: 1000
            running: true
            repeat: true
            onTriggered: {
                root.currentTime = root.refreshClock()
                root.telemetryNowMs = Date.now()
                if (root.lastServiceTelemetryMs > 0 && root.telemetryNowMs - root.lastServiceTelemetryMs > 60000) root.resetServices()
                root.cpuHistory = root.push(root.cpuHistory, isFinite(root.cpu) ? root.cpu : null)
                root.gpu0History = root.push(root.gpu0History, MonitorLogic.gpuLoadSample(root.gpuLoadStates["0"], root.telemetryNowMs))
                root.gpu1History = root.push(root.gpu1History, MonitorLogic.gpuLoadSample(root.gpuLoadStates["1"], root.telemetryNowMs))
                root.ramHistory = root.push(root.ramHistory, isFinite(root.ram) ? root.ram : null)
                root.downHistory = root.push(root.downHistory, MonitorLogic.telemetrySample(root.down, root.networkAvailable, root.lastNetworkTelemetryMs, root.telemetryNowMs, 5000))
                root.upHistory = root.push(root.upHistory, MonitorLogic.telemetrySample(root.up, root.networkAvailable, root.lastNetworkTelemetryMs, root.telemetryNowMs, 5000))
                root.historyLastTickMs = root.telemetryNowMs
                computeGraph.requestPaint()
                downGraph.requestPaint()
                upGraph.requestPaint()
            }
        }
    }

    // df(1) view of the local root. The Plasma sensors cannot express it (see the
    // property block above), and a statvfs read is cheap and side-effect free.
    PlasmaSupport.DataSource {
        id: diskUsageSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/disk_usage.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + " /"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var output = buffer.trim()
            var exitCode = data["exit code"]
            buffer = ""
            disconnectSource(source)
            if (exitCode !== 0) return
            var fields = output.split(/\s+/)
            if (fields.length !== 4) return
            var used = parseFloat(fields[0])
            var avail = parseFloat(fields[1])
            var total = parseFloat(fields[2])
            var percent = parseFloat(fields[3])
            if (isNaN(used) || isNaN(avail) || isNaN(total) || isNaN(percent)) return
            root.diskUsedBytesDf = used
            root.diskAvailBytesDf = avail
            root.diskTotalBytes = total
            root.diskPercentDf = percent
        }
    }

    PlasmaSupport.DataSource {
        id: processCountSource
        engine: "executable"
        connectedSources: []
        // Count /proc PIDs with shell builtins only. The executable engine runs
        // this via /bin/sh -c, so that one shell is the only process the
        // measurement adds (ps | wc used to inflate the count by three).
        property string command: "set -- /proc/[0-9]*; echo $(($# - 1))"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var count = parseInt(buffer.trim())
            buffer = ""
            disconnectSource(source)
            if (!isNaN(count)) root.processCount = count
        }
    }

    // Local SQLite read only: this metric performs no model/API call and uses no tokens.
    PlasmaSupport.DataSource {
        id: hermesThinkSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/hermes_max_think.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var payload = null
            try { payload = JSON.parse(buffer.trim()) } catch (error) { payload = null }
            buffer = ""
            disconnectSource(source)
            var valid = Number(data["exit code"]) === 0 && payload && !payload.error
                && typeof payload.seconds === "number" && isFinite(payload.seconds) && payload.seconds >= 0
            root.hermesMaxThinkUnavailable = !valid
            root.hermesMaxThinkPartial = !!(valid && payload.partial === true)
            if (!valid) {
                root.hermesMaxThinkSeconds = 0
                root.hermesMaxThinkService = ""
                return
            }
            root.hermesMaxThinkSeconds = payload.seconds
            root.hermesMaxThinkService = String(payload.service || "").toUpperCase()
        }
    }

    // Hindsight observation health: reports the rebuild-proof quality signals
    // (MERGE, BACKLOG, LAG) plus the scope structure that actually explains them --
    // tag drift among the scopes and the share of observations sitting in the single
    // untagged catch-all pool. The consolidation throughput and the sample audit of
    // the unreferenced facts replace the old frozen pre-reset reference. The all-time
    // TAGS-0/PROOF-1 percentages stay in the tooltip as context only.
    PlasmaSupport.DataSource {
        id: obsHealthSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/hindsight_observation_health.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var payload = null
            try { payload = JSON.parse(buffer.trim()) } catch (error) { payload = null }
            buffer = ""
            disconnectSource(source)
            // Explicit drift signal: an unreachable API or malformed payload
            // resets the card instead of leaving stale numbers on screen.
            if (!payload || payload.error) {
                root.resetObsHealth()
                return
            }
            var tagless = Number(payload.tagless_pct)
            var single = Number(payload.single_proof_pct)
            if (isNaN(tagless) || isNaN(single)) {
                root.resetObsHealth()
                return
            }
            root.obsHealthUnavailable = false
            root.obsCount = Number(payload.observations) || 0
            // Null means "field absent", which must not read as 0 on the card.
            function numOr(value, fallback) {
                if (value === null || value === undefined) return fallback
                var parsed = Number(value)
                return isNaN(parsed) ? fallback : parsed
            }
            root.obsMergePct = numOr(payload.merge_pct, -1)
            root.obsMerged = numOr(payload.merged, -1)
            root.obsOrphans = numOr(payload.orphans, -1)
            root.obsRawFacts = numOr(payload.raw_facts, -1)
            root.obsReferencedPct = numOr(payload.referenced_pct, -1)
            root.obsAvgProofs = numOr(payload.avg_proofs, -1)
        }
    }

    // Exposes aggregate OpenAI OAuth availability only; no credential details
    // are passed to the UI.
    PlasmaSupport.DataSource {
        id: openAiKeysSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/hermes_openai_keys.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            // Helper contract: always "<available> <total>"; "-1 0" is the
            // explicit drift signal (missing executable, subprocess/timeout
            // error, missing selected profile or format drift).
            // Nonzero exit or unknown output resets the card to unknown so
            // stale values never persist.
            var match = buffer.trim().match(/^(-?\d+)\s+(\d+)$/)
            buffer = ""
            disconnectSource(source)
            if (Number(data["exit code"]) !== 0 || !match) {
                root.openAiActiveKeys = -1
                root.openAiTotalKeys = 0
                // Failed fetch (e.g. plasmashell cold start): back off briefly instead of
                // staying UNKNOWN for the full 15-minute refresh interval.
                if (root.openAiFetchFailures < 5) root.openAiFetchFailures += 1
                return
            }
            root.openAiFetchFailures = 0
            root.openAiActiveKeys = parseInt(match[1])
            root.openAiTotalKeys = parseInt(match[2])
        }
    }

    PlasmaSupport.DataSource {
        id: aiServicesSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/ai_services_status.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var payload = null
            try { payload = JSON.parse(buffer.trim()) } catch (error) { payload = null }
            buffer = ""
            disconnectSource(source)
            if (Number(data["exit code"]) !== 0 || !payload
                    || typeof payload.gateway !== "string" || typeof payload.hindsight !== "string") {
                root.resetServices()
                return
            }
            root.lastServiceTelemetryMs = Date.now()
            root.hermesGatewayState = (payload.gateway || "UNKNOWN").toUpperCase()
            root.hindsightState = (payload.hindsight || "UNKNOWN").toUpperCase()
        }
    }

    PlasmaSupport.DataSource {
        id: gpuTelemetrySource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/gpu_telemetry.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var payload = null
            try { payload = JSON.parse(buffer.trim()) } catch (error) { payload = null }
            buffer = ""
            disconnectSource(source)
            var fullUnavailable = Number(data["exit code"]) !== 0 || !payload || !payload.gpus
            if (!fullUnavailable) fullUnavailable = !!payload.gpu_error || (!!payload.error && !payload.process_error)
            root.gpuProcessUnavailable = fullUnavailable || payload === null || payload.processes_available === false
            if (fullUnavailable) {
                root.applyGpuTelemetry(null, 0)
                root.applyGpuTelemetry(null, 1)
                return
            }
            root.lastGpuTelemetryMs = Date.now()
            // Index by the GPU's own index field, never by array position:
            // nvidia-smi numbers the GPUs itself and a missing card must leave
            // exactly its own card empty instead of shifting the others.
            var byIndex = ({})
            for (var i = 0; i < payload.gpus.length; i++) {
                byIndex[String(Number(payload.gpus[i].index))] = payload.gpus[i]
            }
            root.applyGpuTelemetry(byIndex["0"] || null, 0)
            root.applyGpuTelemetry(byIndex["1"] || null, 1)
        }
    }

    PlasmaSupport.DataSource {
        id: gpuLoadSource
        engine: "executable"
        connectedSources: []
        // No Python startup or process enumeration in the fast sampling path.
        // A hung nvidia-smi would block this source (connectSource on a running
        // command is a no-op), so bound it; a killed call is a missing sample.
        property string command: "timeout 2 nvidia-smi --query-gpu=index,uuid,utilization.gpu --format=csv,noheader,nounits"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var entries = Number(data["exit code"]) === 0 ? MonitorLogic.parseGpuLoads(buffer) : ({})
            buffer = ""
            disconnectSource(source)
            root.applyGpuLoads(entries, Date.now())
        }
    }

    PlasmaSupport.DataSource {
        id: topCpuSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/cpu_process_snapshot.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            root.cpuProcessUnavailable = Number(data["exit code"]) !== 0
            if (root.cpuProcessUnavailable) {
                buffer = ""
                disconnectSource(source)
                root.topCpuProcesses = []
                return
            }
            var lines = buffer.trim().split("\n")
            buffer = ""
            disconnectSource(source)
            var samples = []
            var currentByPid = ({})
            for (var i = 0; i < lines.length; i++) {
                var match = lines[i].trim().match(/^([0-9]+)\s+([0-9.]+)\s+(.+)$/)
                if (!match) continue
                var sample = { pid: parseInt(match[1], 10), cpuSeconds: parseFloat(match[2]), name: match[3].trim() }
                samples.push(sample)
                currentByPid[String(sample.pid)] = sample
            }
            var now = Date.now()
            var elapsedMs = root.previousCpuSampleMs > 0 ? now - root.previousCpuSampleMs : 0
            var processes = MonitorLogic.cpuProcessRates(root.previousCpuSamples, samples, elapsedMs, 4)
            if (samples.length > 0) root.lastCpuTelemetryMs = now
            root.topCpuProcesses = processes
            root.previousCpuSamples = currentByPid
            root.previousCpuSampleMs = now
        }
    }

    PlasmaSupport.DataSource {
        id: topRamSource
        engine: "executable"
        connectedSources: []
        property string command: "ps -eo rss=,comm= --sort=-rss | head -4"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            root.ramProcessUnavailable = Number(data["exit code"]) !== 0
            if (root.ramProcessUnavailable) {
                buffer = ""
                disconnectSource(source)
                root.topRamProcesses = []
                return
            }
            var lines = buffer.trim().split("\n")
            buffer = ""
            disconnectSource(source)
            var processes = []
            for (var i = 0; i < lines.length && processes.length < 4; i++) {
                var match = lines[i].trim().match(/^([0-9]+)\s+(.+)$/)
                if (!match) continue
                processes.push({ ram: (parseFloat(match[1]) / 1024).toFixed(1) + " MiB", ramExact: match[1] + " KiB", name: match[2].trim() })
            }
            root.topRamProcesses = processes
        }
    }


    PlasmaSupport.DataSource {
        id: netDetectSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/network_interface.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath)
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var iface = buffer.trim()
            buffer = ""
            disconnectSource(source)
            if (Number(data["exit code"]) !== 0) iface = ""
            if (iface !== root.netIf) {
                networkCountersSource.disconnectSource(networkCountersSource.command)
                networkCountersSource.buffer = ""
                networkCountersSource.generation += 1
                root.netIf = iface
                root.resetNetwork()
                if (iface.length > 0) networkCountersSource.connectSource(networkCountersSource.command)
            }
        }
    }

    // Read cumulative byte counters directly from /proc/net/dev. KDE's dynamic
    // network Sensor bindings can remain stuck on the empty startup interface.
    PlasmaSupport.DataSource {
        id: networkCountersSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/network_counters.sh"))
        // The helper ignores the final argument. It gives each interface
        // generation a distinct executable source, including A -> B -> A.
        property int generation: 0
        property string command: "sh " + MonitorLogic.shellQuote(scriptPath) + " " + MonitorLogic.shellQuote(root.netIf) + " /proc/net/dev " + networkCountersSource.generation
        property string buffer: ""
        onNewData: function(source, data) {
            if (source !== networkCountersSource.command) {
                disconnectSource(source)
                return
            }
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var output = buffer.trim()
            var exitCode = data["exit code"]
            buffer = ""
            disconnectSource(source)
            if (exitCode !== 0) { root.resetNetwork(); return }
            var fields = output.split(/\s+/)
            if (fields.length !== 2) { root.resetNetwork(); return }
            var rxBytes = parseFloat(fields[0])
            var txBytes = parseFloat(fields[1])
            var now = Date.now()
            if (!isFinite(rxBytes) || !isFinite(txBytes)) { root.resetNetwork(); return }
            if (root.previousNetworkSampleMs > 0 && now > root.previousNetworkSampleMs) {
                var elapsedSeconds = (now - root.previousNetworkSampleMs) / 1000
                root.down = Math.max(0, rxBytes - root.previousRxBytes) / elapsedSeconds
                root.up = Math.max(0, txBytes - root.previousTxBytes) / elapsedSeconds
                root.networkAvailable = true
            } else {
                root.down = 0
                root.up = 0
                root.networkAvailable = false
            }
            root.previousRxBytes = rxBytes
            root.previousTxBytes = txBytes
            root.previousNetworkSampleMs = now
            root.lastNetworkTelemetryMs = now
        }
    }

    Component.onCompleted: {
        gpuLoadSource.connectSource(gpuLoadSource.command)
        gpuTelemetrySource.connectSource(gpuTelemetrySource.command)
        topCpuSource.connectSource(topCpuSource.command)
        topRamSource.connectSource(topRamSource.command)
        processCountSource.connectSource(processCountSource.command)
        hermesThinkSource.connectSource(hermesThinkSource.command)
        openAiKeysSource.connectSource(openAiKeysSource.command)
        aiServicesSource.connectSource(aiServicesSource.command)
        obsHealthSource.connectSource(obsHealthSource.command)
        netDetectSource.connectSource(netDetectSource.command)
        diskUsageSource.connectSource(diskUsageSource.command)
        root.currentTime = root.refreshClock()
    }

    Timer {
        interval: 500
        running: true
        repeat: true
        onTriggered: gpuLoadSource.connectSource(gpuLoadSource.command)
    }

    Timer {
        interval: 2000
        running: true
        repeat: true
        onTriggered: gpuTelemetrySource.connectSource(gpuTelemetrySource.command)
    }

    Timer {
        interval: 5000
        running: true
        repeat: true
        onTriggered: topCpuSource.connectSource(topCpuSource.command)
    }

    Timer {
        interval: 5000
        running: true
        repeat: true
        onTriggered: topRamSource.connectSource(topRamSource.command)
    }

    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: diskUsageSource.connectSource(diskUsageSource.command)
    }

    Timer {
        interval: 10000
        running: true
        repeat: true
        onTriggered: processCountSource.connectSource(processCountSource.command)
    }

    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: hermesThinkSource.connectSource(hermesThinkSource.command)
    }

    // OAuth key counts: retry failed fetches with short backoff (cold-start failures),
    // then settle on the 15-minute steady-state interval.
    Timer {
        interval: Math.min(60000, 5000 * Math.pow(2, Math.min(5, root.openAiFetchFailures)))
        running: root.openAiActiveKeys < 0 || root.openAiTotalKeys < 0
        repeat: true
        onTriggered: openAiKeysSource.connectSource(openAiKeysSource.command)
    }

    // Sole steady-state OAuth poll (the service poll no longer runs
    // `hermes auth list`); one minute tracks elapsing cooldowns closely.
    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: openAiKeysSource.connectSource(openAiKeysSource.command)
    }

    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: aiServicesSource.connectSource(aiServicesSource.command)
    }

    // Observation health moves only when a consolidation run writes rows, so a
    // 5-minute cadence is far below the signal's rate of change.
    Timer {
        interval: 300000
        running: true
        repeat: true
        onTriggered: obsHealthSource.connectSource(obsHealthSource.command)
    }


    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            if (root.netIf.length > 0) networkCountersSource.connectSource(networkCountersSource.command)
        }
    }

    // Re-detect network interface every 30s in case of hotplug
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: netDetectSource.connectSource(netDetectSource.command)
    }

    // Static ksystemstats bindings for CPU, memory, disk and uptime.
    Sensors.Sensor { sensorId: "cpu/all/usage"; enabled: true; property bool readyForSamples: false; onValueChanged: root.recordSystemSensor("cpu", value, readyForSamples && status === Sensors.Sensor.Ready); onStatusChanged: { readyForSamples = false; Qt.callLater(function() { readyForSamples = status === Sensors.Sensor.Ready }); if (status !== Sensors.Sensor.Ready) root.recordSystemSensor("cpu", NaN, false) } }
    Sensors.Sensor { sensorId: "cpu/all/averageTemperature"; enabled: true; property bool readyForSamples: false; onValueChanged: root.recordSystemSensor("cpuTemp", value, readyForSamples && status === Sensors.Sensor.Ready); onStatusChanged: { readyForSamples = false; Qt.callLater(function() { readyForSamples = status === Sensors.Sensor.Ready }); if (status !== Sensors.Sensor.Ready) root.recordSystemSensor("cpuTemp", NaN, false) } }

    Sensors.Sensor { sensorId: "memory/physical/usedPercent"; enabled: true; property bool readyForSamples: false; onValueChanged: root.recordSystemSensor("ram", value, readyForSamples && status === Sensors.Sensor.Ready); onStatusChanged: { readyForSamples = false; Qt.callLater(function() { readyForSamples = status === Sensors.Sensor.Ready }); if (status !== Sensors.Sensor.Ready) root.recordSystemSensor("ram", NaN, false) } }
    Sensors.Sensor { sensorId: "memory/physical/used"; enabled: true; property bool readyForSamples: false; onValueChanged: root.recordSystemSensor("ramUsedBytes", value, readyForSamples && status === Sensors.Sensor.Ready); onStatusChanged: { readyForSamples = false; Qt.callLater(function() { readyForSamples = status === Sensors.Sensor.Ready }); if (status !== Sensors.Sensor.Ready) root.recordSystemSensor("ramUsedBytes", NaN, false) } }
    Sensors.Sensor { sensorId: "memory/physical/total"; enabled: true; property bool readyForSamples: false; onValueChanged: root.recordSystemSensor("ramTotalBytes", value, readyForSamples && status === Sensors.Sensor.Ready); onStatusChanged: { readyForSamples = false; Qt.callLater(function() { readyForSamples = status === Sensors.Sensor.Ready }); if (status !== Sensors.Sensor.Ready) root.recordSystemSensor("ramTotalBytes", NaN, false) } }
    Sensors.Sensor { sensorId: "os/system/uptime"; enabled: true; onValueChanged: root.uptimeSeconds = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "cpu/loadaverages/loadaverage1"; enabled: true; onValueChanged: root.loadAverage = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "cpu/all/coreCount"; enabled: true; onValueChanged: { root.cpuCoreCount = Math.round(parseFloat(value)) || 0 } }
}
