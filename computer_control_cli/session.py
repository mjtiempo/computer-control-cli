"""The desktop session: environment resolution and hyprctl access.

``Session`` is the one place that knows how to reach the live Hyprland
instance (runtime dir, instance signature, Wayland display, ydotool socket)
and the one place that runs ``hyprctl``. ``ProcessRunner`` is the single
process boundary, so tests can inject a double and drive the whole stack
without a desktop.
"""

from __future__ import annotations

import glob
import json
import os
import stat
import subprocess
from typing import Any, Mapping, Optional, Protocol, Union

from computer_control_cli.coerce import Coerce  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.models import Monitor  # pi-lens-ignore: reportMissingImports

NO_SESSION = ("no live Hyprland socket under /run/user/*/hypr/* "
              "(run as the desktop user in their session)")


class Runner(Protocol):
    """Anything that can run a command and capture it."""

    def run(self, argv, env: Optional[Mapping[str, str]] = None,
            timeout: float = 15, binary: bool = False,
            input: Optional[Union[str, bytes]] = None
            ) -> subprocess.CompletedProcess: ...

    def spawn(self, argv, env: Optional[Mapping[str, str]] = None,
              input: Optional[Union[str, bytes]] = None,
              binary: bool = False) -> int: ...


class Discoverer(Protocol):
    """Anything that can resolve the session environment."""

    def discover(self) -> Optional[dict]: ...


class ProcessRunner:
    """Run a command and capture it; the single process boundary."""

    def run(self, argv, env: Optional[Mapping[str, str]] = None,
            timeout: float = 15, binary: bool = False,
            input: Optional[Union[str, bytes]] = None
            ) -> subprocess.CompletedProcess:
        """Run a command and capture it.

        Text mode decodes with ``errors="replace"``: a selection that holds
        image bytes is then replacement characters, never a UnicodeDecodeError
        traceback out of a read.
        """
        common = {"capture_output": True, "input": input, "timeout": timeout}
        if binary:
            return subprocess.run(list(argv), env=dict(env) if env else None,
                                  text=False, **common)
        return subprocess.run(list(argv), env=dict(env) if env else None,
                              text=True, errors="replace", **common)

    def spawn(self, argv, env: Optional[Mapping[str, str]] = None,
              input: Optional[Union[str, bytes]] = None,
              binary: bool = False) -> int:
        """Start a command that outlives the call (a clipboard holder) and
        return its pid. Pipes are /dev/null: a forked child must not hold a
        capture pipe open, or waiting for it would hang.
        """
        proc = subprocess.Popen(
            list(argv), env=dict(env) if env else None,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=not binary, start_new_session=True)
        if input is not None and proc.stdin is not None:
            try:
                proc.stdin.write(input)
                proc.stdin.close()
            except OSError:
                pass
        return proc.pid


class SessionDiscoverer:
    """Find the live Hyprland instance and build the env its clients need.

    Discovery is deliberately generous: it runs from a bare shell with no
    session variables, so it looks under ``/run/user/*/hypr/*`` and picks the
    newest live socket.
    """

    def __init__(self, environ: Optional[Mapping[str, str]] = None,
                 root: str = "/run/user", runner: Optional[Runner] = None):
        self._environ = dict(os.environ if environ is None else environ)
        self._root = root
        self._runner = runner or ProcessRunner()

    @staticmethod
    def is_live_socket(path: str) -> bool:
        """A socket owned by root must never receive the keystroke stream."""
        try:
            status = os.stat(path)
        except OSError:
            return False
        return stat.S_ISSOCK(status.st_mode)

    @staticmethod
    def root_socket(path: str) -> bool:
        try:
            status = os.stat(path)
        except OSError:
            return False
        return stat.S_ISSOCK(status.st_mode) and status.st_uid == 0

    def discover(self) -> Optional[dict]:
        candidates = [directory for directory in
                      glob.glob(os.path.join(self._root, "*", "hypr", "*"))
                      if self.is_live_socket(os.path.join(directory,
                                                          ".socket.sock"))]
        signature = self._environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if signature:
            candidates = [d for d in candidates if signature in d]
        if not candidates:
            return None
        candidates.sort(key=self._age_key, reverse=True)
        directory = candidates[0]
        return self._environment(directory)

    @staticmethod
    def _age_key(directory: str) -> tuple:
        try:
            return (os.path.getmtime(os.path.join(directory, ".socket.sock")),
                    directory)
        except OSError:
            return (0.0, directory)

    def _environment(self, directory: str) -> dict:
        signature = os.path.basename(directory)
        uid = directory.split(os.sep)[3]
        env = dict(self._environ)
        env.update(
            XDG_RUNTIME_DIR=f"/run/user/{uid}",
            HYPRLAND_INSTANCE_SIGNATURE=signature,
            YDOTOOL_SOCKET=Session.ydotool_socket(uid, self._environ),
        )
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
        display = env.get("WAYLAND_DISPLAY", "")
        if "wayland-" not in display:
            socket = self._wayland_socket(env, signature, uid)
            if socket:
                env["WAYLAND_DISPLAY"] = socket
        return env

    def _wayland_socket(self, env: dict, signature: str, uid: str) -> str:
        """Ask the compositor which Wayland socket belongs to this instance."""
        try:
            reply = self._runner.run(["hyprctl", "-j", "instances"],
                                     env=env, timeout=5).stdout
            instances = json.loads(reply)
        except (ControlError, ValueError, OSError):
            return ""
        if not isinstance(instances, list):
            return ""
        mine = [row for row in instances if isinstance(row, dict)
                and str(row.get("instance") or "") == signature]
        if len(mine) == 1:
            return str(mine[0].get("wl_socket") or "")
        if len(instances) == 1:
            return str(instances[0].get("wl_socket") or "")
        return ""


class Session:
    """A live (or injected) desktop session on this machine."""

    def __init__(self, env: Optional[Mapping[str, str]] = None,
                 runner: Optional[Runner] = None,
                 discoverer: Optional[Discoverer] = None):
        self._env = dict(env) if env is not None else None
        self._runner: Runner = runner or ProcessRunner()
        self._discoverer: Discoverer = discoverer or SessionDiscoverer()

    @staticmethod
    def ydotool_socket(uid: str,
                       environ: Optional[Mapping[str, str]] = None) -> str:
        """The ydotool daemon socket: env override, runtime dir, else /tmp.

        The ``/tmp`` candidate is accepted only as a socket owned by root —
        the path is predictable in a world-writable directory.
        """
        source = dict(os.environ if environ is None else environ)
        if source.get("YDOTOOL_SOCKET"):
            return source["YDOTOOL_SOCKET"]
        runtime = f"/run/user/{uid}/.ydotool_socket"
        if os.path.exists(runtime):
            return runtime
        if SessionDiscoverer.root_socket("/tmp/.ydotool_socket"):
            return "/tmp/.ydotool_socket"
        return runtime

    # ------------------------------------------------------------- resolve
    def with_env(self, updates: Mapping[str, str]) -> "Session":
        """A session like this one with environment overrides.

        The same runner and discoverer are kept, so a caller can target an
        instance or display without losing the process boundary it injected.
        An instance with no live socket refuses here, as `no-session`: a
        target that is not there is a missing session, not a hyprctl failure.
        """
        base = dict(self.resolved_env() or {})
        base.update(updates)
        signature = str(base.get("HYPRLAND_INSTANCE_SIGNATURE") or "")
        runtime = str(base.get("XDG_RUNTIME_DIR") or "")
        if signature and runtime and not os.path.exists(
                os.path.join(runtime, "hypr", signature, ".socket.sock")):
            raise ControlError(
                "no-session",
                f"no live Hyprland socket for instance {signature!r} under "
                f"{runtime}/hypr")
        return Session(env=base, runner=self._runner,
                       discoverer=self._discoverer)

    def resolved_env(self) -> Optional[dict]:
        if self._env is None:
            self._env = self._discoverer.discover()
        return self._env

    def env(self) -> dict:
        env = self.resolved_env()
        if env is None:
            raise ControlError("no-session", NO_SESSION)
        return env

    @staticmethod
    def lua_string(value: str) -> str:
        """A double-quoted Lua string literal for a caller-built dispatch.

        hyprctl evaluates the expression as Lua, so a value spliced between
        quotes is code; every caller-supplied value goes through here.
        """
        escaped = (str(value).replace("\\", "\\\\").replace('"', '\\"')
                   .replace("\n", "\\n").replace("\r", "\\r"))
        return f'"{escaped}"'

    # ---------------------------------------------------------------- run
    def spawn(self, argv, input: Optional[Union[str, bytes]] = None,
              binary: bool = False) -> int:
        """Start a command that outlives this call, with the session env."""
        try:
            return self._runner.spawn(list(argv), env=self.env(), input=input,
                                      binary=binary)
        except FileNotFoundError:
            raise ControlError("not-found", f"{argv[0]}: not found on PATH")
        except OSError as error:
            raise ControlError("subprocess-failed", f"{argv[0]}: {error}")

    def run(self, argv, timeout: float = 15, binary: bool = False,
            input: Optional[Union[str, bytes]] = None
            ) -> subprocess.CompletedProcess:
        """Run a command in the session environment, translating the ways a
        child can fail to answer into coded refusals."""
        try:
            return self._runner.run(list(argv), env=self.env(),
                                    timeout=timeout, binary=binary,
                                    input=input)
        except subprocess.TimeoutExpired:
            raise ControlError("subprocess-timeout",
                               f"{argv[0]}: did not finish within "
                               f"{timeout:g}s")
        except FileNotFoundError:
            raise ControlError("not-found", f"{argv[0]}: not found on PATH")
        except OSError as error:
            raise ControlError("subprocess-failed", f"{argv[0]}: {error}")

    def hypr(self, args, timeout: float = 15) -> str:
        proc = self.run(["hyprctl", *args], timeout)
        if proc.returncode != 0:
            raise ControlError(
                "hyprctl",
                f"hyprctl {' '.join(args)}: {(proc.stderr or '').strip()} "
                f"({(proc.stdout or '').strip()})")
        return proc.stdout

    def hypr_json(self, args, timeout: float = 15) -> Any:
        output = self.hypr(args, timeout)
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise ControlError(
                "hyprctl", f"hyprctl {' '.join(args)}: bad JSON reply "
                f"({error}): {output[:200]}")

    def dispatch(self, lua_expression: str) -> None:
        """Dispatch one Lua expression; the caller re-reads the effect.

        Hyprland answers ``ok`` to dispatches it did not apply, so this only
        reports the dispatch's own answer.
        """
        proc = self.run(["hyprctl", "dispatch", lua_expression])
        if proc.returncode != 0 or "ok" not in (proc.stdout or "").lower():
            raise ControlError(
                "dispatch",
                f"dispatch {lua_expression!r} failed: "
                f"{(proc.stdout or '').strip()} {(proc.stderr or '').strip()}")

    # -------------------------------------------------------------- reads
    def monitors(self) -> list[Monitor]:
        raw = self.hypr_json(["-j", "monitors"])
        rows = raw if isinstance(raw, list) else []
        return [monitor for monitor in (Monitor.from_hypr(row) for row in rows)
                if monitor is not None]

    def cursor(self) -> tuple[int, int]:
        parts = self.hypr(["cursorpos"]).strip().split(",")
        if len(parts) < 2:
            raise ControlError("hyprctl",
                               "hyprctl cursorpos: expected 'x,y', got "
                               f"{','.join(parts)[:60]!r}")
        return Coerce.as_int(parts[0], 0), Coerce.as_int(parts[1], 0)

    def desktop_bbox(self) -> tuple[int, int, int, int]:
        """The union of the monitor rects the pointer can be inside."""
        monitors = self.monitors()
        if not monitors:
            return 0, 0, 0, 0
        left = min(m.x for m in monitors)
        top = min(m.y for m in monitors)
        right = max(m.x + m.w for m in monitors)
        bottom = max(m.y + m.h for m in monitors)
        return left, top, right - left, bottom - top

    def pointer_reach(self) -> tuple[int, int, int, int]:
        """The rect the absolute pointer mapping can land in (origin monitor)."""
        anchor = self.anchor_monitor()
        if anchor is None or anchor.w <= 0 or anchor.h <= 0:
            return 0, 0, 0, 0
        return 0, 0, anchor.w, anchor.h

    def anchor_monitor(self) -> Optional[Monitor]:
        """The monitor the pointer device is sized from: origin, else focused."""
        monitors = self.monitors()
        for monitor in monitors:
            if monitor.contains(0, 0):
                return monitor
        for monitor in monitors:
            if monitor.focused:
                return monitor
        return monitors[0] if monitors else None
