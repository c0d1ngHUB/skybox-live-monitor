#!/usr/bin/env python3
"""Tests for contents/code/hindsight_observation_health.py.

The helper guards the observation_scopes="shared" switch, so the two things
worth pinning down are: it counts the right population (observations only, not
world/experience facts), and it never invents a number when the API is
unreachable -- a stale percentage on the card would be worse than no card.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

MODULE = Path(__file__).parents[1] / "contents/code/hindsight_observation_health.py"
spec = importlib.util.spec_from_file_location("hindsight_observation_health", MODULE)
assert spec and spec.loader
health = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = health
spec.loader.exec_module(health)


def _obs(proof: int, tags: list[str] | None = None, mentioned: str = "") -> dict:
    entry = {"fact_type": "observation", "proof_count": proof, "tags": tags or []}
    if mentioned:
        entry["mentioned_at"] = mentioned
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


def test_summarize_reports_both_percentages():
    items = [_obs(1, []), _obs(1, ["session:a"]), _obs(2, []), _obs(4, [])]
    payload = health.summarize(items)
    # 3 of 4 tagless, 2 of 4 single-proof
    assert payload["tagless_pct"] == 75.0
    assert payload["single_proof_pct"] == 50.0


def test_percentages_are_null_instead_of_zero_without_observations():
    payload = health.summarize([{"fact_type": "world", "proof_count": 1}])
    # None, not 0.0: "no data" must not read as a perfect score on the card.
    assert payload["tagless_pct"] is None
    assert payload["single_proof_pct"] is None


def test_baseline_is_carried_in_the_payload():
    payload = health.summarize([_obs(1, [])])
    assert payload["baseline_tagless_pct"] == 1.7
    assert payload["baseline_single_proof_pct"] == 62.4
    # The plateau measured while the switch was blind on the sweep path. The card
    # compares the post-switch cohort share against it.
    assert payload["baseline_cohort_tagless_pct"] == 0.6


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


def test_scope_stats_never_invent_a_total_without_rows_or_a_total_field():
    original = health.fetch_json
    # An empty page with no `total` field: nothing to count, so the total must be
    # None (the card prints "?") instead of a confident zero.
    health.fetch_json = lambda url: {"scopes": []}
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats == {"scope_total": None, "untagged_scopes": 0, "untagged_observations": 0}

    # A real zero reported by the endpoint stays a zero.
    health.fetch_json = lambda url: {"total": 0, "scopes": []}
    try:
        stats = health.collect_scope_stats("http://api", "hermes")
    finally:
        health.fetch_json = original
    assert stats == {"scope_total": 0, "untagged_scopes": 0, "untagged_observations": 0}


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


def test_since_switch_counts_only_rows_created_after_the_migration():
    before = _obs(1, ["session:old"], mentioned="2026-09-20T10:00:00+00:00")
    after = _obs(1, [], mentioned="2026-09-25T10:00:00+00:00")
    payload = health.summarize([before, after])
    # Old rows keep their tags for good, so only post-switch rows are evidence.
    assert payload["since_switch"] == 1
    assert payload["since_switch_tagless"] == 1
    assert payload["since_switch_tagless_pct"] == 100.0


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
