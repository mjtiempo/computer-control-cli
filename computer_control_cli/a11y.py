"""AT-SPI element control: a semantic tree and element actions.

The CLI-facing side: it runs the bridge (``atspi_bridge.py``) inside the
desktop session, parses its JSON, and turns a px fallback into a real click —
adding the focused window's origin, because AT-SPI extents are
window-relative on Wayland, and refusing when the element's owner is not the
window that would aim the click.
"""

from __future__ import annotations

import json
import os
from typing import Optional, Sequence

from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input, KeyMap  # pi-lens-ignore: reportMissingImports
from computer_control_cli.outcome import Outcome  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports


class A11y:
    """Element trees and element actions through the bridge."""

    MODE_TIMEOUTS = {"click": 90, "index": 60}

    def __init__(self, session: Session, input: Optional[Input] = None,
                 desktop: Optional[Desktop] = None,
                 bridge: Optional[str] = None, python: str = "python3"):
        self.session = session
        self.input = input or Input(session)
        self.desktop = desktop or Desktop(session)
        self._bridge = bridge
        self._python = python

    @staticmethod
    def bridge_path() -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "atspi_bridge.py")

    def path(self) -> str:
        return self._bridge or self.bridge_path()

    def run(self, mode: str, extra: Sequence[str] = (),
            timeout: Optional[float] = None) -> str:
        """One bridge call; its stdout, or the code it refused with."""
        proc = self.session.run(
            [self._python, self.path(), mode, *extra],
            timeout=timeout or self.MODE_TIMEOUTS.get(mode, 15))
        if proc.returncode != 0:
            refusal = ControlError.from_line(proc.stderr or "")
            if refusal is not None:
                raise refusal
            raise ControlError(
                "atspi",
                f"atspi {mode} failed: "
                f"{(proc.stderr or '').strip() or (proc.stdout or '').strip()}")
        return (proc.stdout or "").strip()

    def index(self, app: Optional[str] = None,
              max_items: Optional[int] = None,
              snapshot: bool = True) -> list:
        """The element rows; ``index`` is the FULL walk's position.

        ``snapshot=False`` leaves the dump that ``click --el`` resolves
        against untouched, so a caller that reads for its own answer (the
        audit) cannot renumber another caller's elements.
        """
        extra: list = []
        if app:
            extra += ["--app", str(app)]
        if max_items is not None:
            extra += ["--max", str(int(max_items))]
        if not snapshot:
            extra += ["--no-save"]
        text = self.run("index", extra)
        try:
            rows = json.loads(text) if text else []
        except ValueError:
            raise ControlError("atspi", "a11y index returned no JSON")
        return rows if isinstance(rows, list) else []

    def enable(self) -> Outcome:
        """Turn the session's AT-SPI flag on, then read it back."""
        proc = self.session.run(
            ["busctl", "--user", "set-property", "org.a11y.Bus",
             "/org/a11y/bus", "org.a11y.Status", "IsEnabled", "b", "true"])
        if proc.returncode != 0:
            raise ControlError(
                "atspi", f"a11y-enable failed: {(proc.stderr or '').strip()}")
        check = self.session.run(
            ["busctl", "--user", "get-property", "org.a11y.Bus",
             "/org/a11y/bus", "org.a11y.Status", "IsEnabled"])
        got = (check.stdout or "").strip()
        if check.returncode != 0 or "true" not in got.lower():
            raise ControlError(
                "atspi",
                f"a11y-enable: IsEnabled is {got!r} after setting it true")
        return Outcome.confirmed("ax", IsEnabled=got)

    def click_element(self, index: int, button: str = "L") -> Outcome:
        """Invoke an element's AT-SPI action, or click its px fallback."""
        try:
            reply = json.loads(self.run("click", [str(int(index))]))
        except ValueError:
            raise ControlError("atspi",
                               f"click --el {index}: the bridge returned no "
                               "JSON")
        if not isinstance(reply, dict):
            raise ControlError("atspi", f"click --el {index}: bad reply")
        route = reply.get("route")
        if route == "ax":
            if str(button).upper() != "L":
                raise ControlError(
                    "bad-args",
                    f"click --el {index}: the AT-SPI route only invokes the "
                    f"element's default action — --button {button} is a px "
                    "fallback option")
            if not reply.get("ok"):
                raise ControlError(
                    "atspi",
                    f"click --el {index}: doAction({reply.get('action')!r}) "
                    "failed")
            return Outcome.unverifiable("ax", route="ax", element=int(index),
                                        action=reply.get("action"),
                                        role=reply.get("role"),
                                        name=reply.get("name"),
                                        button=str(button).upper())
        if route == "px-fallback":
            return self.click_fallback(index, reply, button)
        raise ControlError("atspi",
                           f"click --el {index}: unknown route {route!r}")

    def click_fallback(self, index: int, reply: dict,
                       button: str = "L") -> Outcome:
        """Click the element's reported SCREEN point, owner-checked.

        The bridge asks AT-SPI for screen coordinates, so the frame centre is
        already absolute: adding the window origin aimed the click at
        "window origin + element", which is not where the element is. The
        click is refused unless the point really falls inside the focused
        window, and unless the element's process IS that window's process.
        """
        upper = str(button).upper()
        code = KeyMap.BUTTONS.get(upper)
        if code is None:
            raise ControlError("bad-args",
                               f"click: bad button {button!r} "
                               "(expected L, R or M)")
        app = str(reply.get("app") or "")
        focused = self.desktop.active()
        focused_class = focused.cls if focused else ""
        if focused is None or not self.owner_matches(app, focused_class):
            raise ControlError(
                "element-owner-mismatch",
                f"click --el {index}: the element belongs to "
                f"{app or 'an unnamed app'} but the focused window is "
                f"{focused_class or 'unnamed'} — focus the app, re-dump with "
                "`a11y`, or click by pixels")
        x = int(reply.get("x", 0))
        y = int(reply.get("y", 0))
        element_pid = int(reply.get("pid") or 0)
        if element_pid and focused.pid and element_pid != focused.pid:
            raise ControlError(
                "element-owner-mismatch",
                f"click --el {index}: the element belongs to pid "
                f"{element_pid} but the focused window is pid {focused.pid} "
                "— the click would land on another window's pixels")
        if not (focused.x <= x < focused.x + focused.w
                and focused.y <= y < focused.y + focused.h):
            raise ControlError(
                "element-owner-mismatch",
                f"click --el {index}: the element reports screen point "
                f"({x}, {y}), outside the focused "
                f"{focused.cls or 'window'} at ({focused.x}, {focused.y}) "
                f"{focused.w}x{focused.h} — a frame outside its own window "
                "is not a target")
        self.input.warp(x, y)
        self.input.pointer.button(code)
        return Outcome.unverifiable("px", route="px-fallback", element=int(index),
                                    x=x, y=y, role=reply.get("role"),
                                    name=reply.get("name"), pid=element_pid,
                                    button=upper)

    @staticmethod
    def owner_matches(app: str, window_class: str) -> bool:
        """Does the AT-SPI application plausibly own the focused window?

        Both sides are normalized to bare alphanumerics and matched as
        substrings either way, because the spellings disagree by design
        ("org.gnome.Nautilus" vs "nautilus", "Chromium" vs "chromium").
        """
        def normalize(value: str) -> str:
            return "".join(ch for ch in str(value).lower() if ch.isalnum())

        left, right = normalize(app), normalize(window_class)
        return bool(left) and bool(right) and (left in right or right in left)
