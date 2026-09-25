"""MCP tool handlers — one module per entity group.

Each module exposes a `register(mcp, store, adaptor)` function that
registers its tools on the MCPServer instance.
"""

from . import assessment, belief, candidate, hypothesis, observation, programme, trial

__all__ = [
    "assessment",
    "belief",
    "candidate",
    "hypothesis",
    "observation",
    "programme",
    "trial",
]
