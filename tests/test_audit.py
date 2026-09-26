"""Tests for the machine audit."""

import struct
import unittest

from computer_control_cli.a11y import A11y  # pi-lens-ignore: reportMissingImports
from computer_control_cli.audit import Audit  # pi-lens-ignore: reportMissingImports
from computer_control_cli.desktop import Desktop  # pi-lens-ignore: reportMissingImports
from computer_control_cli.input import Input  # pi-lens-ignore: reportMissingImports
from computer_control_cli.screen import Screen  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeHyprland, FakeInputRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1", "WAYLAND_DISPLAY": "wayland-1"}
GRIM = "grim -s 1 -"


def png(width: int, height: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"x")


class Which:
    """A shutil.which double: only the listed tools exist."""

    def __init__(self, found):
        self.found = set(found)

    def __call__(self, name):
        return f"/usr/bin/{name}" if name in self.found else None


ALL_TOOLS = ("hyprctl", "ydotool", "wtype", "grim", "wl-copy", "wl-paste",
             "busctl")


def make_audit(responses=None, returncodes=None, stderrs=None, tools=ALL_TOOLS,
               stubborn=False, env=ENV):
    runner = FakeInputRunner(FakeHyprland())
    runner.stubborn = stubborn
    runner.responses.update(responses or {})
    runner.returncodes.update(returncodes or {})
    runner.stderrs.update(stderrs or {})
    session = Session(env=env, runner=runner, discoverer=FakeDiscoverer(env))
    desktop = Desktop(session)
    input_ = Input(session)
    return (Audit(session, desktop, Screen(session), input_,
                  A11y(session, input=input_, desktop=desktop),
                  which=Which(tools)), runner)


class AuditTests(unittest.TestCase):
    def test_all_good(self):
        audit, _ = make_audit(responses={GRIM: png(1920, 1080)})

        result = audit.audit(no_a11y=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["critical_failed"], [])
        self.assertEqual(result["capabilities"]["logical_screen"],
                         [1920, 1080])

    def test_missing_session_refuses_critically(self):
        audit, _ = make_audit(env=None)

        result = audit.audit(no_a11y=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["critical_failed"], ["session"])

    def test_missing_critical_tool_refuses(self):
        audit, _ = make_audit(responses={GRIM: png(1920, 1080)},
                              tools=set(ALL_TOOLS) - {"ydotool"})

        result = audit.audit(no_a11y=True)

        self.assertFalse(result["ok"])
        self.assertIn("tool:ydotool", result["critical_failed"])
        self.assertNotIn("tool:wl-copy", result["critical_failed"])

    def test_screenshot_mismatch_refuses(self):
        audit, _ = make_audit(responses={GRIM: png(100, 100)})

        result = audit.audit(no_a11y=True)

        self.assertFalse(result["ok"])
        self.assertIn("screenshot-1to1", result["critical_failed"])

    def test_calibration_failure_refuses(self):
        audit, _ = make_audit(responses={GRIM: png(1920, 1080)},
                              stubborn=True)

        result = audit.audit(no_a11y=True)

        self.assertFalse(result["ok"])
        self.assertIn("calibration", result["critical_failed"])

    def test_no_probe_skips_calibration(self):
        audit, _ = make_audit(responses={GRIM: png(1920, 1080)},
                              stubborn=True)

        result = audit.audit(no_probe=True, no_a11y=True)

        self.assertTrue(result["ok"])
        self.assertNotIn("calibration", result["critical_failed"])

    def test_a11y_failure_is_a_warning_not_a_refusal(self):
        index_key = f"python3 {A11y.bridge_path()} index --max 1"
        audit, _ = make_audit(responses={GRIM: png(1920, 1080)},
                              returncodes={index_key: 1},
                              stderrs={index_key: "Traceback\n"})

        result = audit.audit()

        self.assertTrue(result["ok"])
        a11y_check = next(row for row in result["checks"]
                          if row["name"] == "a11y")
        self.assertFalse(a11y_check["ok"])
        self.assertFalse(a11y_check["critical"])


if __name__ == "__main__":
    unittest.main()
