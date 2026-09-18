"""Override payload contract (the 255 fix) and the TV / wake-up gates on the
hand-written Living Lights packages.

Run: python3 -m unittest tests/living_lights/test_override_payload.py

Deterministic and offline: it parses the checked-in packages, imports the
pure payload builders from the extended_openai_conversation function file
without Home Assistant, and renders the good-morning payload template with
a far-future clock. It never contacts Home Assistant.

What it pins:
  - input_text.living_lights_override_text_<zone> has Home Assistant's
    255-character ceiling; both writers (homeai_good_morning.yaml and
    functions/living_lights.py) now send the six-key helper payload
    {command_id, brightness_pct, hold_until, vacancy_grace_s, pinned,
    source}, and the function file refuses an oversize payload before the
    write; the long payload (colour, prompt, baseline, ...) keeps every old
    key in the ledger row.
  - living_lights_ambient.yaml and the return-home script read the shared
    binary_sensor.living_lights_tv_playing instead of media_player.lg_tv.
  - living_lights_woke_up_on_wake requires any_occupied on for 2 min.
"""
from __future__ import annotations

import importlib.util
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PACKAGES = REPO / "ha-config" / "packages"
GOOD_MORNING = PACKAGES / "homeai_good_morning.yaml"
AMBIENT = PACKAGES / "living_lights_ambient.yaml"
LIFECYCLE = PACKAGES / "living_lights_override_lifecycle.yaml"
PROACTIVE = REPO / "ha-config" / "homeai_proactive.yaml"
FUNCTION_FILE = (REPO / "ha-config" / "extended_openai_conversation"
                 / "functions" / "living_lights.py")

HELPER_KEYS = ("command_id", "brightness_pct", "hold_until",
               "vacancy_grace_s", "pinned", "source")
LONG_KEYS = {
    "command_id", "brightness_pct", "color_temp_kelvin", "started_at",
    "hold_until", "min_hold_min", "vacancy_grace_s", "source", "prompt",
    "remote", "pinned", "baseline",
}
TV_PLAYING = "binary_sensor.living_lights_tv_playing"
ANY_OCCUPIED = "binary_sensor.living_lights_any_occupied"
ASLEEP = "input_boolean.living_lights_asleep"
TRAVEL = "input_boolean.living_lights_travel_mode"
FAR_FUTURE = datetime(2099, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _load_function_module():
    """Import functions/living_lights.py as a plain module: its Home
    Assistant imports are guarded, so no mocks are needed."""
    spec = importlib.util.spec_from_file_location("living_lights_function", FUNCTION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _automations(doc: dict) -> dict:
    return {a["id"]: a for a in doc["automation"]}


def _templates(node) -> list[str]:
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


def _good_morning_value_template() -> str:
    auto = _automations(yaml.safe_load(GOOD_MORNING.read_text(encoding="utf-8")))["homeai_good_morning"]
    energize = auto["actions"][0]["if"]
    repeat = auto["actions"][0]["then"][0]["repeat"]
    writes = [s for s in repeat["sequence"] if s.get("action") == "input_text.set_value"]
    assert len(writes) == 1, writes
    assert any(c.get("entity_id") == "input_boolean.living_lights_morning_energize_enabled"
               for c in energize)
    return writes[0]["data"]["value"]


class GoodMorningPayloadTests(unittest.TestCase):
    def test_payload_template_has_exactly_the_six_keys(self):
        template = _good_morning_value_template()
        keys = re.findall(r"'([a-z_]+)':", template)
        self.assertEqual(keys, list(HELPER_KEYS))
        for gone in ("color_temp_kelvin", "started_at", "min_hold_min", "prompt", "remote"):
            self.assertNotIn(gone, template)
        self.assertIn("'morning-' ~", template)
        self.assertIn("| to_json", template)

    def test_rendered_payload_is_under_255_with_a_2099_timestamp(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        env.filters["to_json"] = json.dumps
        env.globals["now"] = lambda: FAR_FUTURE
        env.globals["as_datetime"] = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc)
        rendered = env.from_string(_good_morning_value_template()).render().strip()
        self.assertLess(len(rendered), 255, rendered)
        payload = json.loads(rendered)
        self.assertEqual(list(payload), list(HELPER_KEYS))
        self.assertEqual(payload["command_id"], f"morning-{int(FAR_FUTURE.timestamp())}")
        self.assertEqual(payload["brightness_pct"], 90)
        self.assertEqual(payload["vacancy_grace_s"], 300)
        self.assertIs(payload["pinned"], False)
        self.assertEqual(payload["source"], "auto")
        hold = datetime.fromisoformat(payload["hold_until"])
        self.assertEqual(hold - FAR_FUTURE, timedelta(minutes=40))

    def test_colour_temperature_moved_to_the_logbook_line(self):
        auto = _automations(yaml.safe_load(GOOD_MORNING.read_text(encoding="utf-8")))["homeai_good_morning"]
        logs = [a for a in auto["actions"] if a.get("action") == "logbook.log"]
        self.assertEqual(len(logs), 1)
        message = logs[0]["data"]["message"]
        self.assertIn("3000 K", message)
        self.assertIn("90%", message)

    def test_lifecycle_reader_only_needs_helper_keys(self):
        """The lifecycle automation's from_json reader must find every key it
        asks for inside the six-key helper payload."""
        autos = _automations(yaml.safe_load(LIFECYCLE.read_text(encoding="utf-8")))
        to_clear = autos["living_lights_override_lifecycle"]["actions"][0]["variables"]["to_clear"]
        self.assertIn("txt[0] == '{'", to_clear)
        read = set(re.findall(r"obj\.get\('([a-z_]+)'", to_clear))
        self.assertEqual(read, {"pinned", "hold_until", "vacancy_grace_s"})
        self.assertTrue(read <= set(HELPER_KEYS))


class FunctionPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ll = _load_function_module()

    def _long(self, **overrides):
        args = {"brightness_pct": 100, "color_temp_kelvin": 6500,
                "source_text": "y" * 160, "pinned": True}
        args.update(overrides)
        return self.ll.build_ledger_payload(
            baseline={"brightness_pct": 50, "color_temp_kelvin": 2700},
            args=args, source="hardware", now=FAR_FUTURE,
            command_id=self.ll._new_command_id(FAR_FUTURE), remote=True,
        )

    def test_importable_without_home_assistant(self):
        self.assertEqual(self.ll.MAX_INPUT_TEXT, 255)
        self.assertEqual(self.ll.HELPER_PAYLOAD_KEYS, HELPER_KEYS)
        self.assertTrue(issubclass(self.ll.OverridePayloadTooLong, ValueError))

    def test_short_payload_is_under_255_with_2099_and_a_160_char_prompt(self):
        long = self._long()
        self.assertEqual(len(long["prompt"]), 160)
        short = self.ll.helper_payload(long)
        self.assertEqual(list(short), list(HELPER_KEYS))
        text = self.ll.encode_helper_payload(short)
        self.assertLess(len(text), 255, text)
        self.assertEqual(json.loads(text), short)
        self.assertNotIn(" ", text)
        self.assertNotIn("baseline", text)
        self.assertNotIn("yyyy", text)
        self.assertTrue(short["hold_until"].startswith("2100-01-01T00:39:59"))

    def test_long_payload_keeps_every_old_key(self):
        long = self._long()
        self.assertEqual(set(long), LONG_KEYS)
        self.assertEqual(long["color_temp_kelvin"], 6500)
        self.assertEqual(long["min_hold_min"], self.ll.MIN_HOLD_MINUTES)
        self.assertEqual(long["baseline"], {"brightness_pct": 50, "color_temp_kelvin": 2700})
        self.assertIs(long["remote"], True)
        self.assertIs(long["pinned"], True)
        # the input_text ceiling never applies to the ledger row
        self.assertGreater(len(json.dumps(long)), 255)
        row = self.ll.ledger_event("sink", "input_text.living_lights_override_text_sink",
                                   long, self.ll.helper_payload(long))
        self.assertEqual(row["payload"], long)
        self.assertEqual(row["helper_payload"], self.ll.helper_payload(long))
        self.assertEqual(row["requested"]["color_temp_kelvin"], 6500)
        self.assertEqual(row["baseline"], long["baseline"])
        self.assertEqual(row["kind"], "lighting_command_event")

    def test_delta_inputs_resolve_against_the_baseline(self):
        long = self._long(brightness_pct=None, color_temp_kelvin=None,
                          brightness_delta_pct=25, color_temp_delta_kelvin=-700)
        self.assertEqual(long["brightness_pct"], 75)
        self.assertEqual(long["color_temp_kelvin"], 2000)

    def test_guard_raises_at_256_and_passes_at_255(self):
        short = self.ll.helper_payload(self._long())
        fill = 255 - len(json.dumps(dict(short, command_id=""), separators=(",", ":")))
        exact = dict(short, command_id="x" * fill)
        self.assertEqual(len(self.ll.encode_helper_payload(exact)), 255)
        over = dict(short, command_id="x" * (fill + 1))
        with self.assertRaises(self.ll.OverridePayloadTooLong) as ctx:
            self.ll.encode_helper_payload(over)
        self.assertIn("256", str(ctx.exception))
        self.assertIn("255", str(ctx.exception))


class TvPlayingGateTests(unittest.TestCase):
    def test_ambient_reads_the_shared_tv_playing_sensor(self):
        doc = yaml.safe_load(AMBIENT.read_text(encoding="utf-8"))
        auto = _automations(doc)["living_lights_ambient"]
        text = yaml.safe_dump(auto)
        self.assertNotIn("media_player.lg_tv", text)
        trigger_entities = [e for t in auto["triggers"] for e in (t.get("entity_id") or [])]
        self.assertIn(TV_PLAYING, trigger_entities)
        gate = auto["actions"][0]["if"][0]
        self.assertEqual(gate["condition"], "template")
        self.assertIn(f"is_state('{TV_PLAYING}', 'on')", gate["value_template"])
        self.assertIn("is_state('input_boolean.user_at_home', 'off')", gate["value_template"])
        self.assertIn(f"is_state('{ASLEEP}', 'on')", gate["value_template"])
        self.assertEqual(auto["actions"][0]["then"][0]["action"], "switch.turn_off")
        self.assertEqual(auto["actions"][0]["else"][0]["action"], "switch.turn_on")

    def test_return_home_scene_does_not_relight_a_film(self):
        doc = yaml.safe_load(PROACTIVE.read_text(encoding="utf-8"))
        seq = doc["script"]["homeai_return_home"]["sequence"]
        first_action = next(i for i, s in enumerate(seq) if not s.get("condition"))
        leading = seq[:first_action]
        states = [(c.get("entity_id"), c.get("state")) for c in leading
                  if c.get("condition") == "state"]
        self.assertIn((ASLEEP, "off"), states)
        self.assertIn((TRAVEL, "off"), states)
        # a template, not `state: off` (lg_tv reads unavailable when off)
        self.assertNotIn(TV_PLAYING, [e for e, _ in states])
        tv = [c for c in leading if c.get("condition") == "template"
              and TV_PLAYING in c.get("value_template", "")]
        self.assertEqual(len(tv), 1)
        self.assertIn(f"not is_state('{TV_PLAYING}', 'on')", tv[0]["value_template"])
        self.assertNotIn("media_player.lg_tv", yaml.safe_dump(seq))


# The wake-up latch moved on after this file was written: the M2 round-2
# change made `living_lights_woke_up_on_wake` fire on fifteen minutes of
# sustained occupancy instead of on the asleep latch clearing, so a ten-minute
# night excursion can no longer energize the house. Its tests live in
# tests/living_lights/test_wake_up_latch.py; nothing is asserted here.


class TemplateSyntaxTests(unittest.TestCase):
    def test_every_template_parses(self):
        try:
            import jinja2
        except ImportError:  # pragma: no cover
            self.skipTest("jinja2 not installed")
        env = jinja2.Environment()
        for path in (GOOD_MORNING, AMBIENT, LIFECYCLE, PROACTIVE):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for template in _templates(doc):
                with self.subTest(path=path.name, template=template[:60]):
                    env.parse(template)

    def test_function_files_and_yaml_comments_are_ascii(self):
        """The Python files are ASCII throughout. In the YAML packages every
        comment is ASCII; only pre-existing Home Assistant names (aliases,
        helper names, a logbook message) keep their em dashes, because
        test_ha_deploy_packages pins the alias text and they are live
        entity names."""
        for path in (FUNCTION_FILE, FUNCTION_FILE.parent.parent / "test_living_lights.py",
                     Path(__file__)):
            with self.subTest(path=path.name):
                path.read_text(encoding="ascii")
        for path in (GOOD_MORNING, AMBIENT, LIFECYCLE, PROACTIVE):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#") or not line.isascii():
                    with self.subTest(path=path.name, line=number):
                        self.assertTrue(line.isascii() or ("alias:" in line or "name:" in line
                                                           or "message:" in line), line)


if __name__ == "__main__":
    unittest.main()
