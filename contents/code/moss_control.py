#!/usr/bin/env python3
"""Widget bridge for the MOSS-TTS voice service (hermes-gpu-scheduler.service).

MOSS-TTS is the /voice speech output. The scheduler unit owns it. Since 2026-11-07 the
scheduler keeps MOSS loaded on the Blackwell while the unit runs (resident mode, ~16.6 GB);
during a Qwen session, or when another program needs the memory, it unloads MOSS and loads it
again afterwards. An older scheduler without resident mode loads MOSS per reply instead. This
helper switches that unit on and off for the AI SERVICES button.

  status  read-only state
  start   start the unit, wait until the scheduler API answers and, in resident mode, until
          MOSS is loaded (or the scheduler explains why it waits)
  stop    wait until no voice job is running and the last one has been collected (a TTS
          client is waiting for its result, or the scheduler changed state a moment ago),
          then stop the unit (systemd takes the MOSS child process of the unit down with it)
          and confirm that no MOSS process is left

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
# Resident mode: how long `start` waits for the model load (~5-10 s, first run after a driver
# update recompiles CUDA kernels and takes longer).
LOAD_WAIT = float(os.environ.get("MOSS_LOAD_WAIT", "90"))
STOP_WAIT = float(os.environ.get("MOSS_STOP_WAIT", "150"))
# After `systemctl stop`: how long to wait until no MOSS process is left.
UNLOAD_WAIT = float(os.environ.get("MOSS_UNLOAD_WAIT", "15"))
POLL = float(os.environ.get("MOSS_POLL", "1"))
NVIDIA_SMI = os.environ.get("MOSS_NVIDIA_SMI", "nvidia-smi")
GPU_UUID = os.environ.get("MOSS_GPU_UUID", "GPU-2bc81341-8b1a-3a2e-679b-7a2bb75c6d9e")  # Blackwell
PROC_ROOT = Path(os.environ.get("MOSS_PROC_ROOT", "/proc"))
STATE_FILE = Path(os.environ.get("MOSS_STATE_FILE",
                                 Path.home() / ".local/state/moss-tts/scheduler-state.json"))
# Quiet period after the scheduler's last state change before a stop may proceed. The Hermes
# TTS client collects a job's result only every 5 s (--poll), and a long reply is several jobs
# in a row with short gaps; stopping inside that window loses the reply.
QUIET = float(os.environ.get("MOSS_QUIET", "8"))
OPENER = build_opener(ProxyHandler({}))  # loopback only, never through a proxy
RUNNING = {"active", "activating", "reloading"}
STOPPED = {"inactive", "failed"}  # a removed unit also reports "inactive" (exit 4)


class ControlError(Exception):
    """A failure with a message that is safe to show on the button tooltip."""


def payload(mode: str = "unknown", service: str = "unknown", ready: bool = False,
            busy: bool = False, state: str = "", queue_len: int | None = None,
            free_mib: int | None = None, required_mib: int | None = None,
            blocker: str = "", note: str = "", ok: bool = True, error: str = "",
            resident: bool = False, loaded: bool = False, moss_state: str = "",
            moss_reason: str = "", moss_vram_mib: int | None = None) -> dict:
    return {"mode": mode, "service": service, "ready": ready, "busy": busy, "state": state,
            "queue_len": queue_len, "free_mib": free_mib, "required_mib": required_mib,
            "blocker": blocker, "note": note, "ok": ok, "error": error,
            "resident": resident, "loaded": loaded, "moss_state": moss_state,
            "moss_reason": moss_reason, "moss_vram_mib": moss_vram_mib}


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
CLIENT_SCRIPT = "moss_tts_client.py"


def _runs_python_script(argv: list[str], matches) -> bool:
    return len(argv) >= 2 and Path(argv[0]).name.startswith("python") \
        and any(matches(arg) for arg in argv[1:])


def is_moss_service_argv(argv: list[str]) -> bool:
    """The scheduler's MOSS child: `<python> .../hermes_gpu_scheduler/moss_service.py` (moss_proc.py
    starts exactly this). A substring test on the whole command line is not enough: a shell,
    editor or grep that merely mentions the script would count as a job."""
    return _runs_python_script(argv, lambda arg: arg.endswith(MOSS_SCRIPT_SUFFIX))


def is_tts_client_argv(argv: list[str]) -> bool:
    """A Hermes TTS client (`<python> .../moss_tts_client.py ...`) that submitted or is about to
    submit a job and has not yet collected its result."""
    return _runs_python_script(argv, lambda arg: Path(arg).name == CLIENT_SCRIPT)


def voice_processes() -> tuple[bool, bool]:
    """(MOSS child exists, TTS client exists). The child holds the Blackwell; the client still
    waits for audio."""
    moss_child = client = False
    try:
        entries = list(PROC_ROOT.iterdir())
    except OSError:
        return False, False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = [arg for arg in (entry / "cmdline").read_bytes().decode(errors="replace").split("\0") if arg]
        except OSError:
            continue  # the process ended while scanning
        if is_moss_service_argv(argv):
            moss_child = True
        elif is_tts_client_argv(argv):
            client = True
    return moss_child, client


def voice_process_running() -> bool:
    """A MOSS child or a TTS client exists. Either counts as a voice job when the API is silent."""
    return any(voice_processes())


def resident_api(api: dict | None) -> bool:
    """The scheduler keeps MOSS loaded on purpose (resident mode, since 2026-11-07)."""
    return api is not None and api.get("moss_resident") is True


def is_busy(api: dict | None) -> bool:
    if api is not None and (bool(api.get("current_job")) or int(api.get("queue_len") or 0) > 0):
        return True
    moss_child, client = voice_processes()
    if client:
        return True
    # In resident mode the loaded MOSS child is the normal idle state, not a running reply.
    # Without an answering API it still counts: then nothing proves it is idle.
    return moss_child and not resident_api(api)


def resident_info(api: dict | None) -> tuple[bool, bool, str, str, int | None]:
    """(resident, loaded, state, reason, MOSS VRAM in MiB) from the scheduler's /status."""
    if api is None or api.get("moss_resident") is not True:
        return False, False, "", "", None
    moss = api.get("moss")
    moss = moss if isinstance(moss, dict) else {}
    vram = moss.get("vram_mib")
    vram = int(vram) if isinstance(vram, (int, float)) and not isinstance(vram, bool) else None
    return (True, api.get("moss_loaded") is True, str(moss.get("state") or ""),
            str(moss.get("reason") or ""), vram)


def gib(mib: float) -> str:
    return f"{mib / 1024:.1f}".replace(".", ",")


def resident_note(api: dict, busy: bool) -> str:
    _, loaded, state, reason, vram = resident_info(api)
    if busy:
        return "MOSS erzeugt gerade eine Sprachantwort."
    if loaded:
        size = f" ({gib(vram)} GiB auf der Blackwell)" if vram else " auf der Blackwell"
        return f"MOSS ist geladen{size} und antwortet ohne Ladezeit."
    if state == "loading":
        return "MOSS wird gerade auf die Blackwell geladen."
    if state == "waiting" and reason == "qwen":
        return ("Qwen belegt die Blackwell: MOSS ist entladen und lädt danach automatisch. "
                "Bis dahin kommen Sprachantworten nur als Text.")
    if state == "waiting" and reason == "pressure":
        return ("Ein anderes Programm braucht den Grafikspeicher: MOSS wurde entladen und lädt "
                "wieder, sobald genug frei ist.")
    if state == "waiting" and reason == "vram":
        return "MOSS wartet auf freien Grafikspeicher und lädt dann automatisch."
    if state == "waiting" and reason == "recovery":
        return "MOSS wird nach einem Fehler neu geladen."
    if state == "error":
        return f"MOSS konnte nicht geladen werden ({reason}); neuer Versuch in etwa einer Minute."
    return "MOSS ist gerade nicht geladen."


def recently_active() -> bool:
    """The scheduler changed state less than QUIET seconds ago (it persists {"ts"} at every
    transition). Unreadable data and timestamps from the future are not trusted."""
    try:
        age = time.time() - float(json.loads(STATE_FILE.read_text()).get("ts"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return -1.0 < age < QUIET


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
    if resident_api(api):
        return resident_note(api, busy)
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
    resident, loaded, moss_state, moss_reason, vram = resident_info(api)
    blocker = ""
    # A loaded MOSS owns its memory; only a MOSS that still has to load can lack VRAM.
    if api is not None and not busy and not loaded and free is not None and free < need:
        blocker = (f"Zu wenig freier Grafikspeicher für MOSS: {free / 1024:.1f} GiB frei, "
                   f"ca. {need / 1024:.1f} GiB nötig")
    return payload(mode=mode, service=service, ready=api is not None, busy=busy,
                   state=str(api.get("state") or "") if api else "",
                   queue_len=int(api.get("queue_len") or 0) if api else None,
                   free_mib=free, required_mib=need, blocker=blocker,
                   note=note or default_note(service, api, busy), ok=ok, error=error,
                   resident=resident, loaded=loaded, moss_state=moss_state,
                   moss_reason=moss_reason, moss_vram_mib=vram)


def gpu_free_mib() -> int | None:
    """Free memory of the Blackwell per nvidia-smi (best effort, only for the stop note)."""
    try:
        out = subprocess.run([NVIDIA_SMI, "--query-gpu=uuid,memory.free",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        uuid, _, value = line.partition(",")
        if uuid.strip() == GPU_UUID:
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def cmd_start() -> dict:
    if unit_state() not in RUNNING:
        try:
            systemctl("start", timeout=30)
        except ControlError as exc:
            return snapshot(ok=False, error=str(exc))
    deadline = time.monotonic() + START_WAIT
    while True:
        api = api_status()
        if api is not None:
            break
        if unit_state() in STOPPED:
            return snapshot(ok=False, error="Der Dienst wurde direkt nach dem Start beendet; "
                                            "siehe journalctl --user -u " + UNIT)
        if time.monotonic() >= deadline:
            return snapshot(ok=False, error=f"Der Dienst läuft, aber der Scheduler ist nach "
                                            f"{START_WAIT:.0f} s nicht bereit.")
        time.sleep(POLL)
    if not resident_api(api):
        return snapshot("MOSS ist bereit.")
    # Resident mode: report the start only once MOSS is loaded, or the scheduler says why not.
    deadline = time.monotonic() + LOAD_WAIT
    while True:
        _, loaded, state, reason, _ = resident_info(api)
        if loaded:
            return snapshot("MOSS ist geladen und bereit.")
        if state == "error":
            return snapshot(ok=False, error=f"MOSS konnte nicht geladen werden: {reason}")
        if state == "waiting":
            return snapshot()  # the note explains the wait (Qwen, memory)
        if time.monotonic() >= deadline:
            return snapshot(ok=False, error=f"MOSS ist nach {LOAD_WAIT:.0f} s noch nicht geladen.")
        time.sleep(POLL)
        api = api_status()
        if api is None:
            return snapshot(ok=False, error="Der Scheduler antwortet während des Ladens nicht mehr.")


def cmd_stop() -> dict:
    if unit_state() in STOPPED:
        return snapshot("MOSS ist bereits beendet.")
    deadline = time.monotonic() + STOP_WAIT
    while True:
        if not is_busy(api_status()) and not recently_active():
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
    # The memory is free only when the MOSS process is gone, not when the unit reports stopped.
    deadline = time.monotonic() + UNLOAD_WAIT
    while voice_processes()[0]:
        if time.monotonic() >= deadline:
            return snapshot(ok=False, error="Der Dienst ist beendet, aber ein MOSS-Prozess läuft noch "
                                            "und belegt Grafikspeicher.")
        time.sleep(POLL)
    free = gpu_free_mib()
    if free is not None:
        return snapshot(f"MOSS ist beendet und hat den Grafikspeicher freigegeben "
                        f"(Blackwell: {gib(free)} GiB frei).")
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
