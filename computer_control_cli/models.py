"""Domain models parsed from hyprctl's JSON.

Monitor, Workspace and Window are immutable views of one row each. Every
``from_hypr`` classmethod answers ``None`` for a row that is not an object,
so a malformed reply contributes nothing instead of raising out of a read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from computer_control_cli.coerce import Coerce  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports


@dataclass(frozen=True)
class Monitor:
    """One monitor's LOGICAL rect (physical size divided by its scale)."""

    name: str
    x: int
    y: int
    w: int
    h: int
    focused: bool
    scale: float
    width: int
    height: int

    @classmethod
    def from_hypr(cls, raw: Any) -> Optional["Monitor"]:
        if not isinstance(raw, dict):
            return None
        scale = Coerce.as_float(raw.get("scale"), 1.0) or 1.0
        return cls(
            name=Coerce.as_str(raw.get("name")),
            x=Coerce.as_int(raw.get("x")),
            y=Coerce.as_int(raw.get("y")),
            w=int(Coerce.as_float(raw.get("width"), 0.0) / scale),
            h=int(Coerce.as_float(raw.get("height"), 0.0) / scale),
            focused=bool(raw.get("focused")),
            scale=scale,
            width=Coerce.as_int(raw.get("width")),
            height=Coerce.as_int(raw.get("height")),
        )

    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    def to_dict(self) -> dict:
        return {"name": self.name, "x": self.x, "y": self.y,
                "w": self.w, "h": self.h, "focused": self.focused,
                "scale": self.scale}

    def contains(self, x: int, y: int) -> bool:
        """Half-open containment, so one point has one owning monitor."""
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


@dataclass(frozen=True)
class Workspace:
    """One workspace row: its id, name and the monitor drawing it."""

    id: int
    name: str
    monitor: str
    windows: int

    @classmethod
    def from_hypr(cls, raw: Any) -> Optional["Workspace"]:
        if not isinstance(raw, dict):
            return None
        return cls(
            id=Coerce.as_int(raw.get("id")),
            name=Coerce.as_str(raw.get("name")),
            monitor=Coerce.as_str(raw.get("monitor")),
            windows=Coerce.as_int(raw.get("windows")),
        )

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "monitor": self.monitor,
                "windows": self.windows}


@dataclass(frozen=True)
class Window:
    """One client window, in the shape every reader expects."""

    address: str
    title: str
    cls: str
    workspace: int
    x: int
    y: int
    w: int
    h: int
    mapped: bool
    fullscreen: bool
    floating: bool
    pid: int

    @classmethod
    def from_hypr(cls, raw: Any) -> Optional["Window"]:
        if not isinstance(raw, dict):
            return None
        address = Coerce.as_str(raw.get("address"))
        if not address:
            return None          # hyprctl's empty reply: nothing is focused
        workspace = raw.get("workspace")
        workspace_id = -1
        if isinstance(workspace, dict):
            workspace_id = Coerce.as_int(workspace.get("id"), -1)
        x, y = Coerce.pair(raw.get("at"))
        w, h = Coerce.pair(raw.get("size"))
        return cls(
            address=address,
            title=Coerce.as_str(raw.get("title")),
            cls=Coerce.as_str(raw.get("class")),
            workspace=workspace_id,
            x=x, y=y, w=w, h=h,
            mapped=bool(raw.get("mapped")),
            fullscreen=bool(raw.get("fullscreen")),
            floating=bool(raw.get("floating")),
            pid=Coerce.as_int(raw.get("pid")),
        )

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def brief(self) -> dict[str, Any]:
        """The compact row the CLI prints for a verb's reply."""
        return {"address": self.address, "class": self.cls,
                "title": self.title, "workspace": self.workspace,
                "geometry": [self.x, self.y, self.w, self.h]}

    def to_dict(self) -> dict[str, Any]:
        """The full row: every field a caller may act on."""
        return {"address": self.address, "class": self.cls,
                "title": self.title, "workspace": self.workspace,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "mapped": self.mapped, "fullscreen": self.fullscreen,
                "floating": self.floating, "pid": self.pid}


class WindowSpec:
    """A window target: ``class:``, ``title:``, ``address:`` or a bare name."""

    _PREFIX = re.compile(r"(?i)^(class|title|address):(.*)$")
    _ADDRESS = re.compile(r"0x[0-9a-fA-F]+")

    def __init__(self, field: str, value: str):
        self.field = field
        self.value = value

    @classmethod
    def parse(cls, text: Any) -> "WindowSpec":
        raw = str(text).strip()
        if not raw:
            raise ControlError("bad-args", "empty window target")
        if cls._ADDRESS.fullmatch(raw):
            return cls("address", raw)
        match = cls._PREFIX.match(raw)
        if match:
            return cls(match.group(1).lower(), match.group(2).strip())
        return cls("", raw)

    def matches(self, window: Window) -> bool:
        if not self.value:
            return False
        needle = self.value.lower()
        if self.field == "address":
            return window.address.lower() == needle
        if self.field == "class":
            return needle in window.cls.lower()
        if self.field == "title":
            return needle in window.title.lower()
        return needle in window.cls.lower() or needle in window.title.lower()

    def describe(self) -> str:
        return f"{self.field}:{self.value}" if self.field else self.value
