import QtQuick 2.15
import "../code/monitor_logic.js" as MonitorLogic
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support 2.0 as PlasmaSupport

// Codex plan usage (5-h session + weekly limit) from the endpoint behind the
// limits Codex shows in /status. Read-only, never refreshes the token, costs
// no model usage. Polled every 2 min; unknown is never shown as 0 %.
// Rendered as one status row of the KI-DIENSTE block, in the same form as the
// CLAUDE row above it. With poolEntry set, the row shows a second account whose
// token lives in the Hermes credential pool.
Rectangle {
    id: usage
    objectName: "codexUsage"
    property color ink: "#e4f5ff"
    property color muted: "#8ba8b5"
    property color accent: "#96f5f6"
    property color warning: "#ffca70"
    property color critical: "#ff7373"
    // Healthy rows keep the quiet structural border; only warn/critical colour it.
    property color quietBorder: Qt.rgba(0.63, 0.78, 0.85, 0.28)
    property bool pollingEnabled: true
    property int pollIntervalMs: 120000
    property double freshnessMs: pollIntervalMs * 2.5
    property url helperUrl: Qt.resolvedUrl("../code/codex_usage.py")
    // Hermes credential-pool entry id of a second Codex account. Empty: the
    // Codex login in $CODEX_HOME/auth.json. Only the id is passed on; the
    // helper owns the pool's provider key.
    property string poolEntry: ""
    // Short account name shown after the plan (e.g. "CODEX PLUS · MARION"), so
    // several Codex rows stay distinguishable. Never an e-mail address.
    property string accountLabel: ""
    // Heading column width; main.qml widens it for every usage row alike so the
    // limit columns of the CLAUDE and CODEX rows stay aligned.
    property int headingWidth: 130
    property var payload: null
    property double nowMs: Date.now()
    // Length of the last published long limit (the "weekly" slot of the helper).
    // 8 days and more is a month, not a week: the Go plan publishes 30 days.
    // Kept while the row is unknown, so it does not fall back to WOCHE; an
    // instance may preset the length it expects before the first answer.
    property double longWindowS: 0
    readonly property bool monthly: longWindowS >= 8 * 86400
    onPayloadChanged: {
        var w = payload && payload.available === true ? payload.weekly : null
        if (w && typeof w.window_s === "number" && w.window_s > 0) longWindowS = w.window_s
    }
    // Stale after 2.5 poll intervals (one missed poll is tolerated).
    readonly property bool fresh: !!payload && payload.available === true && typeof payload.stamp === "number"
        && nowMs / 1000 >= payload.stamp - 2 && nowMs - payload.stamp * 1000 <= freshnessMs
    readonly property string tone: fresh ? String(payload.tone || "unknown") : "unknown"
    implicitHeight: 28
    radius: 9
    color: Qt.rgba(0, 0, 0, 0.22)
    border.width: 1
    border.color: tone === "critical" ? critical : (tone === "ok" ? quietBorder : warning)
    clip: true

    function pad(n) { return ("0" + n).slice(-2) }
    // The API sends whole epoch seconds; cut to the minute like Codex' own
    // "resets hh:mm" so both show the same time.
    function resetDate(stamp) { return new Date(Math.floor(stamp / 60) * 60000) }
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
    // "5 h", "7 Tage": the window length the API published for a limit.
    function windowText(seconds) {
        if (typeof seconds !== "number" || seconds <= 0) return "?"
        if (seconds < 86400) return Math.round(seconds / 3600) + " h"
        var days = Math.round(seconds / 86400)
        return days + (days === 1 ? " Tag" : " Tage")
    }
    function longLabel() { return monthly ? "MONAT" : "WOCHE" }
    function longName() { return monthly ? "Monat" : "Woche" }
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
    // The limits row is only visible while fresh, so a missing limit here means
    // the API published no such window (e.g. a weekly-only plan): muted, not a
    // warning colour that would read as a fault on a healthy account.
    function levelColor(key) {
        var l = limit(key)
        if (!l) return muted
        var t = payload.thresholds || {}
        if (l.pct >= (t.critical_pct || 90)) return critical
        if (l.pct >= (t.warn_pct || 75)) return warning
        return accent
    }
    function reasonText() {
        var r = payload ? payload.reason : null
        if (!payload) return "noch keine Messung"
        if (r === "token_expired") return "Token abgelaufen"
        if (r === "no_credentials") return poolEntry.length ? "kein Hermes-Pool-Eintrag" : "keine Codex-Anmeldung"
        if (r === "network") return "nicht erreichbar"
        if (r === "bad_payload") return "Antwort unbekannt"
        if (r === "internal_error") return "interner Fehler"
        if (r && r.indexOf("http_") === 0) return "HTTP " + r.slice(5)
        if (payload.available === true) return "Messung veraltet"
        return "unbekannt"
    }
    function planText() {
        return payload && typeof payload.plan === "string" ? payload.plan.trim().replace(/_/g, " ").toUpperCase() : ""
    }
    // "CODEX PLUS" when the plan is known, plain "CODEX" otherwise — never a
    // literal placeholder like "CODEX PLAN".
    function accountText() { return accountLabel.trim().toUpperCase() }
    function headingText() {
        var plan = planText()
        var account = accountText()
        return (plan.length ? "CODEX " + plan : "CODEX") + (account.length ? " · " + account : "")
    }
    // The reset suffix only exists when the API named a reset time.
    function limitText(label, key) {
        var l = limit(key)
        var reset = l && typeof l.resets_at === "number" ? " ↻ " + resetText(l.resets_at) : ""
        return label + " " + pctText(key) + reset
    }
    function creditsText() {
        var c = payload.credits
        if (!c || typeof c !== "object") return "unbekannt"
        if (c.unlimited === true) return "unbegrenzt"
        if (c.has_credits === true) return typeof c.balance === "string" && c.balance.length ? "Guthaben " + c.balance : "vorhanden"
        if (c.has_credits === false) return "keine"
        return "unbekannt"
    }
    // Where the token comes from and who renews it (never this widget).
    function sourceText() {
        if (poolEntry.length)
            return "Quelle: chatgpt.com/backend-api/wham/usage mit dem Token aus dem Hermes-Pool (Eintrag " + poolEntry
                + " in $HERMES_HOME/auth.json, Standard ~/.hermes/auth.json).\n"
                + "Der Token wird hier nie erneuert; Hermes erneuert ihn bei Nutzung oder per „hermes auth refresh“."
        return "Quelle: chatgpt.com/backend-api/wham/usage mit dem Token aus $CODEX_HOME/auth.json (Standard ~/.codex/auth.json).\n"
            + "Der Token wird hier nie erneuert; Codex erneuert ihn beim nächsten Aufruf."
    }
    function detailText() {
        var account = accountText()
        if (!fresh) return "Codex-Nutzung " + (account.length ? account + " " : "") + "UNBEKANNT: " + reasonText() + ".\n" + sourceText()
        var s = limit("session"), w = limit("weekly")
        var lines = ["Codex-Nutzungslimits des Plans " + (planText() || "?") + " · nur lesend, kein Modellaufruf.",
            s ? "Sitzung (" + windowText(s.window_s) + "): " + s.pct + "% · Reset " + fullReset(s.resets_at) : "Sitzung: kein Limit gemeldet",
            w ? longName() + " (" + windowText(w.window_s) + "): " + w.pct + "% · Reset " + fullReset(w.resets_at) : longName() + ": kein Limit gemeldet"]
        if (poolEntry.length) lines.splice(1, 0, "Konto: " + (account.length ? account + " · " : "") + "Hermes-Pool-Eintrag " + poolEntry + " · Token wird hier nie erneuert.")
        else if (account.length) lines.splice(1, 0, "Konto: " + account + " · Codex-Anmeldung")
        if (payload.limit_reached === true) lines.push("LIMIT ERREICHT: Codex ist bis zum nächsten Reset gesperrt.")
        var extra = payload.extra || []
        for (var i = 0; i < extra.length; i++) {
            var e = extra[i]
            if (!e || typeof e.pct !== "number") continue
            lines.push(e.name + " (" + windowText(e.window_s) + "): " + e.pct + "%"
                + (typeof e.resets_at === "number" ? " · Reset " + fullReset(e.resets_at) : ""))
        }
        lines.push("Credits: " + creditsText())
        if (typeof payload.reset_credits === "number" && payload.reset_credits > 0)
            lines.push("Gespeicherte Limit-Resets: " + payload.reset_credits)
        var t = payload.thresholds || {}
        lines.push("Gelb ab " + (t.warn_pct || 75) + "%, rot ab " + (t.critical_pct || 90) + "% oder wenn das Limit erreicht ist.")
        lines.push("Abgerufen vor " + Math.max(0, Math.round((nowMs / 1000 - payload.stamp) / 60)) + " min · Abfrage alle " + Math.round(pollIntervalMs / 60000) + " min.")
        return lines.join("\n")
    }

    Text {
        id: heading
        objectName: "codexHeading"
        anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter
        width: usage.headingWidth
        text: usage.headingText()
        color: usage.muted
        font.family: "DejaVu Sans Mono"; font.pixelSize: 14; font.bold: true
        elide: Text.ElideRight
    }
    Text {
        objectName: "codexUnknown"
        visible: !usage.fresh
        anchors.left: heading.right; anchors.right: parent.right; anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        horizontalAlignment: Text.AlignRight
        text: "SITZUNG -- · " + usage.longLabel() + " -- · " + usage.reasonText()
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
                    objectName: index === 0 ? "codexSession" : "codexWeekly"
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                    width: 200
                    text: usage.limitText(index === 0 ? "SITZUNG" : usage.longLabel(), key)
                    color: usage.levelColor(key)
                    font.family: "DejaVu Sans Mono"; font.pixelSize: 13; font.bold: true
                    elide: Text.ElideRight
                }
                Rectangle {
                    objectName: index === 0 ? "codexSessionBar" : "codexWeeklyBar"
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
        objectName: "codexSource"
        engine: "executable"
        connectedSources: []
        property bool busy: false
        property int requestId: 0
        property double startedMs: 0
        property string buffer: ""
        property string scriptPath: MonitorLogic.localFilePath(usage.helperUrl)
        property string helperArgs: usage.poolEntry.length ? " --hermes-pool-entry " + MonitorLogic.shellQuote(usage.poolEntry) : ""
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + helperArgs + " # skybox-request-" + (requestId % 4)
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
