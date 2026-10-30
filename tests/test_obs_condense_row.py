#!/usr/bin/env python3
"""HINDSIGHT VERDICHTUNG row: how well raw facts are condensed into observations.

Three numbers, all from the same helper payload as the OBS card:

- ``referenced_pct``  share of raw facts referenced by at least one observation
- ``merge_pct``       share of observations backed by >= 2 raw facts (existing)
- ``avg_proofs``      mean proof_count per observation

The row is informational (no health gate): it must show the exact values, read
as unknown when unmeasured, and blank together with the OBS card on a failed poll.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
QML = ROOT / "contents/ui/main.qml"
MODULE = ROOT / "contents/code/hindsight_observation_health.py"
spec = importlib.util.spec_from_file_location("hindsight_observation_health_condense", MODULE)
assert spec and spec.loader
health = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = health
spec.loader.exec_module(health)


def _obs(proof: int, sources: list[str]) -> dict:
    return {"fact_type": "observation", "proof_count": proof, "tags": [], "source_memory_ids": sources}


def _fact(oid: str) -> dict:
    return {"id": oid, "fact_type": "world", "tags": []}


def test_helper_reports_referenced_share_and_average_proofs():
    items = [_obs(1, ["f1"]), _obs(3, ["f2", "f3"]), _fact("f1"), _fact("f2"), _fact("f3"), _fact("f4")]
    payload = health.summarize(items)
    # 3 of 4 raw facts sit in an observation; proofs (1 + 3) / 2 = 2.0
    assert payload["referenced_pct"] == 75.0
    assert payload["avg_proofs"] == 2.0


def test_helper_reports_null_instead_of_zero_without_a_population():
    payload = health.summarize([])
    assert payload["referenced_pct"] is None
    assert payload["avg_proofs"] is None


def _label(root_js: str) -> str:
    source = QML.read_text()
    funcs = source[source.index('    function obsCondenseLabel()'):source.index('    function obsCondenseDetail()')]
    script = funcs + '''
function fmtPct0(v) { return v < 0 ? "--" : Math.round(v) + "%"; }
var root = ''' + root_js + ''';
root.fmtPct0 = fmtPct0;
console.log(obsCondenseLabel());
'''
    return subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout.strip()


def test_row_label_shows_the_three_condensation_values():
    out = _label('{obsHealthUnavailable: false, obsReferencedPct: 83.0, obsMergePct: 36.9, obsAvgProofs: 2.27}')
    assert out == "IN OBS 83% \u00b7 MERGE 37% \u00b7 \u00d8 2.27"


def test_row_label_is_unknown_when_unavailable_or_unmeasured():
    assert _label('{obsHealthUnavailable: true, obsReferencedPct: -1, obsMergePct: -1, obsAvgProofs: -1}') == "? OBS"
    out = _label('{obsHealthUnavailable: false, obsReferencedPct: -1, obsMergePct: -1, obsAvgProofs: -1}')
    assert out == "IN OBS -- \u00b7 MERGE -- \u00b7 \u00d8 --"


def test_row_tooltip_explains_the_values_without_placeholders():
    source = QML.read_text()
    funcs = source[source.index('    function obsCondenseDetail()'):source.index('    function resetObsHealth()')]
    script = funcs + '''
function fmtPct1(v) { return v < 0 ? "--" : (Math.round(v * 10) / 10).toFixed(1) + "%"; }
function obsInt(v) { return v < 0 ? "?" : String(Math.round(v)); }
var root = {obsHealthUnavailable: false, obsReferencedPct: 83.0, obsMergePct: 36.9, obsAvgProofs: 2.27,
            obsRawFacts: 14586, obsOrphans: 2482, obsMerged: 2167, obsCount: 5871,
            fmtPct1: fmtPct1, obsInt: obsInt};
console.log(obsCondenseDetail());
'''
    detail = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
    assert "undefined" not in detail and "%%" not in detail and "NaN" not in detail
    assert "83.0% (12104 VON 14586 ROHFAKTEN" in detail
    assert "36.9% (2167 VON 5871 OBS MIT PROOF >= 2)" in detail
    assert "\u00d8 2.27 BELEGE PRO OBSERVATION" in detail


def test_row_is_wired_into_the_dashboard_and_reset_path():
    text = QML.read_text()
    assert "id: obsCondenseCard" in text
    assert 'text: "HINDSIGHT VERDICHTUNG"' in text
    assert "root.obsCondenseLabel()" in text and "root.obsCondenseDetail()" in text
    assert "payload.referenced_pct, -1" in text and "payload.avg_proofs, -1" in text
    reset = text[text.index('    function resetObsHealth()'):text.index('    function gpuPowerText(')]
    assert "root.obsReferencedPct = -1" in reset and "root.obsAvgProofs = -1" in reset
    # The row sits directly below the OAuth card (the OBS row was removed).
    assert text.index("id: obsCondenseCard") > text.index("id: openAiOauthCard")
    assert "id: obsHealthCard" not in text
