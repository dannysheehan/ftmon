"""SQLite storage layer (DESIGN.md section 8/12).

`db` owns connections/migrations, `writer.TickWriter` is the daemon's single
bulk writer (one commit per tick, PM-03), `query.Query` is the shared
read-only facade used by the CLI/MCP/web (DM-06).
"""

from __future__ import annotations
