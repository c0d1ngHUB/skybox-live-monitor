// Pure monitor helpers shared by QML and the Node-based regression tests.

function historyX(index, length, width, maxSamples) {
    if (maxSamples <= 1 || width <= 0) return 0
    var visibleLength = Math.max(1, Math.min(length, maxSamples))
    var start = maxSamples - visibleLength
    return (start + index) * width / (maxSamples - 1)
}

// Domains that refresh on a slow cadence carry their own staleness budget.
// The disk footer is polled every 30 s (space barely moves), so the generic
// 15 s window would flag DISK as stale between two perfectly healthy reads.
function staleAfterMsFor(metric, defaultMs) {
    if (metric === "diskPercent" || metric === "diskUsed" || metric === "diskTotal") return 90000
    return defaultMs
}

function staleDomains(nowMs, updates, staleAfterMs) {
    var domains = [
        ["cpu", "CPU", ["cpuUsage", "cpuTemperature"]],
        ["gpu0", "GPU 0", ["gpu0Telemetry"]],
        ["gpu1", "GPU 1", ["gpu1Telemetry"]],
        ["memory", "MEMORY", ["memoryPercent", "memoryUsed", "memoryTotal"]],
        ["network", "NETWORK", ["network"]],
        ["disk", "DISK", ["diskPercent", "diskUsed", "diskTotal"]],
        ["system", "SYSTEM", ["uptime", "loadAverage"]]
    ]
    var stale = []
    for (var i = 0; i < domains.length; i++) {
        var metrics = domains[i][2]
        var domainStale = false
        for (var j = 0; j < metrics.length; j++) {
            var metric = metrics[j]
            var stamp = Number(updates[metric] || 0)
            var budget = staleAfterMsFor(metric, staleAfterMs)
            if (stamp <= 0 || nowMs - stamp > budget) {
                domainStale = true
                break
            }
        }
        if (domainStale) stale.push(domains[i][1])
    }
    return stale
}

function normalizeServiceState(raw) {
    var value = String(raw || "UNKNOWN").trim().toUpperCase()
    if (value === "RUNNING" || value === "HEALTHY" || value === "OK" || value === "IDLE") return "OPERATIONAL"
    if (value === "DEGRADED" || value === "WARN" || value === "WARNING") return "DEGRADED"
    if (value === "DOWN" || value === "ERROR" || value === "OFFLINE" || value === "DISCONNECTED") return "OFFLINE"
    // "Nothing set up yet" is not a fault: an empty credential store must not
    // raise the same alarm as a store that exists but cannot authenticate.
    if (value === "NOT_CONFIGURED") return "NOT_CONFIGURED"
    return "UNKNOWN"
}

function serviceSymbol(state) {
    if (state === "OPERATIONAL") return "●"
    if (state === "DEGRADED") return "▲"
    if (state === "OFFLINE") return "✕"
    if (state === "NOT_CONFIGURED") return "○"
    return "?"
}

function serviceTone(state) {
    if (state === "OPERATIONAL") return "cyan"
    if (state === "DEGRADED") return "warning"
    if (state === "OFFLINE") return "critical"
    return "muted"
}

function openAiOauthState(active, total) {
    active = Number(active)
    total = Number(total)
    if (!isFinite(active) || !isFinite(total) || active < 0 || total < 0) return "UNKNOWN"
    // No credentials configured at all is a configuration state, not an outage.
    if (total === 0) return "NOT_CONFIGURED"
    if (active >= total) return "OPERATIONAL"
    if (active <= 0) return "OFFLINE"
    return "DEGRADED"
}

function openAiOauthSymbol(state) {
    return serviceSymbol(state)
}

function openAiOauthTone(state) {
    return serviceTone(state)
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

// One threshold decides the unit for everything the network panel prints.
// The axis label used to be derived from the chart ceiling (~15% above the
// peak) while the live value was scaled by the raw rate, so a transfer just
// above 1 Mbit printed "MBIT/S" over a rate line reading "2000 KBIT/S".
//
// The panel chooses its unit from the data in the window (peak) plus the current
// rate, never from the retained chart ceiling: the ceiling keeps its hysteresis
// for the plot height, but a ceiling that outlives a burst must not leave the
// axis in MBIT/S while the live value reads KBIT/S.
var NETWORK_MBIT_THRESHOLD = 1

function networkUsesMbit(valueMbit) {
    return Number(valueMbit) >= NETWORK_MBIT_THRESHOLD
}

function networkPanelUsesMbit(peakBytesPerSecond, liveBytesPerSecond) {
    var peak = Number(peakBytesPerSecond) || 0
    var live = Number(liveBytesPerSecond) || 0
    return networkUsesMbit(Math.max(peak, live) * 8 / 1000000)
}

// Axis label for the unit the whole panel prints.
function networkAxisUnit(peakBytesPerSecond, liveBytesPerSecond) {
    return networkPanelUsesMbit(peakBytesPerSecond, liveBytesPerSecond) ? "MBIT/S" : "KBIT/S"
}

function trimNetworkNumber(value) {
    if (value >= 100) return String(Math.round(value))
    if (value >= 10) return value.toFixed(1).replace(".0", "")
    return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")
}

function formatNetworkRate(valueMbit) {
    var mbit = Number(valueMbit)
    if (!isFinite(mbit) || mbit <= 0) return "0 KBIT/S"
    if (!networkUsesMbit(mbit)) return trimNetworkNumber(mbit * 1000) + " KBIT/S"
    return trimNetworkNumber(mbit) + " MBIT/S"
}

// Bytes/s -> rate label in the unit the panel decided on, so the summary block
// and the axis never disagree about the magnitude of one transfer.
function networkRateLabel(bytesPerSecond, useMbit) {
    var bps = Number(bytesPerSecond) || 0
    if (bps <= 0) return useMbit ? "0 MBIT/S" : "0 KBIT/S"
    var mbit = bps * 8 / 1000000
    if (useMbit === undefined) return formatNetworkRate(mbit)
    return trimNetworkNumber(useMbit ? mbit : mbit * 1000) + (useMbit ? " MBIT/S" : " KBIT/S")
}

// Adaptive network axis profile. Very low traffic uses Kbit-scale ceilings so
// idle/background transfers remain visible; larger peaks progressively switch
// to wider Mbit steps without producing an unreadable number of grid lines.
function adaptiveNetworkScale(samples, direction) {
    var peakBytes = 0
    for (var i = 0; i < samples.length; i++) {
        var value = Number(samples[i]) || 0
        if (value > peakBytes) peakBytes = value
    }
    var neededMbit = peakBytes * 8 * 1.15 / 1000000
    var upload = direction === "upload"
    var tiers = upload
        ? [[0.01, 0.0025], [0.1, 0.025], [0.5, 0.1], [5, 1], [50, 5]]
        : [[0.01, 0.0025], [0.1, 0.025], [1, 0.25], [10, 2.5], [600, 50]]
    var capMbit = upload ? 50 : 600
    var stepMbit = tiers[tiers.length - 1][1]
    for (var j = 0; j < tiers.length; j++) {
        if (neededMbit <= tiers[j][0]) {
            stepMbit = tiers[j][1]
            break
        }
    }
    var ceilingMbit = Math.max(stepMbit, Math.ceil(neededMbit / stepMbit) * stepMbit)
    return {
        ceilingMbit: Math.min(ceilingMbit, capMbit),
        stepMbit: stepMbit
    }
}

if (typeof module !== "undefined") {
    module.exports = {
        historyX: historyX,
        staleDomains: staleDomains,
        staleAfterMsFor: staleAfterMsFor,
        parseNvidiaMemory: parseNvidiaMemory,
        normalizeServiceState: normalizeServiceState,
        serviceSymbol: serviceSymbol,
        serviceTone: serviceTone,
        openAiOauthState: openAiOauthState,
        openAiOauthSymbol: openAiOauthSymbol,
        openAiOauthTone: openAiOauthTone,
        cpuProcessRates: cpuProcessRates,
        networkScaleMbit: networkScaleMbit,
        adaptiveNetworkScale: adaptiveNetworkScale,
        networkUsesMbit: networkUsesMbit,
        networkPanelUsesMbit: networkPanelUsesMbit,
        formatNetworkRate: formatNetworkRate,
        networkRateLabel: networkRateLabel,
        networkAxisUnit: networkAxisUnit
    }
}
