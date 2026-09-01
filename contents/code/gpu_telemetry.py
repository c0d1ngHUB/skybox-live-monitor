#!/usr/bin/env python3
"""Emit local telemetry for every NVIDIA GPU as one JSON line.

Error contract:
- Full GPU query failure: JSON with ``gpu_error`` and CLI exit 1.
- Successful GPU query plus failed process query: GPU metrics with
  ``processes_available=false`` and CLI exit 0.
- Successful empty process query: ``processes_available=true`` and
  ``process_count=0`` per GPU and CLI exit 0.
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

TIMEOUT = 4.0
GPU_QUERY = [
    "nvidia-smi",
    "--query-gpu=index,uuid,name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw,power.limit",
    "--format=csv,noheader,nounits",
]
PROCESS_QUERY = [
    "nvidia-smi",
    "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
    "--format=csv,noheader,nounits",
]
GPU_ERROR = "nvidia-smi gpu query failed"
PROCESS_ERROR = "nvidia-smi process query failed"


def run_capture(command: list[str], timeout: float = TIMEOUT) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return result.returncode, (result.stdout or "").strip()


def number(value: str) -> float | None:
    try:
        parsed = float(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed


def short_gpu_name(name: str, index: int) -> str:
    cleaned = re.sub(r"^NVIDIA\s+(?:GeForce\s+)?", "", name.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+Blackwell$", "", cleaned, flags=re.IGNORECASE)
    return cleaned or f"GPU {index}"


def service_name_for_pid(pid: int, proc_root: Path = Path("/proc")) -> str:
    try:
        cgroup = (proc_root / str(pid) / "cgroup").read_text(errors="replace")
    except OSError:
        return ""
    matches = re.findall(r"/([^/\n]+\.service)(?:\n|$)", cgroup)
    if not matches:
        return ""
    return matches[-1][:-len(".service")]


def parse_gpus(output: str) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(str(output or "")), skipinitialspace=True):
        fields = [field.strip() for field in row]
        if len(fields) != 9:
            continue
        try:
            index = int(fields[0])
        except ValueError:
            continue
        values = [number(field) for field in fields[3:]]
        if values[0] is None or values[1] is None or values[2] is None or values[3] is None:
            continue
        gpus.append(
            {
                "index": index,
                "uuid": fields[1],
                "name": fields[2],
                "short_name": short_gpu_name(fields[2], index),
                "utilization_percent": values[0],
                "temperature_c": values[1],
                "memory_used_mib": values[2],
                "memory_total_mib": values[3],
                "power_draw_w": values[4],
                "power_limit_w": values[5],
                "process_count": 0,
                "processes": [],
            }
        )
    return sorted(gpus, key=lambda gpu: gpu["index"])


def parse_processes(output: str, proc_root: Path = Path("/proc")) -> dict[str, list[dict[str, Any]]]:
    by_uuid: dict[str, list[dict[str, Any]]] = {}
    for row in csv.reader(io.StringIO(str(output or "")), skipinitialspace=True):
        fields = [field.strip() for field in row]
        if len(fields) != 4:
            continue
        try:
            pid = int(fields[1])
        except ValueError:
            continue
        used_mib = number(fields[3])
        if used_mib is None:
            continue
        fallback = Path(fields[2]).name or "GPU PROCESS"
        display_name = service_name_for_pid(pid, proc_root) or fallback
        by_uuid.setdefault(fields[0], []).append(
            {"pid": pid, "name": display_name, "used_mib": used_mib}
        )
    for processes in by_uuid.values():
        processes.sort(key=lambda process: process["used_mib"], reverse=True)
    return by_uuid


def collect(proc_root: Path = Path("/proc")) -> dict[str, Any]:
    gpu_code, gpu_output = run_capture(GPU_QUERY)
    if gpu_code != 0:
        return {
            "gpus": [],
            "processes_available": False,
            "error": GPU_ERROR,
            "gpu_error": GPU_ERROR,
        }
    process_code, process_output = run_capture(PROCESS_QUERY)
    gpus = parse_gpus(gpu_output)
    processes_available = process_code == 0
    processes_by_uuid = parse_processes(process_output, proc_root) if processes_available else {}
    for gpu in gpus:
        processes = processes_by_uuid.get(gpu["uuid"], [])
        gpu["process_count"] = len(processes)
        gpu["processes"] = processes[:2]
    payload: dict[str, Any] = {"gpus": gpus, "processes_available": processes_available}
    if not processes_available:
        payload["process_error"] = PROCESS_ERROR
    return payload


def main() -> int:
    payload = collect()
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 1 if payload.get("gpu_error") else 0


if __name__ == "__main__":
    sys.exit(main())
