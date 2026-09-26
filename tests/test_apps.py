"""Tests for app entries, resolution, launching and the directory probe."""

import os
import stat
import tempfile
import unittest

from computer_control_cli.apps import Apps, DesktopEntry  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}

FOOT_ENTRY = """[Desktop Entry]
Name=Foot
Exec=foot --server
Terminal=false
Icon=foot
"""

KATE_ENTRY = """[Desktop Entry]
Name=Kate
Exec=kate %U
Terminal=false
"""

HIDDEN_ENTRY = """[Desktop Entry]
Name=Hidden
Exec=hidden
NoDisplay=true
"""

FOOT_CLIENT = {"address": "0xfeed", "title": "foot", "class": "foot",
               "workspace": {"id": 1, "name": "1"}, "at": [0, 0],
               "size": [800, 600], "mapped": True, "fullscreen": 0,
               "floating": False, "pid": 55}


class AppsFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.entries_dir = os.path.join(self.dir, "applications")
        os.makedirs(self.entries_dir)
        self.write_entry("foot.desktop", FOOT_ENTRY)
        self.write_entry("kate.desktop", KATE_ENTRY)
        self.write_entry("hidden.desktop", HIDDEN_ENTRY)
        self.bin_dir = os.path.join(self.dir, "bin")
        os.makedirs(self.bin_dir)
        self.proc_dir = os.path.join(self.dir, "proc")
        os.makedirs(self.proc_dir)
        self.hypr = FakeHyprland()
        self.session = Session(env=ENV, runner=self.hypr,
                               discoverer=FakeDiscoverer(ENV))
        self.desktop = Desktop(self.session)
        self.apps = Apps(self.session, desktop=self.desktop,
                         entry_dirs=[self.entries_dir], path=self.bin_dir,
                         terminal_launcher="foot")

    def tearDown(self):
        self._tmp.cleanup()

    def write_entry(self, name, text):
        with open(os.path.join(self.entries_dir, name), "w") as handle:
            handle.write(text)

    def make_executable(self, name):
        path = os.path.join(self.bin_dir, name)
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def create_proc(self, pid, comm, argv):
        directory = os.path.join(self.proc_dir, str(pid))
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "comm"), "w") as handle:
            handle.write(comm + "\n")
        with open(os.path.join(directory, "cmdline"), "wb") as handle:
            handle.write(b"\x00".join(a.encode() for a in argv) + b"\x00")
        return directory


class DesktopEntryTests(unittest.TestCase):
    def test_parses_the_fields(self):
        entry = DesktopEntry.from_text(FOOT_ENTRY, "foot")
        assert entry is not None

        self.assertEqual(entry.name, "Foot")
        self.assertEqual(entry.command, "foot --server")
        self.assertFalse(entry.terminal)
        self.assertEqual(entry.entry_id, "foot")

    def test_field_codes_are_stripped_from_argv(self):
        entry = DesktopEntry.from_text(KATE_ENTRY, "kate")
        assert entry is not None

        self.assertEqual(entry.argv(), ["kate"])

    def test_terminal_flag(self):
        entry = DesktopEntry.from_text(
            "[Desktop Entry]\nName=T\nExec=htop\nTerminal=true\n", "t")
        assert entry is not None

        self.assertTrue(entry.terminal)


class AppsListTests(AppsFixture):
    def test_entries_skip_nodisplay(self):
        names = [entry.name for entry in self.apps.entries()]

        self.assertEqual(sorted(names), ["Foot", "Kate"])

    def test_list_is_printable_rows(self):
        rows = self.apps.list()

        self.assertEqual(rows[0]["name"], "Foot")
        self.assertIn("command", rows[0])
        self.assertIn("terminal", rows[0])

    def test_list_marks_a_running_entry(self):
        self.create_proc(321, "foot", ["/usr/bin/foot", "--server"])
        apps = self.apps_with_proc()

        row = next(r for r in apps.list() if r["name"] == "Foot")

        self.assertTrue(row["running"])
        self.assertEqual(row["pid"], 321)

    def test_list_includes_processes_without_an_entry(self):
        self.create_proc(654, "mystery", ["/usr/bin/mystery", "--flag"])
        apps = self.apps_with_proc()

        rows = [r for r in apps.list() if r.get("kind") == "process"]

        self.assertTrue(any(r["name"] == "mystery" and r["pid"] == 654
                            for r in rows))

    def apps_with_proc(self):
        return Apps(self.session, desktop=self.desktop,
                    entry_dirs=[self.entries_dir], path=self.bin_dir,
                    terminal_launcher="foot", proc=self.proc_dir)


class AppsResolveTests(AppsFixture):
    def test_exact_name(self):
        self.assertEqual(self.apps.resolve("Foot").command, "foot --server")

    def test_substring(self):
        self.assertEqual(self.apps.resolve("kat").name, "Kate")

    def test_unknown_refuses(self):
        with self.assertRaises(ControlError) as caught:
            self.apps.resolve("nothing-like-this")

        self.assertEqual(caught.exception.code, "app-unknown")

    def test_ambiguous_refuses(self):
        self.write_entry("kate-extra.desktop",
                         "[Desktop Entry]\nName=Kately\nExec=kately\n")

        with self.assertRaises(ControlError) as caught:
            self.apps.resolve("kat")

        self.assertEqual(caught.exception.code, "app-ambiguous")

    def test_which_finds_an_executable(self):
        path = self.make_executable("hello")

        self.assertEqual(self.apps.which("hello"), path)

    def test_which_unknown_refuses(self):
        with self.assertRaises(ControlError) as caught:
            self.apps.which("definitely-not-here")

        self.assertEqual(caught.exception.code, "not-found")


class AppsOpenTests(AppsFixture):
    def test_dry_run_plans_without_launching(self):
        plan = self.apps.open("Foot", dry_run=True)

        self.assertEqual(plan["command"], "foot --server")
        self.assertEqual(self.hypr.dispatch_log, [])

    def test_open_waits_for_and_names_the_new_window(self):
        self.hypr.spawn_on_exec = [dict(FOOT_CLIENT)]

        result = self.apps.open("Foot", timeout=1)

        self.assertTrue(result.verified)
        self.assertEqual(result.details["window"]["address"], "0xfeed")

    def test_open_places_the_window_on_the_asked_workspace(self):
        client = dict(FOOT_CLIENT, workspace={"id": 1, "name": "1"})
        self.hypr.spawn_on_exec = [client]

        result = self.apps.open("Foot", workspace=2, timeout=1)

        self.assertTrue(result.verified)
        self.assertEqual(result.details["workspace"], 2)

    def test_open_times_out_with_a_refusal(self):
        with self.assertRaises(ControlError) as caught:
            self.apps.open("Foot", timeout=0)

        self.assertEqual(caught.exception.code, "app-open-failed")

    def test_open_refuses_a_missing_cwd(self):
        with self.assertRaises(ControlError) as caught:
            self.apps.open("Foot", cwd=os.path.join(self.dir, "nope"))

        self.assertEqual(caught.exception.code, "cwd-missing")

    def test_launch_places_the_window_on_a_workspace(self):
        self.hypr.spawn_on_exec = [dict(FOOT_CLIENT)]

        result = self.apps.launch(["foot", "--server"], wait=True, timeout=1,
                                  workspace=2)

        self.assertTrue(result.verified)
        moved = next(c for c in self.hypr.clients
                     if c["address"] == "0xfeed")
        self.assertEqual(moved["workspace"]["id"], 2)

    def test_a_terminal_entry_runs_inside_a_terminal(self):
        self.write_entry("htop.desktop",
                         "[Desktop Entry]\nName=Htop\nExec=htop\nTerminal=true\n")
        self.hypr.spawn_on_exec = [dict(FOOT_CLIENT)]

        result = self.apps.open("Htop", timeout=1)

        self.assertTrue(result.verified)
        dispatch = self.hypr.dispatch_log[-1]
        self.assertIn("foot", dispatch)
        self.assertIn("htop", dispatch)

    def test_a_terminal_entry_without_a_launcher_refuses(self):
        self.write_entry("htop.desktop",
                         "[Desktop Entry]\nName=Htop\nExec=htop\nTerminal=true\n")
        bare = Apps(self.session, desktop=self.desktop,
                    entry_dirs=[self.entries_dir], path=self.bin_dir)

        with self.assertRaises(ControlError) as caught:
            bare.open("Htop", timeout=0)

        self.assertEqual(caught.exception.code, "not-found")


class AppsDirTests(AppsFixture):
    def test_existing_directory(self):
        result = self.apps.dir(self.entries_dir)

        self.assertTrue(result["existed"])
        self.assertFalse(result["created"])

    def test_missing_directory_without_create(self):
        target = os.path.join(self.dir, "fresh")

        result = self.apps.dir(target)

        self.assertFalse(result["existed"])
        self.assertFalse(os.path.exists(target))

    def test_create_makes_the_directory(self):
        target = os.path.join(self.dir, "fresh", "nested")

        result = self.apps.dir(target, create=True)

        self.assertTrue(result["created"])
        self.assertTrue(os.path.isdir(target))

    def test_a_file_in_the_way_refuses(self):
        target = os.path.join(self.dir, "file.txt")
        with open(target, "w") as handle:
            handle.write("x")

        with self.assertRaises(ControlError) as caught:
            self.apps.dir(target)

        self.assertEqual(caught.exception.code, "not-a-dir")


if __name__ == "__main__":
    unittest.main()
