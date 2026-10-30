#!/usr/bin/env python3
"""Read-only Claude plan usage (session / weekly limits) for the Skybox widget.

Reads the OAuth access token Claude Code keeps in ~/.claude/.credentials.json and
issues ONE GET to the (undocumented) https://api.anthropic.com/api/oauth/usage
endpoint, which backs the "Plan usage limits" page of claude.ai.

The token is never refreshed here: a refresh rotates the refresh token and would
log out Claude Code / Hermes. An expired token is reported as unknown; Claude
Code refreshes it on its own next use. No model is called, so this costs no
usage. Failures are reported as unknown (null), never as 0 %.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

URL = "https://api.anthropic.com/api/oauth/usage"
WARN_PCT = 75
CRITICAL_PCT = 90
EXPIRY_MARGIN_S = 30


def _epoch(value):
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _pct(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def _limit(entry):
    pct = _pct(entry.get("percent"))
    if pct is None:
        return None
    return {"pct": pct, "resets_at": _epoch(entry.get("resets_at")),
            "severity": entry.get("severity") if isinstance(entry.get("severity"), str) else "normal"}


def _legacy(entry):
    if not isinstance(entry, dict) or _pct(entry.get("utilization")) is None:
        return None
    return {"pct": _pct(entry["utilization"]), "resets_at": _epoch(entry.get("resets_at")), "severity": "normal"}


def parse(body):
    """Return (session, weekly, scoped, breakdown, extra_enabled) or None if unusable."""
    if not isinstance(body, dict):
        return None
    session = weekly = None
    scoped = []
    for entry in body.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind == "session" and session is None:
            session = _limit(entry)
        elif kind == "weekly_all" and weekly is None:
            weekly = _limit(entry)
        elif kind == "weekly_scoped":
            item = _limit(entry)
            scope = entry.get("scope") or {}
            model = scope.get("model") if isinstance(scope, dict) else None
            name = (model or {}).get("display_name") if isinstance(model, dict) else None
            if item is not None:
                scoped.append({"name": name or "?", "pct": item["pct"], "resets_at": item["resets_at"]})
    session = session or _legacy(body.get("five_hour"))
    weekly = weekly or _legacy(body.get("seven_day"))
    if session is None and weekly is None:
        return None
    breakdown = []
    rows = (body.get("seven_day_breakdown") or {}).get("rows") if isinstance(body.get("seven_day_breakdown"), dict) else None
    for row in rows or []:
        if isinstance(row, dict) and _pct(row.get("percent")) is not None:
            breakdown.append({"name": row.get("display_name") or row.get("key") or "?", "pct": _pct(row["percent"])})
    extra = body.get("extra_usage")
    extra_enabled = extra.get("is_enabled") if isinstance(extra, dict) and isinstance(extra.get("is_enabled"), bool) else None
    return session, weekly, scoped, breakdown, extra_enabled


def tone(session, weekly):
    level = 0
    for item in (session, weekly):
        if item is None:
            continue
        sev = item.get("severity", "normal")
        if item["pct"] >= CRITICAL_PCT or sev == "critical":
            level = max(level, 2)
        elif item["pct"] >= WARN_PCT or sev not in ("normal", "", None):
            level = max(level, 1)
    return ["ok", "warn", "critical"][level]


def collect(credentials: Path, url: str, now: float, timeout: float):
    result = {"available": False, "stamp": now, "reason": None, "plan": None, "tone": "unknown",
              "session": None, "weekly": None, "scoped": [], "breakdown": [], "extra_usage_enabled": None,
              "token_expires_in_s": None, "thresholds": {"warn_pct": WARN_PCT, "critical_pct": CRITICAL_PCT}}
    try:
        oauth = json.loads(credentials.read_text())["claudeAiOauth"]
        token = oauth["accessToken"]
        expires_at = float(oauth.get("expiresAt") or 0) / 1000
    except (OSError, ValueError, KeyError, TypeError):
        result["reason"] = "no_credentials"
        return result
    result["plan"] = oauth.get("subscriptionType") if isinstance(oauth.get("subscriptionType"), str) else None
    result["token_expires_in_s"] = int(expires_at - now)
    if not token or expires_at - EXPIRY_MARGIN_S <= now:
        result["reason"] = "token_expired"
        return result
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": "skybox-live-monitor"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024)
    except urllib.error.HTTPError as error:
        result["reason"] = "http_%d" % error.code
        return result
    except (urllib.error.URLError, OSError, ValueError):
        result["reason"] = "network"
        return result
    try:
        parsed = parse(json.loads(raw))
    except ValueError:
        parsed = None
    if parsed is None:
        result["reason"] = "bad_payload"
        return result
    session, weekly, scoped, breakdown, extra_enabled = parsed
    result.update(available=True, session=session, weekly=weekly, scoped=scoped, breakdown=breakdown,
                  extra_usage_enabled=extra_enabled, tone=tone(session, weekly))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, default=Path.home() / ".claude/.credentials.json")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    now = args.now if args.now is not None else time.time()
    print(json.dumps(collect(args.credentials, args.url, now, args.timeout)))


if __name__ == "__main__":
    main()
