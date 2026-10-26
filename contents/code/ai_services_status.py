#!/usr/bin/env python3
"""Emit local AI service status as a single JSON line.

This helper only probes localhost and local machine state. It never sends
secrets, credentials, or remote traffic.
"""

from __future__ import annotations

import json
from http.client import HTTPException
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import ProxyHandler, Request, build_opener


TIMEOUT = 2.5
LOCAL_OPENER = build_opener(ProxyHandler({}))


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
    except (URLError, HTTPError, HTTPException, OSError, ValueError, TimeoutError, subprocess.SubprocessError):
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
    data = fetch_json("http://127.0.0.1:9177/health")
    if isinstance(data, dict):
        healthy = data.get("status") in {"ok", "healthy"} or data.get("healthy") is True
        return ServiceStatus("healthy" if healthy else "error")
    return ServiceStatus("error")


def main() -> int:
    gateway = hermes_gateway_status()
    hindsight = hindsight_status()
    # OAuth key availability is polled separately by the widget
    # (hermes_openai_keys.py); running `hermes auth list` here every 15 s
    # duplicated that work at ~0.5 s CPU per poll.
    payload = {
        "gateway": gateway.state,
        "hindsight": hindsight.state,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
