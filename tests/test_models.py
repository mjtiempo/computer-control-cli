"""Tests for the domain models parsed from hyprctl's JSON."""

import unittest
from typing import cast

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.models import Monitor, Window, WindowSpec, Workspace  # pi-lens-ignore: reportMissingImports


class MonitorTests(unittest.TestCase):
    RAW = {
        "name": "eDP-1", "x": 0, "y": 0, "width": 3840, "height": 2160,
        "scale": 2.0, "focused": True,
    }

    def test_from_hypr_makes_logical_rect(self):
        monitor = Monitor.from_hypr(self.RAW)
        assert monitor is not None

        self.assertEqual(monitor.name, "eDP-1")
        self.assertEqual((monitor.x, monitor.y), (0, 0))
        self.assertEqual((monitor.w, monitor.h), (1920, 1080))
        self.assertTrue(monitor.focused)

    def test_malformed_rows_contribute_no_monitor(self):
        self.assertIsNone(Monitor.from_hypr("not-a-dict"))
        self.assertIsNone(Monitor.from_hypr(None))

    def test_missing_size_is_zero_not_a_crash(self):
        monitor = Monitor.from_hypr({"name": "HDMI-A-1"})
        assert monitor is not None

        self.assertEqual((monitor.w, monitor.h), (0, 0))
        self.assertEqual(monitor.scale, 1.0)


class WorkspaceTests(unittest.TestCase):
    def test_from_hypr(self):
        workspace = Workspace.from_hypr(
            {"id": 2, "name": "2", "monitor": "eDP-1", "windows": 3})
        assert workspace is not None

        self.assertEqual(workspace.id, 2)
        self.assertEqual(workspace.name, "2")
        self.assertEqual(workspace.monitor, "eDP-1")
        self.assertEqual(workspace.windows, 3)

    def test_string_id_is_coerced(self):
        workspace = cast(Workspace, Workspace.from_hypr({"id": "7"}))

        self.assertEqual(workspace.id, 7)

    def test_malformed_rows_contribute_nothing(self):
        self.assertIsNone(Workspace.from_hypr(7))


class WindowTests(unittest.TestCase):
    RAW = {
        "address": "0x55A0", "title": "foot", "class": "foot",
        "workspace": {"id": 1, "name": "1"},
        "at": [100, 200], "size": [800, 600],
        "mapped": True, "fullscreen": 0, "floating": False, "pid": 42,
    }

    def test_from_hypr_unpacks_geometry_and_workspace(self):
        window = Window.from_hypr(self.RAW)
        assert window is not None

        self.assertEqual(window.address, "0x55A0")
        self.assertEqual(window.cls, "foot")
        self.assertEqual(window.workspace, 1)
        self.assertEqual((window.x, window.y, window.w, window.h),
                         (100, 200, 800, 600))
        self.assertEqual(window.center, (500, 500))

    def test_one_element_pairs_fall_back_to_zero(self):
        raw = dict(self.RAW, at=[100], size=[])

        window = Window.from_hypr(raw)
        assert window is not None

        self.assertEqual((window.x, window.y, window.w, window.h), (0, 0, 0, 0))

    def test_missing_workspace_reports_negative_one(self):
        raw = dict(self.RAW)
        raw.pop("workspace")

        window = cast(Window, Window.from_hypr(raw))

        self.assertEqual(window.workspace, -1)

    def test_brief_is_the_row_the_cli_prints(self):
        window = cast(Window, Window.from_hypr(self.RAW))
        brief = window.brief()

        self.assertEqual(brief["address"], "0x55A0")
        self.assertEqual(brief["class"], "foot")
        self.assertEqual(brief["workspace"], 1)
        self.assertEqual(brief["geometry"], [100, 200, 800, 600])

    def test_malformed_rows_contribute_no_window(self):
        self.assertIsNone(Window.from_hypr([]))


class WindowSpecTests(unittest.TestCase):
    WINDOW = cast(Window, Window.from_hypr(WindowTests.RAW))

    def test_bare_address_is_an_address(self):
        spec = WindowSpec.parse("0x55a0")

        self.assertEqual(spec.field, "address")
        self.assertTrue(spec.matches(self.WINDOW))

    def test_class_and_title_fields(self):
        self.assertTrue(WindowSpec.parse("class:foot").matches(self.WINDOW))
        self.assertTrue(WindowSpec.parse("title:FOOT").matches(self.WINDOW))
        self.assertFalse(WindowSpec.parse("class:kate").matches(self.WINDOW))

    def test_bare_name_matches_class_or_title(self):
        self.assertTrue(WindowSpec.parse("foo").matches(self.WINDOW))

    def test_empty_spec_is_bad_args(self):
        with self.assertRaises(ControlError) as caught:
            WindowSpec.parse("   ")

        self.assertEqual(caught.exception.code, "bad-args")

    def test_describe_is_human_readable(self):
        self.assertEqual(WindowSpec.parse("class:foot").describe(), "class:foot")


if __name__ == "__main__":
    unittest.main()
