#!/usr/bin/env python3
"""Behavior tests for the read-only Claude plan usage collector (synthetic data only)."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import subprocess
import threading

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contents/code/claude_usage.py"
NOW = 1_900_000_000.0
TOKEN = "sk-ant-oat-SYNTHETIC-secret-token"

# Trimmed copy of the real response shape (values synthetic).
USAGE = {
    "five_hour": {"utilization": 16.0, "resets_at": "2030-03-17T22:50:00.010562+00:00"},
    "seven_day": {"utilization": 3.0, "resets_at": "2030-03-22T12:00:00.010586+00:00"},
    "extra_usage": {"is_enabled": False},
    "limits": [
        {"kind": "session", "group": "session", "percent": 16, "severity": "normal",
         "resets_at": "2030-03-17T22:50:00.010562+00:00", "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 3, "severity": "normal",
         "resets_at": "2030-03-22T12:00:00.010586+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "severity": "normal",
         "resets_at": "2030-03-22T12:00:00+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": False},
    ],
    "seven_day_breakdown": {"rows": [
        {"key": "claude_code", "display_name": "Claude Code", "percent": 100},
        {"key": "chat", "display_name": "Chats", "percent": 0}]},
}


class Server:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.headers = status, body, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.headers.append(dict(self.headers))
                self.send_response(outer.status)
                self.end_headers()
                payload = outer.body if isinstance(outer.body, bytes) else json.dumps(outer.body).encode()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/api/oauth/usage" % self.httpd.server_port

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    created = []

    def make(status=200, body=USAGE):
        s = Server(status, body)
        created.append(s)
        return s
    yield make
    for s in created:
        s.close()


def creds(tmp_path, expires_in_s=3600, plan="max"):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": TOKEN, "refreshToken": "sk-ant-ort-SYNTHETIC",
        "expiresAt": int((NOW + expires_in_s) * 1000), "subscriptionType": plan}}))
    return path


def run(credentials, url="http://127.0.0.1:9/unused"):
    out = subprocess.run(["python3", str(SCRIPT), "--credentials", str(credentials), "--url", url,
                          "--now", str(NOW), "--timeout", "3"],
                         check=True, text=True, capture_output=True)
    assert TOKEN not in out.stdout and TOKEN not in out.stderr
    return json.loads(out.stdout)


def test_parses_session_weekly_scoped_and_breakdown(tmp_path, server):
    s = server()
    p = run(creds(tmp_path), s.url)
    assert p["available"] is True and p["reason"] is None and p["stamp"] == NOW
    assert p["plan"] == "max" and p["tone"] == "ok"
    assert p["session"] == {"pct": 16, "resets_at": 1900018200, "severity": "normal"}
    assert p["weekly"]["pct"] == 3 and p["weekly"]["resets_at"] == 1900411200
    assert p["scoped"] == [{"name": "Fable", "pct": 0, "resets_at": 1900411200}]
    assert p["breakdown"] == [{"name": "Claude Code", "pct": 100}, {"name": "Chats", "pct": 0}]
    assert p["extra_usage_enabled"] is False
    assert p["thresholds"] == {"warn_pct": 75, "critical_pct": 90}
    # Read-only call with the bearer token and the OAuth beta header; the token never leaks.
    h = {k.lower(): v for k, v in s.headers[0].items()}
    assert h["authorization"] == "Bearer " + TOKEN and h["anthropic-beta"] == "oauth-2025-04-20"


def test_falls_back_to_legacy_fields_without_limits(tmp_path, server):
    body = {k: v for k, v in USAGE.items() if k != "limits"}
    p = run(creds(tmp_path), server(body=body).url)
    assert p["available"] is True
    assert p["session"]["pct"] == 16 and p["weekly"]["pct"] == 3 and p["scoped"] == []


@pytest.mark.parametrize("session,weekly,severity,tone", [
    (74, 3, "normal", "ok"), (75, 3, "normal", "warn"), (20, 90, "normal", "critical"),
    (10, 3, "warning", "warn"), (10, 3, "critical", "critical")])
def test_tone_from_thresholds_and_api_severity(tmp_path, server, session, weekly, severity, tone):
    body = json.loads(json.dumps(USAGE))
    body["limits"][0]["percent"] = session
    body["limits"][0]["severity"] = severity
    body["limits"][1]["percent"] = weekly
    assert run(creds(tmp_path), server(body=body).url)["tone"] == tone


def test_expired_token_is_unknown_without_network_call(tmp_path, server):
    s = server()
    p = run(creds(tmp_path, expires_in_s=-5), s.url)
    assert p["available"] is False and p["reason"] == "token_expired"
    assert p["session"] is None and p["weekly"] is None and p["tone"] == "unknown"
    assert s.headers == []  # never refreshed, never called


def test_missing_credentials(tmp_path):
    p = run(tmp_path / "nope.json")
    assert p["available"] is False and p["reason"] == "no_credentials" and p["session"] is None


@pytest.mark.parametrize("status,reason", [(401, "http_401"), (429, "http_429"), (500, "http_500")])
def test_http_errors_are_unknown_never_zero(tmp_path, server, status, reason):
    p = run(creds(tmp_path), server(status=status, body={"error": "x"}).url)
    assert p["available"] is False and p["reason"] == reason
    assert p["session"] is None and p["weekly"] is None


def test_network_error(tmp_path):
    p = run(creds(tmp_path), "http://127.0.0.1:9/api/oauth/usage")
    assert p["available"] is False and p["reason"] == "network"


@pytest.mark.parametrize("body", [b"not json", {"five_hour": None, "limits": []}, [1, 2]])
def test_malformed_payload(tmp_path, server, body):
    p = run(creds(tmp_path), server(body=body).url)
    assert p["available"] is False and p["reason"] == "bad_payload" and p["session"] is None
