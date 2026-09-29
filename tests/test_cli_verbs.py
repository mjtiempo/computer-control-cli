"""Tests for the CLI verbs: JSON on stdout, ERR[code] on stderr, exit codes."""

import io
import json
import os
import struct
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from computer_control_cli.a11y import A11y  # pi-lens-ignore: reportMissingImports
from computer_control_cli.cli import Cli  # pi-lens-ignore: reportMissingImports
from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import KeyMap  # pi-lens-ignore: reportMissingImports
from computer_control_cli.log import ActionLog  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland, FakeInputRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}

CLIENTS = [
    {"address": "0xaaa1", "title": "foot", "class": "foot",
     "workspace": {"id": 1, "name": "1"}, "at": [100, 200],
     "size": [800, 600], "mapped": True, "fullscreen": 0,
     "floating": False, "pid": 11},
]


def png(width: int, height: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"x")


class CliFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        self.runner = FakeInputRunner(self.hypr)
        self.session = Session(env=ENV, runner=self.runner,
                               discoverer=FakeDiscoverer(ENV))
        self.config = Config(env={"CCC_LOG": os.path.join(self._tmp.name,
                                                          "actions.jsonl")})

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = Cli(list(argv), session=self.session,
                       config=self.config).run()
        return code, stdout.getvalue(), stderr.getvalue()

    def run_json(self, *argv):
        code, stdout, stderr = self.run_cli(*argv)
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)


class CliReadTests(CliFixture):
    def test_windows(self):
        rows = self.run_json("windows")

        self.assertEqual(rows[0]["address"], "0xaaa1")

    def test_state(self):
        state = self.run_json("state")

        self.assertEqual(state["active_workspace"], 1)
        self.assertEqual(state["active"]["class"], "foot")

    def test_workspaces_and_monitors(self):
        self.assertEqual(self.run_json("workspaces")[0]["id"], 1)
        self.assertEqual(self.run_json("monitors")[0]["name"], "eDP-1")

    def test_pos(self):
        self.assertEqual(self.run_json("pos"), {"x": 0, "y": 0})

    def test_system(self):
        info = self.run_json("system")

        self.assertIn("cpu_count", info)
        self.assertIn("memory", info)

    def test_apps_and_which(self):
        self.assertIsInstance(self.run_json("apps"), list)
        self.assertTrue(self.run_json("which", "sh")["path"].endswith("sh"))

    def test_dir(self):
        result = self.run_json("dir", self._tmp.name)

        self.assertTrue(result["existed"])


class CliActionTests(CliFixture):
    def test_switch(self):
        result = self.run_json("switch", "2")

        self.assertTrue(result["verified"])
        self.assertEqual(self.hypr.active_workspace, 2)

    def test_focus(self):
        result = self.run_json("focus", "class:foot")

        self.assertTrue(result["verified"])

    def test_close_apps_dry_run(self):
        result = self.run_json("close-apps", "--kind", "terminal", "--dry-run")

        self.assertEqual(result["count"], 1)
        self.assertTrue(result["dry_run"])
        self.assertNotIn("ok", result, "a plan is not a failure")

    def test_move_window(self):
        result = self.run_json("move-window", "class:foot", "2")

        self.assertTrue(result["verified"])

    def test_wait_window_timeout_is_a_refusal(self):
        code, _, stderr = self.run_cli("wait-window", "class:nope",
                                       "--timeout", "0")

        self.assertEqual(code, 1)
        self.assertIn("ERR[wait-timeout]", stderr)

    def test_wait_window_filters(self):
        result = self.run_json("wait-window", "--title", "foot",
                               "--timeout", "0")

        self.assertTrue(result["verified"])
        self.assertEqual(result["window"]["address"], "0xaaa1")

    def test_wait_window_new_finds_nothing(self):
        code, _, stderr = self.run_cli("wait-window", "--new",
                                       "--timeout", "0")

        self.assertEqual(code, 1)
        self.assertIn("ERR[wait-timeout]", stderr)


class BinaryStdout:
    """A stdout double that carries a .buffer for PNG bytes."""

    def __init__(self):
        self.buffer = io.BytesIO()
        self.text = io.StringIO()

    def write(self, value: str) -> int:
        return self.text.write(value)

    def flush(self) -> None:
        pass


class CliScreenTests(CliFixture):
    def test_screen_monitor_captures_its_rect(self):
        self.runner.responses["grim -c -s 1 -g 0,0 1920x1080 -"] = png(1920, 1080)

        result = self.run_json("screen", "--monitor", "eDP-1",
                               "--out", self._tmp.name)

        self.assertEqual((result["width"], result["height"]), (1920, 1080))

    def test_active_screen_by_window_spec(self):
        self.runner.responses["grim -c -s 1 -g 100,200 800x600 -"] = png(800, 600)

        result = self.run_json("active-screen", "--window", "class:foot",
                               "--out", self._tmp.name)

        self.assertEqual((result["width"], result["height"]), (800, 600))

    def test_screen_bytes(self):
        self.runner.responses["grim -c -s 1 -"] = png(1920, 1080)
        stdout = BinaryStdout()

        with mock.patch("sys.stdout", stdout):
            code = Cli(["screen"], session=self.session,
                       config=self.config).run()

        self.assertEqual(code, 0)
        self.assertTrue(stdout.buffer.getvalue().startswith(b"\x89PNG"))

    def test_screen_out_reports_the_file(self):
        self.runner.responses["grim -c -s 1 -"] = png(640, 480)

        result = self.run_json("screen", "--out", self._tmp.name)

        self.assertTrue(os.path.isfile(result["path"]))
        self.assertEqual((result["width"], result["height"]), (640, 480))


class CliInputTests(CliFixture):
    def test_move(self):
        result = self.run_json("move", "500", "300")

        self.assertTrue(result["verified"])

    def test_click(self):
        result = self.run_json("click", "100", "200", "--button", "R")

        self.assertIn(["ydotool", "click", "0xC1"], self.runner.calls)

    def test_key(self):
        result = self.run_json("key", "ctrl+c")

        self.assertEqual(result["route"], "wtype")

    def test_type(self):
        result = self.run_json("type", "hello")

        self.assertEqual(result["chars"], 5)

    def test_type_with_delay(self):
        self.run_json("type", "hi", "--delay", "100")

        self.assertIn(["wtype", "-d", "100", "hi"], self.runner.calls)

    def test_key_down_and_up(self):
        self.run_json("key-down", "ctrl+c")
        self.run_json("key-up", "ctrl+c")

        self.assertIn(["ydotool", "key", "29:1", "46:1"], self.runner.calls)
        self.assertIn(["ydotool", "key", "29:0", "46:0"], self.runner.calls)

    def test_clearmodifiers_releases_before_typing(self):
        release = ["ydotool", "key"] + [f"{code}:0"
                                         for code in KeyMap.MODIFIER_RELEASE_CODES]

        self.run_json("type", "hi", "--clearmodifiers")

        self.assertEqual(self.runner.calls[0], release)

    def test_drag(self):
        result = self.run_json("drag", "100", "100", "300", "200",
                               "--duration", "0")

        self.assertFalse(result["verified"])
        self.assertTrue(any(call[0] == "python3" and "EV_KEY" in call[-1]
                            for call in self.runner.calls))

    def test_launch_with_workspace(self):
        self.hypr.spawn_on_exec = [{"address": "0xfeed", "title": "prog",
                                    "class": "prog",
                                    "workspace": {"id": 1, "name": "1"},
                                    "at": [0, 0], "size": [100, 100],
                                    "mapped": True, "fullscreen": 0,
                                    "floating": False, "pid": 77}]

        result = self.run_json("launch", "--workspace", "2", "prog")

        self.assertTrue(result["verified"])
        self.assertEqual(result["workspace"], 2)

    def test_clipboard_set_and_get(self):
        self.run_json("clipboard", "set", "hello")
        self.assertEqual(self.runner.spawned[-1], (["wl-copy"], "hello"))
        got = self.run_json("clipboard", "get")
        self.assertEqual(got["text"], "hello")
        self.assertFalse(got["primary"])

    def test_clipboard_primary_and_clear(self):
        self.run_json("clipboard", "set", "first", "--primary")
        self.assertEqual(self.runner.spawned[-1][0],
                         ["wl-copy", "--primary"])
        cleared = self.run_json("clipboard", "clear", "--primary")
        self.assertTrue(cleared["cleared"])
        self.assertEqual(self.runner.calls[-1],
                         ["wl-copy", "--primary", "--clear"])

    def test_clipboard_get_primary(self):
        self.runner.clipboard = "primary text"

        result = self.run_json("clipboard", "get", "--primary")

        self.assertTrue(result["primary"])
        self.assertEqual(result["text"], "primary text")

    def test_clipboard_types(self):
        self.runner.responses["wl-paste --list-types"] = \
            "text/plain\nimage/png"

        self.assertEqual(self.run_json("clipboard", "types"),
                         ["text/plain", "image/png"])

    def test_clipboard_file_round_trip(self):
        payload = b"\x89PNG\r\n\x1a\nfake-image"
        source = os.path.join(self._tmp.name, "in.png")
        with open(source, "wb") as handle:
            handle.write(payload)

        result = self.run_json("clipboard", "set", "--file", source,
                               "--type", "image/png")
        self.assertEqual(result["bytes"], len(payload))
        out = os.path.join(self._tmp.name, "out.png")
        got = self.run_json("clipboard", "get", "--type", "image/png",
                            "--out", out)

        self.assertEqual(got["bytes"], len(payload))
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_clipboard_get_type_writes_raw_bytes(self):
        self.runner.clipboard_data = b"\x89PNG"
        self.runner.clipboard_mime = "image/png"
        stdout = BinaryStdout()

        with mock.patch("sys.stdout", stdout):
            code = Cli(["clipboard", "get", "--type", "image/png"],
                       session=self.session, config=self.config).run()

        self.assertEqual(code, 0)
        self.assertEqual(stdout.buffer.getvalue(), b"\x89PNG")

    def test_clipboard_missing_file_refuses(self):
        code, _, stderr = self.run_cli("clipboard", "set", "--file",
                                       "/definitely/nope.png")

        self.assertEqual(code, 1)
        self.assertIn("ERR[no-file]", stderr)

    def test_clipboard_types_refuses_an_out_it_cannot_use(self):
        target = os.path.join(self._tmp.name, "types.bin")

        code, _, stderr = self.run_cli("clipboard", "types", "--out", target)

        self.assertEqual(code, 1)
        self.assertIn("ERR[bad-args]", stderr)

    def test_log_zero_prints_no_entries(self):
        self.run_json("type", "hi")

        code, stdout, _ = self.run_cli("log", "0")

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), [])

    def test_click_el_refuses_a_button_the_ax_route_cannot_use(self):
        self.runner.responses[f"python3 {A11y.bridge_path()} click 0"] = \
            json.dumps({"ok": True, "route": "ax", "action": "press",
                        "role": "push button", "name": "Save"})

        code, _, stderr = self.run_cli("click", "--el", "0", "--button", "R")

        self.assertEqual(code, 1)
        self.assertIn("ERR[bad-args]", stderr)

    def test_click_el_px_fallback(self):
        self.runner.responses[f"python3 {A11y.bridge_path()} click 0"] = \
            json.dumps({"ok": True, "route": "px-fallback", "x": 110,
                        "y": 220, "app": "foot", "role": "push button",
                        "name": "Save"})

        result = self.run_json("click", "--el", "0")

        self.assertEqual(result["route"], "px-fallback")
        self.assertEqual(result["x"], 110)   # screen coords, as reported
        self.assertEqual(result["y"], 220)
        self.assertIn(["ydotool", "click", "0xC0"], self.runner.calls)

    def test_the_rung_vocabulary_is_the_documented_one(self):
        rungs = {self.run_json("scroll", "down", "1")["rung"],
                 self.run_json("click")["rung"],
                 self.run_json("key", "a")["rung"],
                 self.run_json("type", "hi")["rung"]}

        self.assertLessEqual(
            rungs, {"dispatch", "px", "input", "app", "poll", "ax"},
            f"undocumented rung(s): {rungs}")

    def test_log_text_lines(self):
        self.run_json("type", "hi")
        code, stdout, _ = self.run_cli("log", "1", "--text")

        self.assertEqual(code, 0)
        self.assertIn("type ok", stdout)
        self.assertNotIn("hi", stdout)

    def test_type_is_redacted_in_the_log(self):
        self.run_json("type", "hunter2")

        code, stdout, _ = self.run_cli("log", "1")
        entry = json.loads(stdout)[0]
        self.assertNotIn("text", entry)
        self.assertEqual(entry["text_chars"], 7)

    def test_sequence_does_not_log_typed_text(self):
        self.run_json("sequence", json.dumps([["type", "hunter2"]]))

        code, stdout, _ = self.run_cli("log", "1")
        entry = json.loads(stdout)[0]
        self.assertEqual(entry["action"], "sequence")
        self.assertNotIn("hunter2", json.dumps(entry))
        self.assertIn("<redacted>", json.dumps(entry))


class CliA11yTests(CliFixture):
    def test_a11y_prints_rows(self):
        self.runner.responses[f"python3 {A11y.bridge_path()} index"] = \
            json.dumps([{"index": 0, "app": "foot", "role": "push button",
                         "name": "Save", "actions": ["press"]}])

        rows = self.run_json("a11y")

        self.assertEqual(rows[0]["name"], "Save")

    def test_click_el_ax_route(self):
        self.runner.responses[f"python3 {A11y.bridge_path()} click 0"] = \
            json.dumps({"ok": True, "route": "ax", "action": "press",
                        "role": "push button", "name": "Save"})

        result = self.run_json("click", "--el", "0")

        self.assertEqual(result["route"], "ax")
        self.assertEqual(result["action"], "press")

    def test_click_el_with_coordinates_refuses(self):
        code, _, stderr = self.run_cli("click", "1", "2", "--el", "3")

        self.assertEqual(code, 1)
        self.assertIn("ERR[bad-args]", stderr)

    def test_click_el_clears_modifiers(self):
        self.runner.responses[f"python3 {A11y.bridge_path()} click 0"] = \
            json.dumps({"ok": True, "route": "ax", "action": "press",
                        "role": "push button", "name": "Save"})
        release = ["ydotool", "key"] + [f"{code}:0"
                                        for code in KeyMap.MODIFIER_RELEASE_CODES]

        self.run_json("click", "--el", "0", "--clearmodifiers")

        self.assertEqual(self.runner.calls[0], release)

    def test_a11y_enable(self):
        set_key = ("busctl --user set-property org.a11y.Bus /org/a11y/bus "
                   "org.a11y.Status IsEnabled b true")
        get_key = ("busctl --user get-property org.a11y.Bus /org/a11y/bus "
                   "org.a11y.Status IsEnabled")
        self.runner.responses[set_key] = ""
        self.runner.responses[get_key] = "b true"

        result = self.run_json("a11y-enable")

        self.assertTrue(result["verified"])


class CliAuditTests(CliFixture):
    def test_audit_reports_capabilities(self):
        self.runner.responses["grim -s 1 -"] = png(1920, 1080)

        result = self.run_json("audit", "--no-probe", "--no-a11y")

        self.assertTrue(result["ok"])
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["capabilities"]["logical_screen"],
                         [1920, 1080])

    def test_a_critical_failure_exits_1_with_the_rows_intact(self):
        session = Session(env=None, runner=FakeInputRunner(FakeHyprland()),
                          discoverer=FakeDiscoverer(None))
        stdout, stderr = io.StringIO(), io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = Cli(["audit", "--no-probe", "--no-a11y"], session=session,
                       config=self.config).run()

        self.assertEqual(code, 1)
        rows = json.loads(stdout.getvalue())
        self.assertFalse(rows["ok"])
        self.assertIn("session", rows["critical_failed"])


class CliGlobalTests(CliFixture):
    def test_version(self):
        code, stdout, _ = self.run_cli("--version")

        self.assertEqual(code, 0)
        self.assertIn("computer-control", stdout)

    def test_instance_override_reaches_the_runner(self):
        self.run_json("--instance", "sig_xyz", "pos")

        self.assertEqual(
            self.runner.environments[-1]["HYPRLAND_INSTANCE_SIGNATURE"],
            "sig_xyz")

    def test_display_override_reaches_the_runner(self):
        self.run_json("--display", "wayland-9", "pos")

        self.assertEqual(self.runner.environments[-1]["WAYLAND_DISPLAY"],
                         "wayland-9")


class CliSequenceTests(CliFixture):
    def test_sequence_runs_steps(self):
        steps = json.dumps([["pos"], ["move", "100", "100"]])

        results = self.run_json("sequence", steps)

        self.assertEqual(len(results), 2)
        self.assertTrue(results[1]["verified"])
        self.assertEqual(self.runner.cursorpos(), "100,100")

    def test_sequence_stops_on_a_refusal(self):
        steps = json.dumps([["focus", "class:missing"], ["move", "1", "1"]])

        code, _, stderr = self.run_cli("sequence", steps)

        self.assertEqual(code, 1)
        self.assertIn("ERR[window-not-found]", stderr)
        self.assertFalse(any(call[:2] == ["ydotool", "mousemove"]
                             for call in self.runner.calls))

    def test_sequence_bad_json_refuses(self):
        code, _, stderr = self.run_cli("sequence", "nope")

        self.assertEqual(code, 1)
        self.assertIn("ERR[bad-json]", stderr)

    def test_sequence_bad_step_refuses(self):
        code, _, stderr = self.run_cli("sequence", json.dumps([["move"]]))

        self.assertEqual(code, 1)
        self.assertIn("ERR[bad-args]", stderr)

    def test_sequence_step_dry_run_does_not_act(self):
        steps = json.dumps([["--dry-run", "switch", "2"],
                            ["--dry-run", "move", "100", "100"]])

        results = self.run_json("sequence", steps)

        self.assertTrue(all(result["dry_run"] for result in results))
        self.assertEqual(self.hypr.active_workspace, 1)
        self.assertEqual(self.hypr.dispatch_log, [])
        self.assertFalse(any(call[:2] == ["ydotool", "mousemove"]
                             for call in self.runner.calls))

    def test_sequence_step_plans_name_the_step(self):
        steps = json.dumps([["--dry-run", "switch", "2"],
                            ["--dry-run", "move", "100", "100"]])

        results = self.run_json("sequence", steps)

        self.assertIn("switch", results[0]["argv"])
        self.assertIn("move", results[1]["argv"])
        self.assertNotEqual(results[0]["argv"], results[1]["argv"])

    def test_sequence_step_timeout_is_honoured(self):
        stub = StubWait()
        steps = json.dumps([["--timeout", "0", "wait-window", "class:nope"]])

        with mock.patch.object(Desktop, "wait", stub):
            self.run_json("sequence", steps)

        self.assertEqual(stub.calls[0]["timeout"], 0.0)

    def test_a_steps_timeout_does_not_leak_to_later_steps(self):
        stub = StubWait()
        steps = json.dumps([["--timeout", "7", "wait-window", "class:foot"],
                            ["wait-window", "class:foot"]])

        with mock.patch.object(Desktop, "wait", stub):
            self.run_json("sequence", steps)

        self.assertEqual([call["timeout"] for call in stub.calls], [7.0, 60.0])


class StubFollow:
    """Stands in for ``ActionLog.follow`` so a test can end the stream."""

    def __init__(self, entries):
        self.entries = entries

    def __call__(self, count=10, since=None, poll=0.25):
        yield from self.entries


class StubWait:
    """Records the timeout ``Desktop.wait`` was called with."""

    def __init__(self):
        self.calls = []

    def __call__(self, _desktop, spec="", **kwargs):
        self.calls.append({"spec": spec, **kwargs})
        return {}


class StubInterruptedFollow(StubFollow):
    """Yields what it has, then behaves like Ctrl+C at the terminal."""

    def __call__(self, count=10, since=None, poll=0.25):
        yield from self.entries
        raise KeyboardInterrupt


class ClosedPipe(io.StringIO):
    """A stdout whose reader has gone away, as in ``log --follow | head``."""

    def write(self, text):
        raise BrokenPipeError(32, "Broken pipe")


class CliGlobalFlagTests(CliFixture):
    def test_quiet_suppresses_stdout(self):
        code, stdout, _ = self.run_cli("--quiet", "pos")

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")

    def test_quiet_keeps_refusals_on_stderr(self):
        code, stdout, stderr = self.run_cli("--quiet", "focus",
                                            "class:missing")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ERR[window-not-found]", stderr)

    def test_global_dry_run_plans_without_acting(self):
        result = self.run_json("--dry-run", "switch", "2")

        self.assertTrue(result["dry_run"])
        self.assertEqual(self.hypr.active_workspace, 1)
        self.assertEqual(self.hypr.dispatch_log, [])

    def test_global_dry_run_does_not_move_the_pointer(self):
        before = self.runner.cursorpos()

        result = self.run_json("--dry-run", "move", "500", "300")

        self.assertTrue(result["dry_run"])
        self.assertEqual(self.runner.cursorpos(), before)

    def test_global_dry_run_leaves_reads_alone(self):
        result = self.run_json("--dry-run", "state")

        self.assertIn("active_workspace", result)
        self.assertNotIn("dry_run", result)

    def test_global_dry_run_uses_the_verbs_own_plan(self):
        before = len(self.hypr.clients)

        result = self.run_json("--dry-run", "close-apps",
                               "--kind", "terminal")

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(self.hypr.clients), before)

    def test_global_timeout_bounds_window_waits(self):
        code, _, stderr = self.run_cli("--timeout", "0", "wait-window",
                                       "class:nope")

        self.assertEqual(code, 1)
        self.assertIn("ERR[wait-timeout]", stderr)

    def test_global_timeout_reaches_the_verb(self):
        stub = StubWait()

        with mock.patch.object(Desktop, "wait", stub):
            self.run_json("--timeout", "3", "wait-window", "class:foot")

        self.assertEqual(stub.calls[0]["timeout"], 3.0)

    def test_the_verbs_own_timeout_wins(self):
        stub = StubWait()

        with mock.patch.object(Desktop, "wait", stub):
            self.run_json("--timeout", "5", "wait-window", "class:nope",
                          "--timeout", "0")

        self.assertEqual(stub.calls[0]["timeout"], 0.0)

    def test_a_global_flag_after_the_verb_is_bad_args(self):
        code, stdout, stderr = self.run_cli("switch", "2", "--dry-run")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ERR[bad-args]", stderr)
        self.assertIn("before the VERB", stderr)
        self.assertEqual(self.hypr.dispatch_log, [])

    def test_dry_run_capture_with_out_writes_no_file(self):
        target = os.path.join(self._tmp.name, "shot.png")

        result = self.run_json("--dry-run", "screen", "--out", target)

        self.assertTrue(result["dry_run"])
        self.assertFalse(os.path.exists(target))

    def test_dry_run_clipboard_get_with_out_writes_no_file(self):
        target = os.path.join(self._tmp.name, "clip.txt")

        result = self.run_json("--dry-run", "clipboard", "get",
                               "--out", target)

        self.assertTrue(result["dry_run"])
        self.assertFalse(os.path.exists(target))

    def test_dry_run_audit_does_not_probe(self):
        self.runner.responses["grim -s 1 -"] = png(1920, 1080)

        result = self.run_json("--dry-run", "audit", "--no-a11y")

        self.assertTrue(result["dry_run"])
        self.assertFalse(any(call[:2] == ["ydotool", "mousemove"]
                             for call in self.runner.calls))

    def test_dry_run_type_redacts_the_text(self):
        result = self.run_json("--dry-run", "type", "hunter2-SECRET")

        self.assertTrue(result["dry_run"])
        self.assertIn("<redacted>", result["argv"])
        self.assertNotIn("hunter2-SECRET", result["argv"])

    def test_quiet_capture_without_out_refuses(self):
        code, stdout, stderr = self.run_cli("--quiet", "screen")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ERR[bad-args]", stderr)
        self.assertIn("--out", stderr)

    def test_quiet_follow_refuses(self):
        with mock.patch.object(ActionLog, "follow", StubFollow([])):
            code, stdout, stderr = self.run_cli("--quiet", "log", "--follow")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("ERR[bad-args]", stderr)


class CliLogFollowTests(CliFixture):
    ENTRIES = [{"ts": "2026-01-01T00:00:00", "action": "move",
                "ok": True},
               {"ts": "2026-01-01T00:00:01", "action": "state",
                "ok": True}]

    def test_follow_prints_one_entry_per_line(self):
        with mock.patch.object(ActionLog, "follow", StubFollow(self.ENTRIES)):
            code, stdout, _ = self.run_cli("log", "--follow")

        lines = [line for line in stdout.splitlines() if line.strip()]
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertIn('"action":"move"', lines[0])

    def test_follow_text_mode(self):
        with mock.patch.object(ActionLog, "follow", StubFollow(self.ENTRIES)):
            code, stdout, _ = self.run_cli("log", "--follow", "--text")

        self.assertEqual(code, 0)
        self.assertIn("move ok", stdout)

    def test_follow_interrupted_exits_130_without_claiming_success(self):
        stub = StubInterruptedFollow(self.ENTRIES)

        with mock.patch.object(ActionLog, "follow", stub):
            code, _, _ = self.run_cli("log", "--follow")

        self.assertEqual(code, 130)
        path = os.path.join(self._tmp.name, "actions.jsonl")
        written = ""
        if os.path.exists(path):
            with open(path) as handle:
                written = handle.read()
        self.assertNotIn('"action":"log"', written,
                         "an interrupted tail must not log a clean success")

    def test_follow_survives_a_closed_pipe(self):
        pipe = ClosedPipe()

        with mock.patch.object(ActionLog, "follow", StubFollow(self.ENTRIES)):
            with redirect_stdout(pipe), redirect_stderr(io.StringIO()):
                code = Cli(["log", "--follow"], session=self.session,
                           config=self.config).run()

        self.assertEqual(code, 0)


class CliRefusalTests(CliFixture):
    def test_refusal_prints_the_code(self):
        code, _, stderr = self.run_cli("focus", "class:missing")

        self.assertEqual(code, 1)
        self.assertIn("ERR[window-not-found]", stderr)

    def test_a_failing_device_is_a_ydotool_refusal(self):
        self.runner.fail_prefixes.add("ydotool")

        code, _, stderr = self.run_cli("move", "10", "20")

        self.assertEqual(code, 1)
        self.assertIn("ERR[ydotool]", stderr)

    def test_a_runner_that_raises_is_a_subprocess_refusal(self):
        self.runner.raise_oserror.add("hyprctl")

        code, _, stderr = self.run_cli("pos")

        self.assertEqual(code, 1)
        self.assertIn("ERR[subprocess-failed]", stderr)

    def test_a_directory_under_a_file_is_create_failed(self):
        blocker = os.path.join(self._tmp.name, "a-file")
        with open(blocker, "w") as handle:
            handle.write("x")

        code, _, stderr = self.run_cli("dir", os.path.join(blocker, "sub"),
                                       "--create")

        self.assertEqual(code, 1)
        self.assertIn("ERR[create-failed]", stderr)

    def test_close_on_an_empty_desktop_is_a_refusal(self):
        self.hypr.empty_active = True

        code, _, stderr = self.run_cli("close")

        self.assertEqual(code, 1)
        self.assertIn("ERR[empty-desktop]", stderr)

    def test_log_refuses_a_non_regular_log(self):
        config = Config(env={"CCC_LOG": self._tmp.name})    # a directory
        stdout, stderr = io.StringIO(), io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = Cli(["log", "1"], session=self.session,
                       config=config).run()

        self.assertEqual(code, 1)
        self.assertIn("ERR[no-file]", stderr.getvalue())

    def test_the_global_timeout_reaches_the_verification_budget(self):
        self.hypr.honor_dispatches = False
        started = time.time()

        code, _, stderr = self.run_cli("--timeout", "1", "switch", "2")

        self.assertEqual(code, 1, stderr)
        self.assertIn("ERR[switch-not-verified]", stderr)
        self.assertGreaterEqual(time.time() - started, 0.9,
                                "the resolved timeout must set the poll budget")

    def test_unknown_verb_is_usage(self):
        code, _, stderr = self.run_cli("nope")

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
