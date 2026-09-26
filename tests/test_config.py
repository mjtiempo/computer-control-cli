"""Tests for the resolved configuration (one place that reads the env)."""

import unittest

from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports


class ConfigTests(unittest.TestCase):
    def test_defaults_under_xdg_state(self):
        config = Config(env={"HOME": "/home/mark"})

        self.assertEqual(
            config.log_path(),
            "/home/mark/.local/state/computer-control/actions.jsonl")

    def test_xdg_state_home_wins_over_home(self):
        config = Config(env={"HOME": "/home/mark", "XDG_STATE_HOME": "/state"})

        self.assertEqual(config.log_path(), "/state/computer-control/actions.jsonl")

    def test_log_path_override(self):
        config = Config(env={"CCC_LOG": "/tmp/ccc.jsonl"})

        self.assertEqual(config.log_path(), "/tmp/ccc.jsonl")

    def test_text_logging_modes(self):
        self.assertEqual(Config(env={}).log_text(), "preview")
        self.assertEqual(Config(env={"CCC_LOG_TEXT": "full"}).log_text(), "full")
        self.assertEqual(Config(env={"CCC_LOG_TEXT": "junk"}).log_text(), "preview")

    def test_env_defaults_to_the_process_environment(self):
        self.assertIsInstance(Config().log_path(), str)


if __name__ == "__main__":
    unittest.main()
