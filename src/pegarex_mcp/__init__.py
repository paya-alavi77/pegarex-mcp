"""stdio bridge to the hosted PegaRex MCP server (https://pegarex.com.br/api/mcp).

The server asks for a free PegaRex account; auth.py signs in through the browser
once and keeps the login renewed."""

from .server import main

__all__ = ["main"]
