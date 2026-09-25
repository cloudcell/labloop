#!/usr/bin/env bash
# Start the ml-episteme MCP server with HTTP transport and the observability GUI.
# Ports come from ports.env in the project root.
set -euo pipefail
cd "$(dirname "$0")"
set -a
source ./ports.env
# Optional token for the artifact-ingest surface — secrets live in
# ingest.env (gitignored), never in ports.env. If unset and an ingest
# port is configured, the server refuses to start the listener.
if [ -f ./ingest.env ]; then
    source ./ingest.env
fi
set +a

# Execution-integrity prerequisites. The default sandbox mode is
# "full" (read-only root, sealed-code overlays) which requires
# bubblewrap; if it is missing every trial run FAILS rather than
# degrading. strace powers executed-code capture (trace_reads=auto).
missing=()
command -v bwrap  >/dev/null 2>&1 || missing+=("bubblewrap")
command -v strace >/dev/null 2>&1 || missing+=("strace")
if [ ${#missing[@]} -gt 0 ]; then
    echo "WARNING: missing tools: ${missing[*]}" >&2
    echo "  Install them (e.g. 'apt install ${missing[*]}') or trials will" >&2
    echo "  run with degraded isolation/capture — or fail outright under" >&2
    echo "  executor.sandbox=auto|full|minimal." >&2
fi

args=(
    --transport http
    --port "${ML_EPISTEME_PORT}"
    --stateless
    --observability-port "${ML_EPISTEME_GUI_PORT}"
    --log-tool-args
)
# Ingest is enabled iff a token is configured — the token is the
# switch, not the port. ports.env always carries the port; without
# a token (no ingest.env) the surface is simply absent. The server
# still exits if --ingest-port is passed with no token (fail closed).
if [ -n "${ML_EPISTEME_INGEST_PORT:-}" ] && \
   [ -n "${ML_EPISTEME_INGEST_TOKEN:-}" ]; then
    args+=(--ingest-port "${ML_EPISTEME_INGEST_PORT}")
fi

exec uv run ml-episteme-mcp "${args[@]}"
