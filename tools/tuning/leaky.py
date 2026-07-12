#!/usr/bin/env python3
"""Controlled RSS growth for leak-monitor threshold tuning on a live host.

Allocates resident memory at a fixed rate so operators can compare candidate
leak.toml settings against known curves. Complements the in-repo JSONL
scenarios (TS-04), which prove correctness in CI; this tool exercises the
real process sampler and ring buffers.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import signal
import sys
import time

_CHUNK = 1024 * 1024  # 1 MiB slabs keep RSS honest without huge single allocations.


def _set_process_name(name: str) -> None:
    """Linux comm (15 bytes) — matches what the process sampler reads as name."""
    libc_path = ctypes.util.find_library("c")
    if libc_path is None:
        return
    libc = ctypes.CDLL(libc_path, use_errno=True)
    # PR_SET_NAME = 15 on Linux.
    buf = name.encode()[:15]
    # prctl expects a C string; pad so the kernel always sees a terminator.
    libc.prctl(15, buf + b"\0", 0, 0, 0)


def _allocate_mib(target_mib: int, hold: list[bytearray]) -> None:
    while len(hold) < target_mib:
        hold.append(bytearray(_CHUNK))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grow resident memory at a fixed rate for leak-monitor tuning.",
    )
    parser.add_argument(
        "--rate-mib-per-hour",
        type=float,
        default=120.0,
        help="sustained allocation rate (default 120 = 2 MiB/min, matches firefox-leak scenario)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3600.0,
        help="seconds to run the growth phase (default 3600)",
    )
    parser.add_argument(
        "--burst-mib",
        type=float,
        default=0.0,
        help="allocate this many MiB immediately at start (startup-ramp simulation)",
    )
    parser.add_argument(
        "--hold",
        action="store_true",
        help="keep memory after growth phase instead of exiting",
    )
    parser.add_argument(
        "--process-name",
        default="tuning-leaky",
        help="Linux comm name visible to ftmon, max 15 chars (default tuning-leaky)",
    )
    args = parser.parse_args(argv)

    if args.rate_mib_per_hour <= 0 and args.burst_mib <= 0:
        parser.error("need a positive --rate-mib-per-hour and/or --burst-mib")

    hold: list[bytearray] = []
    stop = False

    def _handle(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    _set_process_name(args.process_name)

    mib_per_sec = args.rate_mib_per_hour / 3600.0
    if args.burst_mib > 0:
        _allocate_mib(int(args.burst_mib), hold)
        print(
            f"burst: {int(args.burst_mib)} MiB RSS (hold={len(hold)} MiB)",
            flush=True,
        )

    print(
        f"leaky: name={args.process_name!r} rate={args.rate_mib_per_hour:.1f} MiB/h "
        f"duration={args.duration:.0f}s hold={args.hold}",
        flush=True,
    )

    start = time.monotonic()
    next_tick = start
    while not stop:
        now = time.monotonic()
        elapsed = now - start
        if elapsed >= args.duration:
            break
        target = int(args.burst_mib + mib_per_sec * elapsed)
        if len(hold) < target:
            _allocate_mib(target, hold)
        if now >= next_tick:
            print(f"  {elapsed:6.0f}s  rss_hold={len(hold)} MiB", flush=True)
            next_tick = now + 60.0
        time.sleep(0.25)

    if stop:
        print("stopped by signal", flush=True)
        return 0

    print(f"growth done: {len(hold)} MiB held", flush=True)
    if args.hold:
        while not stop:
            time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
