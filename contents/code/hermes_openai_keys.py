#!/usr/bin/env python3
"""Print available and total OpenAI OAuth credential counts from ``hermes auth list``.

Only aggregate counts are emitted; credential labels and identifiers are never
forwarded to the Plasma widget.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys


UNAVAILABLE_MARKERS = ("rate-limited", "cooldown", "exhausted", "dead", "disabled", "invalid")


def configured_monitor_profile() -> str:
    """Explicitly requested profile name, with empty/whitespace treated as unset.

    Never default to a profile name: ``hermes auth list`` must resolve the
    credential store the Hermes instance actually uses. Pinning a profile that
    does not exist (parked, renamed, or never created on this machine) makes
    the command print nothing, which used to surface as a silent 0/0.
    """
    return os.environ.get("HERMES_MONITOR_PROFILE", "").strip()


def profile_home_for_monitor(profile_name: str) -> Path:
    return Path.home() / ".hermes" / "profiles" / profile_name


def hermes_monitor_env() -> dict[str, str]:
    """Environment for the auth query; pins a profile only when one was asked for.

    Without an override, ``HERMES_HOME`` is inherited, so an unset variable,
    an empty value and a whitespace-only value all mean the same thing: use the
    profile Hermes resolves by itself.
    """
    profile_name = configured_monitor_profile()
    env = os.environ.copy()
    if profile_name:
        env["HERMES_HOME"] = str(profile_home_for_monitor(profile_name))
    return env


def count_openai_credentials(output: str) -> tuple[int, int]:
    """Return ``(available, total)`` for OpenAI Codex OAuth credentials."""
    in_provider = False
    active = 0
    total = 0

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if re.match(r"^openai-codex \(\d+ credentials?\):$", line):
            in_provider = True
            continue
        if in_provider and line and not line[0].isspace():
            break
        if not in_provider or not re.match(r"^\s+#\d+\s+", line):
            continue
        if not re.search(r"\soauth\s", f" {line.strip()} "):
            continue

        total += 1
        lowered = line.lower()
        if not any(marker in lowered for marker in UNAVAILABLE_MARKERS):
            active += 1

    return active, total


def hermes_executable() -> str | None:
    found = shutil.which("hermes")
    if found:
        return found
    fallback = Path.home() / ".local/bin/hermes"
    return str(fallback) if fallback.is_file() else None


def main() -> int:
    executable = hermes_executable()
    if not executable:
        print("-1 0")
        return 0
    # An explicitly configured profile must exist before it is queried. When it
    # does not, the auth store is unresolved and the count is not trusted;
    # asking Hermes anyway would query a fallback store and look like real data.
    profile_name = configured_monitor_profile()
    if profile_name and not profile_home_for_monitor(profile_name).is_dir():
        print("-1 0")
        return 0
    try:
        result = subprocess.run(
            [executable, "auth", "list"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            env=hermes_monitor_env(),
        )
    except (OSError, subprocess.SubprocessError):
        print("-1 0")
        return 0

    active, total = count_openai_credentials(result.stdout)
    if total == 0:
        # Distinguish "no OpenAI-Codex credentials configured" from an empty or
        # unparseable auth list, so a wrong store surfaces instead of 0/0.
        if not result.stdout.strip() or "openai-codex" in result.stdout:
            print("-1 0")
            return 0
    print(f"{active} {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
