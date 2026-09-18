"""Story T actuator tests: the generated pilots turn a zone off when the
classifier predicts exactly 0, issue nothing when the prediction is absent,
and keep their turn-on calls otherwise; the override branch stays turn-on
only; the TV predicate is read through binary_sensor.living_lights_tv_playing;
the sofa gradient is gated on the pilot's base level; the checked-in
packages are exactly what the generators emit.

Run: python3 -m unittest tests/living_lights/test_story_t_actuators.py

Deterministic and offline: it renders the generators in-process (the
learning generator in a scratch copy of tools/), parses the checked-in
packages, and never contacts Home Assistant. Branch outcomes are read by
walking the rendered `if` steps with a given `predicted_bri`; guards on
anything else (zone brightness, the cooldown gate) are treated as unknown
and both sides are collected, so "issues nothing" means nothing on any path.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
PACKAGES = REPO / "ha-config" / "packages"
GRADIENT = PACKAGES / "living_lights_gradient.yaml"
LEARNING = PACKAGES / "living_lights_learning.yaml"
MANUAL = PACKAGES / "living_lights_manual_detection.yaml"

TV_SENSOR_TEMPLATE = "{{ is_state('binary_sensor.living_lights_tv_playing', 'on') }}"
PREDICATE_RE = re.compile(r"\{\{ predicted_bri (==|>|<) (-?\d+) \}\}")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _actuators():
    return _load("build_living_lights_actuators_t", TOOLS / "build-living-lights-actuators.py")


def _pilot_slugs(gen) -> list[str]:
    return [slug for slug, targets in gen.LIGHT_TARGETS.items() if targets is not None]


def _state_of(option: dict) -> str | None:
    """The classifier state a `choose` option selects on, or None."""
    for cond in option.get("conditions", []):
        match = re.search(r"new_state == '([a-z_]+)'", cond.get("value_template", ""))
        if match:
            return match.group(1)
    return None


def _find_choose(node):
    """The pilot's state `choose` step (the one with a `present` option)."""
    if isinstance(node, dict):
        if "choose" in node and any(_state_of(opt) == "present" for opt in node["choose"]):
            return node
        for value in node.values():
            found = _find_choose(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_choose(value)
            if found is not None:
                return found
    return None


def _branches(doc: dict) -> dict:
    """state name -> sequence, plus 'default'."""
    choose = _find_choose(doc)
    out = {"default": choose["default"]}
    for opt in choose["choose"]:
        out[_state_of(opt)] = opt["sequence"]
    return out


def _issued(seq: list, predicted: int) -> list[dict]:
    """Service calls a sequence can issue for `predicted_bri == predicted`.
    `if` steps whose only condition is a predicted_bri comparison are
    decided; any other guard is unknown and both sides are collected."""
    out: list[dict] = []
    for step in seq:
        if "if" in step:
            conds = step["if"]
            verdict = None
            if len(conds) == 1 and conds[0].get("condition") == "template":
                match = PREDICATE_RE.fullmatch(conds[0]["value_template"].strip())
                if match:
                    op, n = match.group(1), int(match.group(2))
                    verdict = {"==": predicted == n, ">": predicted > n, "<": predicted < n}[op]
            if verdict is None:
                out += _issued(step.get("then", []), predicted)
                out += _issued(step.get("else", []), predicted)
            elif verdict:
                out += _issued(step.get("then", []), predicted)
            else:
                out += _issued(step.get("else", []), predicted)
        elif "action" in step:
            out.append(step)
    return out


def _variables(doc: dict) -> dict:
    found = {}

    def walk(node):
        if isinstance(node, dict):
            if "variables" in node and isinstance(node["variables"], dict):
                found.update(node["variables"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    return found


class PilotBranchTests(unittest.TestCase):
    """One rendered pilot (sofa: four lights) walked at -1, 0 and 50."""

    @classmethod
    def setUpClass(cls):
        cls.gen = _actuators()
        cls.slug = "sofa"
        cls.text = cls.gen.emit_actuator(cls.slug, cls.gen.LIGHT_TARGETS[cls.slug])
        cls.doc = yaml.safe_load(cls.text)
        cls.branches = _branches(cls.doc)
        cls.lights = [t[1] for t in cls.gen.LIGHT_TARGETS[cls.slug] if t[0] == "light"]

    def test_predicted_bri_is_read_with_a_negative_sentinel(self):
        variables = _variables(self.doc)
        self.assertEqual(
            variables["predicted_bri"],
            "{{ state_attr('sensor.living_room_sofa_lighting_state', 'predicted_brightness_pct') | int(-1) }}")

    def test_absent_attribute_issues_nothing_in_present_and_default(self):
        for name in ("present", "default", "pass_through", "anticipated"):
            with self.subTest(name):
                self.assertEqual(_issued(self.branches[name], -1), [])

    def test_zero_turns_every_light_off_and_never_on(self):
        for name, transition in (("present", self.gen.RAMP_SLOW_S),
                                 ("default", self.gen.DEFAULT_TRANSITION_S)):
            with self.subTest(name):
                calls = _issued(self.branches[name], 0)
                self.assertEqual([c["action"] for c in calls], ["light.turn_off"] * len(self.lights))
                self.assertEqual([c["target"]["entity_id"] for c in calls], self.lights)
                for call in calls:
                    self.assertEqual(call["data"], {"transition": transition})
        for name in ("pass_through", "anticipated"):
            with self.subTest(name):
                self.assertEqual(_issued(self.branches[name], 0), [])

    def test_fast_present_call_sits_under_the_positive_guard(self):
        # No path issues the safe_initial call unless predicted_bri > 0.
        for predicted in (-1, 0):
            calls = _issued(self.branches["present"], predicted)
            self.assertFalse(any("safe_initial" in yaml.safe_dump(c) for c in calls))
        calls = _issued(self.branches["present"], 50)
        fast = [c for c in calls if "[safe_initial, 1] | max" in c["data"].get("brightness_pct", "")]
        self.assertEqual([c["target"]["entity_id"] for c in fast], self.lights)

    def test_positive_prediction_keeps_the_existing_turn_on_calls(self):
        present = _issued(self.branches["present"], 50)
        self.assertTrue(present)
        self.assertTrue(all(c["action"] == "light.turn_on" for c in present))
        slow = [c for c in present if c["data"].get("brightness_pct") == "{{ [predicted_bri, 1] | max }}"]
        self.assertEqual([c["target"]["entity_id"] for c in slow], self.lights)
        self.assertEqual([c["data"]["transition"] for c in slow], [self.gen.RAMP_SLOW_S] * len(self.lights))
        for call in present:
            self.assertIn("color_temp_kelvin", call["data"])
        default = _issued(self.branches["default"], 50)
        self.assertEqual([c["action"] for c in default], ["light.turn_on"] * len(self.lights))
        self.assertEqual([c["target"]["entity_id"] for c in default], self.lights)
        for call in default:
            self.assertEqual(call["data"]["brightness_pct"], "{{ [predicted_bri, 1] | max }}")
            self.assertEqual(call["data"]["transition"], self.gen.DEFAULT_TRANSITION_S)
        for name in ("pass_through", "anticipated"):
            calls = _issued(self.branches[name], 50)
            self.assertEqual([c["target"]["entity_id"] for c in calls], self.lights)
            for call in calls:
                self.assertEqual(call["action"], "light.turn_on")
                self.assertIn("[predicted_bri, ", call["data"]["brightness_pct"])
                self.assertIn("| max", call["data"]["brightness_pct"])

    def test_override_branch_is_still_turn_on_only(self):
        seq = self.branches["presence_override"]
        self.assertEqual(seq[0].keys(), {"variables"})
        self.assertIn("ov_bri", seq[0]["variables"])
        calls = seq[1:]
        self.assertEqual([c.get("action") for c in calls], ["light.turn_on"] * len(self.lights))
        self.assertEqual([c["target"]["entity_id"] for c in calls], self.lights)
        self.assertNotIn("turn_off", yaml.safe_dump(seq))
        self.assertFalse(any("if" in step for step in seq))

    def test_vacant_and_away_branches_are_unchanged(self):
        vacant = yaml.safe_dump(self.branches["vacant"])
        self.assertIn("{{ predicted_bri > 0 }}", vacant)
        self.assertEqual(vacant.count("light.turn_off"), len(self.lights))
        away = _issued(self.branches["away"], -1)
        self.assertEqual([c["action"] for c in away], ["light.turn_off"] * len(self.lights))

    def test_every_pilot_has_the_turn_off_branches(self):
        for slug in _pilot_slugs(self.gen):
            with self.subTest(slug):
                doc = yaml.safe_load(self.gen.emit_actuator(slug, self.gen.LIGHT_TARGETS[slug]))
                branches = _branches(doc)
                lights = [t[1] for t in self.gen.LIGHT_TARGETS[slug] if t[0] == "light"]
                for name in ("present", "default"):
                    self.assertEqual(_issued(branches[name], -1), [])
                    self.assertEqual([c["action"] for c in _issued(branches[name], 0)],
                                     ["light.turn_off"] * len(lights))


class TvPredicateTests(unittest.TestCase):
    """The learning and manual-detection packages read the TV through the
    tv_playing sensor; no package this generator set owns names lg_tv."""

    def test_no_owned_package_names_the_tv_directly(self):
        gen = _actuators()
        owned = [PACKAGES / f"living_lights_pilot_{s}.yaml" for s in _pilot_slugs(gen)]
        owned += [LEARNING, MANUAL, GRADIENT]
        for path in owned:
            with self.subTest(path.name):
                self.assertNotIn("media_player.lg_tv", path.read_text(encoding="utf-8"))
        for source in ("build-living-lights-actuators.py", "build-living-lights-learning.py",
                       "build-gradient-lighting.py"):
            with self.subTest(source):
                self.assertNotIn("media_player.lg_tv", (TOOLS / source).read_text(encoding="utf-8"))

    def test_tv_playing_variables_read_the_sensor(self):
        for path in (LEARNING, MANUAL):
            with self.subTest(path.name):
                variables = []
                for match in re.finditer(r"^\s+tv_playing: \"(.*)\"$",
                                         path.read_text(encoding="utf-8"), flags=re.M):
                    variables.append(match.group(1))
                reads = [v for v in variables if v != "{{ tv_playing }}"]
                self.assertTrue(reads)
                self.assertEqual(set(reads), {TV_SENSOR_TEMPLATE})


class GradientGateTests(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(GRADIENT.read_text(encoding="utf-8"))
        self.sofa = {a["id"]: a for a in self.doc["automation"]}["living_lights_gradient_sofa"]

    def test_actuator_requires_a_positive_base_level(self):
        gates = [c["value_template"] for c in self.sofa["conditions"]
                 if c.get("condition") == "template" and "predicted_brightness_pct" in c["value_template"]]
        self.assertEqual(gates, [
            "{{ state_attr('sensor.living_room_sofa_lighting_state', 'predicted_brightness_pct') | int(0) > 0 }}"])

    def test_floor_applies_only_while_the_base_is_positive(self):
        variables = self.sofa["actions"][0]["variables"]
        self.assertEqual(
            variables["grad_base"],
            "{{ state_attr('sensor.living_room_sofa_lighting_state', 'predicted_brightness_pct') | int(0) }}")
        calls = [a for a in self.sofa["actions"] if a.get("action") == "light.turn_on"]
        self.assertEqual(len(calls), 4)
        for call in calls:
            pct = call["data"]["brightness_pct"]
            self.assertIn("| int(0), 5 ] | max", pct)
            self.assertTrue(pct.endswith("if grad_base > 0 else 0 }}"))
            self.assertNotIn("int(5)", pct)


class GeneratorDriftTests(unittest.TestCase):
    """Regenerating produces exactly the checked-in files."""

    def test_pilots_and_manual_detection_match_generator(self):
        gen = _actuators()
        slugs = _pilot_slugs(gen)
        self.assertEqual(len(slugs), 10)
        for slug in slugs:
            with self.subTest(slug):
                path = PACKAGES / f"living_lights_pilot_{slug}.yaml"
                self.assertEqual(gen.emit_actuator(slug, gen.LIGHT_TARGETS[slug]),
                                 path.read_text(encoding="utf-8"),
                                 f"regenerate {path.name}: build-living-lights-actuators.py --apply")
        self.assertEqual(gen.emit_manual_detection(), MANUAL.read_text(encoding="utf-8"),
                         "regenerate living_lights_manual_detection.yaml")

    def test_learning_package_matches_generator(self):
        # The learning generator writes next to its own file, so run a copy
        # of tools/ in a scratch tree and compare its output.
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            (scratch / "tools").mkdir()
            (scratch / "ha-config" / "packages").mkdir(parents=True)
            for name in ("build-living-lights-learning.py", "build-living-lights-actuators.py"):
                shutil.copy(TOOLS / name, scratch / "tools" / name)
            subprocess.run([sys.executable, str(scratch / "tools" / "build-living-lights-learning.py"),
                            "--apply"], check=True, capture_output=True)
            produced = (scratch / "ha-config" / "packages" / "living_lights_learning.yaml")
            self.assertEqual(produced.read_text(encoding="utf-8"), LEARNING.read_text(encoding="utf-8"),
                             "regenerate living_lights_learning.yaml: build-living-lights-learning.py --apply")

    def test_gradient_package_matches_generator(self):
        gen = _load("build_gradient_lighting_t", TOOLS / "build-gradient-lighting.py")
        cents, dw, dh = gen.load_centroids()
        self.assertEqual(gen.build_yaml(cents, dw, dh), GRADIENT.read_text(encoding="utf-8"),
                         "regenerate living_lights_gradient.yaml")


class TemplateSyntaxTests(unittest.TestCase):
    def test_every_template_in_a_rendered_pilot_parses(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        gen = _actuators()
        for slug in _pilot_slugs(gen):
            text = gen.emit_actuator(slug, gen.LIGHT_TARGETS[slug])
            for template in re.findall(r'"(\{\{.*?\}\})"', text):
                with self.subTest(slug=slug, template=template[:60]):
                    env.parse(template)


if __name__ == "__main__":
    unittest.main()
