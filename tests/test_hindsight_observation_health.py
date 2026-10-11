#!/usr/bin/env python3
"""Tests for contents/code/hindsight_observation_health.py.

The bank "hermes" was deleted and rebuilt on 2026-10-02 22:15 UTC, so the old
"since the observation_scopes switch" comparison lost its reference. What the
card must report now is the merging health of the CURRENT bank:

- ``merge_pct`` share of observations backed by MORE than one raw fact. This is
  the direct quality signal: consolidation that never merges can only produce
  1:1 copies of raw facts.
- ``orphans``   raw facts (world/experience) that no observation references yet.
- ``lag_hours`` distance between the newest raw fact and the newest observation.
- ``scope_total`` distinct observation-scope tag sets (split = merge pool split).

The things worth pinning down are: each metric counts the right population, a
metric that cannot be measured is null (never 0.0, which would read as healthy on
the card), and an unreachable API blanks the card instead of leaving stale
numbers on screen.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

MODULE = Path(__file__).parents[1] / "contents/code/hindsight_observation_health.py"
spec = importlib.util.spec_from_file_location("hindsight_observation_health", MODULE)
assert spec and spec.loader
health = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = health
spec.loader.exec_module(health)


def _obs(
    proof: int,
    tags: list[str] | None = None,
    mentioned: str = "",
    oid: str = "",
    sources: list[str] | None = None,
    text: str = "",
) -> dict:
    entry: dict[str, Any] = {"fact_type": "observation", "proof_count": proof, "tags": tags or []}
    if mentioned:
        entry["mentioned_at"] = mentioned
    if oid:
        entry["id"] = oid
    if sources is not None:
        entry["source_memory_ids"] = sources
    if text:
        entry["text"] = text
    return entry


def _fact(
    oid: str,
    mentioned: str = "",
    tags: list[str] | None = None,
    fact_type: str = "world",
    consolidated: str = "",
    text: str = "",
) -> dict:
    entry: dict[str, Any] = {"id": oid, "fact_type": fact_type, "tags": tags or []}
    if mentioned:
        entry["mentioned_at"] = mentioned
    if consolidated:
        entry["consolidated_at"] = consolidated
    if text:
        entry["text"] = text
    return entry


def test_summarize_counts_only_observations():
    items = [
        _obs(1, []),
        _obs(3, ["session:x"]),
        {"fact_type": "world", "proof_count": 1, "tags": []},
        {"fact_type": "experience", "proof_count": 1, "tags": []},
    ]
    payload = health.summarize(items)
    assert payload["observations"] == 2
    assert payload["tagless"] == 1
    assert payload["single_proof"] == 1
    assert payload["raw_facts"] == 2


def test_summarize_reports_both_percentages():
    items = [_obs(1, []), _obs(1, ["session:a"]), _obs(2, []), _obs(4, [])]
    payload = health.summarize(items)
    # 3 of 4 tagless, 2 of 4 single-proof
    assert payload["tagless_pct"] == 75.0
    assert payload["single_proof_pct"] == 50.0


def test_merge_share_counts_the_observations_that_absorbed_more_than_one_fact():
    items = [_obs(1, oid="a"), _obs(2, oid="b"), _obs(4, oid="c")]
    payload = health.summarize(items)
    # This is the number the card leads with: 2 of 3 observations are merges.
    assert payload["merged"] == 2
    assert payload["merge_pct"] == 66.7


def test_orphans_count_raw_facts_that_no_observation_references():
    items = [
        _fact("f1"),
        _fact("f2"),
        _fact("f3", fact_type="experience"),
        _obs(1, oid="o1", sources=["f1"]),
        _obs(2, oid="o2", sources=["f2", "f3"]),
    ]
    payload = health.summarize(items)
    assert payload["raw_facts"] == 3
    assert payload["orphans"] == 0

    items.append(_fact("f4"))
    payload = health.summarize(items)
    assert payload["raw_facts"] == 4
    assert payload["orphans"] == 1


def test_orphan_count_ignores_references_to_unknown_units():
    items = [_fact("f1"), _obs(1, oid="o1", sources=["f1", "deleted-unit"])]
    payload = health.summarize(items)
    # A reference to a unit that is not in the bank must not push the count
    # below zero or hide a real orphan.
    assert payload["orphans"] == 0

    payload = health.summarize([_fact("f1"), _fact("f2"), _obs(1, oid="o1", sources=["f1"])])
    assert payload["orphans"] == 1


def test_orphans_split_into_backlog_and_consolidated_unreferenced():
    # Hindsight >= 0.10 merges a losing fact's content into the surviving
    # observation but does not keep the losing fact's source_memory_ids
    # reference. Such a fact is consolidated (consolidated_at set) and its
    # content is covered -- a bookkeeping artifact, not a backlog. Only facts
    # WITHOUT consolidated_at are a real "consolidation never ran here" signal.
    items = [
        _fact("f1"),                                                   # real backlog: no reference, never consolidated
        _fact("f2", consolidated="2026-10-03T05:00:00+00:00"),         # consolidated but unreferenced: benign
        _obs(1, oid="o1", sources=["f3"]),
        _fact("f3", consolidated="2026-10-03T05:00:00+00:00"),         # referenced: not an orphan at all
    ]
    payload = health.summarize(items)
    assert payload["orphans"] == 2                     # total unreferenced (unchanged definition)
    assert payload["orphan_backlog"] == 1              # the only real backlog fact
    assert payload["orphan_consolidated"] == 1         # benign: processed, just no surviving reference

    # A fact whose consolidated_at cannot be parsed counts as backlog: the
    # card must not silently upgrade an unknown state into "benign".
    items.append(_fact("f4", consolidated="not-a-date"))
    payload = health.summarize(items)
    assert payload["orphans"] == 3
    assert payload["orphan_backlog"] == 2
    assert payload["orphan_consolidated"] == 1


def test_lag_hours_is_the_gap_between_the_newest_fact_and_the_newest_observation():
    # The newest raw fact is at 02:30, the newest observation only at 00:00: the
    # consolidation is 2.5h behind the raw material that is waiting for it.
    items = [
        _fact("f1", mentioned="2026-10-03T02:30:00+00:00"),
        _obs(1, mentioned="2026-10-03T00:00:00+00:00", sources=["f1"]),
    ]
    assert health.summarize(items)["lag_hours"] == 2.5


def test_lag_is_clamped_at_zero_and_null_when_unmeasurable():
    # Observations newer than the newest raw fact are not "ahead of schedule".
    ahead = [_fact("f1", mentioned="2026-10-03T00:00:00+00:00"), _obs(1, mentioned="2026-10-03T05:00:00+00:00")]
    assert health.summarize(ahead)["lag_hours"] == 0.0

    # No raw facts (or no observations) means the gap cannot be measured: this
    # must be None, not 0.0, so the card never shows a perfect backlog.
    assert health.summarize([_obs(1, mentioned="2026-10-03T05:00:00+00:00")])["lag_hours"] is None
    assert health.summarize([_fact("f1", mentioned="2026-10-03T05:00:00+00:00")])["lag_hours"] is None
    assert health.summarize([_fact("f1"), _obs(1, sources=["f1"])])["lag_hours"] is None


def test_unmeasurable_metrics_are_null_instead_of_zero_without_the_population():
    payload = health.summarize([_fact("f1")])
    # None, not 0.0: "no data" must not read as a perfect score on the card.
    assert payload["tagless_pct"] is None
    assert payload["single_proof_pct"] is None
    assert payload["merge_pct"] is None


def test_thresholds_travel_with_the_payload_and_the_frozen_reference_is_gone():
    payload = health.summarize([_obs(1, sources=["f1"]), _fact("f1")])
    # The card colours on these, so they travel with the data instead of being
    # duplicated as magic numbers on the QML side.
    assert payload["thresholds"] == {
        "merge_target_pct": 25.0,
        "merge_floor_pct": 15.0,
        "lag_ok_hours": 2.0,
        "untagged_share_warn_pct": 50.0,
        "consolidated_window_hours": 24.0,
        "orphan_check_min_containment_pct": 20.0,
    }
    # The frozen pre-reset reference (1009 observations / 37.6% merges) is NOT
    # shipped any more: the rebuilt bank happened to sit at the same merge share,
    # so the number invited a "same as before" reading that means nothing. The
    # card carries a rolling trend of its own polls instead.
    assert "reference_observations" not in payload
    assert "reference_merge_pct" not in payload
    assert "reference_at" not in payload


def test_scope_stats_count_the_untagged_scope_separately():
    pages = {
        0: {
            "total": 3,
            "scopes": [
                {"tags": [], "count": 44},
                {"tags": ["scope:private", "session:a"], "count": 12},
                {"tags": ["scope:private"], "count": 7},
            ],
        }
    }
    original = health.fetch_json
    health.fetch_json = lambda url: pages[int(url.rsplit("offset=", 1)[1])]
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats is not None
    assert stats["scope_total"] == 3
    assert stats["untagged_scopes"] == 1
    assert stats["untagged_observations"] == 44
    # The largest scope is the merge pool the bank actually consolidates into.
    assert stats["largest_scope_observations"] == 44
    assert stats["largest_scope_share_pct"] == 69.8


def test_scope_stats_never_invent_a_total_without_rows_or_a_total_field():
    original = health.fetch_json
    # An empty page with no `total` field: nothing to count, so the total must be
    # None (the card prints "?") instead of a confident zero.
    health.fetch_json = lambda url: {"scopes": []}
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats is not None
    assert stats["scope_total"] is None
    assert stats["untagged_observations"] == 0
    assert stats["largest_scope_observations"] == 0
    assert stats["largest_scope_share_pct"] is None

    # A real zero reported by the endpoint stays a zero.
    health.fetch_json = lambda url: {"total": 0, "scopes": []}
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats is not None
    assert stats["scope_total"] == 0


def test_scope_stats_fall_back_to_the_row_count_for_the_total():
    original = health.fetch_json
    health.fetch_json = lambda url: {"scopes": [{"tags": [], "count": 5}]}
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats is not None
    assert stats["scope_total"] == 1
    assert stats["untagged_observations"] == 5


def test_scope_stats_return_none_when_the_endpoint_is_unreachable():
    original = health.fetch_json
    health.fetch_json = lambda url: None
    try:
        # None, not a zero-filled dict: an unreachable API must blank the card.
        assert health.collect_scope_stats("http://api", "hermes") is None
    finally:
        health.fetch_json = original


def test_timestamps_with_and_without_timezone_are_both_parsed():
    assert health.parse_timestamp("2026-09-25T10:00:00Z") is not None
    assert health.parse_timestamp("2026-09-25T10:00:00+02:00") is not None
    assert health.parse_timestamp("2026-09-25T10:00:00") is not None
    assert health.parse_timestamp("") is None
    assert health.parse_timestamp(None) is None
    assert health.parse_timestamp("not a date") is None


def test_collect_pages_until_a_short_page_arrives():
    pages = {
        0: {"items": [{"id": str(n)} for n in range(health.PAGE)]},
        health.PAGE: {"items": [{"id": "last"}]},
    }
    original = health.fetch_json
    health.fetch_json = lambda url: pages.get(int(url.rsplit("offset=", 1)[1]), {"items": []})
    try:
        items = health.collect("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert items is not None
    assert len(items) == health.PAGE + 1


def test_collect_returns_none_when_the_api_is_unreachable():
    original = health.fetch_json
    health.fetch_json = lambda url: None
    try:
        assert health.collect("http://api", "hermes") is None
    finally:
        health.fetch_json = original


def test_main_emits_an_explicit_error_payload_on_an_unreachable_api(capsys):
    original_collect = health.collect
    health.collect = lambda api, bank: None
    try:
        code = health.main()
    finally:
        health.collect = original_collect
    assert code == 1
    out = capsys.readouterr().out
    assert '"error":"unreachable"' in out


def test_scope_stats_report_the_tag_drift_behind_the_scope_count():
    # A scope is the tag set of the source fact, and consolidation only merges
    # inside one scope. A scope that merely adds a tag to an existing tag set is
    # therefore tag drift: it splits a pool that already existed. The untagged
    # scope ("" tags) is excluded from the comparison -- every tagged scope is a
    # superset of it, which would report drift for a perfectly normal bank.
    pages = {
        0: {
            "total": 4,
            "scopes": [
                {"tags": [], "count": 40},
                {"tags": ["mm:a"], "count": 10},
                {"tags": ["mm:a", "mm:b"], "count": 6},
                {"tags": ["mm:c"], "count": 3},
            ],
        }
    }
    original = health.fetch_json
    health.fetch_json = lambda url: pages[int(url.rsplit("offset=", 1)[1])]
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats is not None
    assert stats["scope_total"] == 4
    assert stats["drift_scopes"] == 1          # only {mm:a, mm:b} wraps an existing scope
    assert stats["untagged_share_pct"] == 67.8  # 40 of 59 observations sit in the catch-all scope


def test_scope_share_is_null_without_rows_and_zero_without_an_untagged_scope():
    original = health.fetch_json
    # A bank with tagged scopes only: 0.0 is a real reading, not "unknown".
    health.fetch_json = lambda url: {"total": 1, "scopes": [{"tags": ["mm:a"], "count": 4}]}
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats is not None
    assert stats["untagged_share_pct"] == 0.0
    assert stats["drift_scopes"] == 0


def test_consolidation_throughput_is_measured_from_the_consolidated_at_stamps():
    # LAG stays near zero as long as any recent fact got consolidated, so it
    # cannot show whether consolidation is running at all. Counting the
    # consolidated_at stamps inside the window does.
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    items = [
        _fact("f1", consolidated="2026-10-11T11:00:00+00:00"),   # 1h ago, referenced
        _fact("f2", consolidated="2026-10-11T06:00:00+00:00"),   # 6h ago, unreferenced
        _fact("f3", consolidated="2026-10-09T12:00:00+00:00"),   # outside the window
        _fact("f4"),                                            # never consolidated (backlog)
        _obs(2, oid="o1", sources=["f1"]),
    ]
    payload = health.summarize(items, now=now)
    assert payload["consolidated_recent"] == 2
    assert payload["consolidated_recent_unreferenced_pct"] == 50.0
    assert payload["consolidated_newest_hours"] == 1.0


def test_consolidation_throughput_is_null_instead_of_zero_without_the_population():
    payload = health.summarize([_obs(1)])
    # None, not 0: with no raw facts at all there is nothing to measure.
    assert payload["consolidated_recent"] is None
    assert payload["consolidated_newest_hours"] is None
    assert payload["consolidated_recent_unreferenced_pct"] is None

    # Facts that were never consolidated are a backlog, not throughput.
    payload = health.summarize([_fact("f1")])
    assert payload["consolidated_recent"] == 0
    assert payload["consolidated_newest_hours"] is None
    assert payload["consolidated_recent_unreferenced_pct"] is None


def test_orphan_audit_measures_whether_unreferenced_facts_left_a_trace():
    items = [
        _fact("f1", text="tokenaaa tokenbbb tokenccc tokenddd"),
        _fact("f2", text="tokeneEe tokenfff tokenGGG tokenhhh"),
        _fact("f3", text="tokeniii tokenjjj tokenkkk tokenlLl"),
        _fact("f9", text="referenced fact", consolidated="2026-10-11T11:00:00+00:00"),
        _obs(2, oid="o1", text="tokenaaa tokenbbb tokenccc tokenddd then tokeneEe later", sources=["f9"]),
        _obs(2, oid="o2", text="tokeneEe tokenfff survived", sources=["f9"]),
    ]
    payload = health.summarize(items)
    # f9 is referenced, so the sample is f1..f3.
    assert payload["orphan_check_sample"] == 3
    assert payload["orphan_check_verbatim"] == 1        # f1's text survives word for word
    assert payload["orphan_check_covered"] == 2         # f1 and f2 are covered above the threshold
    assert payload["orphan_check_median_containment"] == 0.5
    assert "rows" not in payload


def test_orphan_audit_is_deterministic_and_skips_facts_too_short_to_compare():
    items = [_fact("f1", text="zu kurz"), _fact("f2", text="ok")]
    audit = health.audit_orphans(items, [], sample_size=2)
    # Nothing distinctive to compare against: skipped, and never counted as a
    # failure -- a missing measurement must not read as content loss.
    assert audit["checked"] == 0
    assert audit["skipped"] == 2
    assert audit["median_containment"] is None

    many = [
        _fact(f"f{n}", text=f"token{n:03d}aaa token{n:03d}bbb token{n:03d}ccc token{n:03d}ddd")
        for n in range(9)
    ]
    first = health.audit_orphans(many, [], sample_size=3)
    second = health.audit_orphans(many, [], sample_size=3)
    ids = [row["id"] for row in first["rows"]]
    assert len(ids) == 3 and len(set(ids)) == 3
    # The same sample every poll: the number has to be comparable over time.
    assert ids == [row["id"] for row in second["rows"]]


def test_main_orphan_audit_mode_prints_the_sample_without_a_health_payload(capsys):
    items = [
        _fact("f1", text="tokenaaa tokenbbb tokenccc tokenddd"),
        _obs(1, oid="o1", text="tokenaaa tokenbbb tokenccc tokenddd", sources=["f9"]),
    ]
    original = health.collect
    health.collect = lambda api, bank: items
    try:
        code = health.main(["--orphan-audit", "--sample", "1"])
    finally:
        health.collect = original
    assert code == 0
    out = capsys.readouterr().out
    assert '"checked":1' in out
    assert '"verbatim":1' in out
    assert '"merge_pct"' not in out
