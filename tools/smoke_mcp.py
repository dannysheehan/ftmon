"""[MC-01][MC-05][TS-06] Smoke an installed FTMON wheel over real MCP stdio."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

from ftmon import __version__
from ftmon.mcp_server import TOOL_NAMES

_RESOURCE_URIS = (
    "ftmon://docs/definitions",
    "ftmon://docs/check-authoring",
    "ftmon://docs/external-checks",
)


async def _exercise(params: StdioServerParameters, mode: str, expected_protocol: str) -> None:
    async with Client(params, mode=mode) as client:
        if client.protocol_version != expected_protocol:
            raise SystemExit(
                f"{mode} protocol {client.protocol_version!r} != {expected_protocol!r}"
            )
        if (
            client.server_info is None
            or client.server_info.name != "ftmon"
            or client.server_info.version != __version__
        ):
            raise SystemExit("MCP server metadata does not report the FTMON package version")
        if "human action" not in (client.instructions or ""):
            raise SystemExit("MCP server instructions are missing the approval boundary")

        tools = await client.list_tools()
        if {tool.name for tool in tools.tools} != set(TOOL_NAMES):
            raise SystemExit("installed MCP tool surface differs from TOOL_NAMES")
        for tool in tools.tools:
            if tool.annotations is None:
                raise SystemExit(f"installed MCP tool lacks annotations: {tool.name}")
            read_only = tool.name not in {"define_monitor", "ack_incident"}
            if tool.annotations.read_only_hint is not read_only:
                raise SystemExit(f"installed MCP tool has wrong authority: {tool.name}")
            if (
                tool.annotations.destructive_hint is not False
                or tool.annotations.open_world_hint is not False
            ):
                raise SystemExit(f"installed MCP tool has unsafe annotations: {tool.name}")
        status = await client.call_tool("get_status", {})
        if status.is_error or not status.content:
            raise SystemExit("installed MCP get_status call failed")
        json.loads(status.content[0].text)

        resources = await client.list_resources()
        if tuple(str(resource.uri) for resource in resources.resources) != _RESOURCE_URIS:
            raise SystemExit("installed MCP resource surface is incomplete")
        for uri in _RESOURCE_URIS:
            body = await client.read_resource(uri)
            if not body.contents:
                raise SystemExit(f"installed MCP resource is empty: {uri}")


async def _main() -> None:
    with tempfile.TemporaryDirectory(prefix="ftmon-mcp-smoke-") as temp:
        root = Path(temp)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ftmon.cli", "mcp"],
            env={
                "FTMON_CONFIG_DIR": str(root / "config"),
                "FTMON_DATA_DIR": str(root / "data"),
                "FTMON_STATE_DIR": str(root / "state"),
                "FTMON_RUNTIME_DIR": str(root / "run"),
            },
        )
        await _exercise(params, "auto", "2026-07-28")
        await _exercise(params, "legacy", "2025-11-25")


if __name__ == "__main__":
    asyncio.run(_main())
    print("MCP installed-wheel stdio smoke OK")
