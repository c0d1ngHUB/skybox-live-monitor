#!/usr/bin/env python3
"""Emit local AI service status as a single JSON line.

This helper only probes localhost and local machine state. It never sends
secrets, credentials, or remote traffic.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import ProxyHandler, Request, build_opener


TIMEOUT = 2.5
LOCAL_OPENER = build_opener(ProxyHandler({}))


@dataclass
class ServiceStatus:
    state: str = "unknown"
    detail: str = ""


def run_capture(command: list[str], timeout: float = TIMEOUT) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def fetch_json(url: str) -> Any:
    try:
        req = Request(url, headers={"User-Agent": "skybox-live-monitor/1.0"})
        with LOCAL_OPENER.open(req, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
        return json.loads(raw)
    except (URLError, HTTPError, ValueError, TimeoutError, subprocess.SubprocessError):
        return None


def hermes_gateway_status() -> ServiceStatus:
    out = run_capture(["systemctl", "--user", "is-active", "hermes-gateway.service"])
    if out == "active":
        return ServiceStatus("running")
    if out:
        return ServiceStatus("down")
    return ServiceStatus("down")


def hindsight_status() -> ServiceStatus:
    data = fetch_json("http://127.0.0.1:9177/health")
    if isinstance(data, dict):
        healthy = data.get("status") in {"ok", "healthy"} or data.get("healthy") is True
        return ServiceStatus("healthy" if healthy else "error")
    return ServiceStatus("error")


def local_llm_status() -> tuple[ServiceStatus, str]:
    health = fetch_json("http://127.0.0.1:11435/health")
    if not isinstance(health, dict) or str(health.get("status", "")).lower() not in {"ok", "healthy"}:
        return ServiceStatus("error"), ""

    catalog = fetch_json("http://127.0.0.1:11435/v1/models")
    model_name = ""
    if isinstance(catalog, dict):
        models = catalog.get("models") or catalog.get("data") or []
        first = models[0] if isinstance(models, list) and models else None
        if isinstance(first, dict):
            model_name = str(first.get("name") or first.get("model") or first.get("id") or "").strip()
    return ServiceStatus("healthy"), model_name


def openai_oauth_availability() -> tuple[int, int]:
    """Read privacy-safe aggregate availability from the dedicated helper."""
    helper = Path(__file__).with_name("hermes_openai_keys.py")
    out = run_capture([sys.executable, str(helper)], timeout=5.0)
    match = re.fullmatch(r"(-?\d+)\s+(\d+)", out)
    if not match:
        return -1, -1
    return int(match.group(1)), int(match.group(2))


def main() -> int:
    gateway = hermes_gateway_status()
    hindsight = hindsight_status()
    local_llm, model_name = local_llm_status()
    openai_available, openai_total = openai_oauth_availability()
    payload = {
        "gateway": gateway.state,
        "hindsight": hindsight.state,
        "local_llm": local_llm.state,
        "local_llm_model": model_name,
        "openai_oauth_available": openai_available,
        "openai_oauth_total": openai_total,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
