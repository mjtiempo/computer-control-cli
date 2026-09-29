"""Tests for the action log: redacted, fail-open JSONL."""

import json
import os
import stat
import tempfile
import unittest

from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
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

    def test_off_disables_the_log(self):
        config = Config(env={"CCC_LOG": "off"})
        log = ActionLog(config)

        log.write("move", ok=True)

        self.assertEqual(log.entries(), [])
        self.assertFalse(os.path.exists(log.path()))

    def test_since_filters_by_timestamp(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as handle:
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:00.000+00:00", "action": "one",
                 "ok": True}) + "\n")
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:01.000+00:00", "action": "two",
                 "ok": True}) + "\n")
        log = ActionLog(self.config)

        actions = [entry["action"] for entry in
                   log.entries(50, since="2026-01-01T00:00:01.000+00:00")]

        self.assertEqual(actions, ["two"])

    def test_since_compares_offsets_as_times_not_text(self):
        # 02:15+01:00 is 01:15Z: AFTER 02:30+02:00 (00:30Z), though it sorts
        # before it as text — the DST change reverses the string order.
        later = {"ts": "2025-10-26T02:15:00+01:00"}
        earlier = {"ts": "2025-10-26T02:15:00+02:00"}

        self.assertTrue(ActionLog._kept(later,
                                        "2025-10-26T02:30:00+02:00"))
        self.assertFalse(ActionLog._kept(earlier,
                                         "2025-10-26T02:30:00+01:00"))

    def test_render_is_one_line_per_entry(self):
        lines = ActionLog.render([
            {"ts": "2026-01-01T00:00:00", "action": "move", "ok": True},
            {"ts": "2026-01-01T00:00:01", "action": "focus", "ok": False,
             "code": "window-not-found"},
        ])

        self.assertEqual(lines[0], "2026-01-01T00:00:00 move ok")
        self.assertIn("focus fail window-not-found", lines[1])

    def test_follow_yields_the_backlog_then_appended_entries(self):
        log = ActionLog(self.config)
        log.write("one", ok=True)

        stream = log.follow(count=5, poll=0.01)
        self.assertEqual(next(stream)["action"], "one")

        log.write("two", ok=True)
        self.assertEqual(next(stream)["action"], "two")

    def test_follow_honours_since(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as handle:
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:00.000+00:00", "action": "old",
                 "ok": True}) + "\n")
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:01.000+00:00", "action": "new",
                 "ok": True}) + "\n")
        log = ActionLog(self.config)

        stream = log.follow(count=5, since="2026-01-01T00:00:01.000+00:00",
                            poll=0.01)

        self.assertEqual(next(stream)["action"], "new")

    def test_follow_recovers_when_the_log_is_truncated(self):
        log = ActionLog(self.config)
        log.write("a" * 200, ok=True)

        stream = log.follow(count=1, poll=0.01)
        self.assertEqual(next(stream)["action"], "a" * 200)

        with open(self.path, "w"):
            pass
        log.write("two", ok=True)

        self.assertEqual(next(stream)["action"], "two")

    def test_follow_waits_for_a_log_that_does_not_exist_yet(self):
        log = ActionLog(self.config)

        stream = log.follow(count=1, poll=0.01)
        log.write("first", ok=True)

        self.assertEqual(next(stream)["action"], "first")

    def test_follow_finishes_an_entry_that_was_half_written(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        half = ('{"ts": "2026-01-01T00:00:02.000+00:00", '
                '"action": "broken", "ok":')
        with open(self.path, "w") as handle:
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:01.000+00:00", "action": "one",
                 "ok": True}) + "\n")
            handle.write(half)          # no newline: the writer is mid-line
        log = ActionLog(self.config)

        stream = log.follow(count=5, poll=0.01)
        self.assertEqual(next(stream)["action"], "one")

        with open(self.path, "a") as handle:
            handle.write(" true}\n")            # the writer finishes the line
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:03.000+00:00", "action": "two",
                 "ok": True}) + "\n")

        self.assertEqual(next(stream)["action"], "broken")

    def test_follow_filters_since_on_appended_entries(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as handle:
            handle.write(json.dumps(
                {"ts": "2099-01-01T00:00:00.000+00:00", "action": "seed",
                 "ok": True}) + "\n")
        log = ActionLog(self.config)

        stream = log.follow(count=5, since="2099-01-01T00:00:00.000+00:00",
                            poll=0.01)
        self.assertEqual(next(stream)["action"], "seed")

        with open(self.path, "a") as handle:
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:01.000+00:00", "action": "old",
                 "ok": True}) + "\n")
            handle.write(json.dumps(
                {"ts": "2099-01-01T00:00:01.000+00:00", "action": "new",
                 "ok": True}) + "\n")

        self.assertEqual(next(stream)["action"], "new")

    def test_follow_reads_a_replaced_log_from_the_top(self):
        log = ActionLog(self.config)
        log.write("a" * 200, ok=True)

        stream = log.follow(count=1, poll=0.01)
        self.assertEqual(next(stream)["action"], "a" * 200)

        rotated = self.path + ".1"
        with open(rotated, "w") as handle:            # a bigger replacement
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:01.000+00:00", "action": "rotated",
                 "ok": True, "pad": "r" * 300}) + "\n")
        os.replace(rotated, self.path)

        with open(self.path, "a") as handle:
            handle.write(json.dumps(
                {"ts": "2026-01-01T00:00:02.000+00:00", "action": "after",
                 "ok": True}) + "\n")

        self.assertEqual(next(stream)["action"], "rotated")

    def test_follow_refuses_a_log_it_cannot_read(self):
        log = ActionLog(Config(env={"CCC_LOG": self._tmp.name}))

        with self.assertRaises(ControlError) as caught:
            next(log.follow(count=1, poll=0.01))

        self.assertEqual(caught.exception.code, "no-file")

    def test_the_log_is_private(self):
        log = ActionLog(self.config)

        log.write("switch", ok=True, args=[2])

        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600, "the action log is not world-readable")

    def test_a_non_regular_log_refuses(self):
        config = Config(env={"CCC_LOG": self._tmp.name})    # a directory

        with self.assertRaises(ControlError) as caught:
            ActionLog(config).entries(5)

        self.assertEqual(caught.exception.code, "no-file")

    def test_render_escapes_control_characters(self):
        lines = ActionLog.render([{
            "ts": "2026-01-01T00:00:00", "action": "focus", "ok": False,
            "code": "ambiguous-window",
            "detail": "\x1b]0;pwned\x07\x1b[2J"}])

        self.assertNotIn("\x1b", lines[0])
        self.assertNotIn("\x07", lines[0])
        self.assertIn("\\x1b", lines[0])


if __name__ == "__main__":
    unittest.main()
