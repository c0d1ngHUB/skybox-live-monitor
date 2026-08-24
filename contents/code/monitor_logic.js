// Pure monitor helpers shared by QML and the Node-based regression tests.

function historyX(index, length, width, maxSamples) {
    if (maxSamples <= 1 || width <= 0) return 0
    var visibleLength = Math.max(1, Math.min(length, maxSamples))
    var start = maxSamples - visibleLength
    return (start + index) * width / (maxSamples - 1)
}

function staleDomains(nowMs, updates, staleAfterMs) {
    var domains = [
        ["CPU", ["cpuUsage", "cpuTemperature"]],
        ["GPU", ["gpuUsage", "gpuTemperature", "gpuVram"]],
        ["MEMORY", ["memoryPercent", "memoryUsed", "memoryTotal"]],
        ["NETWORK", ["network"]],
        ["DISK", ["diskPercent", "diskUsed", "diskTotal"]],
        ["SYSTEM", ["uptime", "loadAverage"]]
    ]
    var stale = []
    for (var i = 0; i < domains.length; i++) {
        var metrics = domains[i][1]
        for (var j = 0; j < metrics.length; j++) {
            var stamp = Number(updates[metrics[j]] || 0)
            if (stamp <= 0 || nowMs - stamp > staleAfterMs) {
                stale.push(domains[i][0])
                break
            }
        }
    }
    return stale
}

function cpuProcessRates(previousByPid, samples, elapsedMs, limit) {
    if (elapsedMs <= 0) return []
    var rates = []
    for (var i = 0; i < samples.length; i++) {
        var sample = samples[i]
        var previous = previousByPid[String(sample.pid)]
        if (!previous) continue
        var deltaSeconds = Number(sample.cpuSeconds) - Number(previous.cpuSeconds)
        if (!isFinite(deltaSeconds) || deltaSeconds < 0) continue
        // percent = delta CPU-seconds / elapsed seconds * 100
        // (elapsedMs in ms → *1000 for seconds, *100 for percent → 100000)
        rates.push({
            pid: sample.pid,
            name: sample.name,
            cpu: (deltaSeconds * 100000 / elapsedMs).toFixed(1)
        })
    }
    rates.sort(function(a, b) { return parseFloat(b.cpu) - parseFloat(a.cpu) })
    return rates.slice(0, Math.max(0, limit || 0))
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

// Network axis: pick the smallest scale ceiling (in bytes/s) so that the
// history peak fits with ~15% headroom. Ceilings grow in fixed Mbit/s steps
// (download: 50-step grid, upload: 5-step grid) and never exceed capMbit.
function networkScaleMbit(samples, stepMbit, capMbit) {
    var peakBytes = 0
    for (var i = 0; i < samples.length; i++) {
        var v = Number(samples[i]) || 0
        if (v > peakBytes) peakBytes = v
    }
    var needed = peakBytes * 8 * 1.15 // 15% headroom above peak
    var neededMbit = needed / 1000000
    var steps = Math.max(1, Math.ceil(neededMbit / stepMbit))
    return Math.min(steps * stepMbit, capMbit)
}

if (typeof module !== "undefined") {
    module.exports = {
        historyX: historyX,
        staleDomains: staleDomains,
        parseNvidiaMemory: parseNvidiaMemory,
        cpuProcessRates: cpuProcessRates,
        networkScaleMbit: networkScaleMbit
    }
}
