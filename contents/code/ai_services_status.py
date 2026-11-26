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


def _operation_progress(operations: list) -> tuple[int, int] | None:
    """Sum the live worker progress over running operations, or None if unknown."""
    done = total = 0
    for operation in operations:
        progress = operation.get("progress") if isinstance(operation, dict) else None
        processed = progress.get("processed") if isinstance(progress, dict) else None
        op_total = progress.get("total") if isinstance(progress, dict) else None
        # One running job without a usable progress report makes the sum unknowable.
        if type(processed) is not int or processed < 0 or type(op_total) is not int or op_total < 0:
            return None
        done += processed
        total += op_total
    return done, total


def hindsight_consolidation_counts(base_url: str = HINDSIGHT_URL) -> dict[str, int | None] | None:
    """Count jobs, not facts, over every bank visible to the local API.

    ``done``/``total`` aggregate the worker progress of the running jobs so the
    dashboard can draw one real progress bar; they stay ``None`` while nothing
    runs or when any running job did not report progress.
    """
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

    def get_pending(bank_id: str) -> int | None:
        if monotonic() >= deadline:
            return None
        payload = fetch_json(f"{url}/{quote(bank_id, safe='')}/operations?type=consolidation&status=pending&limit=1")
        total = payload.get("total") if isinstance(payload, dict) else None
        return total if type(total) is int and total >= 0 else None

    def get_processing(bank_id: str) -> tuple[int, int | None, int | None] | None:
        if monotonic() >= deadline:
            return None
        # Running jobs are few; fetch their rows to read the live progress fields.
        payload = fetch_json(f"{url}/{quote(bank_id, safe='')}/operations?type=consolidation&status=processing&limit=100")
        if not isinstance(payload, dict):
            return None
        total = payload.get("total")
        if type(total) is not int or total < 0:
            return None
        operations = payload.get("operations")
        if total == 0 or not isinstance(operations, list) or len(operations) != total:
            # The count stands; the progress sum needs every running job in hand.
            return total, None, None
        summed = _operation_progress(operations)
        if summed is None:
            return total, None, None
        return total, summed[0], summed[1]

    counts = {"processing": 0, "pending": 0, "done": None, "total": None}
    processing_count = pending_count = 0
    done_sum = total_sum = None
    progress_known = True
    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = {bank_id: (executor.submit(get_pending, bank_id), executor.submit(get_processing, bank_id))
                for bank_id in ids}
        for bank_id, (pending_job, processing_job) in jobs.items():
            pending, processing = pending_job.result(), processing_job.result()
            if pending is None or processing is None:
                return None
            pending_count += pending
            count, done, op_total = processing
            processing_count += count
            if count > 0:
                if done is None or op_total is None:
                    progress_known = False
                else:
                    done_sum = (done_sum or 0) + done
                    total_sum = (total_sum or 0) + op_total
    if progress_known:
        counts["done"], counts["total"] = done_sum, total_sum
    counts["processing"], counts["pending"] = processing_count, pending_count
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
