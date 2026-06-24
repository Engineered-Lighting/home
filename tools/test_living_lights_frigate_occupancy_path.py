#!/usr/bin/env python3
"""Regression coverage for the Frigate zone occupancy -> Living Lights path.

This suite is intentionally static and generator-backed. It proves that the
checked-in Home Assistant packages still listen to the raw Frigate zone
occupancy sensors, build de-flapped stable occupancy sensors, feed those into
the classifier, and wake the actuator directly when Frigate changes.

Run: `py -3 tools/test_living_lights_frigate_occupancy_path.py`
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
PACKAGES = REPO_ROOT / "ha-config" / "packages"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


yaml_gen = _load_module(
    "build_living_lights_yaml",
    TOOLS / "build-living-lights-yaml.py",
)
actuator_gen = _load_module(
    "build_living_lights_actuators",
    TOOLS / "build-living-lights-actuators.py",
)


def _trigger_entity_ids(triggers: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for trigger in triggers:
        entity_id = trigger.get("entity_id")
        if isinstance(entity_id, list):
            ids.extend(str(item) for item in entity_id)
        elif entity_id:
            ids.append(str(entity_id))
    return ids


def _find_template_block(
    template_doc: dict[str, Any],
    kind: str,
    unique_id: str,
) -> dict[str, Any]:
    for block in template_doc["template"]:
        for item in block.get(kind, []) or []:
            if item.get("unique_id") == unique_id:
                return block
    raise AssertionError(f"missing template block for {kind}.{unique_id}")


def _items_by_unique_id(block: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {
        str(item["unique_id"]): item
        for item in block.get(kind, []) or []
        if "unique_id" in item
    }


def _entities_in_tree(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        target = node.get("target")
        if isinstance(target, dict):
            entity_id = target.get("entity_id")
            if isinstance(entity_id, list):
                found.update(str(item) for item in entity_id)
            elif entity_id:
                found.add(str(entity_id))
        for value in node.values():
            found.update(_entities_in_tree(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_entities_in_tree(item))
    return found


def _vacant_sequence(automation: dict[str, Any]) -> list[dict[str, Any]]:
    choose = automation["actions"][2]["then"][0]["choose"]
    for branch in choose:
        conditions = branch.get("conditions") or []
        if conditions and conditions[0].get("value_template") == "{{ new_state == 'vacant' }}":
            return branch.get("sequence") or []
    raise AssertionError("missing vacant branch in actuator automation")


def _state_branch_sequence(automation: dict[str, Any], state: str) -> list[dict[str, Any]]:
    choose = automation["actions"][2]["then"][0]["choose"]
    expected = "{{ new_state == '" + state + "' }}"
    for branch in choose:
        conditions = branch.get("conditions") or []
        if conditions and conditions[0].get("value_template") == expected:
            return branch.get("sequence") or []
    raise AssertionError(f"missing {state} branch in actuator automation")


def _turn_on_calls(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [action for action in actions if action.get("action") == "light.turn_on"]


def _target_entity(action: dict[str, Any]) -> str:
    target = action.get("target") or {}
    return str(target.get("entity_id", ""))


def _full_observability_package() -> str:
    return "\n".join([
        yaml_gen.HEADER,
        yaml_gen.emit_input_boolean(),
        yaml_gen.emit_input_text(),
        yaml_gen.emit_input_number(),
        yaml_gen.emit_input_datetime(),
        yaml_gen.emit_template(),
        yaml_gen.emit_mqtt(),
        yaml_gen.emit_automations(),
    ])


def main() -> int:
    passed_checks = 0
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        nonlocal passed_checks
        if condition:
            passed_checks += 1
        else:
            failures.append(message)

    actuated_zones = [
        slug
        for slug, targets in actuator_gen.LIGHT_TARGETS.items()
        if targets
    ]
    all_zones = list(yaml_gen.ZONES.keys())

    check("sofa" in actuated_zones, "sofa must remain an actuated living-room zone")
    check("sink" in actuated_zones, "sink must remain an actuated kitchen zone")
    check("island_left" in actuated_zones, "left island must remain actuated")
    check("island_right" in actuated_zones, "right island must remain actuated")

    for slug in actuated_zones:
        check(slug in yaml_gen.ZONES, f"{slug} actuator missing from ZONES")
        check(
            actuator_gen.ZONE_CAMERA.get(slug) == yaml_gen.ZONES[slug]["camera"],
            f"{slug} actuator camera does not match observability camera",
        )

    for slug in all_zones:
        check(
            yaml_gen._slug_to_entity_id(slug) == f"binary_sensor.{slug}_person_occupancy",
            f"{slug} raw Frigate entity should use the bare zone slug",
        )

    expected_obs = _full_observability_package()
    actual_obs_path = PACKAGES / "living_lights_observability.yaml"
    check(actual_obs_path.exists(), "checked-in observability package is missing")
    if actual_obs_path.exists():
        check(
            actual_obs_path.read_text(encoding="utf-8") == expected_obs,
            "checked-in observability package is stale relative to the generator",
        )

    template_doc = yaml.safe_load(yaml_gen.emit_template())
    stable_block = _find_template_block(
        template_doc,
        "binary_sensor",
        "living_room_sofa_person_occupancy_stable",
    )
    classifier_block = _find_template_block(
        template_doc,
        "sensor",
        "living_room_sofa_lighting_state",
    )
    any_occupied_block = _find_template_block(
        template_doc,
        "binary_sensor",
        "living_lights_any_occupied",
    )

    stable_triggers = set(_trigger_entity_ids(stable_block["trigger"]))
    classifier_triggers = set(_trigger_entity_ids(classifier_block["trigger"]))
    stable_sensors = _items_by_unique_id(stable_block, "binary_sensor")
    classifier_sensors = _items_by_unique_id(classifier_block, "sensor")
    any_occupied = _items_by_unique_id(any_occupied_block, "binary_sensor")[
        "living_lights_any_occupied"
    ]
    any_occupied_state = str(any_occupied.get("state", ""))

    for slug, meta in yaml_gen.ZONES.items():
        camera = meta["camera"]
        raw_entity = f"binary_sensor.{slug}_person_occupancy"
        stable_entity = f"binary_sensor.{camera}_{slug}_person_occupancy_stable"
        stable_uid = f"{camera}_{slug}_person_occupancy_stable"
        classifier_uid = f"{camera}_{slug}_lighting_state"
        classifier_entity = f"sensor.{camera}_{slug}_lighting_state"

        stable_sensor = stable_sensors.get(stable_uid)
        check(stable_sensor is not None, f"{slug} stable occupancy sensor missing")
        check(raw_entity in stable_triggers, f"{slug} raw occupancy does not trigger stable sensor")
        if stable_sensor is not None:
            stable_state = str(stable_sensor.get("state", ""))
            check(
                f"states.{raw_entity}" in stable_state,
                f"{slug} stable sensor does not read raw Frigate occupancy",
            )
            check(
                f"< {yaml_gen.STABLE_OCCUPANCY_HOLD_SECONDS}" in stable_state,
                f"{slug} stable sensor missing hold-window logic",
            )

        if slug in yaml_gen.KITCHEN_PERSON_FALLBACK_ZONES:
            check(
                "binary_sensor.kitchen_person_occupancy" in stable_triggers,
                f"{slug} stable trigger missing kitchen camera-level fallback",
            )
            check(
                stable_sensor is not None
                and "states.binary_sensor.kitchen_person_occupancy" in str(stable_sensor.get("state", "")),
                f"{slug} stable sensor missing kitchen camera-level fallback state read",
            )
            check(
                stable_sensor is not None
                and "fallback.state == 'on'" in str(stable_sensor.get("state", "")),
                f"{slug} stable sensor fallback does not turn on immediately",
            )
        else:
            check(
                stable_sensor is None
                or "states.binary_sensor.kitchen_person_occupancy" not in str(stable_sensor.get("state", "")),
                f"{slug} unexpectedly uses kitchen camera fallback",
            )

        classifier_sensor = classifier_sensors.get(classifier_uid)
        check(classifier_sensor is not None, f"{slug} classifier sensor missing")
        check(raw_entity in classifier_triggers, f"{slug} raw occupancy does not trigger classifier")
        check(stable_entity in classifier_triggers, f"{slug} stable occupancy does not trigger classifier")
        if classifier_sensor is not None:
            classifier_state = str(classifier_sensor.get("state", ""))
            brightness = str(classifier_sensor.get("attributes", {}).get("predicted_brightness_pct", ""))
            check(
                f"states.{stable_entity}" in classifier_state,
                f"{slug} classifier does not read the stable occupancy object",
            )
            check(
                f"states('{stable_entity}')" in classifier_state,
                f"{slug} classifier does not read the stable occupancy state",
            )
            check(
                f"states.{raw_entity}" in classifier_state,
                f"{slug} classifier does not read the raw occupancy object",
            )
            check(
                f"states('{raw_entity}')" in classifier_state,
                f"{slug} classifier does not read the raw occupancy state",
            )
            check(
                "{% elif stable == 'off' %}vacant" in classifier_state,
                f"{slug} classifier no longer treats stable-off as vacant",
            )
            check(
                "{% else %}present" in classifier_state,
                f"{slug} classifier no longer treats stable-on as present",
            )
            check(
                "cap = asleep_cap_pct if asleep else 100" in brightness,
                f"{slug} brightness template missing asleep cap behavior",
            )
            check(
                "floor = 0 if asleep else" in brightness,
                f"{slug} brightness template missing asleep vacant-floor behavior",
            )
            check(
                "{{ [floor, [ramp_target_pct, cap] | min] | max }}" in brightness,
                f"{slug} occupied brightness no longer lifts to the asleep cap",
            )

        check(
            classifier_entity in any_occupied_state,
            f"{slug} classifier not included in house-wide occupied sensor",
        )
        check(
            raw_entity not in any_occupied_state,
            f"{slug} raw occupancy leaked into house-wide occupied sensor",
        )

    for slug in actuated_zones:
        camera = actuator_gen.ZONE_CAMERA[slug]
        targets = actuator_gen.LIGHT_TARGETS[slug]
        sensor_id = f"sensor.{camera}_{slug}_lighting_state"
        stable_entity = f"binary_sensor.{camera}_{slug}_person_occupancy_stable"
        raw_entity = f"binary_sensor.{slug}_person_occupancy"
        path = PACKAGES / f"living_lights_pilot_{slug}.yaml"
        expected = actuator_gen.emit_actuator(slug, targets)

        check(path.exists(), f"{slug} checked-in actuator package is missing")
        if path.exists():
            actual = path.read_text(encoding="utf-8")
            check(actual == expected, f"{slug} actuator package is stale relative to generator")
            doc = yaml.safe_load(actual)
        else:
            doc = yaml.safe_load(expected)

        automation = doc["automation"][0]
        trigger_ids = set(_trigger_entity_ids(automation["triggers"]))
        check(sensor_id in trigger_ids, f"{slug} actuator does not trigger on classifier state")
        check(stable_entity in trigger_ids, f"{slug} actuator does not trigger on stable occupancy")
        check(raw_entity in trigger_ids, f"{slug} actuator does not trigger on raw occupancy")
        check(
            "input_boolean.living_lights_asleep" in trigger_ids,
            f"{slug} actuator does not re-evaluate when asleep flips",
        )
        check(
            "input_boolean.living_lights_shadow" in trigger_ids,
            f"{slug} actuator does not re-evaluate when shadow mode is disabled",
        )

        condition_templates = [
            str(cond.get("value_template", ""))
            for cond in automation.get("conditions", [])
            if cond.get("condition") == "template"
        ]
        condition_text = "\n".join(condition_templates)
        check(
            f"eid == '{sensor_id}'" in condition_text,
            f"{slug} actuator no-op guard is not scoped to its classifier sensor",
        )
        check(
            "trigger.attribute" in condition_text and "attr in [none, '', 'None']" in condition_text,
            f"{slug} actuator no-op guard must allow dedicated attribute triggers",
        )
        check(
            "trigger.from_state.state == trigger.to_state.state" in condition_text,
            f"{slug} actuator no-op guard does not block same-state classifier churn",
        )
        check(
            "['vacant', 'present', 'pass_through', 'asleep']" in condition_text,
            f"{slug} manual cooldown gate does not manage the asleep state",
        )
        check(
            "or asleep" not in condition_text,
            f"{slug} manual cooldown gate still bypasses while asleep",
        )

        first_action = automation["actions"][0]
        first_action_text = str(first_action)
        check(
            stable_entity in first_action_text and raw_entity in first_action_text,
            f"{slug} actuator delay guard does not cover both occupancy triggers",
        )
        check(
            first_action.get("then", [{}])[0].get("delay") == "00:00:00.25",
            f"{slug} actuator missing the Frigate classifier-settle delay",
        )

        variables_action = automation["actions"][1]
        variables_text = str(variables_action)
        check(
            f"states('{sensor_id}')" in variables_text,
            f"{slug} actuator does not read the current classifier state after delay",
        )

        present_actions = _state_branch_sequence(automation, "present")
        check(len(present_actions) >= 4, f"{slug} present branch is missing the fade-on flow")
        if len(present_actions) >= 4:
            zone_max_text = str(present_actions[0].get("variables", {}).get("zone_max_bri", ""))
            for _domain, light_entity, _supports_color in targets:
                check(
                    f"state_attr('{light_entity}', 'brightness')" in zone_max_text,
                    f"{slug} present branch does not inspect current brightness for {light_entity}",
                )

            fast_gate = present_actions[1]
            fast_gate_text = str((fast_gate.get("if") or [{}])[0].get("value_template", ""))
            check(
                "zone_max_bri < safe_initial" in fast_gate_text,
                f"{slug} present branch does not skip the fast ramp when already bright",
            )
            fast_then = fast_gate.get("then") or []
            fast_calls = _turn_on_calls(fast_then)
            fast_delay = next((item.get("delay") for item in fast_then if "delay" in item), None)
            check(
                len(fast_calls) == len(targets),
                f"{slug} present fast ramp should touch each target exactly once",
            )
            check(
                fast_delay == {"seconds": actuator_gen.RAMP_FAST_S},
                f"{slug} present fast ramp delay should match RAMP_FAST_S",
            )
            for _domain, light_entity, _supports_color in targets:
                matching = [call for call in fast_calls if _target_entity(call) == light_entity]
                check(
                    len(matching) == 1,
                    f"{slug} present fast ramp missing {light_entity}",
                )
                if matching:
                    data = matching[0].get("data") or {}
                    check(
                        data.get("brightness_pct") == "{{ [safe_initial, 1] | max }}",
                        f"{slug}/{light_entity} fast ramp does not use safe_initial",
                    )
                    check(
                        data.get("transition") == actuator_gen.RAMP_FAST_S,
                        f"{slug}/{light_entity} fast ramp transition drifted",
                    )
                    check(
                        sensor_id in str(data.get("color_temp_kelvin", "")),
                        f"{slug}/{light_entity} fast ramp no longer pins CT from classifier",
                    )

            cooldown_text = str(present_actions[2].get("value_template", ""))
            check(
                f"states('{sensor_id}')" in cooldown_text,
                f"{slug} present branch cooldown recheck does not read classifier state",
            )
            check(
                f"input_datetime.living_lights_zone_{slug}_last_manual_at" in cooldown_text,
                f"{slug} present branch cooldown recheck does not read manual hold timestamp",
            )
            check(
                "as_datetime(lm | string) | as_local" in cooldown_text,
                f"{slug} present branch cooldown recheck is missing timezone-safe parsing",
            )
            check(
                stable_entity in cooldown_text and "stable_obj.state == 'on'" in cooldown_text,
                f"{slug} present branch cooldown recheck does not block in-zone manual holds",
            )

            slow_calls = _turn_on_calls(present_actions[3:])
            check(
                len(slow_calls) == len(targets),
                f"{slug} present slow ramp should touch each target exactly once",
            )
            for _domain, light_entity, _supports_color in targets:
                matching = [call for call in slow_calls if _target_entity(call) == light_entity]
                check(
                    len(matching) == 1,
                    f"{slug} present slow ramp missing {light_entity}",
                )
                if matching:
                    data = matching[0].get("data") or {}
                    check(
                        data.get("brightness_pct") == "{{ [predicted_bri, 1] | max }}",
                        f"{slug}/{light_entity} slow ramp does not use predicted_bri",
                    )
                    check(
                        data.get("transition") == actuator_gen.RAMP_SLOW_S,
                        f"{slug}/{light_entity} slow ramp transition drifted",
                    )
                    check(
                        sensor_id in str(data.get("color_temp_kelvin", "")),
                        f"{slug}/{light_entity} slow ramp no longer pins CT from classifier",
                    )

        vacant_actions = _vacant_sequence(automation)
        for _domain, light_entity, _supports_color in targets:
            owners = actuator_gen.CO_CONTROLLERS.get(light_entity, [])
            other_owners = [(z, c) for z, c in owners if z != slug]
            matching_actions = [
                action for action in vacant_actions
                if light_entity in _entities_in_tree(action)
            ]
            check(
                len(matching_actions) == 1,
                f"{slug} vacant branch should have one action tree for {light_entity}",
            )
            if not matching_actions:
                continue
            guard = str((matching_actions[0].get("if") or [{}])[0].get("value_template", ""))
            if other_owners:
                check(
                    "namespace(any_active=false)" in guard and "{{ not ns.any_active }}" in guard,
                    f"{slug}/{light_entity} shared vacant action missing co-controller guard",
                )
                for other_slug, other_camera in other_owners:
                    other_sensor = f"sensor.{other_camera}_{other_slug}_lighting_state"
                    check(
                        f"states('{other_sensor}') != 'vacant'" in guard,
                        f"{slug}/{light_entity} guard does not check {other_sensor}",
                    )
            else:
                check(
                    "namespace(any_active=false)" not in guard,
                    f"{slug}/{light_entity} single-owner vacant action should not have co-controller guard",
                )

    if failures:
        print()
        for failure in failures:
            print("FAIL", failure)
        print(f"\n{passed_checks} pass . {len(failures)} fail")
        return 1

    print("Frigate occupancy path invariants pass.")
    print(f"{passed_checks} pass . 0 fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
