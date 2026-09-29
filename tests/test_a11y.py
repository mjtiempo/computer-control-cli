"""Tests for AT-SPI element control and the bridge's snapshot logic."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from computer_control_cli.a11y import A11y  # pi-lens-ignore: reportMissingImports
from computer_control_cli.atspi_bridge import AtspiBridge, TreeSnapshot, TreeWalker  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland, FakeInputRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}
BRIDGE = "/tmp/ccc-bridge.py"

ROWS = [
    {"index": 0, "app": "foot", "role": "push button", "name": "Save",
     "value": "", "enabled": True, "selected": False,
     "actions": ["press"], "frame": [10, 20, 30, 40]},
    {"index": 1, "app": "foot", "role": "label", "name": "hello",
     "value": "", "enabled": True, "selected": False, "actions": [],
     "frame": None},
]


def make_a11y(responses=None, returncodes=None, stderrs=None):
    runner = FakeInputRunner(FakeHyprland())
    runner.responses.update(responses or {})
    runner.returncodes.update(returncodes or {})
    runner.stderrs.update(stderrs or {})
    session = Session(env=ENV, runner=runner, discoverer=FakeDiscoverer(ENV))
    return (A11y(session, input=Input(session), desktop=Desktop(session),
                 bridge=BRIDGE), runner)


class A11yIndexTests(unittest.TestCase):
    def test_index_returns_rows(self):
        a11y, runner = make_a11y(
            responses={f"python3 {BRIDGE} index": json.dumps(ROWS)})

        rows = a11y.index()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Save")
        self.assertTrue(runner.calls[-1][1].endswith("ccc-bridge.py"))

    def test_index_passes_filters(self):
        a11y, runner = make_a11y(
            responses={f"python3 {BRIDGE} index --app foot --max 10": "[]"})

        a11y.index(app="foot", max_items=10)

        self.assertEqual(runner.calls[-1][3:], ["--app", "foot", "--max", "10"])

    def test_index_without_json_refuses(self):
        a11y, _ = make_a11y(responses={f"python3 {BRIDGE} index": "not json"})

        with self.assertRaises(ControlError) as caught:
            a11y.index()

        self.assertEqual(caught.exception.code, "atspi")

    def test_index_can_skip_the_snapshot(self):
        a11y, runner = make_a11y(
            responses={f"python3 {BRIDGE} index --max 1 --no-save": "[]"})

        rows = a11y.index(max_items=1, snapshot=False)

        self.assertEqual(rows, [])
        self.assertEqual(runner.calls[-1][3:], ["--max", "1", "--no-save"])


class A11yClickTests(unittest.TestCase):
    def test_ax_route_invokes_the_action(self):
        a11y, _ = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": True, "route": "ax", "action": "press",
                 "role": "push button", "name": "Save"})})

        result = a11y.click_element(0)

        self.assertFalse(result.verified)
        self.assertEqual(result.details["route"], "ax")
        self.assertEqual(result.details["action"], "press")

    def test_failed_ax_action_refuses(self):
        a11y, _ = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": False, "route": "ax", "action": "press"})})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "atspi")

    def test_ax_route_refuses_a_button_it_cannot_use(self):
        a11y, _ = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": True, "route": "ax", "action": "press",
                 "role": "push button", "name": "Save"})})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0, button="R")

        self.assertEqual(caught.exception.code, "bad-args")

    def test_click_element_pins_the_dump(self):
        a11y, runner = make_a11y(responses={
            f"python3 {BRIDGE} click 0 --dump ab12cd34": json.dumps(
                {"ok": True, "route": "ax", "action": "press",
                 "role": "push button", "name": "Save",
                 "dump": "ab12cd34"})})

        result = a11y.click_element(0, dump="ab12cd34")

        self.assertEqual(runner.calls[-1][3:], ["0", "--dump", "ab12cd34"])
        self.assertEqual(result.details["dump"], "ab12cd34")

    def test_px_fallback_warps_and_clicks(self):
        a11y, runner = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": True, "route": "px-fallback", "x": 110, "y": 220,
                 "app": "foot", "role": "push button", "name": "Save"})})

        result = a11y.click_element(0)

        self.assertEqual(result.details["route"], "px-fallback")
        self.assertEqual(result.details["x"], 110)   # screen coords, as sent
        self.assertEqual(result.details["y"], 220)
        self.assertEqual(runner.cursorpos(), "110,220")
        self.assertIn(["ydotool", "click", "0xC0"], runner.calls)

    def test_px_fallback_refuses_another_process(self):
        a11y, runner = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": True, "route": "px-fallback", "x": 110, "y": 220,
                 "pid": 999, "app": "foot", "role": "push button",
                 "name": "Save"})})   # the focused client is pid 11

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "element-owner-mismatch")
        self.assertNotIn(["ydotool", "click", "0xC0"], runner.calls)

    def test_px_fallback_refuses_a_point_outside_the_window(self):
        a11y, runner = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": True, "route": "px-fallback", "x": 10, "y": 20,
                 "app": "foot", "role": "push button", "name": "Save"})})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "element-owner-mismatch")
        self.assertNotIn(["ydotool", "click", "0xC0"], runner.calls)

    def test_px_fallback_owner_mismatch_refuses(self):
        a11y, _ = make_a11y(responses={
            f"python3 {BRIDGE} click 0": json.dumps(
                {"ok": True, "route": "px-fallback", "x": 10, "y": 20,
                 "app": "kate", "role": "push button", "name": "Save"})})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "element-owner-mismatch")

    def test_stale_tree_refusal_keeps_its_code(self):
        a11y, _ = make_a11y(
            returncodes={f"python3 {BRIDGE} click 0": 3},
            stderrs={f"python3 {BRIDGE} click 0": "ERR[stale-tree]: gone\n"})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "stale-tree")

    def test_ambiguous_element_refusal_keeps_its_code(self):
        a11y, _ = make_a11y(
            returncodes={f"python3 {BRIDGE} click 0": 3},
            stderrs={f"python3 {BRIDGE} click 0":
                     "ERR[ambiguous-element]: 2 nodes share it\n"})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "ambiguous-element")

    def test_unstructured_failure_is_an_atspi_refusal(self):
        a11y, _ = make_a11y(
            returncodes={f"python3 {BRIDGE} click 0": 1},
            stderrs={f"python3 {BRIDGE} click 0": "Traceback...\n"})

        with self.assertRaises(ControlError) as caught:
            a11y.click_element(0)

        self.assertEqual(caught.exception.code, "atspi")


class A11yEnableTests(unittest.TestCase):
    SET = ("busctl --user set-property org.a11y.Bus /org/a11y/bus "
           "org.a11y.Status IsEnabled b true")
    GET = "busctl --user get-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled"

    def test_enable_reads_the_flag_back(self):
        a11y, _ = make_a11y(responses={self.SET: "", self.GET: "b true"})

        result = a11y.enable()

        self.assertTrue(result.verified)
        self.assertIn("true", result.details["IsEnabled"])

    def test_enable_refuses_when_the_flag_did_not_take(self):
        a11y, _ = make_a11y(responses={self.SET: "", self.GET: "b false"})

        with self.assertRaises(ControlError) as caught:
            a11y.enable()

        self.assertEqual(caught.exception.code, "atspi")


class OwnerMatchTests(unittest.TestCase):
    def test_owner_matching_normalizes_spellings(self):
        self.assertTrue(A11y.owner_matches("org.gnome.Nautilus", "nautilus"))
        self.assertTrue(A11y.owner_matches("Chromium", "chromium"))
        self.assertTrue(A11y.owner_matches("Google-chrome", "googlechrome"))

    def test_owner_matching_refuses_empty_and_foreign(self):
        self.assertFalse(A11y.owner_matches("foot", "kate"))
        self.assertFalse(A11y.owner_matches("", "foot"))
        self.assertFalse(A11y.owner_matches("foot", ""))


class TreeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "snapshot.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_identity_is_role_name_actions(self):
        self.assertEqual(TreeSnapshot.identity(ROWS[0]),
                         ("push button", "Save", ("press",)))

    def test_hits_finds_every_matching_row(self):
        twin = dict(ROWS[0], index=1)

        self.assertEqual(TreeSnapshot.hits([ROWS[0], twin],
                                           TreeSnapshot.identity(ROWS[0])),
                         [0, 1])
        self.assertEqual(TreeSnapshot.hits([ROWS[1]],
                                           TreeSnapshot.identity(ROWS[0])), [])

    def test_target_out_of_range_is_none(self):
        self.assertIsNone(TreeSnapshot.target([ROWS[0]], 5))

    def test_load_refuses_a_symlink(self):
        target = os.path.join(self._tmp.name, "elsewhere")
        TreeSnapshot.save(ROWS, target)
        link = os.path.join(self._tmp.name, "link")
        os.symlink(target, link)

        self.assertIsNone(TreeSnapshot.load(link))

    def test_the_dump_id_covers_the_ordered_identities(self):
        one = [ROWS[0], ROWS[1]]

        self.assertEqual(TreeSnapshot.digest(one), TreeSnapshot.digest(list(one)))
        self.assertNotEqual(TreeSnapshot.digest(one),
                            TreeSnapshot.digest(list(reversed(one))))
        renamed = [dict(ROWS[0], name="Cancel"), ROWS[1]]
        self.assertNotEqual(TreeSnapshot.digest(one),
                            TreeSnapshot.digest(renamed))

    def test_a_saved_dump_keeps_its_id(self):
        rows = [ROWS[0], ROWS[1]]

        TreeSnapshot.save(rows, self.path)
        stored = TreeSnapshot.load(self.path)

        assert stored is not None
        self.assertEqual(TreeSnapshot.digest(stored),
                         TreeSnapshot.digest(rows))
        self.assertIsNone(TreeSnapshot.target([ROWS[0]], -1))
        self.assertEqual(TreeSnapshot.target([ROWS[0]], 0),
                         TreeSnapshot.identity(ROWS[0]))

    def test_save_and_load_round_trip(self):
        self.assertTrue(TreeSnapshot.save([ROWS[0]], self.path))

        stored = TreeSnapshot.load(self.path)
        assert stored is not None

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["name"], "Save")

    def test_load_of_a_missing_file_is_none(self):
        self.assertIsNone(TreeSnapshot.load(os.path.join(self._tmp.name,
                                                         "nope.json")))

    def test_save_to_an_unwritable_path_is_false(self):
        self.assertFalse(TreeSnapshot.save([ROWS[0]],
                                           "/proc/definitely/not/writable"))


class BridgeArgvTests(unittest.TestCase):
    def test_flag_reads_a_value(self):
        self.assertEqual(AtspiBridge.flag(["--app", "foot"], "--app"), "foot")
        self.assertIsNone(AtspiBridge.flag([], "--app"))
        self.assertIsNone(AtspiBridge.flag(["--app"], "--app"))

    def test_unknown_mode_is_an_error(self):
        self.assertEqual(AtspiBridge.main(["nope"]), 1)

    def test_click_without_an_index_is_an_error(self):
        self.assertEqual(AtspiBridge.main(["click"]), 1)


class FakeNode:
    """One AT-SPI node: just enough of the accessible interface to walk."""

    def __init__(self, name="", role="", children=(), actions=()):
        self.name = name
        self.role = role
        self.children = list(children)
        self.actions = list(actions)

    def get_name(self):
        return self.name

    def get_role_name(self):
        return self.role

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_value_iface(self):
        return None

    def get_n_actions(self):
        return len(self.actions)

    def get_action_name(self, index):
        return self.actions[index]

    def get_state_set(self):
        return None

    def get_component_iface(self):
        return None

    def get_process_id(self):
        return 4242


class FakeAtspi:
    """The Atspi module double: ``get_desktop`` and the two enums are all
    the walker reads from the library."""

    class StateType:
        ENABLED = "enabled"
        SELECTED = "selected"

    class CoordType:
        SCREEN = "screen"

    def __init__(self, desktop):
        self.desktop = desktop

    def get_desktop(self, index):
        return self.desktop


class TreeWalkerTests(unittest.TestCase):
    """The walk's row space: the app filter must run before the cap."""

    def tearDown(self):
        TreeWalker._ATSPI = None

    @staticmethod
    def node(name: str, children=(), actions=("press",)):
        return FakeNode(name=name, role="push button", children=children,
                        actions=actions)

    def test_the_app_filter_is_applied_before_the_cap(self):
        other = FakeNode(name="other", role="application", children=[
            self.node("one"), self.node("two"), self.node("three")])
        nautilus = FakeNode(name="nautilus", role="application", children=[
            self.node("Files"), self.node("Home")])
        TreeWalker._ATSPI = FakeAtspi(
            FakeNode(children=[other, nautilus]))
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = AtspiBridge(["index", "--app", "nautilus", "--max", "2",
                                "--no-save"]).index()

        self.assertEqual(code, 0)
        rows = json.loads(stdout.getvalue())
        self.assertEqual([row["app"] for row in rows],
                         ["nautilus", "nautilus"])
        self.assertEqual(rows[0]["pid"], 4242)
        self.assertEqual(rows[0]["dump"], TreeSnapshot.digest(rows))


if __name__ == "__main__":
    unittest.main()
