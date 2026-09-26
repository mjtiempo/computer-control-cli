"""Applications: what a name IS, and how to open it — verified.

A desktop entry is resolved by exact name/id first, then a unique substring;
several candidates refuse. Opening dispatches one exec and then WAITS for the
window that appeared, because a launcher that raised an existing window is
not a launch to report as done. Placement on a workspace is a verified
``move_window``, never a guess.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import time
from typing import Any, Optional

from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.models import Window  # pi-lens-ignore: reportMissingImports
from computer_control_cli.outcome import Outcome  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

DEFAULT_ENTRY_DIRS = ("/usr/share/applications",
                      "~/.local/share/applications")
FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")
POLL_DELAY = 0.05
TERMINAL_CANDIDATES = ("foot", "ghostty", "alacritty", "kitty", "wezterm",
                       "ptyxis", "kgx", "xterm")


class DesktopEntry:
    """One ``.desktop`` file, parsed."""

    def __init__(self, name: str, command: str, terminal: bool,
                 entry_id: str, path: str = "", wm_class: str = ""):
        self.name = name
        self.command = command
        self.terminal = terminal
        self.entry_id = entry_id
        self.path = path
        self.wm_class = wm_class

    @classmethod
    def from_text(cls, text: str, entry_id: str,
                  path: str = "") -> Optional["DesktopEntry"]:
        fields: dict[str, str] = {}
        in_entry = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_entry = stripped == "[Desktop Entry]"
                continue
            if not in_entry or "=" not in stripped or stripped.startswith("#"):
                continue
            key, _, value = stripped.partition("=")
            fields.setdefault(key.strip(), value.strip())
        name = fields.get("Name", "")
        command = FIELD_CODES.sub("", fields.get("Exec", "")).strip()
        if not name or not command:
            return None
        if fields.get("NoDisplay", "").lower() == "true" \
                or fields.get("Hidden", "").lower() == "true":
            return None
        return cls(name=name, command=command,
                   terminal=fields.get("Terminal", "").lower() == "true",
                   entry_id=entry_id, path=path,
                   wm_class=fields.get("StartupWMClass", ""))

    @classmethod
    def from_file(cls, path: str) -> Optional["DesktopEntry"]:
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return None
        entry_id = os.path.basename(path)
        if entry_id.endswith(".desktop"):
            entry_id = entry_id[:-len(".desktop")]
        return cls.from_text(text, entry_id, path)

    def argv(self) -> list[str]:
        try:
            return shlex.split(self.command)
        except ValueError:
            return []

    def matches(self, needle: str) -> bool:
        lowered = needle.lower()
        return lowered in self.name.lower() or lowered in self.entry_id.lower()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "id": self.entry_id,
                "command": self.command, "terminal": self.terminal,
                "wm_class": self.wm_class or None}


class Apps:
    """Resolution, launching and the app-adjacent verbs."""

    def __init__(self, session: Session, desktop: Optional[Desktop] = None,
                 entry_dirs: Optional[list] = None,
                 path: Optional[str] = None,
                 terminal_launcher: Optional[str] = None):
        self.session = session
        self.desktop = desktop or Desktop(session)
        self.entry_dirs = list(entry_dirs) if entry_dirs is not None \
            else list(DEFAULT_ENTRY_DIRS)
        self._path = path if path is not None else os.environ.get("PATH", "")
        self.terminal_launcher = terminal_launcher

    def detect_terminal(self) -> Optional[str]:
        """The terminal to run a ``Terminal=true`` program inside."""
        if self.terminal_launcher:
            return self.terminal_launcher
        for candidate in TERMINAL_CANDIDATES:
            if shutil.which(candidate, path=self._path):
                return candidate
        return None

    # --------------------------------------------------------------- entry
    def entries(self) -> list[DesktopEntry]:
        """Every visible desktop entry, user dirs winning over system ones."""
        seen: dict[str, DesktopEntry] = {}
        for directory in self.entry_dirs:
            expanded = os.path.expanduser(directory)
            try:
                names = sorted(os.listdir(expanded))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".desktop"):
                    continue
                entry = DesktopEntry.from_file(os.path.join(expanded, name))
                if entry is not None and entry.entry_id not in seen:
                    seen[entry.entry_id] = entry
        return list(seen.values())

    def list(self) -> list[dict]:
        return [entry.to_dict() for entry in self.entries()]

    def resolve(self, name: str) -> DesktopEntry:
        """Exact name/id, else a unique substring; nothing or several refuses."""
        query = str(name).strip().lower()
        if not query:
            raise ControlError("bad-args", "app: empty name")
        entries = self.entries()
        exact = [entry for entry in entries
                 if entry.name.lower() == query or entry.entry_id.lower() == query]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise self._ambiguous(query, exact)
        partial = [entry for entry in entries if entry.matches(query)]
        if len(partial) == 1:
            return partial[0]
        if partial:
            raise self._ambiguous(query, partial)
        raise ControlError("app-unknown", f"app: nothing matches {name!r}")

    @staticmethod
    def _ambiguous(query: str, entries: list[DesktopEntry]) -> ControlError:
        names = ", ".join(entry.name for entry in entries[:4])
        return ControlError("app-ambiguous",
                            f"app: {len(entries)} entries match {query!r} "
                            f"({names})")

    def which(self, name: str) -> str:
        """Where a program resolves on PATH, or a refusal."""
        found = shutil.which(name, path=self._path)
        if not found:
            raise ControlError("not-found", f"which: {name!r} is not on PATH")
        return found

    # -------------------------------------------------------------- launch
    def build_command(self, entry: DesktopEntry, cwd: str = "") -> list[str]:
        """The argv to exec, wrapping a terminal program in a terminal."""
        argv = entry.argv()
        if not argv:
            raise ControlError("app-open-failed",
                               f"app: {entry.name!r} has no Exec command")
        if not entry.terminal:
            return argv
        launcher = self.detect_terminal()
        if launcher is None:
            raise ControlError(
                "not-found",
                f"app-open: {entry.name!r} needs a terminal, and none of "
                f"{', '.join(TERMINAL_CANDIDATES)} is on PATH")
        if not cwd:
            return [launcher, "-e", *argv]
        # the directory is part of the command, not a launcher-specific flag,
        # so every terminal in the candidate list behaves the same way
        script = "cd " + shlex.quote(cwd) + " && exec " \
            + " ".join(shlex.quote(word) for word in argv)
        return [launcher, "-e", "sh", "-c", script]

    def open(self, name: str, workspace: int = 0, cwd: str = "",
             timeout: float = 15.0, dry_run: bool = False) -> Any:
        """Open an app, wait for its window, and place it when asked."""
        entry = self.resolve(name)
        if cwd:
            expanded = os.path.expanduser(cwd)
            if not os.path.isdir(expanded):
                raise ControlError(
                    "cwd-missing",
                    f"app-open: {cwd!r} does not exist — create it first "
                    "(dir --create)")
            if not entry.terminal:
                raise ControlError(
                    "cwd-unsupported",
                    f"app-open: {entry.name!r} is not a terminal program, so "
                    "it cannot start in a directory")
            cwd = expanded
        argv = self.build_command(entry, cwd)
        command_line = " ".join(shlex.quote(word) for word in argv)
        if dry_run:
            return {"dry_run": True, "name": entry.name, "argv": argv,
                    "command": command_line, "terminal": entry.terminal,
                    "workspace": int(workspace)}
        before = {window.address.lower() for window in self.desktop.windows()}
        # the command line is QUOTED for the shell and the literal for Lua:
        # hyprctl evaluates the dispatch as Lua, and exec_cmd re-parses a
        # shell line, so both layers get their own quoting.
        self.session.dispatch(
            f"hl.dsp.exec_cmd({Session.lua_string(command_line)})")
        window = self.wait_for_window(before, self.needles(entry, argv),
                                      timeout, entry.name)
        placed = 0
        if workspace and window.workspace != int(workspace):
            self.desktop.move_window(f"address:{window.address}", workspace)
            window = self.find_address(window.address) or window
            placed = int(workspace)
        result = Outcome.confirmed("app", window=window.brief(),
                                   route="terminal" if entry.terminal
                                   else "desktop")
        if placed:
            result.details["workspace"] = placed
        return result

    def launch(self, argv: list[str], wait: bool = False,
               timeout: float = 15.0) -> Outcome:
        """Start a program from an explicit argv, optionally waiting for it."""
        if not argv:
            raise ControlError("bad-args", "launch: a program is required")
        command_line = " ".join(shlex.quote(str(word)) for word in argv)
        before = {window.address.lower() for window in self.desktop.windows()}
        self.session.dispatch(
            f"hl.dsp.exec_cmd({Session.lua_string(command_line)})")
        if not wait:
            return Outcome.unverifiable("app", command=command_line)
        needle = os.path.basename(str(argv[0])).lower()
        window = self.wait_for_window(before, [needle], timeout, argv[0])
        return Outcome.confirmed("app", window=window.brief(),
                                 command=command_line)

    @staticmethod
    def needles(entry: DesktopEntry, argv: list[str]) -> list[str]:
        """Names a new window for this app is expected to carry."""
        candidates = [entry.name.lower(), entry.wm_class.lower(),
                      os.path.basename(argv[0]).lower() if argv else ""]
        return [needle for needle in candidates if needle]

    def wait_for_window(self, before: set, needles: list[str],
                        timeout: float, name: str) -> Window:
        """The one window that appeared, or a refusal."""
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            fresh = [window for window in self.desktop.windows()
                     if window.address.lower() not in before]
            matched = [window for window in fresh
                       if any(needle in window.cls.lower()
                              or needle in window.title.lower()
                              for needle in needles)]
            if len(matched) == 1:
                return matched[0]
            if len(matched) > 1:
                names = ", ".join(f"{w.cls} {w.address}" for w in matched[:4])
                raise ControlError(
                    "ambiguous-window",
                    f"app-open: {len(matched)} windows appeared for {name!r} "
                    f"({names}) — refusing to pick one")
            if time.time() >= deadline:
                raise ControlError(
                    "app-open-failed",
                    f"app-open: {name!r} opened no window within {timeout:g}s")
            time.sleep(POLL_DELAY)

    def find_address(self, address: str) -> Optional[Window]:
        for window in self.desktop.windows():
            if window.address.lower() == address.lower():
                return window
        return None

    # ------------------------------------------------------------------ fs
    def dir(self, path: str, create: bool = False) -> dict:
        """Does the directory exist? ``--create`` makes it, and says which."""
        expanded = os.path.expanduser(str(path))
        if os.path.isdir(expanded):
            return {"path": expanded, "existed": True, "created": False}
        if os.path.exists(expanded):
            raise ControlError("not-a-dir",
                               f"dir: {path!r} exists and is not a directory")
        if not create:
            return {"path": expanded, "existed": False, "created": False}
        try:
            os.makedirs(expanded, exist_ok=True)
        except OSError as error:
            raise ControlError("create-failed",
                               f"dir: cannot create {path!r}: {error}")
        if not os.path.isdir(expanded):
            raise ControlError("create-not-verified",
                               f"dir: {path!r} did not appear after creation")
        return {"path": expanded, "existed": False, "created": True}
