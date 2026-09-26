"""Structured refusals.

Every anticipated failure is a :class:`ControlError` with a stable,
machine-readable ``code`` and a message a caller can act on. The CLI turns
one into ``ERR[code]: message`` on stderr; library callers catch it.
"""

from __future__ import annotations


class ControlError(Exception):
    """An anticipated refusal: ``code`` is the contract, ``message`` the prose."""

    def __init__(self, code: str = "error", message: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message
