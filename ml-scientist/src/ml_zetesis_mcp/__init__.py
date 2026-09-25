"""ml-zetesis-mcp — Loop 1, the search loop.

Zetesis (ζήτησις — seeking, inquiry) is the ecosystem's researcher
server: it investigates how research is done. Its enforced lifecycle is
open_investigation → pull_evidence → record_finding →
conclude_investigation, and its distilled output is `methodological`
claims minted to anamnesis with derived_from edges to the Loop-0
entities consulted.

Own server, own store (search.db), own ports — ADR-0001: one MCP
server per loop. It is an MCP *client* of ml-episteme (evidence,
read-only) and anamnesis (claims) — commands flow down, evidence flows
up, only over the protocol. Imports nothing from ml_episteme_mcp.
"""
