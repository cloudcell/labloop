"""Adaptor layer — maps roles to concrete MCP servers via configuration.

The adaptor layer is the ONLY place where product names appear. The rest
of the server knows only the role interface. Adaptors are swappable; roles
are not.
"""

from .roles import ClaimsRole, DataSourceRole, ExecutorRole, OptimizerRole

__all__ = ["ClaimsRole", "DataSourceRole", "ExecutorRole", "OptimizerRole"]
