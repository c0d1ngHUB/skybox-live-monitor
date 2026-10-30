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
    // Dropdown with tokens, cron and Hindsight details; collapsed by default.
    property bool expanded: false
    property var details: null
    readonly property int detailRowHeight: 20
    readonly property bool detailsFresh: !!details && typeof details.stamp === "number" && nowMs / 1000 >= details.stamp - 2 && nowMs / 1000 - details.stamp <= 120
    readonly property bool detailsWarn: !detailsFresh || tokensTone() !== "ok" || cronTone() !== "ok" || hindsightTone() !== "ok"
    implicitHeight: 64 + (expanded ? 3 * detailRowHeight + 8 : 0)
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
        if (payload.partial === true) text += " · TEILWEISE"
        return text
    }
    function summaryText() {
        if (!fresh) return "Keine frische Messung · Quelle nicht lesbar oder veraltet"
        var last = payload.last_activity_age_s === null || payload.last_activity_age_s === undefined
            ? "?" : duration(payload.last_activity_age_s)
        var origin = payload.last_source || ""
        if (payload.last_profile && payload.last_profile !== "default") origin += (origin ? "/" : "") + payload.last_profile
        return "Zuletzt " + last + (origin ? " (" + origin + ")" : "")
            + " · " + (payload.model || "Modell ?")
            + " · FALLBACK 24h: " + count(payload.fallback_24h)
            + " · 429 24h: " + count(payload.limit_429_24h)
    }
    function detailText() {
        if (!fresh) return "Hermes-Status UNBEKANNT: state.db nicht lesbar oder Messung älter als 25 s."
        var models = payload.fallback_models && payload.fallback_models.length ? payload.fallback_models.join(", ") : "keine"
        var profiles = payload.profiles && payload.profiles.length ? payload.profiles.join(", ") : "default"
        var failed = payload.failed_profiles && payload.failed_profiles.length ? " · NICHT LESBAR: " + payload.failed_profiles.join(", ") : ""
        return ["Nur lesend aus state.db, config.yaml und errors.log(.1) je Profil · kein Modellaufruf, keine Tokens.",
            "Profile: " + profiles + failed,
            "Aktive Turns: " + count(payload.running_turns) + " · längster " + duration(payload.longest_turn_s),
            "Worker: " + count(payload.workers) + " · Delegationen: " + count(payload.delegations),
            "Letzte Aktivität: " + (payload.last_description || "?"),
            "Fallback-Sessions 24h: " + count(payload.fallback_24h) + " (Aufrufe " + count(payload.fallback_calls_24h) + ") · Modelle: " + models,
            "Provider-Limit (429) 24h: " + count(payload.limit_429_24h),
            "Hängt? = ein Turn läuft, aber seine eigene Session zeigt seit über 5 min keine Aktivität."].join("\n")
    }

    function compact(n) {
        if (n === null || n === undefined) return "?"
        n = Number(n)
        if (n >= 1e9) return (n / 1e9).toFixed(1) + " G"
        if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e8 ? 0 : 1) + " M"
        if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + " k"
        return String(Math.round(n))
    }
    function shortModel(name) {
        var s = String(name || "?").replace(/^claude-/, "")
        return s.length > 14 ? s.slice(0, 14) : s
    }
    function clock(stamp) {
        var d = new Date(stamp * 1000)
        var hm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2)
        var now = new Date(nowMs)
        if (d.toDateString() === now.toDateString()) return hm
        return ("0" + d.getDate()).slice(-2) + "." + ("0" + (d.getMonth() + 1)).slice(-2) + ". " + hm
    }
    function section(key) {
        return detailsFresh && details[key] && details[key].available === true ? details[key] : null
    }
    function tokensTone() {
        var t = section("tokens")
        return !t ? "unknown" : (t.partial ? "warn" : "ok")
    }
    function cronTone() {
        var c = section("cron")
        if (!c || c.failed_24h === null || c.expected === null) return "unknown"
        return c.missing.length > 0 || c.disabled.length > 0 || c.failed_24h > 0 ? "warn" : "ok"
    }
    function hindsightTone() {
        var h = section("hindsight")
        if (!h) return "unknown"
        return h.warn ? "warn" : "ok"
    }
    function tokensText() {
        var t = section("tokens")
        if (!t) return "TOKENS 24h · UNBEKANNT"
        var parts = ["TOKENS 24h · " + compact(t.total),
            "Cache " + (t.cache_hit_pct === null ? "?" : t.cache_hit_pct + "%"),
            "~" + Math.round(t.cost_usd) + " $"]
        var shares = []
        for (var i = 0; i < t.models.length && i < 3; i++) {
            var pct = t.total > 0 ? Math.round(100 * t.models[i].total / t.total) : 0
            if (pct > 0) shares.push(shortModel(t.models[i].model) + " " + pct + "%")
        }
        if (shares.length) parts.push(shares.join(" "))
        return parts.join(" · ")
    }
    function tokensDetail() {
        var t = section("tokens")
        if (!t) return "Token-Daten UNBEKANNT: state.db nicht lesbar oder Messung älter als 120 s."
        var lines = ["Sessions mit Aktivität in den letzten 24 h, jeweils mit ihrem GESAMTEN Verbrauch",
            "(Hermes speichert keine Tokens pro Nachricht, daher keine exakte 24h-/60-min-Abgrenzung).",
            "Cache-Hit = cache_read / (input + cache_read + cache_write).",
            "USD = Hermes-Schätzung (estimated_cost_usd); beim Max-Abo fiktiv, nur als Verbrauchsmaß."]
        for (var i = 0; i < t.models.length; i++) {
            var m = t.models[i]
            lines.push(m.model + ": " + compact(m.total) + " (in " + compact(m.input) + ", out " + compact(m.output)
                + ", cache r/w " + compact(m.cache_read) + "/" + compact(m.cache_write) + ") · " + m.sessions + " Sessions · ~" + m.cost_usd + " $")
        }
        if (t.partial) lines.push("NICHT LESBAR: " + t.failed_profiles.join(", "))
        return lines.join("\n")
    }
    function cronText() {
        var c = section("cron")
        if (!c) return "CRON · UNBEKANNT"
        var parts = ["CRON · " + c.enabled + "/" + (c.expected === null ? "?" : c.expected)]
        if (c.missing.length) parts.push("FEHLT " + c.missing.length)
        if (c.disabled.length) parts.push("PAUSIERT " + c.disabled.length)
        parts.push("nächster " + (c.next_run_at ? clock(c.next_run_at) : "–"))
        parts.push("Fehler 24h: " + count(c.failed_24h))
        return parts.join(" · ")
    }
    function cronDetail() {
        var c = section("cron")
        if (!c) return "Cron-Daten UNBEKANNT: jobs.json nicht lesbar oder Messung älter als 120 s."
        var lines = ["Aktive Jobs " + c.enabled + " · Soll " + count(c.expected) + (c.baseline_new ? " (Soll eben neu gesetzt)" : ""),
            "Nächster: " + (c.next_run_at ? clock(c.next_run_at) + " · " + (c.next_name || "?") : "keiner geplant")]
        if (c.missing.length) lines.push("FEHLT in jobs.json: " + c.missing.join(", "))
        if (c.disabled.length) lines.push("Pausiert/deaktiviert: " + c.disabled.join(", "))
        lines.push("Fehler 24h: " + count(c.failed_24h) + (c.failed_names.length ? " · " + c.failed_names.join(", ") : ""))
        lines.push("Soll-Liste: " + c.expected_file)
        lines.push("Absichtliche Änderung übernehmen: python3 hermes_details.py --accept-cron")
        return lines.join("\n")
    }
    function hindsightText() {
        var h = section("hindsight")
        if (!h) return "HINDSIGHT · UNBEKANNT"
        return "HINDSIGHT · Retains 60m: " + (h.retains_60m === null ? ">500" : h.retains_60m)
            + " · konsolidiert vor " + (h.consolidation_age_s === null ? "?" : duration(h.consolidation_age_s))
            + " · offen " + count(h.pending_consolidation)
            + " · Fehler " + count(h.failed_consolidation)
    }
    function hindsightDetail() {
        var h = section("hindsight")
        if (!h) return "Hindsight UNBEKANNT: API nicht erreichbar oder Messung älter als 120 s."
        return ["Bank " + h.bank + " · nur GET auf die lokale Hindsight-API.",
            "Retains (ohne Batch-Eltern) in den letzten 60 min: " + (h.retains_60m === null ? "mehr als 500 oder unvollständig" : h.retains_60m),
            "Letzte Konsolidierung vor " + (h.consolidation_age_s === null ? "?" : duration(h.consolidation_age_s))
                + " · letzter Schreibvorgang vor " + (h.last_write_age_s === null ? "?" : duration(h.last_write_age_s)),
            "Offen für Konsolidierung: " + count(h.pending_consolidation) + " · fehlgeschlagen: " + count(h.failed_consolidation),
            "Fehlgeschlagene Operationen gesamt (historisch): " + count(h.failed_operations),
            "Gelb: Konsolidierungsfehler, oder offene Einträge und letzte Konsolidierung älter als 6 h."].join("\n")
    }
    function toneColor(tone) {
        return tone === "ok" ? muted : warning
    }

    Text {
        objectName: "hermesHeading"
        anchors.left: parent.left; anchors.right: toggle.left; anchors.top: parent.top; anchors.margins: 10
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
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 64
        mainText: live.detailText()
    }
    Text {
        id: toggle
        objectName: "hermesToggle"
        anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
        text: (live.expanded ? "▾" : "▸") + " DETAILS"
        color: live.detailsWarn ? live.warning : live.accent
        font.family: "DejaVu Sans Mono"; font.pixelSize: 12; font.bold: true
    }
    // The whole header row toggles the dropdown; tooltips stay on hover.
    MouseArea {
        objectName: "hermesToggleArea"
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 64
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton
        onClicked: { live.expanded = !live.expanded; if (live.expanded) live.pollDetails() }
    }
    Column {
        objectName: "hermesDetails"
        visible: live.expanded
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 62
        // Static model: rows bind to the live functions, so new data never recreates delegates.
        Repeater {
            model: 3
            delegate: Item {
                width: parent.width; height: live.detailRowHeight
                Text {
                    objectName: ["hermesTokens", "hermesCron", "hermesHindsight"][index]
                    anchors.fill: parent
                    verticalAlignment: Text.AlignVCenter
                    text: index === 0 ? live.tokensText() : (index === 1 ? live.cronText() : live.hindsightText())
                    color: live.toneColor(index === 0 ? live.tokensTone() : (index === 1 ? live.cronTone() : live.hindsightTone()))
                    font.family: "DejaVu Sans Mono"; font.pixelSize: 12
                    elide: Text.ElideRight
                }
                PlasmaCore.ToolTipArea {
                    anchors.fill: parent
                    mainText: index === 0 ? live.tokensDetail() : (index === 1 ? live.cronDetail() : live.hindsightDetail())
                }
            }
        }
    }
    // Source names rotate through 4 slots: Plasma5Support keeps one property per
    // source name ever connected in EVERY executable DataSource and rebuilds the
    // meta-object for each new one, so an unbounded per-poll name stalled the
    // plasmashell main thread for seconds after a few hours. requestId still
    // advances on every request/abandon, so a late result of an abandoned slot
    // never matches the current command.
    PlasmaSupport.DataSource {
        id: source
        engine: "executable"
        connectedSources: []
        property bool busy: false
        property int requestId: 0
        property double startedMs: 0
        property string buffer: ""
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/hermes_live.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + " # skybox-request-" + (requestId % 4)
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
    PlasmaSupport.DataSource {
        id: detailSource
        engine: "executable"
        connectedSources: []
        property bool busy: false
        property int requestId: 0
        property double startedMs: 0
        property string buffer: ""
        property string scriptPath: MonitorLogic.localFilePath(Qt.resolvedUrl("../code/hermes_details.py"))
        property string command: "python3 " + MonitorLogic.shellQuote(scriptPath) + " # skybox-request-" + (requestId % 4)
        onNewData: function(name, data) {
            if (name !== command) return
            buffer += data["stdout"] || ""
            if (data["exit code"] === undefined) return
            disconnectSource(name); busy = false
            try { live.details = JSON.parse(buffer.trim()) } catch (error) { live.details = null }
            buffer = ""
            live.nowMs = Date.now()
        }
    }
    function pollDetails() {
        if (!pollingEnabled) return
        if (detailSource.busy) {
            if (Date.now() - detailSource.startedMs <= 20000) return
            detailSource.disconnectSource(detailSource.command)
        }
        detailSource.requestId++; detailSource.startedMs = Date.now()
        detailSource.busy = true; detailSource.buffer = ""; detailSource.connectSource(detailSource.command)
    }
    // Details also poll while collapsed so the toggle can turn yellow on a hidden problem.
    Timer { interval: 30000; running: live.pollingEnabled; repeat: true; onTriggered: live.pollDetails() }
    function poll() {
        if (!pollingEnabled || source.busy) return
        source.requestId++; source.startedMs = Date.now()
        source.busy = true; source.buffer = ""; source.connectSource(source.command)
    }
    Timer { interval: 5000; running: live.pollingEnabled; repeat: true; onTriggered: live.poll() }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: live.nowMs = Date.now() }
    // Retry after an executable engine stalls; freshness becomes unknown after 25 s.
    Timer { interval: 15000; running: live.pollingEnabled; repeat: true; onTriggered: { if (source.busy && Date.now() - source.startedMs > 15000) { source.disconnectSource(source.command); source.requestId++; source.busy = false; source.buffer = "" } } }
    Component.onCompleted: { poll(); pollDetails() }
}
