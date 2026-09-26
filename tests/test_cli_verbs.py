"""Tests for the CLI verbs: JSON on stdout, ERR[code] on stderr, exit codes."""

import io
import json
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from computer_control_cli.cli import Cli  # pi-lens-ignore: reportMissingImports
from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports
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

    def test_move_window(self):
        result = self.run_json("move-window", "class:foot", "2")

        self.assertTrue(result["verified"])

    def test_wait_window_timeout_is_a_refusal(self):
        code, _, stderr = self.run_cli("wait-window", "class:nope",
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

    def test_clipboard_set_and_get(self):
        self.run_json("clipboard", "set", "hello")
        self.assertEqual(self.runner.spawned[-1], (["wl-copy"], "hello"))
        self.assertEqual(self.run_json("clipboard", "get"), {"text": "hello"})

    def test_type_is_redacted_in_the_log(self):
        self.run_json("type", "hunter2")

        code, stdout, _ = self.run_cli("log", "1")
        entry = json.loads(stdout)[0]
        self.assertNotIn("text", entry)
        self.assertEqual(entry["text_chars"], 7)


class CliRefusalTests(CliFixture):
    def test_refusal_prints_the_code(self):
        code, _, stderr = self.run_cli("focus", "class:missing")

        self.assertEqual(code, 1)
        self.assertIn("ERR[window-not-found]", stderr)

    def test_unknown_verb_is_usage(self):
        code, _, stderr = self.run_cli("nope")

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
