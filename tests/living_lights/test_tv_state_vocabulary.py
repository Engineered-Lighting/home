"""The two halves of the system must agree about what the television is doing.

``tools/living_lights_tv_states.py`` is what the generated Home Assistant
packages are built from. ``lighting_publisher.stories`` is what the belief
publisher's state machine reads. They are in different trees, they cannot
import each other, and nothing else would notice them drifting apart.

They had drifted. ``buffering`` and ``idle`` meant the screen was on for the
packages and were unknown to the machine, so a television reporting either was
treated there as a possibly dropped connection and held its previous state for
the ten-minute grace while the house was already dimming for a film. ``idle``
is what an Apple TV, a Chromecast and a Roku report while powered on and not
playing, so it is not an exotic case.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PUBLISHER = REPO / "stack" / "services" / "lighting-publisher"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load("ll_tv_states", REPO / "tools" / "living_lights_tv_states.py")


def _stories():
    sys.path.insert(0, str(PUBLISHER))
    try:
        from lighting_publisher import stories
        return stories
    finally:
        sys.path.remove(str(PUBLISHER))


PUB = _stories()


class TheVocabulariesAgree(unittest.TestCase):
    def test_on_states_are_identical(self):
        self.assertEqual(tuple(GEN.TV_ON_STATES), tuple(PUB.TV_ON_STATES),
                         "the packages and the publisher disagree about what 'on' means")

    def test_off_differs_only_by_the_grace_states(self):
        """The one deliberate difference, and it is checked rather than assumed.

        A template has to decide immediately, so the generator treats
        ``unavailable`` and ``unknown`` as off. The machine has a grace window
        and can afford to wait, so they are neither on nor off there.
        """
        self.assertEqual(
            tuple(GEN.TV_OFF_STATES),
            tuple(PUB.TV_OFF_STATES) + tuple(PUB.TV_GRACE_STATES),
            "the only difference permitted is the grace states")

    def test_no_state_is_both_on_and_off(self):
        for source, on, off in (("generator", GEN.TV_ON_STATES, GEN.TV_OFF_STATES),
                                ("publisher", PUB.TV_ON_STATES, PUB.TV_OFF_STATES)):
            with self.subTest(source=source):
                self.assertEqual(set(on) & set(off), set())

    def test_idle_and_buffering_mean_the_screen_is_on(self):
        """The regression this test exists for."""
        for state in ("idle", "buffering"):
            with self.subTest(state=state):
                self.assertIn(state, GEN.TV_ON_STATES)
                self.assertIn(state, PUB.TV_ON_STATES)


if __name__ == "__main__":
    unittest.main()
