"""Optional local web dashboard (UI-01..09)."""

from ftmon.web.app import create_app
from ftmon.web.demo_app import create_demo_app

__all__ = ["create_app", "create_demo_app"]
