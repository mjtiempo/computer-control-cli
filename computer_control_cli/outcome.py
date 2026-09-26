"""The outcome of a desktop-changing action.

Every verb that changes the desktop answers with the same four keys:

``ok``
    the action is done, as far as this verb can tell
``verified``
    the verb READ THE DESKTOP BACK and saw the effect it was asked for
``effect``
    ``confirmed`` | ``unconfirmed`` | ``unverifiable``
``rung``
    how the effect was produced (``dispatch``, ``px``, ``input``, ...)

The constraints are enforced at construction, so a service cannot report an
impossible pair: ``verified`` means exactly ``effect == "confirmed"``, a
failure is never verified, and an absent effect (``unconfirmed``) is never a
success. ``unverifiable`` is the honest answer for pointer and keyboard
events, which no read can prove; it is not a failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Outcome:
    """One action's result, carrying the contract's four keys plus details."""

    ok: bool
    verified: bool
    effect: str
    rung: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verified != (self.effect == "confirmed"):
            raise AssertionError(
                f"effect {self.effect!r} with verified={self.verified!r}")
        if self.verified and not self.ok:
            raise AssertionError(
                f"ok={self.ok!r} with verified={self.verified!r}")
        if self.effect == "unconfirmed" and self.ok:
            raise AssertionError(
                "effect='unconfirmed' with ok=True: an absent effect is not "
                "a success")

    @classmethod
    def confirmed(cls, rung: str, **details: Any) -> "Outcome":
        """The effect was read back and is present."""
        return cls(ok=True, verified=True, effect="confirmed", rung=rung,
                   details=dict(details))

    @classmethod
    def unverifiable(cls, rung: str, ok: bool = True,
                     **details: Any) -> "Outcome":
        """No read can prove this effect (pointer, keyboard, app-owned)."""
        return cls(ok=ok, verified=False, effect="unverifiable", rung=rung,
                   details=dict(details))

    @classmethod
    def unconfirmed(cls, rung: str, **details: Any) -> "Outcome":
        """The effect was read back and is ABSENT: a failure."""
        return cls(ok=False, verified=False, effect="unconfirmed", rung=rung,
                   details=dict(details))

    def to_dict(self) -> dict[str, Any]:
        """The reply as printed: the four keys first, then the details."""
        return {"ok": self.ok, "verified": self.verified,
                "effect": self.effect, "rung": self.rung, **self.details}
