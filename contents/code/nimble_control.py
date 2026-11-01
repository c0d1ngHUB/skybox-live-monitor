#!/usr/bin/env python3
"""Widget bridge to ~/.local/bin/nimble-ctl (Nimble v3 residency: auto/hold).

Always prints exactly one JSON object, also when nimble-ctl is missing or broken,
so the QML side never has to parse free text.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

TIMEOUTS = {"status": 10, "hold": 420, "auto": 90}
KEYS = ("mode", "service", "loaded", "vram_mib", "blocker", "note", "ok", "error")


def ctl_path() -> Path:
    return Path(os.environ.get("NIMBLE_CTL", Path.home() / ".local/bin/nimble-ctl"))


def failure(message: str, state: str = "unknown") -> dict:
    return {"mode": "unknown", "service": state, "loaded": None, "vram_mib": None,
            "blocker": "", "note": "", "ok": False, "error": message}


def run(action: str) -> dict:
    ctl = ctl_path()
    if not ctl.is_file() or not os.access(ctl, os.X_OK):
        return failure(f"{ctl.name} fehlt oder ist nicht ausführbar.", "unavailable")
    try:
        result = subprocess.run([str(ctl), action], capture_output=True, text=True,
                                timeout=TIMEOUTS[action], check=False)
    except subprocess.TimeoutExpired:
        return failure(f"Zeitlimit bei Nimble-{action} erreicht.")
    except OSError as error:
        return failure(f"nimble-ctl nicht ausführbar: {error}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = (result.stderr or result.stdout or "keine Ausgabe").strip()[-600:]
        return failure(f"nimble-ctl lieferte kein JSON: {detail}")
    if not isinstance(payload, dict) or payload.get("mode") not in {"auto", "hold"}:
        return failure("nimble-ctl lieferte einen ungültigen Zustand.")
    return {key: payload.get(key) for key in KEYS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=tuple(TIMEOUTS), nargs="?", default="status")
    parser.add_argument("--request-id", type=int, default=0)
    args = parser.parse_args()
    result = run(args.action)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
