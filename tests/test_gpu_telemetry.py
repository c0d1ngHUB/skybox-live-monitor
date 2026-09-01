#!/usr/bin/env python3
from __future__ import annotations

import json
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
    assert payload["processes_available"] is True
    assert "error" not in payload
    assert len(payload["gpus"]) == 2
    assert payload["gpus"][0]["process_count"] == 0
    assert payload["gpus"][1]["process_count"] == 3
    assert [process["used_mib"] for process in payload["gpus"][1]["processes"]] == [300, 200]


def test_collect_reports_full_gpu_failure_with_clear_error_and_exit_one(monkeypatch):
    monkeypatch.setattr(GPU, "run_capture", lambda command, timeout=GPU.TIMEOUT: (1, ""))
    payload = GPU.collect()
    assert payload["gpus"] == []
    assert payload["processes_available"] is False
    assert payload["gpu_error"] == GPU.GPU_ERROR
    assert payload["error"] == GPU.GPU_ERROR
    assert GPU.main() == 1


def test_collect_keeps_gpu_metrics_when_process_query_fails(monkeypatch, tmp_path: Path):
    gpu_output = (
        "0, GPU-a, NVIDIA RTX PRO 4000 Blackwell, 90, 80, 20000, 24467, 140, 145\n"
        "1, GPU-b, NVIDIA GeForce RTX 3060 Ti, 0, 40, 7000, 8192, 14, 200"
    )

    def fake_run(command, timeout=GPU.TIMEOUT):
        return (0, gpu_output) if "--query-gpu=" in command[1] else (1, "")

    monkeypatch.setattr(GPU, "run_capture", fake_run)
    payload = GPU.collect(tmp_path)
    assert payload["processes_available"] is False
    assert payload["process_error"] == GPU.PROCESS_ERROR
    assert "error" not in payload
    assert "gpu_error" not in payload
    assert len(payload["gpus"]) == 2
    assert payload["gpus"][0]["utilization_percent"] == 90
    assert payload["gpus"][1]["temperature_c"] == 40
    assert payload["gpus"][0]["process_count"] == 0
    assert payload["gpus"][0]["processes"] == []
    assert GPU.main() == 0


def test_collect_reports_empty_process_query_as_available(monkeypatch, tmp_path: Path):
    gpu_output = "0, GPU-a, NVIDIA RTX PRO 4000 Blackwell, 90, 80, 20000, 24467, 140, 145"

    def fake_run(command, timeout=GPU.TIMEOUT):
        return (0, gpu_output) if "--query-gpu=" in command[1] else (0, "")

    monkeypatch.setattr(GPU, "run_capture", fake_run)
    payload = GPU.collect(tmp_path)
    assert payload["processes_available"] is True
    assert "error" not in payload
    assert "process_error" not in payload
    assert "gpu_error" not in payload
    assert payload["gpus"][0]["process_count"] == 0
    assert payload["gpus"][0]["processes"] == []
    assert GPU.main() == 0


def test_parse_gpus_tolerates_commas_inside_quoted_fields():
    output = '0, "GPU-a", "NVIDIA RTX PRO 4000, Blackwell", 98, 84, 22949, 24467, 145.05, 145.00'
    gpus = GPU.parse_gpus(output)
    assert len(gpus) == 1
    assert gpus[0]["uuid"] == "GPU-a"
    assert gpus[0]["name"] == "NVIDIA RTX PRO 4000, Blackwell"
    assert gpus[0]["utilization_percent"] == 98


def test_parse_processes_tolerates_commas_inside_quoted_process_names(tmp_path: Path):
    output = 'GPU-a, 101, "/opt/weird, name", 12000'
    grouped = GPU.parse_processes(output, tmp_path)
    assert grouped["GPU-a"] == [{"pid": 101, "name": "weird, name", "used_mib": 12000}]


def test_main_outputs_json_line_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(GPU, "run_capture", lambda command, timeout=GPU.TIMEOUT: (1, ""))
    assert GPU.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["gpu_error"] == GPU.GPU_ERROR
