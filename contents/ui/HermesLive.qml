import QtQuick 2.15
import "../code/monitor_logic.js" as MonitorLogic
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport

// Hermes live status: what is Hermes doing right now, plus a warning indicator
// for fallback use and provider limits in the last 24 h. Read-only, token-free.
Rectangle {
    id: live
    objectName: "hermesLive"
    property color ink: "#e4f5ff"
    property color muted: "#8ba8b5"
    property color accent: "#db91ff"
    property color warning: "#ffca70"
    property color critical: "#ff7373"
    property bool pollingEnabled: true
    property var payload: null
    property double nowMs: Date.now()
    readonly property bool fresh: !!payload && payload.available === true && typeof payload.stamp === "number" && nowMs / 1000 >= payload.stamp - 2 && nowMs / 1000 - payload.stamp <= 25
    readonly property bool warn: !fresh || payload.tone !== "ok"
    implicitHeight: 64
    radius: 12
    color: Qt.rgba(0.035, 0.22, 0.34, 0.82)
    border.color: warn ? warning : Qt.rgba(0.86, 0.57, 1, 0.35)
    clip: true

    function duration(seconds) {
        var s = Math.max(0, Math.round(Number(seconds)))
        if (s < 60) return s + " s"
        if (s < 3600) return Math.floor(s / 60) + " min " + (s % 60) + " s"
        return Math.floor(s / 3600) + " h " + Math.floor(s % 3600 / 60) + " min"
    }
    function count(value) {
        return value === null || value === undefined ? "?" : String(value)
    }
    function headingText() {
        if (!fresh) return "HERMES · UNBEKANNT"
        var text = "HERMES · " + (payload.running_turns > 0 ? payload.running_turns + " TURN AKTIV (" + duration(payload.longest_turn_s) + ")" : "BEREIT")
        if (payload.workers > 0) text += " · " + payload.workers + " WORKER"
        if (payload.delegations > 0) text += " · " + payload.delegations + " DELEGATION"
        if (payload.stalled === true) text += " · HÄNGT?"
        return text
    }
    function summaryText() {
        if (!fresh) return "Keine frische Messung · Quelle nicht lesbar oder veraltet"
        var last = payload.last_activity_age_s === null || payload.last_activity_age_s === undefined
            ? "?" : duration(payload.last_activity_age_s)
        return "Zuletzt " + last + (payload.last_source ? " (" + payload.last_source + ")" : "")
            + " · " + (payload.model || "Modell ?")
            + " · FALLBACK 24h: " + count(payload.fallback_24h)
            + " · 429 24h: " + count(payload.limit_429_24h)
    }
    function detailText() {
        if (!fresh) return "Hermes-Status UNBEKANNT: state.db nicht lesbar oder Messung älter als 25 s."
        var models = payload.fallback_models && payload.fallback_models.length ? payload.fallback_models.join(", ") : "keine"
        return ["Nur lesend aus ~/.hermes/state.db, config.yaml und errors.log · kein Modellaufruf, keine Tokens.",
            "Aktive Turns: " + count(payload.running_turns) + " · längster " + duration(payload.longest_turn_s),
            "Worker: " + count(payload.workers) + " · Delegationen: " + count(payload.delegations),
            "Letzte Aktivität: " + (payload.last_description || "?"),
            "Fallback-Sessions 24h: " + count(payload.fallback_24h) + " (Aufrufe " + count(payload.fallback_calls_24h) + ") · Modelle: " + models,
            "Provider-Limit (429) 24h: " + count(payload.limit_429_24h),
            "Hängt? = Turn läuft, aber seit über 5 min keine Aktivität."].join("\n")
    }

    Text {
        objectName: "hermesHeading"
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
        text: live.headingText()
        color: live.warn ? live.warning : live.ink
        font.family: "DejaVu Sans Mono"; font.pixelSize: 12; font.bold: true
        elide: Text.ElideRight
    }
    Text {
        objectName: "hermesSummary"
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 32
        text: live.summaryText()
        color: live.muted
        font.family: "DejaVu Sans Mono"; font.pixelSize: 12
        elide: Text.ElideRight
    }
    PlasmaCore.ToolTipArea {
        anchors.fill: parent
        mainText: live.detailText()
    }
    PlasmaSupport.DataSource {
        id: source
        engine: "executable"
        connectedSources: []
        property bool busy: false
        property int requestId: 0
        property double startedMs: 0
        property string buffer: ""
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/hermes_live.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + " # skybox-request-" + requestId
        onNewData: function(name, data) {
            if (name !== command) return
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            disconnectSource(name); busy = false
            try { live.payload = JSON.parse(buffer.trim()) } catch (error) { live.payload = null }
            buffer = ""
            live.nowMs = Date.now()
        }
    }
    function poll() {
        if (!pollingEnabled || source.busy) return
        source.requestId++; source.startedMs = Date.now()
        source.busy = true; source.buffer = ""; source.connectSource(source.command)
    }
    Timer { interval: 5000; running: live.pollingEnabled; repeat: true; onTriggered: live.poll() }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: live.nowMs = Date.now() }
    // Retry after an executable engine stalls; freshness becomes unknown after 25 s.
    Timer { interval: 15000; running: live.pollingEnabled; repeat: true; onTriggered: { if (source.busy && Date.now() - source.startedMs > 15000) { source.disconnectSource(source.command); source.requestId++; source.busy = false; source.buffer = "" } } }
    Component.onCompleted: poll()
}
