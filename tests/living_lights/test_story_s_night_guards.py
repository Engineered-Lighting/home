"""Story S night guards: the generated and hand-written Home Assistant
packages must keep the overnight asleep latch honest.

Run: python3 -m unittest tests/living_lights/test_story_s_night_guards.py

Deterministic and offline: it renders the generators in-process, parses
the checked-in packages, and checks the exact automations that the
2026-09-17 night post-mortem implicated. It never contacts Home Assistant.
"""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PACKAGES = REPO / "ha-config" / "packages"
OBS = PACKAGES / "living_lights_observability.yaml"
GRADIENT = PACKAGES / "living_lights_gradient.yaml"
GOOD_MORNING = PACKAGES / "homeai_good_morning.yaml"
PROACTIVE = REPO / "ha-config" / "homeai_proactive.yaml"

ASLEEP = "input_boolean.living_lights_asleep"
ANY_OCCUPIED = "binary_sensor.living_lights_any_occupied"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _automations(doc: dict) -> dict:
    return {a["id"]: a for a in doc["automation"]}


def _state_conditions(conditions: list) -> list[tuple[str, str]]:
    return [(c.get("entity_id"), c.get("state")) for c in conditions
            if c.get("condition") == "state"]


def _templates(node) -> list[str]:
    """Every value_template / templated string in a parsed package."""
    found: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            found.extend(_templates(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_templates(value))
    elif isinstance(node, str) and ("{{" in node or "{%" in node):
        found.append(node)
    return found


def _strip_jinja_comments(text: str) -> str:
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    return re.sub(r"\s+", " ", text).strip()


class GeneratorDriftTests(unittest.TestCase):
    """The checked-in packages must be exactly what the generators emit."""

    def test_observability_package_matches_generator(self):
        # The generator's own writer is the source of truth for the layout;
        # compare through it rather than re-implementing the assembly.
        import subprocess
        import sys
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".yaml") as tmp:
            subprocess.run([sys.executable, str(REPO / "tools" / "build-living-lights-yaml.py"),
                            "--output", tmp.name], check=True, capture_output=True)
            self.assertEqual(Path(tmp.name).read_text(encoding="utf-8"),
                             OBS.read_text(encoding="utf-8"),
                             "regenerate ha-config/packages/living_lights_observability.yaml")

    def test_gradient_package_matches_generator(self):
        gen = _load("build_gradient_lighting", REPO / "tools" / "build-gradient-lighting.py")
        cents, dw, dh = gen.load_centroids()
        self.assertEqual(gen.build_yaml(cents, dw, dh), GRADIENT.read_text(encoding="utf-8"),
                         "regenerate ha-config/packages/living_lights_gradient.yaml")


class AsleepLatchTests(unittest.TestCase):
    def setUp(self):
        self.autos = _automations(yaml.safe_load(OBS.read_text(encoding="utf-8")))
        self.on = self.autos["living_lights_asleep_on"]

    def test_raw_motion_sensors_no_longer_block_the_latch(self):
        text = yaml.safe_dump(self.on)
        self.assertNotIn("_motion", text)
        gen = _load("build_living_lights_yaml_blockers", REPO / "tools" / "build-living-lights-yaml.py")
        self.assertTrue(all(e.endswith("_person_occupancy") for e in gen.ASLEEP_QUIET_BLOCKERS))
        self.assertEqual(len(gen.ASLEEP_QUIET_BLOCKERS), 8)

    def test_quiet_must_have_lasted_the_idle_window_on_the_tick_path(self):
        gen = _load("build_living_lights_yaml_idle", REPO / "tools" / "build-living-lights-yaml.py")
        templates = [c["value_template"] for c in self.on["conditions"]
                     if c.get("condition") == "template"]
        quiet = [t for t in templates if "idle_s" in t]
        self.assertEqual(len(quiet), 1)
        quiet = quiet[0]
        self.assertIn(f"set idle_s = {gen.ASLEEP_IDLE_MINUTES * 60}", quiet)
        # any_occupied must be off AND have been off for the whole window.
        self.assertIn("states.binary_sensor.living_lights_any_occupied", quiet)
        self.assertIn("any_occ.state != 'off'", quiet)
        self.assertIn("(now() - any_occ.last_changed).total_seconds() < idle_s", quiet)
        # every person-occupancy blocker: on blocks, recently-off blocks,
        # unknown/unavailable never blocks (expand skips missing entities).
        self.assertIn("for obj in expand(blockers)", quiet)
        self.assertIn("obj.state == 'on' or (obj.state == 'off' and (now() - obj.last_changed).total_seconds() < idle_s)", quiet)
        for entity in gen.ASLEEP_QUIET_BLOCKERS:
            self.assertIn(f"'{entity}'", quiet)
        # the tick trigger is still there: the duration check is what gates it.
        self.assertTrue(any(t.get("trigger") == "time_pattern" for t in self.on["triggers"]))

    def test_asleep_off_triggers_keep_their_entities_and_carry_ids(self):
        # M2 (story S residuals): the three original triggers are unchanged
        # but carry ids so the OFF conditions can be judged per trigger; a
        # fourth trigger `arrival` (front-door occupancy turning on, credible
        # only when user_at_home turned on within 60 s) makes the order in
        # which the phone and the door camera report irrelevant; both legacy
        # automations are gated on `not estimate_live` (the estimator mirror
        # owns the latch while it is live). Detailed shape checks live in
        # test_story_t_generator.py.
        off = self.autos["living_lights_asleep_off"]
        self.assertEqual(len(off["triggers"]), 4)
        self.assertEqual([t.get("entity_id") for t in off["triggers"]],
                         [ANY_OCCUPIED, "sensor.living_lights_profile", "input_boolean.user_at_home",
                          "binary_sensor.front_door_person_occupancy"])
        self.assertEqual([t.get("id") for t in off["triggers"]], ["occupancy", "midday", "presence", "arrival"])
        self.assertTrue(all(t.get("trigger") == "state" for t in off["triggers"]))
        # every OFF path still requires the latch to be on and passes through
        # the per-trigger credibility `or`, which names all four ids.
        self.assertIn((ASLEEP, "on"), _state_conditions(off["conditions"]))
        either = next(c for c in off["conditions"] if c.get("condition") == "or")
        self.assertEqual(sorted(yaml.safe_dump(either).count(f"id: {i}") for i in ("occupancy", "midday", "presence", "arrival")),
                         [1, 1, 1, 1])
        writer = [a for a in off["actions"] if a.get("action") == "input_text.set_value"]
        self.assertEqual(writer[0]["data"]["value"], "legacy_off:{{ trigger.id }}")
        gates = {auto_id: [c["value_template"] for c in self.autos[auto_id]["conditions"]
                           if c.get("condition") == "template" and "asleep_from_estimator" in c["value_template"]]
                 for auto_id in ("living_lights_asleep_on", "living_lights_asleep_off")}
        self.assertEqual(len(gates["living_lights_asleep_on"]), 1)
        self.assertEqual(gates["living_lights_asleep_on"], gates["living_lights_asleep_off"])

    def test_new_asleep_writers_exist(self):
        # M2: an ungated hard backstop and the estimator mirror join the pair.
        for auto_id in ("living_lights_asleep_hard_backstop", "living_lights_asleep_mirror"):
            with self.subTest(auto_id):
                self.assertIn(auto_id, self.autos)

    def test_morning_latch_waits_for_the_house_to_be_awake(self):
        for auto_id in ("living_lights_working_hours_morning_latch",
                        "living_lights_working_hours_morning_resume",
                        "living_lights_working_hours_start_catchup"):
            with self.subTest(auto_id):
                self.assertIn((ASLEEP, "off"), _state_conditions(self.autos[auto_id]["conditions"]))


class ColourTemperatureActuatorTests(unittest.TestCase):
    def test_ct_push_only_touches_lights_that_are_on(self):
        autos = _automations(yaml.safe_load(OBS.read_text(encoding="utf-8")))
        actions = autos["living_lights_ct_actuator"]["actions"]
        self.assertEqual(actions[0].keys(), {"variables"})
        lit = actions[0]["variables"]["lit_lights"]
        self.assertIn("selectattr('state', 'eq', 'on')", lit)
        for light in ("light.office", "light.front_left", "light.front_right", "light.rear_left",
                      "light.rear_right", "light.sink", "light.island_left", "light.island_right"):
            self.assertIn(f"'{light}'", lit)
        guard = actions[1]["if"][0]
        self.assertEqual(guard["condition"], "template")
        self.assertIn("lit_lights | count > 0", guard["value_template"])
        turn_on = actions[1]["then"][0]
        self.assertEqual(turn_on["action"], "light.turn_on")
        self.assertEqual(turn_on["target"]["entity_id"], "{{ lit_lights }}")
        self.assertIn("color_temp_kelvin", turn_on["data"])
        self.assertNotIn("brightness", yaml.safe_dump(turn_on))
        # no unconditional turn_on survives anywhere in the actuator
        self.assertEqual(sum(1 for a in actions if a.get("action") == "light.turn_on"), 0)


class GradientCooldownGateTests(unittest.TestCase):
    def test_gradient_gate_matches_the_pilot_gate(self):
        actuators = _load("build_living_lights_actuators", REPO / "tools" / "build-living-lights-actuators.py")
        pilot_gate = actuators._manual_cooldown_gate_template(
            "sensor.living_room_sofa_lighting_state", "sofa",
            "binary_sensor.living_room_sofa_person_occupancy_stable", 0)
        grad = yaml.safe_load(GRADIENT.read_text(encoding="utf-8"))
        sofa = _automations(grad)["living_lights_gradient_sofa"]
        gates = [c["value_template"] for c in sofa["conditions"]
                 if c.get("condition") == "template" and "last_manual_at" in c["value_template"]]
        self.assertEqual(len(gates), 1)
        gate = gates[0]
        self.assertNotIn("or asleep", gate)
        self.assertIn("'asleep'", gate)
        self.assertIn("| as_local", gate)
        # Same logic as the pilot once comments and whitespace are removed.
        self.assertEqual(_strip_jinja_comments(gate),
                         _strip_jinja_comments(pilot_gate.replace("- condition: template", "")
                                               .replace("value_template: >-", "")))


class HandWrittenEntryPointsTests(unittest.TestCase):
    def test_good_morning_never_energizes_a_sleeping_house(self):
        doc = yaml.safe_load(GOOD_MORNING.read_text(encoding="utf-8"))
        auto = _automations(doc)["homeai_good_morning"]
        self.assertIn((ASLEEP, "off"), _state_conditions(auto["conditions"]))

    def test_return_home_scene_is_gated_on_the_asleep_latch(self):
        doc = yaml.safe_load(PROACTIVE.read_text(encoding="utf-8"))
        seq = doc["script"]["homeai_return_home"]["sequence"]
        leading = [s for s in seq if s.get("condition")]
        self.assertIn((ASLEEP, "off"), _state_conditions(leading))
        # the gate sits before any light.turn_on
        first_action = next(i for i, s in enumerate(seq) if not s.get("condition"))
        gate_index = next(i for i, s in enumerate(seq) if s.get("entity_id") == ASLEEP)
        self.assertLess(gate_index, first_action)


class TemplateSyntaxTests(unittest.TestCase):
    """Every Jinja template in the touched packages must at least parse."""

    def test_every_template_parses(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        for path in (OBS, GRADIENT, GOOD_MORNING, PROACTIVE):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for template in _templates(doc):
                with self.subTest(path=path.name, template=template[:60]):
                    env.parse(template)


if __name__ == "__main__":
    unittest.main()
