#!/usr/bin/env python3
"""test_living_lights_cooldown_gate.py — regression test for the cooldown-gate
template emitted by build-living-lights-actuators.py.

The gate compares an `input_datetime` value (HA renders this as a NAIVE
"YYYY-MM-DD HH:MM:SS" string) to a sensor's `last_changed` (HA stores this as
a tz-AWARE UTC datetime) and `now()` (tz-AWARE local). If the parsed
`input_datetime` is left naive, Python raises:

    TypeError: can't compare offset-naive and offset-aware datetimes

HA logs a WARNING and the condition evaluates to False — silently skipping
the actuator. The fix is `as_datetime(lm | string) | as_local` (the
`as_local` filter assumes naive input is DEFAULT_TIME_ZONE and attaches the
local tz, making the comparison tz-safe).

This test renders both branches of the gate (cooldown active vs expired)
through a Jinja2 environment with HA-flavoured globals/filters. If the
TypeError ever comes back, or the boolean flips, the assertion fails.

Run: `python tools/test_living_lights_cooldown_gate.py`
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, Undefined

TOOLS = Path(__file__).resolve().parent
# Filename has hyphens — can't use a normal `import`.
_spec = importlib.util.spec_from_file_location(
    "build_living_lights_actuators",
    TOOLS / "build-living-lights-actuators.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Arbitrary non-UTC tz so a wall-clock shift bug (not just a TypeError)
# would show up in the result. -5h stands in for the user's local zone.
LOCAL_TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 5, 26, 14, 30, 0, tzinfo=LOCAL_TZ)


# ───────────────────────── HA template stand-ins ─────────────────────────

class _StateObj:
    def __init__(self, state, last_changed):
        self.state = state
        self.last_changed = last_changed


class _DomainNs:
    """Backs `states.<domain>.<entity>` attribute access."""
    def __init__(self, obj_map, domain):
        self._obj_map = obj_map
        self._domain = domain

    def __getattr__(self, name):
        return self._obj_map.get(f"{self._domain}.{name}")


class _StatesProxy:
    """HA's `states` is callable AND has nested attribute access."""
    def __init__(self, state_map, obj_map):
        self._state_map = state_map
        self._obj_map = obj_map

    def __call__(self, eid):
        return self._state_map.get(eid, "")

    def __getattr__(self, domain):
        if domain.startswith("_"):
            raise AttributeError(domain)
        return _DomainNs(self._obj_map, domain)


def _as_datetime(value):
    """Mirror HA's behaviour: strings parse to NAIVE datetimes (that's the
    whole point of this test); numbers parse to tz-aware UTC."""
    if value is None or isinstance(value, Undefined):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    s = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _as_local(dt_val):
    """Mirror HA's `as_local`: naive ⇒ treat as DEFAULT_TIME_ZONE, then return
    in the local tz."""
    if dt_val is None or isinstance(dt_val, Undefined):
        return None
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=LOCAL_TZ)
    return dt_val.astimezone(LOCAL_TZ)


def _make_env(state_map, obj_map):
    states_proxy = _StatesProxy(state_map, obj_map)

    def is_state(eid, val):
        return state_map.get(eid) == val

    env = Environment()
    env.filters["as_local"] = _as_local
    env.globals.update({
        "states": states_proxy,
        "is_state": is_state,
        "as_datetime": _as_datetime,
        "as_local": _as_local,
        "now": lambda: NOW,
    })
    return env


# ───────────────────────── helpers ───────────────────────────────────────

def _extract_cooldown_template(zone: str = "office") -> str:
    yaml_text = _mod.emit_actuator(zone, _mod.LIGHT_TARGETS[zone])
    doc = yaml.safe_load(yaml_text)
    for cond in doc["automation"][0]["conditions"]:
        if cond.get("condition") == "template" and "last_manual_at" in str(cond.get("value_template", "")):
            return cond["value_template"]
    raise RuntimeError("no template condition found in generated YAML")


def _truthy(rendered: str) -> bool:
    """The gate emits either a literal `true`/`false` from a short-circuit
    branch, or the Python `True`/`False` repr from the final inequality.
    Last word, lower-cased, is the answer."""
    tokens = rendered.split()
    if not tokens:
        raise AssertionError(f"empty render: {rendered!r}")
    return tokens[-1].lower() == "true"


def _automation_by_alias(doc, alias: str):
    for automation in doc.get("automation", []):
        if automation.get("alias") == alias:
            return automation
    raise AssertionError(f"missing automation alias: {alias}")


def _trigger_specs(automation) -> set[tuple[str, str | None]]:
    return {
        (str(trigger.get("entity_id")), trigger.get("attribute"))
        for trigger in automation.get("triggers", [])
    }


def _target_entity_ids(action) -> list[str]:
    target = action.get("target") or {}
    entity_id = target.get("entity_id")
    if isinstance(entity_id, list):
        return [str(item) for item in entity_id]
    if entity_id:
        return [str(entity_id)]
    return []


def _assert_vision_capture_guard(automation, label: str) -> None:
    conditions = automation.get("conditions") or []
    for condition in conditions:
        if (
            condition.get("condition") == "state"
            and condition.get("entity_id") == "input_boolean.living_lights_vision_capture_override"
            and condition.get("state") == "off"
            and str(condition.get("for")) == "00:00:05"
        ):
            return
    raise AssertionError(
        f"{label} must ignore system-owned vision-capture light writes and "
        "their immediate restore grace period"
    )


# ───────────────────────── the test ──────────────────────────────────────

def main() -> int:
    passed_checks = 0
    actuator_yaml = _mod.emit_actuator("office", _mod.LIGHT_TARGETS["office"])
    if actuator_yaml.count("input_datetime.living_lights_zone_office_last_manual_at") < 2:
        raise AssertionError(
            "office actuator should check the manual cooldown both at action "
            "start and again after the present-branch fast-ramp delay."
        )
    passed_checks += 1

    manual_yaml = _mod.emit_manual_detection()
    manual_path = TOOLS.parent / "ha-config" / "packages" / "living_lights_manual_detection.yaml"
    if not manual_path.exists():
        raise AssertionError("checked-in living_lights_manual_detection.yaml is missing")
    passed_checks += 1
    if manual_path.read_text(encoding="utf-8") != manual_yaml:
        raise AssertionError(
            "checked-in living_lights_manual_detection.yaml is stale relative "
            "to build-living-lights-actuators.py"
        )
    passed_checks += 1

    manual_doc = yaml.safe_load(manual_yaml)
    automations = manual_doc["automation"]
    aliases = [automation.get("alias", "") for automation in automations]
    expected_fast_lights = list({
        **_mod.AGGREGATE_LIGHT_CONTROLLERS,
        **_mod.CO_CONTROLLERS,
    }.keys())
    expected_physical_lights = list(_mod.CO_CONTROLLERS.keys())
    fast_aliases = [
        alias for alias in aliases
        if alias.startswith("Living Lights - fast manual hold arm ")
    ]
    detect_aliases = [
        alias for alias in aliases
        if "manual touch detection (" in alias
    ]
    if len(fast_aliases) != len(expected_fast_lights):
        raise AssertionError(
            f"expected {len(expected_fast_lights)} fast manual armers, got {len(fast_aliases)}"
        )
    passed_checks += 1
    if len(detect_aliases) != len(expected_physical_lights):
        raise AssertionError(
            f"expected {len(expected_physical_lights)} settled manual detectors, got {len(detect_aliases)}"
        )
    passed_checks += 1

    for light_entity, owning_pairs in _mod.CO_CONTROLLERS.items():
        fast_alias = f"Living Lights - fast manual hold arm ({light_entity})"
        fast = _automation_by_alias(manual_doc, fast_alias)
        detect = next(
            (
                automation for automation in automations
                if str(automation.get("alias", "")).endswith(f"manual touch detection ({light_entity})")
            ),
            None,
        )
        if detect is None:
            raise AssertionError(f"missing settled manual detector for {light_entity}")
        passed_checks += 1

        _assert_vision_capture_guard(fast, f"{light_entity} fast armer")
        passed_checks += 1
        _assert_vision_capture_guard(detect, f"{light_entity} settled detector")
        passed_checks += 1

        if automations.index(fast) > automations.index(detect):
            raise AssertionError(
                f"{light_entity} fast manual armer must appear before the slower settled detector"
            )
        passed_checks += 1

        expected_triggers = {
            (light_entity, None),
            (light_entity, "brightness"),
            (light_entity, "color_temp_kelvin"),
            (light_entity, "color_temp"),
        }
        trigger_specs = _trigger_specs(fast)
        if not expected_triggers.issubset(trigger_specs):
            raise AssertionError(
                f"{light_entity} fast armer missing immediate state/brightness/CT triggers: "
                f"{sorted(expected_triggers - trigger_specs)}"
            )
        passed_checks += 1
        if any("for" in trigger for trigger in fast.get("triggers", [])):
            raise AssertionError(f"{light_entity} fast armer must be immediate, with no debounce `for`")
        passed_checks += 1

        fast_text = yaml.safe_dump(fast, sort_keys=False)
        if "parent_id | default(none, true) in [none, '', 'None']" in fast_text:
            raise AssertionError(
                f"{light_entity} fast armer must not depend on parent_id-only "
                "context filtering; Siri/HomeKit/Lutron changes can carry a parent context."
            )
        passed_checks += 1
        variables = (fast.get("actions") or [{}])[0].get("variables", {})
        if "match_ok" not in variables:
            raise AssertionError(
                f"{light_entity} fast armer must compare actual light state with "
                "Living Lights prediction before stamping a manual hold."
            )
        passed_checks += 1
        arm_condition = fast["actions"][1]["if"][0].get("value_template", "")
        if arm_condition != "{{ match_ok != true and match_ok != 'true' }}":
            raise AssertionError(
                f"{light_entity} fast armer must stamp a hold when the light "
                "state is not explained by the current Living Lights prediction."
            )
        passed_checks += 1

        then_actions = fast["actions"][1]["then"]
        expected_manual_targets = {
            f"input_datetime.living_lights_zone_{slug}_last_manual_at"
            for slug, _camera in owning_pairs
        }
        actual_manual_targets = set(_target_entity_ids(then_actions[0]))
        if actual_manual_targets != expected_manual_targets:
            raise AssertionError(
                f"{light_entity} fast armer should stamp every owning zone manual hold; "
                f"expected {sorted(expected_manual_targets)}, got {sorted(actual_manual_targets)}"
            )
        passed_checks += 1

        expected_context_targets = {
            f"input_text.living_lights_zone_{slug}_last_override_context_id"
            for slug, _camera in owning_pairs
        }
        actual_context_targets = set(_target_entity_ids(then_actions[1]))
        if actual_context_targets != expected_context_targets:
            raise AssertionError(
                f"{light_entity} fast armer should stamp every owning zone context id; "
                f"expected {sorted(expected_context_targets)}, got {sorted(actual_context_targets)}"
            )
        passed_checks += 1

        event = then_actions[2]
        event_data = event.get("event_data") or {}
        if event.get("event") != "living_lights_manual_hold_armed":
            raise AssertionError(f"{light_entity} fast armer must emit living_lights_manual_hold_armed")
        passed_checks += 1
        if "source_parent_id" not in event_data or "debug_match_ok" not in event_data:
            raise AssertionError(
                f"{light_entity} fast armer event must preserve source_parent_id and debug_match_ok"
            )
        passed_checks += 1

    first_detect_index = min(
        automations.index(automation)
        for automation in automations
        if "manual touch detection (" in str(automation.get("alias", ""))
    )
    for light_entity, owning_pairs in _mod.AGGREGATE_LIGHT_CONTROLLERS.items():
        fast_alias = f"Living Lights - fast manual hold arm ({light_entity})"
        fast = _automation_by_alias(manual_doc, fast_alias)
        passed_checks += 1

        _assert_vision_capture_guard(fast, f"{light_entity} aggregate fast armer")
        passed_checks += 1

        if automations.index(fast) > first_detect_index:
            raise AssertionError(
                f"{light_entity} aggregate fast armer must appear before slower settled detectors"
            )
        passed_checks += 1

        expected_triggers = {
            (light_entity, None),
            (light_entity, "brightness"),
            (light_entity, "color_temp_kelvin"),
            (light_entity, "color_temp"),
        }
        trigger_specs = _trigger_specs(fast)
        if not expected_triggers.issubset(trigger_specs):
            raise AssertionError(
                f"{light_entity} aggregate fast armer missing immediate triggers: "
                f"{sorted(expected_triggers - trigger_specs)}"
            )
        passed_checks += 1
        if any("for" in trigger for trigger in fast.get("triggers", [])):
            raise AssertionError(f"{light_entity} aggregate fast armer must not debounce")
        passed_checks += 1

        fast_text = yaml.safe_dump(fast, sort_keys=False)
        if "parent_id | default(none, true) in [none, '', 'None']" in fast_text:
            raise AssertionError(
                f"{light_entity} aggregate fast armer must tolerate parented HomeKit/Siri/Lutron contexts"
            )
        passed_checks += 1
        if "shell_command.living_lights_append_preference_log" in fast_text:
            raise AssertionError(
                f"{light_entity} aggregate fast armer should only stamp a hold, not log preferences"
            )
        passed_checks += 1

        variables = (fast.get("actions") or [{}])[0].get("variables", {})
        if "match_ok" not in variables:
            raise AssertionError(
                f"{light_entity} aggregate fast armer must compare actual state with predictions"
            )
        passed_checks += 1
        arm_condition = fast["actions"][1]["if"][0].get("value_template", "")
        if arm_condition != "{{ match_ok != true and match_ok != 'true' }}":
            raise AssertionError(
                f"{light_entity} aggregate fast armer must stamp only unexplained user changes"
            )
        passed_checks += 1

        then_actions = fast["actions"][1]["then"]
        expected_manual_targets = {
            f"input_datetime.living_lights_zone_{slug}_last_manual_at"
            for slug, _camera in owning_pairs
        }
        actual_manual_targets = set(_target_entity_ids(then_actions[0]))
        if actual_manual_targets != expected_manual_targets:
            raise AssertionError(
                f"{light_entity} aggregate fast armer should stamp every room-cover zone; "
                f"expected {sorted(expected_manual_targets)}, got {sorted(actual_manual_targets)}"
            )
        passed_checks += 1

        expected_context_targets = {
            f"input_text.living_lights_zone_{slug}_last_override_context_id"
            for slug, _camera in owning_pairs
        }
        actual_context_targets = set(_target_entity_ids(then_actions[1]))
        if actual_context_targets != expected_context_targets:
            raise AssertionError(
                f"{light_entity} aggregate fast armer should stamp every room-cover context id; "
                f"expected {sorted(expected_context_targets)}, got {sorted(actual_context_targets)}"
            )
        passed_checks += 1

        event = then_actions[2]
        event_data = event.get("event_data") or {}
        if event.get("event") != "living_lights_manual_hold_armed":
            raise AssertionError(f"{light_entity} aggregate fast armer must emit hold event")
        passed_checks += 1
        expected_owning_zones = [slug for slug, _camera in owning_pairs]
        if event_data.get("owning_zones") != expected_owning_zones:
            raise AssertionError(
                f"{light_entity} aggregate fast armer should preserve owning zone list"
            )
        passed_checks += 1
        if "source_parent_id" not in event_data or "debug_match_ok" not in event_data:
            raise AssertionError(
                f"{light_entity} aggregate fast armer event must preserve source_parent_id and debug_match_ok"
            )
        passed_checks += 1

    template_text = _extract_cooldown_template("office")
    if "or asleep" in template_text:
        raise AssertionError(
            "cooldown gate must not bypass manual holds while asleep; overnight "
            "manual light changes need to stick."
        )
    passed_checks += 1
    if "['vacant', 'present', 'pass_through', 'asleep']" not in template_text:
        raise AssertionError(
            "cooldown gate must include the classifier's asleep state in the "
            "managed-state list so manual touches still block automation."
        )
    passed_checks += 1
    # Specific string sanity-check: the as_local filter must appear inside the
    # manual_dt assignment (not just inside a comment). Catches a regression
    # where someone reverts the filter but leaves the comment.
    if "as_datetime(lm | string) | as_local" not in template_text:
        raise AssertionError(
            "cooldown gate is missing the `as_local` filter on the manual_dt "
            "parse — the tz fix is not present in the generated YAML. The bug "
            "will re-surface as TypeError warnings on every actuator trigger."
        )
    passed_checks += 1

    # Both scenarios: zone is vacant (cooldown is GATED on),
    # stable-occupancy is OFF (out-of-zone → max(manual_dt, leave_time) path),
    # asleep starts OFF for the baseline branches.
    stable_off = _StateObj("off", (NOW - timedelta(minutes=20)).astimezone(timezone.utc))
    common_state = {
        "sensor.living_room_office_lighting_state": "vacant",
        "input_boolean.living_lights_asleep": "off",
    }
    obj_map = {
        "binary_sensor.living_room_office_person_occupancy_stable": stable_off,
    }

    # Recent: manual 5 min ago → max(5min, 20min) = 5min → 300s < 900s
    #   → cooldown ACTIVE → expected FALSE (don't run actuator).
    # Old:    manual 30 min ago → max(30min, 20min) = 20min (leave_time wins)
    #   → 1200s > 900s → cooldown EXPIRED → expected TRUE (run actuator).
    recent_manual = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    old_manual    = (NOW - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

    scenarios = [
        ("recent_manual_in_cooldown_window", recent_manual, False, "vacant", "off"),
        ("old_manual_past_cooldown_window", old_manual, True, "vacant", "off"),
        ("recent_manual_while_asleep", recent_manual, False, "vacant", "on"),
        ("recent_manual_with_classifier_asleep_state", recent_manual, False, "asleep", "on"),
    ]

    env_template = _make_env({}, {}).from_string(template_text)
    failures = []
    for name, lm_value, expected, classifier_state, asleep_state in scenarios:
        state_map = dict(common_state)
        state_map["sensor.living_room_office_lighting_state"] = classifier_state
        state_map["input_boolean.living_lights_asleep"] = asleep_state
        state_map["input_datetime.living_lights_zone_office_last_manual_at"] = lm_value
        env = _make_env(state_map, obj_map)
        try:
            rendered = env.from_string(template_text).render()
        except TypeError as exc:
            failures.append(
                f"[{name}] TypeError during render — the tz bug is back: {exc}"
            )
            continue
        actual = _truthy(rendered)
        if actual != expected:
            failures.append(
                f"[{name}] expected truthy={expected}, got {actual!r} "
                f"(render: {rendered.strip()!r})"
            )
        else:
            passed_checks += 1
            print(f"  OK {name}: {actual}")

    # Bonus: the sentinel branch — manual_dt.year < 2000 ⇒ true regardless.
    sentinel_state = dict(common_state)
    sentinel_state["input_datetime.living_lights_zone_office_last_manual_at"] = (
        "1970-01-01 00:00:00"
    )
    sentinel_env = _make_env(sentinel_state, obj_map)
    try:
        sentinel_render = sentinel_env.from_string(template_text).render()
        if not _truthy(sentinel_render):
            failures.append(
                f"[sentinel_1970] expected true, got render={sentinel_render.strip()!r}"
            )
        else:
            passed_checks += 1
            print("  OK sentinel_1970: True")
    except TypeError as exc:
        failures.append(f"[sentinel_1970] TypeError: {exc}")

    default_office = _mod.emit_actuator("office", _mod.LIGHT_TARGETS["office"])
    canary_office = _mod.emit_actuator(
        "office",
        _mod.LIGHT_TARGETS["office"],
        omit_ct_zones={"office"},
    )
    canary_sink = _mod.emit_actuator(
        "sink",
        _mod.LIGHT_TARGETS["sink"],
        omit_ct_zones={"office"},
    )
    if "color_temp_kelvin:" not in default_office:
        failures.append("[ct_default] office pilot must keep CT injection by default")
    else:
        passed_checks += 1
        print("  OK ct_default: office injects CT by default")
    if "color_temp_kelvin:" in canary_office:
        failures.append("[ct_canary] office canary pilot must omit CT injection")
    elif "Adaptive Lighting canary owns color" not in canary_office:
        failures.append("[ct_canary] office canary pilot should document why CT is omitted")
    else:
        passed_checks += 1
        print("  OK ct_canary: office canary omits CT")
    if "color_temp_kelvin:" not in canary_sink:
        failures.append("[ct_non_canary] non-canary zones must keep CT injection")
    else:
        passed_checks += 1
        print("  OK ct_non_canary: sink still injects CT")

    if failures:
        print()
        for f in failures:
            print("FAIL", f)
        print(f"\n{passed_checks} pass . {len(failures)} fail")
        return 1
    print("\nAll cooldown-gate branches pass — tz-mismatch bug stays fixed.")
    print(f"{passed_checks} pass . 0 fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
