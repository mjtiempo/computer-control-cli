"""Screen capture: PNG bytes, logical-size regions, opt-in saving.

``grim -s 1`` renders at logical size, so a capture is a 1:1 pixel map for
clicks. Saving is opt-in and prunes the oldest, because screenshots are
pictures of a real desktop: they are never the answer and must not accumulate
forever.
"""

from __future__ import annotations

import glob
import os
import re
import struct
import time
from typing import Optional

from computer_control_cli.coerce import Coerce  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.models import Window  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

DEFAULT_KEEP = 20


class Screen:
    """grim-backed captures: full, region, focused window."""

    CURSOR_FLAG = "-c"
    GRIM_ARGS = ["-s", "1"]  # render at logical size: a 1:1 map for clicks

    def __init__(self, session: Session):
        self.session = session

    def _grim(self, extra: list, no_cursor: bool = False) -> bytes:
        argv = (["grim"]
                + ([] if no_cursor else [self.CURSOR_FLAG])
                + self.GRIM_ARGS + extra + ["-"])
        proc = self.session.run(argv, binary=True)
        if proc.returncode != 0:
            raise ControlError("capture",
                               f"grim failed: {(proc.stderr or '').strip()}")
        return proc.stdout

    def full(self, no_cursor: bool = False) -> bytes:
        return self._grim([], no_cursor)

    def region(self, x: int, y: int, w: int, h: int,
               no_cursor: bool = False) -> bytes:
        return self._grim(["-g", f"{int(x)},{int(y)} {int(w)}x{int(h)}"],
                          no_cursor)

    def active(self, no_cursor: bool = False) -> bytes:
        """The focused window's rect, or a refusal when nothing is focused."""
        raw = self.session.hypr_json(["-j", "activewindow"])
        if not isinstance(raw, dict) or "at" not in raw or "size" not in raw:
            raise ControlError(
                "window-not-found",
                "active-screen: no active window (focus an app first)")
        x, y = Coerce.pair(raw.get("at"))
        w, h = Coerce.pair(raw.get("size"))
        if w <= 0 or h <= 0:
            raise ControlError(
                "window-not-found",
                "active-screen: the focused window reports no size")
        return self.region(x, y, w, h, no_cursor)

    def capture_window(self, window: Window,
                       no_cursor: bool = False) -> bytes:
        """The rect of one named window, resolved by the caller."""
        return self.region(window.x, window.y, window.w, window.h, no_cursor)

    def monitor(self, name: str, no_cursor: bool = False) -> bytes:
        """One monitor's whole logical rect, by name (exact, else unique)."""
        needle = str(name).strip().lower()
        monitors = self.session.monitors()
        exact = [m for m in monitors if m.name.lower() == needle]
        matches = exact or [m for m in monitors if needle in m.name.lower()]
        if not matches:
            names = ", ".join(m.name for m in monitors) or "none readable"
            raise ControlError(
                "monitor-not-found",
                f"screen: no monitor named {name!r} (have: {names})")
        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            raise ControlError(
                "ambiguous-monitor",
                f"screen: {len(matches)} monitors match {name!r} ({names})")
        monitor = matches[0]
        return self.region(monitor.x, monitor.y, monitor.w, monitor.h,
                           no_cursor)

    def save(self, data: bytes, out_dir: str, prefix: str = "screen",
             keep: int = DEFAULT_KEEP) -> dict:
        """Write a capture into ``out_dir`` and prune the oldest ones."""
        width, height = self.png_size(data)
        directory = os.path.expanduser(out_dir)
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            path = os.path.join(directory,
                                f"{prefix}_{int(time.time() * 1000)}.png")
            # A capture is a picture of the whole desktop: 0600, and never
            # through a symlink the caller did not mean to follow.
            fd = os.open(path,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        except OSError as error:
            raise ControlError(
                "capture", f"cannot save the capture under {directory}: {error}")
        self._prune(directory, prefix, keep)
        return {"path": path, "width": width, "height": height}

    @staticmethod
    def png_size(data: bytes) -> tuple[int, int]:
        """(width, height) from the PNG's IHDR — the only trustworthy source."""
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ControlError("capture", "capture is not a PNG")
        return struct.unpack(">II", data[16:24])

    @staticmethod
    def _prune(directory: str, prefix: str, keep: int) -> None:
        """Delete the oldest captures THIS tool wrote, and only those.

        The name is the only proof of ownership there is:
        ``{prefix}_<ms>.png``. A bare glob also matches a picture the user
        put in ``--out`` (``screen_desktop.png``, say) and deletes it.
        """
        mine = re.compile(rf"{re.escape(prefix)}_\d+\.png\Z")
        try:
            pattern = os.path.join(directory, f"{prefix}_*.png")
            files = sorted((path for path in glob.glob(pattern)
                            if mine.match(os.path.basename(path))),
                           key=lambda path: os.path.getmtime(path))
        except OSError:
            return
        for stale in files[:max(0, len(files) - max(1, int(keep)))]:
            try:
                os.unlink(stale)
            except OSError:
                pass
