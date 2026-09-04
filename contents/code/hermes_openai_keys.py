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


def hermes_home_for_monitor() -> Path:
    profile_name = os.environ.get("HERMES_MONITOR_PROFILE", "coordinator")
    return Path.home() / ".hermes" / "profiles" / profile_name


def hermes_monitor_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home_for_monitor())
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
    # The selected profile directory must exist before the call. The read-only
    # auth list still runs so profile fallback keeps working, but its result
    # is not trusted for the widget when the selected profile was absent.
    profile_ready = hermes_home_for_monitor().is_dir()
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
    if not profile_ready:
        print("-1 0")
        return 0

    active, total = count_openai_credentials(result.stdout)
    if total == 0:
        # Distinguish "no OpenAI-Codex credentials configured" from "could not
        # parse the auth list" so format drift surfaces instead of showing 0/0.
        if "openai-codex" in result.stdout:
            print("-1 0")
            return 0
    print(f"{active} {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
