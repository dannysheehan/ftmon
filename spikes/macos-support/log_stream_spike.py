#!/usr/bin/env python3
"""Exercise macOS unified-log streaming and timestamp-based replay."""

from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUBSYSTEM = "org.ftmon.spike"


def compile_emitter(output: Path) -> None:
    subprocess.run(
        ["xcrun", "clang", str(ROOT / "log_emitter.c"), "-o", str(output)],
        check=True,
    )


def json_records(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("eventType") == "logEvent":
            records.append(value)
    return records


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ftmon-macos-log-") as raw_tmp:
        tmp = Path(raw_tmp)
        emitter = tmp / "log-emitter"
        compile_emitter(emitter)

        stream = subprocess.Popen(
            [
                "/usr/bin/log",
                "stream",
                "--style",
                "ndjson",
                "--level",
                "debug",
                "--predicate",
                f'subsystem == "{SUBSYSTEM}"',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1)
        first = f"FTMON_STREAM_FIRST_{time.time_ns()}"
        subprocess.run([str(emitter), first], check=True)
        time.sleep(1)
        stream.send_signal(signal.SIGINT)
        stdout, stderr = stream.communicate(timeout=10)
        streamed = [row for row in json_records(stdout) if row.get("eventMessage") == first]
        print(f"stream_exit={stream.returncode}")
        print(f"stream_stderr={stderr.strip()!r}")
        print(f"stream_records={len(streamed)}")
        if not streamed:
            print(stdout)
            return 1

        row = streamed[0]
        print(f"field_names={sorted(row)}")
        print(f"first_timestamp={row['timestamp']}")

        # Monterey rejects the stream's fractional timestamp as --start input.
        # Truncate to the accepted second-resolution form, deliberately creating
        # an overlap suitable for at-least-once replay plus deduplication.
        start = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S.%f%z")
        start_arg = start.strftime("%Y-%m-%d %H:%M:%S%z")

        # Add an event after the saved timestamp, then demonstrate that replay
        # includes both the boundary event and the new one.
        second = f"FTMON_STREAM_SECOND_{time.time_ns()}"
        subprocess.run([str(emitter), second], check=True)
        time.sleep(1)
        replay = subprocess.run(
            [
                "/usr/bin/log",
                "show",
                "--style",
                "ndjson",
                "--start",
                start_arg,
                "--predicate",
                f'subsystem == "{SUBSYSTEM}"',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        replayed = json_records(replay.stdout)
        messages = [item.get("eventMessage") for item in replayed]
        replayed_first = next(item for item in replayed if item.get("eventMessage") == first)
        print(f"replay_start_arg={start_arg}")
        print(f"replay_first_count={messages.count(first)}")
        print(f"replay_second_count={messages.count(second)}")
        print(f"stream_vs_store_timestamp={row['timestamp']} / {replayed_first['timestamp']}")
        print(f"replay_finished_at={datetime.now().astimezone().isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
