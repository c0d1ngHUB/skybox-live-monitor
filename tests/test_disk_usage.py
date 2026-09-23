#!/usr/bin/env python3
"""Regression tests for the df(1)-semantics disk helper."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).parents[1] / "contents/code/disk_usage.py"
SPEC = importlib.util.spec_from_file_location("disk_usage", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_used_excludes_reserved_blocks_and_percent_matches_df():
    """The ext4 reserve must not inflate "used" or the percentage.

    Real numbers from a 3.6 TiB ext4 root with the default 5% reservation:
    f_blocks 3634.6 GiB, f_bfree 2961.3 GiB, f_bavail 2776.6 GiB.
    df reports "674G / 20%"; the Plasma sensors reported "858.0 GiB / 24%".
    """
    gib = 1024**3
    total, free, avail = 3634.6 * gib, 2961.3 * gib, 2776.6 * gib
    values = {"f_frsize": 4096, "f_blocks": total / 4096, "f_bfree": free / 4096, "f_bavail": avail / 4096}

    class Stats:
        def __getattr__(self, name):
            return values[name]

    original = os.statvfs
    os.statvfs = lambda _path: Stats()
    try:
        used, available, reported_total, percent = MODULE.disk_usage(Path("/"))
    finally:
        os.statvfs = original

    assert round(used / gib) == 673
    assert round(available / gib) == 2777
    assert round(reported_total / gib) == 3635
    # df Capacity counts reserved blocks as neither used nor available.
    assert round(percent) == 20
    # used = total - f_bfree, and the ~185 GiB reserve sits in neither bucket,
    # which is exactly why df's percentage (20%) is below used/total (18.5%).
    reserve = 184.7 * gib
    assert abs(used + available + reserve - reported_total) < 2 * gib


def test_helper_prints_four_fields_for_the_real_root():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "/"],
        text=True,
        capture_output=True,
        check=True,
    )
    fields = result.stdout.split()
    assert len(fields) == 4
    used, avail, total, percent = int(fields[0]), int(fields[1]), int(fields[2]), float(fields[3])
    assert total > 0 and used > 0 and avail > 0
    assert used + avail <= total  # reserved blocks are in neither bucket
    assert 0 <= percent <= 100
    # Cross-check against df(1) itself: the used figure must not carry the reserve.
    df_used = subprocess.run(["df", "-B1", "--output=used", "/"], text=True, capture_output=True, check=True)
    df_bytes = int(df_used.stdout.splitlines()[-1])
    assert abs(used - df_bytes) <= total * 0.01, f"helper {used} vs df {df_bytes}"


def test_unreadable_mount_reports_failure_not_a_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "/definitely/not/a/mount"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert result.stdout.strip() == ""
