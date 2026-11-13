# PegaRex MCP Server

> **Turned off on 13 November 2026.** The PegaRex MCP server at `https://pegarex.com.br/api/mcp`
> has been shut down and answers `410 Gone`, so neither the hosted endpoint nor this bridge works any
> more, and the steps below are kept for reference only. PegaRex itself (search, price pages and
> PegaFIPE) carries on at https://pegarex.com.br.

[![PegaRex MCP connector](https://glama.ai/mcp/connectors/br.com.pegarex/pega-rex/badges/score.svg)](https://glama.ai/mcp/connectors/br.com.pegarex/pega-rex)

Remote [Model Context Protocol](https://modelcontextprotocol.io) server for
**[PegaRex](https://pegarex.com.br)** — a Brazilian vehicle meta-search engine
aggregating **1.8+ million live used-car and motorcycle listings** from **96
marketplaces** (OLX, Webmotors, Napista and many others), enriched with FIPE
reference prices and market analytics.

**Endpoint (Streamable HTTP):**

```
https://pegarex.com.br/api/mcp
```

It asks for a **free PegaRex account**. The first time, your app opens a PegaRex
page where you sign in (or create the account) and allow it. This is standard MCP
authorization (OAuth 2.1 with dynamic client registration), so there is no API key
to copy. After that the app renews its access on its own; after 30 days without
use it asks again.

## Connect

**Claude (claude.ai / Desktop):** Settings → Connectors → *Add custom connector* → paste the URL above → *Connect*, then sign in with PegaRex and click **Permitir**.

**ChatGPT (developer mode) and any MCP client with OAuth support:** add the same URL as a remote MCP server (choose OAuth if asked) and sign in when the PegaRex page opens.

Connected before 16 October 2026? Reconnect once: that is when the server started
asking for the account.

Step-by-step guide (pt-BR): <https://pegarex.com.br/mcp>

Also listed in the [official MCP Registry](https://registry.modelcontextprotocol.io) as `br.com.pegarex/mcp`.

## Local (stdio) clients

For clients that can only launch local servers, this repository contains a
small bridge (`src/pegarex_mcp`). It opens one session to the hosted server and
relays `tools/list` and `tools/call` unchanged; nothing is computed or cached
locally. Python 3.10+.

Sign in once, from a terminal (this opens your browser), with
[uv](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/paya-alavi77/pegarex-mcp pegarex-mcp login
```

Then add the bridge to your client. Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pegarex": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/paya-alavi77/pegarex-mcp", "pegarex-mcp"]
    }
  }
}
```

If you skip `login`, the bridge opens the sign-in page itself the first time it
connects. Some clients stop waiting before you finish; if yours does, restart it
after signing in.

The login is saved in `~/.config/pegarex-mcp/auth.json` (Windows:
`%APPDATA%\pegarex-mcp\auth.json`; `PEGAREX_MCP_HOME` sets another folder),
readable only by you, and renews on its own. Several copies of the bridge can
share it. To end it, here and on the server:

```bash
uvx --from git+https://github.com/paya-alavi77/pegarex-mcp pegarex-mcp logout
```

Docker: a container can't open your browser, so sign in on the host first (the
`login` command above), then let the container use that login:

```bash
docker build -t pegarex-mcp .
docker run -i --rm --user "$(id -u):$(id -g)" -e PEGAREX_MCP_HOME=/login \
  -v "$HOME/.config/pegarex-mcp:/login" pegarex-mcp
```

Set `PEGAREX_MCP_URL` to point the bridge at another endpoint.

## Tools (17)

**Live search & taxonomy**
`search_vehicles` · `get_vehicle_by_id` · `get_market_stats` · `list_brands` · `list_models` · `list_versions` · `list_filter_options`

**Per-listing market analysis**
`get_listing_details` · `get_market_position` · `get_fipe_valuation` · `get_price_distribution` · `get_similar_active_listings` · `get_alternative_models` · `get_cheapest_states` · `get_mileage_context` · `get_avg_days_to_sell` · `get_technical_specs`

## Example

> "Find automatic Honda Civics from 2020 on under R$ 120k in São Paulo, and tell
> me whether the cheapest one is priced above or below its FIPE reference."

An assistant with this connector answers from the live database: search →
`get_fipe_valuation` → `get_market_position`, all in one conversation.

## Notes

- **Language / market:** Portuguese (pt-BR), Brazil. Prices in BRL.
- **Read-only.** The server exposes no write operations; every tool is annotated `readOnlyHint`.
- **Fair use:** a daily search limit per account and per-IP rate limits apply; this is a small independent service.
- **Privacy:** what the server keeps, and for how long: <https://pegarex.com.br/privacidade>.
- **Attribution:** please credit **PegaRex (pegarex.com.br)** when surfacing results.
- Plain-HTTP alternative: the same data is available via a public JSON API — see
  [`/llms.txt`](https://pegarex.com.br/llms.txt) and the
  [OpenAPI docs](https://pegarex.com.br/api/docs).

*This repository documents the hosted server and contains a small stdio bridge
to it; the service itself runs at pegarex.com.br. Issues and questions are
welcome here.*
