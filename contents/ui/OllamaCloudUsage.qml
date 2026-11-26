import QtQuick

// Keep the quota columns, bars and polling mechanics identical to Codex.
// The data comes from the local watcher, not from a credential or API call.
CodexUsage {
    helperUrl: Qt.resolvedUrl("../code/ollama_cloud_usage.py")
    freshnessMs: 75 * 60 * 1000 // 2.5 sampling intervals of the 30-min watcher

    function headingText() { return "OLLAMA CLOUD" }
    function limit(key) {
        var l = fresh ? payload[key] : null
        return l && typeof l.pct === "number"
            && (typeof l.resets_at !== "number" || nowMs / 1000 < l.resets_at) ? l : null
    }
    function reasonText() {
        if (!payload) return "noch keine Messung"
        if (payload.reason === "cache_missing") return "kein Kontingentstand gespeichert"
        if (payload.reason === "bad_payload") return "Kontingentstand nicht lesbar"
        return "Messung veraltet"
    }
    function detailText() {
        var source = "Quelle: lokaler Hermes-Kontingentwächter (Messung alle 30 min)."
        if (!fresh) return "Ollama-Cloud-Kontingent unbekannt: " + reasonText() + ".\n" + source
        var s = limit("session"), w = limit("weekly")
        return ["Ollama Cloud · Anteil des genutzten Kontingents",
                s ? "Sitzung: " + s.pct + "% · Reset " + fullReset(s.resets_at) : "Sitzung: keine aktuelle Messung",
                w ? "Woche: " + w.pct + "% · Reset " + fullReset(w.resets_at) : "Woche: keine aktuelle Messung",
                "Gemessen vor " + Math.max(0, Math.floor((nowMs / 1000 - payload.stamp) / 60)) + " min.",
                source].join("\n")
    }
    border.color: {
        var s = limit("session"), w = limit("weekly")
        if (!s && !w) return warning
        if ((s && levelColor("session") === critical) || (w && levelColor("weekly") === critical)) return critical
        if ((s && levelColor("session") === warning) || (w && levelColor("weekly") === warning)) return warning
        return quietBorder
    }
}
