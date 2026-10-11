#!/usr/bin/env python3
"""Emit Hindsight observation-health indicators as a single JSON line.

Why this exists: the bank "hermes" was deleted and rebuilt on 2026-10-02 22:15
UTC, so the earlier guard -- comparing rows created after the
``observation_scopes="shared"`` switch against a frozen pre-switch plateau -- has
no reference left. The card therefore reports metrics that survive a rebuild and
that actually move when the quality of consolidation changes:

- ``merge_pct``  share of observations backed by MORE than one raw fact. This is
                 the direct quality signal: consolidation that never merges can
                 only produce 1:1 copies of raw facts. Higher is better.
- ``orphans``    raw facts (world/experience) that no observation references yet.
- ``orphan_backlog``  the subset of ``orphans`` WITHOUT ``consolidated_at`` --
                 the real "consolidation never ran here" signal the health
                 rule gates on. Hindsight >= 0.10 merges a losing fact's
                 content into the surviving observation without keeping its
                 source_memory_ids reference, so a processed fact can still
                 count in ``orphans``; ``orphan_consolidated`` is that benign
                 bookkeeping remainder and does NOT fail the card.
- ``lag_hours``  distance between the newest raw fact and the newest observation.
                 Small is good, but it is a weak signal on its own: observation
                 timestamps are copied from the source fact, so one recent
                 consolidation keeps it at 0.0h. ``consolidated_recent`` counts the
                 ``consolidated_at`` stamps of the last window instead, which is
                 what actually shows whether consolidation is running.
- ``scope_total`` distinct observation-scope tag sets. ``observation_scopes`` is
                 derived from the SOURCE fact's tags, and consolidation only
                 merges inside one scope -- so the scope split caps the merge
                 pool. A rising count with no new topic means tag drift, which
                 ``drift_scopes`` counts directly: scopes whose tag set merely
                 wraps an existing one. ``untagged_share_pct`` says how much of the
                 bank sits in the single untagged catch-all scope, because a count
                 of 51 scopes reads very differently when one of them holds 81%.
- ``orphan_check_*``  a deterministic sample audit of the unreferenced facts: how
                 much of each fact's own vocabulary survives in an observation.
                 Low containment is EXPECTED (consolidation paraphrases and the
                 fact row itself stays valid), so this is a baseline for changes,
                 never a pass/fail gate. ``--orphan-audit`` prints the rows on
                 demand.

The card colours on the thresholds shipped in ``thresholds`` so the numbers live
in one place instead of being duplicated as magic values in QML.

Context values stay in the tooltip only: the all-time tagless/single-proof
percentages are diluted by every row ever written.

Localhost only: it reads the local Hindsight API. No credentials, no writes, no
remote traffic. Never invokes a model, so polling it costs no inference tokens --
everything here is counted from rows the API already returns.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = os.environ.get("HINDSIGHT_API_URL", "http://127.0.0.1:9177").rstrip("/")
BANK = os.environ.get("HINDSIGHT_HEALTH_BANK", "hermes")
TIMEOUT = 10.0
PAGE = 5000
SCOPES_PAGE = 1000

RAW_FACT_TYPES = ("world", "experience")

# Colour thresholds. Kept here (not in QML) so the card and any offline analysis
# agree, and shipped inside the payload as ``thresholds``.
MERGE_TARGET_PCT = 25.0
MERGE_FLOOR_PCT = 15.0
LAG_OK_HOURS = 2.0
# Share of observations sitting in the untagged catch-all scope above which the
# tooltip calls the scope split out: the merge pool is then one big bucket.
UNTAGGED_SHARE_WARN_PCT = 50.0
# Window for the consolidation throughput (``consolidated_recent``).
CONSOLIDATED_WINDOW_HOURS = 24.0
# Content-trace audit of the unreferenced facts (``orphan_check_*``). The audit works
# with a 0..1 fraction; the payload ships the percentage so every threshold the card
# reads is a percentage.
ORPHAN_CHECK_SAMPLE = 24
ORPHAN_CHECK_MIN_CONTAINMENT = 0.2
ORPHAN_CHECK_MIN_CONTAINMENT_PCT = round(ORPHAN_CHECK_MIN_CONTAINMENT * 100.0, 1)
ORPHAN_TOKEN_MIN_LEN = 6
ORPHAN_TOKEN_MIN_COUNT = 4
ORPHAN_VERBATIM_CHARS = 40

# A word long enough to be a content fingerprint. Bare numbers (ids, timestamps,
# version strings) appear all over the bank and would make unrelated rows look
# related, so they are filtered out after the match.
TOKEN_RE = re.compile(r"[0-9A-Za-z_.\-/]{%d,}" % ORPHAN_TOKEN_MIN_LEN)


def fetch_json(url: str):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "hindsight-observation-health/1.0"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError):
        return None


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pct(part: int, whole: int) -> float | None:
    """Percentage as a float, or None when there is no population to divide by.

    None rather than 0.0: an empty bank must not read as a perfect score.
    """
    return round(100.0 * part / whole, 1) if whole else None


def distinctive_tokens(text: object) -> set[str]:
    """Long-enough words of ``text``, lowercased, bare numbers dropped."""
    if not isinstance(text, str) or not text:
        return set()
    tokens = set()
    for word in TOKEN_RE.findall(text):
        digits = word.replace("-", "").replace("_", "").replace(".", "").replace("/", "")
        if digits.isdigit():
            continue
        tokens.add(word.lower())
    return tokens


def normalized(text: object) -> str:
    """Whitespace-collapsed lowercase text, for the verbatim containment check."""
    return " ".join(text.split()).lower() if isinstance(text, str) else ""


def even_sample(entries: list, size: int) -> list:
    """Deterministic sample spread over the whole list: no RNG, same input -> same pick.

    A random sample would move every poll and make the audit number incomparable;
    an evenly spread one also covers the whole bank instead of only its newest rows.
    """
    if size <= 0 or not entries:
        return []
    if len(entries) <= size:
        return list(entries)
    if size == 1:
        return [entries[0]]
    picked = []
    seen = set()
    for index in range(size):
        position = round(index * (len(entries) - 1) / (size - 1))
        if position in seen:
            continue
        seen.add(position)
        picked.append(entries[position])
    return picked


def collect(api: str, bank: str) -> list[dict] | None:
    items: list[dict] = []
    offset = 0
    while True:
        page = fetch_json(f"{api}/v1/default/banks/{bank}/memories/list?limit={PAGE}&offset={offset}")
        if not isinstance(page, dict):
            return None
        batch = page.get("items") or page.get("memories") or []
        if not isinstance(batch, list):
            return None
        items.extend(entry for entry in batch if isinstance(entry, dict))
        if len(batch) < PAGE:
            break
        offset += PAGE
        if offset > 200000:  # runaway guard
            break
    return items


def split_memories(items: list[dict]) -> tuple[list[dict], list[dict], set[str], list[dict]]:
    """Split the bank into observations, raw facts, referenced ids and orphans.

    A raw fact counts as orphaned when no observation points at it. A fact
    carrying no id cannot be matched at all, so it is not scored as orphan --
    a missing id is a different defect and must not inflate this number.
    """
    observations = [entry for entry in items if str(entry.get("fact_type") or "") == "observation"]
    raw_facts = [entry for entry in items if str(entry.get("fact_type") or "") in RAW_FACT_TYPES]
    referenced: set[str] = set()
    for entry in observations:
        for source_id in entry.get("source_memory_ids") or []:
            referenced.add(str(source_id))
    orphan_entries = [entry for entry in raw_facts if entry.get("id") and str(entry["id"]) not in referenced]
    return observations, raw_facts, referenced, orphan_entries


def collect_scope_stats(api: str, bank: str) -> dict | None:
    """Count observation-scope tag sets and how the observations distribute over them.

    This is the signal that says whether consolidation even has a merge pool:
    ``observation_scopes`` comes from the source fact's tags, and merging only
    happens inside one scope, so a fragmented bank cannot merge.

    Returns ``None`` when the API is unreachable, and ``{"scopes": None}``
    when the endpoint exists but reports no rows and no total -- the caller must
    then show ``?`` rather than inventing a zero.
    """
    scopes: list[dict] = []
    offset = 0
    while True:
        page = fetch_json(f"{api}/v1/default/banks/{bank}/observations/scopes?limit={SCOPES_PAGE}&offset={offset}")
        if not isinstance(page, dict):
            return None
        batch = page.get("scopes")
        if not isinstance(batch, list):
            return {"scopes": None}
        scopes.extend(entry for entry in batch if isinstance(entry, dict))
        if len(batch) < SCOPES_PAGE:
            break
        offset += SCOPES_PAGE
        if offset > 100000:  # runaway guard
            break

    total = None
    reported = page.get("total")
    if isinstance(reported, int):
        total = reported
    elif scopes:
        total = len(scopes)

    untagged = [entry for entry in scopes if not (entry.get("tags") or [])]
    untagged_count = sum(int(entry.get("count") or 0) for entry in untagged)

    counts = [int(entry.get("count") or 0) for entry in scopes]
    largest = max(counts) if counts else 0
    summed = sum(counts)

    # Tag drift: a scope whose tag set merely wraps another, non-empty tag set.
    # Consolidation can only merge inside one scope, so such a scope splits a pool
    # that already existed. The untagged scope is excluded from the comparison --
    # every tagged scope is a superset of it, which would flag a normal bank.
    tagged_sets = [frozenset(entry.get("tags") or []) for entry in scopes]
    tagged_sets = [tags for tags in tagged_sets if tags]
    drift_scopes = sum(1 for tags in tagged_sets if any(other < tags for other in tagged_sets))

    return {
        "scope_total": total,
        "untagged_scopes": len(untagged),
        "untagged_observations": untagged_count,
        "untagged_share_pct": _pct(untagged_count, summed),
        "drift_scopes": drift_scopes,
        "tagged_scopes": len(tagged_sets),
        "largest_scope_observations": largest,
        "largest_scope_share_pct": _pct(largest, summed),
    }


def audit_orphans(
    orphan_facts: list[dict],
    observations: list[dict],
    sample_size: int = ORPHAN_CHECK_SAMPLE,
    min_containment: float = ORPHAN_CHECK_MIN_CONTAINMENT,
) -> dict:
    """Measure what the unreferenced facts left behind in the observations.

    Hindsight merges a losing fact's content into the surviving observation but
    keeps no source reference, so ``orphans`` cannot be judged from the reference
    lists alone. This samples the unreferenced facts deterministically and measures
    per fact how much of its own vocabulary shows up inside a single observation:

    * ``verbatim``    the fact's text survives word for word in an observation.
    * ``containment`` share of the fact's distinctive tokens present in the best
                      matching observation.

    Low containment is EXPECTED and not a defect: consolidation rewrites the
    content, and the fact row itself stays in the bank, valid and retrievable. The
    value is a baseline -- if it collapses between two days, content is being lost
    instead of paraphrased. Rows with too few distinctive tokens are SKIPPED rather
    than counted as uncovered, because a missing measurement must not read as loss.
    """
    observation_tokens: list[set[str]] = []
    observation_texts: list[str] = []
    for entry in observations:
        tokens = distinctive_tokens(entry.get("text"))
        if not tokens:
            continue
        observation_tokens.append(tokens)
        observation_texts.append(normalized(entry.get("text")))

    rows = []
    skipped = 0
    for entry in even_sample(orphan_facts, sample_size):
        text = entry.get("text")
        tokens = distinctive_tokens(text)
        if len(tokens) < ORPHAN_TOKEN_MIN_COUNT:
            skipped += 1
            continue
        needle = normalized(text)[:ORPHAN_VERBATIM_CHARS]
        verbatim = bool(needle) and any(needle in hay for hay in observation_texts)
        best = 0.0
        for other in observation_tokens:
            overlap = len(tokens & other)
            if overlap:
                best = max(best, overlap / len(tokens))
        rows.append({"id": str(entry.get("id") or ""), "containment": round(best, 2), "verbatim": verbatim})

    containments = [row["containment"] for row in rows]
    return {
        "checked": len(rows),
        "skipped": skipped,
        "verbatim": sum(1 for row in rows if row["verbatim"]),
        "covered": sum(1 for row in rows if row["containment"] >= min_containment),
        "median_containment": round(statistics.median(containments), 2) if containments else None,
        "min_containment": min_containment,
        "window": ORPHAN_CHECK_SAMPLE,
        "rows": rows,
    }


def summarize(items: list[dict], now: datetime | None = None) -> dict:
    observations, raw_facts, referenced, orphan_entries = split_memories(items)

    total = len(observations)
    tagless = sum(1 for entry in observations if not (entry.get("tags") or []))
    single = sum(1 for entry in observations if int(entry.get("proof_count") or 0) == 1)
    merged = sum(1 for entry in observations if int(entry.get("proof_count") or 0) >= 2)

    orphans = len(orphan_entries)

    # Split the orphans by why they are unreferenced. Hindsight >= 0.10 merges
    # a losing fact's content into the surviving observation WITHOUT keeping
    # the losing fact's source_memory_ids reference, so a fact can be fully
    # processed (consolidated_at set) yet count as orphaned. That is a
    # bookkeeping artifact of upstream, not a backlog: the health rule
    # therefore gates on ``orphan_backlog`` only, while ``orphans`` keeps its
    # original total-unreferenced meaning for trend comparability.
    # Unparseable consolidated_at counts as backlog -- an unknown state must
    # not silently read as benign.
    orphan_backlog = sum(
        1 for entry in orphan_entries if parse_timestamp(entry.get("consolidated_at")) is None
    )
    orphan_consolidated = orphans - orphan_backlog

    def stamp(entry: dict) -> datetime | None:
        return parse_timestamp(entry.get("mentioned_at")) or parse_timestamp(entry.get("date"))

    # Backlog: how far the newest observation trails the newest raw fact.
    # Unmeasurable (no raw facts, no observations, no timestamps) is None, and a
    # negative gap is clamped to 0 -- an observation newer than the newest raw
    # fact is not "ahead of schedule", it is just not a backlog.
    fact_times = [t for t in (stamp(entry) for entry in raw_facts) if t is not None]
    obs_times = [t for t in (stamp(entry) for entry in observations) if t is not None]
    lag_hours = None
    if fact_times and obs_times:
        lag_hours = round(max(0.0, (max(fact_times) - max(obs_times)).total_seconds() / 3600.0), 1)

    # Throughput: LAG stays near zero as long as ANY recent fact got consolidated,
    # so on its own it cannot show whether consolidation is running. Counting the
    # consolidated_at stamps inside the window does, and splitting them by whether
    # a surviving reference exists shows how much of it the card can see.
    moment = now or datetime.now(timezone.utc)
    recent_consolidated = []
    newest_consolidated = None
    for entry in raw_facts:
        consolidated_at = parse_timestamp(entry.get("consolidated_at"))
        if consolidated_at is None:
            continue
        if newest_consolidated is None or consolidated_at > newest_consolidated:
            newest_consolidated = consolidated_at
        if (moment - consolidated_at).total_seconds() <= CONSOLIDATED_WINDOW_HOURS * 3600.0:
            recent_consolidated.append(entry)
    consolidated_recent = len(recent_consolidated) if raw_facts else None
    consolidated_newest_hours = (
        round(max(0.0, (moment - newest_consolidated).total_seconds() / 3600.0), 1)
        if newest_consolidated is not None
        else None
    )
    consolidated_recent_unreferenced_pct = (
        _pct(
            sum(1 for entry in recent_consolidated if str(entry.get("id") or "") not in referenced),
            len(recent_consolidated),
        )
        if recent_consolidated
        else None
    )

    audit = audit_orphans(orphan_entries, observations)

    return {
        "observations": total,
        "raw_facts": len(raw_facts),
        "tagless": tagless,
        "tagless_pct": _pct(tagless, total),
        "single_proof": single,
        "single_proof_pct": _pct(single, total),
        "merged": merged,
        "merge_pct": _pct(merged, total),
        "orphans": orphans,
        "orphan_backlog": orphan_backlog,
        "orphan_consolidated": orphan_consolidated,
        "lag_hours": lag_hours,
        "consolidated_recent": consolidated_recent,
        "consolidated_recent_unreferenced_pct": consolidated_recent_unreferenced_pct,
        "consolidated_newest_hours": consolidated_newest_hours,
        "orphan_check_sample": audit["checked"],
        "orphan_check_skipped": audit["skipped"],
        "orphan_check_verbatim": audit["verbatim"],
        "orphan_check_covered": audit["covered"],
        "orphan_check_median_containment": audit["median_containment"],
        "thresholds": {
            "merge_target_pct": MERGE_TARGET_PCT,
            "merge_floor_pct": MERGE_FLOOR_PCT,
            "lag_ok_hours": LAG_OK_HOURS,
            "untagged_share_warn_pct": UNTAGGED_SHARE_WARN_PCT,
            "consolidated_window_hours": CONSOLIDATED_WINDOW_HOURS,
            "orphan_check_min_containment_pct": ORPHAN_CHECK_MIN_CONTAINMENT_PCT,
        },
    }


def orphan_audit_report(args: list[str]) -> int:
    """Print the content-trace audit with the sampled rows attached.

    ``main()`` ships the same numbers on every poll; this form exists to look at
    WHICH unreferenced facts left no trace, so it is the one to run daily or when
    the card's median moves.
    """
    sample_size = ORPHAN_CHECK_SAMPLE
    if "--sample" in args:
        index = args.index("--sample")
        if index + 1 < len(args):
            try:
                sample_size = max(1, int(args[index + 1]))
            except ValueError:
                sample_size = ORPHAN_CHECK_SAMPLE

    items = collect(API, BANK)
    if items is None:
        print(json.dumps({"error": "unreachable"}, separators=(",", ":")))
        return 1
    observations, raw_facts, _, orphan_entries = split_memories(items)
    report = audit_orphans(orphan_entries, observations, sample_size=sample_size)
    report["bank"] = BANK
    report["orphans"] = len(orphan_entries)
    report["raw_facts"] = len(raw_facts)
    print(json.dumps(report, separators=(",", ":"), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--orphan-audit" in args:
        return orphan_audit_report(args)

    items = collect(API, BANK)
    if items is None:
        # Explicit drift signal, mirroring the other helpers: an unreachable API
        # resets the card instead of leaving stale values on screen.
        print(json.dumps({"error": "unreachable"}, separators=(",", ":")))
        return 1
    payload = summarize(items)
    payload["bank"] = BANK

    # Scope stats are the fragmentation signal; they are optional so a broken
    # scope endpoint degrades the card detail instead of blanking it entirely.
    scope_stats = collect_scope_stats(API, BANK)
    if scope_stats is not None:
        payload.update(scope_stats)
    else:
        payload["scope_total"] = None

    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
