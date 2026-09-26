"""Live checks against the real desktop. Opt-in: ``CCC_LIVE=1``.

Each check reads its effect back. A missing prerequisite (no session, no
terminal to host a scratch window) is a SKIP with its reason, never a pass.
Scratch windows are closed and temp files removed in ``finally``.
"""

import os
import shlex
import socket
import tempfile
import time
import unittest

from computer_control_cli.apps import Apps  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input  # pi-lens-ignore: reportMissingImports
from computer_control_cli.screen import Screen  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports
from computer_control_cli.system import SystemInfo  # pi-lens-ignore: reportMissingImports

LIVE = os.environ.get("CCC_LIVE") == "1"


@unittest.skipUnless(LIVE, "set CCC_LIVE=1 to run against the real desktop")
class LiveDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session()
        try:
            cls.session.env()
        except ControlError as error:
            raise unittest.SkipTest(f"no live session: {error.message}")
        cls.desktop = Desktop(cls.session)
        cls.input = Input(cls.session)
        cls.screen = Screen(cls.session)
        cls.system = SystemInfo(cls.session)
        cls.apps = Apps(cls.session, cls.desktop)

    # ------------------------------------------------------------- reads
    def test_state_reads_the_real_desktop(self):
        state = self.desktop.state()

        self.assertIsInstance(state["active_workspace"], int)
        self.assertIsInstance(state["windows"], list)
        self.assertTrue(all(window.address for window in state["windows"]))

    def test_system_info(self):
        info = self.system.sysinfo()

        self.assertEqual(info["hostname"], socket.gethostname())
        self.assertGreater(info["uptime_s"], 0)
        self.assertGreater(info["memory"]["total_kb"], 0)

    def test_wait_window_finds_an_existing_window(self):
        windows = self.desktop.windows()
        if not windows:
            self.skipTest("the desktop has no window to match")

        result = self.desktop.wait(f"address:{windows[0].address}", timeout=2)

        self.assertTrue(result.verified)

    def test_screen_capture_is_a_png(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = self.screen.save(self.screen.full(), directory, "live")

            self.assertTrue(os.path.isfile(saved["path"]))
            self.assertGreater(saved["width"], 0)
            self.assertGreater(saved["height"], 0)

    def test_close_apps_dry_run_changes_nothing(self):
        before = len(self.desktop.windows())

        self.desktop.close_apps("terminal", dry_run=True)

        self.assertEqual(len(self.desktop.windows()), before)

    # ------------------------------------------------------------- pointer
    def test_move_round_trip(self):
        origin = self.input.pos()
        monitor = next((m for m in self.desktop.monitors()
                        if m.contains(origin["x"], origin["y"])), None)
        if monitor is None:
            self.skipTest("the cursor is not on a monitor")
        target = (monitor.x + monitor.w // 2, monitor.y + monitor.h // 2)
        try:
            result = self.input.move(*target)
            self.assertTrue(result.verified)
            landed = self.input.pos()
            self.assertLessEqual(max(abs(landed["x"] - target[0]),
                                     abs(landed["y"] - target[1])), 2)
        finally:
            self.input.move(origin["x"], origin["y"])

    def test_scroll_emits_wheel_events(self):
        result = self.input.scroll("down", 1)

        self.assertFalse(result.verified)  # honest: no read-back exists

    # ------------------------------------------------------------ keyboard
    def test_clipboard_round_trip(self):
        original = self.system.clipboard_get()
        marker = f"ccc-live-{os.getpid()}"
        try:
            self.system.clipboard_set(marker)
            self.assertEqual(self.system.clipboard_get(), marker)
        finally:
            self.system.clipboard_set(original)

    def test_type_and_enter_reach_a_scratch_window(self):
        launcher = self.apps.detect_terminal()
        if launcher is None:
            self.skipTest("no terminal launcher on PATH")
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "typed.txt")
            script = ("read line; printf '%s' \"$line\" > "
                      + shlex.quote(out) + "; sleep 5")
            window = None
            try:
                result = self.apps.launch([launcher, "-e", "sh", "-c", script],
                                          wait=True, timeout=10)
                window = result.details["window"]["address"]
                self.desktop.focus(f"address:{window}")
                self.input.type_text("hello-live")
                self.input.key("return")
                deadline = time.time() + 5
                while time.time() < deadline and not os.path.exists(out):
                    time.sleep(0.1)
                self.assertTrue(os.path.exists(out),
                                "the typed line never reached the window")
                with open(out) as handle:
                    self.assertEqual(handle.read(), "hello-live")
            finally:
                if window:
                    try:
                        self.desktop.close(f"address:{window}")
                    except ControlError:
                        pass
                for window_row in self.desktop.windows():
                    if "sleep 5" in window_row.title:
                        try:
                            self.desktop.close(f"address:{window_row.address}")
                        except ControlError:
                            pass


if __name__ == "__main__":
    unittest.main()
