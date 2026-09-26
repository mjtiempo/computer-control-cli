"""Tests for the action-outcome contract.

Every desktop-changing verb answers with the same four keys, and the pairs
are constrained: `verified` means the effect was read back, an absent effect
is never a success, and a failure is never verified.
"""

import unittest

from computer_control_cli.outcome import Outcome  # pi-lens-ignore: reportMissingImports


class OutcomeTests(unittest.TestCase):
    def test_confirmed_outcome(self):
        result = Outcome.confirmed("dispatch", address="0xabc")

        self.assertTrue(result.ok)
        self.assertTrue(result.verified)
        self.assertEqual(result.effect, "confirmed")
        self.assertEqual(result.rung, "dispatch")
        self.assertEqual(result.to_dict()["address"], "0xabc")

    def test_unverifiable_outcome_is_honest_not_failed(self):
        result = Outcome.unverifiable("px", x=10, y=20)

        self.assertTrue(result.ok)
        self.assertFalse(result.verified)
        self.assertEqual(result.effect, "unverifiable")

    def test_unconfirmed_outcome_is_a_failure(self):
        result = Outcome.unconfirmed("dispatch", remaining=["foot"])

        self.assertFalse(result.ok)
        self.assertFalse(result.verified)
        self.assertEqual(result.effect, "unconfirmed")

    def test_unconfirmed_can_be_carried_as_unverifiable_data(self):
        """`close-apps` reports survivors as data with ok=False."""
        result = Outcome.unverifiable("dispatch", ok=False, remaining=["x"])

        self.assertFalse(result.ok)
        self.assertFalse(result.verified)

    def test_key_order_of_the_reply(self):
        keys = list(Outcome.confirmed("dispatch", address="0xabc").to_dict())

        self.assertEqual(keys[:4], ["ok", "verified", "effect", "rung"])

    def test_invariants_are_enforced(self):
        with self.assertRaises(AssertionError):
            Outcome(ok=False, verified=True, effect="confirmed", rung="x")
        with self.assertRaises(AssertionError):
            Outcome(ok=True, verified=True, effect="unverifiable", rung="x")
        with self.assertRaises(AssertionError):
            Outcome(ok=True, verified=False, effect="unconfirmed", rung="x")
        with self.assertRaises(AssertionError):
            Outcome(ok=True, verified=False, effect="confirmed", rung="x")


if __name__ == "__main__":
    unittest.main()
