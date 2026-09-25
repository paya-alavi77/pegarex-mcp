# stdio bridge to the hosted PegaRex MCP server (https://pegarex.com.br/api/mcp).
# The container holds no data: it relays tools/list and tools/call to the
# hosted server, so it needs outbound HTTPS and nothing else.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 app
USER app
ENTRYPOINT ["pegarex-mcp"]
