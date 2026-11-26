#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
import sys
import threading

import pytest

MODULE = Path(__file__).parents[1] / "contents/code/ai_services_status.py"
spec = importlib.util.spec_from_file_location("ai_services_status", MODULE)
assert spec and spec.loader
ai = cast(Any, importlib.util.module_from_spec(spec))
sys.modules[spec.name] = ai
spec.loader.exec_module(ai)


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


def test_hindsight_status_accepts_live_healthy_payload():
    original = ai.fetch_json
    ai.fetch_json = lambda url: {"status": "healthy", "database": "connected"} if url.endswith("/health") else None
    try:
        status = ai.hindsight_status()
    finally:
        ai.fetch_json = original
    assert status.state == "healthy"


def test_gateway_status_maps_inactive_to_down(monkeypatch):
    import subprocess
    monkeypatch.setattr(ai.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 3, "inactive", ""))
    status = ai.hermes_gateway_status()
    assert status.state == "down"


def test_fetch_json_does_not_follow_redirects():
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            if self.path == "/health":
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"healthy"}')

        def log_message(self, format, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = ai.fetch_json(f"http://127.0.0.1:{server.server_port}/health")
        finally:
            server.shutdown()
            thread.join()

    assert result is None
    assert requested == ["/health"]


def test_incomplete_bank_inventory_is_not_reported_as_idle(monkeypatch):
    monkeypatch.setattr(ai, 'fetch_json', lambda _: {"total": 101, "banks": []})
    assert ai.hindsight_consolidation_counts() is None


def _serve_routes(routes: dict[str, dict | None]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = routes.get(self.path)
            self.send_response(200 if payload is not None else 503)
            self.end_headers()
            if payload is not None:
                self.wfile.write(ai.json.dumps(payload).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _counts_routes(hermes_processing: dict | None, hermes_pending: dict | None = {"total": 0},
                   bot_processing: dict | None = {"total": 0, "operations": []},
                   bot_pending: dict | None = {"total": 0}) -> dict[str, dict | None]:
    routes: dict[str, dict | None] = {
        "/v1/default/banks?limit=100": {"total": 2, "banks": [
            {"bank_id": "hermes"}, {"bank_id": "bot:hotel"},
        ]},
        "/v1/default/banks/hermes/operations?type=consolidation&status=processing&limit=100": hermes_processing,
        "/v1/default/banks/hermes/operations?type=consolidation&status=pending&limit=1": hermes_pending,
        "/v1/default/banks/bot%3Ahotel/operations?type=consolidation&status=processing&limit=100": bot_processing,
        "/v1/default/banks/bot%3Ahotel/operations?type=consolidation&status=pending&limit=1": bot_pending,
    }
    return routes


@pytest.mark.parametrize("routes,expected", [
    # Idle: no running job means no progress either.
    (_counts_routes({"total": 0, "operations": []}),
     {"processing": 0, "pending": 0, "done": None, "total": None}),
    # Running jobs report live worker progress; the sums become the bar.
    (_counts_routes({"total": 2, "operations": [
        {"progress": {"processed": 8, "total": 24}},
        {"progress": {"processed": 0, "total": 9}},
    ]}, hermes_pending={"total": 3}, bot_pending={"total": 1}),
     {"processing": 2, "pending": 4, "done": 8, "total": 33}),
    # One running job without progress keeps the counts but drops the bar.
    (_counts_routes({"total": 1, "operations": [{"progress": None}]}, hermes_pending={"total": 2}),
     {"processing": 1, "pending": 2, "done": None, "total": None}),
    # A truncated operations page keeps the counts but drops the bar.
    (_counts_routes({"total": 2, "operations": [{"progress": {"processed": 1, "total": 2}}]}),
     {"processing": 2, "pending": 0, "done": None, "total": None}),
    # A failed bank poll is not an idle bank.
    (_counts_routes({"total": 1, "operations": [{"progress": {"processed": 1, "total": 2}}]},
                   hermes_pending=None),
     None),
])
def test_hindsight_counts_filtered_jobs_and_running_progress(routes, expected):
    server = _serve_routes(routes)
    try:
        actual = ai.hindsight_consolidation_counts(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert actual == expected


def test_hindsight_counts_poll_every_configured_route():
    routes = _counts_routes({"total": 1, "operations": [{"progress": {"processed": 4, "total": 12}}]},
                            hermes_pending={"total": 2})
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            payload = routes.get(self.path)
            self.send_response(200 if payload is not None else 503)
            self.end_headers()
            self.wfile.write(ai.json.dumps(payload).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            actual = ai.hindsight_consolidation_counts(f"http://127.0.0.1:{server.server_port}")
        finally:
            server.shutdown()
            thread.join()

    assert actual == {"processing": 1, "pending": 2, "done": 4, "total": 12}
    assert sorted(requested) == sorted(routes)
