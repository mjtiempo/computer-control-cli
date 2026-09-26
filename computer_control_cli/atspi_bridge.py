#!/usr/bin/env python3
"""ccc_atspi — the AT-SPI bridge for computer-control-cli.

Run as a program (``python3 atspi_bridge.py index|click …``), never imported
for its side effects: PyGObject lives in the desktop's own Python, and the
tool side only ever sees this program's JSON on stdout and its
``ERR[code]: message`` refusals on stderr.

Modes:
  index [--app SUBSTR] [--max N]   walk the tree, print JSON rows
  click N                          re-locate row N in a FRESH walk and invoke
                                   its first AT-SPI action; with no action,
                                   print a window-relative px fallback

The dump's row identities are snapshotted, so ``click`` re-locates its
element by identity rather than by position: a live tree that shifted must
not send the click to a neighbour. A gone element refuses
``ERR[stale-tree]``, several nodes sharing one identity refuse
``ERR[ambiguous-element]`` — both exit 3, and the caller branches on which.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional


class TreeSnapshot:
    """The dump's identities on disk: save, load, re-locate."""

    FILENAME = "ccc_tree_fp"

    @classmethod
    def path(cls) -> str:
        """The snapshot path, under the per-user runtime dir when there is one.

        ``/tmp/ccc_tree_fp_<uid>`` is predictable in a world-writable place,
        so the runtime dir (0700) is preferred; the fallback file is written
        0600 and refused if it is not ours.
        """
        xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if xdg and os.path.isdir(xdg):
            return os.path.join(xdg, cls.FILENAME)
        return f"/tmp/{cls.FILENAME}_{os.getuid()}"

    @staticmethod
    def identity(row: dict) -> tuple:
        """The stable identity of one dump row: (role, name, actions)."""
        return (row.get("role", ""), row.get("name", ""),
                tuple(row.get("actions") or []))

    @classmethod
    def save(cls, rows: list[dict], path: Optional[str] = None) -> bool:
        target = path or cls.path()
        try:
            fd = os.open(target,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o600)
            if os.fstat(fd).st_uid != os.getuid():
                os.close(fd)
                return False
            with os.fdopen(fd, "w") as handle:
                json.dump([{"role": row.get("role"), "name": row.get("name"),
                            "actions": row.get("actions")} for row in rows],
                          handle)
            return True
        except OSError:
            return False

    @classmethod
    def load(cls, path: Optional[str] = None) -> Optional[list]:
        target = path or cls.path()
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return None
        return stored if isinstance(stored, list) else None

    @classmethod
    def hits(cls, rows: list[dict], target: tuple) -> list[int]:
        """Every row index whose identity equals ``target``."""
        return [index for index, row in enumerate(rows)
                if cls.identity(row) == target]

    @classmethod
    def target(cls, stored: list, index: int) -> Optional[tuple]:
        if index < 0 or index >= len(stored):
            return None
        return cls.identity(stored[index])


class TreeWalker:
    """Walks the live AT-SPI tree; the only part that needs PyGObject."""

    INTERESTING = {
        "push button", "toggle button", "check box", "radio button",
        "combo box", "text", "text entry", "document", "web", "link",
        "menu item", "tab", "slider", "spin button", "switch", "list item",
        "tree item", "table", "column header", "row header", "scroll bar",
        "icon", "heading", "label",
    }
    _ATSPI: Any = None

    @classmethod
    def atspi(cls):
        """The Atspi module, imported once (PyGObject lives on the target)."""
        if cls._ATSPI is None:
            import warnings
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            import gi  # type: ignore[import-not-found]
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # type: ignore[import-not-found]
            cls._ATSPI = Atspi
        return cls._ATSPI

    @classmethod
    def describe(cls, accessible) -> tuple:
        """(role, name, value, actions) — the dump and click walks share it."""
        Atspi = cls.atspi()
        name = (accessible.get_name() or "").strip()
        role = accessible.get_role_name() or ""
        value = ""
        try:
            interface = accessible.get_value_iface()
            if interface is not None:
                value = str(interface.get_current_value()).strip()
        except Exception:
            pass
        actions = []
        try:
            actions = [accessible.get_action_name(i)
                       for i in range(accessible.get_n_actions())]
        except Exception:
            pass
        return role, name, value, actions

    @classmethod
    def included(cls, role: str, name: str, value: str,
                 actions: list) -> bool:
        """The one inclusion rule both walks use, so indices agree."""
        if role in ("application", "frame", "panel", "window"):
            return False
        return bool(actions) or role in cls.INTERESTING or bool(name) \
            or bool(value)

    @classmethod
    def walk(cls, app: str, parent, rows: list, max_rows: int,
             accs: Optional[list] = None) -> bool:
        Atspi = cls.atspi()
        for index in range(parent.get_child_count()):
            accessible = parent.get_child_at_index(index)
            if accessible is None:
                continue
            role, name, value, actions = cls.describe(accessible)
            enabled = selected = False
            try:
                state = accessible.get_state_set()
                if state:
                    enabled = state.contains(Atspi.StateType.ENABLED)
                    selected = state.contains(Atspi.StateType.SELECTED)
            except Exception:
                pass
            frame = None
            try:
                component = accessible.get_component_iface()
                if component:
                    rect = component.get_extents(Atspi.CoordType.SCREEN)
                    if rect.width > 0 and rect.height > 0:
                        frame = [int(rect.x), int(rect.y), int(rect.width),
                                 int(rect.height)]
            except Exception:
                pass
            if cls.included(role, name, value, actions):
                rows.append({"index": len(rows), "app": app, "role": role,
                             "name": name[:80], "value": value[:80],
                             "enabled": enabled, "selected": selected,
                             "actions": actions, "frame": frame})
                if accs is not None:
                    accs.append(accessible)
                if len(rows) >= max_rows:
                    return False
            if accessible.get_child_count() > 0 \
                    and not cls.walk(app, accessible, rows, max_rows, accs):
                return False
        return True

    @classmethod
    def walk_all(cls, max_rows: int, with_accs: bool = False) -> tuple:
        Atspi = cls.atspi()
        desktop = Atspi.get_desktop(0)
        rows: list = []
        accs: list = []
        for index in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(index)
            if app is None:
                continue
            cls.walk(app.get_name() or "?", app, rows, max_rows,
                     accs if with_accs else None)
            if len(rows) >= max_rows:
                break
        return rows, accs


class AtspiBridge:
    """argv in, JSON out, refusals as ``ERR[code]`` on stderr."""

    DEFAULT_MAX = 5000
    CLICK_MAX = 10_000_000

    def __init__(self, argv: list):
        self.argv = list(argv)

    @staticmethod
    def flag(args: list, name: str) -> Optional[str]:
        if name in args:
            position = args.index(name)
            if position + 1 < len(args):
                return args[position + 1]
        return None

    def index(self) -> int:
        app_filter = self.flag(self.argv, "--app")
        raw_max = self.flag(self.argv, "--max")
        try:
            max_rows = int(raw_max) if raw_max is not None else self.DEFAULT_MAX
        except ValueError:
            sys.stderr.write("--max needs an integer\n")
            return 1
        try:
            rows, _ = TreeWalker.walk_all(max_rows)
        except Exception as error:                       # noqa: BLE001
            sys.stderr.write(f"atspi walk failed: {error}\n")
            return 1
        if not TreeSnapshot.save(rows):
            sys.stderr.write("ERR[stale-tree]: cannot write the dump snapshot "
                             "— `click --el` could not resolve it\n")
            return 3
        if app_filter:
            needle = app_filter.lower()
            rows = [row for row in rows
                    if needle in str(row.get("app") or "").lower()]
        sys.stdout.write(json.dumps(rows[:max_rows]))
        sys.stdout.write("\n")
        return 0

    def click(self) -> int:
        if not self.argv:
            sys.stderr.write("click: element index required\n")
            return 1
        try:
            target = int(self.argv[0])
        except ValueError:
            sys.stderr.write(f"click: bad element index {self.argv[0]!r}\n")
            return 1
        stored = TreeSnapshot.load()
        if stored is None:
            sys.stderr.write("ERR[stale-tree]: no a11y dump yet "
                             "(run `a11y` first)\n")
            return 3
        wanted = TreeSnapshot.target(stored, target)
        if wanted is None:
            sys.stderr.write(f"click: element index {target} out of range "
                             f"({len(stored)} dump rows — re-run `a11y`)\n")
            return 1
        rows, accs = TreeWalker.walk_all(self.CLICK_MAX, with_accs=True)
        hits = TreeSnapshot.hits(rows, wanted)
        if not hits:
            sys.stderr.write(
                "ERR[stale-tree]: the dumped element is gone from the "
                "accessibility tree — re-run `a11y` and re-pick\n")
            return 3
        if len(hits) > 1:
            sys.stderr.write(
                f"ERR[ambiguous-element]: {len(hits)} nodes share "
                f"({wanted[0]!r}, {wanted[1]!r}) — re-dump and pick one with "
                "a unique name, or click by pixels\n")
            return 3
        row, accessible = rows[hits[0]], accs[hits[0]]
        if row["actions"]:
            try:
                ok = accessible.do_action(0)
            except Exception as error:                   # noqa: BLE001
                sys.stderr.write(f"ERR[atspi]: doAction failed: {error}\n")
                return 3
            TreeSnapshot.save(rows)
            sys.stdout.write(json.dumps({
                "ok": bool(ok), "route": "ax", "action": row["actions"][0],
                "role": row["role"], "name": row["name"]}))
            sys.stdout.write("\n")
            return 0
        if row["frame"]:
            x, y, w, h = row["frame"]
            sys.stdout.write(json.dumps({
                "ok": True, "route": "px-fallback",
                "x": x + w // 2, "y": y + h // 2, "app": row["app"],
                "role": row["role"], "name": row["name"]}))
            sys.stdout.write("\n")
            return 0
        sys.stderr.write(f"click: element {target} has no action and no "
                         "bounds\n")
        return 1

    @classmethod
    def main(cls, argv: list) -> int:
        mode = argv[0] if argv else ""
        bridge = cls(argv[1:])
        if mode == "index":
            return bridge.index()
        if mode == "click":
            return bridge.click()
        sys.stderr.write(__doc__ or "")
        return 1


if __name__ == "__main__":
    raise SystemExit(AtspiBridge.main(sys.argv[1:]))
