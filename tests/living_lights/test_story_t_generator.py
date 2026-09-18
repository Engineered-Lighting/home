"""Story T (one TV predicate, route light, dormant belief hooks) and story S
hooks (estimator gate, trigger ids, hard backstop, mirror, writer record) in
the generated observability package and the generated MQTT mirror package.

Run: python3 -m unittest tests/living_lights/test_story_t_generator.py

Deterministic and offline: it loads the generator in-process, parses the
checked-in packages and re-runs the generator into a temporary directory to
prove the checked-in files are what it emits. It never contacts Home
Assistant and nothing here talks to the network.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PACKAGES = REPO / "ha-config" / "packages"
OBS = PACKAGES / "living_lights_observability.yaml"
MIRROR = PACKAGES / "living_lights_mqtt_mirror.yaml"
MANUAL_DETECTION = PACKAGES / "living_lights_manual_detection.yaml"
GENERATOR = REPO / "tools" / "build-living-lights-yaml.py"
sys.path.insert(0, str(REPO / "tools"))   # living_lights_tv_states, the shared vocabulary

TV_PLAYING = "binary_sensor.living_lights_tv_playing"
TV_WATCHING = "binary_sensor.living_lights_tv_watching"
PUBLISHER_FRESH = "binary_sensor.living_lights_publisher_fresh"
HEARTBEAT = "sensor.lighting_publisher_heartbeat"
BELIEF_TOGGLE = "input_boolean.living_lights_actuate_from_belief_changes"
SOFA_STABLE = "binary_sensor.living_room_sofa_person_occupancy_stable"
ESTIMATOR = "sensor.living_lights_asleep_estimator"
WRITER = "input_text.living_lights_asleep_writer"
LG_TV = "media_player.lg_tv"
TV_PREDICATE = f"is_state('{TV_PLAYING}', 'on')"


def _load_generator():
    spec = importlib.util.spec_from_file_location("build_living_lights_yaml_story_t", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _templates(node) -> list[str]:
    """Every Jinja-bearing string in a parsed package."""
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


def _automations(doc: dict) -> dict:
    return {a["id"]: a for a in doc["automation"]}


def _binary_sensor(doc: dict, unique_id: str) -> tuple[dict, dict]:
    """(trigger block, sensor) for a trigger-based template binary_sensor."""
    for block in doc["template"]:
        for sensor in block.get("binary_sensor", []):
            if sensor.get("unique_id") == unique_id:
                return block, sensor
    raise KeyError(unique_id)


def _classifier_block(doc: dict) -> tuple[dict, list]:
    for block in doc["template"]:
        sensors = [s for s in block.get("sensor", []) if s.get("unique_id", "").endswith("_lighting_state")]
        if sensors:
            return block, sensors
    raise KeyError("classifier block")


def _state_trigger_entities(block: dict) -> list[str]:
    for trig in block["trigger"]:
        if trig.get("platform") == "state":
            return list(trig["entity_id"])
    return []


def _time_pattern(block: dict) -> dict | None:
    for trig in block["trigger"]:
        if trig.get("platform") == "time_pattern":
            return trig
    return None


def _writes_latch(auto: dict) -> bool:
    """True when an action turns input_boolean.living_lights_asleep on or off."""
    found = False

    def walk(node):
        nonlocal found
        if isinstance(node, dict):
            if node.get("action") in ("input_boolean.turn_on", "input_boolean.turn_off", "input_boolean.toggle"):
                target = node.get("target", {}).get("entity_id")
                targets = target if isinstance(target, list) else [target]
                if "input_boolean.living_lights_asleep" in targets:
                    found = True
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(auto.get("actions", []))
    return found


def _writer_values(auto: dict) -> list[str]:
    values = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("action") == "input_text.set_value" and node.get("target", {}).get("entity_id") == WRITER:
                values.append(node["data"]["value"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(auto.get("actions", []))
    return values


class GeneratorDriftTests(unittest.TestCase):
    def test_both_packages_match_the_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "obs.yaml"
            mirror = Path(tmp) / "mirror.yaml"
            subprocess.run([sys.executable, str(GENERATOR), "--output", str(obs),
                            "--mirror-output", str(mirror)], check=True, capture_output=True)
            self.assertEqual(obs.read_text(encoding="utf-8"), OBS.read_text(encoding="utf-8"),
                             "regenerate ha-config/packages/living_lights_observability.yaml")
            self.assertEqual(mirror.read_text(encoding="utf-8"), MIRROR.read_text(encoding="utf-8"),
                             "regenerate ha-config/packages/living_lights_mqtt_mirror.yaml")

    def test_mirror_is_only_written_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "obs.yaml"
            subprocess.run([sys.executable, str(GENERATOR), "--output", str(obs)],
                           check=True, capture_output=True)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["obs.yaml"])

    def test_new_generator_text_is_ascii(self):
        mirror_text = MIRROR.read_text(encoding="utf-8")
        self.assertTrue(mirror_text.isascii())
        # The new sensors and automations in the observability package.
        for marker in ("Living Lights TV playing", "Living Lights publisher fresh",
                       "asleep hard backstop", "asleep mirror (estimator)", "asleep writer",
                       "TypeSafe egress enabled", "asleep from estimator"):
            line = next(l for l in OBS.read_text(encoding="utf-8").splitlines() if marker in l)
            self.assertTrue(line.isascii(), line)


class OnePredicateSourceTests(unittest.TestCase):
    """Only the tv_playing sensor's own templates (and the mirror, which
    forwards the raw state) may read media_player.lg_tv in a template."""

    def test_only_tv_playing_and_the_mirror_read_lg_tv(self):
        obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))
        _block, playing = _binary_sensor(obs, "living_lights_tv_playing")
        allowed = set(_templates(playing))
        self.assertTrue(all(LG_TV in t for t in allowed))
        for path in sorted(PACKAGES.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            offenders = [t for t in _templates(doc) if LG_TV in t and t not in allowed]
            if path == MIRROR:
                # the payload names lg_tv only as a key of the attribute subset
                offenders = [t for t in offenders if f"states('{LG_TV}')" in t or f"states.{LG_TV}" in t
                             or f"is_state('{LG_TV}'" in t]
            with self.subTest(package=path.name):
                self.assertEqual(offenders, [], f"{path.name} reads {LG_TV} outside the tv_playing sensor")

    def test_classifier_templates_read_the_sensor(self):
        gen = _load_generator()
        obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))
        _block, sensors = _classifier_block(obs)
        self.assertEqual(len(sensors), len(gen.ZONES))
        for sensor in sensors:
            with self.subTest(sensor=sensor["unique_id"]):
                for key in ("state",):
                    self.assertIn(TV_PREDICATE, sensor[key])
                    self.assertNotIn(LG_TV, sensor[key])
                for key in ("predicted_brightness_pct", "predicted_color_temp_kelvin"):
                    self.assertIn(TV_PREDICATE, sensor["attributes"][key])
                    self.assertNotIn(LG_TV, sensor["attributes"][key])
        bias_event = _automations(obs)["living_lights_bias_changed_event"]
        tv_playing = bias_event["actions"][0]["event_data"]["tv_playing"]
        self.assertEqual(tv_playing, "{{ " + TV_PREDICATE + " }}")
        self.assertFalse(any(LG_TV in t for t in _templates(bias_event)))


class TvPlayingSensorTests(unittest.TestCase):
    def setUp(self):
        self.gen = _load_generator()
        self.obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))

    def test_tv_playing_triggers_and_hold(self):
        block, sensor = _binary_sensor(self.obs, "living_lights_tv_playing")
        self.assertEqual(_state_trigger_entities(block),
                         [LG_TV, TV_WATCHING, PUBLISHER_FRESH, BELIEF_TOGGLE, SOFA_STABLE])
        self.assertTrue(any(t.get("platform") == "homeassistant" and t.get("event") == "start"
                            for t in block["trigger"]))
        self.assertEqual(_time_pattern(block)["minutes"], "/1")
        self.assertNotIn(HEARTBEAT, yaml.safe_dump(block["trigger"]))
        self.assertIn(f"{self.gen.TV_UNAVAILABLE_HOLD_S} if states('{LG_TV}') in ['unavailable', 'unknown'] else 0",
                      sensor["delay_off"])
        self.assertEqual(self.gen.TV_UNAVAILABLE_HOLD_S, 90)

    def test_tv_playing_state_is_the_plan_rule_over_the_shared_vocabulary(self):
        from living_lights_tv_states import TV_OFF_JINJA  # noqa: E402 (tools on sys.path below)
        _block, sensor = _binary_sensor(self.obs, "living_lights_tv_playing")
        state = sensor["state"]
        self.assertIn(f"lg_tv not in {TV_OFF_JINJA} or bridged", state)
        self.assertIn("bridged = lg_tv in ['unavailable', 'unknown'] and belief_live and tv_watching == 'on'", state)
        self.assertIn("tv_playing = tv_seen_on and (not belief or not fresh or tv_watching != 'off' or sofa_stable_on)", state)
        self.assertIn(f"is_state('{PUBLISHER_FRESH}', 'on')", state)
        self.assertIn(f"is_state('{SOFA_STABLE}', 'on')", state)
        self.assertEqual(sensor["attributes"]["lg_tv"], f"{{{{ states('{LG_TV}') }}}}")
        self.assertIn("reason", sensor["attributes"])

    def test_publisher_fresh_sensor(self):
        block, sensor = _binary_sensor(self.obs, "living_lights_publisher_fresh")
        self.assertEqual(_state_trigger_entities(block), [HEARTBEAT])
        self.assertEqual(_time_pattern(block)["minutes"], "/1")
        self.assertIn(f"as_datetime(states('{HEARTBEAT}'), none)", sensor["state"])
        self.assertIn(f"<= {self.gen.PUBLISHER_FRESH_S}", sensor["state"])
        self.assertEqual(self.gen.PUBLISHER_FRESH_S, 180)
        self.assertIn("hb is none or hb.tzinfo is none", sensor["state"])


class ClassifierTriggerTests(unittest.TestCase):
    def setUp(self):
        self.gen = _load_generator()
        self.obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))
        self.block, self.sensors = _classifier_block(self.obs)
        self.triggers = _state_trigger_entities(self.block)

    def test_heartbeat_is_never_a_classifier_trigger(self):
        self.assertNotIn(HEARTBEAT, self.triggers)
        self.assertNotIn(HEARTBEAT, yaml.safe_dump(self.block["trigger"]))

    def test_belief_entities_and_new_helpers_are_triggers(self):
        for entity in (TV_PLAYING, TV_WATCHING, PUBLISHER_FRESH, BELIEF_TOGGLE,
                       "input_number.living_lights_tv_route_pct",
                       "input_number.living_lights_tv_vacant_floor_pct"):
            self.assertIn(entity, self.triggers)
        activity = [f"sensor.{meta['camera']}_{slug}_activity" for slug, meta in self.gen.ZONES.items()]
        self.assertEqual(len(activity), 15)
        for entity in activity:
            self.assertIn(entity, self.triggers)
        # the media player itself is read only by tv_playing
        self.assertNotIn(LG_TV, self.triggers)
        self.assertEqual(len(self.triggers), len(set(self.triggers)))


class RouteAndFloorTests(unittest.TestCase):
    def setUp(self):
        self.gen = _load_generator()
        self.obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))
        _block, sensors = _classifier_block(self.obs)
        self.by_slug = {}
        for sensor in sensors:
            slug = next(s for s in self.gen.ZONES if sensor["unique_id"] == f"{self.gen.ZONES[s]['camera']}_{s}_lighting_state")
            self.by_slug[slug] = sensor

    def test_constants(self):
        self.assertEqual(self.gen.MOVIE_DIM_PCT, 0)
        self.assertEqual(self.gen.TV_VACANT_FLOOR_PCT, 0)
        self.assertEqual(self.gen.TV_ROUTE_PCT, 30)
        self.assertEqual(self.gen.MOVIE_WATCH_ZONES,
                         {s for s, m in self.gen.ZONES.items() if m["camera"] == "living_room"})

    def test_helpers_with_initials(self):
        numbers = self.obs["input_number"]
        self.assertEqual(numbers["living_lights_movie_dim_pct"]["initial"], 0)
        route = numbers["living_lights_tv_route_pct"]
        self.assertEqual((route["min"], route["max"], route["step"], route["initial"]), (0, 100, 1, 30))
        floor = numbers["living_lights_tv_vacant_floor_pct"]
        self.assertEqual((floor["min"], floor["max"], floor["step"], floor["initial"]), (0, 100, 1, 0))
        booleans = self.obs["input_boolean"]
        self.assertIs(booleans["living_lights_asleep_from_estimator"]["initial"], False)
        self.assertIs(booleans["living_lights_typesafe_egress_enabled"]["initial"], False)
        writer = self.obs["input_text"]["living_lights_asleep_writer"]
        self.assertEqual(writer["max"], 32)
        self.assertEqual(writer["initial"], "")

    def test_non_watch_zones_route_and_vacant_floor(self):
        route = "{{ [tv_route_pct, cap, profile_max] | min }}"
        for slug, sensor in self.by_slug.items():
            if slug in self.gen.MOVIE_WATCH_ZONES:
                continue
            bri = sensor["attributes"]["predicted_brightness_pct"]
            with self.subTest(zone=slug):
                self.assertIn("tv_vacant_floor_pct if tv_playing else", bri)
                self.assertNotIn("movie_dim_pct if tv_playing", bri)
                self.assertIn("{% elif tv_playing and dwell < " + str(self.gen.TV_ROUTE_MAX_DWELL_MS) + " %}" + route, bri)
                self.assertIn("{% elif dwell < 2000 and speed >= 1.0 %}{{ ([tv_route_pct, cap, profile_max] | min) if tv_playing else", bri)
                self.assertNotIn("{% elif tv_playing %}{{ floor }}", bri)
                self.assertIn("state_attr('sensor.living_lights_profile', 'max_brightness_pct') | int(100)", bri)
                # the route comes after the activity branches, before the default
                self.assertLess(bri.index("activity == 'napping'"), bri.index(route))
                self.assertLess(bri.index(route), bri.index("{% else %}{{ [floor, [ramp_target_pct, cap] | min] | max }}"))

    def test_watch_zones_are_unchanged(self):
        for slug in self.gen.MOVIE_WATCH_ZONES:
            bri = self.by_slug[slug]["attributes"]["predicted_brightness_pct"]
            with self.subTest(zone=slug):
                self.assertIn("movie_dim_pct if tv_playing else", bri)
                self.assertIn("{% elif tv_playing %}{{ floor }}", bri)
                self.assertIn("{% elif tv_playing %}{{ movie_dim_pct }}", bri)
                self.assertNotIn("tv_route_pct, cap, profile_max", bri)
                self.assertNotIn("tv_vacant_floor_pct if tv_playing", bri)

    def test_front_left_generator_rule_is_absent(self):
        for auto in self.obs["automation"]:
            self.assertNotIn("front_left", auto["id"])
            self.assertNotIn("front_left", auto["alias"])
        self.assertNotIn("FRONT_LEFT", GENERATOR.read_text(encoding="utf-8"))


class StorySHooksTests(unittest.TestCase):
    def setUp(self):
        self.gen = _load_generator()
        self.obs = yaml.safe_load(OBS.read_text(encoding="utf-8"))
        self.autos = _automations(self.obs)
        self.on = self.autos["living_lights_asleep_on"]
        self.off = self.autos["living_lights_asleep_off"]

    def _gates(self, auto: dict) -> list[str]:
        return [c["value_template"] for c in auto["conditions"]
                if c.get("condition") == "template" and "asleep_from_estimator" in c["value_template"]]

    def test_both_legacy_automations_carry_the_identical_gate(self):
        on_gates, off_gates = self._gates(self.on), self._gates(self.off)
        self.assertEqual(len(on_gates), 1)
        self.assertEqual(len(off_gates), 1)
        self.assertEqual(on_gates[0], off_gates[0])
        self.assertEqual(on_gates[0], self.gen.LEGACY_ASLEEP_GATE)
        gate = on_gates[0]
        self.assertTrue(gate.startswith("{{ not ("))
        for term in (f"is_state('{BELIEF_TOGGLE}', 'on')",
                     "is_state('input_boolean.living_lights_asleep_from_estimator', 'on')",
                     f"states('{ESTIMATOR}') in ['likely_asleep', 'awake', 'away']",
                     f"is_state('{PUBLISHER_FRESH}', 'on')"):
            self.assertIn(term, gate)

    def test_asleep_off_trigger_ids_and_credibility(self):
        self.assertEqual([t["id"] for t in self.off["triggers"]], ["occupancy", "midday", "presence"])
        self.assertEqual([t["entity_id"] for t in self.off["triggers"]],
                         ["binary_sensor.living_lights_any_occupied", "sensor.living_lights_profile",
                          "input_boolean.user_at_home"])
        self.assertEqual(self.off["triggers"][0]["for"], {"minutes": self.gen.ASLEEP_WAKE_MINUTES})
        either = next(c for c in self.off["conditions"] if c.get("condition") == "or")
        by_id = {}
        for branch in either["conditions"]:
            if branch.get("condition") == "trigger":
                by_id[branch["id"]] = []
            else:
                inner = branch["conditions"]
                by_id[inner[0]["id"]] = inner[1:]
        self.assertEqual(set(by_id), {"occupancy", "midday", "presence"})
        self.assertEqual(by_id["occupancy"], [])
        self.assertEqual(by_id["midday"], [{"condition": "state",
                                            "entity_id": "binary_sensor.living_lights_any_occupied",
                                            "state": "on", "for": {"minutes": 2}}])
        presence = by_id["presence"][0]["value_template"]
        self.assertIn("binary_sensor.front_door_person_occupancy", presence)
        self.assertIn("binary_sensor.living_room_person_occupancy", presence)
        self.assertIn("obj.state == 'on' and (now() - obj.last_changed).total_seconds() <= 60", presence)

    def test_hard_backstop_is_ungated(self):
        backstop = self.autos["living_lights_asleep_hard_backstop"]
        self.assertEqual(self._gates(backstop), [])
        self.assertNotIn("asleep_from_estimator", yaml.safe_dump(backstop))
        self.assertNotIn("publisher_fresh", yaml.safe_dump(backstop))
        state_trigger = backstop["triggers"][0]
        self.assertEqual((state_trigger["id"], state_trigger["entity_id"], state_trigger["to"], state_trigger["for"]),
                         ("occupied", "binary_sensor.living_lights_any_occupied", "on", {"minutes": 30}))
        self.assertIn({"trigger": "time", "id": "six", "at": "06:00:00"}, backstop["triggers"])
        self.assertIn({"condition": "time", "after": "06:00:00"}, backstop["conditions"])
        self.assertIn({"condition": "state", "entity_id": "input_boolean.living_lights_asleep", "state": "on"},
                      backstop["conditions"])
        # the 30-min hold is re-checked only on the 06:00 path: the state
        # trigger already guarantees it, and a `for` condition evaluated at
        # that trigger's own boundary instant refuses (proven in the ha-sim venv).
        either = next(c for c in backstop["conditions"] if c.get("condition") == "or")
        self.assertEqual(either["conditions"][0], {"condition": "trigger", "id": "occupied"})
        self.assertEqual(either["conditions"][1]["conditions"],
                         [{"condition": "trigger", "id": "six"},
                          {"condition": "state", "entity_id": "binary_sensor.living_lights_any_occupied",
                           "state": "on", "for": {"minutes": 30}}])
        self.assertEqual(backstop["actions"][0], {"action": "input_boolean.turn_off",
                                                  "target": {"entity_id": "input_boolean.living_lights_asleep"}})

    def test_mirror_automation(self):
        mirror = self.autos["living_lights_asleep_mirror"]
        self.assertEqual([t["entity_id"] for t in mirror["triggers"]], [ESTIMATOR, ESTIMATOR])
        self.assertEqual(mirror["triggers"][0]["to"], "likely_asleep")
        self.assertEqual(mirror["triggers"][1]["to"], ["awake", "away"])
        self.assertEqual([c["value_template"] for c in mirror["conditions"]], [self.gen.ESTIMATE_LIVE_GATE])
        self.assertEqual(self.gen.ESTIMATE_LIVE_GATE, "{{ " + self.gen.ESTIMATE_LIVE_EXPR + " }}")
        self.assertEqual(self.gen.LEGACY_ASLEEP_GATE, "{{ not (" + self.gen.ESTIMATE_LIVE_EXPR + ") }}")
        branch = mirror["actions"][0]["if"]
        self.assertEqual(branch, [{"condition": "trigger", "id": "latch"}])
        self.assertEqual(mirror["actions"][0]["then"][0]["action"], "input_boolean.turn_on")
        self.assertEqual(mirror["actions"][0]["else"][0]["action"], "input_boolean.turn_off")

    def test_every_writer_records_its_name(self):
        expect = {
            "living_lights_asleep_on": ["legacy_on"],
            "living_lights_asleep_off": ["legacy_off:{{ trigger.id }}"],
            "living_lights_asleep_hard_backstop": ["hard_backstop"],
            "living_lights_asleep_mirror": ["mirror:{{ trigger.to_state.state }}"],
        }
        for auto_id, values in expect.items():
            with self.subTest(auto_id):
                self.assertEqual(_writer_values(self.autos[auto_id]), values)
        # rendered names fit the 32-char helper
        for name in ("legacy_on", "legacy_off:occupancy", "legacy_off:midday", "legacy_off:presence",
                     "hard_backstop", "mirror:likely_asleep", "mirror:awake", "mirror:away"):
            self.assertLessEqual(len(name), 32)
        # nothing else in the package writes the latch
        self.assertEqual({a["id"] for a in self.obs["automation"] if _writes_latch(a)}, set(expect))


class MirrorPackageTests(unittest.TestCase):
    def setUp(self):
        self.gen = _load_generator()
        self.doc = yaml.safe_load(MIRROR.read_text(encoding="utf-8"))
        self.autos = _automations(self.doc)

    def test_parses_and_lists_the_topics(self):
        self.assertEqual(list(self.doc), ["automation"])
        change = self.autos["living_lights_mqtt_mirror_change"]
        republish = self.autos["living_lights_mqtt_mirror_republish"]
        self.assertEqual(change["triggers"][0]["entity_id"], self.gen.MIRROR_ENTITIES)
        self.assertIn("to", change["triggers"][0])
        self.assertIsNone(change["triggers"][0]["to"])
        for entity in (LG_TV, "input_boolean.user_at_home", "input_boolean.living_lights_asleep",
                       "sensor.living_lights_profile", BELIEF_TOGGLE,
                       "input_boolean.living_lights_typesafe_egress_enabled",
                       "input_boolean.living_lights_asleep_from_estimator"):
            self.assertIn(entity, self.gen.MIRROR_ENTITIES)
        publish = change["actions"][0]
        self.assertEqual(publish["action"], "mqtt.publish")
        self.assertEqual(publish["data"]["topic"], "living_lights/mirror/{{ trigger.entity_id | replace('.', '/') }}")
        self.assertIs(publish["data"]["retain"], True)
        for key in ("'entity_id': obj.entity_id", "'state': obj.state",
                    "'changed_at': obj.last_changed.isoformat()", "'attributes': ns.attrs", "| to_json"):
            self.assertIn(key, publish["data"]["payload"])
        self.assertEqual([t.get("trigger") for t in republish["triggers"]], ["homeassistant", "time_pattern"])
        self.assertEqual(republish["triggers"][1]["minutes"], "/1")
        loop = republish["actions"][0]["repeat"]
        for entity in self.gen.MIRROR_ENTITIES:
            self.assertIn(f"'{entity}',", loop["for_each"])
        inner = loop["sequence"][0]
        self.assertEqual(inner["data"]["topic"], "living_lights/mirror/{{ repeat.item | replace('.', '/') }}")
        self.assertIs(inner["data"]["retain"], True)
        heartbeat = republish["actions"][1]
        self.assertEqual(heartbeat["data"]["topic"], "living_lights/mirror/heartbeat")
        self.assertEqual(heartbeat["data"]["payload"], "{{ now().isoformat() }}")
        self.assertIs(heartbeat["data"]["retain"], True)
        topics = {a["entity_id"].replace(".", "/") for a in [{"entity_id": e} for e in self.gen.MIRROR_ENTITIES]}
        self.assertIn("media_player/lg_tv", topics)

    def test_local_only(self):
        text = yaml.safe_dump(self.doc)
        actions = set()

        def walk(node):
            if isinstance(node, dict):
                if "action" in node:
                    actions.add(node["action"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(self.doc)
        self.assertEqual(actions, {"mqtt.publish"})
        self.assertNotIn("http", text)
        self.assertNotIn("rest_command", text)

    def test_last_command_helpers_match_the_actuators_package(self):
        if not MANUAL_DETECTION.exists():
            self.skipTest("manual detection package absent")
        manual = yaml.safe_load(MANUAL_DETECTION.read_text(encoding="utf-8"))
        helpers = sorted(k for k in manual.get("input_text", {}) if k.endswith("_last_command_id"))
        expected = sorted(f"living_lights_zone_{slug}_last_command_id" for slug in self.gen.LAST_COMMAND_ZONES)
        self.assertEqual(helpers, expected)
        for helper in helpers:
            self.assertIn(f"input_text.{helper}", self.gen.MIRROR_ENTITIES)

    def test_every_template_parses(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        for template in _templates(self.doc):
            with self.subTest(template=template[:60]):
                env.parse(template)


if __name__ == "__main__":
    unittest.main()
