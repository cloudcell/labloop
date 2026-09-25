#!/usr/bin/env bash
# Start the zetesis MCP server (Loop 1 — the search loop) with HTTP transport.
# Separate process, separate port, separate store — ADR-0001.
# Ports come from ports.env in the project root.
set -euo pipefail
cd "$(dirname "$0")"
source ./ports.env
exec uv run ml-zetesis-mcp \
    --transport http \
    --port "${ML_ZETESIS_PORT}" \
    --stateless \
    --observability-port "${ML_ZETESIS_GUI_PORT}" \
    --log-tool-args
