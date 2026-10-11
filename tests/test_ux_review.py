"""Regression checks for the portrait dashboard's information hierarchy."""
from pathlib import Path
import json
import subprocess

import pytest

QML = (Path(__file__).resolve().parents[1] / "contents/ui/main.qml")
LOGIC = QML.parents[1] / "code/monitor_logic.js"


def test_process_names_get_space_without_losing_exact_values():
    source = QML.read_text()
    assert 'readonly property int kpiColumn: Math.max(60, Math.round(parent.width * 0.35))' in source
    assert 'mainText: parent.process.name' in source
    assert 'mainText: parent.fullValue' in source
    assert 'text: processValue.parent.displayValue' in source
    assert 'processDetails.metricKind === "gpu" ? "VRAM" : processDetails.metricKind.toUpperCase()' in source
    assert 'text: root.shortProcessName(parent.mainText)' in source
    assert 'mainText: cardData.powerText || ""' in source
    assert 'text: (cardData.powerText || "").replace("POWER ", "")' in source


def test_compact_process_values_preserve_full_units_in_tooltips():
    source = QML.read_text()
    helper = source[source.index('    function compactProcessValue('):source.index('    function metricBorderColor(')]
    probe = helper + "\nconsole.log(JSON.stringify([" + ",".join([
        'compactProcessValue("RAM", "919.9 MiB")',
        'compactProcessValue("RAM", "1536 MiB")',
        'compactProcessValue("VRAM", "3.7 GiB")',
        'compactProcessValue("VRAM", "250 MiB")',
        'compactProcessValue("CPU %", "39.3%")',
    ]) + "]))"
    result = subprocess.run(["node", "-e", probe], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ["920M", "1.5G", "3.7G", "250M", "39.3%"]
    assert 'mainText: parent.fullValue' in source
    assert 'mainText: parent.process.name' in source


def test_gpu_process_tooltip_retains_unrounded_mib_reading():
    source = QML.read_text()
    formatter = source[source.index('    function fmtVram('):source.index('    function fmtCompactVram(')]
    rows = source[source.index('    function gpuProcessRows('):source.index('    // The card title column')]
    script = 'const MonitorLogic=require(' + json.dumps(str(LOGIC)) + ');\n' + formatter + rows + '''
var root = {fmtVram: fmtVram};
var processes = gpuProcessRows({processes: [
    {name: "a", used_mib: 3801}, {name: "b", used_mib: 3810}
]});
console.log(JSON.stringify(processes));
'''
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    processes = json.loads(result.stdout)
    assert [row["gpu"] for row in processes] == ["3.7 GiB", "3.7 GiB"]
    assert [row["gpuExact"] for row in processes] == ["3801 MiB", "3810 MiB"]
    assert 'processDetails.metricKind === "gpu" ? process.gpuExact' in source
    assert 'processDetails.metricKind === "gpu" ? process.gpu : (processDetails.metricKind === "ram" ? process.ram : fullValue)' in source
    assert 'mainText: parent.fullValue' in source


def test_each_gpu_vram_bar_tooltip_receives_live_used_and_total_readings():
    source = QML.read_text()
    cards = source.split('// --- Three-GPU row', 1)[1].split('x: index', 1)[0]
    for index in range(3):
        row = next(line for line in cards.splitlines() if '{kind:"gpu", label:"GPU ' + str(index) in line)
        assert f'vramUsedMiB:root.gpu{index}VramUsedMiB' in row
        assert f'vramTotalMiB:root.gpu{index}VramTotalMiB' in row
    assert 'root.fmtVram(cardData.vramUsedMiB || 0) + " / " + root.fmtVram(cardData.vramTotalMiB || 0)' in source


def test_metric_denominators_and_short_labels_have_explanations():
    source = QML.read_text()
    assert 'Anteil eines CPU-Threads' in source
    assert 'CPU-Gesamtwert: Durchschnitt' in source
    assert 'text: "SKALEN UNABHÄNGIG"' in source
    assert 'mainText: root.diskReserveDetail()' in source
    assert 'MERGE ' in source


def test_fast_telemetry_age_tracks_the_oldest_successful_source():
    script = LOGIC.read_text() + "\nconsole.log(JSON.stringify([" + ",".join([
        "telemetryAgeSeconds(100000, 0, 95000, 99000)",
        "telemetryAgeSeconds(100000, 99000, 80000, 95000)",
        "telemetryAgeSeconds(100000, 101000, 99000, 99500)",
    ]) + "]))"
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [None, 20, 1]
    source = QML.read_text()
    assert 'text: root.telemetryAgeLabel()' in source
    assert 'root.lastCpuTelemetryMs = now' in source
    assert 'root.lastGpuTelemetryMs = Date.now()' in source
    assert 'root.lastNetworkTelemetryMs = now' in source


def test_obs_health_uses_merge_orphans_lag_and_scope_drift():
    source = QML.read_text()
    assert '    function updateObsScopeTotal(' in source
    assert '    function updateObsDriftTotal(' in source
    # The scope rule moved into obsScopeTrendOk(), which obsHealthOk() consumes.
    rule = source[source.index('    function obsScopeTrendOk()'):source.index('    function obsMergeTrendText(')]
    funcs = source[source.index('    function obsHealthOk()'):source.index('    function obsHealthDetail()')]
    script = rule + funcs + '''
function thresholds() { return {merge_target_pct: 25, merge_floor_pct: 15, lag_ok_hours: 2}; }
function obsThreshold(name, fallback) { var v = thresholds()[name]; return v === undefined ? fallback : v; }
function setCase(m, o, lag, scope, prev, drift, prevDrift) {
  root.obsMergePct = m; root.obsOrphans = o; root.obsLagHours = lag;
  root.obsScopeTotal = scope; root.obsPreviousScopeTotal = prev;
  root.obsDriftScopes = drift; root.obsPreviousDriftScopes = prevDrift;
}
var root = {obsHealthUnavailable: false, obsMergePct: 0, obsOrphans: 0, obsLagHours: 0,
            obsScopeTotal: 0, obsPreviousScopeTotal: 0, obsDriftScopes: 0,
            obsPreviousDriftScopes: 0, obsThreshold: obsThreshold};
// QML reaches every helper through root; the probe has to do the same.
root.obsScopeTrendOk = obsScopeTrendOk;
var cases = [
  [10, 0, 0.5, 6, 6, 5, 5],
  [30, 0, 0.5, 6, 6, 5, 5],
  [30, 1, 0.5, 6, 6, 5, 5],
  [30, 0, 5, 6, 6, 5, 5],
  [30, 0, 0.5, 6, -1, 5, 5],
  [30, 0, 0.5, 7, 6, 5, 5],
  [30, 0, 0.5, 7, 6, 6, 5],
  [30, 0, 0.5, 7, 6, -1, -1],
  [-1, 0, 0.5, 6, 6, 5, 5],
  [30, 0, -1, 6, 6, 5, 5],
  [30, -1, 0.5, 6, 6, 5, 5]
];
console.log(JSON.stringify(cases.map(function (c) {
  setCase(c[0], c[1], c[2], c[3], c[4], c[5], c[6]);
  return obsHealthOk();
})));
'''
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    # A rising scope count is healthy only while no scope started wrapping another
    # tag set; with drift unknown the strict verdict stands.
    assert json.loads(result.stdout) == [False, True, False, False, False, True, False, False, False, False, False]
    assert 'root.obsScopeTotal <= root.obsPreviousScopeTotal' in source
    assert 'root.obsDriftScopes <= root.obsPreviousDriftScopes' in source
    assert 'root.updateObsScopeTotal(numOr(payload.scope_total, -1))' in source
    assert 'root.updateObsDriftTotal(numOr(payload.drift_scopes, -1))' in source
    assert 'payload.merge_pct, -1' in source


def test_obs_label_shows_backlog_scope_drift_untag_and_lag():
    source = QML.read_text()
    funcs = source[source.index('    function obsHealthLabel()'):source.index('    function obsHealthOk()')]
    script = funcs + '''
function fmtPct0(v) { return v < 0 ? "--" : Math.round(v) + "%"; }
function fmtLagHours(v) { return v < 0 ? "--" : (Math.round(v * 10) / 10).toFixed(1) + "h"; }
function obsInt(v) { return v < 0 ? "?" : String(Math.round(v)); }
function obsScopeTrendLabel() { return "\\u0394 0"; }
var root = {obsHealthUnavailable: false, obsMergePct: 9.8, obsOrphans: 0, obsScopeTotal: 6,
            obsLagHours: 0.0, obsDriftScopes: 18, obsUntaggedSharePct: 81.5, fmtPct0: fmtPct0,
            fmtLagHours: fmtLagHours, obsInt: obsInt,
            obsScopeTrendLabel: obsScopeTrendLabel};
console.log(obsHealthLabel());
'''
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    # The exact string the user sees, so a silent relabelling fails here. DRIFT and
    # UNTAG sit between the scope trend and LAG: health first, structure after it.
    assert result.stdout.strip() == (
        "MERGE 10% \u00b7 ORPHAN \u03a3 0 \u00b7 SCOPE 6 \u0394 0 \u00b7 DRIFT 18 \u00b7 UNTAG 82% \u00b7 LAG 0.0h"
    )


def test_obs_label_reports_unmeasurable_values_as_unknown():
    source = QML.read_text()
    funcs = source[source.index('    function obsHealthLabel()'):source.index('    function obsHealthOk()')]
    script = funcs + '''
function fmtPct0(v) { return v < 0 ? "--" : v + "%"; }
function fmtLagHours(v) { return v < 0 ? "--" : v + "h"; }
function obsInt(v) { return v < 0 ? "?" : String(v); }
function obsScopeTrendLabel() { return "? TREND"; }
var root = {obsHealthUnavailable: true, obsMergePct: -1, obsOrphans: -1, obsScopeTotal: -1,
            obsLagHours: -1, fmtPct0: fmtPct0, fmtLagHours: fmtLagHours, obsInt: obsInt,
            obsScopeTrendLabel: obsScopeTrendLabel};
console.log(obsHealthLabel());
'''
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    # An absent API must not read as a measured zero anywhere on the card.
    assert result.stdout.strip() == "? OBS"


def test_obs_tooltip_renders_without_placeholders_or_doubled_percent():
    source = QML.read_text()
    # Every function between the percent formatters and the next unrelated block is
    # plain JS, so the tooltip can be rendered for real instead of only grepped.
    funcs = source[source.index('    function fmtPct1('):source.index('    function gpuPowerText(')]
    script = funcs + '''
function thresholds() { return {merge_target_pct: 25, merge_floor_pct: 15, lag_ok_hours: 2,
  untagged_share_warn_pct: 50, consolidated_window_hours: 24, orphan_check_min_containment_pct: 20}; }
var root = {obsHealthUnavailable: false, obsMergePct: 37.9, obsMerged: 417, obsCount: 1099,
  obsOrphans: 1384, obsRawFacts: 3650, obsOrphanBacklog: 0, obsOrphanConsolidated: 1384,
  obsLagHours: 0, obsConsolidatedRecent: 950, obsConsolidatedRecentUnrefPct: 25.4,
  obsConsolidatedNewestHours: 0.2, obsOrphanCheckSample: 23, obsOrphanCheckCovered: 11,
  obsOrphanCheckMedianContainment: 0.18, obsScopeTotal: 51, obsPreviousScopeTotal: 51,
  obsDriftScopes: 18, obsPreviousDriftScopes: 18, obsUntaggedSharePct: 81.5,
  obsUntaggedObservations: 896, obsLargestScope: 896, obsLargestScopeSharePct: 81.5,
  obsTaglessPct: 81.5, obsSingleProofPct: 62.1, obsThresholds: thresholds(),
  obsMergeHistory: [{t: 0, merge: 37.9, obs: 1099}, {t: 300000, merge: 37.9, obs: 1099}]};
root.fmtPct0 = fmtPct0; root.fmtPct1 = fmtPct1; root.fmtLagHours = fmtLagHours;
root.fmtFractionPct = fmtFractionPct; root.obsInt = obsInt; root.obsThreshold = obsThreshold;
root.obsHealthOk = obsHealthOk; root.obsMergeTrendText = obsMergeTrendText;
root.obsScopeTrendOk = obsScopeTrendOk;
console.log(obsHealthDetail());
'''
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    detail = result.stdout
    # fmtPct0/fmtFractionPct already carry the percent sign: a literal "%" next to
    # them printed "%%" on the card before.
    assert '%%' not in detail
    assert 'undefined' not in detail and 'NaN' not in detail
    assert 'ZUSTAND GESUND' in detail
    assert 'KONSOLIDIERUNG 24H: 950 ROHFAKTEN KONSOLIDIERT · DAVON 25% OHNE REFERENZ' in detail
    assert 'UNTAG 82% DER OBS LIEGEN IM TAGLOSEN SAMMEL-SCOPE (896 OBS · WARN AB 50%)' in detail
    assert 'ORPHAN-AUDIT 23 STICHPROBEN · 11 ÜBER 20% TOKEN-ÜBERDECKUNG · MEDIAN 18%' in detail
    assert 'MERGE-TREND SEIT WIDGET-START 38% → 38% (2 POLLS / 0.1H)' in detail


def test_ram_tooltip_retains_original_unrounded_rss_kib():
    source = QML.read_text()
    ram_source = source[source.index('        id: topRamSource'):source.index('        id: netDetectSource')]
    parser = ram_source[ram_source.index('            var lines = buffer.trim().split'):ram_source.index('            root.topRamProcesses = processes')]
    script = '''var buffer = "2359296 llama-server\\n941976 chromium";
var root = {}, source = "";
function disconnectSource() {}\n''' + parser + '\nroot.topRamProcesses = processes; console.log(JSON.stringify(root.topRamProcesses));'
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    rows = json.loads(result.stdout)
    assert rows[0] == {"ram": "2304.0 MiB", "ramExact": "2359296 KiB", "name": "llama-server"}
    assert rows[1]["ramExact"] == "941976 KiB"
    assert 'processDetails.metricKind === "ram" ? process.ramExact' in source
    assert 'processDetails.metricKind === "ram" ? process.ram : fullValue' in source


@pytest.mark.parametrize("backlog,total,want", [(0,42,"BACKLOG 0"), (3,42,"BACKLOG 3"), (-1,42,"ORPHAN Σ 42")])
def test_obs_primary_label_reports_real_backlog_with_explicit_legacy_total(backlog, total, want):
    source = QML.read_text()
    funcs = source[source.index('    function obsHealthLabel()'):source.index('    function obsHealthOk()')]
    script = funcs + "\nvar root = " + json.dumps({
        "obsHealthUnavailable": False, "obsMergePct": 40.7, "obsOrphanBacklog": backlog,
        "obsOrphans": total, "obsScopeTotal": 3, "obsLagHours": 0,
        "obsDriftScopes": 18, "obsUntaggedSharePct": 81.5,
    }) + ";\n" + """
root.fmtPct0 = function(v) { return Math.round(v) + "%"; };
root.obsInt = function(v) { return String(v); };
root.obsScopeTrendLabel = function() { return "Δ 0"; };
root.fmtLagHours = function(v) { return v.toFixed(1) + "h"; };
console.log(obsHealthLabel());
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "MERGE 41% · " + want + " · SCOPE 3 Δ 0 · DRIFT 18 · UNTAG 82% · LAG 0.0h"
