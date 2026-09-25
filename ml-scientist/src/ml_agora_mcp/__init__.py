"""ml-agora-mcp — the lab status hub (read-only aggregation).

Agora is a role-filler, not a loop (ADR-0004/ADR-0005): it fills the
StatusRole capability — it reads every server's status digest and
synthesizes the lab-level answer to "what do I do next". Its client
has no call_tool path; it can never write into a loop.
"""
