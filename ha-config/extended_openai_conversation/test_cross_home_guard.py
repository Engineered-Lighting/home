#!/usr/bin/env python3
"""Standalone tests for the cross-home action guard (no Home Assistant needed)."""

from __future__ import annotations

import ast
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


class ConversationWiringTests(unittest.TestCase):
    """The live conversation agent runs the guard before any tool or the model."""

    @classmethod
    def setUpClass(cls) -> None:
        tree = ast.parse(Path(__file__).with_name("conversation.py").read_text(encoding="utf-8"))
        cls.module = tree
        cls.process = next(node for node in ast.walk(tree)
                           if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_process")

    def _first_call_line(self, name: str) -> int:
        lines = [node.lineno for node in ast.walk(self.process) if isinstance(node, ast.Call)
                 and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name)]
        self.assertTrue(lines, f"async_process never calls {name}")
        return min(lines)

    def test_the_guard_is_imported_from_this_package(self) -> None:
        self.assertTrue(any(isinstance(node, ast.ImportFrom) and node.module == "cross_home_guard" and node.level == 1
                            and any(alias.name == "cross_home_action_reply" for alias in node.names)
                            for node in self.module.body))

    def test_the_guard_runs_before_the_camera_preroute_and_the_model(self) -> None:
        guard_line = self._first_call_line("cross_home_action_reply")
        self.assertLess(guard_line, self._first_call_line("_should_preroute_grounded_look"))
        self.assertLess(guard_line, self._first_call_line("_async_handle_message"))


if __name__ == "__main__":
    result = unittest.main(exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
