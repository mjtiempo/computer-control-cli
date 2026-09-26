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
MEMINFO = ("MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n"
           "SwapTotal:       4096000 kB\nSwapFree:        3072000 kB\n")
CPUINFO = "processor\t: 0\nmodel name\t: AMD Ryzen Test 9000\n"
NETDEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1000       10    0    0    0     0          0         0     1000       10    0    0    0     0       0          0
  eth0:  204800     1200    0    0    0     0          0         0   102400      900    0    0    0     0       0          0
"""


class SystemInfoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proc = self._tmp.name
        self._write("uptime", UPTIME)
        self._write("loadavg", LOADAVG)
        self._write("meminfo", MEMINFO)
        self._write("cpuinfo", CPUINFO)
        os.makedirs(os.path.join(self.proc, "net"))
        self._write(os.path.join("net", "dev"), NETDEV)
        self._write(os.path.join("1234", "comm"), "bash\n")
        self.sys = os.path.join(self._tmp.name, "sys")
        self._sys_file("class/net/eth0/operstate", "up\n")
        self._sys_file("class/net/lo/operstate", "unknown\n")
        self._sys_file("class/power_supply/BAT0/capacity", "87\n")
        self._sys_file("class/power_supply/BAT0/status", "Discharging\n")
        self._sys_file("class/thermal/thermal_zone0/temp", "42000\n")
        self.runner = FakeRunner()
        self.session = Session(env=ENV, runner=self.runner,
                               discoverer=FakeDiscoverer(ENV))
        self.system = SystemInfo(self.session, proc=self.proc, sys=self.sys)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        path = os.path.join(self.proc, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)

    def _sys_file(self, name, text):
        path = os.path.join(self.sys, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
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
        for name in ("uptime", "loadavg", "meminfo", "cpuinfo"):
            os.unlink(os.path.join(self.proc, name))

        info = self.system.sysinfo()

        self.assertEqual(info["uptime_s"], 0)
        self.assertEqual(info["load"], {"1": 0.0, "5": 0.0, "15": 0.0})
        self.assertEqual(info["memory"], {"total_kb": 0, "available_kb": 0})
        self.assertEqual(info["cpu_model"], "")
        self.assertEqual(info["swap"], {"total_kb": 0, "free_kb": 0})

    def test_cpu_model_and_swap(self):
        info = self.system.sysinfo()

        self.assertEqual(info["cpu_model"], "AMD Ryzen Test 9000")
        self.assertEqual(info["swap"], {"total_kb": 4096000,
                                          "free_kb": 3072000})

    def test_process_count(self):
        self.assertGreaterEqual(self.system.sysinfo()["processes"], 1)

    def test_network_interfaces(self):
        network = {row["name"]: row for row in self.system.sysinfo()["network"]}

        self.assertEqual(network["eth0"]["state"], "up")
        self.assertEqual(network["eth0"]["rx_bytes"], 204800)
        self.assertEqual(network["eth0"]["tx_bytes"], 102400)
        self.assertEqual(network["lo"]["state"], "unknown")

    def test_battery_and_thermal(self):
        info = self.system.sysinfo()

        self.assertEqual(info["battery"], [{"name": "BAT0", "capacity": 87,
                                             "status": "Discharging"}])
        self.assertEqual(info["thermal"], [{"zone": "thermal_zone0",
                                             "temp_c": 42.0}])

    def test_missing_sys_tree_is_empty(self):
        empty = os.path.join(self._tmp.name, "empty")
        info = SystemInfo(self.session, proc=empty, sys=empty).sysinfo()

        self.assertEqual(info["network"], [])
        self.assertEqual(info["battery"], [])
        self.assertEqual(info["thermal"], [])
        self.assertEqual(info["processes"], 0)

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

    def test_clipboard_primary_selection(self):
        self.runner.clipboard = "primary text"

        self.assertEqual(self.system.clipboard_get(primary=True),
                         "primary text")
        self.system.clipboard_set("new primary", primary=True)
        self.assertEqual(self.runner.spawned[-1][0], ["wl-copy", "--primary"])
        self.assertEqual(self.system.clipboard_get(primary=True),
                         "new primary")

    def test_clipboard_clear(self):
        self.runner.clipboard = "something"

        self.system.clipboard_clear()

        self.assertEqual(self.runner.calls[-1], ["wl-copy", "--clear"])

    def test_clipboard_clear_primary(self):
        self.system.clipboard_clear(primary=True)

        self.assertEqual(self.runner.calls[-1],
                         ["wl-copy", "--primary", "--clear"])


if __name__ == "__main__":
    unittest.main()
