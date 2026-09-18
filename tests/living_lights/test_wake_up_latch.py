"""Wake-up latch guard (plan M2, round-1 finding S12).

Run: python3 -m unittest tests/living_lights/test_wake_up_latch.py

Deterministic and offline: it parses the checked-in hand-written package
ha-config/packages/living_lights_override_lifecycle.yaml and pins the shape
of the living_lights_woke_up_on_wake automation. It never contacts Home
Assistant.

What it pins:
  - the automation no longer triggers on input_boolean.living_lights_asleep
    clearing (a 10-minute night excursion clears the latch through the
    generated 10-minute occupancy rule, which used to energize the house);
  - it triggers on binary_sensor.living_lights_any_occupied being ON for
    15 minutes, and only that;
  - the conditions are exactly: asleep off, woke_up_today off, user_at_home
    on, and the 04:30-11:00 morning window;
  - it still turns on woke_up_today and writes the logbook line.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
LIFECYCLE = REPO / "ha-config" / "packages" / "living_lights_override_lifecycle.yaml"

ASLEEP = "input_boolean.living_lights_asleep"
ANY_OCCUPIED = "binary_sensor.living_lights_any_occupied"
WOKE_UP_TODAY = "input_boolean.living_lights_woke_up_today"
USER_AT_HOME = "input_boolean.user_at_home"
MORNING_WINDOW = "{{ 4.5 <= (now().hour + now().minute / 60) < 11.0 }}"
HOLD_MINUTES = 15


def _automations(doc: dict) -> dict:
    return {a["id"]: a for a in doc["automation"]}


def _for_seconds(spec) -> float:
    """Normalise a Home Assistant `for:` (mapping or HH:MM:SS) to seconds."""
    if isinstance(spec, dict):
        return (spec.get("hours", 0) * 3600 + spec.get("minutes", 0) * 60
                + spec.get("seconds", 0))
    if isinstance(spec, str):
        parts = [float(x) for x in spec.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(spec)


class WakeUpLatchTests(unittest.TestCase):
    def setUp(self):
        doc = yaml.safe_load(LIFECYCLE.read_text(encoding="utf-8"))
        self.autos = _automations(doc)
        self.auto = self.autos["living_lights_woke_up_on_wake"]
        self.states = [(c.get("entity_id"), c.get("state"))
                       for c in self.auto["conditions"]
                       if c.get("condition") == "state"]
        self.templates = [c["value_template"] for c in self.auto["conditions"]
                          if c.get("condition") == "template"]

    def test_single_trigger_is_any_occupied_on_for_fifteen_minutes(self):
        triggers = self.auto["triggers"]
        self.assertEqual(len(triggers), 1)
        trigger = triggers[0]
        self.assertEqual(trigger.get("trigger"), "state")
        self.assertEqual(trigger.get("entity_id"), ANY_OCCUPIED)
        self.assertEqual(trigger.get("to"), "on")
        # no `from`: the hold must start from any prior state, including
        # unavailable -> on after a restart, never only from off.
        self.assertNotIn("from", trigger)
        self.assertIn("for", trigger)
        self.assertEqual(_for_seconds(trigger["for"]), HOLD_MINUTES * 60)

    def test_no_trigger_on_the_asleep_boolean_remains(self):
        # S12: a 10-minute excursion clears the latch (generated 10-minute
        # occupancy rule); the latch clearing must not be a wake signal.
        for trigger in self.auto["triggers"]:
            self.assertNotEqual(trigger.get("entity_id"), ASLEEP)
            self.assertNotIn(ASLEEP, yaml.safe_dump(trigger))
        self.assertNotIn("from", yaml.safe_dump(self.auto["triggers"]))

    def test_hold_is_longer_than_the_generated_occupancy_clear(self):
        # The excursion that clears the latch lasts 10 min through the
        # generated any_occupied rule; the wake hold must outlast it, or a
        # cleared latch and a still-occupied house would latch at once.
        obs = yaml.safe_load((REPO / "ha-config" / "packages"
                              / "living_lights_observability.yaml").read_text(encoding="utf-8"))
        off = _automations(obs)["living_lights_asleep_off"]
        occupancy = [t for t in off["triggers"] if t.get("id") == "occupancy"]
        self.assertEqual(len(occupancy), 1)
        self.assertEqual(occupancy[0].get("entity_id"), ANY_OCCUPIED)
        clear_seconds = _for_seconds(occupancy[0].get("for", 0))
        self.assertGreater(_for_seconds(self.auto["triggers"][0]["for"]), clear_seconds)

    def test_state_conditions_are_exactly_the_three_gates(self):
        self.assertEqual(sorted(self.states), sorted([
            (ASLEEP, "off"),
            (WOKE_UP_TODAY, "off"),
            (USER_AT_HOME, "on"),
        ]))

    def test_morning_window_is_the_only_template_condition(self):
        self.assertEqual(self.templates, [MORNING_WINDOW])
        # the old "any_occupied on for 2 min" template condition is gone:
        # the `for` on the trigger owns the duration now.
        self.assertNotIn(ANY_OCCUPIED, yaml.safe_dump(self.auto["conditions"]))
        self.assertNotIn("last_changed", yaml.safe_dump(self.auto["conditions"]))

    def test_no_other_condition_kinds(self):
        kinds = sorted({c.get("condition") for c in self.auto["conditions"]})
        self.assertEqual(kinds, ["state", "template"])
        self.assertEqual(len(self.auto["conditions"]), 4)

    def test_actions_latch_woke_up_today_and_keep_the_logbook_line(self):
        actions = self.auto["actions"]
        self.assertEqual(actions[0]["action"], "input_boolean.turn_on")
        self.assertEqual(actions[0]["target"]["entity_id"], WOKE_UP_TODAY)
        logbook = [a for a in actions if a.get("action") == "logbook.log"]
        self.assertEqual(len(logbook), 1)
        self.assertEqual(logbook[0]["data"]["name"], "Living Lights morning")
        self.assertEqual(logbook[0]["data"]["entity_id"], WOKE_UP_TODAY)
        self.assertIn("woke_up_today latched", logbook[0]["data"]["message"])
        # nothing in this automation touches a light or an override helper
        self.assertNotIn("light.", yaml.safe_dump(actions))
        self.assertNotIn("override_text", yaml.safe_dump(actions))

    def test_mode_single_and_id_unchanged(self):
        # good-morning and the simulator reach it by id; the id must not move.
        self.assertEqual(self.auto["mode"], "single")
        self.assertEqual(self.auto["id"], "living_lights_woke_up_on_wake")

    def test_comment_explains_the_night_excursion(self):
        # The comment is the deploy-time rationale; keep the S12 story in it.
        text = LIFECYCLE.read_text(encoding="utf-8")
        head = text[:text.index("id: living_lights_woke_up_on_wake")]
        block = head[head.rindex("# -- Wake-up latch"):]
        # comment lines wrap; compare on the prose, not the line breaks
        prose = " ".join(line.strip().lstrip("#").strip() for line in block.splitlines())
        for phrase in ("10-minute", "excursion", "clears the latch", "energize"):
            self.assertIn(phrase, prose)

    def test_asleep_failsafe_untouched(self):
        # test_story_s_night_guards and the deploy protocol rely on it.
        failsafe = self.autos["living_lights_asleep_failsafe"]
        self.assertEqual(failsafe["triggers"][0].get("entity_id"), ASLEEP)
        self.assertEqual(failsafe["triggers"][0].get("to"), "on")
        self.assertEqual(_for_seconds(failsafe["triggers"][0]["for"]), 12 * 3600)


class TemplateSyntaxTests(unittest.TestCase):
    def test_every_template_parses(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        doc = yaml.safe_load(LIFECYCLE.read_text(encoding="utf-8"))
        text = yaml.safe_dump(doc)
        self.assertIn("{{", text)
        for auto in doc["automation"]:
            for cond in auto.get("conditions", []):
                if cond.get("condition") == "template":
                    with self.subTest(auto=auto["id"], template=cond["value_template"][:60]):
                        env.parse(cond["value_template"])


if __name__ == "__main__":
    unittest.main()
