import QtQuick 2.15
import "../code/monitor_logic.js" as MonitorLogic
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport

Rectangle {
    id: health
    objectName: "blackwellHealth"
    property color ink: "#e4f5ff"
    property color muted: "#8ba8b5"
    property color accent: "#db91ff"
    property color warning: "#ffca70"
    property color critical: "#ff7373"
    property bool expanded: false
    property bool pollingEnabled: true
    property var payload: null
    property double nowMs: Date.now()
    property string session: String(Date.now()) + "-" + Math.floor(Math.random() * 1000000000000).toString(36)
    readonly property bool fresh: !!payload && payload.available === true && typeof payload.stamp === "number" && nowMs / 1000 >= payload.stamp && nowMs / 1000 - payload.stamp <= 25
    readonly property var current: fresh && payload.current ? payload.current : ({})
    // ECC is deliberately off and not monitored. An unreadable kernel journal
    // only disables Xid capture; it is shown as a hint, not as a warning state.
    readonly property bool uncertain: !fresh || !complete(current.aer) || current.thermal === null
    readonly property bool journalReadable: fresh && payload.journal_available === true
    readonly property bool thermal: fresh && current.thermal === true
    readonly property bool eventWarning: {
        var events = payload && payload.events ? payload.events : []
        for (var i = 0; i < events.length; i++) {
            if (nowMs / 1000 - events[i].time <= 120 && ["aer", "xid", "thermal"].indexOf(events[i].kind) >= 0) return true
        }
        return false
    }
    implicitHeight: expanded ? 255 : 64
    radius: 12
    color: Qt.rgba(0.035, 0.22, 0.34, 0.82)
    border.color: thermal ? critical : (uncertain || eventWarning ? warning : Qt.rgba(0.86, 0.57, 1, 0.35))
    clip: true

    function number(value, digits) {
        return value === null || value === undefined || !isFinite(Number(value)) ? "--" : Number(value).toFixed(digits || 0)
    }
    function complete(values) {
        if (!values || Object.keys(values).length === 0) return false
        for (var key in values) if (values[key] === null || values[key] === undefined) return false
        return true
    }
    function total(values) {
        if (!complete(values)) return "?"
        var sum = 0
        for (var key in values) sum += Number(values[key])
        return String(sum)
    }
    function reasonsText() {
        if (!fresh || current.reasons === null || current.reasons === undefined) return "LIMITS UNBEKANNT"
        var labels = {sw_power_cap: "POWER-LIMIT", hw_thermal_slowdown: "THERMISCH", sw_thermal_slowdown: "THERMISCH (SW)", gpu_idle: "LEERLAUF", reliability: "RELIABILITY", hw_slowdown: "HW-SLOWDOWN", hw_power_brake_slowdown: "POWER-BRAKE"}
        var reasons = current.reasons.map(function(key) { return labels[key] || key.toUpperCase() })
        if (current.reasons_complete !== true) return reasons.length ? reasons.join(" · ") + " · TEILWEISE UNBEKANNT" : "LIMITS UNBEKANNT"
        return reasons.length ? reasons.join(" · ") : "KEIN LIMIT AKTIV"
    }
    function detailText() {
        var lines = ["GPU-Board-Schätzung · Messung alle 10 s · keine Prozess-/Systemenergie.",
            "Seit Widgetstart " + (payload ? new Date(payload.started * 1000).toLocaleTimeString() : "--")
            + " · erfasste Zeit " + number(payload ? payload.covered_s : null) + " s (Messlücken ausgeschlossen).",
            "Ø letzte 60 s: " + number(fresh ? payload.average_w : null, 1) + " W · abgedeckt " + number(fresh ? payload.average_covered_s : null) + " s."]
        lines.push("PCIe AER seit Boot (Endpoint/Rootport können dasselbe Ereignis zählen):")
        var aer = current.aer || ({})
        for (var name in aer) lines.push("  " + name + ": " + number(aer[name]))
        if (!fresh) lines.push("Xid-Kerneljournal: UNBEKANNT")
        else if (journalReadable) lines.push("Xid-Kerneljournal: lesbar · neue Ereignisse ab Monitoringstart")
        else lines.push("Hinweis: Kernel-Journal nicht lesbar (Benutzer nicht in Gruppe systemd-journal) · Xid-Ereignisse werden nicht erfasst.")
        lines.push("Ereignisse (letzte 20, nur lokal seit Widgetstart):")
        var events = payload && payload.events ? payload.events : []
        if (!events.length) lines.push("  Keine erfassten Ereignisse; unbekannte Quellen sind kein Fehlerfrei-Nachweis.")
        for (var i = events.length - 1; i >= 0; i--) lines.push(new Date(events[i].time * 1000).toLocaleTimeString() + " · " + events[i].text)
        return lines.join("\n")
    }
    Text {
        id: heading
        objectName: "blackwellHeading"
        anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 10
        text: "BLACKWELL · " + (!health.fresh ? "UNBEKANNT" : health.number(health.current.temperature_c) + "°C · PCIe AER Σ " + health.total(health.current.aer))
            + (health.eventWarning ? " · NEUES EREIGNIS" : "")
        color: health.fresh ? health.ink : health.warning
        font.family: "DejaVu Sans Mono"; font.pixelSize: 12; font.bold: true
        width: parent.width - toggle.width - 32; elide: Text.ElideRight
    }
    Text {
        id: toggle
        anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
        text: health.expanded ? "DETAILS ▴" : "DETAILS ▾"
        color: health.accent; font.family: "DejaVu Sans Mono"; font.pixelSize: 12
    }
    Text {
        objectName: "blackwellSummary"
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: heading.bottom
        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 5
        text: "Ø60s " + health.number(health.fresh ? health.payload.average_w : null, 1) + "W · ~"
            + health.number(health.payload ? health.payload.energy_wh : null, 2) + "Wh · " + health.reasonsText()
            + (health.uncertain ? " · QUELLEN ?" : "")
        color: health.thermal ? health.critical : health.muted
        font.family: "DejaVu Sans Mono"; font.pixelSize: 12
        elide: Text.ElideRight
    }
    PlasmaCore.ToolTipArea {
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 64
        mainText: health.detailText()
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: health.expanded = !health.expanded
        }
    }
    Column {
        visible: health.expanded
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 67
        spacing: 6
        Text {
            width: parent.width
            text: "SM " + health.number(health.current.sm_mhz) + " MHz · VRAM " + health.number(health.current.memory_mhz)
                + " MHz · LIMIT " + health.number(health.current.limit_w) + " W · Xid " + (!health.fresh ? "UNBEKANNT" : health.journalReadable ? "LESBAR" : "N/V")
            color: health.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12; elide: Text.ElideRight
        }
        Text {
            text: "120 s · LEISTUNG (0–145 W) / TEMPERATUR (0–100°C) / LAST (0–100%)"
            color: health.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 11
            width: parent.width; elide: Text.ElideRight
        }
        Canvas {
            id: chart
            width: parent.width; height: 84
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                ctx.strokeStyle = "#466372"; ctx.lineWidth = 1
                ctx.beginPath(); ctx.moveTo(0, height - 1); ctx.lineTo(width, height - 1); ctx.stroke()
                var h = health.payload && health.payload.history ? health.payload.history : []
                var end = health.nowMs / 1000
                function plot(key, maximum, color) {
                    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath()
                    var drawing = false; var previous = 0
                    for (var i = 0; i < h.length; i++) {
                        var p = h[i]
                        if (p[key] === null || p[key] === undefined || p.time < end - 120) { drawing = false; continue }
                        var x = (p.time - end + 120) / 120 * width
                        var y = height - Math.min(1, p[key] / maximum) * (height - 2)
                        if (!drawing || p.time - previous > 25) ctx.moveTo(x, y)
                        else ctx.lineTo(x, y)
                        drawing = true; previous = p.time
                    }
                    ctx.stroke()
                }
                plot("power", 145, "#db91ff"); plot("temperature", 100, "#ffca70"); plot("load", 100, "#96f5f6")
            }
        }
        Text {
            width: parent.width
            text: {
                var events = health.payload && health.payload.events ? health.payload.events : []
                if (!events.length) return "KEINE NEUEN EREIGNISSE ERFASST · Details im Tooltip"
                var e = events[events.length - 1]
                return new Date(e.time * 1000).toLocaleTimeString() + " · " + e.text
            }
            color: health.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 12; elide: Text.ElideRight
        }
        Text {
            width: parent.width
            text: "~ GPU-Wh seit Widgetstart · Lücken zählen nicht · Violett W / Gelb °C / Cyan %"
            color: health.muted; font.family: "DejaVu Sans Mono"; font.pixelSize: 11; elide: Text.ElideRight
        }
    }
    onPayloadChanged: chart.requestPaint()
    PlasmaSupport.DataSource {
        id: source
        engine: "executable"
        connectedSources: []
        property bool busy: false
        property int requestId: 0
        property double startedMs: 0
        property string buffer: ""
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/blackwell_health.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + " --session " + health.session + " # skybox-request-" + requestId
        onNewData: function(name, data) {
            if (name !== command) return
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            disconnectSource(name); busy = false
            try { health.payload = JSON.parse(buffer.trim()) } catch (error) { health.payload = null }
            buffer = ""
            health.nowMs = Date.now()
        }
    }
    function poll() {
        if (!pollingEnabled || source.busy) return
        source.requestId++; source.startedMs = Date.now()
        source.busy = true; source.buffer = ""; source.connectSource(source.command)
    }
    Timer { interval: 10000; running: health.pollingEnabled; repeat: true; onTriggered: health.poll() }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: { health.nowMs = Date.now(); chart.requestPaint() } }
    // Retry after an executable engine stalls; freshness becomes unknown after 25 s.
    Timer { interval: 15000; running: health.pollingEnabled; repeat: true; onTriggered: { if (source.busy && Date.now() - source.startedMs > 15000) { source.disconnectSource(source.command); source.requestId++; source.busy = false; source.buffer = "" } } }
    Component.onCompleted: poll()
}
