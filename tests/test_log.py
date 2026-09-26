"""Tests for the action log: redacted, fail-open JSONL."""

import json
import os
import tempfile
import unittest

from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports
from computer_control_cli.log import ActionLog  # pi-lens-ignore: reportMissingImports


class ActionLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "actions.jsonl")
        self.config = Config(env={"CCC_LOG": self.path})

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_then_read_entries(self):
        log = ActionLog(self.config)

        log.write("switch", ok=True, args=[2])

        entry = log.entries()[0]
        self.assertEqual(entry["action"], "switch")
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["args"], [2])
        self.assertIn("ts", entry)

    def test_typed_text_is_redacted_by_default(self):
        log = ActionLog(self.config)

        log.write("type", text="hunter2 and more")

        entry = log.entries()[0]
        self.assertNotIn("text", entry)
        self.assertEqual(entry["text_chars"], len("hunter2 and more"))
        self.assertEqual(entry["text_preview"], "hunter2 and more"[:8])

    def test_full_text_logging_keeps_the_text(self):
        config = Config(env={"CCC_LOG": self.path, "CCC_LOG_TEXT": "full"})
        log = ActionLog(config)

        log.write("type", text="hunter2")

        self.assertEqual(log.entries()[0]["text"], "hunter2")

    def test_off_logging_drops_text_entirely(self):
        config = Config(env={"CCC_LOG": self.path, "CCC_LOG_TEXT": "off"})
        log = ActionLog(config)

        log.write("type", text="hunter2")

        entry = log.entries()[0]
        self.assertNotIn("text", entry)
        self.assertNotIn("text_chars", entry)

    def test_refusals_are_recorded_with_their_code(self):
        log = ActionLog(self.config)

        log.write("focus", ok=False, code="window-not-found",
                  detail="no window matching 'x'")

        entry = log.entries()[0]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["code"], "window-not-found")

    def test_unwritable_path_fails_open(self):
        config = Config(env={"CCC_LOG": "/proc/definitely/not/writable.jsonl"})
        log = ActionLog(config)

        log.write("switch", ok=True)  # must not raise

    def test_entries_are_newest_last_and_capped(self):
        log = ActionLog(self.config)
        for index in range(5):
            log.write("move", ok=True, args=[index])

        entries = log.entries(3)

        self.assertEqual([e["args"][0] for e in entries], [2, 3, 4])

    def test_file_is_jsonl(self):
        log = ActionLog(self.config)
        log.write("state", ok=True)

        with open(self.path) as handle:
            self.assertEqual(json.loads(handle.readline())["action"], "state")


if __name__ == "__main__":
    unittest.main()
