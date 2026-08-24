#!/usr/bin/env python3
"""Print precise cumulative CPU seconds for every readable Linux process."""

import argparse
import os
from pathlib import Path


def process_samples(proc_root: Path, clock_ticks: int):
    if clock_ticks <= 0:
        return
    for stat_path in sorted(proc_root.glob("[0-9]*/stat"), key=lambda path: int(path.parent.name)):
        try:
            text = stat_path.read_text()
            open_paren = text.index("(")
            close_paren = text.rindex(")")
            pid = int(text[:open_paren].strip())
            name = text[open_paren + 1 : close_paren]
            fields = text[close_paren + 2 :].split()
            # fields starts at proc(5) state (field 3); utime/stime are fields 14/15.
            cpu_seconds = (int(fields[11]) + int(fields[12])) / clock_ticks
        except (OSError, ValueError, IndexError):
            continue
        yield pid, cpu_seconds, name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--clock-ticks", type=int, default=os.sysconf("SC_CLK_TCK"))
    args = parser.parse_args()
    for pid, cpu_seconds, name in process_samples(args.proc_root, args.clock_ticks):
        print(f"{pid} {cpu_seconds:.6f} {name}")


if __name__ == "__main__":
    main()
