"""Rule-message rendering (MD-02, NO-01).

Templates were validated at load time, but *values* only exist at fire time
and may be None (insufficient data). Rendering must never raise mid-cycle,
so None renders as "n/a" with any format spec ignored — a notification with
one "n/a" beats a crashed tick.
"""

from __future__ import annotations

import string
from collections.abc import Mapping

_BODY_MAX = 200  # NO-01


class _Formatter(string.Formatter):
    def get_value(self, key, args, kwargs):  # noqa: ANN001 - stdlib signature
        return kwargs.get(key, "n/a")

    def format_field(self, value, format_spec):  # noqa: ANN001
        if value is None or value == "n/a":
            return "n/a"
        try:
            return super().format_field(value, format_spec)
        except (ValueError, TypeError):
            return str(value)


_FMT = _Formatter()


def render_message(template: str, values: Mapping[str, object]) -> str:
    try:
        return _FMT.vformat(template, (), dict(values))[:_BODY_MAX]
    except Exception:
        # Validated templates should never get here; the fallback keeps the
        # never-raise property absolute.
        return template[:_BODY_MAX]
