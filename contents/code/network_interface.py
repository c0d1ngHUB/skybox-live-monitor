#!/usr/bin/env python3
"""Print an existing default-route interface, with a local-interface fallback."""
import argparse
from pathlib import Path
import subprocess


def detect_interface(sys_net: Path) -> str:
    try:
        result = subprocess.run(["ip", "-o", "route", "show", "default"], capture_output=True, text=True, timeout=2, check=False)
        routes = result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        routes = ""
    for route in routes.splitlines():
        tokens = route.split()
        for index, token in enumerate(tokens[:-1]):
            if token == "dev":
                interface = tokens[index + 1]
                if "/" not in interface and (sys_net / interface).is_dir():
                    return interface
    try:
        candidates = sorted(path.name for path in sys_net.iterdir() if path.is_dir())
    except OSError:
        return ""
    for interface in candidates:
        if interface == "lo" or interface.startswith(("docker", "br-", "veth", "tailscale", "wlxd")):
            continue
        return interface
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sys-net", type=Path, default=Path("/sys/class/net"))
    interface = detect_interface(parser.parse_args().sys_net)
    if not interface:
        return 1
    print(interface)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
