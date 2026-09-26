"""Tests for screen capture: PNG bytes, logical-size regions, opt-in saving."""

import os
import struct
import tempfile
import time
import unittest

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.screen import Screen  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland, FakeRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}

ACTIVE = {"address": "0xaaa1", "title": "foot", "class": "foot",
          "at": [100, 200], "size": [800, 600]}


def png(width: int, height: int, payload: bytes = b"x") -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + payload)


def make_screen(responses=None, returncodes=None):
    runner = FakeRunner(responses=responses, returncodes=returncodes)
    session = Session(env=ENV, runner=runner, discoverer=FakeDiscoverer(ENV))
    return Screen(session), runner


class ScreenCaptureTests(unittest.TestCase):
    GRIM = "grim -c -s 1 -"

    def test_full_renders_logical_size_with_the_cursor(self):
        data = png(1920, 1080)
        screen, runner = make_screen(responses={self.GRIM: data})

        self.assertEqual(screen.full(), data)
        self.assertTrue(runner.binary_flags[-1])

    def test_no_cursor_drops_the_flag(self):
        screen, runner = make_screen(responses={"grim -s 1 -": b"png"})

        self.assertEqual(screen.full(no_cursor=True), b"png")
        self.assertEqual(runner.calls[-1], ["grim", "-s", "1", "-"])

    def test_region_uses_the_geometry_flag(self):
        screen, runner = make_screen(
            responses={"grim -c -s 1 -g 10,20 300x400 -": b"png"})

        self.assertEqual(screen.region(10, 20, 300, 400), b"png")
        self.assertEqual(runner.calls[-1],
                         ["grim", "-c", "-s", "1", "-g", "10,20 300x400", "-"])

    def test_active_captures_the_focused_window(self):
        hypr = FakeHyprland(clients=[dict(ACTIVE)])

        screen = make_active_screen(
            hypr, {"grim -c -s 1 -g 100,200 800x600 -": png(800, 600)})

        self.assertEqual(screen.active(), png(800, 600))

    def test_active_without_a_window_refuses(self):
        hypr = FakeHyprland(clients=[])
        hypr.active_address = ""
        session = Session(env=ENV, runner=hypr, discoverer=FakeDiscoverer(ENV))

        with self.assertRaises(ControlError) as caught:
            Screen(session).active()

        self.assertEqual(caught.exception.code, "window-not-found")

    def test_grim_failure_is_a_capture_refusal(self):
        screen, _ = make_screen(returncodes={self.GRIM: 1})

        with self.assertRaises(ControlError) as caught:
            screen.full()

        self.assertEqual(caught.exception.code, "capture")


class ScreenSaveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_writes_and_reports_the_size(self):
        screen, _ = make_screen()

        result = screen.save(png(640, 480), self.dir, "screen", keep=5)

        self.assertTrue(os.path.isfile(result["path"]))
        self.assertEqual((result["width"], result["height"]), (640, 480))

    def test_save_prunes_the_oldest(self):
        screen, _ = make_screen()
        for index in range(4):
            path = os.path.join(self.dir, f"screen_100{index}.png")
            with open(path, "wb") as handle:
                handle.write(png(10, 10))
            os.utime(path, (index, index))

        screen.save(png(20, 20), self.dir, "screen", keep=2)

        remaining = sorted(os.listdir(self.dir))
        self.assertEqual(len(remaining), 2)

    def test_a_non_png_is_refused(self):
        screen, _ = make_screen()

        with self.assertRaises(ControlError) as caught:
            screen.save(b"not a png", self.dir)

        self.assertEqual(caught.exception.code, "capture")


class _TwoCommandRunner:
    """Routes hyprctl to one double and everything else to another."""

    def __init__(self, hyprctl, other):
        self.hyprctl = hyprctl
        self.other = other

    def run(self, argv, env=None, timeout: float = 15, binary: bool = False,
            input=None):
        if argv and argv[0] == "hyprctl":
            return self.hyprctl.run(argv, env=env, timeout=timeout,
                                    binary=binary, input=input)
        return self.other.run(argv, env=env, timeout=timeout, binary=binary,
                              input=input)

    def spawn(self, argv, env=None, input=None) -> int:
        return self.other.spawn(argv, env=env, input=input)


def make_active_screen(hyprland, responses):
    runner = _TwoCommandRunner(hyprland, FakeRunner(responses=responses))
    session = Session(env=ENV, runner=runner, discoverer=FakeDiscoverer(ENV))
    return Screen(session)


if __name__ == "__main__":
    unittest.main()
