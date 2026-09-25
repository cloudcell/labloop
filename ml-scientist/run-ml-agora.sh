#!/usr/bin/env bash
# Start the agora MCP server (the lab status hub) with HTTP transport.
# Read-only role-filler — it aggregates every server's status digest
# and never writes into a loop (ADR-0004/0005). Ports come from
# ports.env in the project root.
set -euo pipefail
cd "$(dirname "$0")"
source ./ports.env
exec uv run ml-agora-mcp \
    --transport http \
    --port "${ML_AGORA_PORT}" \
    --stateless \
    --observability-port "${ML_AGORA_GUI_PORT}" \
    --log-tool-args
