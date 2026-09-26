"""The command line: one subcommand per verb, JSON on stdout, ERR[code] on
stderr with exit 1.

The parser is built in one place (:class:`ParserBuilder`) and the handler for
a verb is the matching ``cmd_<verb>`` method, which returns Python data and
prints nothing. ``run`` presents that data (JSON, or raw PNG bytes) and is the
only place that logs and maps refusals to the ``ERR[code]`` line; ``sequence``
reuses the same dispatch without printing.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, Optional, Union

from computer_control_cli import __version__
from computer_control_cli.a11y import A11y  # pi-lens-ignore: reportMissingImports
from computer_control_cli.apps import Apps  # pi-lens-ignore: reportMissingImports
from computer_control_cli.audit import Audit  # pi-lens-ignore: reportMissingImports
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
    """Builds the verb table and the global flags in one place."""

    def __init__(self):
        self.parser = argparse.ArgumentParser(prog=PROG,
                                              description=DESCRIPTION)
        self.parser.add_argument("--version", action="version",
                                 version=f"{PROG} {__version__}")
        self.parser.add_argument("--instance", default="",
                                 help="target this Hyprland instance signature")
        self.parser.add_argument("--display", default="",
                                 help="target this Wayland display")
        self.parser.add_argument("--quiet", action="store_true",
                                 help="suppress stdout (exit codes and "
                                      "refusals still apply)")
        self.parser.add_argument("--dry-run", dest="dry_run_all",
                                 action="store_true",
                                 help="report actions without doing them")
        self.parser.add_argument("--timeout", dest="timeout_default",
                                 type=float, default=None,
                                 help="default window-wait timeout in seconds")
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
                ("system", "cpu, memory, uptime, disk, network, battery"),
                ("apps", "desktop entries and running processes")):
            self.verb(name, help_text)
        a11y = self.verb("a11y", "AT-SPI element tree (JSON rows)")
        a11y.add_argument("--app", default="")
        a11y.add_argument("--max", type=int, default=None)
        self.verb("a11y-enable", "turn the session's AT-SPI flag on")
        audit = self.verb("audit", "tools, capture 1:1, calibration, a11y")
        audit.add_argument("--no-probe", action="store_true")
        audit.add_argument("--no-a11y", action="store_true")
        sequence = self.verb("sequence", "run a JSON array of argv steps")
        sequence.add_argument("steps")
        log = self.verb("log", "last N actions from the action log")
        log.add_argument("count", nargs="?", type=int, default=50)
        log.add_argument("--since", default="")
        log.add_argument("--text", action="store_true")
        log.add_argument("--follow", action="store_true",
                         help="tail the log, one entry per line")

    def screen_verbs(self) -> None:
        screen = self.verb("screen", "full screenshot (PNG bytes)")
        screen.add_argument("--monitor", default="")
        self.capture_flags(screen)
        region = self.verb("region", "crop a region (logical px)")
        region.add_argument("x", type=int)
        region.add_argument("y", type=int)
        region.add_argument("w", type=int)
        region.add_argument("h", type=int)
        self.capture_flags(region)
        active = self.verb("active-screen", "screenshot of a window")
        active.add_argument("--window", default="")
        self.capture_flags(active)

    def window_verbs(self) -> None:
        switch = self.verb("switch", "focus workspace N")
        switch.add_argument("workspace", type=int)
        focus = self.verb("focus", "focus one window, verified")
        focus.add_argument("spec")
        close = self.verb("close", "close a window (default: the focused one)")
        close.add_argument("spec", nargs="?")
        close_apps = self.verb("close-apps",
                               "close every window of a kind "
                               "(terminal, browser, other, all)")
        close_apps.add_argument("--kind", default="terminal")
        close_apps.add_argument("--match", default="")
        close_apps.add_argument("--keep", default="")
        close_apps.add_argument("--dry-run", action="store_true")
        move_window = self.verb("move-window", "move a window to a workspace")
        move_window.add_argument("spec")
        move_window.add_argument("workspace")
        wait_window = self.verb("wait-window", "block until a window exists")
        wait_window.add_argument("spec", nargs="?", default="")
        wait_window.add_argument("--gone", action="store_true")
        wait_window.add_argument("--timeout", type=float, default=None)
        wait_window.add_argument("--new", dest="new_only", action="store_true")
        wait_window.add_argument("--exclude", default="")
        wait_window.add_argument("--title", default="")
        wait_window.add_argument("--class", dest="klass", default="")
        wait_window.add_argument("--stable", type=int, default=1)

    def app_verbs(self) -> None:
        launch = self.verb("launch", "start a program")
        launch.add_argument("--no-wait", action="store_true")
        launch.add_argument("--workspace", type=int, default=0)
        launch.add_argument("--timeout", type=float, default=None)
        launch.add_argument("program", nargs=argparse.REMAINDER)
        app_open = self.verb("app-open", "open an app and return its window")
        app_open.add_argument("name")
        app_open.add_argument("--workspace", type=int, default=0)
        app_open.add_argument("--cwd", default="")
        app_open.add_argument("--timeout", type=float, default=None)
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
        move.add_argument("--clearmodifiers", action="store_true")
        click = self.verb("click", "click at a point, at the pointer, or an "
                                  "AT-SPI element")
        click.add_argument("x", nargs="?", type=int)
        click.add_argument("y", nargs="?", type=int)
        click.add_argument("--button", default="L")
        click.add_argument("--double", action="store_true")
        click.add_argument("--el", type=int, default=None)
        click.add_argument("--clearmodifiers", action="store_true")
        scroll = self.verb("scroll", "scroll at the pointer or at X Y")
        scroll.add_argument("direction")
        scroll.add_argument("amount", type=int)
        scroll.add_argument("x", nargs="?", type=int)
        scroll.add_argument("y", nargs="?", type=int)
        scroll.add_argument("--clearmodifiers", action="store_true")
        key = self.verb("key", "press a key chord")
        key.add_argument("combo")
        key.add_argument("--ydotool", action="store_true")
        key.add_argument("--clearmodifiers", action="store_true")
        key_down = self.verb("key-down", "hold a chord's keys down")
        key_down.add_argument("combo")
        key_down.add_argument("--clearmodifiers", action="store_true")
        key_up = self.verb("key-up", "release a chord's keys")
        key_up.add_argument("combo")
        key_up.add_argument("--clearmodifiers", action="store_true")
        type_ = self.verb("type", "type text into the focused window")
        type_.add_argument("text")
        type_.add_argument("--delay", type=int, default=0)
        type_.add_argument("--clearmodifiers", action="store_true")
        drag = self.verb("drag", "press, move and release the pointer")
        drag.add_argument("x1", type=int)
        drag.add_argument("y1", type=int)
        drag.add_argument("x2", type=int)
        drag.add_argument("y2", type=int)
        drag.add_argument("--button", default="L")
        drag.add_argument("--duration", type=int, default=300)
        drag.add_argument("--clearmodifiers", action="store_true")
        clipboard = self.verb("clipboard",
                              "read, write, clear, or list the clipboard")
        clipboard.add_argument("mode",
                               choices=("get", "set", "clear", "types"))
        clipboard.add_argument("text", nargs="?")
        clipboard.add_argument("--primary", action="store_true")
        clipboard.add_argument("--type", dest="mime", default="",
                               help="MIME type (images, HTML, …)")
        clipboard.add_argument("--file", dest="file", default="",
                               help="send these bytes (set)")
        clipboard.add_argument("--out", dest="out", default="",
                               help="save the bytes here (get)")


class Cli:
    """The command surface: services in, JSON out, coded refusals."""

    def __init__(self, argv: Optional[list] = None,
                 session: Optional[Session] = None,
                 config: Optional[Config] = None):
        self._argv = list(sys.argv[1:] if argv is None else argv)
        self._injected_session = session
        self.config = config or Config()
        self.quiet = False
        self.dry_run = False
        self.default_timeout = 15.0
        self.build_services()

    # --------------------------------------------------------------- wiring
    def build_services(self, instance: str = "", display: str = "") -> None:
        """Construct (or re-construct) every service over one session."""
        overrides = {}
        if instance:
            overrides["HYPRLAND_INSTANCE_SIGNATURE"] = instance
        if display:
            overrides["WAYLAND_DISPLAY"] = display
        if overrides:
            base = self._injected_session or Session()
            self.session = base.with_env(overrides)
        else:
            self.session = self._injected_session or Session()
        self.log = ActionLog(self.config)
        self.desktop = Desktop(self.session)
        self.screen = Screen(self.session)
        self.system = SystemInfo(self.session)
        self.apps = Apps(self.session, self.desktop)
        self.input = Input(self.session)
        self.a11y = A11y(self.session, input=self.input,
                         desktop=self.desktop)
        self.audit = Audit(self.session, self.desktop, self.screen,
                           self.input, self.a11y)

    # ---------------------------------------------------------------- verbs
    def cmd_state(self, args) -> Any:
        return self.desktop.state()

    def cmd_windows(self, args) -> Any:
        return self.desktop.windows()

    def cmd_active(self, args) -> Any:
        return self.desktop.active()

    def cmd_workspaces(self, args) -> Any:
        return self.desktop.workspaces()

    def cmd_monitors(self, args) -> Any:
        return self.desktop.monitors()

    def cmd_pos(self, args) -> Any:
        return self.input.pos()

    def cmd_system(self, args) -> Any:
        return self.system.sysinfo()

    def cmd_apps(self, args) -> Any:
        return self.apps.list()

    def cmd_a11y(self, args) -> Any:
        return self.a11y.index(args.app or None, args.max)

    def cmd_a11y_enable(self, args) -> Any:
        return self.a11y.enable()

    def cmd_audit(self, args) -> Any:
        return self.audit.audit(no_probe=args.no_probe, no_a11y=args.no_a11y)

    def cmd_log(self, args) -> Any:
        if args.follow:
            self.follow_log(args)
            return None
        entries = self.log.entries(args.count, since=args.since or None)
        if args.text:
            if not self.quiet:
                for line in ActionLog.render(entries):
                    print(line)
            return None
        return entries

    def follow_log(self, args) -> None:
        """Tail the action log: one line per entry until interrupted."""
        try:
            for entry in self.log.follow(args.count,
                                         since=args.since or None):
                if self.quiet:
                    continue
                if args.text:
                    print(ActionLog.render([entry])[0], flush=True)
                else:
                    print(json.dumps(entry, separators=(",", ":")),
                          flush=True)
        except KeyboardInterrupt:
            return

    def cmd_screen(self, args) -> Any:
        if args.monitor:
            data = self.screen.monitor(args.monitor, args.no_cursor)
            prefix = "monitor"
        else:
            data = self.screen.full(args.no_cursor)
            prefix = "screen"
        if args.out:
            return self.screen.save(data, args.out, prefix)
        return data

    def cmd_region(self, args) -> Any:
        data = self.screen.region(args.x, args.y, args.w, args.h,
                                  args.no_cursor)
        if args.out:
            return self.screen.save(data, args.out, "region")
        return data

    def cmd_active_screen(self, args) -> Any:
        if args.window:
            window = self.desktop.resolve(args.window, "active-screen")
            data = self.screen.capture_window(window, args.no_cursor)
        else:
            data = self.screen.active(args.no_cursor)
        if args.out:
            return self.screen.save(data, args.out, "active")
        return data

    def cmd_switch(self, args) -> Any:
        return self.desktop.switch(args.workspace)

    def cmd_focus(self, args) -> Any:
        return self.desktop.focus(args.spec)

    def cmd_close(self, args) -> Any:
        return self.desktop.close(args.spec)

    def cmd_close_apps(self, args) -> Any:
        return self.desktop.close_apps(args.kind, args.match, args.keep,
                                       args.dry_run)

    def cmd_move_window(self, args) -> Any:
        return self.desktop.move_window(args.spec, args.workspace)

    def cmd_wait_window(self, args) -> Any:
        exclude = {part.strip().lower()
                   for part in str(args.exclude).split(",") if part.strip()}
        timeout = (args.timeout if args.timeout is not None
                   else self.default_timeout)
        return self.desktop.wait(args.spec, gone=args.gone,
                                 timeout=timeout, new_only=args.new_only,
                                 exclude=exclude, title=args.title,
                                 klass=args.klass, stable=args.stable)

    def cmd_launch(self, args) -> Any:
        if not args.program:
            raise ControlError("bad-args", "launch: a program is required")
        timeout = (args.timeout if args.timeout is not None
                   else self.default_timeout)
        return self.apps.launch(args.program, wait=not args.no_wait,
                                workspace=args.workspace, timeout=timeout)

    def cmd_app_open(self, args) -> Any:
        timeout = (args.timeout if args.timeout is not None
                   else self.default_timeout)
        return self.apps.open(args.name, workspace=args.workspace,
                              cwd=args.cwd, timeout=timeout,
                              dry_run=args.dry_run)

    def cmd_which(self, args) -> Any:
        return {"name": args.name, "path": self.apps.which(args.name)}

    def cmd_dir(self, args) -> Any:
        return self.apps.dir(args.path, create=args.create)

    def cmd_move(self, args) -> Any:
        return self.input.move(args.x, args.y, args.clearmodifiers)

    def cmd_click(self, args) -> Any:
        if args.el is not None:
            if args.x is not None or args.y is not None:
                raise ControlError("bad-args",
                                   "click: give X Y or --el N, not both")
            if args.double:
                raise ControlError(
                    "bad-args",
                    "click --el does not support --double (AT-SPI has no "
                    "double-click action); click by pixels instead")
            return self.a11y.click_element(args.el, args.button)
        return self.input.click(args.x, args.y, args.button, args.double,
                                args.clearmodifiers)

    def cmd_scroll(self, args) -> Any:
        return self.input.scroll(args.direction, args.amount, args.x, args.y,
                                 args.clearmodifiers)

    def cmd_key(self, args) -> Any:
        return self.input.key(args.combo, args.ydotool, args.clearmodifiers)

    def cmd_key_down(self, args) -> Any:
        return self.input.key_down(args.combo, args.clearmodifiers)

    def cmd_key_up(self, args) -> Any:
        return self.input.key_up(args.combo, args.clearmodifiers)

    def cmd_type(self, args) -> Any:
        return self.input.type_text(args.text, args.delay,
                                    args.clearmodifiers)

    def cmd_drag(self, args) -> Any:
        return self.input.drag(args.x1, args.y1, args.x2, args.y2,
                               args.button, args.duration,
                               args.clearmodifiers)

    def cmd_clipboard(self, args) -> Any:
        primary = bool(getattr(args, "primary", False))
        if args.mode == "types":
            return self.system.clipboard_types(primary)
        if args.mode == "get":
            if args.mime:
                data: Union[str, bytes] = self.system.clipboard_get(
                    primary, mime=args.mime, binary=True)
                if isinstance(data, str):
                    data = data.encode()
                if args.out:
                    return self.save_bytes(data, args.out)
                return data          # raw bytes to stdout
            text = self.system.clipboard_get(primary)
            if args.out:
                return self.save_bytes(str(text).encode(), args.out)
            return {"text": text, "primary": primary}
        if args.mode == "clear":
            self.system.clipboard_clear(primary)
            return {"ok": True, "cleared": True, "primary": primary}
        if args.file:
            data = self.read_file(args.file)
            self.system.clipboard_set(data, primary, mime=args.mime)
            return {"ok": True, "bytes": len(data), "primary": primary,
                    "type": args.mime or None}
        if args.text is None:
            raise ControlError("bad-args",
                               "clipboard set: TEXT or --file is required")
        if args.mime:
            self.system.clipboard_set(args.text.encode(), primary,
                                      mime=args.mime)
        else:
            self.system.clipboard_set(args.text, primary)
        return {"ok": True, "chars": len(args.text), "primary": primary,
                "type": args.mime or None}

    @staticmethod
    def read_file(path: str) -> bytes:
        try:
            with open(os.path.expanduser(path), "rb") as handle:
                return handle.read()
        except OSError as error:
            raise ControlError("no-file", f"cannot read {path!r}: {error}")

    @staticmethod
    def save_bytes(data: bytes, path: str) -> dict:
        target = os.path.expanduser(path)
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as error:
            raise ControlError("no-file", f"cannot write {path!r}: {error}")
        return {"path": target, "bytes": len(data)}

    def cmd_sequence(self, args) -> Any:
        try:
            steps = json.loads(args.steps)
        except ValueError:
            raise ControlError("bad-json", "sequence: STEPS is not JSON")
        if not isinstance(steps, list):
            raise ControlError(
                "bad-args", "sequence: STEPS must be a JSON array of argv "
                "arrays")
        results = []
        for step in steps:
            if (not isinstance(step, list) or not step
                    or not all(isinstance(word, str) for word in step)):
                raise ControlError(
                    "bad-args",
                    "sequence: every step must be a non-empty array of "
                    "strings")
            results.append(self.dispatch(step))
        return results

    # --------------------------------------------------------------- plumbing
    def dispatch(self, argv: list) -> Any:
        """Run one argv through the same parser, returning its raw result."""
        parser = self.build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit:
            raise ControlError(
                "bad-args", f"sequence: {argv!r} is not a valid call") \
                from None
        verb = str(getattr(args, "verb", "") or "")
        handler = getattr(self, "cmd_" + verb.replace("-", "_"), None)
        if handler is None:
            raise ControlError("bad-args",
                               f"sequence: no verb called {verb!r}")
        if self.dry_run and self.mutates(verb, args):
            if hasattr(args, "dry_run"):
                args.dry_run = True
            else:
                return self.plan()
        result = handler(args)
        if isinstance(result, bytes):
            raise ControlError(
                "bad-args",
                "sequence: a screenshot step must use --out DIR")
        return result

    def run(self) -> int:
        parser = self.build_parser()
        try:
            args = parser.parse_args(self._argv)
        except SystemExit as exit_error:  # --help, --version, bad usage
            return int(exit_error.code or 0)
        self.quiet = bool(getattr(args, "quiet", False))
        self.dry_run = bool(getattr(args, "dry_run_all", False))
        if getattr(args, "timeout_default", None) is not None:
            self.default_timeout = float(args.timeout_default)
        if getattr(args, "instance", "") or getattr(args, "display", ""):
            try:
                self.build_services(args.instance, args.display)
            except ControlError as error:
                verb = str(getattr(args, "verb", "") or "target")
                self.log.write(verb, ok=False, args=list(self._argv),
                               code=error.code, detail=error.message)
                return self.report(error)
        verb = str(getattr(args, "verb", "") or "")
        handler = getattr(self, "cmd_" + verb.replace("-", "_"), None) \
            if verb else None
        if handler is None:
            print(parser.format_usage().rstrip(), file=sys.stderr)
            return 2
        if self.dry_run and self.mutates(verb, args):
            if hasattr(args, "dry_run"):
                args.dry_run = True          # the verb has its own, richer plan
            else:
                self.log.write(verb, ok=True, args=self.log_args(verb),
                               detail="dry-run")
                return self.present(self.plan())
        try:
            result = handler(args)
        except ControlError as error:
            self.log.write(verb, ok=False, args=self.log_args(verb),
                           code=error.code, detail=error.message,
                           text=self.log_text(verb, args))
            return self.report(error)
        self.log.write(verb, ok=True, args=self.log_args(verb),
                       text=self.log_text(verb, args))
        return self.present(result)

    def present(self, result: Any) -> int:
        if self.quiet:
            return 0
        if isinstance(result, bytes):
            sys.stdout.buffer.write(result)
            return 0
        if result is None:
            return 0
        return self.emit(result)

    MUTATING = frozenset({
        "switch", "focus", "close", "close-apps", "move-window", "launch",
        "app-open", "move", "click", "scroll", "drag", "key", "key-down",
        "key-up", "type", "a11y-enable",
    })

    @classmethod
    def mutates(cls, verb: str, args) -> bool:
        """Does this call change the desktop (or the filesystem)?"""
        if verb == "clipboard":
            return str(getattr(args, "mode", "")) in ("set", "clear")
        if verb == "dir":
            return bool(getattr(args, "create", False))
        return verb in cls.MUTATING

    def plan(self) -> dict:
        """The dry-run reply: what would have run, and nothing else."""
        return {"dry_run": True, "argv": [PROG, *self._argv]}

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        return ParserBuilder().build()

    @staticmethod
    def emit(value: Any) -> int:
        print(json.dumps(Cli.jsonable(value), separators=(",", ":")))
        return 0

    @staticmethod
    def jsonable(value: Any) -> Any:
        if isinstance(value, Outcome):
            return value.to_dict()
        if isinstance(value, (list, tuple)):
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
