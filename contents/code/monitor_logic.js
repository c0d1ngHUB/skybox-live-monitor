// Pure monitor helpers shared by QML and the Node-based regression tests.

function historyX(index, length, width, maxSamples) {
    if (maxSamples <= 1 || width <= 0) return 0
    var visibleLength = Math.max(1, Math.min(length, maxSamples))
    var start = maxSamples - visibleLength
    return (start + index) * width / (maxSamples - 1)
}

function staleDomains(nowMs, updates, staleAfterMs) {
    var domains = [
        ["cpu", "CPU"],
        ["gpu", "GPU"],
        ["memory", "MEMORY"],
        ["network", "NETWORK"],
        ["disk", "DISK"],
        ["system", "SYSTEM"]
    ]
    var stale = []
    for (var i = 0; i < domains.length; i++) {
        var key = domains[i][0]
        var stamp = Number(updates[key] || 0)
        if (stamp <= 0 || nowMs - stamp > staleAfterMs) stale.push(domains[i][1])
    }
    return stale
}

function parseNvidiaMemory(output, gpuIndex) {
    var lines = String(output || "").trim().split("\n")
    for (var i = 0; i < lines.length; i++) {
        var values = lines[i].split(",")
        if (values.length !== 3 || parseInt(values[0].trim(), 10) !== gpuIndex) continue
        var used = parseFloat(values[1])
        var total = parseFloat(values[2])
        if (!isNaN(used) && !isNaN(total)) return {usedMiB: used, totalMiB: total}
    }
    return null
}

if (typeof module !== "undefined") {
    module.exports = {
        historyX: historyX,
        staleDomains: staleDomains,
        parseNvidiaMemory: parseNvidiaMemory
    }
}
