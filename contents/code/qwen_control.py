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
import signal
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

PROBE_TIMEOUT = 2.5
CONTROL_TIMEOUT = 900
RECOVERY_REQUIRED_EXIT = 75
RECOVERY_TIMEOUT = 180
TERMINATE_GRACE = 2.0
KILL_GRACE = 2.0
LOCAL_OPENER = build_opener(ProxyHandler({}))


class ScriptLaunchError(OSError):
    """Popen failed before an executable operation could begin."""


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


def recovery_barrier() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or (Path.home() / ".cache/skybox-live-monitor"))
    return runtime / "skybox-qwen-control.recovery.json"


def qwen_status() -> dict:
    # Reading status never starts cleanup, removes the barrier or takes the lock.
    if recovery_barrier().exists():
        return {"state": "failed", "detail": "Qwen-Wiederherstellung erforderlich; tatsächlicher Modellzustand nicht bestätigt. Erneut versuchen."}
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


def handoff_settled() -> bool:
    """A stop must finish its unit job and leave no live model container."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "qwen38-ninfer.service",
             "--property=LoadState,ActiveState,Job"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return (result.returncode == 0 and props.get("LoadState") == "loaded"
            and props.get("ActiveState") in {"inactive", "failed"}
            and props.get("Job") in {"", "0"}
            and container_state() == "")


def prior_backend_active() -> bool | None:
    """Snapshot rollback intent before the start script can release the GPU."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "hindsight-ollama.service"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state = result.stdout.strip()
    if result.returncode in {0, 3}:
        if state in {"active", "activating", "reloading"}:
            return True
        if state in {"inactive", "failed"}:
            return False
    return None


def group_is_live(pgid: int) -> bool:
    """Linux zombies cannot perform work; wait for every other group member."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            # comm may contain spaces and parentheses; fields after the final
            # parenthesis begin with state, parent PID and process group ID.
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def settle_group(process: subprocess.Popen) -> None:
    """Terminate only this newly created session, including pipe-free children."""
    for sig, grace in ((signal.SIGTERM, TERMINATE_GRACE), (signal.SIGKILL, KILL_GRACE)):
        if not group_is_live(process.pid):
            break
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + grace
        while group_is_live(process.pid) and time.monotonic() < deadline:
            process.poll()  # Reap the leader; descendants may still be running.
            time.sleep(0.02)
    if group_is_live(process.pid):
        raise OSError("Qwen-Prozessgruppe konnte nicht vollständig beendet werden.")
    process.wait(timeout=KILL_GRACE)


def execute_script(script: Path, timeout: float, arguments: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    """Bound the whole script process group rather than just its leader."""
    try:
        process = subprocess.Popen([str(script), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
    except OSError as error:
        raise ScriptLaunchError(str(error)) from error
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    finally:
        try:
            settle_group(process)
        finally:
            # Never wait indefinitely for a pipe inherited by an escaped child.
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
    return subprocess.CompletedProcess([str(script), *arguments], process.returncode, stdout, stderr)


def write_barrier(path: Path, processes_settled: bool, restore_hindsight: bool = True) -> None:
    """Publish before dispatch so an interrupted controller also fails closed."""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump({"processes_settled": processes_settled, "restore_hindsight": restore_hindsight}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def reconcile(barrier: Path, stop_script: Path) -> bool:
    """Always run under the action lock; stop cancels any pending start job."""
    try:
        record = json.loads(barrier.read_text())
        if not isinstance(record, dict) or record.get("processes_settled") is not True:
            # An interrupted controller may have left an unidentified group.
            # Never kill saved PIDs after reuse or dispatch another operation.
            return False
        if not stop_script.is_file() or not os.access(stop_script, os.X_OK):
            return False
        restore_hindsight = record.get("restore_hindsight", True)
        if not isinstance(restore_hindsight, bool):
            return False
        write_barrier(barrier, False, restore_hindsight)
        try:
            result = execute_script(stop_script, RECOVERY_TIMEOUT,
                                    () if restore_hindsight else ("--no-restore",))
        except (ScriptLaunchError, subprocess.TimeoutExpired):
            write_barrier(barrier, True, restore_hindsight)
            return False
        write_barrier(barrier, True, restore_hindsight)
        if result.returncode != 0 or not handoff_settled():
            return False
        barrier.unlink()
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def run_action(action: str) -> dict:
    def outcome(ok: bool, error: str = "") -> dict:
        return {**qwen_status(), "ok": ok, "error": error}

    if action not in {"start", "stop"}:
        return outcome(False, "Unbekannte Qwen-Aktion.")
    script = Path.home() / ".local/bin" / f"qwen38-{action}"
    if not script.is_file() or not os.access(script, os.X_OK):
        return outcome(False, f"Start/Stop-Skript fehlt oder ist nicht ausführbar: {script.name}")
    barrier = recovery_barrier()
    runtime = barrier.parent
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "skybox-qwen-control.lock").open("a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return outcome(False, "Eine andere Qwen-Umschaltung läuft bereits.")
            stop_script = script.with_name("qwen38-stop")

            def recovery_error(message: str) -> dict:
                return {"state": "unknown", "detail": "Qwen-Wiederherstellung erforderlich.",
                        "ok": False, "error": message}

            if barrier.exists() and not reconcile(barrier, stop_script):
                return recovery_error("Qwen-Wiederherstellung nicht abgeschlossen; neue Umschaltung gesperrt. Bei unbekanntem Prozessabschluss ist eine manuelle Prüfung erforderlich.")
            restore_hindsight = prior_backend_active() if action == "start" else True
            if restore_hindsight is None:
                return outcome(False, "Vorheriger Hindsight-Zustand nicht lesbar; Qwen-Start ohne Änderung abgebrochen.")
            write_barrier(barrier, False, restore_hindsight)
            try:
                result = execute_script(script, CONTROL_TIMEOUT)
            except ScriptLaunchError as error:
                barrier.unlink()
                return outcome(False, f"Qwen-Skript konnte nicht ausgeführt werden: {error}")
            except subprocess.TimeoutExpired:
                write_barrier(barrier, True, restore_hindsight)
                if not reconcile(barrier, stop_script):
                    return recovery_error("Zeitlimit erreicht; Qwen-Wiederherstellung nicht abgeschlossen, Umschaltung gesperrt.")
                return outcome(False, "Zeitlimit bei Qwen-Umschaltung erreicht; Qwen gestoppt und Übergabe wiederhergestellt.")
            except (OSError, subprocess.SubprocessError) as error:
                # Keep the pre-dispatch barrier: group completion is uncertain.
                return recovery_error(f"Qwen-Skript konnte nicht sicher abgeschlossen werden; Wiederherstellung erforderlich: {error}")
            if result.returncode == RECOVERY_REQUIRED_EXIT:
                write_barrier(barrier, True, restore_hindsight)
                detail = (result.stderr or result.stdout or "Keine weiteren Angaben.").strip()[-1600:]
                return recovery_error(f"Qwen-Wiederherstellung nicht abgeschlossen; Umschaltung gesperrt: {detail}")
            barrier.unlink()
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
