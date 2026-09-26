"""Tests for the refusal type: the ERR[code] contract's other half."""

import unittest

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports


class ControlErrorTests(unittest.TestCase):
    def test_carries_code_and_message(self):
        error = ControlError("window-not-found", "focus: no window matching 'x'")

        self.assertEqual(error.code, "window-not-found")
        self.assertEqual(error.message, "focus: no window matching 'x'")
        self.assertEqual(str(error), "focus: no window matching 'x'")

    def test_is_catchable_as_exception(self):
        with self.assertRaises(ControlError) as caught:
            raise ControlError("bad-args", "move: X must be an integer")

        self.assertEqual(caught.exception.code, "bad-args")

    def test_code_defaults_to_generic_error(self):
        self.assertEqual(ControlError().code, "error")
        self.assertEqual(ControlError().message, "")

    def test_from_line_reads_a_refusal(self):
        error = ControlError.from_line("ERR[stale-tree]: the element is gone")

        assert error is not None
        self.assertEqual(error.code, "stale-tree")
        self.assertEqual(error.message, "the element is gone")

    def test_from_line_ignores_prose(self):
        self.assertIsNone(ControlError.from_line("just a message"))
        self.assertIsNone(ControlError.from_line(""))


if __name__ == "__main__":
    unittest.main()
