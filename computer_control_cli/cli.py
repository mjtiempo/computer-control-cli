"""The command line: one subcommand per verb, JSON on stdout, ERR[code] on
stderr with exit 1.

The parser is built in one place (:class:`ParserBuilder`) and the handler for
a verb is the matching ``cmd_<verb>`` method, so a new verb is added in two
places and nothing else. Services are constructed once and injected, so tests
run the whole command surface against a double.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Any, Optional

from computer_control_cli.apps import Apps  # pi-lens-ignore: reportMissingImports
from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input  # pi-lens-ignore: reportMissingImports
from computer_control_cli.log import ActionLog  # pi-lens-ignore: reportMissingImports
from computer_control_cli.outcome import Outcome  # pi-lens-ignore: reportMissingImports
from computer_control_cli.screen import Screen  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports
from computer_control_cli.system import SystemInfo  # pi-lens-ignore: reportMissingImports

PROG = "computer-control"
DESCRIPTION = "Drive a Hyprland desktop: reads, workspaces, apps, input."


class ParserBuilder:
    """Builds the verb table in one place."""

    def __init__(self):
        self.parser = argparse.ArgumentParser(prog=PROG,
                                              description=DESCRIPTION)
        self.subs = self.parser.add_subparsers(dest="verb", metavar="VERB")

    def verb(self, name: str, help_text: str) -> argparse.ArgumentParser:
        return self.subs.add_parser(name, help=help_text)

    @staticmethod
    def capture_flags(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--out", default="")
        parser.add_argument("--no-cursor", action="store_true")

    def build(self) -> argparse.ArgumentParser:
        self.read_verbs()
        self.screen_verbs()
        self.window_verbs()
        self.app_verbs()
        self.input_verbs()
        return self.parser

    def read_verbs(self) -> None:
        for name, help_text in (
                ("state", "snapshot: workspaces, windows, active, pointer"),
                ("windows", "list visible windows"),
                ("active", "the focused window"),
                ("workspaces", "workspaces and the active id"),
                ("monitors", "monitors as logical rects"),
                ("pos", "cursor position"),
                ("system", "cpu, memory, uptime, disk"),
                ("apps", "desktop entries")):
            self.verb(name, help_text)
        log = self.verb("log", "last N actions from the action log")
        log.add_argument("count", nargs="?", type=int, default=50)

    def screen_verbs(self) -> None:
        screen = self.verb("screen", "full screenshot (PNG bytes)")
        self.capture_flags(screen)
        region = self.verb("region", "crop a region (logical px)")
        region.add_argument("x", type=int)
        region.add_argument("y", type=int)
        region.add_argument("w", type=int)
        region.add_argument("h", type=int)
        self.capture_flags(region)
        active = self.verb("active-screen", "screenshot of the focused window")
        self.capture_flags(active)

    def window_verbs(self) -> None:
        switch = self.verb("switch", "focus workspace N")
        switch.add_argument("workspace", type=int)
        focus = self.verb("focus", "focus one window, verified")
        focus.add_argument("spec")
        close = self.verb("close", "close a window (default: the focused one)")
        close.add_argument("spec", nargs="?")
        close_apps = self.verb("close-apps", "close every window of a kind")
        close_apps.add_argument("--kind", default="terminal")
        close_apps.add_argument("--match", default="")
        close_apps.add_argument("--keep", default="")
        close_apps.add_argument("--dry-run", action="store_true")
        move_window = self.verb("move-window", "move a window to a workspace")
        move_window.add_argument("spec")
        move_window.add_argument("workspace")
        wait_window = self.verb("wait-window", "block until a window exists")
        wait_window.add_argument("spec")
        wait_window.add_argument("--gone", action="store_true")
        wait_window.add_argument("--timeout", type=float, default=15.0)

    def app_verbs(self) -> None:
        launch = self.verb("launch", "start a program")
        launch.add_argument("--no-wait", action="store_true")
        launch.add_argument("program", nargs=argparse.REMAINDER)
        app_open = self.verb("app-open", "open an app and return its window")
        app_open.add_argument("name")
        app_open.add_argument("--workspace", type=int, default=0)
        app_open.add_argument("--cwd", default="")
        app_open.add_argument("--timeout", type=float, default=15.0)
        app_open.add_argument("--dry-run", action="store_true")
        which = self.verb("which", "where a program resolves")
        which.add_argument("name")
        directory = self.verb("dir", "does the directory exist (--create)")
        directory.add_argument("path")
        directory.add_argument("--create", action="store_true")

    def input_verbs(self) -> None:
        move = self.verb("move", "move the pointer (verified)")
        move.add_argument("x", type=int)
        move.add_argument("y", type=int)
        click = self.verb("click", "click at a point, or at the pointer")
        click.add_argument("x", nargs="?", type=int)
        click.add_argument("y", nargs="?", type=int)
        click.add_argument("--button", default="L")
        click.add_argument("--double", action="store_true")
        scroll = self.verb("scroll", "scroll at the pointer or at X Y")
        scroll.add_argument("direction")
        scroll.add_argument("amount", type=int)
        scroll.add_argument("x", nargs="?", type=int)
        scroll.add_argument("y", nargs="?", type=int)
        key = self.verb("key", "press a key chord")
        key.add_argument("combo")
        key.add_argument("--ydotool", action="store_true")
        type_ = self.verb("type", "type text into the focused window")
        type_.add_argument("text")
        clipboard = self.verb("clipboard", "read or write the clipboard")
        clipboard.add_argument("mode", choices=("get", "set"))
        clipboard.add_argument("text", nargs="?")


class Cli:
    """The command surface: services in, JSON out, coded refusals."""

    def __init__(self, argv: Optional[list] = None,
                 session: Optional[Session] = None,
                 config: Optional[Config] = None):
        self._argv = list(sys.argv[1:] if argv is None else argv)
        self.config = config or Config()
        self.session = session or Session()
        self.log = ActionLog(self.config)
        self.desktop = Desktop(self.session)
        self.screen = Screen(self.session)
        self.system = SystemInfo(self.session)
        self.apps = Apps(self.session, self.desktop)
        self.input = Input(self.session)

    # ---------------------------------------------------------------- verbs
    def cmd_state(self, args) -> int:
        return self.emit(self.desktop.state())

    def cmd_windows(self, args) -> int:
        return self.emit(self.desktop.windows())

    def cmd_active(self, args) -> int:
        return self.emit(self.desktop.active())

    def cmd_workspaces(self, args) -> int:
        return self.emit(self.desktop.workspaces())

    def cmd_monitors(self, args) -> int:
        return self.emit(self.desktop.monitors())

    def cmd_pos(self, args) -> int:
        return self.emit(self.input.pos())

    def cmd_system(self, args) -> int:
        return self.emit(self.system.sysinfo())

    def cmd_apps(self, args) -> int:
        return self.emit(self.apps.list())

    def cmd_log(self, args) -> int:
        return self.emit(self.log.entries(args.count))

    def cmd_screen(self, args) -> int:
        return self.emit_capture(self.screen.full(args.no_cursor),
                                 args.out, "screen")

    def cmd_region(self, args) -> int:
        return self.emit_capture(
            self.screen.region(args.x, args.y, args.w, args.h,
                               args.no_cursor), args.out, "region")

    def cmd_active_screen(self, args) -> int:
        return self.emit_capture(self.screen.active(args.no_cursor),
                                 args.out, "active")

    def cmd_switch(self, args) -> int:
        return self.emit(self.desktop.switch(args.workspace))

    def cmd_focus(self, args) -> int:
        return self.emit(self.desktop.focus(args.spec))

    def cmd_close(self, args) -> int:
        return self.emit(self.desktop.close(args.spec))

    def cmd_close_apps(self, args) -> int:
        return self.emit(self.desktop.close_apps(args.kind, args.match,
                                                 args.keep, args.dry_run))

    def cmd_move_window(self, args) -> int:
        return self.emit(self.desktop.move_window(args.spec, args.workspace))

    def cmd_wait_window(self, args) -> int:
        return self.emit(self.desktop.wait(args.spec, args.gone,
                                           args.timeout))

    def cmd_launch(self, args) -> int:
        if not args.program:
            raise ControlError("bad-args", "launch: a program is required")
        return self.emit(self.apps.launch(args.program,
                                          wait=not args.no_wait))

    def cmd_app_open(self, args) -> int:
        result = self.apps.open(args.name, workspace=args.workspace,
                                cwd=args.cwd, timeout=args.timeout,
                                dry_run=args.dry_run)
        return self.emit(result)

    def cmd_which(self, args) -> int:
        return self.emit({"name": args.name, "path": self.apps.which(args.name)})

    def cmd_dir(self, args) -> int:
        return self.emit(self.apps.dir(args.path, create=args.create))

    def cmd_move(self, args) -> int:
        return self.emit(self.input.move(args.x, args.y))

    def cmd_click(self, args) -> int:
        return self.emit(self.input.click(args.x, args.y, args.button,
                                          args.double))

    def cmd_scroll(self, args) -> int:
        return self.emit(self.input.scroll(args.direction, args.amount,
                                           args.x, args.y))

    def cmd_key(self, args) -> int:
        return self.emit(self.input.key(args.combo, args.ydotool))

    def cmd_type(self, args) -> int:
        return self.emit(self.input.type_text(args.text))

    def cmd_clipboard(self, args) -> int:
        if args.mode == "get":
            return self.emit({"text": self.system.clipboard_get()})
        if args.text is None:
            raise ControlError("bad-args", "clipboard set: TEXT is required")
        self.system.clipboard_set(args.text)
        return self.emit({"ok": True, "chars": len(args.text)})

    # --------------------------------------------------------------- plumbing
    def run(self) -> int:
        parser = self.build_parser()
        try:
            args = parser.parse_args(self._argv)
        except SystemExit as exit_error:  # --help, bad usage
            return int(exit_error.code or 0)
        verb = str(getattr(args, "verb", "") or "")
        handler = getattr(self, "cmd_" + verb.replace("-", "_"), None) \
            if verb else None
        if handler is None:
            print(parser.format_usage().rstrip(), file=sys.stderr)
            return 2
        try:
            result = int(handler(args) or 0)
        except ControlError as error:
            self.log.write(verb, ok=False, args=self.log_args(verb),
                           code=error.code, detail=error.message,
                           text=self.log_text(verb, args))
            return self.report(error)
        self.log.write(verb, ok=True, args=self.log_args(verb),
                       text=self.log_text(verb, args))
        return result

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        return ParserBuilder().build()

    @staticmethod
    def emit(value: Any) -> int:
        print(json.dumps(Cli.jsonable(value), separators=(",", ":")))
        return 0

    def emit_capture(self, data: bytes, out: str, prefix: str) -> int:
        if out:
            return self.emit(self.screen.save(data, out, prefix))
        sys.stdout.buffer.write(data)
        return 0

    @staticmethod
    def jsonable(value: Any) -> Any:
        if isinstance(value, Outcome):
            return value.to_dict()
        if isinstance(value, list):
            return [Cli.jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [Cli.jsonable(item) for item in value]
        if hasattr(value, "to_dict") and not isinstance(value, dict):
            return value.to_dict()
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return dataclasses.asdict(value)
        if isinstance(value, dict):
            return {key: Cli.jsonable(item) for key, item in value.items()}
        return value

    @staticmethod
    def report(error: ControlError) -> int:
        """The one place a refusal becomes a line and an exit code."""
        print(f"ERR[{error.code}]: {error.message}", file=sys.stderr)
        return 1

    def log_args(self, verb: str) -> list:
        """The argv to record — the text of a secret-adjacent verb is dropped."""
        if verb in ("type", "clipboard"):
            return self._argv[:1]
        return list(self._argv)

    def log_text(self, verb: str, args) -> Optional[str]:
        if verb == "type":
            return getattr(args, "text", None)
        if verb == "clipboard" and args.mode == "set":
            return args.text
        return None

    @classmethod
    def main(cls, argv: Optional[list] = None) -> int:
        return cls(argv).run()
