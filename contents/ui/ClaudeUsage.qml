import QtQuick 2.15
import "../code/monitor_logic.js" as MonitorLogic
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport

// Claude plan usage (5-h session + weekly limit) from the same endpoint as
// claude.ai's "Plan usage limits" page. Read-only, never refreshes the token,
// costs no model usage. Polled every 2 min; unknown is never shown as 0 %.
// Rendered as one status row of the KI-DIENSTE block, in the same form as the
// OPENAI OAUTH and HINDSIGHT VERDICHTUNG rows above it.
Rectangle {
    id: usage
    objectName: "claudeUsage"
    property color ink: "#e4f5ff"
    property color muted: "#8ba8b5"
    property color accent: "#96f5f6"
    property color warning: "#ffca70"
    property color critical: "#ff7373"
    // Healthy rows keep the quiet structural border; only warn/critical colour it.
    property color quietBorder: Qt.rgba(0.63, 0.78, 0.85, 0.28)
    property bool pollingEnabled: true
    property int pollIntervalMs: 120000
    property var payload: null
    property double nowMs: Date.now()
    // Stale after 2.5 poll intervals (one missed poll is tolerated).
    readonly property bool fresh: !!payload && payload.available === true && typeof payload.stamp === "number"
        && nowMs / 1000 >= payload.stamp - 2 && nowMs / 1000 - payload.stamp <= pollIntervalMs / 1000 * 2.5
    readonly property string tone: fresh ? String(payload.tone || "unknown") : "unknown"
    implicitHeight: 28
    radius: 9
    color: Qt.rgba(0, 0, 0, 0.22)
    border.width: 1
    border.color: tone === "critical" ? critical : (tone === "ok" ? quietBorder : warning)
    clip: true

    function pad(n) { return ("0" + n).slice(-2) }
    // The API reports resets as hh:59:59.99; round to the minute like claude.ai does.
    function resetDate(stamp) { return new Date(Math.round(stamp / 60) * 60000) }
    function resetText(stamp) {
        if (typeof stamp !== "number") return "?"
        var d = resetDate(stamp)
        var hm = pad(d.getHours()) + ":" + pad(d.getMinutes())
        var now = new Date(nowMs)
        if (d.toDateString() === now.toDateString()) return hm
        if (d.getTime() - now.getTime() < 7 * 86400000)
            return ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"][d.getDay()] + " " + hm
        return pad(d.getDate()) + "." + pad(d.getMonth() + 1) + ". " + hm
    }
    function fullReset(stamp) {
        if (typeof stamp !== "number") return "unbekannt"
        var d = resetDate(stamp)
        return ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"][d.getDay()] + " " + pad(d.getDate()) + "." + pad(d.getMonth() + 1)
            + "." + d.getFullYear() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes())
    }
    function limit(key) {
        return fresh && payload[key] && typeof payload[key].pct === "number" ? payload[key] : null
    }
    function pctText(key) {
        var l = limit(key)
        return l ? l.pct + "%" : "--"
    }
    function fill(key) {
        var l = limit(key)
        return l ? Math.max(0, Math.min(100, l.pct)) / 100 : 0
    }
    function levelColor(key) {
        var l = limit(key)
        if (!l) return warning
        var t = payload.thresholds || {}
        if (l.pct >= (t.critical_pct || 90) || l.severity === "critical") return critical
        if (l.pct >= (t.warn_pct || 75) || (l.severity && l.severity !== "normal")) return warning
        return accent
    }
    function reasonText() {
        var r = payload ? payload.reason : null
        if (!payload) return "noch keine Messung"
        if (r === "token_expired") return "Token abgelaufen"
        if (r === "no_credentials") return "keine Claude-Anmeldung"
        if (r === "network") return "nicht erreichbar"
        if (r === "bad_payload") return "Antwort unbekannt"
        if (r && r.indexOf("http_") === 0) return "HTTP " + r.slice(5)
        if (payload.available === true) return "Messung veraltet"
        return "unbekannt"
    }
    // "CLAUDE MAX" when the plan is known, plain "CLAUDE" otherwise — never a
    // literal placeholder like "CLAUDE PLAN".
    function headingText() {
        var plan = payload && typeof payload.plan === "string" ? payload.plan.trim().toUpperCase() : ""
        return plan.length ? "CLAUDE " + plan : "CLAUDE"
    }
    // The reset suffix only exists when the API named a reset time. Without an
    // active 5-h window the session has pct 0 and resets_at null; "↻ ?" there
    // read like a broken value.
    function limitText(label, key) {
        var l = limit(key)
        var reset = l && typeof l.resets_at === "number" ? " ↻ " + resetText(l.resets_at) : ""
        return label + " " + pctText(key) + reset
    }
    function detailText() {
        if (!fresh) return "Claude-Nutzung UNBEKANNT: " + reasonText() + ".\n"
            + "Quelle: api.anthropic.com/api/oauth/usage mit dem Token aus ~/.claude/.credentials.json.\n"
            + "Der Token wird hier nie erneuert; Claude Code bzw. Hermes erneuern ihn beim nächsten Aufruf."
        var s = limit("session"), w = limit("weekly")
        var lines = ["Nutzungslimits des Plans " + (payload.plan || "?") + " · nur lesend, kein Modellaufruf.",
            "Aktuelle Sitzung (5 h): " + (s ? s.pct + "% · Reset " + fullReset(s.resets_at) : "unbekannt"),
            "Woche, alle Modelle: " + (w ? w.pct + "% · Reset " + fullReset(w.resets_at) : "unbekannt")]
        var scoped = payload.scoped || []
        for (var i = 0; i < scoped.length; i++)
            lines.push("Woche, " + scoped[i].name + ": " + scoped[i].pct + "%")
        var rows = payload.breakdown || []
        var parts = []
        for (var j = 0; j < rows.length; j++) parts.push(rows[j].name + " " + rows[j].pct + "%")
        if (parts.length) lines.push("Wochenanteil: " + parts.join(" · "))
        lines.push("Extra-Usage: " + (payload.extra_usage_enabled === true ? "AKTIV (kostet extra)" : (payload.extra_usage_enabled === false ? "aus" : "unbekannt")))
        var t = payload.thresholds || {}
        lines.push("Gelb ab " + (t.warn_pct || 75) + "%, rot ab " + (t.critical_pct || 90) + "%.")
        lines.push("Abgerufen vor " + Math.max(0, Math.round((nowMs / 1000 - payload.stamp) / 60)) + " min · Abfrage alle " + Math.round(pollIntervalMs / 60000) + " min.")
        return lines.join("\n")
    }

    Text {
        id: heading
        objectName: "claudeHeading"
        anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter
        width: 130
        text: usage.headingText()
        color: usage.muted
        font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true
        elide: Text.ElideRight
    }
    Text {
        objectName: "claudeUnknown"
        visible: !usage.fresh
        anchors.left: heading.right; anchors.right: parent.right; anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        horizontalAlignment: Text.AlignRight
        text: "SITZUNG -- · WOCHE -- · " + usage.reasonText()
        color: usage.warning
        font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true
        elide: Text.ElideRight
    }
    Row {
        id: limits
        visible: usage.fresh
        anchors.left: heading.right; anchors.right: parent.right; anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        spacing: 16
        Repeater {
            model: 2
            delegate: Item {
                readonly property string key: index === 0 ? "session" : "weekly"
                width: (limits.width - limits.spacing) / 2; height: 20
                Text {
                    id: label
                    objectName: index === 0 ? "claudeSession" : "claudeWeekly"
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                    width: 200
                    text: usage.limitText(index === 0 ? "SITZUNG" : "WOCHE", key)
                    color: usage.levelColor(key)
                    font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true
                    elide: Text.ElideRight
                }
                Rectangle {
                    objectName: index === 0 ? "claudeSessionBar" : "claudeWeeklyBar"
                    anchors.left: label.right; anchors.right: parent.right; anchors.leftMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    height: 6; radius: 3
                    color: Qt.rgba(1, 1, 1, 0.16)
                    Rectangle {
                        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: usage.fill(key) > 0 ? Math.max(parent.height, parent.width * usage.fill(key)) : 0
                        radius: 3
                        color: usage.levelColor(key)
                    }
                }
            }
        }
    }
    PlasmaCore.ToolTipArea {
        anchors.fill: parent
        mainText: usage.detailText()
    }

    // Source names rotate through a bounded set (see test_datasource_name_bound).
    PlasmaSupport.DataSource {
        id: source
        engine: "executable"
        connectedSources: []
        property bool busy: false
        property int requestId: 0
        property double startedMs: 0
        property string buffer: ""
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/claude_usage.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + " # skybox-request-" + (requestId % 4)
        onNewData: function(name, data) {
            if (name !== command) return
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            disconnectSource(name); busy = false
            try { usage.payload = JSON.parse(buffer.trim()) } catch (error) { usage.payload = null }
            buffer = ""
            usage.nowMs = Date.now()
        }
    }
    function poll() {
        if (!pollingEnabled) return
        if (source.busy) {
            if (Date.now() - source.startedMs <= 30000) return
            source.disconnectSource(source.command)
        }
        source.requestId++; source.startedMs = Date.now()
        source.busy = true; source.buffer = ""; source.connectSource(source.command)
    }
    Timer { interval: usage.pollIntervalMs; running: usage.pollingEnabled; repeat: true; onTriggered: usage.poll() }
    Timer { interval: 30000; running: true; repeat: true; onTriggered: usage.nowMs = Date.now() }
    Component.onCompleted: poll()
}
