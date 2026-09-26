"""Tests for the CLI scaffold: usage, exit codes, the ERR[code] contract."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from computer_control_cli.cli import Cli  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports


class CliScaffoldTests(unittest.TestCase):
    def test_no_verb_prints_usage_and_fails(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = Cli([]).run()

        self.assertEqual(code, 2)
        self.assertIn("usage:", stderr.getvalue())

    def test_help_prints_usage_and_succeeds(self):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = Cli(["--help"]).run()

        self.assertEqual(code, 0)
        self.assertIn("usage:", stdout.getvalue())

    def test_unknown_verb_is_a_usage_failure(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = Cli(["nope"]).run()

        self.assertEqual(code, 2)

    def test_main_returns_the_exit_code(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(Cli.main([]), 2)

    def test_refusals_print_the_err_line(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = Cli([]).report(ControlError("bad-args", "nope"))

        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue().strip(), "ERR[bad-args]: nope")


if __name__ == "__main__":
    unittest.main()
