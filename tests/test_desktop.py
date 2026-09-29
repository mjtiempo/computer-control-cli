"""Tests for the desktop: reads, resolution, and workspace actions.

Every action is exercised twice: once against a double that APPLIES the
dispatch (the effect is read back and reported `confirmed`) and once against
one that answers `ok` without applying it (the verb must refuse with a
`*-not-verified` code, never report success).
"""

import unittest

from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}

CLIENTS = [
    {"address": "0xaaa1", "title": "foot", "class": "foot",
     "workspace": {"id": 1, "name": "1"}, "at": [100, 200],
     "size": [800, 600], "mapped": True, "fullscreen": 0,
     "floating": False, "pid": 11},
    {"address": "0xbbb2", "title": "kate", "class": "kate",
     "workspace": {"id": 2, "name": "2"}, "at": [10, 20],
     "size": [900, 700], "mapped": True, "fullscreen": 0,
     "floating": False, "pid": 22},
    {"address": "0xccc3", "title": "hidden", "class": "foot",
     "workspace": {"id": 2, "name": "2"}, "at": [0, 0],
     "size": [10, 10], "mapped": False, "fullscreen": 0,
     "floating": False, "pid": 33},
]


def make_desktop(hyprland=None):
    hyprland = hyprland or FakeHyprland(clients=[dict(c) for c in CLIENTS])
    session = Session(env=ENV, runner=hyprland, discoverer=FakeDiscoverer(ENV))
    return Desktop(session), hyprland


class DesktopReadTests(unittest.TestCase):
    def test_windows_are_mapped_and_parsed(self):
        desktop, _ = make_desktop()

        windows = desktop.windows()

        self.assertEqual([w.address for w in windows], ["0xaaa1", "0xbbb2"])

    def test_active_window(self):
        desktop, _ = make_desktop()

        active = desktop.active()
        assert active is not None
        self.assertEqual(active.address, "0xaaa1")

    def test_no_active_window_is_none(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.active_address = ""
        desktop, _ = make_desktop(hypr)

        self.assertIsNone(desktop.active())

    def test_an_empty_activewindow_reply_is_none(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.empty_active = True
        desktop, _ = make_desktop(hypr)

        self.assertIsNone(desktop.active())

    def test_workspaces(self):
        desktop, _ = make_desktop()

        self.assertEqual([w.id for w in desktop.workspaces()], [1, 2])

    def test_monitors(self):
        desktop, _ = make_desktop()

        self.assertEqual(desktop.monitors()[0].name, "eDP-1")

    def test_state_carries_the_snapshot(self):
        desktop, _ = make_desktop()

        state = desktop.state()

        self.assertEqual(state["active_workspace"], 1)
        self.assertEqual(len(state["windows"]), 2)
        self.assertEqual(state["active"].address, "0xaaa1")
        self.assertEqual(state["pointer"], {"x": 0, "y": 0})

    def test_clients_skip_rows_that_are_not_objects(self):
        hypr = FakeHyprland(clients=[dict(CLIENTS[0]), "junk"])
        desktop, _ = make_desktop(hypr)

        self.assertEqual(len(desktop.windows()), 1)


class DesktopResolveTests(unittest.TestCase):
    def test_resolves_one_window(self):
        desktop, _ = make_desktop()

        self.assertEqual(desktop.resolve("class:kate").address, "0xbbb2")

    def test_none_refuses(self):
        desktop, _ = make_desktop()

        with self.assertRaises(ControlError) as caught:
            desktop.resolve("class:missing", "focus")

        self.assertEqual(caught.exception.code, "window-not-found")
        self.assertIn("focus", caught.exception.message)

    def test_several_refuses_and_names_them(self):
        second = dict(CLIENTS[0], address="0xddd4", title="foot 2")
        hypr = FakeHyprland(clients=[dict(CLIENTS[0]), second])
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.resolve("class:foot", "close")

        self.assertEqual(caught.exception.code, "ambiguous-window")
        self.assertIn("0xaaa1", caught.exception.message)


class DesktopKindTests(unittest.TestCase):
    def test_class_matching_is_word_bounded(self):
        self.assertTrue(Desktop.class_matches("com.mitchellh.ghostty",
                                              Desktop.TERMINALS))
        self.assertTrue(Desktop.class_matches("foot", Desktop.TERMINALS))
        self.assertFalse(Desktop.class_matches("steam", Desktop.TERMINALS))
        self.assertFalse(Desktop.class_matches("stremio", Desktop.TERMINALS))
        self.assertFalse(Desktop.class_matches("zenity", Desktop.BROWSERS))

    def test_kind_of(self):
        desktop, _ = make_desktop()
        windows = desktop.windows()

        self.assertEqual(Desktop.kind_of(windows[0]), "terminal")
        self.assertEqual(Desktop.kind_of(windows[1]), "other")


class DesktopSwitchTests(unittest.TestCase):
    def test_switch_is_verified(self):
        desktop, hypr = make_desktop()

        result = desktop.switch(2)

        self.assertTrue(result.ok)
        self.assertTrue(result.verified)
        self.assertEqual(hypr.active_workspace, 2)

    def test_switch_that_did_not_take_refuses(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.honor_dispatches = False
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.switch(2)

        self.assertEqual(caught.exception.code, "switch-not-verified")


class DesktopFocusTests(unittest.TestCase):
    def test_focus_is_verified(self):
        desktop, hypr = make_desktop()

        result = desktop.focus("class:kate")

        self.assertTrue(result.verified)
        self.assertEqual(hypr.active_address, "0xbbb2")

    def test_focus_that_did_not_take_refuses(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.honor_dispatches = False
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.focus("class:kate")

        self.assertEqual(caught.exception.code, "focus-not-verified")


class DesktopCloseTests(unittest.TestCase):
    def test_close_target_is_verified(self):
        desktop, hypr = make_desktop()

        result = desktop.close("class:kate")

        self.assertTrue(result.verified)
        self.assertEqual([c["address"] for c in hypr.clients], ["0xaaa1", "0xccc3"])

    def test_close_that_did_not_take_refuses(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.honor_dispatches = False
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.close("class:kate")

        self.assertEqual(caught.exception.code, "close-not-verified")

    def test_bare_close_closes_the_focused_window(self):
        desktop, hypr = make_desktop()

        result = desktop.close()

        self.assertTrue(result.verified)
        self.assertEqual([c["address"] for c in hypr.clients], ["0xbbb2", "0xccc3"])

    def test_bare_close_with_nothing_focused_is_honest(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.active_address = ""
        desktop, _ = make_desktop(hypr)

        result = desktop.close()

        self.assertFalse(result.ok)
        self.assertEqual(result.effect, "unverifiable")


class DesktopMoveTests(unittest.TestCase):
    def test_move_window_is_verified(self):
        desktop, hypr = make_desktop()

        result = desktop.move_window("class:kate", 1)

        self.assertTrue(result.verified)
        moved = next(c for c in hypr.clients if c["address"] == "0xbbb2")
        self.assertEqual(moved["workspace"]["id"], 1)

    def test_the_workspace_is_escaped_into_the_lua(self):
        desktop, hypr = make_desktop()
        payload = '1") ; hl.dsp.exec_cmd("id") ; --'

        desktop.move_window("class:kate", payload)

        expression = hypr.dispatch_log[-1]
        self.assertNotIn(payload, expression)
        self.assertIn(r'workspace = "1\") ; hl.dsp.exec_cmd(\"id\") ; --"',
                      expression)

    def test_move_window_accepts_a_named_workspace(self):
        desktop, _ = make_desktop()

        result = desktop.move_window("class:kate", "name:web")

        self.assertTrue(result.verified)

    def test_move_that_did_not_take_refuses(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.honor_dispatches = False
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.move_window("class:kate", 1)

        self.assertEqual(caught.exception.code, "move-not-verified")


class DesktopCloseAppsTests(unittest.TestCase):
    def test_dry_run_reports_and_closes_nothing(self):
        desktop, hypr = make_desktop()

        result = desktop.close_apps("terminal", dry_run=True)

        self.assertEqual(result.details["count"], 1)
        self.assertEqual(len(hypr.clients), 3)

    def test_closes_every_window_of_a_kind(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.clients.append({"address": "0xddd4", "title": "foot 2",
                             "class": "foot", "workspace": {"id": 1, "name": "1"},
                             "at": [0, 0], "size": [100, 100], "mapped": True,
                             "fullscreen": 0, "floating": False, "pid": 44})
        desktop, _ = make_desktop(hypr)

        result = desktop.close_apps("terminal")

        self.assertTrue(result.verified)
        self.assertEqual(result.details["remaining"], [])

    def test_a_survivor_is_reported_not_smoothed_over(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.honor_dispatches = False
        desktop, _ = make_desktop(hypr)

        result = desktop.close_apps("terminal")

        self.assertFalse(result.ok)
        self.assertEqual(len(result.details["remaining"]), 1)


class DesktopWaitTests(unittest.TestCase):
    def test_waits_for_an_existing_window_immediately(self):
        desktop, _ = make_desktop()

        result = desktop.wait("class:foot", timeout=0)

        self.assertTrue(result.verified)
        self.assertEqual(result.details["window"]["address"], "0xaaa1")

    def test_timeout_refuses(self):
        desktop, _ = make_desktop()

        with self.assertRaises(ControlError) as caught:
            desktop.wait("class:missing", timeout=0)

        self.assertEqual(caught.exception.code, "wait-timeout")

    def test_gone_is_satisfied_when_the_window_is_absent(self):
        desktop, _ = make_desktop()

        result = desktop.wait("class:missing", gone=True, timeout=0)

        self.assertTrue(result.verified)

    def test_gone_times_out_when_it_is_still_open(self):
        desktop, _ = make_desktop()

        with self.assertRaises(ControlError) as caught:
            desktop.wait("class:foot", gone=True, timeout=0)

        self.assertEqual(caught.exception.code, "wait-timeout")

    def test_title_and_class_filters(self):
        desktop, _ = make_desktop()

        self.assertTrue(desktop.wait(title="kate", timeout=0).verified)
        self.assertTrue(desktop.wait(klass="foot", timeout=0).verified)
        with self.assertRaises(ControlError):
            desktop.wait(title="nope", timeout=0)

    def test_excluded_addresses_are_ignored(self):
        desktop, _ = make_desktop()

        result = desktop.wait(exclude={"0xaaa1"}, timeout=0)

        self.assertTrue(result.verified)
        self.assertEqual(result.details["window"]["address"], "0xbbb2")

    def test_new_only_has_nothing_new_to_find(self):
        desktop, _ = make_desktop()

        with self.assertRaises(ControlError) as caught:
            desktop.wait(new_only=True, timeout=0)

        self.assertEqual(caught.exception.code, "wait-timeout")

    def test_stable_needs_consecutive_reads(self):
        desktop, _ = make_desktop()

        result = desktop.wait("class:foot", timeout=1, poll=0.001, stable=3)
        with self.assertRaises(ControlError):
            desktop.wait("class:foot", timeout=0, stable=3)

        self.assertEqual(result.details["stable"], 3)

    def test_two_matches_refuse_instead_of_picking_one(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.clients.append({"address": "0xddd4", "title": "foot 2",
                             "class": "foot",
                             "workspace": {"id": 1, "name": "1"},
                             "at": [0, 0], "size": [100, 100],
                             "mapped": True, "fullscreen": 0,
                             "floating": False, "pid": 44})
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.wait("class:foot", timeout=0)

        self.assertEqual(caught.exception.code, "ambiguous-window")

    def test_gone_is_not_ambiguous(self):
        hypr = FakeHyprland(clients=[dict(c) for c in CLIENTS])
        hypr.clients.append({"address": "0xddd4", "title": "foot 2",
                             "class": "foot",
                             "workspace": {"id": 1, "name": "1"},
                             "at": [0, 0], "size": [100, 100],
                             "mapped": True, "fullscreen": 0,
                             "floating": False, "pid": 44})
        desktop, _ = make_desktop(hypr)

        with self.assertRaises(ControlError) as caught:
            desktop.wait("class:foot", gone=True, timeout=0)

        self.assertEqual(caught.exception.code, "wait-timeout")

    def test_wait_without_a_spec_matches_any_window(self):
        desktop, _ = make_desktop()

        result = desktop.wait(timeout=0)

        self.assertTrue(result.verified)
        self.assertEqual(result.details["window"]["address"], "0xaaa1")


if __name__ == "__main__":
    unittest.main()
