#!/usr/bin/env python3
"""Widget bridge for the MOSS-TTS voice service (hermes-gpu-scheduler.service).

MOSS-TTS is the /voice speech output. The scheduler unit owns it: for one voice reply at a
time it loads MOSS onto the Blackwell and frees the memory again, so an idle service costs
no VRAM. This helper switches that unit on and off for the AI SERVICES button.

  status  read-only state
  start   start the unit and wait until the scheduler API answers
  stop    wait until no voice job is running, then stop the unit (systemd takes a MOSS
          child process of the unit down with it)

Always prints exactly one JSON object, also on unexpected errors, so the QML side never has
to parse free text. The exit status is 0 only when "ok" is true.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

UNIT = "hermes-gpu-scheduler.service"
SCHEDULER_URL = os.environ.get("MOSS_SCHEDULER_URL", "http://127.0.0.1:8097")
SYSTEMCTL = os.environ.get("MOSS_SYSTEMCTL", "systemctl")
CONFIG = Path(os.environ.get("MOSS_CONFIG",
                             Path.home() / ".local/state/moss-tts/scheduler-config.json"))
DEFAULT_REQUIRED_MIB = 19000  # hermes_gpu_scheduler Config.moss_required_free_mib
START_WAIT = float(os.environ.get("MOSS_START_WAIT", "40"))
STOP_WAIT = float(os.environ.get("MOSS_STOP_WAIT", "150"))
POLL = float(os.environ.get("MOSS_POLL", "1"))
PROC_ROOT = Path(os.environ.get("MOSS_PROC_ROOT", "/proc"))
OPENER = build_opener(ProxyHandler({}))  # loopback only, never through a proxy
RUNNING = {"active", "activating", "reloading"}
STOPPED = {"inactive", "failed"}  # a removed unit also reports "inactive" (exit 4)


class ControlError(Exception):
    """A failure with a message that is safe to show on the button tooltip."""


def payload(mode: str = "unknown", service: str = "unknown", ready: bool = False,
            busy: bool = False, state: str = "", queue_len: int | None = None,
            free_mib: int | None = None, required_mib: int | None = None,
            blocker: str = "", note: str = "", ok: bool = True, error: str = "") -> dict:
    return {"mode": mode, "service": service, "ready": ready, "busy": busy, "state": state,
            "queue_len": queue_len, "free_mib": free_mib, "required_mib": required_mib,
            "blocker": blocker, "note": note, "ok": ok, "error": error}


def systemctl(*args: str, timeout: float) -> str:
    """Run `systemctl --user <args> <unit>`; non-zero exit raises with systemctl's own text."""
    try:
        result = subprocess.run([SYSTEMCTL, "--user", *args, UNIT], capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ControlError(f"systemctl {args[0]} antwortet nach {timeout:.0f} s nicht.")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlError(f"systemctl ist nicht ausführbar: {exc}")
    if result.returncode != 0 and args[0] != "is-active":
        detail = (result.stderr or result.stdout).strip()[-600:]
        raise ControlError(detail or f"systemctl {args[0]} endete mit Status {result.returncode}.")
    return result.stdout.strip()


def unit_state() -> str:
    return systemctl("is-active", timeout=5) or "unknown"


def api_status() -> dict | None:
    """The scheduler's /status, or None while it does not answer (down or still starting)."""
    try:
        with OPENER.open(SCHEDULER_URL + "/status", timeout=3) as response:
            data = json.loads(response.read())
    except (URLError, OSError, ValueError, http.client.HTTPException):
        return None
    return data if isinstance(data, dict) and data.get("ok") is True else None


MOSS_SCRIPT_SUFFIX = "/hermes_gpu_scheduler/moss_service.py"


def is_moss_service_argv(argv: list[str]) -> bool:
    """True for the scheduler's MOSS child: `<python> .../hermes_gpu_scheduler/moss_service.py`
    (moss_proc.py starts exactly this). A substring test on the whole command line is not
    enough: a shell, editor or grep that merely mentions the script would count as a job."""
    return (len(argv) >= 2 and Path(argv[0]).name.startswith("python")
            and any(arg.endswith(MOSS_SCRIPT_SUFFIX) for arg in argv[1:]))


def moss_process_running() -> bool:
    """A MOSS inference process of the scheduler exists. It holds the Blackwell, so it
    counts as a voice job even when the API is silent."""
    try:
        entries = list(PROC_ROOT.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue  # the process ended while scanning
        if is_moss_service_argv([arg for arg in argv if arg]):
            return True
    return False


def is_busy(api: dict | None) -> bool:
    if api is not None and (bool(api.get("current_job")) or int(api.get("queue_len") or 0) > 0):
        return True
    return moss_process_running()


def required_mib() -> int:
    try:
        value = json.loads(CONFIG.read_text()).get("moss_required_free_mib")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_REQUIRED_MIB
    valid = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return int(value) if valid else DEFAULT_REQUIRED_MIB


def default_note(service: str, api: dict | None, busy: bool) -> str:
    if service == "failed":
        return "Der Dienst ist fehlgeschlagen; Starten versucht es erneut."
    if service in STOPPED:
        return "MOSS ist beendet; Sprachantworten kommen nur als Text."
    if service == "deactivating":
        return "Der Dienst wird gerade beendet."
    if api is None:
        return "Der Dienst läuft, der Scheduler ist noch nicht bereit."
    if busy:
        return "MOSS erzeugt gerade eine Sprachantwort."
    return "MOSS ist bereit und belegt nur während einer Sprachantwort Grafikspeicher."


def snapshot(note: str = "", ok: bool = True, error: str = "") -> dict:
    service = unit_state()
    mode = "on" if service in RUNNING else "off" if service in STOPPED else "unknown"
    api = api_status() if mode == "on" else None
    busy = mode == "on" and is_busy(api)
    free = api.get("gpu_free_mib") if api else None
    free = int(free) if isinstance(free, (int, float)) and not isinstance(free, bool) else None
    need = required_mib()
    blocker = ""
    if api is not None and not busy and free is not None and free < need:
        blocker = (f"Zu wenig freier Grafikspeicher für MOSS: {free / 1024:.1f} GiB frei, "
                   f"ca. {need / 1024:.1f} GiB nötig")
    return payload(mode=mode, service=service, ready=api is not None, busy=busy,
                   state=str(api.get("state") or "") if api else "",
                   queue_len=int(api.get("queue_len") or 0) if api else None,
                   free_mib=free, required_mib=need, blocker=blocker,
                   note=note or default_note(service, api, busy), ok=ok, error=error)


def cmd_start() -> dict:
    if unit_state() not in RUNNING:
        try:
            systemctl("start", timeout=30)
        except ControlError as exc:
            return snapshot(ok=False, error=str(exc))
    deadline = time.monotonic() + START_WAIT
    while True:
        if api_status() is not None:
            return snapshot("MOSS ist bereit.")
        if unit_state() in STOPPED:
            return snapshot(ok=False, error="Der Dienst wurde direkt nach dem Start beendet; "
                                            "siehe journalctl --user -u " + UNIT)
        if time.monotonic() >= deadline:
            return snapshot(ok=False, error=f"Der Dienst läuft, aber der Scheduler ist nach "
                                            f"{START_WAIT:.0f} s nicht bereit.")
        time.sleep(POLL)


def cmd_stop() -> dict:
    if unit_state() in STOPPED:
        return snapshot("MOSS ist bereits beendet.")
    deadline = time.monotonic() + STOP_WAIT
    while True:
        if not is_busy(api_status()):
            break
        if time.monotonic() >= deadline:
            return snapshot(ok=False, error=f"Eine Sprachausgabe läuft noch (nach {STOP_WAIT:.0f} s); "
                                            "MOSS wurde nicht beendet. Später erneut versuchen.")
        time.sleep(POLL)
    try:
        systemctl("stop", timeout=60)
    except ControlError as exc:
        return snapshot(ok=False, error=str(exc))
    deadline = time.monotonic() + 10  # `systemctl stop` blocks; this only confirms it
    while unit_state() not in STOPPED:
        if time.monotonic() >= deadline:
            return snapshot(ok=False, error="Der Dienst ist nach dem Stop noch aktiv.")
        time.sleep(POLL)
    return snapshot("MOSS ist beendet und belegt keinen Grafikspeicher.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("status", "start", "stop"), nargs="?",
                        default="status")
    parser.add_argument("--request-id", type=int, default=0)  # widget source-name rotation
    args = parser.parse_args()
    try:
        result = {"status": snapshot, "start": cmd_start, "stop": cmd_stop}[args.action]()
    except ControlError as exc:
        result = payload(ok=False, error=str(exc))
    except Exception as exc:  # the JSON-only contract also covers surprises
        result = payload(ok=False, error=f"Unerwarteter Fehler: {type(exc).__name__}: {exc}")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
