"""Parsing helpers for values that arrive from a command's JSON/plain reply.

One class, not loose functions: every reader of a foreign reply uses the same
coercions, and a malformed field becomes a default instead of a traceback out
of a read.
"""

from __future__ import annotations

from typing import Any


class Coerce:
    """Best-effort conversions that never raise on a foreign reply."""

    @staticmethod
    def as_int(value: Any, default: int = 0) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def as_float(value: Any, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def as_str(value: Any, default: str = "") -> str:
        if value is None:
            return default
        return str(value)

    @staticmethod
    def pair(value: Any, fallback: tuple[int, int] = (0, 0)) -> tuple[int, int]:
        """The first two numbers of a hyprctl pair, or the fallback."""
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            first, second = value[0], value[1]
            if isinstance(first, (int, float)) and isinstance(second, (int, float)):
                return int(first), int(second)
        return fallback

    @staticmethod
    def rows(value: Any) -> list[dict]:
        """The dict rows of a reply that claims to be a list of objects."""
        if not isinstance(value, list):
            return []
        return [row for row in value if isinstance(row, dict)]
