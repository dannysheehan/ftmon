#!/usr/bin/env python3
"""Check DM-04 raw-window recovery after a DM-05 degradation episode (#102).

Two questions with different clocks, which is why they need separate readings:

  1. Steady-state prune cost. Answered once the backlog of over-retention
     rollups has drained — minutes to hours, depending on how much there was.
  2. Whether raw retention returns to DM-04's 48 h. Answered roughly 24 h
     after the *last destructive pass*, not after the drain. Degradation
     trims raw samples to a 24 h floor and deleted samples cannot be
     recreated, so the window regrows at one hour per wall-clock hour
     regardless of how quickly the rollups went.

Anchoring on `last_degradation_ts` rather than on deploy time is what makes
the second answer meaningful: a plateau before the projected recovery, or a
renewed `db_degrading`, means pressure remains rather than that the fix
failed to work.

Read-only. Safe to run against a live daemon (CLAUDE.md forbids only writable
clients).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

RAW_TARGET_H = 48.0
DEGRADE_FLOOR_H = 24.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db", nargs="?", type=Path,
                    default=Path.home() / ".local/share/ftmon/ftmon.db")
    args = ap.parse_args()
    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = time.time()

    oldest = conn.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
    raw_h = (now - oldest) / 3600 if oldest else 0.0
    row = conn.execute(
        "SELECT value FROM meta WHERE key='last_degradation_ts'"
    ).fetchone()
    last_degrade = float(row["value"]) if row else None
    recent = conn.execute(
        "SELECT COUNT(*) FROM events WHERE message LIKE 'db over budget%' AND ts > ?",
        (now - 3600,),
    ).fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    size = conn.execute("PRAGMA page_size").fetchone()[0]
    conn.close()

    print(f"used bytes            {(pages - free) * size / 2**20:7.1f} MB")
    print(f"raw window            {raw_h:7.2f} h   (DM-04 target {RAW_TARGET_H:.0f})")
    print(f"degradation last hour {recent:7d}")

    if last_degrade is None:
        print("\nno degradation recorded; nothing to recover from")
        return 0

    since_h = (now - last_degrade) / 3600
    when = datetime.fromtimestamp(last_degrade).strftime("%Y-%m-%d %H:%M")
    print(f"last destructive pass {when}  ({since_h:.1f} h ago)")

    # `recent` is context, not the predicate: an hour-wide count still sees
    # passes from before degradation stopped. last_degradation_ts is the
    # anchor, so quiescence is measured from it — a couple of retention
    # cadences (60 s each) without a pass is enough to call it stopped.
    if since_h < 0.2:
        print("\nSTILL DEGRADING — pressure remains; the window cannot recover yet.")
        return 2
    # The window was trimmed to a 24 h floor and regrows at wall-clock rate,
    # so the earliest honest verdict is one floor-width after the last pass.
    due_h = DEGRADE_FLOOR_H
    if since_h < due_h:
        eta = datetime.fromtimestamp(
            last_degrade + due_h * 3600
        ).strftime("%Y-%m-%d %H:%M")
        print(f"\nTOO EARLY — recovery cannot be judged before {eta}.")
        print(f"expected raw window by now: ~{min(RAW_TARGET_H, DEGRADE_FLOOR_H + since_h):.1f} h")
        return 0
    if raw_h >= RAW_TARGET_H - 1:
        print("\nRECOVERED — raw retention is back at the DM-04 window.")
        return 0
    print(f"\nPLATEAUED at {raw_h:.1f} h with no degradation for {since_h:.1f} h.")
    print("Something other than DM-05 degradation is bounding the raw window.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
