#!/usr/bin/env python3
"""Emit Hindsight observation-health indicators as a single JSON line.

Why this exists: on 2026-09-24 the retain path was switched to
``observation_scopes="shared"`` so that observations dedupe across the volatile
per-session tags instead of isolating one scope per session. Two indicators say
whether that took effect, and both must be read against the frozen baseline
measured just before the switch:

- ``tagless_pct``      share of observations carrying NO tags at all.
                       ``shared`` writes into the untagged scope, so this rises.
                       Baseline 1.7%.
- ``single_proof_pct`` share of observations backed by exactly one source fact.
                       Isolated per-session scopes could never merge, so this was
                       the dominant state. It should fall. Baseline 62.4%.

The migration timestamp matters because observations existing before it keep
their old tags for good -- ``observation_scopes`` is read from the SOURCE fact at
consolidation time and never re-derived for rows already written. Only rows
created after the switch are evidence about the switch.

Localhost only: it reads the local Hindsight API. No credentials, no writes, no
remote traffic. Never invokes a model, so polling it costs no inference tokens.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = os.environ.get("HINDSIGHT_API_URL", "http://127.0.0.1:9177").rstrip("/")
BANK = os.environ.get("HINDSIGHT_HEALTH_BANK", "hermes")
TIMEOUT = 10.0
PAGE = 5000
SCOPES_PAGE = 1000

# Frozen reference values, measured 2026-09-24 23:1x on bank "hermes" (2981 nodes,
# 1009 observations) immediately before the observation_scopes switch.
BASELINE_TAGLESS_PCT = 1.7
BASELINE_SINGLE_PROOF_PCT = 62.4

# Share of untagged rows among the observations created after the switch, measured
# 2026-09-25 07:45 -- i.e. while ONLY the plugin path carried observation_scopes.
# The sweep path wrote "combined" until 2026-09-25 08:06, so this is the plateau a
# working switch has to climb above. It is the sensitive signal: the all-time
# percentages move ~1.3 points per 20 new untagged rows and cannot show the fix.
BASELINE_COHORT_TAGLESS_PCT = 0.6

# The retain switch: ~/.hermes/hindsight/config.json gained "observation_scopes":
# "shared" and the gateway restarted at 2026-09-24 23:19:49 CEST.
MIGRATION_AT = "2026-09-24T23:17:00+02:00"


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


def collect_scope_stats(api: str, bank: str) -> dict | None:
    """Count observation-scope tag sets: how many exist and how many are untagged.

    This is the signal that actually moves when ``observation_scopes="shared"``
    starts working. The all-time percentages are diluted by every row ever
    written, so they hide the effect; the scope count responds immediately.

    Returns ``None`` when the API is unreachable, and ``{"scopes": None}`` when
    the endpoint exists but does not report a total -- the caller must then show
    ``total`` but no untagged share rather than inventing one.
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
    return {
        "scope_total": total,
        "untagged_scopes": len(untagged),
        "untagged_observations": untagged_count,
    }


def summarize(items: list[dict]) -> dict:
    observations = [entry for entry in items if str(entry.get("fact_type") or "") == "observation"]
    total = len(observations)
    tagless = sum(1 for entry in observations if not (entry.get("tags") or []))
    single = sum(1 for entry in observations if int(entry.get("proof_count") or 0) == 1)

    migration_at = parse_timestamp(MIGRATION_AT)
    fresh: list[dict] = []
    if migration_at is not None:
        for entry in observations:
            stamp = parse_timestamp(entry.get("mentioned_at")) or parse_timestamp(entry.get("date"))
            if stamp is not None and stamp >= migration_at:
                fresh.append(entry)
    fresh_tagless = sum(1 for entry in fresh if not (entry.get("tags") or []))

    def pct(part: int, whole: int) -> float | None:
        return round(100.0 * part / whole, 1) if whole else None

    # The cohort share is the sensitive signal: it is measured only over rows
    # created after the switch, so a fix in a retain path shows up here within
    # one consolidation cycle instead of being diluted by 1500 legacy rows.
    cohort_tagless_pct = pct(fresh_tagless, len(fresh))

    payload = {
        "observations": total,
        "tagless": tagless,
        "tagless_pct": pct(tagless, total),
        "single_proof": single,
        "single_proof_pct": pct(single, total),
        "since_switch": len(fresh),
        "since_switch_tagless": fresh_tagless,
        "since_switch_tagless_pct": cohort_tagless_pct,
        "baseline_tagless_pct": BASELINE_TAGLESS_PCT,
        "baseline_single_proof_pct": BASELINE_SINGLE_PROOF_PCT,
        "baseline_cohort_tagless_pct": BASELINE_COHORT_TAGLESS_PCT,
        "migration_at": MIGRATION_AT,
    }
    return payload


def main() -> int:
    items = collect(API, BANK)
    if items is None:
        # Explicit drift signal, mirroring the other helpers: an unreachable API
        # resets the card instead of leaving stale values on screen.
        print(json.dumps({"error": "unreachable"}, separators=(",", ":")))
        return 1
    payload = summarize(items)
    payload["bank"] = BANK

    # Scope stats are the sensitive signal; they are optional so a broken scope
    # endpoint degrades the card detail instead of blanking it entirely.
    scope_stats = collect_scope_stats(API, BANK)
    if scope_stats is not None:
        payload.update(scope_stats)
    elif scope_stats is None:
        payload["scope_total"] = None

    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
