#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

MODULE = Path(__file__).parents[1] / "contents/code/gpu_telemetry.py"
SPEC = importlib.util.spec_from_file_location("gpu_telemetry", MODULE)
assert SPEC and SPEC.loader
GPU = cast(Any, importlib.util.module_from_spec(SPEC))
SPEC.loader.exec_module(GPU)


def test_parse_gpus_keeps_both_cards_independent():
    output = (
        "0, GPU-a, NVIDIA RTX PRO 4000 Blackwell, 98, 84, 22949, 24467, 145.05, 145.00\n"
        "1, GPU-b, NVIDIA GeForce RTX 3060 Ti, 17, 55, 7338, 8192, 112.4, 200.0"
    )
    gpus = GPU.parse_gpus(output)
    assert [gpu["index"] for gpu in gpus] == [0, 1]
    assert [gpu["short_name"] for gpu in gpus] == ["RTX PRO 4000", "RTX 3060 Ti"]
    assert gpus[0]["utilization_percent"] == 98
    assert gpus[1]["memory_total_mib"] == 8192
    assert gpus[0]["power_limit_w"] == 145
    assert gpus[1]["temperature_c"] == 55


def test_processes_are_grouped_by_gpu_uuid_and_use_service_names(tmp_path: Path):
    for pid, unit in ((101, "qwen38-llama-server.service"), (202, "qwen3-reranker-4b.service")):
        proc = tmp_path / str(pid)
        proc.mkdir()
        (proc / "cgroup").write_text(f"0::/user.slice/app.slice/{unit}\n")
    output = (
        "GPU-a, 101, /opt/llama-server, 12000\n"
        "GPU-b, 202, /opt/llama-server, 4030\n"
        "GPU-b, 303, /opt/fallback-server, 100"
    )
    grouped = GPU.parse_processes(output, tmp_path)
    assert grouped["GPU-a"] == [{"pid": 101, "name": "qwen38-llama-server", "used_mib": 12000}]
    assert grouped["GPU-b"][0]["name"] == "qwen3-reranker-4b"
    assert grouped["GPU-b"][1]["name"] == "fallback-server"


def test_collect_attaches_top_two_processes_and_exact_count(monkeypatch, tmp_path: Path):
    gpu_output = (
        "0, GPU-a, NVIDIA RTX PRO 4000 Blackwell, 90, 80, 20000, 24467, 140, 145\n"
        "1, GPU-b, NVIDIA GeForce RTX 3060 Ti, 0, 40, 7000, 8192, 14, 200"
    )
    process_output = (
        "GPU-b, 1, /bin/one, 100\n"
        "GPU-b, 2, /bin/two, 300\n"
        "GPU-b, 3, /bin/three, 200"
    )

    def fake_run(command, timeout=GPU.TIMEOUT):
        return (0, gpu_output) if "--query-gpu=" in command[1] else (0, process_output)

    monkeypatch.setattr(GPU, "run_capture", fake_run)
    payload = GPU.collect(tmp_path)
    assert len(payload["gpus"]) == 2
    assert payload["gpus"][0]["process_count"] == 0
    assert payload["gpus"][1]["process_count"] == 3
    assert [process["used_mib"] for process in payload["gpus"][1]["processes"]] == [300, 200]


def test_collect_reports_unavailable_nvidia_smi(monkeypatch):
    monkeypatch.setattr(GPU, "run_capture", lambda command, timeout=GPU.TIMEOUT: (1, ""))
    assert GPU.collect() == {"gpus": [], "error": "nvidia-smi unavailable"}
