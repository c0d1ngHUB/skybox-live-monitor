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
    property real gpu: 0
    property real ram: 0
    property real ramUsedBytes: 0
    property real ramTotalBytes: 0
    property real cpuTemp: 0
    property real gpuTemp: 0
    property real gpuVramUsedMiB: 0
    property real gpuVramTotalMiB: 0
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
    property string hermesGatewayState: "UNKNOWN"
    property string hindsightState: "UNKNOWN"
    property string localLlmState: "UNKNOWN"
    property string localLlmModelName: ""
    property string gpuPowerDrawWatts: ""
    property string gpuPowerLimitWatts: ""
    property real diskUsedPercent: 0
    property real diskUsedBytes: 0
    property real diskTotalBytes: 0
    property string lastRefresh: "--:--"
    property string currentTime: refreshClock()
    property string dataStatus: "WAITING"
    property int staleAfterMs: 15000
    property var metricUpdateMs: ({
        cpuUsage: 0, cpuTemperature: 0,
        gpuUsage: 0, gpuTemperature: 0, gpuVram: 0,
        memoryPercent: 0, memoryUsed: 0, memoryTotal: 0,
        network: 0,
        diskPercent: 0, diskUsed: 0, diskTotal: 0,
        uptime: 0, loadAverage: 0
    })
    property int gpuProcessCount: 0
    property string gpuTopProcess: "NO ACTIVE COMPUTE WORKLOAD"
    // Two largest CPU, RAM, and GPU consumers, sampled every five seconds.
    property var topCpuProcesses: []
    property var previousCpuSamples: ({})
    property double previousCpuSampleMs: 0
    property bool cpuProcessUnavailable: false
    property var topRamProcesses: []
    property bool ramProcessUnavailable: false
    property var topGpuProcesses: []
    property bool gpuProcessUnavailable: false
    property int historySeconds: 120
    property var cpuHistory: []
    property var gpuHistory: []
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
    function networkAxisUnit(maxMbit) {
        return maxMbit < 1 ? "KBIT/S" : "MBIT/S"
    }
    function fmtNetworkAxisValue(value) {
        if (value >= 100) return Math.round(value).toString()
        if (value >= 10) return value.toFixed(1).replace(".0", "")
        return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")
    }
    function fmtNetworkLive(bytesPerSecond, scaleBytesPerSecond) {
        var bps = Number(bytesPerSecond) || 0
        var scale = Number(scaleBytesPerSecond) || 0
        if (bps <= 0 || scale <= 0) return "0 KBIT/S"
        var mbit = bps * 8 / 1000000
        return root.fmtNetworkAxisValue(mbit) + " " + root.networkAxisUnit(mbit)
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
    function markMetricFresh(metric) {
        var now = Date.now()
        var updates = Object.assign({}, root.metricUpdateMs)
        updates[metric] = now
        root.metricUpdateMs = updates
        // Only advance the header clock when every domain is fresh —
        // a stale domain must not masquerade as a recent refresh.
        var stale = MonitorLogic.staleDomains(now, updates, root.staleAfterMs)
        if (stale.length === 0) root.lastRefresh = root.refreshClock()
        root.updateDataStatus(now)
    }
    function updateDataStatus(nowMs) {
        var stale = MonitorLogic.staleDomains(nowMs, root.metricUpdateMs, root.staleAfterMs)
        if (stale.length === 6) root.dataStatus = "WAITING"
        else if (stale.length > 0) root.dataStatus = "STALE · " + stale.join(" · ")
        else root.dataStatus = "LIVE"
    }
    function dataStatusAgeText() {
        if (root.dataStatus !== "LIVE") return ""
        var newest = 0
        for (var key in root.metricUpdateMs) newest = Math.max(newest, Number(root.metricUpdateMs[key] || 0))
        if (newest <= 0) return ""
        return " · " + root.fmtAge((Date.now() - newest) / 1000) + " AGO"
    }
    function statusSeverity() {
        if (root.dataStatus.indexOf("STALE") >= 0) return "CRITICAL"
        if (root.dataStatus === "WAITING") return "WARNING"
        if (root.dataStatus === "LIVE") {
            if (root.cpuTemp >= 85 || root.gpuTemp >= 90 || root.ram >= 95 || root.vramPercent() >= 95 || root.diskUsedPercent >= 95) return "CRITICAL"
            if (root.cpuTemp >= 75 || root.gpuTemp >= 85 || root.ram >= 85 || root.vramPercent() >= 85 || root.diskUsedPercent >= 85) return "WARNING"
            var services = [root.hermesGatewayState, root.hindsightState, root.localLlmState]
            for (var i = 0; i < services.length; i++) {
                var state = MonitorLogic.normalizeServiceState(services[i])
                if (state === "OFFLINE") return "CRITICAL"
                if (state === "DEGRADED") return "WARNING"
            }
            var oauthState = root.openAiOauthState()
            if (oauthState === "OFFLINE") return "CRITICAL"
            if (oauthState === "DEGRADED") return "WARNING"
            return "OPERATIONAL"
        }
        return "CRITICAL"
    }
    function statusTone() {
        var severity = root.statusSeverity()
        if (severity === "OPERATIONAL") return root.cyan
        if (severity === "WARNING") return root.warning
        return root.critical
    }
    function statusBackground() {
        var severity = root.statusSeverity()
        if (severity === "OPERATIONAL") return Qt.rgba(0.15, 0.75, 0.80, 0.14)
        if (severity === "WARNING") return Qt.rgba(0.96, 0.74, 0.20, 0.18)
        return Qt.rgba(1.0, 0.38, 0.38, 0.18)
    }
    function statusLabel() {
        var severity = root.statusSeverity()
        if (severity === "OPERATIONAL") return "● ALL SYSTEMS OPERATIONAL"
        if (severity === "WARNING") return "▲ SYSTEM WARNING"
        return root.dataStatus.indexOf("STALE") >= 0 ? "✕ TELEMETRY STALE" : "✕ CRITICAL"
    }

    function serviceToneColor(rawState) {
        var tone = MonitorLogic.serviceTone(MonitorLogic.normalizeServiceState(rawState))
        if (tone === "cyan") return root.cyan
        if (tone === "warning") return root.warning
        if (tone === "critical") return root.critical
        return root.muted
    }
    function serviceStateLabel(rawState) {
        var state = MonitorLogic.normalizeServiceState(rawState)
        return MonitorLogic.serviceSymbol(state) + " " + state
    }
    function localLlmStateLabel() {
        var state = MonitorLogic.normalizeServiceState(root.localLlmState)
        return MonitorLogic.serviceSymbol(state) + " " + state + (state === "OPERATIONAL" && root.localLlmModelName.length > 0 ? " · " + root.localLlmModelName : "")
    }
    function openAiOauthState() { return MonitorLogic.openAiOauthState(root.openAiActiveKeys, root.openAiTotalKeys) }
    function openAiOauthLabel() {
        if (root.openAiActiveKeys < 0 || root.openAiTotalKeys < 0) return "OPENAI 0AUTH ?/? verfügbar"
        return "OPENAI 0AUTH " + root.openAiActiveKeys + "/" + root.openAiTotalKeys + " verfügbar"
    }
    function openAiOauthTone() { return root.serviceToneColor(root.openAiOauthState()) }
    function gpuPowerText() {
        var draw = Number(root.gpuPowerDrawWatts)
        var limit = Number(root.gpuPowerLimitWatts)
        if (!isFinite(draw) || !isFinite(limit)) return ""
        return "POWER " + draw.toFixed(0) + "/" + limit.toFixed(0) + " W"
    }

    function vramPercent() {
        if (root.gpuVramTotalMiB <= 0) return 0
        return 100 * root.gpuVramUsedMiB / root.gpuVramTotalMiB
    }
    function diskFreeBytes() { return Math.max(0, root.diskTotalBytes - root.diskUsedBytes) }

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
        return Qt.rgba(0.63, 0.78, 0.85, 0.55)
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
            border.width: 2
            border.color: root.violet
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
            border.color: root.cyan
            opacity: 0.40
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
                    Text { text: "AIEX · LOCAL · CEST"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                }
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 0
                    Text { id: headerClock; text: root.currentTime; color: root.ink; font.family: "DejaVu Sans"; font.bold: true; font.pixelSize: 18; font.letterSpacing: 1 }
                }
                Rectangle {
                    id: telemetryStatus
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    visible: true
                    radius: 9
                    border.width: 1
                    border.color: root.statusTone()
                    color: root.statusBackground()
                    implicitHeight: 24
                    implicitWidth: Math.min(280, parent.width * 0.42)
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 28
                        horizontalAlignment: Text.AlignHCenter
                        text: root.statusLabel() + root.dataStatusAgeText()
                        color: root.statusTone()
                        font.family: "DejaVu Sans Mono"
                        font.bold: true
                        font.pixelSize: 15
                        elide: Text.ElideRight
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.bottomMargin: -8; color: root.cyan; opacity: 0.45 }

            // --- AI SERVICES section ---
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 170
                Layout.minimumHeight: 166
                clip: true
                Column {
                    anchors.fill: parent
                    anchors.topMargin: 6
                    spacing: 10
                    Text { text: "AI SERVICES"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                    Row {
                        width: parent.width
                        spacing: 10
                        Rectangle {
                            width: (parent.width - 20) / 3
                            height: 44
                            radius: 12
                            color: Qt.rgba(0, 0, 0, 0.22)
                            border.width: 1
                            border.color: root.serviceToneColor(root.hermesGatewayState)
                            Column {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2
                                Text { text: "GATEWAY"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Text { text: root.serviceStateLabel(root.hermesGatewayState); color: root.serviceToneColor(root.hermesGatewayState); font.family: "DejaVu Sans Mono"; font.pixelSize: 17; font.bold: true }
                            }
                        }
                        Rectangle {
                            width: (parent.width - 20) / 3
                            height: 44
                            radius: 12
                            color: Qt.rgba(0, 0, 0, 0.22)
                            border.width: 1
                            border.color: root.serviceToneColor(root.hindsightState)
                            Column {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2
                                Text { text: "HINDSIGHT"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Text { text: root.serviceStateLabel(root.hindsightState); color: root.serviceToneColor(root.hindsightState); font.family: "DejaVu Sans Mono"; font.pixelSize: 17; font.bold: true }
                            }
                        }
                        Rectangle {
                            width: (parent.width - 20) / 3
                            height: 44
                            radius: 12
                            color: Qt.rgba(0, 0, 0, 0.22)
                            border.width: 1
                            border.color: root.serviceToneColor(root.localLlmState)
                            Column {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2
                                Text { text: "QWEN 3.8"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Text {
                                    text: root.localLlmStateLabel()
                                    color: root.serviceToneColor(root.localLlmState)
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 15
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                    Item {
                        width: parent.width
                        height: 18
                        visible: root.openAiActiveKeys >= 0 && root.openAiTotalKeys >= 0
                        Text { anchors.right: parent.right; text: root.openAiOauthLabel(); visible: root.openAiActiveKeys >= 0 && root.openAiTotalKeys >= 0; color: root.openAiOauthTone(); font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideRight }
                    }
                }
            }


            // --- SYSTEM LOAD section ---
            // P1d: All sections use fillHeight + preferredHeight ratio — no fixed heights
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Primary graph: 50% shorter than the previous responsive panel.
                Layout.preferredHeight: 218
                Layout.minimumHeight: 180
                clip: true

                Text { id: headline; anchors.left: parent.left; anchors.top: parent.top; text: "SYSTEM LOAD"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                Row {
                    id: computeLegend
                    anchors.left: parent.left; anchors.top: headline.bottom; anchors.topMargin: 26; spacing: 18
                    Text { text: "━━ GPU LOAD"; color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
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
                        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(160,200,216,0.25)"
                        for (var i = 0; i < 3; i++) { var y = height * i / 2; ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(width, y); ctx.stroke() }
                        ctx.strokeStyle = "rgba(160,200,216,0.15)"; ctx.lineWidth = 1
                        var midTick = plotLeft + chartWidth / 2
                        ctx.beginPath(); ctx.moveTo(midTick, 0); ctx.lineTo(midTick, height); ctx.stroke()


                        // Labels live inside the Canvas gutter so they are never clipped.
                        ctx.fillStyle = root.muted.toString()
                        ctx.font = "12px 'DejaVu Sans Mono'"
                        ctx.textAlign = "right"
                        ctx.fillText("100%", plotLeft - 6, 10)
                        ctx.fillText("50%", plotLeft - 6, height / 2 + 4)
                        ctx.fillText("0%", plotLeft - 6, height - 2)

                        function plot(data, color, fillColor) {
                            if (data.length < 2) return
                            var firstX = MonitorLogic.historyX(0, data.length, chartWidth, root.historySeconds) + plotLeft
                            var lastX = MonitorLogic.historyX(data.length - 1, data.length, chartWidth, root.historySeconds) + plotLeft
                            ctx.beginPath()
                            for (var f = 0; f < data.length; f++) {
                                var fx = plotLeft + MonitorLogic.historyX(f, data.length, chartWidth, root.historySeconds)
                                var fy = height - (root.clamp(data[f]) / 100) * height
                                if (f === 0) ctx.moveTo(fx, fy); else ctx.lineTo(fx, fy)
                            }
                            ctx.lineTo(lastX, height)
                            ctx.lineTo(firstX, height)
                            ctx.closePath()
                            ctx.fillStyle = fillColor
                            ctx.fill()
                            ctx.strokeStyle = color; ctx.lineWidth = 3
                            ctx.beginPath()
                            for (var j = 0; j < data.length; j++) {
                                var x = plotLeft + MonitorLogic.historyX(j, data.length, chartWidth, root.historySeconds)
                                var y = height - (root.clamp(data[j]) / 100) * height
                                if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                            }
                            ctx.stroke()
                        }
                        plot(root.gpuHistory, root.violet, "rgba(219,145,255,0.12)")
                    }
                }
                Item {
                    id: computeTimeline
                    anchors.left: computeGraph.left
                    anchors.right: computeGraph.right
                    anchors.bottom: parent.bottom
                    height: 20
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−2 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "−1 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "NOW · " + Math.round(root.historyFillProgress() * 100) + "% FILLED" : "NOW"; color: root.historyFilling() ? root.warning : root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                }
            }

            // --- Metric cards with sparklines ---
            // P1d: Use fillHeight + preferredHeight instead of fixed 118px
            Row {
                Layout.fillWidth: true
                // Preserve space for utilization, temperature and one secondary bar.
                Layout.preferredHeight: 150
                spacing: 16

                Repeater {
                    model: [
                        {label:"CPU", value:Math.round(root.cpu) + "%", detail:Math.round(root.cpuTemp) + "°C", color:root.cyan, detailColor:root.tempColor(root.cpuTemp, root.muted), healthLevel: root.cpuTemp >= 85 ? 2 : (root.cpuTemp >= 75 ? 1 : 0)},
                        {label:"GPU", value:Math.round(root.gpu) + "%", detail:Math.round(root.gpuTemp) + "°C", detail2:"VRAM " + Math.round(root.vramPercent()) + "%", detail2Color:root.usageColor(root.vramPercent(), root.cyan), detail3:root.gpuTopProcess, color:root.violet, detailColor:root.gpuTempColor(root.gpuTemp, root.muted), healthLevel: (root.gpuTemp >= 90 || root.vramPercent() >= 95) ? 2 : ((root.gpuTemp >= 85 || root.vramPercent() >= 85) ? 1 : 0), vramFill: Math.max(0, Math.min(100, root.vramPercent())), powerText: root.gpuPowerText()},
                        {label:"RAM", value:Math.round(root.ram) + "%", detail:root.fmtMemoryPair(root.ramUsedBytes, root.ramTotalBytes), color:root.blue, detailColor:root.ram >= 85 ? root.warning : root.muted, healthLevel: root.ram >= 95 ? 2 : (root.ram >= 85 ? 1 : 0)}
                    ]
                    delegate: Rectangle {
                        width: (parent.width - 32) / 3; height: parent.height; radius: 16
                        clip: true
                        color: Qt.rgba(0.035, 0.22, 0.34, 0.9); border.width: modelData.healthLevel > 0 ? 2 : 1; border.color: root.metricBorderColor(modelData); opacity: 0.95

                        // A fixed KPI column prevents large percentages from colliding
                        // with process names at narrow dashboard widths.
                        Item {
                            anchors.fill: parent; anchors.margins: 12
                            Column {
                                id: metricKpi
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: Math.max(116, parent.width * 0.34); spacing: 4
                                Text { text: modelData.label; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }

                                // --- GPU card: utilization as the large metric + temperature secondary ---
                                Text {
                                    visible: modelData.label === "GPU"
                                    text: modelData.value
                                    color: root.ink
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: 28
                                    font.bold: true
                                }
                                Text {
                                    visible: modelData.label === "GPU"
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
                                    visible: modelData.label === "GPU"
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
                                    visible: modelData.label === "GPU" && !!modelData.powerText
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
                                    visible: modelData.label === "CPU"
                                    text: modelData.value
                                    color: root.ink
                                    font.family: "DejaVu Sans"
                                    font.pixelSize: 28
                                    font.bold: true
                                }
                                Text {
                                    visible: modelData.label === "CPU"
                                    text: modelData.detail
                                    color: modelData.detailColor
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                // --- RAM card: original layout ---
                                Text { visible: modelData.label === "RAM"; text: modelData.value; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 28; font.bold: true }
                                Text { visible: modelData.label === "RAM"; width: parent.width - 4; text: modelData.detail; color: modelData.detailColor; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                Text { visible: modelData.label === "RAM" && !!modelData.detail2; text: modelData.detail2 || ""; color: modelData.detail2Color || root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                // VRAM fill bar — only on GPU card (original position, kept for non-GPU safety)
                                Item {
                                    width: parent.width - 4
                                    height: 8
                                    visible: modelData.label !== "GPU" && modelData.vramFill !== undefined
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
                            Rectangle { id: metricDivider; anchors.left: metricKpi.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 1; color: modelData.color; opacity: 0.38 }
                            Column {
                                id: processDetails
                                anchors.left: metricDivider.right; anchors.leftMargin: 10
                                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                                spacing: 3
                                property string metricLabel: modelData.label
                                property var processes: metricLabel === "CPU" ? root.topCpuProcesses : (metricLabel === "RAM" ? root.topRamProcesses : root.topGpuProcesses)
                                property string heading: metricLabel === "CPU" ? "TOP · CPU %" : (metricLabel === "GPU" ? "TOP · VRAM" : "TOP · RAM")
                                Text { width: parent.width; text: MonitorLogic.serviceSymbol(MonitorLogic.normalizeServiceState(modelData.label === "GPU" ? root.localLlmState : "OPERATIONAL")) + " " + processDetails.heading; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideRight }
                                Repeater {
                                    model: parent.processes
                                    delegate: Item {
                                        width: parent.width
                                        height: 17
                                        property var process: modelData
                                        property string displayValue: root.compactProcessValue(processDetails.metricLabel, processDetails.metricLabel === "CPU" ? process.cpu + "%" : (processDetails.metricLabel === "RAM" ? process.ram : process.gpu))
                                        Text {
                                            anchors.left: parent.left
                                            anchors.right: processValue.left
                                            anchors.rightMargin: 8
                                            text: root.shortProcessName(parent.process.name)
                                            color: index === 0 ? root.ink : root.muted
                                            font.family: "DejaVu Sans Mono"; font.pixelSize: 13
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            id: processValue
                                            anchors.right: parent.right
                                            text: parent.displayValue
                                            color: index === 0 ? root.ink : root.muted
                                            font.family: "DejaVu Sans Mono"; font.pixelSize: 13
                                            font.bold: true
                                            horizontalAlignment: Text.AlignRight
                                        }
                                    }
                                }
                                Text {
                                    visible: parent.processes.length === 0
                                    text: processDetails.metricLabel === "CPU" ? (root.cpuProcessUnavailable ? "UNAVAILABLE" : "SAMPLING…") : (processDetails.metricLabel === "RAM" ? (root.ramProcessUnavailable ? "UNAVAILABLE" : "SAMPLING…") : (root.gpuProcessUnavailable ? "UNAVAILABLE" : (root.gpuProcessCount === 0 ? "NO ACTIVE WORKLOAD" : "SAMPLING…")))
                                    color: root.muted
                                    font.family: "DejaVu Sans Mono"
                                    font.pixelSize: 13
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
                        Text { text: root.fmtNetworkLive(root.down, root.downloadScaleBytesPerSecond); color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
                        Text { text: root.fmtNetworkLive(root.up, root.uploadScaleBytesPerSecond); color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
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
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "DOWNLOAD · " + root.networkAxisUnit(root.downloadScaleBytesPerSecond * 8 / 1000000); color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
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
                                var axisFactor = maxMbit < 1 ? 1000 : 1
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
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "NOW · " + Math.round(root.historyFillProgress() * 100) + "% FILLED" : "NOW"; color: root.historyFilling() ? root.warning : root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                        }
                    }

                    // Upload sub-chart (P3: filled-area style)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "UPLOAD · " + root.networkAxisUnit(root.uploadScaleBytesPerSecond * 8 / 1000000); color: root.violet; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
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
                                var axisFactor = maxMbit < 1 ? 1000 : 1
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
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "NOW · " + Math.round(root.historyFillProgress() * 100) + "% FILLED" : "NOW"; color: root.historyFilling() ? root.warning : root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
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
                    color: Qt.rgba(0.035, 0.22, 0.34, 0.9); border.width: 1; border.color: root.blue; opacity: 0.95
                    Column {
                        anchors.fill: parent; anchors.margins: 10; spacing: 2
                        Item {
                            width: parent.width
                            height: 26
                            Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "SYSTEM DISK /"; color: root.blue; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: Math.round(root.diskUsedPercent) + "%"; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 20; font.bold: true }
                        }
                        // P0c: elide to prevent truncation; P1: 12px minimum
                        Text { width: parent.width; elide: Text.ElideRight; text: "FREE " + root.fmtDisk(root.diskFreeBytes()) + " · USED " + root.fmtDisk(root.diskUsedBytes); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                        Rectangle {
                            width: parent.width; height: 6; radius: 3; color: Qt.rgba(0.5, 0.5, 0.5, 0.3)
                            Rectangle {
                                width: parent.width * Math.min(1, root.diskUsedPercent / 100); height: parent.height; radius: 3; color: root.blue
                            }
                        }
                    }
                }
                Rectangle {
                    width: (parent.width - 16) / 2; height: parent.height; radius: 12
                    clip: true
                    color: Qt.rgba(0.035, 0.22, 0.34, 0.9); border.width: 1; border.color: root.cyan; opacity: 0.95
                    Column {
                        anchors.fill: parent; anchors.margins: 10; spacing: 2
                        Item {
                            width: parent.width
                            height: 26
                            Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "UPTIME"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                            Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.fmtUptime(root.uptimeSeconds); color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 20; font.bold: true }
                        }
                        Column {
                            id: systemMetaRow
                            width: parent.width
                            spacing: 2
                            Row {
                                width: parent.width
                                Text { width: parent.width / 2; text: "LOAD AVG 1M " + root.loadAverage.toFixed(2); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                                Text { width: parent.width / 2; text: "PROCESSES " + root.processCount; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; horizontalAlignment: Text.AlignRight }
                            }
                            Text { width: parent.width; text: "LÄNGSTER KI-RUN" + (root.hermesMaxThinkService.length > 0 ? " · " + root.hermesMaxThinkService : "") + ": " + root.fmtDuration(root.hermesMaxThinkSeconds); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideRight }
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
                root.gpuHistory = root.push(root.gpuHistory, root.gpu)
                root.ramHistory = root.push(root.ramHistory, root.ram)
                root.downHistory = root.push(root.downHistory, root.down)
                root.upHistory = root.push(root.upHistory, root.up)
                root.updateDataStatus(Date.now())
                computeGraph.requestPaint()
                downGraph.requestPaint()
                upGraph.requestPaint()
            }
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
            var match = buffer.trim().match(/^(\d+)\s+(\d+)$/)
            buffer = ""
            disconnectSource(source)
            if (!match) return
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
            root.localLlmState = (payload.local_llm || "UNKNOWN").toUpperCase()
            root.localLlmModelName = payload.local_llm_model || ""
            root.gpuPowerDrawWatts = payload.gpu_power_draw_w || ""
            root.gpuPowerLimitWatts = payload.gpu_power_limit_w || ""
            if (Number(payload.openai_oauth_available) >= 0) root.openAiActiveKeys = Number(payload.openai_oauth_available)
            if (Number(payload.openai_oauth_total) >= 0) root.openAiTotalKeys = Number(payload.openai_oauth_total)
        }
    }

    PlasmaSupport.DataSource {
        id: vramSource
        engine: "executable"
        connectedSources: []
        property string command: "nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var memory = MonitorLogic.parseNvidiaMemory(buffer, 0)
            buffer = ""
            disconnectSource(source)
            if (!memory) {
                return
            }
            root.gpuVramUsedMiB = memory.usedMiB
            root.gpuVramTotalMiB = memory.totalMiB
            root.markMetricFresh("gpuVram")
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
            var processes = MonitorLogic.cpuProcessRates(root.previousCpuSamples, samples, elapsedMs, 2)
            root.topCpuProcesses = processes
            root.previousCpuSamples = currentByPid
            root.previousCpuSampleMs = now
        }
    }

    PlasmaSupport.DataSource {
        id: topRamSource
        engine: "executable"
        connectedSources: []
        property string command: "ps -eo rss=,comm= --sort=-rss | head -2"
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
            for (var i = 0; i < lines.length && processes.length < 2; i++) {
                var match = lines[i].trim().match(/^([0-9]+)\s+(.+)$/)
                if (!match) continue
                processes.push({ ram: (parseFloat(match[1]) / 1024).toFixed(1) + " MiB", name: match[2].trim() })
            }
            root.topRamProcesses = processes
        }
    }

    PlasmaSupport.DataSource {
        id: topGpuSource
        engine: "executable"
        connectedSources: []
        property string command: "nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            root.gpuProcessUnavailable = Number(data["exit code"]) !== 0
            if (root.gpuProcessUnavailable) {
                buffer = ""
                disconnectSource(source)
                root.topGpuProcesses = []
                root.gpuProcessCount = 0
                root.gpuTopProcess = "GPU PROCESS DATA UNAVAILABLE"
                return
            }
            var lines = buffer.trim().split("\n")
            buffer = ""
            disconnectSource(source)
            var candidates = []
            var validProcessCount = 0
            for (var i = 0; i < lines.length; i++) {
                var parts = lines[i].split(",")
                var name = (parts[0] || "GPU PROCESS").trim().split("/").pop()
                var mib = parseFloat((parts[1] || "").trim())
                if (!isNaN(mib)) {
                    validProcessCount++
                    candidates.push({ name: name, mib: mib })
                }
            }
            candidates.sort(function(a, b) { return b.mib - a.mib })
            var processes = []
            for (var j = 0; j < candidates.length; j++) {
                if (processes.length < 2) processes.push({ name: candidates[j].name, gpu: root.fmtVram(candidates[j].mib) })
            }
            root.topGpuProcesses = processes
            root.gpuProcessCount = validProcessCount
            root.gpuTopProcess = processes.length > 0 ? processes[0].name + " · " + processes[0].gpu : "NO ACTIVE COMPUTE WORKLOAD"
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
            root.markMetricFresh("network")
        }
    }

    Component.onCompleted: {
        vramSource.connectSource(vramSource.command)
        topGpuSource.connectSource(topGpuSource.command)
        topCpuSource.connectSource(topCpuSource.command)
        topRamSource.connectSource(topRamSource.command)
        processCountSource.connectSource(processCountSource.command)
        hermesThinkSource.connectSource(hermesThinkSource.command)
        openAiKeysSource.connectSource(openAiKeysSource.command)
        aiServicesSource.connectSource(aiServicesSource.command)
        netDetectSource.connectSource(netDetectSource.command)
        root.currentTime = root.refreshClock()
        root.lastRefresh = root.refreshClock()
    }

    Timer {
        interval: 5000
        running: true
        repeat: true
        onTriggered: vramSource.connectSource(vramSource.command)
    }

    Timer {
        interval: 5000
        running: true
        repeat: true
        onTriggered: topGpuSource.connectSource(topGpuSource.command)
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

    // Static ksystemstats bindings for the remaining telemetry domains.
    Sensors.Sensor { sensorId: "cpu/all/usage"; enabled: true; onValueChanged: { root.cpu = root.clamp(parseFloat(value)); root.markMetricFresh("cpuUsage") } }
    Sensors.Sensor { sensorId: "cpu/all/averageTemperature"; enabled: true; onValueChanged: { root.cpuTemp = parseFloat(value) || root.cpuTemp; root.markMetricFresh("cpuTemperature") } }
    Sensors.Sensor { sensorId: "gpu/gpu1/usage"; enabled: true; onValueChanged: { root.gpu = root.clamp(parseFloat(value)); root.markMetricFresh("gpuUsage") } }
    Sensors.Sensor { sensorId: "gpu/gpu1/temperature"; enabled: true; onValueChanged: { root.gpuTemp = parseFloat(value) || root.gpuTemp; root.markMetricFresh("gpuTemperature") } }
    Sensors.Sensor { sensorId: "memory/physical/usedPercent"; enabled: true; onValueChanged: { root.ram = root.clamp(parseFloat(value)); root.markMetricFresh("memoryPercent") } }
    Sensors.Sensor { sensorId: "memory/physical/used"; enabled: true; onValueChanged: { root.ramUsedBytes = parseFloat(value) || 0; root.markMetricFresh("memoryUsed") } }
    Sensors.Sensor { sensorId: "memory/physical/total"; enabled: true; onValueChanged: { root.ramTotalBytes = parseFloat(value) || 0; root.markMetricFresh("memoryTotal") } }
    Sensors.Sensor { sensorId: "os/system/uptime"; enabled: true; onValueChanged: { root.uptimeSeconds = parseFloat(value) || 0; root.markMetricFresh("uptime") } }
    Sensors.Sensor { sensorId: "cpu/loadaverages/loadaverage1"; enabled: true; onValueChanged: { root.loadAverage = parseFloat(value) || 0; root.markMetricFresh("loadAverage") } }
    Sensors.Sensor { sensorId: "disk/all/usedPercent"; enabled: true; onValueChanged: { root.diskUsedPercent = parseFloat(value) || 0; root.markMetricFresh("diskPercent") } }
    Sensors.Sensor { sensorId: "disk/all/used"; enabled: true; onValueChanged: { root.diskUsedBytes = parseFloat(value) || 0; root.markMetricFresh("diskUsed") } }
    Sensors.Sensor { sensorId: "disk/all/total"; enabled: true; onValueChanged: { root.diskTotalBytes = parseFloat(value) || 0; root.markMetricFresh("diskTotal") } }
}
