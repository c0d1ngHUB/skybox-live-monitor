#!/usr/bin/env python3
"""Behavior tests for the read-only Codex plan usage collector (synthetic data only)."""

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contents/code/codex_usage.py"
NOW = 1_900_000_000.0
SIGNATURE = "c2lnbmF0dXJlLXN5bnRoZXRpYw"
REFRESH = "rt_synthetic_refresh_token_never_used"


def jwt(exp=NOW + 3600, plan="plus", account="acct-synthetic-1", residency: "str | None" = "us"):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    auth = {"chatgpt_plan_type": plan, "chatgpt_account_id": account}
    if residency is not None:
        auth["chatgpt_compute_residency"] = residency
    claims: dict = {"https://api.openai.com/auth": auth}
    if exp is not None:
        claims["exp"] = int(exp)
    return b64({"alg": "RS256", "typ": "JWT"}) + "." + b64(claims) + "." + SIGNATURE


# Trimmed copy of the real response shape (values synthetic).
USAGE = {
    "user_id": "user-synthetic", "account_id": "acct-synthetic-1", "email": "someone@example.invalid",
    "plan_type": "plus",
    "rate_limit": {
        "allowed": True, "limit_reached": False,
        "primary_window": {"used_percent": 22, "limit_window_seconds": 18000,
                           "reset_after_seconds": 15580, "reset_at": 1_900_015_580},
        "secondary_window": {"used_percent": 19, "limit_window_seconds": 604800,
                             "reset_after_seconds": 517502, "reset_at": 1_900_517_502},
    },
    "code_review_rate_limit": None,
    "additional_rate_limits": None,
    "credits": {"has_credits": False, "unlimited": False, "overage_limit_reached": False, "balance": "0"},
    "spend_control": {"reached": False, "individual_limit": None},
    "rate_limit_reached_type": None,
    "rate_limit_reset_credits": {"available_count": 3, "applicable_available_count": 0},
}


class Server:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.requests = status, body, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path, "method": "GET", "headers": dict(self.headers)})
                self.send_response(outer.status)
                self.end_headers()
                payload = outer.body if isinstance(outer.body, bytes) else json.dumps(outer.body).encode()
                self.wfile.write(payload)

            def do_POST(self):
                outer.requests.append({"path": self.path, "method": "POST", "headers": dict(self.headers)})
                self.send_response(500)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/backend-api/wham/usage" % self.httpd.server_port

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


def auth_file(tmp_path, token=None, account_id: "str | None" = "acct-synthetic-1", tokens=True):
    path = tmp_path / "auth.json"
    token = token if token is not None else jwt()
    body = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": "2030-03-17T10:00:00Z"}
    if tokens:
        body["tokens"] = {"id_token": jwt(), "access_token": token, "refresh_token": REFRESH}
        if account_id is not None:
            body["tokens"]["account_id"] = account_id
    else:
        body["tokens"] = None
    path.write_text(json.dumps(body))
    return path


def secrets(auth):
    try:
        tokens = json.loads(Path(auth).read_text()).get("tokens") or {}
    except (OSError, ValueError, AttributeError):
        tokens = {}
    return [value for value in (tokens.get("access_token"), REFRESH) if value]


def run(auth, url="http://127.0.0.1:9/unused", env=None, pass_auth=True):
    args = ["python3", str(SCRIPT), "--url", url, "--now", str(NOW), "--timeout", "3"]
    if pass_auth:
        args += ["--auth", str(auth)]
    out = subprocess.run(args, check=True, text=True, capture_output=True,
                         env={**os.environ, **(env or {})})
    for value in secrets(auth):
        assert value not in out.stdout and value not in out.stderr
    return json.loads(out.stdout)


def test_parses_session_weekly_plan_and_credits(tmp_path, server):
    s = server()
    path = auth_file(tmp_path)
    before = path.read_bytes()
    p = run(path, s.url)
    assert p["available"] is True and p["reason"] is None and p["stamp"] == NOW
    assert p["plan"] == "plus" and p["tone"] == "ok" and p["limit_reached"] is False
    assert p["session"] == {"pct": 22, "resets_at": 1_900_015_580, "window_s": 18000}
    assert p["weekly"] == {"pct": 19, "resets_at": 1_900_517_502, "window_s": 604800}
    assert p["extra"] == []
    assert p["credits"] == {"has_credits": False, "unlimited": False, "balance": "0"}
    assert p["reset_credits"] == 3 and p["token_expires_in_s"] == 3600
    assert p["thresholds"] == {"warn_pct": 75, "critical_pct": 90}
    # No identity data from the response is passed on to the widget.
    assert "someone@example.invalid" not in json.dumps(p) and "user-synthetic" not in json.dumps(p)
    # Exactly one read-only GET to the usage endpoint; the auth file stays untouched.
    assert [(r["method"], r["path"]) for r in s.requests] == [("GET", "/backend-api/wham/usage")]
    h = {k.lower(): v for k, v in s.requests[0]["headers"].items()}
    assert h["authorization"] == "Bearer " + json.loads(before)["tokens"]["access_token"]
    assert h["chatgpt-account-id"] == "acct-synthetic-1"
    assert h["x-openai-internal-codex-residency"] == "us"
    assert h["user-agent"] == "skybox-live-monitor"
    assert path.read_bytes() == before


def test_account_header_falls_back_to_the_jwt_claim(tmp_path, server):
    s = server()
    run(auth_file(tmp_path, token=jwt(account="acct-from-jwt", residency=None), account_id=None), s.url)
    h = {k.lower(): v for k, v in s.requests[0]["headers"].items()}
    assert h["chatgpt-account-id"] == "acct-from-jwt"
    assert "x-openai-internal-codex-residency" not in h


def test_lone_weekly_limit_in_primary_window_is_the_weekly_limit(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    body["rate_limit"]["primary_window"] = {"used_percent": 7, "limit_window_seconds": 604800, "reset_at": 1_900_500_000}
    body["rate_limit"]["secondary_window"] = None
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["available"] is True and p["session"] is None
    assert p["weekly"] == {"pct": 7, "resets_at": 1_900_500_000, "window_s": 604800}


def test_swapped_windows_follow_their_published_length(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    rl = body["rate_limit"]
    rl["primary_window"], rl["secondary_window"] = rl["secondary_window"], rl["primary_window"]
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["session"]["pct"] == 22 and p["session"]["window_s"] == 18000
    assert p["weekly"]["pct"] == 19 and p["weekly"]["window_s"] == 604800


def test_windows_without_a_length_fall_back_to_their_position(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    body["rate_limit"]["primary_window"] = {"used_percent": 4, "reset_at": 1_900_001_000}
    body["rate_limit"]["secondary_window"] = {"used_percent": 21.6}
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["session"] == {"pct": 4, "resets_at": 1_900_001_000, "window_s": None}
    assert p["weekly"] == {"pct": 22, "resets_at": None, "window_s": None}


def test_two_short_windows_never_masquerade_as_the_weekly_limit(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    body["rate_limit"]["secondary_window"] = {"used_percent": 50, "limit_window_seconds": 3600, "reset_at": 1_900_003_600}
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["session"]["pct"] == 22 and p["weekly"] is None
    assert p["extra"] == [{"pct": 50, "resets_at": 1_900_003_600, "window_s": 3600, "name": "Codex"}]


def test_reset_time_falls_back_to_reset_after_seconds(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    del body["rate_limit"]["primary_window"]["reset_at"]
    body["rate_limit"]["secondary_window"]["reset_at"] = "2030-03-24T08:00:00Z"
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["session"]["resets_at"] == int(NOW) + 15580
    assert p["weekly"]["resets_at"] == 1900569600


@pytest.mark.parametrize("session,weekly,reached,allowed,tone", [
    (74, 3, False, True, "ok"), (75, 3, False, True, "warn"), (20, 89, False, True, "warn"),
    (20, 90, False, True, "critical"), (10, 3, True, True, "critical"), (10, 3, None, False, "critical")])
def test_tone_from_thresholds_and_limit_state(tmp_path, server, session, weekly, reached, allowed, tone):
    body = json.loads(json.dumps(USAGE))
    body["rate_limit"]["primary_window"]["used_percent"] = session
    body["rate_limit"]["secondary_window"]["used_percent"] = weekly
    body["rate_limit"]["limit_reached"] = reached
    body["rate_limit"]["allowed"] = allowed
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["tone"] == tone
    assert p["limit_reached"] is (reached is True or allowed is False)


def test_limit_state_is_unknown_when_the_api_omits_it(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    del body["rate_limit"]["allowed"], body["rate_limit"]["limit_reached"]
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["limit_reached"] is None and p["tone"] == "ok"


def test_expired_token_is_unknown_without_network_call(tmp_path, server):
    s = server()
    p = run(auth_file(tmp_path, token=jwt(exp=NOW - 5, plan="pro")), s.url)
    assert p["available"] is False and p["reason"] == "token_expired"
    assert p["session"] is None and p["weekly"] is None and p["tone"] == "unknown"
    assert p["plan"] == "pro" and p["token_expires_in_s"] == -5
    assert s.requests == []  # never refreshed, never called


def test_token_inside_the_expiry_margin_counts_as_expired(tmp_path, server):
    s = server()
    p = run(auth_file(tmp_path, token=jwt(exp=NOW + 10)), s.url)
    assert p["reason"] == "token_expired" and s.requests == []


def test_token_without_readable_expiry_is_still_tried(tmp_path, server):
    s = server()
    p = run(auth_file(tmp_path, token="opaque-access-token-synthetic"), s.url)
    assert p["available"] is True and p["token_expires_in_s"] is None
    assert len(s.requests) == 1


@pytest.mark.parametrize("make", [
    lambda tmp: tmp / "missing.json",
    lambda tmp: auth_file(tmp, tokens=False),
    lambda tmp: (tmp / "auth.json").write_text("not json") and tmp / "auth.json",
    lambda tmp: auth_file(tmp, token=""),
])
def test_missing_or_unusable_credentials(tmp_path, server, make):
    s = server()
    p = run(make(tmp_path), s.url)
    assert p["available"] is False and p["reason"] == "no_credentials" and p["session"] is None
    assert s.requests == []


def test_codex_home_selects_the_auth_file(tmp_path, server):
    s = server()
    home = tmp_path / "codex-home"
    home.mkdir()
    auth_file(home)
    p = run(home / "auth.json", s.url, env={"CODEX_HOME": str(home)}, pass_auth=False)
    assert p["available"] is True and len(s.requests) == 1


@pytest.mark.parametrize("status,reason", [(401, "http_401"), (403, "http_403"), (429, "http_429"), (500, "http_500")])
def test_http_errors_are_unknown_never_zero(tmp_path, server, status, reason):
    p = run(auth_file(tmp_path), server(status=status, body={"error": "x"}).url)
    assert p["available"] is False and p["reason"] == reason
    assert p["session"] is None and p["weekly"] is None and p["tone"] == "unknown"


def test_network_error(tmp_path):
    p = run(auth_file(tmp_path), "http://127.0.0.1:9/backend-api/wham/usage")
    assert p["available"] is False and p["reason"] == "network"


@pytest.mark.parametrize("body", [
    b"not json", [1, 2], {"plan_type": "plus", "rate_limit": None},
    {"rate_limit": {"primary_window": {"limit_window_seconds": 18000}, "secondary_window": None}},
    {"rate_limit": {"primary_window": {"used_percent": True}}},
])
def test_malformed_payload(tmp_path, server, body):
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["available"] is False and p["reason"] == "bad_payload" and p["session"] is None


def test_plan_falls_back_to_the_token_when_the_payload_has_none(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    del body["plan_type"]
    p = run(auth_file(tmp_path, token=jwt(plan="Business")), server(body=body).url)
    assert p["available"] is True and p["plan"] == "business"


def test_additional_and_code_review_limits_go_to_the_tooltip_list(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    body["additional_rate_limits"] = [
        {"limit_name": "GPT-Spark", "metered_feature": "codex_spark",
         "rate_limit": {"allowed": True, "limit_reached": False,
                        "primary_window": {"used_percent": 40, "limit_window_seconds": 18000, "reset_at": 1_900_010_000},
                        "secondary_window": None}},
        {"metered_feature": "codex_other", "rate_limit": None}, "junk"]
    body["code_review_rate_limit"] = {
        "allowed": True, "limit_reached": False,
        "primary_window": {"used_percent": 5, "limit_window_seconds": 604800, "reset_at": 1_900_600_000}}
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["extra"] == [
        {"pct": 40, "resets_at": 1_900_010_000, "window_s": 18000, "name": "GPT-Spark"},
        {"pct": 5, "resets_at": 1_900_600_000, "window_s": 604800, "name": "Code-Review"}]
    # Extra limits only inform; the row tone follows the main Codex limit.
    assert p["tone"] == "ok"


def test_unexpected_list_types_do_not_break_the_row(tmp_path, server):
    body = json.loads(json.dumps(USAGE))
    body["additional_rate_limits"] = 5
    body["credits"] = "n/a"
    body["rate_limit_reset_credits"] = None
    p = run(auth_file(tmp_path), server(body=body).url)
    assert p["available"] is True and p["extra"] == [] and p["credits"] is None and p["reset_credits"] is None
