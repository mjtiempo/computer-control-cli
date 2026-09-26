"""Machine audit: what this desktop has, and whether clicks can be verified.

One JSON blob answers two questions: which tools and capabilities exist, and
whether the calibration every click depends on still holds. A critical
failure (session, tools, screenshot 1:1, pointer calibration) makes the audit
refuse; a warning (a11y coverage, clipboard) is reported and does not.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from computer_control_cli.a11y import A11y  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input  # pi-lens-ignore: reportMissingImports
from computer_control_cli.screen import Screen  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports


@dataclass
class Check:
    """One audit row: what was checked and what it found."""

    name: str
    ok: bool
    critical: bool
    detail: str = ""


class CheckLog:
    """The rows, and the critical ones that failed."""

    def __init__(self):
        self.checks: list[Check] = []

    def add(self, name: str, ok: bool, critical: bool,
            detail: str = "") -> None:
        self.checks.append(Check(name, bool(ok), bool(critical), str(detail)))

    def critical_failed(self) -> list[str]:
        return [check.name for check in self.checks
                if check.critical and not check.ok]

    def to_dicts(self) -> list[dict]:
        return [asdict(check) for check in self.checks]


class Audit:
    """The install/start-up gate and capability report."""

    CRITICAL_TOOLS = ("hyprctl", "ydotool", "wtype", "grim")
    OPTIONAL_TOOLS = ("wl-copy", "wl-paste", "busctl")

    def __init__(self, session: Session, desktop: Optional[Desktop] = None,
                 screen: Optional[Screen] = None,
                 input: Optional[Input] = None,
                 a11y: Optional[A11y] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None):
        self.session = session
        self.desktop = desktop or Desktop(session)
        self.screen = screen or Screen(session)
        self.input = input or Input(session)
        self.a11y = a11y or A11y(session, input=self.input,
                                 desktop=self.desktop)
        self.which = which or shutil.which

    def audit(self, no_probe: bool = False, no_a11y: bool = False) -> dict:
        log = CheckLog()
        if self.session.resolved_env() is None:
            log.add("session", False, True, "no live Hyprland socket")
            return self.result(log, {})
        log.add("session", True, True, "live")
        for name in self.CRITICAL_TOOLS:
            log.add(f"tool:{name}", bool(self.which(name)), True, "required")
        for name in self.OPTIONAL_TOOLS:
            log.add(f"tool:{name}", bool(self.which(name)), False, "optional")
        monitors = self.session.monitors()
        log.add("monitors", bool(monitors), False,
                f"{len(monitors)} monitor(s)")
        shot = self.screenshot_check(log)
        calibration = self.calibration_check(log, no_probe)
        a11y = self.a11y_check(log, no_a11y)
        capabilities = self.capabilities(monitors, shot, calibration, a11y)
        return self.result(log, capabilities)

    def screenshot_check(self, log: CheckLog) -> dict:
        """Prove the capture is a 1:1 map of the logical desktop."""
        try:
            data = self.screen.full(no_cursor=True)
            width, height = Screen.png_size(data)
            box = self.session.desktop_bbox()
            ok = width == box[2] and height == box[3]
            log.add("screenshot-1to1", ok, True,
                    f"{width}x{height} vs logical {box[2]}x{box[3]}")
            return {"ok": ok, "size": [width, height],
                    "logical": [box[2], box[3]]}
        except ControlError as error:
            log.add("screenshot-1to1", False, True, error.message)
            return {"ok": False, "detail": error.message}

    def calibration_check(self, log: CheckLog, skipped: bool) -> dict:
        """Warp to two probe points, read the cursor back, and restore it."""
        if skipped:
            log.add("calibration", True, False, "skipped (--no-probe)")
            return {"skipped": True}
        start = None
        try:
            position = self.input.pos()
            start = (position["x"], position["y"])
            anchor = self.session.anchor_monitor()
            if anchor is None or anchor.w <= 0 or anchor.h <= 0:
                raise ControlError("move-not-verified",
                                   "no monitor with a logical size")
            probes = [(anchor.x + anchor.w // 4, anchor.y + anchor.h // 4),
                      (anchor.x + anchor.w * 3 // 4,
                       anchor.y + anchor.h * 3 // 4)]
            for probe in probes:
                self.input.move(*probe)
            log.add("calibration", True, True,
                    f"warp verified at {probes}")
            return {"ok": True, "probes": probes}
        except ControlError as error:
            log.add("calibration", False, True, error.message)
            return {"ok": False, "detail": error.message}
        finally:
            if start is not None:
                try:
                    self.input.move(*start)
                except ControlError:
                    pass

    def a11y_check(self, log: CheckLog, skipped: bool) -> dict:
        if skipped:
            log.add("a11y", True, False, "skipped (--no-a11y)")
            return {"skipped": True}
        try:
            rows = self.a11y.index(max_items=1)
            log.add("a11y", True, False, f"{len(rows)} row(s), capped at 1")
            return {"ok": True, "rows": len(rows)}
        except ControlError as error:
            log.add("a11y", False, False, error.message)
            return {"ok": False, "detail": error.message}

    def capabilities(self, monitors: list, shot: dict, calibration: dict,
                     a11y: dict) -> dict:
        tools = {name: bool(self.which(name))
                 for name in (*self.CRITICAL_TOOLS, *self.OPTIONAL_TOOLS)}
        box = self.session.desktop_bbox()
        capabilities: dict = {
            "monitors": [monitor.to_dict() for monitor in monitors],
            "logical_screen": [box[2], box[3]],
            "tools": tools,
            "screenshot": shot,
            "calibration": calibration,
            "a11y": a11y,
        }
        try:
            capabilities["focused_workspace"] = self.desktop.active_workspace_id()
            capabilities["windows"] = len(self.desktop.windows())
            capabilities["workspaces"] = len(self.desktop.workspaces())
        except ControlError as error:
            capabilities["detail"] = error.message
        return capabilities

    @staticmethod
    def result(log: CheckLog, capabilities: dict) -> dict:
        failed = log.critical_failed()
        return {"ok": not failed, "critical_failed": failed,
                "checks": log.to_dicts(), "capabilities": capabilities}
