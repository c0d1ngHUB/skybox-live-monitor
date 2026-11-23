#!/usr/bin/env python3
"""Read-only Codex plan usage (5-h session / weekly limit) for the Skybox widget.

Reads the ChatGPT OAuth access token the Codex CLI/app keeps in
$CODEX_HOME/auth.json (default ~/.codex/auth.json) and issues ONE GET to
https://chatgpt.com/backend-api/wham/usage, the endpoint behind the limits that
Codex shows in /status.

The token is never refreshed here: a refresh rotates the refresh token and would
log out Codex. An expired token is reported as unknown; Codex refreshes it on
its own next use. No model is called, so this costs no usage. Failures are
reported as unknown (null), never as 0 %.
"""

import argparse
import base64
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

URL = "https://chatgpt.com/backend-api/wham/usage"
WARN_PCT = 75
CRITICAL_PCT = 90
EXPIRY_MARGIN_S = 30
DAY_S = 86400
AUTH_CLAIM = "https://api.openai.com/auth"


def default_auth_file():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _pct(value):
    value = _number(value)
    return None if value is None else int(round(value))


def _stamp(value):
    """Epoch seconds from a number or an ISO-8601 string; None otherwise."""
    if _number(value) is not None:
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _claims(token):
    """JWT payload WITHOUT verification: only read for exp, account and plan."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError, AttributeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _window(entry, now):
    if not isinstance(entry, dict):
        return None
    pct = _pct(entry.get("used_percent"))
    if pct is None:
        return None
    window_s = _number(entry.get("limit_window_seconds"))
    resets_at = _stamp(entry.get("reset_at"))
    after = _number(entry.get("reset_after_seconds"))
    if resets_at is None and after is not None:
        resets_at = int(now + after)
    return {"pct": pct, "resets_at": resets_at, "window_s": int(window_s) if window_s is not None else None}


def windows(rate_limit, now):
    """Return (session, weekly, leftovers). Windows are placed by their published
    length, not by position: a plan with only a weekly limit sends it as
    primary_window. Without a length the position decides (primary = session)."""
    slots: dict = {"session": None, "weekly": None}
    leftovers = []
    if not isinstance(rate_limit, dict):
        return None, None, leftovers
    for key, fallback in (("primary_window", "session"), ("secondary_window", "weekly")):
        item = _window(rate_limit.get(key), now)
        if item is None:
            continue
        kind = fallback if item["window_s"] is None else ("session" if item["window_s"] < DAY_S else "weekly")
        if slots[kind] is None:
            slots[kind] = item
        else:
            leftovers.append(item)
    return slots["session"], slots["weekly"], leftovers


def _named_windows(name, rate_limit, now):
    session, weekly, leftovers = windows(rate_limit, now)
    return [dict(item, name=name) for item in (session, weekly, *leftovers) if item is not None]


def parse(body, now):
    """Return the usage fields or None if the payload carries no usable limit."""
    if not isinstance(body, dict):
        return None
    rate_limit = body.get("rate_limit")
    session, weekly, leftovers = windows(rate_limit, now)
    if session is None and weekly is None:
        return None
    limit_reached = None
    if isinstance(rate_limit, dict):
        reached, allowed = rate_limit.get("limit_reached"), rate_limit.get("allowed")
        if isinstance(reached, bool) or isinstance(allowed, bool):
            limit_reached = reached is True or allowed is False
    extra = [dict(item, name="Codex") for item in leftovers]
    additional = body.get("additional_rate_limits")
    for entry in additional if isinstance(additional, list) else []:
        if isinstance(entry, dict):
            name = entry.get("limit_name") or entry.get("metered_feature") or "?"
            extra += _named_windows(str(name), entry.get("rate_limit"), now)
    extra += _named_windows("Code-Review", body.get("code_review_rate_limit"), now)
    credits = body.get("credits")
    if isinstance(credits, dict):
        balance = credits.get("balance")
        credits = {"has_credits": credits.get("has_credits") if isinstance(credits.get("has_credits"), bool) else None,
                   "unlimited": credits.get("unlimited") if isinstance(credits.get("unlimited"), bool) else None,
                   "balance": str(balance) if isinstance(balance, (str, int, float)) and not isinstance(balance, bool) else None}
    else:
        credits = None
    resets = body.get("rate_limit_reset_credits")
    reset_credits = _number(resets.get("available_count")) if isinstance(resets, dict) else None
    plan = body.get("plan_type")
    return {"plan": plan.strip().lower() if isinstance(plan, str) and plan.strip() else None,
            "session": session, "weekly": weekly, "limit_reached": limit_reached, "extra": extra,
            "credits": credits, "reset_credits": int(reset_credits) if reset_credits is not None else None}


def tone(session, weekly, limit_reached):
    if limit_reached:
        return "critical"
    level = 0
    for item in (session, weekly):
        if item is None:
            continue
        if item["pct"] >= CRITICAL_PCT:
            level = max(level, 2)
        elif item["pct"] >= WARN_PCT:
            level = max(level, 1)
    return ["ok", "warn", "critical"][level]


def collect(auth_file: Path, url: str, now: float, timeout: float):
    result = {"available": False, "stamp": now, "reason": None, "plan": None, "tone": "unknown",
              "session": None, "weekly": None, "limit_reached": None, "extra": [], "credits": None,
              "reset_credits": None, "token_expires_in_s": None,
              "thresholds": {"warn_pct": WARN_PCT, "critical_pct": CRITICAL_PCT}}
    try:
        tokens = json.loads(auth_file.read_text())["tokens"]
        token = tokens["access_token"]
        if not isinstance(token, str) or not token.strip():
            raise ValueError("empty access token")
        token = token.strip()
    except (OSError, ValueError, KeyError, TypeError):
        result["reason"] = "no_credentials"
        return result
    claims = _claims(token)
    raw_auth = claims.get(AUTH_CLAIM)
    auth: dict = raw_auth if isinstance(raw_auth, dict) else {}
    plan = auth.get("chatgpt_plan_type")
    result["plan"] = plan.strip().lower() if isinstance(plan, str) and plan.strip() else None
    expires_at = _number(claims.get("exp"))
    if expires_at is not None:
        result["token_expires_in_s"] = int(expires_at - now)
        if expires_at - EXPIRY_MARGIN_S <= now:
            result["reason"] = "token_expired"
            return result
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
               "User-Agent": "skybox-live-monitor"}
    account = tokens.get("account_id") or auth.get("chatgpt_account_id")
    if isinstance(account, str) and account.strip():
        headers["ChatGPT-Account-ID"] = account.strip()
    residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    if isinstance(residency, str) and residency.strip():
        headers["x-openai-internal-codex-residency"] = residency.strip()
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024)
    except urllib.error.HTTPError as error:
        result["reason"] = "http_%d" % error.code
        return result
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        result["reason"] = "network"
        return result
    try:
        parsed = parse(json.loads(raw), now)
    except ValueError:
        parsed = None
    if parsed is None:
        result["reason"] = "bad_payload"
        return result
    result.update(available=True, session=parsed["session"], weekly=parsed["weekly"],
                  limit_reached=parsed["limit_reached"], extra=parsed["extra"], credits=parsed["credits"],
                  reset_credits=parsed["reset_credits"],
                  tone=tone(parsed["session"], parsed["weekly"], parsed["limit_reached"]))
    result["plan"] = parsed["plan"] or result["plan"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", type=Path, default=None, help="Codex auth.json (default: $CODEX_HOME/auth.json)")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    now = args.now if args.now is not None else time.time()
    print(json.dumps(collect(args.auth or default_auth_file(), args.url, now, args.timeout)))


if __name__ == "__main__":
    main()
