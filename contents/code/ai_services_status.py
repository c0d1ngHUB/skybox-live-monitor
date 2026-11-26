#!/usr/bin/env python3
"""Emit local AI service status as a single JSON line.

This helper only probes localhost and local machine state. It never sends
secrets, credentials, or remote traffic.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from http.client import HTTPException
import subprocess
import sys
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


TIMEOUT = 2.5
HINDSIGHT_URL = "http://127.0.0.1:9177"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


LOCAL_OPENER = build_opener(ProxyHandler({}), NoRedirect())


@dataclass
class ServiceStatus:
    state: str = "unknown"
    detail: str = ""


def fetch_json(url: str) -> Any:
    try:
        req = Request(url, headers={"User-Agent": "skybox-live-monitor/1.0"})
        with LOCAL_OPENER.open(req, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
        return json.loads(raw)
    except (HTTPException, OSError, ValueError, RecursionError):
        return None


def hermes_gateway_status() -> ServiceStatus:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "hermes-gateway.service"],
            capture_output=True, text=True, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ServiceStatus("unknown")
    out = (result.stdout or "").strip()
    if result.returncode == 0 and out == "active":
        return ServiceStatus("running")
    if result.returncode in {0, 3} and out in {"inactive", "failed"}:
        return ServiceStatus("down")
    return ServiceStatus("unknown")


def hindsight_status() -> ServiceStatus:
    data = fetch_json(f"{HINDSIGHT_URL}/health")
    if isinstance(data, dict):
        healthy = data.get("status") in ("ok", "healthy") or data.get("healthy") is True
        return ServiceStatus("healthy" if healthy else "error")
    return ServiceStatus("error")


def hindsight_consolidation_counts(base_url: str = HINDSIGHT_URL) -> dict[str, int] | None:
    """Count jobs, not facts, over every bank visible to the local API."""
    url = f"{base_url}/v1/default/banks"
    # Bound the poll; do not report a partial bank inventory as a global count.
    catalog = fetch_json(f"{url}?limit=100")
    if not isinstance(catalog, dict):
        return None
    banks, total = catalog.get("banks"), catalog.get("total")
    if not isinstance(banks, list) or type(total) is not int or total != len(banks) or total > 100:
        return None
    ids: set[str] = set()
    for bank in banks:
        bank_id = bank.get("bank_id") if isinstance(bank, dict) else None
        if not isinstance(bank_id, str) or not bank_id or bank_id in ids:
            return None
        ids.add(bank_id)

    # Don't start new requests after a slow API has consumed the poll budget.
    deadline = monotonic() + 5

    def get_count(job: tuple[str, str]) -> tuple[str, int | None]:
        bank_id, status = job
        if monotonic() >= deadline:
            return status, None
        payload = fetch_json(f"{url}/{quote(bank_id, safe='')}/operations?type=consolidation&status={status}&limit=1")
        total = payload.get("total") if isinstance(payload, dict) else None
        return status, total if type(total) is int and total >= 0 else None

    counts = {"processing": 0, "pending": 0}
    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = ((bank_id, status) for bank_id in ids for status in counts)
        for status, total in executor.map(get_count, jobs):
            if total is None:
                return None
            counts[status] += total
    return counts


def main() -> int:
    gateway = hermes_gateway_status()
    hindsight = hindsight_status()
    payload = {
        "gateway": gateway.state,
        "hindsight": hindsight.state,
        "consolidation": hindsight_consolidation_counts() if hindsight.state == "healthy" else None,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
