"""Live CLI battery: every verb through the real command surface.

Opt-in with ``CCC_LIVE=1`` and only for a real desktop session. Each check
runs ``python3 -m computer_control_cli …`` as a subprocess, reads its effect
back (JSON, a file, or a pixel read of the window list), and restores what it
touched: scratch windows are closed, the cursor and clipboard are restored,
and temp files are removed.

A missing prerequisite (no session, no terminal to host a scratch window) is
a SKIP with its reason, never a pass.
"""

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any

LIVE = os.environ.get("CCC_LIVE") == "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMINALS = ("foot", "ghostty", "alacritty", "kitty", "wezterm", "xterm")


class CliRunner:
    """Run the checked-out CLI as a subprocess."""

    def __init__(self, root: str = ROOT, python: str = sys.executable):
        self.root = root
        self.python = python

    def run(self, *argv, timeout: float = 120):
        proc = subprocess.run([self.python, "-m", "computer_control_cli",
                               *argv], cwd=self.root, capture_output=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr


@unittest.skipUnless(LIVE, "set CCC_LIVE=1 to run against the real desktop")
class LiveCliTests(unittest.TestCase):
    """Every verb, driven through the CLI, checked against the desktop."""

    @classmethod
    def setUpClass(cls):
        cls.cli = CliRunner()
        code, _, stderr = cls.cli.run("windows")
        if code != 0:
            raise unittest.SkipTest(
                f"no live session: {stderr.decode(errors='replace').strip()}")
        cls.launcher = next((name for name in TERMINALS
                             if shutil.which(name)), None)
        cls.tmp = tempfile.mkdtemp(prefix="ccc-live-cli-")
        cls.typed_file = os.path.join(cls.tmp, "typed.txt")
        cls.original_pos = cls.json_of(cls.cli.run("pos"))
        cls.original_workspace = cls.json_of(
            cls.cli.run("state"))["active_workspace"]
        try:
            cls.original_clipboard = cls.json_of(
                cls.cli.run("clipboard", "get"))["text"]
        except Exception:                                   # noqa: BLE001
            cls.original_clipboard = ""
        try:
            cls.original_primary = cls.json_of(
                cls.cli.run("clipboard", "get", "--primary"))["text"]
        except Exception:                                   # noqa: BLE001
            cls.original_primary = ""
        cls.scratch = None
        if cls.launcher is not None:
            script = (f"read line; printf '%s' \"$line\" > "
                      f"{shlex.quote(cls.typed_file)}; sleep 600")
            result = cls.json_of(cls.cli.run(
                "launch", cls.launcher, "-e", "sh", "-c", script))
            cls.scratch = result["window"]

    @classmethod
    def tearDownClass(cls):
        for address in cls.open_scratch_addresses():
            run = cls.cli.run("close", f"address:{address}")
            if run[0] != 0:
                break
        if cls.original_pos:
            cls.cli.run("move", str(cls.original_pos["x"]),
                        str(cls.original_pos["y"]))
        if cls.original_clipboard:
            cls.cli.run("clipboard", "set", cls.original_clipboard)
        else:
            cls.cli.run("clipboard", "clear")
        if cls.original_primary:
            cls.cli.run("clipboard", "set", cls.original_primary, "--primary")
        else:
            cls.cli.run("clipboard", "clear", "--primary")
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def json_of(run) -> Any:
        code, out, stderr = run
        if code != 0:
            raise AssertionError(
                f"CLI refused ({code}): {stderr.decode(errors='replace')}")
        return json.loads(out) if out.strip() else None

    def ok(self, *argv) -> Any:
        return self.json_of(self.cli.run(*argv))

    def refuse(self, *argv):
        code, _, stderr = self.cli.run(*argv)
        self.assertEqual(code, 1, stderr.decode(errors="replace"))
        return stderr.decode(errors="replace")

    def need_scratch(self):
        if self.scratch is None:
            self.skipTest("no terminal launcher on PATH")
        return self.scratch

    def new_scratch(self, title="", script="sleep 600", workspace=0):
        argv = [self.launcher]
        if title:
            argv.append(f"--title={title}")
        argv += ["-e", "sh", "-c", script]
        args = ["launch"]
        if workspace:
            args += ["--workspace", str(workspace)]
        result = self.ok(*args + argv)
        self.addCleanup(self.close_window, result["window"]["address"])
        return result["window"]

    def close_window(self, address):
        if not address:
            return
        self.cli.run("close", f"address:{address}")

    @classmethod
    def open_scratch_addresses(cls):
        addresses = []
        code, out, _ = cls.cli.run("windows")
        if code == 0:
            for window in json.loads(out):
                if any(needle in str(window.get("title", ""))
                       or needle in str(window.get("class", ""))
                       for needle in ("ccc-", "sleep 600", "read line")):
                    addresses.append(window["address"])
        if cls.scratch:
            addresses.append(cls.scratch["address"])
        return addresses

    def window_row(self, address):
        for window in self.ok("windows"):
            if window["address"].lower() == address.lower():
                return window
        return None

    # --------------------------------------------------------------- reads
    def test_state_windows_active_workspaces_monitors(self):
        state = self.ok("state")
        self.assertIsInstance(state["active_workspace"], int)

        windows = self.ok("windows")
        self.assertIsInstance(windows, list)

        active = self.ok("active")
        self.assertTrue(active is None or "address" in active)

        self.assertIsInstance(self.ok("workspaces"), list)
        monitors = self.ok("monitors")
        self.assertTrue(monitors)
        self.assertTrue(all("name" in monitor for monitor in monitors))

    def test_system_info(self):
        info = self.ok("system")

        self.assertEqual(info["hostname"], socket.gethostname())
        self.assertGreater(info["uptime_s"], 0)
        self.assertGreater(info["memory"]["total_kb"], 0)
        self.assertIn("network", info)
        self.assertIn("thermal", info)

    def test_pos_move_round_trip(self):
        start = self.ok("pos")
        monitor = next((m for m in self.ok("monitors")
                        if m["x"] <= start["x"] < m["x"] + m["w"]), None)
        if monitor is None:
            self.skipTest("the cursor is not on a monitor")
        target = (monitor["x"] + monitor["w"] // 2,
                  monitor["y"] + monitor["h"] // 2)
        try:
            result = self.ok("move", str(target[0]), str(target[1]))
            self.assertTrue(result["verified"])
            landed = self.ok("pos")
            self.assertLessEqual(max(abs(landed["x"] - target[0]),
                                     abs(landed["y"] - target[1])), 2)
        finally:
            self.cli.run("move", str(start["x"]), str(start["y"]))

    def test_apps_which_dir(self):
        entries = self.ok("apps")
        self.assertIsInstance(entries, list)
        self.assertTrue(any(entry.get("kind") == "desktop" for entry in entries))

        self.assertTrue(self.ok("which", "sh")["path"].endswith("sh"))
        self.refuse("which", "definitely-not-a-real-program-xyz")

        self.assertTrue(self.ok("dir", self.tmp)["existed"])
        fresh = os.path.join(self.tmp, "made", "nested")
        result = self.ok("dir", fresh, "--create")
        self.assertTrue(result["created"])
        self.assertTrue(os.path.isdir(fresh))

    def test_log_records_the_actions(self):
        entries = self.ok("log", "5")

        self.assertIsInstance(entries, list)
        self.assertTrue(entries)
        self.assertIn("action", entries[0])

    # -------------------------------------------------------------- screen
    def test_screen_region_monitor_and_window(self):
        window = self.need_scratch()
        full = self.ok("screen", "--out", self.tmp)
        self.assertGreater(full["width"], 0)

        region = self.ok("region", "0", "0", "32", "32", "--out", self.tmp)
        self.assertEqual((region["width"], region["height"]), (32, 32))

        monitor = self.ok("monitors")[0]["name"]
        shot = self.ok("screen", "--monitor", monitor, "--out", self.tmp)
        self.assertGreater(shot["width"], 0)

        self.ok("active-screen", "--out", self.tmp)
        by_window = self.ok("active-screen", "--window",
                            f"address:{window['address']}", "--out", self.tmp)
        self.assertEqual((by_window["width"], by_window["height"]),
                         (window["geometry"][2], window["geometry"][3]))

    def test_screen_bytes_to_stdout(self):
        code, out, _ = self.cli.run("screen")

        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b"\x89PNG"))

    # ------------------------------------------------------ window actions
    def test_focus(self):
        window = self.need_scratch()

        result = self.ok("focus", f"address:{window['address']}")

        self.assertTrue(result["verified"])
        self.assertEqual(self.ok("active")["address"].lower(),
                         window["address"].lower())

    def test_switch_and_restore(self):
        origin = self.ok("state")["active_workspace"]
        other = next((workspace["id"] for workspace in self.ok("workspaces")
                      if workspace["id"] != origin), None)
        if other is None:
            self.skipTest("the desktop has only one workspace")
        try:
            self.assertTrue(self.ok("switch", str(other))["verified"])
            self.assertEqual(self.ok("state")["active_workspace"], other)
        finally:
            self.ok("switch", str(origin))
        self.assertEqual(self.ok("state")["active_workspace"], origin)

    def test_move_window(self):
        window = self.need_scratch()
        origin = window["workspace"]
        other = next((workspace["id"] for workspace in self.ok("workspaces")
                      if workspace["id"] != origin), None)
        if other is None:
            self.skipTest("the desktop has only one workspace")
        try:
            result = self.ok("move-window", f"address:{window['address']}",
                             str(other))
            self.assertTrue(result["verified"])
            row = self.window_row(window["address"])
            self.assertTrue(row, "the window vanished during move-window")
            self.assertEqual((row or {}).get("workspace"), other)
        finally:
            self.ok("move-window", f"address:{window['address']}", str(origin))

    def test_wait_window_filters(self):
        window = self.need_scratch()

        self.assertTrue(self.ok("wait-window", f"address:{window['address']}",
                                "--timeout", "5")["verified"])
        self.assertTrue(self.ok("wait-window", "--class",
                                window["class"], "--timeout", "5")["verified"])
        self.assertTrue(self.ok("wait-window", "--stable", "2",
                                "--exclude", "0xdead",
                                "--timeout", "5")["verified"])
        if window.get("title"):
            self.assertTrue(self.ok("wait-window", "--title",
                                    window["title"][:10],
                                    "--timeout", "5")["verified"])

        self.assertIn("ERR[wait-timeout]", self.refuse(
            "wait-window", "class:ccc-nothing-xyz", "--timeout", "0"))
        self.assertIn("ERR[wait-timeout]", self.refuse(
            "wait-window", "--new", "--timeout", "0"))

    def test_close_and_wait_gone(self):
        window = self.new_scratch(title="ccc-close-me")

        result = self.ok("close", f"address:{window['address']}")

        self.assertTrue(result["verified"])
        self.assertIsNone(self.window_row(window["address"]))
        self.assertTrue(self.ok("wait-window", f"address:{window['address']}",
                                "--gone", "--timeout", "5")["verified"])

    def test_close_apps(self):
        unique = f"ccc-sweep-{os.getpid()}"
        self.new_scratch(title=unique)

        dry = self.ok("close-apps", "--kind", "all", "--match", unique,
                      "--dry-run")
        self.assertGreaterEqual(dry["count"], 1)

        result = self.ok("close-apps", "--kind", "all", "--match", unique)

        self.assertTrue(result["verified"])
        self.assertEqual(result["remaining"], [])

    def test_launch_workspace_and_app_open(self):
        window = self.new_scratch(workspace=2)
        self.assertEqual(window["workspace"], 2)

        entries = self.ok("apps")
        target = next((entry for entry in entries
                       if entry.get("kind") == "desktop"
                       and self.launcher
                       and self.launcher in str(entry.get("command") or "")
                       and "--server" not in str(entry.get("command") or "")),
                      None)
        if target is None:
            self.skipTest("no desktop entry opens a terminal window here")
        plan = self.ok("app-open", target["name"], "--dry-run")
        self.assertIn("command", plan)
        opened = self.ok("app-open", target["name"], "--timeout", "20")
        self.addCleanup(self.close_window, opened["window"]["address"])
        self.assertTrue(opened["verified"])

    def test_sequence(self):
        start = self.ok("pos")
        steps = json.dumps([["pos"], ["windows"]])
        results = self.ok("sequence", steps)

        self.assertEqual(len(results), 2)
        self.assertIn("x", results[0])

        self.assertIn("ERR[window-not-found]", self.refuse(
            "sequence", json.dumps([["focus", "class:ccc-none"],
                                    ["move", "1", "1"]])))
        self.assertIn("ERR[bad-json]", self.refuse("sequence", "nope"))
        self.assertEqual(self.ok("pos"), start)

    def test_version_and_bogus_instance(self):
        code, out, _ = self.cli.run("--version")
        self.assertEqual(code, 0)
        self.assertIn(b"computer-control", out)

        self.assertIn("ERR[no-session]", self.refuse(
            "--instance", "ccc-not-an-instance", "pos"))

    # ---------------------------------------------------------------- input
    def test_click_and_scroll_and_drag(self):
        window = self.need_scratch()
        center = (window["geometry"][0] + window["geometry"][2] // 2,
                  window["geometry"][1] + window["geometry"][3] // 2)
        try:
            self.assertFalse(self.ok("click", str(center[0]),
                                     str(center[1]))["verified"])
            self.ok("scroll", "down", "1", str(center[0]), str(center[1]))
            self.ok("drag", str(center[0]), str(center[1]),
                    str(center[0] + 40), str(center[1] + 10),
                    "--duration", "50")
            self.assertEqual(self.ok("active")["address"].lower(),
                             window["address"].lower())
        finally:
            self.cli.run("clipboard", "clear", "--primary")

    def test_type_and_key_chord(self):
        window = self.need_scratch()
        self.ok("focus", f"address:{window['address']}")
        marker = f"hello-{os.getpid()}"
        try:
            self.ok("type", marker)
            self.ok("key", "return")
            deadline = time.time() + 5
            while time.time() < deadline and not os.path.exists(self.typed_file):
                time.sleep(0.1)
            self.assertTrue(os.path.exists(self.typed_file),
                            "the typed line never reached the scratch window")
            with open(self.typed_file) as handle:
                self.assertEqual(handle.read(), marker)
        finally:
            if os.path.exists(self.typed_file):
                os.unlink(self.typed_file)

    def test_key_chord_ctrl_d_is_observable(self):
        eof_file = os.path.join(self.tmp, "eof.txt")
        window = self.new_scratch(
            script=f"cat > /dev/null; printf EOF > {shlex.quote(eof_file)}; "
                   f"sleep 600")
        self.ok("focus", f"address:{window['address']}")
        try:
            self.ok("key", "ctrl+d")
            deadline = time.time() + 5
            while time.time() < deadline and not os.path.exists(eof_file):
                time.sleep(0.1)
            self.assertTrue(os.path.exists(eof_file),
                            "ctrl+d never reached the scratch window")
        finally:
            if os.path.exists(eof_file):
                os.unlink(eof_file)

    def test_key_hold_and_release(self):
        self.need_scratch()
        # a throwaway scratch, FOCUSED: a held chord must type its letter
        # there, not into whatever window the developer had focused
        window = self.new_scratch(script="sleep 600")
        self.ok("focus", f"address:{window['address']}")

        self.assertEqual(self.ok("key-down", "shift+a")["state"], "down")
        self.assertEqual(self.ok("key-up", "shift+a")["state"], "up")

    # ------------------------------------------------------------ clipboard
    def test_clipboard_text_and_types_and_clear(self):
        self.assertTrue(self.ok("clipboard", "set", "ccc-live")["ok"])
        self.assertEqual(self.ok("clipboard", "get")["text"], "ccc-live")

        types = self.ok("clipboard", "types")
        self.assertIsInstance(types, list)

        self.ok("clipboard", "clear")
        self.assertEqual(self.ok("clipboard", "get")["text"], "")

    def test_clipboard_primary(self):
        self.ok("clipboard", "set", "primary-live", "--primary")

        self.assertEqual(self.ok("clipboard", "get", "--primary")["text"],
                         "primary-live")
        self.ok("clipboard", "clear", "--primary")

    def test_clipboard_image_round_trip(self):
        small = os.path.join(self.tmp, "small.png")
        self.ok("region", "0", "0", "48", "48", "--out", self.tmp)
        # find the capture region_*  the region verb just wrote
        candidates = sorted(
            (os.path.join(self.tmp, name) for name in os.listdir(self.tmp)
             if name.startswith("region_")), key=os.path.getmtime)
        self.assertTrue(candidates, "region wrote no capture")
        shutil.copyfile(candidates[-1], small)

        self.ok("clipboard", "set", "--file", small, "--type", "image/png")
        out = os.path.join(self.tmp, "back.png")
        got = self.ok("clipboard", "get", "--type", "image/png", "--out", out)

        with open(small, "rb") as handle:
            original = handle.read()
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(), original)
        self.assertEqual(got["bytes"], len(original))

    # ---------------------------------------------------------- audit/a11y
    def test_audit(self):
        result = self.ok("audit", "--no-a11y")

        self.assertTrue(result["ok"], result["critical_failed"])
        self.assertIn("capabilities", result)

    def test_a11y(self):
        enabled = self.ok("a11y-enable")
        self.assertTrue(enabled["verified"])

        rows = self.ok("a11y", "--max", "5")
        self.assertIsInstance(rows, list)
        if not rows:
            self.skipTest("no accessible elements exposed on this desktop")

    # ------------------------------------------------------------ installer
    def test_installer(self):
        prefix = os.path.join(self.tmp, "prefix")
        for argv in (["sh", os.path.join(ROOT, "install.sh"),
                      "--prefix", prefix],
                     ["sh", os.path.join(ROOT, "install.sh"),
                      "--prefix", prefix, "--check"]):
            proc = subprocess.run(argv, cwd=ROOT, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        linked = os.path.join(prefix, "bin", "computer-control")
        proc = subprocess.run([linked, "--version"], capture_output=True)
        self.assertEqual(proc.returncode, 0)
        proc = subprocess.run(["sh", os.path.join(ROOT, "install.sh"),
                               "--prefix", prefix, "--uninstall"],
                              cwd=ROOT, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(linked))


if __name__ == "__main__":
    unittest.main()
