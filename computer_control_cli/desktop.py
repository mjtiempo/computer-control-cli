"""The desktop: windows, workspaces, monitors, and effect-verified actions.

Hyprland answers ``ok`` to a dispatch it did not apply, so every action here
re-reads the desktop before it reports success: ``switch`` reads the active
workspace back, ``focus`` reads the active window, ``close`` re-reads the
window list, and ``move_window`` waits until the window's workspace changed.
A read-back that does not see the effect refuses with a ``*-not-verified``
code.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from computer_control_cli.coerce import Coerce  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.models import Monitor, Window, WindowSpec, Workspace  # pi-lens-ignore: reportMissingImports
from computer_control_cli.outcome import Outcome  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

# How a dispatch is waited for, and how long: every loop re-reads, so the
# bound only decides when a dispatch that will never apply becomes a refusal.
POLL_TRIES = 20
POLL_DELAY = 0.02
CLOSE_SETTLE_SECONDS = 0.2
CLOSE_APP_ROUNDS = 3


class Desktop:
    """The compositor's windows and workspaces, read and acted on."""

    TERMINALS = ("foot", "footclient", "alacritty", "kitty", "ghostty",
                 "wezterm", "konsole", "gnome-terminal", "gnome.terminal",
                 "org.gnome.console", "org.gnome.terminal", "kgx",
                 "xfce4-terminal", "terminator", "tilix", "xterm", "urxvt",
                 "st", "tmux", "warp", "hyper", "contour", "tabby", "rio",
                 "blackbox", "ptyxis")
    BROWSERS = ("chromium", "chrome", "google-chrome", "brave",
                "microsoft-edge", "vivaldi", "firefox", "librewolf", "zen",
                "helium", "opera")
    _SEPARATOR = re.compile(r"[._:\s]+")

    def __init__(self, session: Session):
        self.session = session

    # ---------------------------------------------------------------- reads
    def clients(self) -> list[dict]:
        """hyprctl's client rows that ARE objects."""
        raw = self.session.hypr_json(["-j", "clients"])
        return Coerce.rows(raw)

    def windows(self) -> list[Window]:
        """The mapped windows, parsed."""
        return [window for window in
                (Window.from_hypr(row) for row in self.clients()
                 if row.get("mapped"))
                if window is not None]

    def active(self) -> Optional[Window]:
        """The focused window, or None when nothing is focused."""
        raw = self.session.hypr_json(["-j", "activewindow"])
        return Window.from_hypr(raw) if isinstance(raw, dict) else None

    def workspaces(self) -> list[Workspace]:
        raw = self.session.hypr_json(["-j", "workspaces"])
        rows = raw if isinstance(raw, list) else []
        return [workspace for workspace in
                (Workspace.from_hypr(row) for row in rows)
                if workspace is not None]

    def monitors(self) -> list[Monitor]:
        return self.session.monitors()

    def active_workspace_id(self) -> Optional[int]:
        """The focused workspace's id, or None when the reply carries none."""
        reply = self.session.hypr_json(["-j", "activeworkspace"])
        if not isinstance(reply, dict) or "id" not in reply:
            return None
        return Coerce.as_int(reply.get("id"))

    def state(self) -> dict[str, Any]:
        """One snapshot: workspaces, windows, the active window, the pointer."""
        pointer = self.session.cursor()
        return {
            "active_workspace": self.active_workspace_id(),
            "workspaces": self.workspaces(),
            "windows": self.windows(),
            "active": self.active(),
            "pointer": {"x": pointer[0], "y": pointer[1]},
        }

    # ----------------------------------------------------------- resolution
    def matching(self, spec: str, rows: Optional[list[Window]] = None) -> list[Window]:
        """Every window a target names (no refusal — callers decide)."""
        parsed = WindowSpec.parse(spec)
        return [window for window in (self.windows() if rows is None else rows)
                if parsed.matches(window)]

    def resolve(self, spec: str, action: str = "window") -> Window:
        """The one window a target names. None or many refuses; never a pick."""
        found = self.matching(spec)
        if not found:
            raise ControlError("window-not-found",
                               f"{action}: no window matching {spec!r}")
        if len(found) > 1:
            names = ", ".join(f"{w.cls} {w.address}" for w in found[:4])
            raise ControlError(
                "ambiguous-window",
                f"{action}: {len(found)} windows match {spec!r} ({names}) — "
                "use address:0x… to pick one")
        return found[0]

    @classmethod
    def class_matches(cls, window_class: str, names: tuple) -> bool:
        """True when a window class IS one of ``names`` or carries it as a word.

        A bare substring made ``steam`` a terminal (the ``st`` entry) and
        ``zenity`` a browser (the ``zen`` entry).
        """
        normalized = cls._normalize(window_class)
        if not normalized:
            return False
        for name in names:
            wanted = cls._normalize(name)
            if not wanted:
                continue
            if (normalized == wanted
                    or normalized.startswith(wanted + "-")
                    or normalized.endswith("-" + wanted)
                    or f"-{wanted}-" in normalized):
                return True
        return False

    @classmethod
    def kind_of(cls, window: Window) -> str:
        if cls.class_matches(window.cls, cls.TERMINALS):
            return "terminal"
        if cls.class_matches(window.cls, cls.BROWSERS):
            return "browser"
        return "other"

    @classmethod
    def select_kind(cls, windows: list[Window], kind: str = "terminal",
                    match: str = "", keep: str = "") -> list[Window]:
        """The windows of a kind, narrowed by ``match`` and spared by ``keep``."""
        needle = match.lower()
        spared = keep.lower()
        selected = []
        for window in windows:
            if kind != "all" and cls.kind_of(window) != kind:
                continue
            if needle and needle not in window.cls.lower() \
                    and needle not in window.title.lower():
                continue
            if spared and spared in window.cls.lower():
                continue
            selected.append(window)
        return selected

    @classmethod
    def _normalize(cls, value: str) -> str:
        return cls._SEPARATOR.sub("-", str(value or "").lower()).strip("-")

    # ------------------------------------------------------ window actions
    def switch(self, workspace: int,
               timeout: Optional[float] = None) -> Outcome:
        """Focus workspace N and prove the switch took."""
        self.session.dispatch(f"hl.dsp.focus({{ workspace = {int(workspace)} }})")
        seen = self._poll(lambda: self.active_workspace_id() == int(workspace),
                          timeout)
        if not seen:
            raise ControlError(
                "switch-not-verified",
                f"switch: asked for workspace {int(workspace)} but the active "
                f"workspace is {self.active_workspace_id()}")
        return Outcome.confirmed("dispatch", workspace=int(workspace))

    def focus(self, spec: str, timeout: Optional[float] = None) -> Outcome:
        """Set keyboard focus to one window and prove it took."""
        window = self.resolve(spec, "focus")
        self.session.dispatch(
            f'hl.dsp.focus({{ window = "address:{window.address}", '
            f'follow = true }})')
        seen = self._poll(
            lambda: self._active_address() == window.address.lower(), timeout)
        if not seen:
            current = self.active()
            raise ControlError(
                "focus-not-verified",
                f"focus: {spec!r} did not take focus — active is "
                f"{current.address if current else None!r}")
        return Outcome.confirmed("dispatch", address=window.address,
                                 **{"class": window.cls})

    def close(self, spec: Optional[str] = None,
              timeout: Optional[float] = None) -> Outcome:
        """Close a window and prove it is gone.

        With a target, close exactly that window; without one, close the
        FOCUSED window — its address is read first, so both forms have a
        read-back. An empty desktop has nothing to close and nothing to
        claim: it refuses instead of reporting a close that never happened.
        """
        window = self.resolve(spec, "close") if spec else self.active()
        address = window.address if window else ""
        if not address:
            raise ControlError(
                "empty-desktop",
                "close: no window is focused — the desktop is empty, so "
                "nothing was closed")
        self.session.dispatch(
            f'hl.dsp.window.close({{ window = "address:{address}" }})')
        seen = self._poll(lambda: address.lower() not in self._open_addresses(),
                          timeout)
        if not seen:
            raise ControlError(
                "close-not-verified",
                f"close: {spec or 'the focused window'} ({address}) is still "
                "open after the close dispatch")
        return Outcome.confirmed("dispatch", address=address,
                                 closed=(window.title or window.cls)
                                 if window else "focused window")

    def move_window(self, spec: str, workspace: Any,
                    timeout: Optional[float] = None) -> Outcome:
        """Move one window to a workspace and return once it is read back."""
        window = self.resolve(spec, "move-window")
        wanted = str(workspace).strip()
        self.session.dispatch(
            f'hl.dsp.window.move({{ window = "address:{window.address}", '
            f'workspace = {Session.lua_string(wanted)} }})')
        wanted_row = wanted.removeprefix("name:")
        landed: Optional[str] = None      # where the window was last seen

        def is_there() -> bool:
            nonlocal landed
            for row in self.clients():
                if str(row.get("address", "")).lower() != window.address.lower():
                    continue
                landed = self._workspace_label(row)
                here = row.get("workspace")
                here = here if isinstance(here, dict) else {}
                return (str(here.get("id")) == wanted_row
                        or str(here.get("name")) == wanted_row)
            landed = None
            return False

        seen = self._poll(is_there, timeout)
        if not seen:
            where = (f"it is on {landed}" if landed
                     else "it is no longer on the desktop")
            raise ControlError(
                "move-not-verified",
                f"move-window: {window.address} is not on workspace {wanted} "
                f"after the move dispatch — {where}")
        return Outcome.confirmed("dispatch", address=window.address,
                                 workspace=wanted)

    def close_apps(self, kind: str = "terminal", match: str = "",
                   keep: str = "", dry_run: bool = False,
                   rounds: int = CLOSE_APP_ROUNDS) -> Any:
        """Close every window of a kind, proving each one gone.

        Read the list, classify, close each by ADDRESS (a dispatcher named by
        address is not scoped to the focused workspace), then re-read and
        repeat. A window that survives every round is REPORTED.
        """
        selected = self.select_kind(self.windows(), kind, match, keep)
        if dry_run:
            return {"dry_run": True, "kind": kind, "match": match,
                    "keep": keep, "count": len(selected),
                    "windows": [window.brief() for window in selected]}
        if not selected:
            return Outcome.confirmed("dispatch", kind=kind, match=match,
                                     keep=keep, closed=[], remaining=[],
                                     rounds=0)

        closed: list[dict] = []
        spent = 0
        while spent < max(1, rounds):
            targets = self.select_kind(self.windows(), kind, match, keep)
            if not targets:
                break
            spent += 1
            pending = {window.address.lower() for window in targets}
            for window in targets:
                self.session.dispatch(
                    f'hl.dsp.window.close({{ window = "address:{window.address}" }})')
            deadline = time.time() + CLOSE_SETTLE_SECONDS
            while pending and time.time() < deadline:
                pending &= self._open_addresses()
                if pending:
                    time.sleep(POLL_DELAY)
            live = self._open_addresses()
            for window in targets:
                if window.address.lower() not in live:
                    closed.append(window.brief())

        remaining = [window.brief() for window in
                     self.select_kind(self.windows(), kind, match, keep)]
        if remaining:
            return Outcome.unverifiable("dispatch", ok=False, kind=kind,
                                        match=match, keep=keep,
                                        closed=closed, remaining=remaining,
                                        rounds=spent)
        return Outcome.confirmed("dispatch", kind=kind, match=match, keep=keep,
                                 closed=closed, remaining=[], rounds=spent)

    def wait(self, spec: str = "", gone: bool = False, timeout: float = 15.0,
             poll: float = 0.05, new_only: bool = False,
             exclude: Optional[set] = None, title: str = "",
             klass: str = "", stable: int = 1) -> Outcome:
        """Block until a matching window appears (or, with ``gone``, is gone).

        ``new_only`` snapshots the open addresses first, so "a new window" is
        one that did not exist when the wait began; ``exclude`` drops
        addresses a caller already accounted for. ``title``/``klass`` narrow
        the matches, and ``stable`` requires that many consecutive satisfied
        reads so a window caught mid-open is not reported as ready.
        """
        ignored = {str(address).lower() for address in (exclude or set())}
        baseline = self._open_addresses() if new_only else None
        needle_title = str(title).lower()
        needle_class = str(klass).lower()
        wanted = max(1, int(stable))
        streak = 0
        address = ""            # the address the streak is about
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            rows = self.matching(spec) if spec else self.windows()
            if baseline is not None:
                rows = [w for w in rows if w.address.lower() not in baseline]
            if ignored:
                rows = [w for w in rows if w.address.lower() not in ignored]
            if needle_title:
                rows = [w for w in rows if needle_title in w.title.lower()]
            if needle_class:
                rows = [w for w in rows if needle_class in w.cls.lower()]
            if spec and not gone and len(rows) > 1:
                raise ControlError(
                    "ambiguous-window",
                    f"wait-window: {len(rows)} windows match {spec!r} — "
                    "narrow it with --class/--title/--exclude")
            if gone:
                streak = streak + 1 if not rows else 0
            else:
                found = rows[0].address.lower() if rows else ""
                if found != address:
                    address, streak = found, 0
                streak = streak + 1 if found else 0
            if streak >= wanted:
                if gone:
                    return Outcome.confirmed("poll", gone=True, stable=streak)
                return Outcome.confirmed("poll", window=rows[0].brief(),
                                         stable=streak)
            if time.time() >= deadline:
                wanted_effect = ("is still open" if gone
                                 else ("did not appear" if not new_only
                                       else "did not appear (no new window)"))
                raise ControlError(
                    "wait-timeout",
                    f"wait-window: {spec or 'a window'!r} {wanted_effect} "
                    f"within {timeout:g}s")
            time.sleep(min(poll, max(0.0, deadline - time.time())))

    # ------------------------------------------------------------ internals
    def _active_address(self) -> str:
        window = self.active()
        return window.address.lower() if window else ""

    @staticmethod
    def _workspace_label(row: dict) -> str:
        """How a client row's workspace reads in a refusal."""
        here = row.get("workspace")
        here = here if isinstance(here, dict) else {}
        name = str(here.get("name") or "")
        number = str(here.get("id", ""))
        if not number and not name:
            return "an unnamed workspace"
        if name and name != number:
            return f"workspace {number or '?'} (name {name!r})"
        return f"workspace {number or name}"

    def _open_addresses(self) -> set:
        """The addresses of the MAPPED windows — the ones a user can see.

        A client row outlives the window: a close-to-tray app keeps an
        unmapped row, so reading back against every client row reports a
        close that DID happen as a failure and counts a hidden window as
        still open. This is the same set ``windows`` shows.
        """
        return {window.address.lower() for window in self.windows()}

    @staticmethod
    def _poll(condition, timeout: Optional[float] = None) -> bool:
        """Run ``condition`` until it holds, up to ``timeout`` seconds.

        The CLI passes the resolved ``--timeout``; a caller that passes
        nothing gets the built-in budget, so a service used directly still
        refuses promptly.
        """
        deadline = (None if timeout is None
                    else time.time() + max(0.0, float(timeout)))
        tries = 0
        while deadline is None or time.time() < deadline:
            if condition():
                return True
            tries += 1
            if deadline is None and tries >= POLL_TRIES:
                break
            time.sleep(POLL_DELAY)
        return bool(condition())
