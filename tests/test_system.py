"""Tests for system info and the clipboard."""

import os
import tempfile
import unittest

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports
from computer_control_cli.system import SystemInfo  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}

UPTIME = "12345.67 8901.23\n"
LOADAVG = "0.10 0.20 0.30 1/234 5678\n"
MEMINFO = "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n"


class SystemInfoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proc = self._tmp.name
        self._write("uptime", UPTIME)
        self._write("loadavg", LOADAVG)
        self._write("meminfo", MEMINFO)
        self.runner = FakeRunner()
        self.session = Session(env=ENV, runner=self.runner,
                               discoverer=FakeDiscoverer(ENV))
        self.system = SystemInfo(self.session, proc=self.proc)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        with open(os.path.join(self.proc, name), "w") as handle:
            handle.write(text)

    def test_sysinfo_reads_cpu_uptime_load_and_memory(self):
        info = self.system.sysinfo()

        self.assertEqual(info["uptime_s"], 12345)
        self.assertEqual(info["load"], {"1": 0.1, "5": 0.2, "15": 0.3})
        self.assertEqual(info["memory"]["total_kb"], 16384000)
        self.assertEqual(info["memory"]["available_kb"], 8192000)
        self.assertGreaterEqual(info["cpu_count"], 1)
        self.assertTrue(info["hostname"])
        self.assertTrue(info["kernel"])
        self.assertIn("disk", info)

    def test_missing_proc_files_do_not_crash(self):
        os.unlink(os.path.join(self.proc, "uptime"))
        os.unlink(os.path.join(self.proc, "loadavg"))
        os.unlink(os.path.join(self.proc, "meminfo"))

        info = self.system.sysinfo()

        self.assertEqual(info["uptime_s"], 0)
        self.assertEqual(info["load"], {"1": 0.0, "5": 0.0, "15": 0.0})
        self.assertEqual(info["memory"], {"total_kb": 0, "available_kb": 0})

    def test_clipboard_get(self):
        self.runner.responses["wl-paste --no-newline"] = "copied text"

        self.assertEqual(self.system.clipboard_get(), "copied text")

    def test_clipboard_get_failure_refuses(self):
        self.runner.returncodes["wl-paste --no-newline"] = 1

        with self.assertRaises(ControlError) as caught:
            self.system.clipboard_get()

        self.assertEqual(caught.exception.code, "clipboard")

    def test_clipboard_set_pipes_the_text(self):
        self.system.clipboard_set("hello world")

        self.assertEqual(self.runner.spawned[-1][0], ["wl-copy"])
        self.assertEqual(self.runner.spawned[-1][1], "hello world")

    def test_clipboard_set_missing_binary_refuses(self):
        self.runner.missing.add("wl-copy")

        with self.assertRaises(ControlError) as caught:
            self.system.clipboard_set("hello")

        self.assertEqual(caught.exception.code, "not-found")


if __name__ == "__main__":
    unittest.main()
