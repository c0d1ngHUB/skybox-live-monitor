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
    // Independent fixed network chart scales, expressed internally as bytes/s.
    property real downloadScaleBytesPerSecond: 600000000 / 8
    property real uploadScaleBytesPerSecond: 50000000 / 8
    property real previousRxBytes: -1
    property real previousTxBytes: -1
    property double previousNetworkSampleMs: 0
    property real uptimeSeconds: 0
    property real loadAverage: 0
    property int processCount: 0
    property real hermesMaxThinkSeconds: 0
    property real diskUsedPercent: 0
    property real diskUsedBytes: 0
    property real diskTotalBytes: 0
    property string lastRefresh: "--:--:--"
    property string currentTime: refreshClock()
    property string dataStatus: "WAITING FOR DATA"
    property int staleAfterMs: 15000
    property var domainUpdateMs: ({cpu: 0, gpu: 0, memory: 0, network: 0, disk: 0, system: 0})
    property int gpuProcessCount: 0
    property string gpuTopProcess: "NO ACTIVE COMPUTE WORKLOAD"
    // Two largest CPU, RAM, and GPU consumers, sampled every five seconds.
    property var topCpuProcesses: []
    property var topRamProcesses: []
    property var topGpuProcesses: []
    property string vramReleaseStatus: "UNLOAD OLLAMA"
    property bool vramReleaseInProgress: false
    property bool vramReleaseArmed: false
    property int historySeconds: 300
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
    function fmtRate(v) {
        if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + " MB/s"
        if (v >= 1024) return (v / 1024).toFixed(1) + " KB/s"
        return Math.round(v) + " B/s"
    }
    function fmtGiB(v) {
        if (!v || v < 1) return "--"
        return (v / 1024 / 1024 / 1024).toFixed(1) + " GiB"
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
    function netPeak() {
        var peak = 16 * 1024
        for (var n = 0; n < root.downHistory.length; n++) peak = Math.max(peak, root.downHistory[n], root.upHistory[n])
        return peak
    }
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
        return ("0" + now.getHours()).slice(-2) + ":" + ("0" + now.getMinutes()).slice(-2) + ":" + ("0" + now.getSeconds()).slice(-2)
    }
    function markDataFresh(domain) {
        var now = Date.now()
        var updates = Object.assign({}, root.domainUpdateMs)
        updates[domain] = now
        root.domainUpdateMs = updates
        // Only advance the header clock when every domain is fresh —
        // a stale domain must not masquerade as a recent refresh.
        var stale = MonitorLogic.staleDomains(now, updates, root.staleAfterMs)
        if (stale.length === 0) root.lastRefresh = root.refreshClock()
        root.updateDataStatus(now)
    }
    function updateDataStatus(nowMs) {
        var stale = MonitorLogic.staleDomains(nowMs, root.domainUpdateMs, root.staleAfterMs)
        if (stale.length === 6) root.dataStatus = "WAITING FOR DATA"
        else if (stale.length > 0) root.dataStatus = "STALE · " + stale.join(" · ")
        else root.dataStatus = "UPDATED " + root.lastRefresh
    }

    function vramPercent() {
        if (root.gpuVramTotalMiB <= 0) return 0
        return 100 * root.gpuVramUsedMiB / root.gpuVramTotalMiB
    }
    function diskFreeBytes() { return Math.max(0, root.diskTotalBytes - root.diskUsedBytes) }
    function requestOllamaUnload() {
        if (root.vramReleaseInProgress) return
        if (!root.vramReleaseArmed) {
            root.vramReleaseArmed = true
            root.vramReleaseStatus = "CONFIRM UNLOAD"
            releaseConfirmTimer.restart()
            return
        }
        root.vramReleaseArmed = false
        root.vramReleaseInProgress = true
        root.vramReleaseStatus = "UNLOADING…"
        releaseModelsSource.connectSource(releaseModelsSource.command)
    }
    function usageColor(value, normalColor) {
        if (value >= 95) return root.critical
        if (value >= 85) return root.warning
        return normalColor
    }
    function shortProcessName(name) {
        var leaf = (name || "PROCESS").split("/").pop()
        return leaf.length > 12 ? leaf.slice(0, 11) + "…" : leaf
    }
    function compactProcessValue(metricLabel, value) {
        if (metricLabel === "GPU") return (value || "").replace(" GiB", "G").replace(" MiB", "M")
        if (metricLabel === "RAM") return (value || "").replace(" MiB", "M")
        return value || ""
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
            // Reduced by another 20 percentage points: 44% transparency (56% opacity).
            opacity: 0.56
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
                Layout.preferredHeight: 34
                Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "SKYBOX"; color: root.cyan; font.family: "DejaVu Sans"; font.bold: true; font.pixelSize: 28; font.letterSpacing: 3 }
                Text {
                    id: headerClock
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.currentTime
                    color: root.cyan
                    font.family: "DejaVu Sans"
                    font.bold: true
                    font.pixelSize: 28
                    font.letterSpacing: 3
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.cyan; opacity: 0.45 }


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
                    Text { text: "● CPU LOAD"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "● GPU LOAD"; color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
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
                        var tick1 = plotLeft + chartWidth / 5; var tick2 = plotLeft + chartWidth * 4 / 5
                        ctx.beginPath(); ctx.moveTo(tick1, 0); ctx.lineTo(tick1, height); ctx.stroke()
                        ctx.beginPath(); ctx.moveTo(tick2, 0); ctx.lineTo(tick2, height); ctx.stroke()


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
                        // GPU is painted last so its solid trace remains in the foreground.
                        plot(root.cpuHistory, root.cyan, "rgba(150,245,246,0.12)")
                        plot(root.gpuHistory, root.violet, "rgba(219,145,255,0.12)")
                    }
                }
                Item {
                    id: computeTimeline
                    anchors.left: computeGraph.left
                    anchors.right: computeGraph.right
                    anchors.bottom: parent.bottom
                    height: 20
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; visible: !root.historyFilling() }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "2.5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; visible: !root.historyFilling() }
                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "FILLING " + Math.round(root.historyFillProgress() * 100) + "%" : "NOW"; color: root.historyFilling() ? root.warning : root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                }
            }

            // --- Metric cards with sparklines ---
            // P1d: Use fillHeight + preferredHeight instead of fixed 118px
            Row {
                Layout.fillWidth: true
                // Preserve space for both GPU temperature and VRAM metadata.
                Layout.preferredHeight: 126
                spacing: 16

                Repeater {
                    model: [
                        {label:"CPU", value:Math.round(root.cpu) + "%", detail:Math.round(root.cpuTemp) + "°C", color:root.cyan, detailColor:root.tempColor(root.cpuTemp, root.muted), healthLevel: root.cpuTemp >= 85 ? 2 : (root.cpuTemp >= 75 ? 1 : 0)},
                        {label:"GPU", value:Math.round(root.gpu) + "%", detail:"VRAM " + Math.round(root.vramPercent()) + "% · " + root.fmtCompactVram(root.gpuVramUsedMiB) + " / " + root.fmtCompactVram(root.gpuVramTotalMiB), detail2:Math.round(root.gpuTemp) + "°C", detail2Color:root.tempColor(root.gpuTemp, root.muted), detail3:root.gpuTopProcess, color:root.violet, detailColor:root.usageColor(root.vramPercent(), root.violet), healthLevel: (root.gpuTemp >= 85 || root.vramPercent() >= 95) ? 2 : ((root.gpuTemp >= 75 || root.vramPercent() >= 85) ? 1 : 0)},
                        {label:"RAM", value:Math.round(root.ram) + "%", detail:root.fmtCompactCapacity(root.ramUsedBytes) + " / " + root.fmtCompactCapacity(root.ramTotalBytes), color:root.blue, detailColor:root.ram >= 85 ? root.warning : root.muted, healthLevel: root.ram >= 95 ? 2 : (root.ram >= 85 ? 1 : 0)}
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
                                Text { text: modelData.value; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 28; font.bold: true }
                                Text { width: parent.width - 4; text: modelData.detail; color: modelData.detailColor; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                                Text { width: parent.width - 4; text: modelData.detail2 || ""; visible: !!modelData.detail2; color: modelData.detail2Color || root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight }
                            }
                            Rectangle { id: metricDivider; anchors.left: metricKpi.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 1; color: modelData.color; opacity: 0.38 }
                            Column {
                                id: processDetails
                                anchors.left: metricDivider.right; anchors.leftMargin: 10
                                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                                spacing: 3
                                property string metricLabel: modelData.label
                                property var processes: metricLabel === "CPU" ? root.topCpuProcesses : (metricLabel === "RAM" ? root.topRamProcesses : root.topGpuProcesses)
                                property string heading: "TOP PROCESSES"
                                Text { width: parent.width; text: processDetails.heading; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true; elide: Text.ElideRight }
                                Repeater {
                                    model: parent.processes
                                    delegate: Text {
                                        width: parent.width
                                        text: root.shortProcessName(modelData.name) + "  " + root.compactProcessValue(processDetails.metricLabel, processDetails.metricLabel === "CPU" ? modelData.cpu + "%" : (processDetails.metricLabel === "RAM" ? modelData.ram : modelData.gpu))
                                        color: index === 0 ? root.ink : root.muted
                                        font.family: "DejaVu Sans Mono"; font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }
                                }
                                Text { visible: parent.processes.length === 0; text: "SAMPLING…"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
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
                Row {
                    id: networkLegend
                    anchors.left: parent.left; anchors.top: networkTitle.bottom; anchors.topMargin: 26; spacing: 18
                    Text { text: "↓ DOWNLOAD"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "↑ UPLOAD"; color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                }
                Column {
                    id: networkDataBlock
                    anchors.right: parent.right; anchors.top: networkTitle.bottom; anchors.topMargin: 22; spacing: 4
                    Row {
                        id: networkLiveValues
                        anchors.right: parent.right; spacing: 16
                        Text { text: "↓ " + root.fmtRate(root.down); color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
                        Text { text: "↑ " + root.fmtRate(root.up); color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 14 }
                    }
                }

                Row {
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: networkDataBlock.bottom; anchors.topMargin: 10
                    anchors.bottom: networkTimeline.top; anchors.bottomMargin: 6
                    spacing: 12

                    // Download sub-chart (P3: filled-area style for better visibility)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "↓ MBIT/S"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                        Canvas {
                            id: downGraph
                            anchors.fill: parent
                            anchors.topMargin: 16
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                ctx.imageSmoothingEnabled = true
                                var plotLeft = 44
                                var chartWidth = width - plotLeft
                                ctx.strokeStyle = "rgba(160,200,216,0.22)"; ctx.lineWidth = 1
                                ctx.fillStyle = root.muted.toString()
                                ctx.font = "11px 'DejaVu Sans Mono'"
                                ctx.textAlign = "right"
                                for (var i = 0; i <= 6; i++) {
                                    var y = height * i / 6
                                    ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(width, y); ctx.stroke()
                                    ctx.fillText(String(600 - i * 100), plotLeft - 6, Math.max(10, Math.min(height - 2, y + 4)))
                                }
                                ctx.strokeStyle = "rgba(160,200,216,0.12)"; ctx.lineWidth = 1
                                var tick1 = plotLeft + chartWidth / 5; var tick2 = plotLeft + chartWidth * 4 / 5
                                ctx.beginPath(); ctx.moveTo(tick1, 0); ctx.lineTo(tick1, height); ctx.stroke()
                                ctx.beginPath(); ctx.moveTo(tick2, 0); ctx.lineTo(tick2, height); ctx.stroke()
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
                    }

                    // Upload sub-chart (P3: filled-area style)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "↑ MBIT/S"; color: root.violet; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                        Canvas {
                            id: upGraph
                            anchors.fill: parent
                            anchors.topMargin: 16
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                ctx.imageSmoothingEnabled = true
                                var plotLeft = 44
                                var chartWidth = width - plotLeft
                                ctx.strokeStyle = "rgba(160,200,216,0.22)"; ctx.lineWidth = 1
                                ctx.fillStyle = root.muted.toString()
                                ctx.font = "11px 'DejaVu Sans Mono'"
                                ctx.textAlign = "right"
                                for (var i = 0; i <= 5; i++) {
                                    var y = height * i / 5
                                    ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(width, y); ctx.stroke()
                                    ctx.fillText(String(50 - i * 10), plotLeft - 6, Math.max(10, Math.min(height - 2, y + 4)))
                                }
                                ctx.strokeStyle = "rgba(160,200,216,0.12)"; ctx.lineWidth = 1
                                var tick1 = plotLeft + chartWidth / 5; var tick2 = plotLeft + chartWidth * 4 / 5
                                ctx.beginPath(); ctx.moveTo(tick1, 0); ctx.lineTo(tick1, height); ctx.stroke()
                                ctx.beginPath(); ctx.moveTo(tick2, 0); ctx.lineTo(tick2, height); ctx.stroke()
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
                    }
                }
                Text {
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: 14
                    visible: root.networkIdle()
                    text: "IDLE · NO TRANSFER IN LAST 5 MIN"
                    color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true
                }
                Item {
                    id: networkTimeline
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 16
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; visible: !root.historyFilling() }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "2.5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; visible: !root.historyFilling() }
                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: root.historyFilling() ? "FILLING " + Math.round(root.historyFillProgress() * 100) + "%" : "NOW"; color: root.historyFilling() ? root.warning : root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
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
                            Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "DISK USED"; color: root.blue; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
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
                        Row {
                            id: systemMetaRow
                            width: parent.width
                            height: 20
                            spacing: 14
                            Text { text: "LOAD " + root.loadAverage.toFixed(2); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12 }
                            Text { text: "PROC " + root.processCount; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12 }
                            Text { text: "MAX THINK: " + root.fmtDuration(root.hermesMaxThinkSeconds); color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12; font.bold: true }
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
        property string command: "python3 " + scriptPath + " --db $HOME/.hermes/state.db"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var seconds = parseFloat(buffer.trim())
            buffer = ""
            disconnectSource(source)
            if (!isNaN(seconds)) root.hermesMaxThinkSeconds = Math.max(0, seconds)
        }
    }

    // Current top two CPU consumers. ps %CPU is an OS rolling sample, ideal for
    // an operational display without keeping a separate per-process history.
    PlasmaSupport.DataSource {
        id: topCpuSource
        engine: "executable"
        connectedSources: []
        property string command: "ps -eo pcpu=,comm= --sort=-pcpu | head -2"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var lines = buffer.trim().split("\n")
            buffer = ""
            disconnectSource(source)
            var processes = []
            for (var i = 0; i < lines.length && processes.length < 2; i++) {
                var match = lines[i].trim().match(/^([0-9.]+)\s+(.+)$/)
                if (!match) continue
                processes.push({ cpu: parseFloat(match[1]).toFixed(1), name: match[2].trim() })
            }
            root.topCpuProcesses = processes
        }
    }

    PlasmaSupport.DataSource {
        id: topRamSource
        engine: "executable"
        connectedSources: []
        // RSS excludes shared-page double counting and is reported in human-friendly MiB.
        property string command: "ps -eo rss=,comm= --sort=-rss | head -2"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
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
            if (!memory) return
            root.gpuVramUsedMiB = memory.usedMiB
            root.gpuVramTotalMiB = memory.totalMiB
            root.markDataFresh("gpu")
        }
    }

    // Unload only models currently served by local Ollama; arbitrary GPU processes are never terminated.
    PlasmaSupport.DataSource {
        id: releaseModelsSource
        engine: "executable"
        connectedSources: []
        property string command: "sh -c 'for dep in curl jq ollama; do command -v \"$dep\" >/dev/null 2>&1 || { echo \"MISSING DEPENDENCY: $dep\"; exit 10; }; done; response=$(curl --max-time 5 -fsS http://127.0.0.1:11434/api/ps) || { echo \"OLLAMA UNREACHABLE\"; exit 11; }; printf \"%s\" \"$response\" | jq -e \".models | arrays\" >/dev/null 2>&1 || { echo \"OLLAMA INVALID RESPONSE\"; exit 12; }; models=$(printf \"%s\" \"$response\" | jq -r \".models[]?.name\"); if [ -z \"$models\" ]; then echo \"NO OLLAMA MODEL LOADED\"; exit 0; fi; failed=0; unloaded=0; printf \"%s\\n\" \"$models\" | while IFS= read -r model; do if OLLAMA_HOST=http://127.0.0.1:11434 ollama stop \"$model\" >/dev/null 2>&1; then echo \"OK\"; else echo \"FAIL\"; fi; done > /tmp/skybox-ollama-unload.$$; unloaded=$(grep -c \"^OK$\" /tmp/skybox-ollama-unload.$$); failed=$(grep -c \"^FAIL$\" /tmp/skybox-ollama-unload.$$); rm -f /tmp/skybox-ollama-unload.$$; if [ $failed -ne 0 ]; then echo \"UNLOAD PARTIAL: $unloaded\"; exit 13; fi; echo \"UNLOADED ALL: $unloaded\"'"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var output = buffer.trim()
            var exitCode = data["exit code"]
            buffer = ""
            disconnectSource(source)
            root.vramReleaseInProgress = false
            if (exitCode === 0 && output.indexOf("UNLOADED ALL:") === 0) root.vramReleaseStatus = "OLLAMA UNLOADED"
            else if (exitCode === 0 && output === "NO OLLAMA MODEL LOADED") root.vramReleaseStatus = "NO OLLAMA MODEL"
            else if (output.indexOf("MISSING DEPENDENCY:") === 0) root.vramReleaseStatus = "MISSING DEPENDENCY"
            else if (output === "OLLAMA UNREACHABLE") root.vramReleaseStatus = "OLLAMA UNREACHABLE"
            else if (output.indexOf("UNLOAD PARTIAL:") === 0) root.vramReleaseStatus = "UNLOAD PARTIAL"
            else root.vramReleaseStatus = "UNLOAD FAILED"
            releaseRefreshTimer.start()
            releaseResetTimer.start()
        }
    }

    // Two largest active GPU compute consumers by allocated VRAM.
    PlasmaSupport.DataSource {
        id: topGpuSource
        engine: "executable"
        connectedSources: []
        property string command: "nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
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

    // Detect the default-route interface, with a safe physical-interface fallback.
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
            root.markDataFresh("network")
        }
    }

    Component.onCompleted: {
        vramSource.connectSource(vramSource.command)
        topGpuSource.connectSource(topGpuSource.command)
        topCpuSource.connectSource(topCpuSource.command)
        topRamSource.connectSource(topRamSource.command)
        processCountSource.connectSource(processCountSource.command)
        hermesThinkSource.connectSource(hermesThinkSource.command)
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
        id: releaseRefreshTimer
        interval: 750
        repeat: false
        onTriggered: {
            vramSource.connectSource(vramSource.command)
            topGpuSource.connectSource(topGpuSource.command)
        }
    }

    Timer {
        id: releaseConfirmTimer
        interval: 5000
        repeat: false
        onTriggered: { if (root.vramReleaseArmed) { root.vramReleaseArmed = false; root.vramReleaseStatus = "UNLOAD OLLAMA" } }
    }

    Timer {
        id: releaseResetTimer
        interval: 4500
        repeat: false
        onTriggered: { root.vramReleaseArmed = false; root.vramReleaseStatus = "UNLOAD OLLAMA" }
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
    Sensors.Sensor { sensorId: "cpu/all/usage"; enabled: true; onValueChanged: { root.cpu = root.clamp(parseFloat(value)); root.markDataFresh("cpu") } }
    Sensors.Sensor { sensorId: "cpu/all/averageTemperature"; enabled: true; onValueChanged: { root.cpuTemp = parseFloat(value) || root.cpuTemp; root.markDataFresh("cpu") } }
    Sensors.Sensor { sensorId: "gpu/gpu1/usage"; enabled: true; onValueChanged: { root.gpu = root.clamp(parseFloat(value)); root.markDataFresh("gpu") } }
    Sensors.Sensor { sensorId: "gpu/gpu1/temperature"; enabled: true; onValueChanged: { root.gpuTemp = parseFloat(value) || root.gpuTemp; root.markDataFresh("gpu") } }
    Sensors.Sensor { sensorId: "memory/physical/usedPercent"; enabled: true; onValueChanged: { root.ram = root.clamp(parseFloat(value)); root.markDataFresh("memory") } }
    Sensors.Sensor { sensorId: "memory/physical/used"; enabled: true; onValueChanged: { root.ramUsedBytes = parseFloat(value) || 0; root.markDataFresh("memory") } }
    Sensors.Sensor { sensorId: "memory/physical/total"; enabled: true; onValueChanged: { root.ramTotalBytes = parseFloat(value) || 0; root.markDataFresh("memory") } }
    Sensors.Sensor { sensorId: "os/system/uptime"; enabled: true; onValueChanged: { root.uptimeSeconds = parseFloat(value) || 0; root.markDataFresh("system") } }
    Sensors.Sensor { sensorId: "cpu/loadaverages/loadaverage1"; enabled: true; onValueChanged: { root.loadAverage = parseFloat(value) || 0; root.markDataFresh("system") } }
    Sensors.Sensor { sensorId: "disk/all/usedPercent"; enabled: true; onValueChanged: { root.diskUsedPercent = parseFloat(value) || 0; root.markDataFresh("disk") } }
    Sensors.Sensor { sensorId: "disk/all/used"; enabled: true; onValueChanged: { root.diskUsedBytes = parseFloat(value) || 0; root.markDataFresh("disk") } }
    Sensors.Sensor { sensorId: "disk/all/total"; enabled: true; onValueChanged: { root.diskTotalBytes = parseFloat(value) || 0; root.markDataFresh("disk") } }
}
