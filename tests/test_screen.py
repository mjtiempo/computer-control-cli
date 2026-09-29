"""Tests for screen capture: PNG bytes, logical-size regions, opt-in saving."""

import os
import stat
import struct
import tempfile
import unittest

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.models import Window  # pi-lens-ignore: reportMissingImports
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

    def test_a_capture_is_private(self):
        screen, _ = make_screen()

        result = screen.save(png(10, 10), self.dir)

        mode = stat.S_IMODE(os.stat(result["path"]).st_mode)
        self.assertEqual(mode, 0o600,
                         "a desktop capture is not world-readable")

    def test_prune_leaves_png_files_it_did_not_write(self):
        foreign = os.path.join(self.dir, "screen_desktop.png")
        first = os.path.join(self.dir, "screen_1000.png")
        second = os.path.join(self.dir, "screen_2000.png")
        for path, when in ((foreign, 1), (first, 2), (second, 3)):
            with open(path, "wb") as handle:
                handle.write(png(10, 10))
            os.utime(path, (when, when))

        Screen._prune(self.dir, "screen", keep=2)

        self.assertEqual(sorted(os.listdir(self.dir)),
                         ["screen_1000.png", "screen_2000.png",
                          "screen_desktop.png"])


class ScreenWindowAndMonitorTests(unittest.TestCase):
    def test_capture_window_uses_its_rect(self):
        window = Window.from_hypr(ACTIVE)
        assert window is not None
        screen, runner = make_screen(
            responses={"grim -c -s 1 -g 100,200 800x600 -": png(800, 600)})

        self.assertEqual(screen.capture_window(window), png(800, 600))
        self.assertEqual(runner.calls[-1],
                         ["grim", "-c", "-s", "1", "-g",
                          "100,200 800x600", "-"])

    def test_monitor_by_exact_name(self):
        hypr = FakeHyprland()
        screen, runner = make_screen_for(
            hypr, {"grim -c -s 1 -g 0,0 1920x1080 -": png(1920, 1080)})

        self.assertEqual(screen.monitor("eDP-1"), png(1920, 1080))

    def test_monitor_unknown_refuses(self):
        screen, _ = make_screen_for(FakeHyprland(), {})

        with self.assertRaises(ControlError) as caught:
            screen.monitor("NOPE-1")

        self.assertEqual(caught.exception.code, "monitor-not-found")

    def test_monitor_ambiguous_refuses(self):
        hypr = FakeHyprland(monitors=[
            {"name": "DP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
             "scale": 1.0, "focused": True},
            {"name": "DP-2", "x": 1920, "y": 0, "width": 1920, "height": 1080,
             "scale": 1.0, "focused": False}])
        screen, _ = make_screen_for(hypr, {})

        with self.assertRaises(ControlError) as caught:
            screen.monitor("DP-")

        self.assertEqual(caught.exception.code, "ambiguous-monitor")


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

    def spawn(self, argv, env=None, input=None, binary: bool = False) -> int:
        return self.other.spawn(argv, env=env, input=input, binary=binary)


def make_active_screen(hyprland, responses):
    runner = _TwoCommandRunner(hyprland, FakeRunner(responses=responses))
    session = Session(env=ENV, runner=runner, discoverer=FakeDiscoverer(ENV))
    return Screen(session)


def make_screen_for(hyprland, responses):
    """(Screen, runner) for a session whose hyprctl goes to ``hyprland``."""
    runner = _TwoCommandRunner(hyprland, FakeRunner(responses=responses))
    session = Session(env=ENV, runner=runner, discoverer=FakeDiscoverer(ENV))
    return Screen(session), runner


if __name__ == "__main__":
    unittest.main()
