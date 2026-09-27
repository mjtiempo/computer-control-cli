"""Tests for the pointer and the keyboard.

The pointer's one provable effect is the warp, so ``move`` must read the
cursor back and refuse a mapping that lands somewhere else. Keyboard and
button events are honestly ``unverifiable``; the tests pin which device each
form takes and what it sends.
"""

import unittest

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input, KeyMap  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland, FakeInputRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}

DUAL_MONITORS = [
    {"name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
     "scale": 1.0, "focused": True},
    {"name": "HDMI-A-1", "x": 1920, "y": 0, "width": 2560, "height": 1440,
     "scale": 1.0, "focused": False},
]


def make_input(hyprland=None):
    runner = FakeInputRunner(hyprland or FakeHyprland())
    session = Session(env=ENV, runner=runner, discoverer=FakeDiscoverer(ENV))
    return Input(session), runner


class KeyMapTests(unittest.TestCase):
    def test_letters_use_evdev_codes(self):
        self.assertEqual(KeyMap.evdev_code("a"), 30)
        self.assertEqual(KeyMap.evdev_code("c"), 46)

    def test_named_keys(self):
        self.assertEqual(KeyMap.evdev_code("return"), 28)
        self.assertEqual(KeyMap.evdev_code("f5"), 63)
        self.assertEqual(KeyMap.evdev_code("pageup"), 104)

    def test_digits(self):
        self.assertEqual(KeyMap.evdev_code("1"), 2)
        self.assertEqual(KeyMap.evdev_code("0"), 11)

    def test_unknown_key_refuses(self):
        with self.assertRaises(ControlError) as caught:
            KeyMap.evdev_code("banana")

        self.assertEqual(caught.exception.code, "key-unknown")

    def test_parse_combo(self):
        combo = KeyMap.parse("ctrl+shift+c")

        self.assertEqual(combo.mods, ("ctrl", "shift"))
        self.assertEqual(combo.key, "c")

    def test_parse_empty_combo_refuses(self):
        with self.assertRaises(ControlError) as caught:
            KeyMap.parse("")

        self.assertEqual(caught.exception.code, "bad-combo")

    def test_parse_two_keys_refuses(self):
        with self.assertRaises(ControlError) as caught:
            KeyMap.parse("ctrl+a+b")

        self.assertEqual(caught.exception.code, "bad-combo")

    def test_wtype_names(self):
        self.assertEqual(KeyMap.wtype_name("f5"), "F5")
        self.assertEqual(KeyMap.wtype_name("c"), "c")
        self.assertIsNone(KeyMap.wtype_name("nonexistent"))


class PointerMoveTests(unittest.TestCase):
    def test_pos_reads_the_cursor(self):
        pointer_input, _ = make_input()

        self.assertEqual(pointer_input.pos(), {"x": 0, "y": 0})

    def test_move_is_verified(self):
        pointer_input, runner = make_input()

        result = pointer_input.move(500, 300)

        self.assertTrue(result.verified)
        self.assertEqual(runner.cursorpos(), "500,300")

    def test_move_to_a_second_monitor_uses_displacement(self):
        pointer_input, runner = make_input(FakeHyprland(monitors=DUAL_MONITORS))

        result = pointer_input.move(2000, 100)

        self.assertTrue(result.verified)
        self.assertEqual(runner.cursorpos(), "2000,100")

    def test_move_outside_the_desktop_refuses(self):
        pointer_input, _ = make_input()

        with self.assertRaises(ControlError) as caught:
            pointer_input.move(5000, 100)

        self.assertEqual(caught.exception.code, "move-not-verified")

    def test_a_warp_that_never_lands_refuses(self):
        pointer_input, runner = make_input()
        runner.stubborn = True

        with self.assertRaises(ControlError) as caught:
            pointer_input.move(500, 300)

        self.assertEqual(caught.exception.code, "move-not-verified")


class PointerClickTests(unittest.TestCase):
    def test_click_warps_then_clicks(self):
        pointer_input, runner = make_input()

        result = pointer_input.click(100, 200)

        self.assertFalse(result.verified)
        self.assertEqual(result.effect, "unverifiable")
        self.assertEqual(result.details["button"], "L")
        self.assertEqual(runner.cursorpos(), "100,200")
        self.assertIn(["ydotool", "click", "0xC0"], runner.calls)

    def test_right_and_middle_buttons(self):
        pointer_input, runner = make_input()

        pointer_input.click(10, 10, button="r")
        pointer_input.click(10, 10, button="M")

        self.assertIn(["ydotool", "click", "0xC1"], runner.calls)
        self.assertIn(["ydotool", "click", "0xC2"], runner.calls)

    def test_double_click_clicks_twice(self):
        pointer_input, runner = make_input()

        pointer_input.click(10, 10, double=True)

        clicks = [call for call in runner.calls
                  if call[:2] == ["ydotool", "click"]]
        self.assertEqual(len(clicks), 2)

    def test_bad_button_refuses(self):
        pointer_input, _ = make_input()

        with self.assertRaises(ControlError) as caught:
            pointer_input.click(10, 10, button="Z")

        self.assertEqual(caught.exception.code, "bad-args")

    def test_click_without_coordinates_does_not_move(self):
        pointer_input, runner = make_input()

        pointer_input.click()

        self.assertFalse(any(call[:2] == ["ydotool", "mousemove"]
                             for call in runner.calls))


class PointerScrollTests(unittest.TestCase):
    def test_scroll_runs_the_wheel_device(self):
        pointer_input, runner = make_input()

        result = pointer_input.scroll("down", 3)

        self.assertFalse(result.verified)
        self.assertEqual(runner.calls[-1][0], "python3")
        self.assertIn("range(3)", runner.calls[-1][-1])

    def test_bad_direction_refuses(self):
        pointer_input, _ = make_input()

        with self.assertRaises(ControlError) as caught:
            pointer_input.scroll("sideways", 1)

        self.assertEqual(caught.exception.code, "bad-args")

    def test_zero_amount_refuses(self):
        pointer_input, _ = make_input()

        with self.assertRaises(ControlError) as caught:
            pointer_input.scroll("up", 0)

        self.assertEqual(caught.exception.code, "bad-args")

    def test_scroll_warps_when_given_a_point(self):
        pointer_input, runner = make_input()

        pointer_input.scroll("up", 1, 50, 60)

        self.assertEqual(runner.cursorpos(), "50,60")


class KeyboardTests(unittest.TestCase):
    def test_ctrl_chord_goes_through_wtype(self):
        keyboard_input, runner = make_input()

        result = keyboard_input.key("ctrl+c")

        self.assertFalse(result.verified)
        self.assertIn(["wtype", "-M", "ctrl", "-k", "c", "-m", "ctrl"],
                      runner.calls)

    def test_super_chord_goes_through_ydotool(self):
        keyboard_input, runner = make_input()

        keyboard_input.key("super+return")

        self.assertIn(["ydotool", "key", "125:1", "28:1", "28:0", "125:0"],
                      runner.calls)

    def test_forced_ydotool(self):
        keyboard_input, runner = make_input()

        keyboard_input.key("ctrl+c", force_ydotool=True)

        self.assertIn(["ydotool", "key", "29:1", "46:1", "46:0", "29:0"],
                      runner.calls)

    def test_type_text(self):
        keyboard_input, runner = make_input()

        result = keyboard_input.type_text("hi\nthere")

        self.assertFalse(result.verified)
        self.assertIn(["wtype", "hi", "-k", "Return", "there"], runner.calls)

    def test_literal_backslash_n_is_a_newline(self):
        keyboard_input, runner = make_input()

        keyboard_input.type_text("a\\nb")

        self.assertIn(["wtype", "a", "-k", "Return", "b"], runner.calls)

    def test_dash_prefixed_line_is_typed_literally(self):
        keyboard_input, runner = make_input()

        keyboard_input.type_text("-hello")

        self.assertIn(["wtype", "--", "-hello"], runner.calls)

    def test_type_failure_refuses(self):
        keyboard_input, runner = make_input()
        runner.stubborn = True  # unused here; keep the fake honest

        class Failing(FakeInputRunner):
            def run(self, argv, env=None, timeout=15, binary=False, input=None):
                if argv and argv[0] == "wtype":
                    import subprocess
                    return subprocess.CompletedProcess(argv, 1, stdout="",
                                                       stderr="boom")
                return super().run(argv, env=env, timeout=timeout,
                                   binary=binary, input=input)

        failing = Failing()
        session = Session(env=ENV, runner=failing, discoverer=FakeDiscoverer(ENV))

        with self.assertRaises(ControlError) as caught:
            Input(session).type_text("hi")

        self.assertEqual(caught.exception.code, "type")


class KeyboardHoldTests(unittest.TestCase):
    def test_key_down_uses_ydotool_state(self):
        keyboard_input, runner = make_input()

        result = keyboard_input.key_down("ctrl+c")

        self.assertEqual(result.details["state"], "down")
        self.assertIn(["ydotool", "key", "29:1", "46:1"], runner.calls)

    def test_key_up_uses_ydotool_state(self):
        keyboard_input, runner = make_input()

        keyboard_input.key_up("ctrl+c")

        self.assertIn(["ydotool", "key", "29:0", "46:0"], runner.calls)

    def test_key_down_super_uses_ydotool(self):
        keyboard_input, runner = make_input()

        keyboard_input.key_down("super+return")

        self.assertIn(["ydotool", "key", "125:1", "28:1"], runner.calls)

    def test_type_with_delay(self):
        keyboard_input, runner = make_input()

        keyboard_input.type_text("hi", delay_ms=100)

        self.assertIn(["wtype", "-d", "100", "hi"], runner.calls)

    def test_type_reports_the_delay(self):
        keyboard_input, _ = make_input()

        result = keyboard_input.type_text("hi", delay_ms=25)

        self.assertEqual(result.details["delay_ms"], 25)

    def test_key_up_super_uses_ydotool(self):
        keyboard_input, runner = make_input()

        keyboard_input.key_up("super+return")

        self.assertIn(["ydotool", "key", "125:0", "28:0"], runner.calls)

    def test_clearmodifiers_releases_before_the_action(self):
        keyboard_input, runner = make_input()
        release = ["ydotool", "key"] + [f"{code}:0"
                                         for code in KeyMap.MODIFIER_RELEASE_CODES]

        keyboard_input.key("ctrl+c", clearmodifiers=True)

        self.assertEqual(runner.calls[0], release)
        self.assertIn(["wtype", "-M", "ctrl", "-k", "c", "-m", "ctrl"],
                      runner.calls)

    def test_clearmodifiers_on_key_holds(self):
        keyboard_input, runner = make_input()
        release = ["ydotool", "key"] + [f"{code}:0"
                                         for code in KeyMap.MODIFIER_RELEASE_CODES]

        keyboard_input.key_down("shift+a", clearmodifiers=True)
        keyboard_input.key_up("shift+a", clearmodifiers=True)

        self.assertEqual(runner.calls[0], release)
        self.assertEqual(runner.calls[2], release)
        self.assertEqual(runner.calls[1], ["ydotool", "key", "42:1", "30:1"])
        self.assertEqual(runner.calls[3], ["ydotool", "key", "42:0", "30:0"])

    def test_clearmodifiers_on_type_click_and_drag(self):
        keyboard_input, runner = make_input()
        release = ["ydotool", "key"] + [f"{code}:0"
                                         for code in KeyMap.MODIFIER_RELEASE_CODES]

        keyboard_input.type_text("hi", clearmodifiers=True)
        keyboard_input.click(10, 10, clearmodifiers=True)
        keyboard_input.drag(1, 1, 2, 2, duration_ms=0, clearmodifiers=True)

        releases = [call for call in runner.calls if call == release]
        self.assertEqual(len(releases), 3)

    def test_clearmodifiers_defaults_to_off(self):
        keyboard_input, runner = make_input()

        keyboard_input.type_text("hi")

        self.assertFalse(any(call[:2] == ["ydotool", "key"] and all(
            part.endswith(":0") for part in call[2:]) for call in runner.calls))

    def test_modifier_release_codes_cover_both_sides(self):
        # Pinned literally: a wrong evdev code releases nothing, and every
        # other clearmodifiers test derives its expectation from this tuple.
        self.assertEqual(KeyMap.MODIFIER_RELEASE_CODES,
                         (29, 97, 56, 100, 42, 54, 125, 126))

    def test_a_refused_action_does_not_release_modifiers(self):
        release = ["ydotool", "key"] + [f"{code}:0"
                                        for code in KeyMap.MODIFIER_RELEASE_CODES]
        cases = {
            "scroll": lambda inp: inp.scroll("sideways", 5,
                                             clearmodifiers=True),
            "key": lambda inp: inp.key("ctrl+nosuchkey", clearmodifiers=True),
            "key_down": lambda inp: inp.key_down("ctrl+nosuchkey",
                                                 clearmodifiers=True),
            "click": lambda inp: inp.click(1, 1, button="Q",
                                           clearmodifiers=True),
            "move": lambda inp: inp.move(99999, 99999, clearmodifiers=True),
        }
        for label, action in cases.items():
            with self.subTest(label):
                keyboard_input, runner = make_input()

                with self.assertRaises(ControlError):
                    action(keyboard_input)

                self.assertNotIn(release, runner.calls,
                                 f"a refused {label} must not have already "
                                 "released the modifiers")


class BreakingRunner(FakeInputRunner):
    """Fails on the second relative move, i.e. after a drag has pressed."""

    def __init__(self):
        super().__init__()
        self.moves = 0

    def run(self, argv, env=None, timeout=15, binary=False, input=None):
        if argv[:2] == ["ydotool", "mousemove"]:
            self.moves += 1
            if self.moves > 1:
                raise ControlError("ydotool", "broken")
        return super().run(argv, env=env, timeout=timeout, binary=binary,
                           input=input)


class DragTests(unittest.TestCase):
    def test_drag_presses_moves_and_releases(self):
        pointer_input, runner = make_input()

        result = pointer_input.drag(100, 100, 300, 200, duration_ms=0)

        self.assertFalse(result.verified)
        scripts = [call[-1] for call in runner.calls if call[0] == "python3"]
        self.assertTrue(any("EV_KEY" in script and ", 1)" in script
                            for script in scripts))
        self.assertTrue(any("EV_KEY" in script and ", 0)" in script
                            for script in scripts))
        self.assertEqual(runner.cursorpos(), "300,200")

    def test_drag_right_button(self):
        pointer_input, runner = make_input()

        pointer_input.drag(10, 10, 20, 20, button="R", duration_ms=0)

        scripts = [call[-1] for call in runner.calls if call[0] == "python3"]
        self.assertTrue(any("273, 1)" in script for script in scripts))

    def test_drag_bad_button_refuses(self):
        pointer_input, _ = make_input()

        with self.assertRaises(ControlError) as caught:
            pointer_input.drag(1, 1, 2, 2, button="Z")

        self.assertEqual(caught.exception.code, "bad-args")

    def test_release_happens_when_movement_fails(self):
        breaking = BreakingRunner()
        session = Session(env=ENV, runner=breaking,
                          discoverer=FakeDiscoverer(ENV))

        with self.assertRaises(ControlError):
            Input(session).drag(100, 100, 300, 200, duration_ms=0)

        scripts = [call[-1] for call in breaking.calls if call[0] == "python3"]
        self.assertTrue(any("EV_KEY" in script and ", 0)" in script
                            for script in scripts),
                        "the button must be released even when the move fails")


if __name__ == "__main__":
    unittest.main()
