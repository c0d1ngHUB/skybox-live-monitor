import QtQuick 2.15

import QtQuick.Layouts 1.15
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid 2.0
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport
import org.kde.ksysguard.sensors 1.0 as Sensors
import "../code/monitor_logic.js" as MonitorLogic

PlasmoidItem {
    id: root
    property real cpu: 0
    property real ram: 0
    property real ramUsedBytes: 0
    property real ramTotalBytes: 0
    property real cpuTemp: 0
    property bool gpu0Available: false
    property string gpu0Name: "GPU 0"
    property real gpu0Usage: 0
    property real gpu0Temp: 0
    property real gpu0VramUsedMiB: 0
    property real gpu0VramTotalMiB: 0
    property real gpu0PowerDrawWatts: 0
    property real gpu0PowerLimitWatts: 0
    property bool gpu1Available: false
    property string gpu1Name: "GPU 1"
    property real gpu1Usage: 0
    property real gpu1Temp: 0
    property real gpu1VramUsedMiB: 0
    property real gpu1VramTotalMiB: 0
    property real gpu1PowerDrawWatts: 0
    property real gpu1PowerLimitWatts: 0
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
    property int openAiActiveKeys: -1
    property int openAiTotalKeys: -1
    property int openAiFetchFailures: 0
    property string hermesGatewayState: "UNKNOWN"
    property string hindsightState: "UNKNOWN"
    // Observation health of the Hindsight bank, read by
    // contents/code/hindsight_observation_health.py. Guards the
    // observation_scopes="shared" switch: TAGS 0 shows the share of observations
    // that reached the untagged scope (baseline 1.7%), PROOF 1 the share backed
    // by a single source fact and therefore never merged (baseline 62.4%).
    property real obsTaglessPct: -1
    property real obsSingleProofPct: -1
    property int obsCount: 0
    property int obsSinceSwitch: 0
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
    property int historySeconds: 120
    property var cpuHistory: []
    property var gpu0History: []
    property var gpu1History: []
    property var ramHistory: []
    property var downHistory: []
    property var upHistory: []
    // Prefer the default-route interface; fall back to the first physical interface.
    property string netIf: ""

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
        if (!usedBytes || !totalBytes) return "-- / -- GiB"
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
        return root.historyPeak(root.downHistory) < 1 && root.historyPeak(root.upHistory) < 1
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
        return root.cpuCoreCount > 0 ? "TOP · CPU % · 1/" + root.cpuCoreCount + " CORE" : "TOP · CPU %"
    }
    function cpuDetailLabel() {
        var temp = Math.round(root.cpuTemp) + "°C"
        return root.cpuCoreCount > 0 ? temp + " · " + root.cpuCoreCount + " THREADS" : temp
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

    // --- Hindsight observation health -------------------------------------
    // "TAGS 0" = share of observations with no tags (the untagged scope that
    // observation_scopes="shared" writes into). It only rises as NEW observations
    // consolidate, because old rows keep their tags for good.
    function fmtPct1(value) {
        if (value === null || value === undefined || value < 0 || !isFinite(value)) return "--"
        return (Math.round(value * 10) / 10).toFixed(1) + "%"
    }

    function obsHealthLabel() {
        if (root.obsHealthUnavailable) return "? OBS"
        return "TAGS 0 " + root.fmtPct1(root.obsTaglessPct) + " · PROOF 1 " + root.fmtPct1(root.obsSingleProofPct)
    }

    // Baseline-relative signal: tagless above baseline and single-proof below it
    // is the direction the switch is supposed to move both numbers.
    function obsHealthTone() {
        if (root.obsHealthUnavailable) return root.muted
        if (root.obsTaglessPct > 1.7 && root.obsSingleProofPct < 62.4) return root.cyan
        return root.warning
    }

    function obsHealthBorderColor() {
        if (root.obsHealthUnavailable) return root.muted
        if (root.obsTaglessPct > 1.7 && root.obsSingleProofPct < 62.4) return root.cyan
        return root.warning
    }

    function obsHealthDetail() {
        var base = root.obsCount + " OBS · " + root.obsSinceSwitch + " SEIT SWITCH"
        if (root.obsHealthUnavailable) return base + " · API NICHT ERREICHBAR"
        return base + " · BASIS " + root.fmtPct1(1.7) + " / " + root.fmtPct1(62.4)
    }
    function gpuPowerText(drawValue, limitValue) {
        var draw = Number(drawValue)
        var limit = Number(limitValue)
        if (!isFinite(draw) || !isFinite(limit) || limit <= 0) return ""
        return "POWER " + draw.toFixed(0) + "/" + limit.toFixed(0) + " W"
    }

    function vramPercent(usedMiB, totalMiB) {
        if (totalMiB <= 0) return 0
        return 100 * usedMiB / totalMiB
    }
    function gpuProcessRows(entry) {
        var rows = []
        var processes = entry && entry.processes ? entry.processes : []
        for (var i = 0; i < processes.length; i++) {
            rows.push({ name: processes[i].name || "GPU PROCESS", gpu: root.fmtVram(Number(processes[i].used_mib) || 0) })
        }
        return rows
    }
    function applyGpuTelemetry(entry, index) {
        var available = !!entry
        if (index === 0) {
            root.gpu0Available = available
            if (!available) { root.topGpu0Processes = []; root.gpu0ProcessCount = 0; return }
            root.gpu0Name = entry.short_name || entry.name || "GPU 0"
            root.gpu0Usage = root.clamp(Number(entry.utilization_percent) || 0)
            root.gpu0Temp = Number(entry.temperature_c) || 0
            root.gpu0VramUsedMiB = Number(entry.memory_used_mib) || 0
            root.gpu0VramTotalMiB = Number(entry.memory_total_mib) || 0
            root.gpu0PowerDrawWatts = Number(entry.power_draw_w) || 0
            root.gpu0PowerLimitWatts = Number(entry.power_limit_w) || 0
            root.gpu0ProcessCount = Number(entry.process_count) || 0
            root.topGpu0Processes = root.gpuProcessRows(entry)
            return
        }
        root.gpu1Available = available
        if (!available) { root.topGpu1Processes = []; root.gpu1ProcessCount = 0; return }
        root.gpu1Name = entry.short_name || entry.name || "GPU 1"
        root.gpu1Usage = root.clamp(Number(entry.utilization_percent) || 0)
        root.gpu1Temp = Number(entry.temperature_c) || 0
        root.gpu1VramUsedMiB = Number(entry.memory_used_mib) || 0
        root.gpu1VramTotalMiB = Number(entry.memory_total_mib) || 0
        root.gpu1PowerDrawWatts = Number(entry.power_draw_w) || 0
        root.gpu1PowerLimitWatts = Number(entry.power_limit_w) || 0
        root.gpu1ProcessCount = Number(entry.process_count) || 0
        root.topGpu1Processes = root.gpuProcessRows(entry)
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

    function usageColor(value, normalColor) {
        if (value >= 95) return root.critical
        if (value >= 85) return root.warning
        return normalColor
    }
    function shortProcessName(name) {
        return (name || "PROCESS").split("/").pop()
    }
    function compactProcessValue(metricLabel, value) {
        // Explicit units are worth the few pixels they use in process rows.
        var text = String(value || "")
        // Large RAM values are shorter and easier to compare in GiB, while
        // smaller values retain MiB precision.
        if (metricLabel === "RAM" && text.indexOf(" MiB") > 0) {
            var mib = parseFloat(text)
            if (isFinite(mib) && mib >= 1024) return (mib / 1024).toFixed(1) + " GiB"
        }
        return text
    }
    function metricBorderColor(metric) {
        if (metric.healthLevel >= 2) return root.critical
        if (metric.healthLevel >= 1) return root.warning
        return root.quietBorder
    }
    function push(hist, value) {
        var h = hist.slice(0)
        h.push(value)
        if (h.length > root.historySeconds) h.shift()
        return h
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
                        id: obsHealthCard
                        width: parent.width
                        height: 28
                        radius: 9
                        color: Qt.rgba(0, 0, 0, 0.22)
                        border.width: 1
                        border.color: root.obsHealthBorderColor()
                        PlasmaCore.ToolTipArea {
                            anchors.fill: parent
                            mainText: root.obsHealthDetail()
                        }
                        Text {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: "HINDSIGHT OBS"
                            color: root.muted
                            font.family: "DejaVu Sans Mono"
                            font.pixelSize: 14
                            font.bold: true
                        }
                        Text {
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.obsHealthLabel()
                            color: root.obsHealthTone()
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
                    Text { text: "━━ GPU 0 · " + root.gpu0Name + " · " + (root.gpu0Available ? Math.round(root.gpu0Usage) + "%" : "UNAVAILABLE"); color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
                    Text { text: "━━ GPU 1 · " + root.gpu1Name + " · " + (root.gpu1Available ? Math.round(root.gpu1Usage) + "%" : "UNAVAILABLE"); color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
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
                            var firstX = MonitorLogic.historyX(0, data.length, chartWidth, root.historySeconds) + plotLeft
                            var lastX = MonitorLogic.historyX(data.length - 1, data.length, chartWidth, root.historySeconds) + plotLeft
                            ctx.beginPath()
                            for (var f = 0; f < data.length; f++) {
                                var fx = plotLeft + MonitorLogic.historyX(f, data.length, chartWidth, root.historySeconds)
                                var fy = height - plotTop - (root.clamp(data[f]) / 100) * plotHeight
                                if (f === 0) ctx.moveTo(fx, fy); else ctx.lineTo(fx, fy)
                            }
                            ctx.lineTo(lastX, height)
                            ctx.lineTo(firstX, height)
                            ctx.closePath()
                            ctx.fillStyle = fillColor
                            ctx.fill()
                            ctx.strokeStyle = color; ctx.lineWidth = 3
                            ctx.setLineDash(dashed ? [8, 5] : [])
                            ctx.beginPath()
                            for (var j = 0; j < data.length; j++) {
                                var x = plotLeft + MonitorLogic.historyX(j, data.length, chartWidth, root.historySeconds)
                                var y = height - plotTop - (root.clamp(data[j]) / 100) * plotHeight
                                if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                            }
                            ctx.stroke()
                            ctx.setLineDash([])

                            // Emphasize the newest sample so the history-to-card
                            // transition is immediately readable at the NOW edge.
                            var currentIndex = data.length - 1
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

            // --- Dual-GPU row followed by compact CPU/RAM row ---
            Grid {
                Layout.fillWidth: true
                Layout.preferredHeight: 278
                columns: 2
                rows: 2
                columnSpacing: 16
                rowSpacing: 16

                Repeater {
                    model: [
                        {kind:"gpu", label:"GPU 0 · " + root.gpu0Name, available:root.gpu0Available, value:root.gpu0Available ? Math.round(root.gpu0Usage) + "%" : "--", detail:root.gpu0Available ? Math.round(root.gpu0Temp) + "°C" : "UNAVAILABLE", color:root.violet, detailColor:root.gpuTempColor(root.gpu0Temp, root.muted), healthLevel:!root.gpu0Available ? 2 : ((root.gpu0Temp >= 90 || root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB) >= 95) ? 2 : ((root.gpu0Temp >= 85 || root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB) >= 85) ? 1 : 0)), vramFill:root.vramPercent(root.gpu0VramUsedMiB, root.gpu0VramTotalMiB), powerText:root.gpuPowerText(root.gpu0PowerDrawWatts, root.gpu0PowerLimitWatts), processes:root.topGpu0Processes, processCount:root.gpu0ProcessCount, processUnavailable:!root.gpu0Available || root.gpuProcessUnavailable},
                        {kind:"gpu", label:"GPU 1 · " + root.gpu1Name, available:root.gpu1Available, value:root.gpu1Available ? Math.round(root.gpu1Usage) + "%" : "--", detail:root.gpu1Available ? Math.round(root.gpu1Temp) + "°C" : "UNAVAILABLE", color:root.cyan, detailColor:root.gpuTempColor(root.gpu1Temp, root.muted), healthLevel:!root.gpu1Available ? 2 : ((root.gpu1Temp >= 90 || root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB) >= 95) ? 2 : ((root.gpu1Temp >= 85 || root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB) >= 85) ? 1 : 0)), vramFill:root.vramPercent(root.gpu1VramUsedMiB, root.gpu1VramTotalMiB), powerText:root.gpuPowerText(root.gpu1PowerDrawWatts, root.gpu1PowerLimitWatts), processes:root.topGpu1Processes, processCount:root.gpu1ProcessCount, processUnavailable:!root.gpu1Available || root.gpuProcessUnavailable},
                        {kind:"cpu", label:"CPU", value:Math.round(root.cpu) + "%", detail:root.cpuDetailLabel(), color:root.blue, detailColor:root.tempColor(root.cpuTemp, root.muted), healthLevel:root.cpuTemp >= 85 ? 2 : (root.cpuTemp >= 75 ? 1 : 0), processes:root.topCpuProcesses, processCount:root.topCpuProcesses.length, processUnavailable:root.cpuProcessUnavailable},
                        {kind:"ram", label:"RAM", value:Math.round(root.ram) + "%", detail:root.fmtMemoryPair(root.ramUsedBytes, root.ramTotalBytes), color:root.orange, detailColor:root.ram >= 85 ? root.warning : root.muted, healthLevel:root.ram >= 95 ? 2 : (root.ram >= 85 ? 1 : 0), processes:root.topRamProcesses, processCount:root.topRamProcesses.length, processUnavailable:root.ramProcessUnavailable}
                    ]
                    delegate: Rectangle {
                        width: (parent.width - 16) / 2; height: (parent.height - 16) / 2; radius: 16
                        clip: true
                        color: Qt.rgba(0.035, 0.22, 0.34, 0.82); border.width: modelData.healthLevel > 0 ? 2 : 1; border.color: root.metricBorderColor(modelData); opacity: 0.95

                        // A fixed KPI column prevents large percentages from colliding
                        // with process names at narrow dashboard widths.
                        Item {
                            anchors.fill: parent; anchors.margins: 12
                            Column {
                                id: metricKpi
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: Math.max(116, parent.width * 0.34); spacing: 4
                                PlasmaCore.ToolTipArea {
                                    width: parent.width - 4
                                    height: 18
                                    mainText: modelData.label
                                    Text { anchors.fill: parent; text: modelData.label; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideMiddle }
                                }

                                // --- GPU card: utilization as the large metric + temperature secondary ---
                                Text {
                                    visible: modelData.kind === "gpu"
                                    text: modelData.value
                                    color: root.ink
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: 28
                                    font.bold: true
                                }
                                Text {
                                    visible: modelData.kind === "gpu"
                                    text: modelData.detail
                                    color: modelData.detailColor
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                // VRAM bar with percentage overlay (GPU only)
                                Item {
                                    width: parent.width - 4
                                    height: 22
                                    visible: modelData.kind === "gpu"
                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 11
                                        color: Qt.rgba(1, 1, 1, 0.08)
                                    }
                                    Rectangle {
                                        height: parent.height
                                        width: parent.width * Math.max(0, Math.min(1, (modelData.vramFill || 0) / 100))
                                        radius: 11
                                        color: (modelData.vramFill || 0) >= 85 ? root.critical : root.cyan
                                    }
                                    Text {
                                        anchors.centerIn: parent
                                        text: "VRAM " + Math.round(modelData.vramFill || 0) + "%"
                                        color: (modelData.vramFill || 0) >= 40 ? "#000000" : root.ink
                                        font.family: "DejaVu Sans Mono"
                                        font.pixelSize: 13
                                        font.bold: true
                                    }
                                }
                                Text {
                                    visible: modelData.kind === "gpu" && !!modelData.powerText
                                    width: parent.width - 4
                                    text: modelData.powerText || ""
                                    color: root.muted
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    font.bold: true
                                    elide: Text.ElideRight
                                }

                                // --- CPU card: utilization as the large metric + temperature secondary ---
                                Text {
                                    visible: modelData.kind === "cpu"
                                    text: modelData.value
                                    color: root.ink
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: 28
                                    font.bold: true
                                }
                                Text {
                                    visible: modelData.kind === "cpu"
                                    text: modelData.detail
                                    color: modelData.detailColor
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                // --- RAM card: original layout ---
                                Text { visible: modelData.kind === "ram"; text: modelData.value; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 28; font.bold: true }
                                Text { visible: modelData.kind === "ram"; width: parent.width - 4; text: modelData.detail; color: modelData.detailColor; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                // VRAM fill bar — only on GPU card (original position, kept for non-GPU safety)
                                Item {
                                    width: parent.width - 4
                                    height: 8
                                    visible: modelData.kind !== "gpu" && modelData.vramFill !== undefined
                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 4
                                        color: Qt.rgba(1, 1, 1, 0.08)
                                    }
                                    Rectangle {
                                        height: parent.height
                                        width: parent.width * Math.min(1, (modelData.vramFill || 0) / 100)
                                        radius: 4
                                        color: (modelData.vramFill || 0) >= 85 ? root.critical : root.cyan
                                    }
                                }
                            }
                            Rectangle { id: metricDivider; anchors.left: metricKpi.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 1; color: modelData.color; opacity: 0.24 }
                            Column {
                                id: processDetails
                                anchors.left: metricDivider.right; anchors.leftMargin: 10
                                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                                spacing: 3
                                property string metricKind: modelData.kind
                                property var processes: modelData.processes || []
                                property string heading: metricKind === "cpu" ? root.cpuProcessHeading() : (metricKind === "gpu" ? "TOP · VRAM" : "TOP · RAM")
                                // A fifth GPU workload is capped, so say how many exist.
                                property string headingSuffix: metricKind === "gpu" && modelData.processCount > processDetails.processes.length ? " (+" + (modelData.processCount - processDetails.processes.length) + ")" : ""
                                Text { width: parent.width; text: processDetails.heading + processDetails.headingSuffix; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight }
                                Repeater {
                                    model: parent.processes
                                    delegate: Item {
                                        width: parent.width
                                        height: 19
                                        property var process: modelData
                                        property string displayValue: root.compactProcessValue(processDetails.metricKind.toUpperCase(), processDetails.metricKind === "cpu" ? process.cpu + "%" : (processDetails.metricKind === "ram" ? process.ram : process.gpu))
                                        PlasmaCore.ToolTipArea {
                                            anchors.left: parent.left
                                            anchors.right: processValue.left
                                            anchors.rightMargin: 8
                                            height: parent.height
                                            mainText: parent.process.name
                                            Text {
                                                anchors.fill: parent
                                                text: root.shortProcessName(parent.mainText)
                                                color: index === 0 ? root.ink : root.muted
                                                font.family: "DejaVu Sans Mono"; font.pixelSize: 14
                                                elide: Text.ElideRight
                                            }
                                        }
                                        Text {
                                            id: processValue
                                            anchors.right: parent.right
                                            text: parent.displayValue
                                            color: index === 0 ? root.ink : root.muted
                                            font.family: "DejaVu Sans Mono"; font.pixelSize: 14
                                            font.bold: true
                                            horizontalAlignment: Text.AlignRight
                                        }
                                    }
                                }
                                Text {
                                    visible: parent.processes.length === 0
                                    text: modelData.processUnavailable ? "UNAVAILABLE" : (modelData.processCount === 0 ? (processDetails.metricKind === "gpu" ? "NO ACTIVE WORKLOAD" : "SAMPLING…") : "SAMPLING…")
                                    color: root.muted
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 14
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
                                var firstX = plotLeft + MonitorLogic.historyX(0, d.length, chartWidth, root.historySeconds)
                                var lastX = plotLeft + MonitorLogic.historyX(d.length - 1, d.length, chartWidth, root.historySeconds)
                                // P3: Filled area under the line
                                ctx.beginPath()
                                for (var j = 0; j < d.length; j++) {
                                    var x = plotLeft + MonitorLogic.historyX(j, d.length, chartWidth, root.historySeconds)
                                    var y = height - Math.min(1, d[j] / root.downloadScaleBytesPerSecond) * height
                                    if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                }
                                ctx.lineTo(lastX, height)
                                ctx.lineTo(firstX, height)
                                ctx.closePath()
                                ctx.fillStyle = "rgba(150,245,246,0.15)"
                                ctx.fill()
                                // Line on top
                                ctx.strokeStyle = root.cyan; ctx.lineWidth = 2.5; ctx.beginPath()
                                for (var k = 0; k < d.length; k++) {
                                    var x2 = plotLeft + MonitorLogic.historyX(k, d.length, chartWidth, root.historySeconds)
                                    var y2 = height - Math.min(1, d[k] / root.downloadScaleBytesPerSecond) * height
                                    if (k === 0) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2)
                                }
                                ctx.stroke()
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
                                var firstX = plotLeft + MonitorLogic.historyX(0, d.length, chartWidth, root.historySeconds)
                                var lastX = plotLeft + MonitorLogic.historyX(d.length - 1, d.length, chartWidth, root.historySeconds)
                                // P3: Filled area
                                ctx.beginPath()
                                for (var j = 0; j < d.length; j++) {
                                    var x = plotLeft + MonitorLogic.historyX(j, d.length, chartWidth, root.historySeconds)
                                    var y = height - Math.min(1, d[j] / root.uploadScaleBytesPerSecond) * height
                                    if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                }
                                ctx.lineTo(lastX, height)
                                ctx.lineTo(firstX, height)
                                ctx.closePath()
                                ctx.fillStyle = "rgba(219,145,255,0.15)"
                                ctx.fill()
                                // Line on top
                                ctx.strokeStyle = root.violet; ctx.lineWidth = 2.5; ctx.beginPath()
                                for (var k = 0; k < d.length; k++) {
                                    var x2 = plotLeft + MonitorLogic.historyX(k, d.length, chartWidth, root.historySeconds)
                                    var y2 = height - Math.min(1, d[k] / root.uploadScaleBytesPerSecond) * height
                                    if (k === 0) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2)
                                }
                                ctx.stroke()
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
                        Text { width: parent.width; elide: Text.ElideRight; text: root.fmtDiskSummary(); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12 }
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
                                    property string sessionLabel: "HERMES-SESSION"
                                    property string profileHint: root.hermesMaxThinkService.length > 0 ? " · Profil: " + root.hermesMaxThinkService.toLowerCase() : ""
                                    mainText: "Längste abgeschlossene Hermes-Antwort der letzten 24 h" + profileHint
                                    Text { anchors.fill: parent; verticalAlignment: Text.AlignVCenter; text: parent.sessionLabel; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                }
                                Text { id: thinkDuration; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.fmtDuration(root.hermesMaxThinkSeconds); color: root.ink; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true }
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
                root.cpuHistory = root.push(root.cpuHistory, root.cpu)
                root.gpu0History = root.push(root.gpu0History, root.gpu0Usage)
                root.gpu1History = root.push(root.gpu1History, root.gpu1Usage)
                root.ramHistory = root.push(root.ramHistory, root.ram)
                root.downHistory = root.push(root.downHistory, root.down)
                root.upHistory = root.push(root.upHistory, root.up)
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
        property string scriptPath: Qt.resolvedUrl("../code/disk_usage.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath + " /"
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
        property string command: "sh -c 'ps -e --no-headers | wc -l'"
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
        property string scriptPath: Qt.resolvedUrl("../code/hermes_max_think.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var payload = null
            try { payload = JSON.parse(buffer.trim()) } catch (error) { payload = null }
            buffer = ""
            disconnectSource(source)
            if (!payload) return
            var seconds = Number(payload.seconds)
            if (!isNaN(seconds)) root.hermesMaxThinkSeconds = Math.max(0, seconds)
            root.hermesMaxThinkService = String(payload.service || "").toUpperCase()
        }
    }

    // Hindsight observation health: guards the observation_scopes="shared"
    // switch by reporting the tagless share (baseline 1.7%) and the
    // single-proof share (baseline 62.4%) of the bank's observations.
    PlasmaSupport.DataSource {
        id: obsHealthSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: Qt.resolvedUrl("../code/hindsight_observation_health.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath
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
                root.obsHealthUnavailable = true
                root.obsTaglessPct = -1
                root.obsSingleProofPct = -1
                return
            }
            var tagless = Number(payload.tagless_pct)
            var single = Number(payload.single_proof_pct)
            if (isNaN(tagless) || isNaN(single)) {
                root.obsHealthUnavailable = true
                root.obsTaglessPct = -1
                root.obsSingleProofPct = -1
                return
            }
            root.obsHealthUnavailable = false
            root.obsTaglessPct = tagless
            root.obsSingleProofPct = single
            root.obsCount = Number(payload.observations) || 0
            root.obsSinceSwitch = Number(payload.since_switch) || 0
        }
    }

    // Exposes aggregate OpenAI OAuth availability only; no credential details
    // are passed to the UI.
    PlasmaSupport.DataSource {
        id: openAiKeysSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: Qt.resolvedUrl("../code/hermes_openai_keys.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath
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
        property string scriptPath: Qt.resolvedUrl("../code/ai_services_status.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var payload = null
            try { payload = JSON.parse(buffer.trim()) } catch (error) { payload = null }
            buffer = ""
            disconnectSource(source)
            if (!payload) return
            root.hermesGatewayState = (payload.gateway || "UNKNOWN").toUpperCase()
            root.hindsightState = (payload.hindsight || "UNKNOWN").toUpperCase()
            if (Number(payload.openai_oauth_available) >= 0) root.openAiActiveKeys = Number(payload.openai_oauth_available)
            if (Number(payload.openai_oauth_total) >= 0) root.openAiTotalKeys = Number(payload.openai_oauth_total)
            // An unavailable aggregator means the dedicated helper never
            // produced a valid aggregate; keep the OAuth card at unknown
            // instead of letting a stale successful count persist.
            if (Number(payload.openai_oauth_available) < 0 || Number(payload.openai_oauth_total) < 0) {
                root.openAiActiveKeys = -1
                root.openAiTotalKeys = 0
            }
        }
    }

    PlasmaSupport.DataSource {
        id: gpuTelemetrySource
        engine: "executable"
        connectedSources: []
        property string scriptPath: Qt.resolvedUrl("../code/gpu_telemetry.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath
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
            var gpu0 = null
            var gpu1 = null
            for (var i = 0; i < payload.gpus.length; i++) {
                if (Number(payload.gpus[i].index) === 0) gpu0 = payload.gpus[i]
                if (Number(payload.gpus[i].index) === 1) gpu1 = payload.gpus[i]
            }
            root.applyGpuTelemetry(gpu0, 0)
            root.applyGpuTelemetry(gpu1, 1)
        }
    }

    PlasmaSupport.DataSource {
        id: topCpuSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: Qt.resolvedUrl("../code/cpu_process_snapshot.py").toString().replace("file://", "")
        property string command: "python3 " + scriptPath
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
                processes.push({ ram: (parseFloat(match[1]) / 1024).toFixed(1) + " MiB", name: match[2].trim() })
            }
            root.topRamProcesses = processes
        }
    }


    PlasmaSupport.DataSource {
        id: netDetectSource
        engine: "executable"
        connectedSources: []
        property string command: "sh -c 'set -- $(ip -o route show default 2>/dev/null); iface=$5; if [ -n \"$iface\" ]; then printf \"%s\\n\" \"$iface\"; exit 0; fi; for iface in $(ls /sys/class/net/); do case \"$iface\" in lo|docker*|br-*|veth*|tailscale*|wlxd*) continue ;; esac; printf \"%s\\n\" \"$iface\"; break; done'"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var iface = buffer.trim()
            buffer = ""
            disconnectSource(source)
            if (iface.length > 0 && iface !== root.netIf) {
                root.netIf = iface
                root.previousRxBytes = -1
                root.previousTxBytes = -1
                root.previousNetworkSampleMs = 0
                networkCountersSource.connectSource(networkCountersSource.command)
            }
        }
    }

    // Read cumulative byte counters directly from /proc/net/dev. KDE's dynamic
    // network Sensor bindings can remain stuck on the empty startup interface.
    PlasmaSupport.DataSource {
        id: networkCountersSource
        engine: "executable"
        connectedSources: []
        property string scriptPath: Qt.resolvedUrl("../code/network_counters.sh").toString().replace("file://", "")
        property string command: "sh " + scriptPath + " " + root.netIf
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
            if (fields.length !== 2) return
            var rxBytes = parseFloat(fields[0])
            var txBytes = parseFloat(fields[1])
            var now = Date.now()
            if (isNaN(rxBytes) || isNaN(txBytes)) return
            if (root.previousNetworkSampleMs > 0 && now > root.previousNetworkSampleMs) {
                var elapsedSeconds = (now - root.previousNetworkSampleMs) / 1000
                root.down = Math.max(0, rxBytes - root.previousRxBytes) / elapsedSeconds
                root.up = Math.max(0, txBytes - root.previousTxBytes) / elapsedSeconds
            } else {
                root.down = 0
                root.up = 0
            }
            root.previousRxBytes = rxBytes
            root.previousTxBytes = txBytes
            root.previousNetworkSampleMs = now
        }
    }

    Component.onCompleted: {
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

    Timer {
        interval: 900000
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
    Sensors.Sensor { sensorId: "cpu/all/usage"; enabled: true; onValueChanged: root.cpu = root.clamp(parseFloat(value)) }
    Sensors.Sensor { sensorId: "cpu/all/averageTemperature"; enabled: true; onValueChanged: root.cpuTemp = parseFloat(value) || root.cpuTemp }

    Sensors.Sensor { sensorId: "memory/physical/usedPercent"; enabled: true; onValueChanged: root.ram = root.clamp(parseFloat(value)) }
    Sensors.Sensor { sensorId: "memory/physical/used"; enabled: true; onValueChanged: root.ramUsedBytes = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "memory/physical/total"; enabled: true; onValueChanged: root.ramTotalBytes = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "os/system/uptime"; enabled: true; onValueChanged: root.uptimeSeconds = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "cpu/loadaverages/loadaverage1"; enabled: true; onValueChanged: root.loadAverage = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "disk/all/total"; enabled: true; onValueChanged: root.diskTotalBytes = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "cpu/all/coreCount"; enabled: true; onValueChanged: { root.cpuCoreCount = Math.round(parseFloat(value)) || 0 } }
}
