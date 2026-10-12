#!/usr/bin/env python3
"""Read Qwen's real unit state or invoke its existing GPU-handoff scripts.

Status is read-only. Start/stop never bypass the user's qwen38-start/qwen38-stop
scripts, never change boot settings and never stop the persistent gateway.
"""
from __future__ import annotations

import argparse
import fcntl
from http.client import HTTPException
import json
import os
from pathlib import Path
import subprocess
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

PROBE_TIMEOUT = 2.5
CONTROL_TIMEOUT = 900
LOCAL_OPENER = build_opener(ProxyHandler({}))


def fetch_props() -> dict | None:
    """/props does not wake the model; a sleeping server still counts as ON."""
    try:
        with LOCAL_OPENER.open("http://127.0.0.1:11437/props", timeout=PROBE_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (URLError, HTTPException, OSError, ValueError):
        return None


def container_state() -> str | None:
    """An empty successful list means absent; query failures remain unknown."""
    try:
        result = subprocess.run(
            ["docker", "container", "ls", "--all", "--filter", "name=^/qwen38-ninfer$",
             "--format", "{{.State}}"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    state = result.stdout.strip()
    return state if state in {"", "created", "running", "paused", "restarting", "removing", "exited", "dead"} else None


def qwen_status() -> dict:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "qwen38-ninfer.service",
             "--property=LoadState,ActiveState,SubState"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"state": "unknown", "detail": "Qwen-Dienstzustand nicht lesbar."}
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if props.get("LoadState") == "not-found":
        return {"state": "unavailable", "detail": "qwen38-ninfer.service ist nicht installiert."}
    if result.returncode != 0 or props.get("LoadState") != "loaded":
        return {"state": "unknown", "detail": "Qwen-Dienstzustand nicht lesbar."}
    state = {
        "active": "running", "inactive": "stopped", "failed": "failed",
        "activating": "starting", "deactivating": "stopping",
    }.get(props.get("ActiveState", ""), "unknown")
    detail = {
        "running": "Qwen ist eingeschaltet.", "stopped": "Qwen ist ausgeschaltet.",
        "failed": "Qwen-Dienst fehlgeschlagen; Start erneut versuchen.",
        "starting": "Qwen wird gestartet.", "stopping": "Qwen wird gestoppt.",
        "unknown": "Qwen-Dienstzustand unbekannt.",
    }[state]
    if state == "running":
        engine = container_state()
        if engine is None:
            return {"state": "unknown", "detail": "Qwen-Containerzustand nicht lesbar."}
        if engine != "running":
            return {"state": "failed", "detail": "Qwen-Container läuft nicht; Start erneut versuchen."}
        model = fetch_props()
        if model is not None and model.get("is_sleeping") is True:
            state, detail = "sleeping", "Qwen ist eingeschaltet und schläft; die Sitzung bleibt reserviert."
        elif model is None:
            detail = "Qwen-Dienst ist aktiv; Modell-API noch nicht erreichbar."
    return {"state": state, "detail": detail}


def run_action(action: str) -> dict:
    def outcome(ok: bool, error: str = "") -> dict:
        return {**qwen_status(), "ok": ok, "error": error}

    if action not in {"start", "stop"}:
        return outcome(False, "Unbekannte Qwen-Aktion.")
    script = Path.home() / ".local/bin" / f"qwen38-{action}"
    if not script.is_file() or not os.access(script, os.X_OK):
        return outcome(False, f"Start/Stop-Skript fehlt oder ist nicht ausführbar: {script.name}")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or (Path.home() / ".cache/skybox-live-monitor"))
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "skybox-qwen-control.lock").open("a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return outcome(False, "Eine andere Qwen-Umschaltung läuft bereits.")
            try:
                result = subprocess.run([str(script)], capture_output=True, text=True,
                                        timeout=CONTROL_TIMEOUT, check=False)
            except subprocess.TimeoutExpired:
                return outcome(False, "Zeitlimit bei Qwen-Umschaltung erreicht; Dienstzustand wurde erneut geprüft.")
            except (OSError, subprocess.SubprocessError) as error:
                return outcome(False, f"Qwen-Skript konnte nicht ausgeführt werden: {error}")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "Keine weiteren Angaben.").strip()[-1600:]
                return outcome(False, f"qwen38-{action} fehlgeschlagen (Exit-Code {result.returncode}): {detail}")
            current = outcome(True)
            expected = {"running", "sleeping"} if action == "start" else {"stopped"}
            if current["state"] not in expected:
                current.update(ok=False, error="Qwen-Umschaltung hat den erwarteten Dienstzustand nicht erreicht.")
            return current
    except OSError as error:
        return outcome(False, f"Qwen-Umschaltung konnte nicht gesperrt werden: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "start", "stop"), nargs="?", default="status")
    # Each executable source is unique: late completions from disconnected
    # requests must not be mistaken for a newer poll or button click.
    parser.add_argument("--request-id", type=int, default=0)
    args = parser.parse_args()
    result = {**qwen_status(), "ok": True, "error": ""} if args.action == "status" else run_action(args.action)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
