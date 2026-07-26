#!/usr/bin/env python3
"""Small launchd-managed process that records SIGHUP without exiting."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

output = Path(sys.argv[1])
reload_requested = False


def hup(_signum: int, _frame: object) -> None:
    global reload_requested
    reload_requested = True


signal.signal(signal.SIGHUP, hup)
with output.open("a", encoding="utf-8", buffering=1) as stream:
    stream.write(f"started pid={os.getpid()}\n")
    while True:
        if reload_requested:
            stream.write(f"reloaded pid={os.getpid()}\n")
            reload_requested = False
        time.sleep(0.1)
