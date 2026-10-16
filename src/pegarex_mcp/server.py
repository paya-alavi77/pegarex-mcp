"""stdio bridge to the hosted PegaRex MCP server.

PegaRex runs as a remote MCP server (Streamable HTTP) at
https://pegarex.com.br/api/mcp, and it asks for a free PegaRex account (OAuth).
Most clients can connect to that URL directly and sign in on their own. This
bridge is for the ones that only launch local servers over stdio: it opens one
session to the hosted server and relays tools/list and tools/call through it,
unchanged. All search and analysis happens on the hosted server; nothing is
computed or cached here, and the only thing stored is the login (auth.py).

Set PEGAREX_MCP_URL to point the bridge at another endpoint.
"""
from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import anyio
import httpx
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .auth import LoginError, PegaRexAuth, Store, logout, say

DEFAULT_URL = "https://pegarex.com.br/api/mcp"

USAGE = """usage: pegarex-mcp [login | logout]

  (no command)  run the stdio bridge to the hosted PegaRex MCP server
  login         sign in now (opens your browser) and save the login
  logout        end the saved login, here and on the server

The hosted server asks for a free PegaRex account. Without a saved login, the
bridge opens the sign-in page itself the first time it connects.

Environment: PEGAREX_MCP_URL (the server), PEGAREX_MCP_HOME (where the login is saved).
"""


def _version() -> str:
    try:
        return version("pegarex-mcp")
    except PackageNotFoundError:
        return "0.0.0"


def _http(auth: PegaRexAuth) -> httpx.AsyncClient:
    # The MCP transports' usual timeouts: a server may hold a response open.
    return httpx.AsyncClient(auth=auth, timeout=httpx.Timeout(30, read=300),
                             headers={"User-Agent": f"pegarex-mcp/{_version()}"})


async def serve(url: str) -> None:
    async with _http(PegaRexAuth(Store(url))) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _session_id):
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


async def login(url: str) -> None:
    store = Store(url)
    auth = PegaRexAuth(store)
    async with _http(auth) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _session_id):
            async with ClientSession(read, write) as remote:
                await remote.initialize()
                tools = await remote.list_tools()
    if not auth.signed_in:
        say("already signed in.")
    say(f"connected: {len(tools.tools)} tools (login saved in {store.path}).")


async def sign_out(url: str) -> None:
    result = await logout(Store(url))
    if result == "ended":
        say("signed out: the login was ended here and on the server.")
    elif result == "local":
        say("signed out here, but the server could not be told; that login stops working "
            "on its own after 30 days unused.")
    else:
        say("not signed in.")


def _login_error(exc: BaseException | None, seen: set[int] | None = None) -> LoginError | None:
    """The sign-in problem behind `exc`, if any: the MCP client runs in task groups,
    which wrap what went wrong."""
    seen = set() if seen is None else seen
    if exc is None or id(exc) in seen:
        return None
    seen.add(id(exc))
    if isinstance(exc, LoginError):
        return exc
    for inner in [*(getattr(exc, "exceptions", None) or ()), exc.__cause__, exc.__context__]:
        found = _login_error(inner, seen)
        if found:
            return found
    return None


def _innermost(exc: BaseException) -> BaseException:
    group = getattr(exc, "exceptions", None)
    return _innermost(group[0]) if group else exc


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "serve"
    if command in ("-h", "--help", "help"):
        print(USAGE, file=sys.stderr)
        return
    run = {"serve": serve, "login": login, "logout": sign_out}.get(command)
    if run is None:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    url = os.environ.get("PEGAREX_MCP_URL", DEFAULT_URL)
    try:
        anyio.run(run, url)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # stdout is the protocol channel, so report on stderr
        problem = _login_error(exc)
        say(str(problem) if problem else f"error talking to {url}: {_innermost(exc)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
