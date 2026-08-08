"""Windows frozen/MSI smoke checks for the packaging workflow (issue #95).

Requires ``FTMON_EXE`` to point at the MSI-installed (or onedir) ``ftmon.exe``.
Never resolves ``ftmon`` via PATH/``uv run``, which can select a development
shim. Invoked by ``.github/workflows/windows-packaging.yml``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def _ftmon() -> str:
    explicit = os.environ.get("FTMON_EXE", "").strip()
    if not explicit:
        raise SystemExit(
            "FTMON_EXE must be set to the absolute installed ftmon.exe "
            "(do not resolve via PATH / uv run)"
        )
    path = Path(explicit)
    if not path.is_file():
        raise SystemExit(f"FTMON_EXE does not exist: {path}")
    if not path.is_absolute():
        raise SystemExit(f"FTMON_EXE must be absolute: {path}")
    resolved = str(path.resolve())
    # Guard against accidentally testing the checkout virtualenv shim.
    lowered = resolved.lower().replace("/", "\\")
    if "\\.venv\\" in lowered:
        raise SystemExit(f"FTMON_EXE looks like a venv shim, not the MSI install: {resolved}")
    return resolved


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, capture_output=True, text=True, check=check, env=env)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_three_ticks(exe: str, env: dict[str, str], daemon: subprocess.Popen[bytes]) -> None:
    """Require three distinct last_tick_ts advances (not merely 'any tick')."""
    seen: list[float] = []
    deadline = time.time() + 90
    while time.time() < deadline:
        if daemon.poll() is not None:
            raise SystemExit(f"daemon exited early with code {daemon.returncode}")
        status = _run([exe, "status", "--json"], env=env, check=False)
        if status.returncode == 0 and status.stdout.strip():
            payload = json.loads(status.stdout)
            tick = payload.get("last_tick_ts")
            if isinstance(tick, (int, float)):
                if not seen or tick != seen[-1]:
                    seen.append(float(tick))
                    print(f"tick {len(seen)}: {tick}", flush=True)
                if len(seen) >= 3:
                    return
        time.sleep(1)
    raise SystemExit(f"daemon did not advance three ticks; saw {seen!r}")


def _assert_loopback_only(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        pass
    # Wildcard / non-loopback must not accept connections (NG-05).
    try:
        with socket.create_connection(("0.0.0.0", port), timeout=1):
            raise SystemExit("web accepted a connection via 0.0.0.0 (not loopback-only)")
    except OSError:
        pass
    # Enumerate listeners when psutil is available in the smoke driver env.
    try:
        import psutil
    except ImportError:
        return
    listeners = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != "LISTEN":
            continue
        if conn.laddr and conn.laddr.port == port:
            listeners.append(conn.laddr.ip)
    if not listeners:
        raise SystemExit(f"no listener found on port {port}")
    for ip in listeners:
        if ip not in ("127.0.0.1", "::1"):
            raise SystemExit(f"web listening on non-loopback address {ip!r}")


def main() -> int:
    exe = _ftmon()
    version = _run([exe, "--version"]).stdout.strip()
    print("version:", version)
    print("exe:", exe)

    root = Path(os.environ["FTMON_SMOKE_ROOT"])
    config = root / "config"
    data = root / "data"
    state = root / "state"
    runtime = root / "runtime"
    for path in (config, data, state, runtime):
        path.mkdir(parents=True, exist_ok=True)
    # Keep child PATH free of Python/uv so the installed binary cannot fall
    # back to a development environment for subprocess helpers.
    stripped_path = os.pathsep.join(
        [
            str(Path(exe).parent),
            os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
            os.environ.get("SystemRoot", r"C:\Windows"),
        ]
    )
    env = {
        **os.environ,
        "PATH": stripped_path,
        "FTMON_CONFIG_DIR": str(config),
        "FTMON_DATA_DIR": str(data),
        "FTMON_STATE_DIR": str(state),
        "FTMON_RUNTIME_DIR": str(runtime),
    }

    _run([exe, "init", "--profile", "windesktop"], env=env)
    check = _run([exe, "check"], env=env, check=False)
    if check.returncode != 0:
        raise SystemExit(f"ftmon check failed: {check.stdout}{check.stderr}")

    daemon = subprocess.Popen([exe, "daemon"], env=env)
    try:
        _wait_three_ticks(exe, env, daemon)
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon.kill()

    port = _free_port()
    web = subprocess.Popen([exe, "web", "--port", str(port)], env=env)
    try:
        html = None
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                    html = resp.read()
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.5)
        if not html:
            raise SystemExit("web UI did not respond on loopback")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/static/ftmon.css", timeout=2
        ) as resp:
            if resp.status != 200:
                raise SystemExit(f"static asset status {resp.status}")
        _assert_loopback_only(port)
    finally:
        web.terminate()
        try:
            web.wait(timeout=15)
        except subprocess.TimeoutExpired:
            web.kill()

    doctor = _run([exe, "doctor"], env=env, check=False)
    print(doctor.stdout)
    if doctor.returncode != 0:
        raise SystemExit(
            f"ftmon doctor failed ({doctor.returncode}): {doctor.stdout}{doctor.stderr}"
        )
    if "ImportError" in (doctor.stdout + doctor.stderr):
        raise SystemExit("doctor reported ImportError (toast/pywin32?)")

    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:
        raise SystemExit(f"mcp client required for smoke MCP handshake: {exc}") from exc

    import asyncio

    async def _mcp() -> None:
        params = StdioServerParameters(command=exe, args=["mcp"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                if not tools.tools:
                    raise SystemExit("expected MCP tools")
                resources = await session.list_resources()
                if not resources.resources:
                    raise SystemExit("expected MCP resources")
                guide = next(
                    r for r in resources.resources if "definitions" in str(r.uri)
                )
                body = await session.read_resource(guide.uri)
                if not body.contents:
                    raise SystemExit("expected packaged guide contents")

    asyncio.run(_mcp())
    print("smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
