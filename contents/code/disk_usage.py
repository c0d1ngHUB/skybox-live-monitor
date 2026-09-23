#!/usr/bin/env python3
"""Print the df(1) view of a filesystem as one line: used avail total percent.

Why this exists: the Plasma sensors ``disk/all/used`` and ``disk/all/free`` use
``f_bavail`` (space available to unprivileged users) for "free" and count the
filesystem's reserved blocks as "used". On a 3.6 TiB ext4 root with the default
5% reservation that reported "USED 858.0 GiB / 24%" where df(1) reports
"674G / 20%" — the card disagreed with the tool a user cross-checks against.

df(1) semantics, reproduced here from statvfs(3):
- used  = total - f_bfree          (reserved blocks are *not* used)
- avail = f_bavail                 (reserved blocks are not available either)
- used% = used / (used + avail)    (df's "Capacity", not used/total)

Only the local filesystem of the given path is read; no network access, no
writes, no credentials. ``df`` itself is not invoked so the numbers cannot drift
with locale, block size or column layout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def disk_usage(path: Path) -> tuple[int, int, int, float]:
    """Return ``(used, avail, total, used_percent)`` in bytes, df(1) semantics."""
    stats = os.statvfs(str(path))
    block = stats.f_frsize
    total = stats.f_blocks * block
    free = stats.f_bfree * block
    avail = stats.f_bavail * block
    used = max(0, total - free)
    usable = used + avail
    percent = (100.0 * used / usable) if usable > 0 else 0.0
    return used, avail, total, percent


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the df(1) view of a filesystem: used avail total percent")
    parser.add_argument("mount", nargs="?", default="/", type=Path)
    args = parser.parse_args()
    try:
        used, avail, total, percent = disk_usage(args.mount)
    except OSError:
        return 1
    print(f"{used} {avail} {total} {percent:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
