"""stdio bridge to the hosted PegaRex MCP server.

PegaRex runs as a remote MCP server (Streamable HTTP, no auth, no API key) at
https://pegarex.com.br/api/mcp. Most clients can connect to that URL directly.
This bridge is for the ones that only launch local servers over stdio: it
opens one session to the hosted server and relays tools/list and tools/call
through it, unchanged. All search and analysis happens on the hosted server;
nothing is computed, cached or stored here.

Set PEGAREX_MCP_URL to point the bridge at another endpoint.
"""
from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import anyio
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# The documented endpoint is .../api/mcp; the server itself lives one slash
# further, and /api/mcp answers every request with a 307 to it. Going straight
# to the final URL saves that extra round trip on each call.
DEFAULT_URL = "https://pegarex.com.br/api/mcp/"


def _version() -> str:
    try:
        return version("pegarex-mcp")
    except PackageNotFoundError:
        return "0.0.0"


async def serve(url: str) -> None:
    async with streamable_http_client(url) as (read, write, _session_id):
        async with ClientSession(read, write) as remote:
            init = await remote.initialize()
            server: Server[Any, Any] = Server(
                "pegarex",
                version=_version(),
                instructions=init.instructions,
                website_url="https://pegarex.com.br/mcp",
            )

            @server.list_tools()
            async def list_tools() -> list[types.Tool]:
                return (await remote.list_tools()).tools

            # The hosted server validates the arguments and builds the whole
            # result (text and structured content, error flag included), so it
            # is passed through as-is rather than re-validated here.
            @server.call_tool(validate_input=False)
            async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
                return await remote.call_tool(name, arguments)

            async with stdio_server() as (stdin, stdout):
                await server.run(stdin, stdout, server.create_initialization_options())


def main() -> None:
    url = os.environ.get("PEGAREX_MCP_URL", DEFAULT_URL)
    try:
        anyio.run(serve, url)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # stdout is the protocol channel, so report on stderr
        print(f"pegarex-mcp: error talking to {url}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
