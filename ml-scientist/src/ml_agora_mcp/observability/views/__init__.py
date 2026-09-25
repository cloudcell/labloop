"""Dashboard plugins for the agora observability GUI.

Each plugin is a self-contained module exporting:

- ``ROUTE`` — the nav target (canonical page path)
- ``NAV_TITLE`` — label in the right-side menu panel
- ``routes(ctx)`` — returns ``list[starlette.routing.Route]``; ``ctx``
  is the ``GuiContext`` built in ``observability.server`` (adaptors,
  log_dir, mcp_health_url)

Retiring a dashboard = deleting its module and removing one entry
from ``PLUGINS``. Extracting a dashboard into a separate package =
moving one file. The right-side menu is generated from this list —
no other wiring exists.
"""

from __future__ import annotations

from . import graph, integrity, overview, topology

PLUGINS = [overview, graph, topology, integrity]
