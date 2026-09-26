"""Structured refusals.

Every anticipated failure is a :class:`ControlError` with a stable,
machine-readable ``code`` and a message a caller can act on. The CLI turns
one into ``ERR[code]: message`` on stderr; library callers catch it.
"""

from __future__ import annotations

import re
from typing import Optional


class ControlError(Exception):
    """An anticipated refusal: ``code`` is the contract, ``message`` the prose."""

    _LINE = re.compile(r"^ERR\[([a-z0-9-]+)]:\s*(.*)$")

    def __init__(self, code: str = "error", message: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message

    @classmethod
    def from_line(cls, text: str) -> Optional["ControlError"]:
        """The refusal an ``ERR[code]: message`` line carries, or None.

        The one spelling of the protocol: a child command's refusal is
        re-raised as the same code, never wrapped in a new one.
        """
        match = cls._LINE.match(str(text).strip())
        if not match:
            return None
        return cls(match.group(1), match.group(2).strip())

    def __str__(self) -> str:
        return self.message
