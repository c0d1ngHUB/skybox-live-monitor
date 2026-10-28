"""Reproduce the real stop-script Type=simple socket-binding race safely.

Only the readiness probe is real networking (isolated ephemeral loopback ports).
All systemctl operations are intercepted; no production service is touched.
"""
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import time
import shutil

import pytest


def test_stop_waits_until_a_started_service_binds_its_http_socket(tmp_path):
    stop_script = Path(__file__).resolve().parents[1] / "runtime/qwen38-stop"
    if not stop_script.is_file() or shutil.which("curl") is None:
        pytest.skip("Local Qwen handoff script and curl are required")
    triggered = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"models":[]}')
            triggered.set()

        def log_message(self, format: str, *args: object) -> None:
            pass

    ready = HTTPServer(("127.0.0.1", 0), Handler)
    delayed = HTTPServer(("127.0.0.1", 0), Handler, bind_and_activate=False)
    delayed.server_bind()  # Port reserved, deliberately not yet listening.
    ready_thread = threading.Thread(target=ready.serve_forever, daemon=True)
    ready_thread.start()

    def bind_later():
        assert triggered.wait(10), "The first readiness probe was never issued"
        time.sleep(1.2)  # Reproduce Type=simple returning before bind/listen.
        delayed.server_activate()
        delayed.serve_forever()

    delayed_thread = threading.Thread(target=bind_later, daemon=True)
    delayed_thread.start()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    fake = fake_bin / "systemctl"
    fake.write_text(f"#!{sys.executable}\nimport sys\n"
                    "if 'is-active' in sys.argv:print('inactive' if 'qwen38-ninfer.service' in sys.argv else 'active')\n")
    fake.chmod(0o755)
    original = stop_script.read_text()
    isolated = original.replace("for port in 11439 11440; do",
                                f"for port in {ready.server_port} {delayed.server_port}; do")
    isolated = isolated.replace("for port in 11439; do",
                                f"for port in {delayed.server_port}; do")
    assert isolated != original, "Cannot isolate the localhost probes"
    script = tmp_path / "qwen38-stop-isolated"
    script.write_text(isolated)
    script.chmod(0o755)
    triggered.set()
    try:
        result = subprocess.run([str(script)], capture_output=True, text=True, timeout=40,
                                env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "JSONDecodeError" not in result.stderr
    finally:
        triggered.set()
        time.sleep(1.3)
        ready.shutdown()
        delayed.shutdown()
        ready.server_close()
        delayed.server_close()
