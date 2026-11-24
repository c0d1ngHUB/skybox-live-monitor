#!/usr/bin/env python3
"""Behavior tests for the read-only Codex plan usage collector (synthetic data only)."""

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contents/code/codex_usage.py"
NOW = 1_900_000_000.0
SIGNATURE = "c2lnbmF0dXJlLXN5bnRoZXRpYw"
REFRESH = "rt_synthetic_refresh_token_never_used"


def jwt(exp=NOW + 3600, plan="plus", account="acct-synthetic-1", residency: "str | None" = "us", exp_raw=None):
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    auth = {"chatgpt_plan_type": plan, "chatgpt_account_id": account}
    if residency is not None:
        auth["chatgpt_compute_residency"] = residency
    claims: dict = {"https://api.openai.com/auth": auth}
    if exp_raw is not None:
        claims["exp"] = exp_raw
    elif exp is not None:
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
    def __init__(self, status=200, body=None, headers=None):
        self.status, self.body, self.requests = status, body, []
        self.response_headers = headers or {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path, "method": "GET", "headers": dict(self.headers)})
                self.send_response(outer.status)
                for name, value in outer.response_headers.items():
                    self.send_header(name, value)
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

    def make(status=200, body=USAGE, headers=None):
        s = Server(status, body, headers)
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
    except (OSError, ValueError, AttributeError, RecursionError):
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


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_not_followed_so_the_token_stays_put(tmp_path, server, status):
    """urllib's default handler re-sends every header (bearer token included) to
    the redirect target; the helper must stop at the first response instead."""
    target = server()
    origin = server(status=status, body=b"", headers={"Location": target.url})
    p = run(auth_file(tmp_path), origin.url)
    assert p["available"] is False and p["reason"] == "http_%d" % status and p["session"] is None
    assert len(origin.requests) == 1 and target.requests == []


def test_non_finite_numbers_are_unknown_not_a_crash(tmp_path, server):
    raw = (json.dumps(USAGE)
           .replace('"used_percent": 22', '"used_percent": Infinity')
           .replace('"reset_at": 1900517502', '"reset_at": 1e400')
           .replace('"available_count": 3', '"available_count": NaN'))
    assert raw.count("Infinity") == 1 and raw.count("1e400") == 1 and raw.count("NaN") == 1
    p = run(auth_file(tmp_path), server(body=raw.encode()).url)
    assert p["available"] is True and p["session"] is None
    # The unusable reset stamp falls back to reset_after_seconds.
    assert p["weekly"] == {"pct": 19, "resets_at": int(NOW) + 517502, "window_s": 604800}
    assert p["reset_credits"] is None


@pytest.mark.parametrize("exp_raw", [float("inf"), float("nan"), 10 ** 400], ids=["inf", "nan", "huge-int"])
def test_unreadable_token_expiry_is_still_tried(tmp_path, server, exp_raw):
    s = server()
    p = run(auth_file(tmp_path, token=jwt(exp_raw=exp_raw)), s.url)
    assert p["available"] is True and p["token_expires_in_s"] is None and len(s.requests) == 1


def test_deeply_nested_json_is_a_bad_payload_and_bad_credentials(tmp_path, server):
    deep = b"[" * 200000 + b"]" * 200000
    p = run(auth_file(tmp_path), server(body=deep).url)
    assert p["available"] is False and p["reason"] == "bad_payload"
    nested = tmp_path / "nested.json"
    nested.write_bytes(deep)
    assert run(nested)["reason"] == "no_credentials"


# --- Hermes credential pool (second Codex account) ---------------------------

POOL_REFRESH = "rt_synthetic_pool_refresh_token_never_used"
POOL_LABEL = "synthetic-pool-label"
# Trimmed shape of the Go plan answer: one 30-day limit, no 5-h window.
USAGE_GO = {
    "user_id": "user-synthetic-go", "account_id": "acct-synthetic-go", "email": "second@example.invalid",
    "plan_type": "go",
    "rate_limit": {
        "allowed": True, "limit_reached": False,
        "primary_window": {"used_percent": 0, "limit_window_seconds": 2592000,
                           "reset_after_seconds": 2592000, "reset_at": 1_902_592_000},
        "secondary_window": None,
    },
    "code_review_rate_limit": None, "additional_rate_limits": None,
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
}


def pool_entry(entry_id, token, label=POOL_LABEL):
    """One credential-pool entry in the shape Hermes writes (values synthetic)."""
    return {"id": entry_id, "label": label, "auth_type": "oauth", "priority": 1, "source": "manual:device_code",
            "access_token": token, "refresh_token": POOL_REFRESH, "last_status": "ok", "last_status_at": None,
            "last_error_code": None, "last_error_reason": None, "last_error_message": None,
            "last_error_reset_at": None, "base_url": "https://chatgpt.com/backend-api/codex",
            "last_refresh": "2030-03-17T10:00:00Z", "request_count": 0}


def hermes_auth(folder, entries=None, pool=True):
    """Synthetic Hermes auth.json: the second entry is the account under test."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "auth.json"
    if entries is None:
        entries = [pool_entry("aa11bb", jwt(plan="plus", account="acct-first-pool")),
                   pool_entry("1449ff", jwt(plan="go", account="acct-second-pool", residency="eu"))]
    body = {"version": 1, "active_provider": "openai-codex", "providers": {},
            "credential_pool": {"anthropic": [pool_entry("cc22dd", "sk-ant-synthetic")]}}
    if pool:
        body["credential_pool"]["openai-codex"] = entries
    path.write_text(json.dumps(body))
    return path


def pool_secrets(path):
    try:
        pools = json.loads(Path(path).read_text())["credential_pool"].values()
        tokens = [entry.get("access_token") for pool in pools for entry in pool if isinstance(entry, dict)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        tokens = []
    return [value for value in (*tokens, POOL_REFRESH, POOL_LABEL) if isinstance(value, str) and value]


def run_pool(tmp_path, hermes, entry="1449ff", url="http://127.0.0.1:9/unused", env=None, pass_path=True):
    """Pool mode. HERMES_HOME/CODEX_HOME point into tmp_path by default so a test
    can never fall back to the developer's real credential files."""
    args = ["python3", str(SCRIPT), "--hermes-pool-entry", entry, "--url", url, "--now", str(NOW), "--timeout", "3"]
    if pass_path:
        args += ["--hermes-auth", str(hermes)]
    environ = {**os.environ, "HERMES_HOME": str(tmp_path / "no-hermes-home"), "CODEX_HOME": str(tmp_path / "no-codex-home")}
    environ.update(env or {})
    out = subprocess.run(args, check=True, text=True, capture_output=True, env={k: v for k, v in environ.items() if v is not None})
    for value in pool_secrets(hermes):
        assert value not in out.stdout and value not in out.stderr
    return json.loads(out.stdout)


def test_pool_entry_token_is_used_read_only(tmp_path, server):
    s = server(body=USAGE_GO)
    path = hermes_auth(tmp_path / "hermes")
    before = path.read_bytes()
    p = run_pool(tmp_path, path, url=s.url)
    assert p["available"] is True and p["reason"] is None and p["plan"] == "go"
    assert p["session"] is None
    assert p["weekly"] == {"pct": 0, "resets_at": 1_902_592_000, "window_s": 2592000}
    assert p["tone"] == "ok" and p["token_expires_in_s"] == 3600
    # One GET with the token of entry 1449ff; account and residency from its JWT.
    assert [(r["method"], r["path"]) for r in s.requests] == [("GET", "/backend-api/wham/usage")]
    h = {k.lower(): v for k, v in s.requests[0]["headers"].items()}
    second = json.loads(before)["credential_pool"]["openai-codex"][1]
    assert h["authorization"] == "Bearer " + second["access_token"]
    assert h["chatgpt-account-id"] == "acct-second-pool"
    assert h["x-openai-internal-codex-residency"] == "eu"
    # Nothing identifying is passed on and the pool file stays untouched.
    shown = json.dumps(p)
    for value in ("second@example.invalid", "user-synthetic-go", "acct-synthetic-go", "acct-second-pool", "1449ff"):
        assert value not in shown, value
    assert path.read_bytes() == before


@pytest.mark.parametrize("make", [
    lambda folder: folder / "missing.json",
    lambda folder: (folder.mkdir(parents=True), (folder / "auth.json").write_text("not json"), folder / "auth.json")[-1],
    lambda folder: hermes_auth(folder, pool=False),
    lambda folder: hermes_auth(folder, entries=[]),
    lambda folder: hermes_auth(folder, entries={"1449ff": {"access_token": jwt()}}),
    lambda folder: hermes_auth(folder, entries=["1449ff", None, 5]),
    lambda folder: hermes_auth(folder, entries=[pool_entry("aa11bb", jwt())]),
    # Only the id selects an entry, never the label.
    lambda folder: hermes_auth(folder, entries=[pool_entry("zz99zz", jwt(), label="1449ff")]),
    lambda folder: hermes_auth(folder, entries=[pool_entry("1449ff", "")]),
    lambda folder: hermes_auth(folder, entries=[pool_entry("1449ff", None)]),
    lambda folder: hermes_auth(folder, entries=[{"id": "1449ff", "label": POOL_LABEL}]),
], ids=["no-file", "not-json", "no-codex-pool", "empty-pool", "pool-not-a-list", "junk-entries",
        "other-entry-only", "label-is-not-an-id", "empty-token", "null-token", "no-token"])
def test_missing_pool_entry_or_token_is_no_credentials(tmp_path, server, make):
    s = server(body=USAGE_GO)
    p = run_pool(tmp_path, make(tmp_path / "hermes"), url=s.url)
    assert p["available"] is False and p["reason"] == "no_credentials"
    assert p["session"] is None and p["weekly"] is None and p["plan"] is None and p["tone"] == "unknown"
    assert s.requests == []


def test_pool_mode_never_falls_back_to_the_codex_login(tmp_path, server):
    """A valid Codex login next to a missing pool entry must not fill the row
    with the first account's numbers."""
    s = server()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    auth_file(codex_home)
    hermes = hermes_auth(tmp_path / "hermes", entries=[pool_entry("aa11bb", jwt())])
    p = run_pool(tmp_path, hermes, url=s.url, env={"CODEX_HOME": str(codex_home)})
    assert p["reason"] == "no_credentials" and s.requests == []


def test_expired_pool_token_is_token_expired_without_network_call(tmp_path, server):
    """Hermes renews pool tokens only when it uses them; the widget never does."""
    s = server(body=USAGE_GO)
    hermes = hermes_auth(tmp_path / "hermes", entries=[pool_entry("1449ff", jwt(exp=NOW - 5, plan="go"))])
    before = hermes.read_bytes()
    p = run_pool(tmp_path, hermes, url=s.url)
    assert p["available"] is False and p["reason"] == "token_expired"
    assert p["plan"] == "go" and p["token_expires_in_s"] == -5 and p["tone"] == "unknown"
    assert s.requests == [] and hermes.read_bytes() == before


def test_hermes_home_selects_the_pool_file(tmp_path, server):
    s = server(body=USAGE_GO)
    hermes = hermes_auth(tmp_path / "hermes-home")
    p = run_pool(tmp_path, hermes, url=s.url, env={"HERMES_HOME": str(tmp_path / "hermes-home")}, pass_path=False)
    assert p["available"] is True and p["plan"] == "go" and len(s.requests) == 1


def test_pool_file_defaults_to_dot_hermes_in_the_home_directory(tmp_path, server):
    s = server(body=USAGE_GO)
    hermes = hermes_auth(tmp_path / "home" / ".hermes")
    p = run_pool(tmp_path, hermes, url=s.url, env={"HERMES_HOME": None, "HOME": str(tmp_path / "home")}, pass_path=False)
    assert p["available"] is True and p["plan"] == "go" and len(s.requests) == 1


def test_an_unexpected_error_still_prints_json(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("codex_usage_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(module, "collect", boom)
    monkeypatch.setattr(sys, "argv", ["codex_usage.py", "--auth", str(tmp_path / "auth.json"), "--now", str(NOW)])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    module.main()
    p = json.loads(out.getvalue())
    assert p["available"] is False and p["reason"] == "internal_error" and p["session"] is None
    assert p["stamp"] == NOW and p["tone"] == "unknown"
