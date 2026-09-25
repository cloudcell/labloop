#!/usr/bin/env bash
# Start the arete MCP server (Loop 2 — the recursive loop) with HTTP transport.
# Separate process, separate port, separate store — ADR-0001.
# Ports come from ports.env in the project root.
set -euo pipefail
cd "$(dirname "$0")"
source ./ports.env
exec uv run ml-arete-mcp \
    --transport http \
    --port "${ML_ARETE_PORT}" \
    --stateless \
    --observability-port "${ML_ARETE_GUI_PORT}" \
    --log-tool-args
