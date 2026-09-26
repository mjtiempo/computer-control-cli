"""Test doubles shared by the hermetic suites.

``FakeRunner`` answers a canned stdout per argv. ``FakeHyprland`` is a
stateful hyprctl double: reads answer from its state, dispatches mutate it
when ``honor_dispatches`` is on (and are recorded either way), so an action's
read-back verification can be exercised without a desktop.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional


class FakeRunner:
    """A ``ProcessRunner`` double with canned replies per argv."""

    def __init__(self, responses: Optional[dict] = None,
                 returncodes: Optional[dict] = None):
        self.responses = dict(responses or {})
        self.returncodes = dict(returncodes or {})
        self.calls: list[list[str]] = []
        self.environments: list[dict] = []
        self.binary_flags: list[bool] = []
        self.inputs: list[Optional[str]] = []
        self.spawned: list[tuple[list[str], Optional[str]]] = []
        self.timeouts: set[str] = set()
        self.missing: set[str] = set()
        self.clipboard = ""

    def run(self, argv, env=None, timeout: float = 15, binary: bool = False,
            input: Optional[str] = None):
        key = " ".join(argv)
        self.calls.append(list(argv))
        self.environments.append(dict(env or {}))
        self.binary_flags.append(binary)
        self.inputs.append(input)
        if key in self.missing:
            raise FileNotFoundError(argv[0])
        if key in self.timeouts:
            raise subprocess.TimeoutExpired(argv, timeout)
        if key == "wl-paste --no-newline":
            stdout = (self.responses[key] if key in self.responses
                      else self.clipboard)
            return subprocess.CompletedProcess(
                argv, self.returncodes.get(key, 0), stdout=stdout, stderr="")
        if key in self.responses:
            return subprocess.CompletedProcess(
                argv, self.returncodes.get(key, 0),
                stdout=self.responses.get(key, ""), stderr="")
        return subprocess.CompletedProcess(
            argv, self.returncodes.get(key, 0), stdout="", stderr="")

    def spawn(self, argv, env=None, input: Optional[str] = None) -> int:
        key = " ".join(argv)
        self.calls.append(list(argv))
        self.spawned.append((list(argv), input))
        if key in self.missing:
            raise FileNotFoundError(argv[0])
        if argv[:1] == ["wl-copy"]:
            self.clipboard = input or ""
        return 4242


class FakeDiscoverer:
    """A ``SessionDiscoverer`` double with a fixed env (or none)."""

    def __init__(self, env: Optional[dict]):
        self._env = env

    def discover(self) -> Optional[dict]:
        return dict(self._env) if self._env is not None else None


class FakeHyprland:
    """A stateful desktop: hyprctl reads answer from it, dispatches change it."""

    def __init__(self, monitors: Optional[list] = None,
                 workspaces: Optional[list] = None,
                 clients: Optional[list] = None,
                 active_workspace: int = 1,
                 cursor: tuple[int, int] = (0, 0)):
        self.monitors = monitors if monitors is not None else [
            {"name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
             "scale": 1.0, "focused": True}]
        self.workspaces = workspaces if workspaces is not None else [
            {"id": 1, "name": "1", "monitor": "eDP-1", "windows": 1},
            {"id": 2, "name": "2", "monitor": "eDP-1", "windows": 0}]
        self.clients = clients if clients is not None else [
            {"address": "0xaaa1", "title": "foot", "class": "foot",
             "workspace": {"id": 1, "name": "1"}, "at": [100, 200],
             "size": [800, 600], "mapped": True, "fullscreen": 0,
             "floating": False, "pid": 11}]
        self.active_workspace = active_workspace
        self.active_address = self.clients[0]["address"] if self.clients else ""
        self.cursor = cursor
        self.honor_dispatches = True
        self.dispatch_log: list[str] = []
        self.spawn_on_exec: list[dict] = []
        self.calls: list[list[str]] = []

    # ------------------------------------------------------------- runner
    def spawn(self, argv, env=None, input: Optional[str] = None) -> int:
        self.calls.append(list(argv))
        return 4242

    def run(self, argv, env=None, timeout: float = 15, binary: bool = False,
            input=None):
        key = " ".join(argv)
        try:
            stdout = self._answer(argv)
            returncode = 0
        except LookupError:
            stdout, returncode = "", 1
        return subprocess.CompletedProcess(argv, returncode,
                                           stdout=stdout, stderr="")

    def _answer(self, argv) -> str:
        if argv[0] == "hyprctl":
            return self._hyprctl(argv[1:])
        raise LookupError(" ".join(argv))

    def _hyprctl(self, args) -> str:
        if args[:2] == ["-j", "monitors"]:
            return json.dumps(self.monitors)
        if args[:2] == ["-j", "workspaces"]:
            return json.dumps(self.workspaces)
        if args[:2] == ["-j", "clients"]:
            return json.dumps(self.clients)
        if args[:2] == ["-j", "activewindow"]:
            return json.dumps(self._active_client())
        if args[:2] == ["-j", "activeworkspace"]:
            row = next((w for w in self.workspaces
                        if w["id"] == self.active_workspace), None)
            return json.dumps(row or {})
        if args[:1] == ["cursorpos"]:
            return f"{self.cursor[0]},{self.cursor[1]}"
        if args[:1] == ["dispatch"]:
            return self._dispatch(args[1])
        raise LookupError("hyprctl " + " ".join(args))

    def _active_client(self) -> Optional[dict]:
        return next((c for c in self.clients
                     if c["address"] == self.active_address), None)

    def _dispatch(self, expression: str) -> str:
        self.dispatch_log.append(expression)
        if self.honor_dispatches:
            self._apply(expression)
        return "ok"

    def _apply(self, expression: str) -> None:
        workspace = re.search(r'workspace = "?([^",})]+)"?', expression)
        window = re.search(r'window = "address:(0x[0-9a-fA-F]+)"', expression)

        if "hl.dsp.exec_cmd" in expression:
            for client in self.spawn_on_exec:
                self.clients.append(dict(client))
            return
        if "hl.dsp.focus" in expression and "window" in expression and window:
            self.active_address = window.group(1)
            return
        if "hl.dsp.focus" in expression and workspace:
            self.active_workspace = int(workspace.group(1))
            return
        if "hl.dsp.window.close" in expression and window:
            self.clients = [c for c in self.clients
                            if c["address"] != window.group(1)]
            if self.active_address == window.group(1):
                self.active_address = ""
            return
        if "hl.dsp.window.move" in expression and window and workspace:
            for client in self.clients:
                if client["address"] == window.group(1):
                    client["workspace"] = {"id": int(workspace.group(1)),
                                           "name": workspace.group(1)}
            return


class FakeInputRunner:
    """hyprctl from a stateful desktop plus a cursor model for the devices.

    ``ydotool`` absolute/relative moves update a raw-unit cursor that
    ``hyprctl cursorpos`` reads back through the anchor monitor's scale, so a
    warp's verification is exercised for real. ``stubborn`` makes every move a
    no-op, which is the machine that answers ok and does not move.
    """

    ABS_X_MAX = 960
    ABS_Y_MAX = 540

    def __init__(self, hyprland: Optional[FakeHyprland] = None):
        self.hypr = hyprland or FakeHyprland()
        self.cursor_raw = (0.0, 0.0)
        self.calls: list[list[str]] = []
        self.stubborn = False
        self.responses: dict[str, object] = {}
        self.inputs: list[Optional[str]] = []
        self.spawned: list[tuple[list[str], Optional[str]]] = []
        self.clipboard = ""

    def scale(self) -> tuple[float, float]:
        anchor = self.hypr.monitors[0]
        width = float(anchor["width"]) / float(anchor.get("scale", 1.0) or 1.0)
        height = float(anchor["height"]) / float(anchor.get("scale", 1.0) or 1.0)
        return self.ABS_X_MAX / width, self.ABS_Y_MAX / height

    def cursorpos(self) -> str:
        sx, sy = self.scale()
        x = int(round(self.cursor_raw[0] / sx))
        y = int(round(self.cursor_raw[1] / sy))
        return f"{x},{y}"

    def run(self, argv, env=None, timeout: float = 15, binary: bool = False,
            input=None):
        self.calls.append(list(argv))
        self.inputs.append(input)
        key = " ".join(argv)
        if key in self.responses:
            return subprocess.CompletedProcess(argv, 0,
                                               stdout=self.responses[key],
                                               stderr="")
        if key == "wl-paste --no-newline":
            return subprocess.CompletedProcess(argv, 0,
                                               stdout=self.clipboard,
                                               stderr="")
        if argv[0] == "hyprctl":
            if argv[1:2] == ["cursorpos"]:
                return subprocess.CompletedProcess(argv, 0,
                                                   stdout=self.cursorpos(),
                                                   stderr="")
            return self.hypr.run(argv, env=env, timeout=timeout,
                                 binary=binary, input=input)
        if argv[:2] == ["ydotool", "mousemove"]:
            if not self.stubborn:
                dx = int(argv[argv.index("-x") + 1])
                dy = int(argv[argv.index("-y") + 1])
                if "--absolute" in argv:
                    self.cursor_raw = (float(dx), float(dy))
                else:
                    self.cursor_raw = (self.cursor_raw[0] + dx,
                                       self.cursor_raw[1] + dy)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0] in ("ydotool", "wtype", "python3", "wl-copy", "wl-paste"):
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unknown")

    def spawn(self, argv, env=None, input: Optional[str] = None) -> int:
        self.calls.append(list(argv))
        self.inputs.append(input)
        self.spawned.append((list(argv), input))
        if argv[:1] == ["wl-copy"]:
            self.clipboard = input or ""
        return 4242
