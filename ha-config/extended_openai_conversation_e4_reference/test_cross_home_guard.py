#!/usr/bin/env python3
"""Standalone tests for the cross-home action guard (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

_spec = importlib.util.spec_from_file_location("cross_home_guard", Path(__file__).with_name("cross_home_guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


class CrossHomeGuardTests(unittest.TestCase):
    def test_actions_naming_victoria_or_both_homes_get_the_fixed_reply(self) -> None:
        for text in ("Turn off the kitchen light in Victoria", "switch on the lights in both homes",
                     "Could you dim the Victoria living room to 20%?", "lock the front door in victoria",
                     "turn off the lights at the other house", "Please set the den lights in both houses to 50%"):
            self.assertEqual(guard.cross_home_action_reply(text), guard.REPLY, text)

    def test_questions_and_local_requests_pass_through(self) -> None:
        for text in ("Is the kitchen light on in Victoria?", "what's the weather in victoria",
                     "turn off the kitchen light", "how many lights are on in both homes",
                     "tell me about victoria", "", None, "set a timer for ten minutes"):
            self.assertIsNone(guard.cross_home_action_reply(text), text)

    def test_the_reply_points_to_confirmed_lighting_in_home(self) -> None:
        self.assertIn("Home chat", guard.REPLY)
        self.assertIn("Victoria", guard.REPLY)


if __name__ == "__main__":
    result = unittest.main(exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
