#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
import sys
import threading

MODULE = Path(__file__).parents[1] / "contents/code/ai_services_status.py"
spec = importlib.util.spec_from_file_location("ai_services_status", MODULE)
assert spec and spec.loader
ai = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = ai
spec.loader.exec_module(ai)


def test_local_llm_status_requires_healthy_endpoint_and_reads_model_name():
    original = ai.fetch_json
    ai.fetch_json = lambda url: (
        {"status": "ok"} if url.endswith("/health")
        else {"models": [{"name": "qwen3.8-27b-local"}]}
    )
    try:
        status, model = ai.local_llm_status()
    finally:
        ai.fetch_json = original
    assert status.state == "healthy"
    assert model == "qwen3.8-27b-local"


def test_local_llm_status_accepts_openai_data_catalog_shape():
    original = ai.fetch_json
    ai.fetch_json = lambda url: {"status": "healthy"} if url.endswith("/health") else {"data": [{"id": "qwen-local"}]}
    try:
        status, model = ai.local_llm_status()
    finally:
        ai.fetch_json = original
    assert status.state == "healthy"
    assert model == "qwen-local"


def test_local_llm_status_is_error_when_health_is_unreachable():
    original = ai.fetch_json
    ai.fetch_json = lambda url: None
    try:
        status, model = ai.local_llm_status()
    finally:
        ai.fetch_json = original
    assert status.state == "error"
    assert model == ""


def test_local_llm_status_stays_healthy_when_model_catalog_is_unavailable():
    original = ai.fetch_json
    ai.fetch_json = lambda url: {"status": "ok"} if url.endswith("/health") else None
    try:
        status, model = ai.local_llm_status()
    finally:
        ai.fetch_json = original
    assert status.state == "healthy"
    assert model == ""


def test_fetch_json_bypasses_proxy_environment(monkeypatch):
    target_requests: list[str] = []
    proxy_requests: list[str] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy"}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"proxy"}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    target = HTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = HTTPServer(("127.0.0.1", 0), ProxyHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    try:
        result = ai.fetch_json(f"http://127.0.0.1:{target.server_port}/health")
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()

    assert result == {"status": "healthy"}
    assert target_requests == ["/health"]
    assert proxy_requests == []


def test_openai_oauth_availability_reads_only_aggregate_counts():
    original = ai.run_capture
    ai.run_capture = lambda command, timeout=ai.TIMEOUT: "3 3" if command[-1].endswith("hermes_openai_keys.py") else ""
    try:
        assert ai.openai_oauth_availability() == (3, 3)
    finally:
        ai.run_capture = original


def test_openai_oauth_availability_fails_closed():
    original = ai.run_capture
    ai.run_capture = lambda command, timeout=ai.TIMEOUT: ""
    try:
        assert ai.openai_oauth_availability() == (-1, -1)
    finally:
        ai.run_capture = original


def test_openai_oauth_availability_passes_through_format_drift_signal():
    original = ai.run_capture
    ai.run_capture = lambda command, timeout=ai.TIMEOUT: "-1 0" if command[-1].endswith("hermes_openai_keys.py") else ""
    try:
        assert ai.openai_oauth_availability() == (-1, 0)
    finally:
        ai.run_capture = original


def test_openai_oauth_availability_passes_through_missing_executable_drift_signal(tmp_path, monkeypatch):
    """A real PATH without `hermes` yields the helper's explicit drift signal, not a crash."""
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_MONITOR_PROFILE", raising=False)
    assert ai.openai_oauth_availability() == (-1, 0)


def test_hindsight_status_accepts_live_healthy_payload():
    original = ai.fetch_json
    ai.fetch_json = lambda url: {"status": "healthy", "database": "connected"} if url.endswith("/health") else None
    try:
        status = ai.hindsight_status()
    finally:
        ai.fetch_json = original
    assert status.state == "healthy"


def test_gateway_status_maps_inactive_to_down():
    original = ai.run_capture
    ai.run_capture = lambda command, timeout=ai.TIMEOUT: "inactive" if command[:3] == ["systemctl", "--user", "is-active"] else ""
    try:
        status = ai.hermes_gateway_status()
    finally:
        ai.run_capture = original
    assert status.state == "down"
