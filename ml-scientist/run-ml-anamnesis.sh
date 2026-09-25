#!/usr/bin/env bash
# Start the anamnesis MCP server (cross-programme claims memory) with HTTP transport.
# Separate process, separate port, separate store — ADR-0002.
# Ports come from ports.env in the project root.
set -euo pipefail
cd "$(dirname "$0")"
source ./ports.env
exec uv run ml-anamnesis-mcp \
    --transport http \
    --port "${ML_ANAMNESIS_PORT}" \
    --stateless \
    --observability-port "${ML_ANAMNESIS_GUI_PORT}" \
    --log-tool-args
