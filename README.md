# PegaRex MCP Server

Remote [Model Context Protocol](https://modelcontextprotocol.io) server for
**[PegaRex](https://pegarex.com.br)** — a Brazilian vehicle meta-search engine
aggregating **1.8+ million live used-car and motorcycle listings** from **96
marketplaces** (OLX, Webmotors, Napista and many others), enriched with FIPE
reference prices and market analytics.

**Endpoint (Streamable HTTP, no auth, no key):**

```
https://pegarex.com.br/api/mcp
```

## Connect

**Claude (claude.ai / Desktop):** Settings → Connectors → *Add custom connector* → paste the URL above.

**ChatGPT (connector mode) and any MCP-capable client:** add the same URL as a remote MCP server.

Step-by-step guide (pt-BR): <https://pegarex.com.br/mcp>

Also listed in the [official MCP Registry](https://registry.modelcontextprotocol.io) as `br.com.pegarex/mcp`.

## Local (stdio) clients

For clients that can only launch local servers, this repository contains a
small bridge (`src/pegarex_mcp`). It opens one session to the hosted server and
relays `tools/list` and `tools/call` unchanged; nothing is computed or stored
locally. Python 3.10+.

With [uv](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/paya-alavi77/pegarex-mcp pegarex-mcp
```

Claude Desktop (`claude_desktop_config.json`):

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

Docker:

```bash
docker build -t pegarex-mcp . && docker run -i --rm pegarex-mcp
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
- **Fair use:** per-IP rate limits apply; this is a small independent service.
- **Attribution:** please credit **PegaRex (pegarex.com.br)** when surfacing results.
- Plain-HTTP alternative: the same data is available via a public JSON API — see
  [`/llms.txt`](https://pegarex.com.br/llms.txt) and the
  [OpenAPI docs](https://pegarex.com.br/api/docs).

*This repository documents the hosted server and contains a small stdio bridge
to it; the service itself runs at pegarex.com.br. Issues and questions are
welcome here.*
