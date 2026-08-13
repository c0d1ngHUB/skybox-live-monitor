import QtQuick 2.15

import QtQuick.Layouts 1.15
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid 2.0
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport
import org.kde.ksysguard.sensors 1.0 as Sensors

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
    property real uptimeSeconds: 0
    property real loadAverage: 0
    property real diskUsedPercent: 0
    property real diskUsedBytes: 0
    property real diskTotalBytes: 0
    property string lastRefresh: "--:--:--"
    property int gpuProcessCount: 0
    property string gpuTopProcess: "NO ACTIVE COMPUTE WORKLOAD"
    // Five largest CPU, RAM, and GPU consumers, sampled every five seconds.
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
    // Auto-detect: first physical network interface
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
    // P0: Fixed VRAM formatting — use MiB directly from nvidia-smi, convert to GiB only above 1024
    function fmtVram(mib) {
        if (!mib || mib < 1) return "--"
        if (mib >= 1024) return (mib / 1024).toFixed(1) + " GiB"
        return Math.round(mib) + " MiB"
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
    function refreshClock() {
        var now = new Date()
        return ("0" + now.getHours()).slice(-2) + ":" + ("0" + now.getMinutes()).slice(-2) + ":" + ("0" + now.getSeconds()).slice(-2)
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
        var leaf = (name || "PROCESS").split("/").pop()
        return leaf.length > 12 ? leaf.slice(0, 11) + "…" : leaf
    }
    function compactProcessValue(metricLabel, value) {
        if (metricLabel === "GPU") return (value || "").replace(" GiB", "G").replace(" MiB", "M")
        if (metricLabel === "RAM") return (value || "").replace(" MiB", "M")
        return value || ""
    }
    function gpuCauseSummary() {
        if (root.topGpuProcesses.length === 0) return "NO GPU COMPUTE WORKLOAD DETECTED"
        var items = []
        for (var i = 0; i < root.topGpuProcesses.length && i < 2; i++) items.push(root.topGpuProcesses[i].name + " " + root.topGpuProcesses[i].gpu)
        return items.join(" · ")
    }
    function healthState() {
        if (root.gpuTemp >= 85) return { level: 2, label: "GPU CRITICAL", detail: Math.round(root.gpu) + "% LOAD · " + Math.round(root.gpuTemp) + "°C · VRAM " + Math.round(root.vramPercent()) + "%", cause: root.gpuCauseSummary(), color: root.critical }
        if (root.cpuTemp >= 85) return { level: 2, label: "SYSTEM ALERT", detail: "CPU TEMP " + Math.round(root.cpuTemp) + "°C", color: root.critical }
        if (root.vramPercent() >= 95) return { level: 2, label: "VRAM CRITICAL", detail: "VRAM " + Math.round(root.vramPercent()) + "% · " + root.fmtVram(root.gpuVramUsedMiB) + " / " + root.fmtVram(root.gpuVramTotalMiB), cause: root.gpuCauseSummary(), color: root.critical }
        if (root.ram >= 95) return { level: 2, label: "SYSTEM ALERT", detail: "MEMORY " + Math.round(root.ram) + "% USED", color: root.critical }
        if (root.diskUsedPercent >= 95) return { level: 2, label: "SYSTEM ALERT", detail: "DISK SPACE " + Math.round(root.diskUsedPercent) + "% USED", color: root.critical }
        if (root.gpuTemp >= 75) return { level: 1, label: "SYSTEM ATTENTION", detail: "GPU TEMP " + Math.round(root.gpuTemp) + "°C", color: root.warning }
        if (root.cpuTemp >= 75) return { level: 1, label: "SYSTEM ATTENTION", detail: "CPU TEMP " + Math.round(root.cpuTemp) + "°C", color: root.warning }
        if (root.vramPercent() >= 85) return { level: 1, label: "SYSTEM ATTENTION", detail: "GPU VRAM " + Math.round(root.vramPercent()) + "% USED", color: root.warning }
        if (root.ram >= 85) return { level: 1, label: "SYSTEM ATTENTION", detail: "MEMORY " + Math.round(root.ram) + "% USED", color: root.warning }
        if (root.diskUsedPercent >= 85) return { level: 1, label: "SYSTEM ATTENTION", detail: "DISK SPACE " + Math.round(root.diskUsedPercent) + "% USED", color: root.warning }
        return { level: 0, label: "NORMAL", detail: "0 ALERTS · VRAM " + Math.round(root.vramPercent()) + "% · DISK " + Math.round(root.diskUsedPercent) + "%", cause: "", color: root.cyan }
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

        // Compact fixed-content cockpit: charts use half the former height and
        // the frame hugs the content instead of consuming the full display.
        Rectangle {
            id: frame
            anchors.centerIn: parent
            width: parent.width - 36
            height: Math.min(parent.height - 24, content.implicitHeight + 68)
            radius: 30
            color: "#0C4A70"
            border.width: 2
            border.color: root.violet
            opacity: 0.20
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

        // Let the content determine the dashboard height; do not stretch into
        // unused vertical screen space.
        ColumnLayout {
            id: content
            anchors.centerIn: frame
            width: frame.width - 68
            height: implicitHeight
            spacing: 12

            // --- Header ---
            RowLayout {
                Layout.fillWidth: true
                Text { text: "SKYBOX"; color: root.cyan; font.family: "DejaVu Sans"; font.bold: true; font.pixelSize: 28; font.letterSpacing: 3 }
                Item { Layout.fillWidth: true }
                Text { text: "LIVE SYSTEM · UPDATED " + root.lastRefresh; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.letterSpacing: 1.5 }
                Rectangle {
                    id: healthChip
                    Layout.leftMargin: 12
                    Layout.preferredWidth: healthChipText.implicitWidth + 22
                    Layout.preferredHeight: 28
                    radius: 14
                    color: Qt.rgba(0.035, 0.22, 0.34, 0.9)
                    border.width: 1
                    border.color: root.healthState().color
                    Row {
                        anchors.centerIn: parent
                        spacing: 7
                        Rectangle { width: 6; height: 6; radius: 3; color: root.healthState().color }
                        Text { id: healthChipText; text: root.healthState().label + (root.healthState().level === 0 ? " · 0 ALERTS" : ""); color: root.healthState().color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                    }
                }
                Rectangle {
                    id: releaseVramButton
                    Layout.leftMargin: 8
                    Layout.preferredWidth: releaseVramButtonText.implicitWidth + 20
                    Layout.preferredHeight: 28
                    radius: 14
                    color: releaseVramMouse.containsMouse ? Qt.rgba(0.30, 0.10, 0.42, 0.95) : Qt.rgba(0.12, 0.06, 0.20, 0.92)
                    border.width: 1
                    border.color: root.violet
                    opacity: releaseVramMouse.pressed ? 0.72 : 1
                    Text {
                        id: releaseVramButtonText
                        anchors.centerIn: parent
                        text: root.vramReleaseStatus
                        color: root.violet
                        font.family: "DejaVu Sans Mono"
                        font.pixelSize: 12
                        font.bold: true
                    }
                    MouseArea {
                        id: releaseVramMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        enabled: !root.vramReleaseInProgress
                        onClicked: {
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
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.cyan; opacity: 0.45 }

            Rectangle {
                id: healthBanner
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 66 : 0
                visible: root.healthState().level > 0
                radius: 12
                color: Qt.rgba(0.035, 0.22, 0.34, 0.9)
                border.width: 1
                border.color: root.healthState().color
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    spacing: 12
                    Text { text: root.healthState().label; color: root.healthState().color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                    Rectangle { Layout.preferredWidth: 5; Layout.preferredHeight: 5; radius: 3; color: root.healthState().color }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text { text: root.healthState().detail; color: root.ink; font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                        Text { visible: root.healthState().cause.length > 0; text: "CAUSE · " + root.healthState().cause; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                }
            }

            // --- SYSTEM LOAD section ---
            // P1d: All sections use fillHeight + preferredHeight ratio — no fixed heights
            Item {
                Layout.fillWidth: true
                // Primary graph: 50% shorter than the previous responsive panel.
                Layout.preferredHeight: 184
                clip: true

                Text { id: headline; anchors.left: parent.left; anchors.top: parent.top; text: "SYSTEM LOAD"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                // P1: Minimum 12px for secondary text
                Text { anchors.left: parent.left; anchors.top: headline.bottom; anchors.topMargin: 4; text: "CPU · GPU UTILIZATION"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14 }
                Row {
                    id: computeLegend
                    anchors.left: parent.left; anchors.top: headline.bottom; anchors.topMargin: 26; spacing: 18
                    Text { text: "● CPU LOAD"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "● GPU LOAD"; color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                }
                Row {
                    anchors.right: parent.right; anchors.verticalCenter: computeLegend.verticalCenter; spacing: 14
                    Text {
                        visible: root.historyPeak(root.gpuHistory) >= 75
                        text: "GPU PEAK " + Math.round(root.historyPeak(root.gpuHistory)) + "% · " + root.fmtAge(root.peakAge(root.gpuHistory)) + " AGO"
                        color: root.violet; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true
                    }
                    Text { text: "LAST 5 MIN · FIXED SCALE · 0–100%"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14 }
                }

                // P0a: Y-axis labels positioned INSIDE the graph area, not with negative margins
                // P1: Minimum 12px for axis labels
                Canvas {
                    id: computeGraph
                    anchors.left: parent.left; anchors.leftMargin: 44
                    anchors.right: parent.right
                    anchors.top: computeLegend.bottom; anchors.topMargin: 10
                    height: 110

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

                        // Operational thresholds keep the fixed scale actionable.
                        ctx.setLineDash([4, 4]); ctx.lineWidth = 1
                        ctx.strokeStyle = "rgba(255,209,102,0.55)"
                        var warningY = height - height * 0.75
                        ctx.beginPath(); ctx.moveTo(plotLeft, warningY); ctx.lineTo(width, warningY); ctx.stroke()
                        ctx.strokeStyle = "rgba(255,107,107,0.62)"
                        var criticalY = height - height * 0.85
                        ctx.beginPath(); ctx.moveTo(plotLeft, criticalY); ctx.lineTo(width, criticalY); ctx.stroke()
                        ctx.setLineDash([])

                        // Labels live inside the Canvas gutter so they are never clipped.
                        ctx.fillStyle = root.muted.toString()
                        ctx.font = "12px 'DejaVu Sans Mono'"
                        ctx.textAlign = "right"
                        ctx.fillText("100%", plotLeft - 6, 10)
                        ctx.fillText("50%", plotLeft - 6, height / 2 + 4)
                        ctx.fillText("0%", plotLeft - 6, height - 2)

                        function plot(data, color, fillColor) {
                            if (data.length < 2) return
                            ctx.beginPath()
                            for (var f = 0; f < data.length; f++) {
                                var fx = plotLeft + f * chartWidth / (root.historySeconds - 1)
                                var fy = height - (root.clamp(data[f]) / 100) * height
                                if (f === 0) ctx.moveTo(fx, fy); else ctx.lineTo(fx, fy)
                            }
                            ctx.lineTo(plotLeft + chartWidth, height)
                            ctx.lineTo(plotLeft, height)
                            ctx.closePath()
                            ctx.fillStyle = fillColor
                            ctx.fill()
                            ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.beginPath()
                            for (var j = 0; j < data.length; j++) {
                                var x = plotLeft + j * chartWidth / (root.historySeconds - 1)
                                var y = height - (root.clamp(data[j]) / 100) * height
                                if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                            }
                            ctx.stroke()
                        }
                        plot(root.cpuHistory, root.cyan, "rgba(150,245,246,0.12)")
                        plot(root.gpuHistory, root.violet, "rgba(219,145,255,0.12)")
                    }
                }
                Item {
                    id: computeTimeline
                    anchors.left: computeGraph.left
                    anchors.right: computeGraph.right
                    anchors.bottom: parent.bottom
                    height: 16
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "2.5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "NOW"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
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
                        {label:"GPU", value:Math.round(root.gpu) + "%", detail:"VRAM " + Math.round(root.vramPercent()) + "% · " + root.fmtVram(root.gpuVramUsedMiB) + " / " + root.fmtVram(root.gpuVramTotalMiB), detail2:Math.round(root.gpuTemp) + "°C · " + root.gpuProcessCount + " WORKLOAD" + (root.gpuProcessCount === 1 ? "" : "S"), detail3:root.gpuTopProcess, color:root.violet, detailColor:root.usageColor(root.vramPercent(), root.violet), healthLevel: (root.gpuTemp >= 85 || root.vramPercent() >= 95) ? 2 : ((root.gpuTemp >= 75 || root.vramPercent() >= 85) ? 1 : 0)},
                        {label:"RAM", value:Math.round(root.ram) + "%", detail:root.fmtGiB(root.ramUsedBytes) + " / " + root.fmtGiB(root.ramTotalBytes), color:root.blue, detailColor:root.ram >= 85 ? root.warning : root.muted, healthLevel: root.ram >= 95 ? 2 : (root.ram >= 85 ? 1 : 0)}
                    ]
                    delegate: Rectangle {
                        width: (parent.width - 32) / 3; height: parent.height; radius: 16
                        clip: true
                        color: Qt.rgba(0.035, 0.22, 0.34, 0.9); border.width: modelData.healthLevel > 0 ? 2 : 1; border.color: root.metricBorderColor(modelData); opacity: 0.95

                        // Every compute card reserves its detail area for the five largest
                        // consumers of the matching resource, refreshed every five seconds.
                        Row {
                            anchors.fill: parent; anchors.margins: 12; spacing: 10
                            Column {
                                width: 48; spacing: 4
                                Text { text: modelData.label; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Text { text: modelData.value; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 28; font.bold: true }
                                Text { text: modelData.detail; color: modelData.detailColor; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                            }
                            Rectangle { width: 1; height: parent.height; color: modelData.color; opacity: 0.38 }
                            Column {
                                id: processDetails
                                width: parent.width - 59; spacing: 2
                                property string metricLabel: modelData.label
                                property var processes: metricLabel === "CPU" ? root.topCpuProcesses : (metricLabel === "RAM" ? root.topRamProcesses : root.topGpuProcesses)
                                property string heading: "TOP 2 " + metricLabel + " PROCESSES"
                                Text { text: processDetails.heading; color: modelData.color; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                                Repeater {
                                    model: parent.processes
                                    delegate: Text {
                                        width: parent.width
                                        text: root.shortProcessName(modelData.name) + " " + root.compactProcessValue(processDetails.metricLabel, processDetails.metricLabel === "CPU" ? modelData.cpu + "%" : (processDetails.metricLabel === "RAM" ? modelData.ram : modelData.gpu))
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
                // Network charts: 50% shorter than the previous responsive panel.
                Layout.preferredHeight: 202
                clip: true

                Text { id: networkTitle; anchors.left: parent.left; anchors.top: parent.top; text: "NETWORK"; color: root.ink; font.bold: true; font.pixelSize: 30; font.letterSpacing: 2 }
                Text { anchors.left: parent.left; anchors.top: networkTitle.bottom; anchors.topMargin: 4; text: "LAN · " + root.netIf + " · LIVE THROUGHPUT · AUTO SCALE"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 14 }
                Row {
                    id: networkLegend
                    anchors.left: parent.left; anchors.top: networkTitle.bottom; anchors.topMargin: 26; spacing: 18
                    Text { text: "↓ DOWNLOAD"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "↑ UPLOAD"; color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                }
                Row {
                    anchors.right: parent.right; anchors.verticalCenter: networkLegend.verticalCenter; spacing: 12
                    Text { text: "↓ " + root.fmtRate(root.down); color: root.cyan; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "↑ " + root.fmtRate(root.up); color: root.violet; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "PEAK " + root.fmtRate(root.netPeak()); color: root.blue; font.family: "DejaVu Sans Mono"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "LAST 5 MIN · AUTO SCALE"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                }

                Row {
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: networkLegend.bottom; anchors.topMargin: 12
                    anchors.bottom: networkTimeline.top; anchors.bottomMargin: 6
                    spacing: 12

                    // Download sub-chart (P3: filled-area style for better visibility)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.left: parent.left; anchors.top: parent.top; anchors.topMargin: -2; text: "↓"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                        Canvas {
                            id: downGraph
                            anchors.fill: parent
                            anchors.topMargin: 16
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                ctx.imageSmoothingEnabled = true
                                ctx.strokeStyle = "rgba(160,200,216,0.22)"; ctx.lineWidth = 1
                                for (var i = 0; i < 3; i++) { var y = height * i / 2; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke() }
                                ctx.strokeStyle = "rgba(160,200,216,0.12)"; ctx.lineWidth = 1
                                var tick1 = width / 5; var tick2 = width * 4 / 5
                                ctx.beginPath(); ctx.moveTo(tick1, 0); ctx.lineTo(tick1, height); ctx.stroke()
                                ctx.beginPath(); ctx.moveTo(tick2, 0); ctx.lineTo(tick2, height); ctx.stroke()
                                var peak = root.netPeak()
                                var d = root.downHistory
                                if (d.length < 2) return
                                // P3: Filled area under the line
                                ctx.beginPath()
                                for (var j = 0; j < d.length; j++) {
                                    var x = j * width / (root.historySeconds - 1)
                                    var y = height - (d[j] / peak) * height
                                    if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                }
                                ctx.lineTo(width, height)
                                ctx.lineTo(0, height)
                                ctx.closePath()
                                ctx.fillStyle = "rgba(150,245,246,0.15)"
                                ctx.fill()
                                // Line on top
                                ctx.strokeStyle = root.cyan; ctx.lineWidth = 2.5; ctx.beginPath()
                                for (var k = 0; k < d.length; k++) {
                                    var x2 = k * width / (root.historySeconds - 1)
                                    var y2 = height - (d[k] / peak) * height
                                    if (k === 0) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2)
                                }
                                ctx.stroke()
                            }
                        }
                    }

                    // Upload sub-chart (P3: filled-area style)
                    Item {
                        width: (parent.width - 12) / 2; height: parent.height
                        Text { anchors.right: parent.right; anchors.top: parent.top; anchors.topMargin: -2; text: "↑"; color: root.violet; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                        Canvas {
                            id: upGraph
                            anchors.fill: parent
                            anchors.topMargin: 16
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                ctx.imageSmoothingEnabled = true
                                ctx.strokeStyle = "rgba(160,200,216,0.22)"; ctx.lineWidth = 1
                                for (var i = 0; i < 3; i++) { var y = height * i / 2; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke() }
                                ctx.strokeStyle = "rgba(160,200,216,0.12)"; ctx.lineWidth = 1
                                var tick1 = width / 5; var tick2 = width * 4 / 5
                                ctx.beginPath(); ctx.moveTo(tick1, 0); ctx.lineTo(tick1, height); ctx.stroke()
                                ctx.beginPath(); ctx.moveTo(tick2, 0); ctx.lineTo(tick2, height); ctx.stroke()
                                var peak = root.netPeak()
                                var d = root.upHistory
                                if (d.length < 2) return
                                // P3: Filled area
                                ctx.beginPath()
                                for (var j = 0; j < d.length; j++) {
                                    var x = j * width / (root.historySeconds - 1)
                                    var y = height - (d[j] / peak) * height
                                    if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                }
                                ctx.lineTo(width, height)
                                ctx.lineTo(0, height)
                                ctx.closePath()
                                ctx.fillStyle = "rgba(219,145,255,0.15)"
                                ctx.fill()
                                // Line on top
                                ctx.strokeStyle = root.violet; ctx.lineWidth = 2.5; ctx.beginPath()
                                for (var k = 0; k < d.length; k++) {
                                    var x2 = k * width / (root.historySeconds - 1)
                                    var y2 = height - (d[k] / peak) * height
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
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: "−5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; text: "2.5 MIN"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "NOW"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
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
                        Row {
                            width: parent.width
                            Text { text: "DISK"; color: root.blue; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                            Item { width: 1; height: 1; Layout.fillWidth: true }
                            Text { text: Math.round(root.diskUsedPercent) + "%"; color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 20; font.bold: true }
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
                        Row {
                            width: parent.width
                            Text { text: "SYSTEM"; color: root.cyan; font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true }
                            Item { width: 1; height: 1; Layout.fillWidth: true }
                            Text { text: root.fmtUptime(root.uptimeSeconds); color: root.ink; font.family: "DejaVu Sans"; font.pixelSize: 20; font.bold: true }
                        }
                        Text { width: parent.width; elide: Text.ElideRight; text: "LOAD " + root.loadAverage.toFixed(2) + " · RTX PRO 4000"; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                        Text { width: parent.width; elide: Text.ElideRight; text: "DEBIAN 13 · PLASMA 6 · REFRESH " + root.lastRefresh; color: root.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 13 }
                    }
                }
            }
        }

        Timer {
            interval: 1000
            running: true
            repeat: true
            onTriggered: {
                root.cpuHistory = root.push(root.cpuHistory, root.cpu)
                root.gpuHistory = root.push(root.gpuHistory, root.gpu)
                root.ramHistory = root.push(root.ramHistory, root.ram)
                root.downHistory = root.push(root.downHistory, root.down)
                root.upHistory = root.push(root.upHistory, root.up)
                root.lastRefresh = root.refreshClock()
                computeGraph.requestPaint()
                downGraph.requestPaint()
                upGraph.requestPaint()
            }
        }
    }

    // Current top five CPU consumers. ps %CPU is an OS rolling sample, ideal for
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
        property string command: "nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var values = buffer.trim().split(",")
            buffer = ""
            disconnectSource(source)
            if (values.length !== 2) return
            root.gpuVramUsedMiB = parseFloat(values[0]) || 0
            root.gpuVramTotalMiB = parseFloat(values[1]) || 0
        }
    }

    // Unload only models currently served by local Ollama; arbitrary GPU processes are never terminated.
    PlasmaSupport.DataSource {
        id: releaseModelsSource
        engine: "executable"
        connectedSources: []
        property string command: "sh -c 'models=$(curl -fsS http://127.0.0.1:11434/api/ps 2>/dev/null | jq -r \".models[]?.name\"); if [ -n \"$models\" ]; then printf \"%s\\n\" \"$models\" | while IFS= read -r model; do ollama stop \"$model\" && echo \"UNLOADED $model\"; done; else echo \"NO OLLAMA MODEL LOADED\"; fi'"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var output = buffer.trim()
            buffer = ""
            disconnectSource(source)
            root.vramReleaseInProgress = false
            root.vramReleaseStatus = output.indexOf("UNLOADED ") === 0 ? "OLLAMA UNLOADED" : (output === "NO OLLAMA MODEL LOADED" ? "NO OLLAMA MODEL" : "UNLOAD FAILED")
            releaseRefreshTimer.start()
            releaseResetTimer.start()
        }
    }

    // Two largest active GPU compute consumers by allocated VRAM.
    PlasmaSupport.DataSource {
        id: topGpuSource
        engine: "executable"
        connectedSources: []
        property string command: "sh -c 'nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits | sort -t, -k2,2nr | head -2'"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var lines = buffer.trim().split("\n")
            buffer = ""
            disconnectSource(source)
            var processes = []
            for (var i = 0; i < lines.length && processes.length < 2; i++) {
                var parts = lines[i].split(",")
                var name = (parts[0] || "GPU PROCESS").trim().split("/").pop()
                var mib = parseFloat((parts[1] || "").trim())
                if (!isNaN(mib)) processes.push({ name: name, gpu: root.fmtVram(mib) })
            }
            root.topGpuProcesses = processes
            root.gpuProcessCount = processes.length
            root.gpuTopProcess = processes.length > 0 ? processes[0].name + " · " + processes[0].gpu : "NO ACTIVE COMPUTE WORKLOAD"
        }
    }

    // Auto-detect network interface by listing /sys/class/net
    PlasmaSupport.DataSource {
        id: netDetectSource
        engine: "executable"
        connectedSources: []
        property string command: "ls /sys/class/net/ | grep -v lo | grep -v docker | grep -v br- | grep -v veth | grep -v tailscale | grep -v wlxd | head -1"
        property string buffer: ""
        onNewData: function(source, data) {
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            var iface = buffer.trim()
            buffer = ""
            disconnectSource(source)
            if (iface.length > 0 && iface !== root.netIf) {
                root.netIf = iface
            }
        }
    }

    Component.onCompleted: {
        vramSource.connectSource(vramSource.command)
        topGpuSource.connectSource(topGpuSource.command)
        topCpuSource.connectSource(topCpuSource.command)
        topRamSource.connectSource(topRamSource.command)
        netDetectSource.connectSource(netDetectSource.command)
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

    // Re-detect network interface every 30s in case of hotplug
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: netDetectSource.connectSource(netDetectSource.command)
    }

    // Dynamic sensor bindings use root.netIf
    Sensors.Sensor { sensorId: "cpu/all/usage"; enabled: true; onValueChanged: root.cpu = root.clamp(parseFloat(value)) }
    Sensors.Sensor { sensorId: "cpu/all/averageTemperature"; enabled: true; onValueChanged: root.cpuTemp = parseFloat(value) || root.cpuTemp }
    Sensors.Sensor { sensorId: "gpu/gpu1/usage"; enabled: true; onValueChanged: root.gpu = root.clamp(parseFloat(value)) }
    Sensors.Sensor { sensorId: "gpu/gpu1/temperature"; enabled: true; onValueChanged: root.gpuTemp = parseFloat(value) || root.gpuTemp }
    Sensors.Sensor { sensorId: "memory/physical/usedPercent"; enabled: true; onValueChanged: root.ram = root.clamp(parseFloat(value)) }
    Sensors.Sensor { sensorId: "memory/physical/used"; enabled: true; onValueChanged: root.ramUsedBytes = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "memory/physical/total"; enabled: true; onValueChanged: root.ramTotalBytes = parseFloat(value) || 0 }
    Sensors.Sensor {
        sensorId: "network/" + root.netIf + "/download"
        enabled: true
        onValueChanged: root.down = parseFloat(value) || 0
    }
    Sensors.Sensor {
        sensorId: "network/" + root.netIf + "/upload"
        enabled: true
        onValueChanged: root.up = parseFloat(value) || 0
    }
    Sensors.Sensor { sensorId: "os/system/uptime"; enabled: true; onValueChanged: root.uptimeSeconds = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "cpu/loadaverages/loadaverage1"; enabled: true; onValueChanged: root.loadAverage = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "disk/all/usedPercent"; enabled: true; onValueChanged: root.diskUsedPercent = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "disk/all/used"; enabled: true; onValueChanged: root.diskUsedBytes = parseFloat(value) || 0 }
    Sensors.Sensor { sensorId: "disk/all/total"; enabled: true; onValueChanged: root.diskTotalBytes = parseFloat(value) || 0 }
}
