#!/usr/bin/env python3
"""probe-lights-drawer.py — scenario tests for the /lights tuning drawer.

Validates that every slider + toggle in the Home app's /lights drawer
actually propagates end-to-end:

  drag in UI → input_number/set_value → classifier re-evaluates →
  pilot fires → light.turn_on → bulb brightness/CT changes.

Bypasses the UI entirely — talks directly to HA's REST API. If these
probes pass but the UI doesn't behave the same way, the bug is in
the frontend's slider component, not the HA cascade.

USAGE
  # Read-only check (no state changes):
  python tools/probe-lights-drawer.py --token-from-haos

  # Full live suite (will change input_numbers + light states briefly):
  python tools/probe-lights-drawer.py --token-from-haos --live --yes

  # Or with explicit token:
  HA_TOKEN=<token> python tools/probe-lights-drawer.py --live --yes

EXIT CODES
  0 — all probes ok
  1 — one or more probes failed
  2 — bad CLI args / not connected to HA
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


# Default HA URL — overridden by HA_URL env or --url.
DEFAULT_HA_URL = "http://192.168.0.125:8123"

# Office is the canonical test zone — primary user focus, single-owner light.
OFFICE_CLASSIFIER = "sensor.living_room_office_lighting_state"
OFFICE_LIGHT = "light.office"

# Tolerances when comparing actual vs expected.
BRIGHTNESS_TOL_PCT = 2     # ±2 percentage points on classifier predictions
CT_TOL_K = 50              # ±50 K (matches the input_number step)
PIPELINE_SETTLE_S = 6      # max seconds to wait for classifier to follow
                           # input_number changes (classifier has /5 time_pattern;
                           # input_number is also in the trigger list for ~1s response)


# ── HTTP helpers (stdlib only, mirrors probe-ct-convergence pattern) ─


def _request(method: str, url: str, token: str, body=None, timeout: float = 8.0):
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8")
            try:
                return r.status, json.loads(text) if text else None
            except json.JSONDecodeError:
                return r.status, text
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)


def get_state(url: str, token: str, entity_id: str):
    """Return (state, attributes) for an entity, or (None, None) on miss."""
    status, body = _request("GET", f"{url}/api/states/{entity_id}", token)
    if status == 200 and isinstance(body, dict):
        return body.get("state"), body.get("attributes", {})
    return None, None


def set_input_number(url: str, token: str, entity_id: str, value):
    """POST input_number/set_value. Returns True on HTTP 200."""
    status, _ = _request(
        "POST",
        f"{url}/api/services/input_number/set_value",
        token,
        body={"entity_id": entity_id, "value": value},
    )
    return status == 200


def set_input_text(url: str, token: str, entity_id: str, value: str):
    status, _ = _request(
        "POST",
        f"{url}/api/services/input_text/set_value",
        token,
        body={"entity_id": entity_id, "value": value},
    )
    return status == 200


def toggle_input_boolean(url: str, token: str, entity_id: str, on: bool):
    service = "turn_on" if on else "turn_off"
    status, _ = _request(
        "POST",
        f"{url}/api/services/input_boolean/{service}",
        token,
        body={"entity_id": entity_id},
    )
    return status == 200


# ── Probe primitives ────────────────────────────────────────────────


def to_int(v, fallback=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return fallback


def read_office_pred_bri(url: str, token: str) -> Optional[int]:
    state, attrs = get_state(url, token, OFFICE_CLASSIFIER)
    if attrs is None:
        return None
    return to_int(attrs.get("predicted_brightness_pct"), None)


def read_office_pred_ct(url: str, token: str) -> Optional[int]:
    state, attrs = get_state(url, token, OFFICE_CLASSIFIER)
    if attrs is None:
        return None
    return to_int(attrs.get("predicted_color_temp_kelvin"), None)


def read_light_bri_pct(url: str, token: str, light: str) -> Optional[int]:
    state, attrs = get_state(url, token, light)
    if state != "on":
        return 0
    if attrs is None:
        return None
    raw = attrs.get("brightness")
    if raw is None:
        return None
    # HA brightness is 0-255; convert to percent.
    return round(to_int(raw, 0) / 2.55)


def read_light_ct(url: str, token: str, light: str) -> Optional[int]:
    state, attrs = get_state(url, token, light)
    if state != "on" or attrs is None:
        return None
    return to_int(attrs.get("color_temp_kelvin"), None)


def wait_for_pred_bri(url: str, token: str, expected: int, timeout: float = PIPELINE_SETTLE_S):
    """Poll the office classifier's predicted_brightness_pct until it
    matches expected (±BRIGHTNESS_TOL_PCT) or the timeout elapses."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        v = read_office_pred_bri(url, token)
        if v is not None:
            last = v
            if abs(v - expected) <= BRIGHTNESS_TOL_PCT:
                return True, v
        time.sleep(0.5)
    return False, last


def wait_for_pred_ct(url: str, token: str, expected: int, timeout: float = PIPELINE_SETTLE_S):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        v = read_office_pred_ct(url, token)
        if v is not None:
            last = v
            if abs(v - expected) <= CT_TOL_K:
                return True, v
        time.sleep(0.5)
    return False, last


# ── Test cases ──────────────────────────────────────────────────────


def case_working_hours_floor(url, token, log):
    """Vacant floor slider: drag working_hours_floor_pct, classifier follows."""
    name = "working_hours_floor_pct drives vacant brightness"
    # Precondition: working_hours_active = on, woke_up_today = on,
    # office state = vacant (we don't enforce vacant; we just read what's there).
    wh_active, _ = get_state(url, token, "binary_sensor.living_lights_working_hours_active")
    if wh_active != "on":
        log("skip", name, f"working_hours_active = {wh_active}, need 'on'")
        return None
    off_state, _ = get_state(url, token, OFFICE_CLASSIFIER)
    if off_state != "vacant":
        log("skip", name, f"office classifier = {off_state}, need 'vacant' for vacant-floor test")
        return None
    # Read baseline
    before = read_office_pred_bri(url, token)
    if before is None:
        log("not_ok", name, "could not read office predicted_brightness_pct")
        return False
    target_value = 30 if before > 60 else 90
    set_input_number(url, token, "input_number.living_lights_working_hours_floor_pct", target_value)
    ok, after = wait_for_pred_bri(url, token, target_value)
    # Restore default
    set_input_number(url, token, "input_number.living_lights_working_hours_floor_pct", 80)
    wait_for_pred_bri(url, token, 80)
    if ok:
        log("ok", name, f"{before}% -> {after}% (target {target_value}%)")
        return True
    log("not_ok", name, f"set {target_value}% but classifier read {after}% (started at {before}%)")
    return False


def case_working_hours_present(url, token, log):
    """Present target slider: drag working_hours_present_pct, classifier follows when present."""
    name = "working_hours_present_pct drives present brightness"
    wh_active, _ = get_state(url, token, "binary_sensor.living_lights_working_hours_active")
    if wh_active != "on":
        log("skip", name, f"working_hours_active = {wh_active}, need 'on'")
        return None
    off_state, _ = get_state(url, token, OFFICE_CLASSIFIER)
    if off_state != "present":
        log("skip", name, f"office classifier = {off_state}, need 'present' (walk into the office to test)")
        return None
    before = read_office_pred_bri(url, token)
    target_value = 70 if before > 85 else 100
    set_input_number(url, token, "input_number.living_lights_working_hours_present_pct", target_value)
    ok, after = wait_for_pred_bri(url, token, target_value)
    set_input_number(url, token, "input_number.living_lights_working_hours_present_pct", 95)
    wait_for_pred_bri(url, token, 95)
    if ok:
        log("ok", name, f"{before}% -> {after}% (target {target_value}%)")
        return True
    log("not_ok", name, f"set {target_value}% but classifier read {after}% (started at {before}%)")
    return False


def case_vacant_day_pct(url, token, log):
    """Vacant day baseline — applies ONLY when working_hours_active is off."""
    name = "vacant_day_pct drives vacant brightness when working_hours is off"
    # Force working_hours off by clearing woke_up_today
    woke, _ = get_state(url, token, "input_boolean.living_lights_woke_up_today")
    toggle_input_boolean(url, token, "input_boolean.living_lights_woke_up_today", False)
    time.sleep(2)
    wh_active, _ = get_state(url, token, "binary_sensor.living_lights_working_hours_active")
    if wh_active != "off":
        # Restore + skip
        if woke == "on":
            toggle_input_boolean(url, token, "input_boolean.living_lights_woke_up_today", True)
        log("skip", name, f"could not force working_hours off (still {wh_active})")
        return None
    off_state, _ = get_state(url, token, OFFICE_CLASSIFIER)
    if off_state != "vacant":
        if woke == "on":
            toggle_input_boolean(url, token, "input_boolean.living_lights_woke_up_today", True)
        log("skip", name, f"office classifier = {off_state}, need 'vacant'")
        return None
    before = read_office_pred_bri(url, token)
    target_value = 25 if before > 40 else 70
    set_input_number(url, token, "input_number.living_lights_vacant_day_pct", target_value)
    ok, after = wait_for_pred_bri(url, token, target_value)
    set_input_number(url, token, "input_number.living_lights_vacant_day_pct", 50)
    # Restore woke_up_today
    if woke == "on":
        toggle_input_boolean(url, token, "input_boolean.living_lights_woke_up_today", True)
    if ok:
        log("ok", name, f"{before}% -> {after}% (target {target_value}%)")
        return True
    log("not_ok", name, f"set {target_value}% but classifier read {after}% (started at {before}%)")
    return False


def case_working_hours_ct_override(url, token, log):
    """working_hours_ct_k override: 0 = inherit ToD, >0 = use that K."""
    name = "working_hours_ct_k override drives predicted CT"
    wh_active, _ = get_state(url, token, "binary_sensor.living_lights_working_hours_active")
    if wh_active != "on":
        log("skip", name, f"working_hours_active = {wh_active}, need 'on'")
        return None
    # Read baseline (should be ToD-driven)
    ct_before = read_office_pred_ct(url, token)
    if ct_before is None:
        log("not_ok", name, "could not read predicted_color_temp_kelvin")
        return False
    target_ct = 3500 if ct_before < 3000 else 2200
    set_input_number(url, token, "input_number.living_lights_working_hours_ct_k", target_ct)
    ok, after = wait_for_pred_ct(url, token, target_ct)
    # Reset to 0 (inherit)
    set_input_number(url, token, "input_number.living_lights_working_hours_ct_k", 0)
    wait_for_pred_ct(url, token, ct_before)
    if ok:
        log("ok", name, f"{ct_before}K -> {after}K (target {target_ct}K)")
        return True
    log("not_ok", name, f"set {target_ct}K but classifier read {after}K (started at {ct_before}K)")
    return False


def case_warmth_bias(url, token, log):
    """Warmth bias: additive K offset on top of cascade output."""
    name = "warmth_bias_k adds K offset (scope = all)"
    scope_before, _ = get_state(url, token, "input_text.living_lights_bias_zone_scope")
    set_input_text(url, token, "input_text.living_lights_bias_zone_scope", "all")
    time.sleep(1)
    ct_before = read_office_pred_ct(url, token)
    if ct_before is None:
        log("not_ok", name, "could not read predicted_color_temp_kelvin")
        return False
    # Try +200 K, clamped to AL max 4000
    bias = 200
    expected = min(ct_before + bias, 4000)
    set_input_number(url, token, "input_number.living_lights_warmth_bias_k", bias)
    ok, after = wait_for_pred_ct(url, token, expected)
    set_input_number(url, token, "input_number.living_lights_warmth_bias_k", 0)
    wait_for_pred_ct(url, token, ct_before)
    if scope_before:
        set_input_text(url, token, "input_text.living_lights_bias_zone_scope", scope_before)
    if ok:
        log("ok", name, f"{ct_before}K + {bias} = {after}K (expected {expected}K)")
        return True
    log("not_ok", name, f"bias +{bias}K but classifier read {after}K (expected {expected}K, started {ct_before}K)")
    return False


def case_brightness_bias_per_zone(url, token, log):
    """Brightness bias scoped to a single zone."""
    name = "brightness_bias_pp scoped to 'office' only affects office"
    # Save current scope
    scope_before, _ = get_state(url, token, "input_text.living_lights_bias_zone_scope")
    # Set scope to office
    set_input_text(url, token, "input_text.living_lights_bias_zone_scope", "office")
    time.sleep(1)
    bri_office_before = read_office_pred_bri(url, token)
    _, kitchen_attrs = get_state(url, token, "sensor.kitchen_sink_lighting_state")
    bri_kitchen_before = to_int(kitchen_attrs.get("predicted_brightness_pct"), None) if kitchen_attrs else None
    if bri_office_before is None or bri_kitchen_before is None:
        # restore
        if scope_before:
            set_input_text(url, token, "input_text.living_lights_bias_zone_scope", scope_before)
        log("not_ok", name, "could not read baseline brightness for office or kitchen")
        return False
    # Set bias -20 (clamped to cap=100, min=0)
    bias = -20
    expected_office = max(0, bri_office_before + bias)
    set_input_number(url, token, "input_number.living_lights_brightness_bias_pp", bias)
    time.sleep(3)
    bri_office_after = read_office_pred_bri(url, token)
    _, kitchen_attrs_after = get_state(url, token, "sensor.kitchen_sink_lighting_state")
    bri_kitchen_after = to_int(kitchen_attrs_after.get("predicted_brightness_pct"), None) if kitchen_attrs_after else None
    # Restore
    set_input_number(url, token, "input_number.living_lights_brightness_bias_pp", 0)
    set_input_text(url, token, "input_text.living_lights_bias_zone_scope", scope_before or "all")
    time.sleep(2)
    office_changed = abs((bri_office_after or 0) - expected_office) <= BRIGHTNESS_TOL_PCT
    kitchen_unchanged = abs((bri_kitchen_after or 0) - bri_kitchen_before) <= BRIGHTNESS_TOL_PCT
    if office_changed and kitchen_unchanged:
        log("ok", name, f"office {bri_office_before}% -> {bri_office_after}%, kitchen unchanged at {bri_kitchen_after}%")
        return True
    log("not_ok", name,
        f"office: {bri_office_before}% -> {bri_office_after}% (expected {expected_office}%); "
        f"kitchen: {bri_kitchen_before}% -> {bri_kitchen_after}% (should be unchanged)")
    return False


def case_actual_light_follows_classifier(url, token, log):
    """End-to-end: drag a slider, the ACTUAL bulb changes (not just classifier)."""
    name = "light.office actual brightness follows classifier (pilot fires)"
    # Precondition: working_hours active, office vacant or present (any state with a non-zero predicted)
    wh_active, _ = get_state(url, token, "binary_sensor.living_lights_working_hours_active")
    if wh_active != "on":
        log("skip", name, f"working_hours_active = {wh_active}, need 'on'")
        return None
    # Cooldown check — last_manual_at must be unset/sentinel
    last_manual_state, _ = get_state(url, token, "input_datetime.living_lights_zone_office_last_manual_at")
    if last_manual_state and not last_manual_state.startswith("1970-"):
        log("skip", name, f"office cooldown active (last_manual_at = {last_manual_state})")
        return None
    off_state, _ = get_state(url, token, OFFICE_CLASSIFIER)
    if off_state not in ("vacant", "present"):
        log("skip", name, f"office classifier = {off_state}, need 'vacant' or 'present'")
        return None
    # Pick a target that's a noticeable jump from current
    bri_before = read_office_pred_bri(url, token)
    light_before = read_light_bri_pct(url, token, OFFICE_LIGHT)
    if bri_before is None:
        log("not_ok", name, "could not read classifier prediction")
        return False
    target_value = 30 if bri_before > 60 else 90
    knob = ("input_number.living_lights_working_hours_floor_pct"
            if off_state == "vacant"
            else "input_number.living_lights_working_hours_present_pct")
    default = 80 if off_state == "vacant" else 95
    set_input_number(url, token, knob, target_value)
    # Wait for classifier
    cls_ok, cls_after = wait_for_pred_bri(url, token, target_value)
    # Then wait for the actual light (pilot has 2s + 10s ramp, plus 5s cooldown buffer)
    time.sleep(14)
    light_after = read_light_bri_pct(url, token, OFFICE_LIGHT)
    # Restore
    set_input_number(url, token, knob, default)
    if not cls_ok:
        log("not_ok", name,
            f"classifier didn't follow ({cls_after}% vs target {target_value}%)")
        return False
    if light_after is None:
        log("not_ok", name, "could not read light.office brightness")
        return False
    if abs(light_after - target_value) <= 5:
        log("ok", name,
            f"slider {target_value}% -> classifier {cls_after}% -> bulb {light_after}% "
            f"(was {light_before}% / {bri_before}%)")
        return True
    log("not_ok", name,
        f"classifier OK at {cls_after}% but bulb at {light_after}% (target {target_value}%, was {light_before}%) — pilot blocked? cooldown? Hue lag?")
    return False


def push_ct_to_light(url, token, light_entity, ct_k):
    """Direct light.turn_on(color_temp_kelvin=X). Mimics the drawer's
    pushCTToAllLights mechanism — used to verify a bulb can actually
    receive a CT command and the Hue Bridge isn't dropping them."""
    status, _ = _request(
        "POST",
        f"{url}/api/services/light/turn_on",
        token,
        body={"entity_id": light_entity, "color_temp_kelvin": ct_k, "transition": 1.0},
    )
    return status == 200


def case_bulb_ct_direct_push(url, token, log):
    """Direct API push of CT to light.office. If this fails, the bulb /
    Hue Bridge / HA light service is the problem, not the cascade."""
    name = "light.office accepts direct color_temp_kelvin push (bypasses cascade)"
    state, attrs = get_state(url, token, OFFICE_LIGHT)
    if state != "on":
        log("skip", name, f"light.office is {state} (need 'on' to compare CT)")
        return None
    ct_before = read_light_ct(url, token, OFFICE_LIGHT)
    # Pick a target distinguishable from the current value AND from AL's
    # current target (so AL re-pushing on its tick would be detectable).
    target = 4000 if (ct_before or 0) < 3000 else 2200
    if not push_ct_to_light(url, token, OFFICE_LIGHT, target):
        log("not_ok", name, "light.turn_on returned non-200")
        return False
    # Sample within 5 seconds (Hue Bridge accepts pretty quickly).
    deadline = time.time() + 5
    last = ct_before
    ok = False
    while time.time() < deadline:
        last = read_light_ct(url, token, OFFICE_LIGHT)
        if last and abs(last - target) <= CT_TOL_K:
            ok = True
            break
        time.sleep(0.4)
    if ok:
        log("ok", name, f"{ct_before}K -> {last}K (target {target}K)")
        return True
    log("not_ok", name, f"pushed {target}K but bulb at {last}K after 5s (was {ct_before}K)")
    return False


def case_ct_slider_drives_actual_bulb(url, token, log):
    """End-to-end CT slider → bulb. This is what the user actually
    wants: drag the ToD Midday CT slider, bulb CT changes. The drawer
    fires light.turn_on directly after the slider commits to bypass the
    brightness-pilot (which doesn't fire on CT-only) and to get ahead
    of AL's 90s cycle. This test simulates the drawer's flow."""
    name = "ToD midday CT slider drives light.office actual bulb CT"
    # Read what the profile currently says we're in
    profile_state, _ = get_state(url, token, "sensor.living_lights_profile")
    if profile_state != "midday":
        log("skip", name, f"profile = {profile_state}, test needs 'midday' bucket active")
        return None
    light_state, _ = get_state(url, token, OFFICE_LIGHT)
    if light_state != "on":
        log("skip", name, f"light.office = {light_state} (need 'on' to read CT)")
        return None
    ct_before = read_light_ct(url, token, OFFICE_LIGHT)
    # Pick a target distinguishable from the current.
    target = 4000 if (ct_before or 0) < 3000 else 2200
    # Step 1: set the midday CT input_number (cascade input)
    set_input_number(url, token, "input_number.living_lights_ct_midday_k", target)
    # Step 2: wait for classifier to recompute
    wait_for_pred_ct(url, token, target)
    # Step 3: push CT to the bulb directly (what the drawer does)
    push_ct_to_light(url, token, OFFICE_LIGHT, target)
    # Step 4: wait + sample
    time.sleep(2)
    ct_after = read_light_ct(url, token, OFFICE_LIGHT)
    # Restore default + push back so AL doesn't fight on its next cycle.
    set_input_number(url, token, "input_number.living_lights_ct_midday_k", 2700)
    time.sleep(1)
    push_ct_to_light(url, token, OFFICE_LIGHT, 2700)
    if ct_after is not None and abs(ct_after - target) <= CT_TOL_K:
        log("ok", name, f"slider {target}K -> classifier {target}K -> bulb {ct_after}K (was {ct_before}K)")
        return True
    log("not_ok", name,
        f"slider {target}K but bulb at {ct_after}K (was {ct_before}K) — Hue Bridge drop? AL re-fired?")
    return False


def case_al_vs_classifier(url, token, log):
    """Diagnose: is the bulb's CT being driven by AL or by Living
    Lights' classifier? If they diverge, AL is winning, which is the
    architectural issue. Read-only, runs in any mode."""
    name = "AL target vs classifier prediction (diagnostic)"
    _, profile_attrs = get_state(url, token, "sensor.living_lights_profile")
    classifier_ct = read_office_pred_ct(url, token)
    light_ct = read_light_ct(url, token, OFFICE_LIGHT)
    _, al_attrs = get_state(url, token, "switch.home_adaptive_lighting_home")
    al_ct = to_int((al_attrs or {}).get("color_temp_kelvin"), None)
    tod_ct = to_int((profile_attrs or {}).get("tod_color_warm"), None)
    log("ok", name,
        f"ToD={tod_ct}K  classifier={classifier_ct}K  AL_target={al_ct}K  bulb={light_ct}K"
        + ("  ← bulb matches AL" if light_ct == al_ct else "")
        + ("  ← bulb matches classifier" if light_ct == classifier_ct and light_ct != al_ct else ""))
    return True


def case_persistence_roundtrip(url, token, log):
    """Set + read back: confirm HA actually persists the input_number value."""
    name = "input_number/set_value persists across read"
    test_value = 37
    ok = set_input_number(url, token, "input_number.living_lights_working_hours_floor_pct", test_value)
    if not ok:
        log("not_ok", name, "input_number/set_value returned non-200")
        return False
    time.sleep(0.5)
    state, _ = get_state(url, token, "input_number.living_lights_working_hours_floor_pct")
    set_input_number(url, token, "input_number.living_lights_working_hours_floor_pct", 80)
    if state and abs(float(state) - test_value) < 0.5:
        log("ok", name, f"wrote {test_value}, read back {state}")
        return True
    log("not_ok", name, f"wrote {test_value} but read back {state}")
    return False


def case_helper_inventory(url, token, log):
    """All 17 expected input_numbers + 1 input_text helper exist."""
    name = "all 17 input_numbers + 1 input_text exist"
    expected = [
        ("input_number", "living_lights_working_hours_floor_pct"),
        ("input_number", "living_lights_working_hours_present_pct"),
        ("input_number", "living_lights_warmth_bias_k"),
        ("input_number", "living_lights_brightness_bias_pp"),
        ("input_number", "living_lights_vacant_day_pct"),
        ("input_number", "living_lights_vacant_night_pct"),
        ("input_number", "living_lights_ramp_target_pct"),
        ("input_number", "living_lights_ramp_initial_pct"),
        ("input_number", "living_lights_asleep_cap_pct"),
        ("input_number", "living_lights_movie_dim_pct"),
        ("input_number", "living_lights_gaming_dim_pct"),
        ("input_number", "living_lights_ct_overnight_k"),
        ("input_number", "living_lights_ct_morning_k"),
        ("input_number", "living_lights_ct_midday_k"),
        ("input_number", "living_lights_ct_afternoon_k"),
        ("input_number", "living_lights_ct_evening_k"),
        ("input_number", "living_lights_ct_late_evening_k"),
        ("input_number", "living_lights_tod_factor_morning_mult"),
        ("input_number", "living_lights_tod_factor_midday_mult"),
        ("input_number", "living_lights_asleep_ct_k"),
        ("input_number", "living_lights_gaming_ct_k"),
        ("input_number", "living_lights_working_hours_ct_k"),
        ("input_number", "living_lights_movie_ct_k"),
        ("input_text", "living_lights_bias_zone_scope"),
    ]
    missing = []
    for domain, slug in expected:
        state, _ = get_state(url, token, f"{domain}.{slug}")
        if state is None:
            missing.append(f"{domain}.{slug}")
    if missing:
        log("not_ok", name, f"missing: {', '.join(missing)}")
        return False
    log("ok", name, f"all {len(expected)} helpers exist")
    return True


# ── Runner ──────────────────────────────────────────────────────────


class Reporter:
    def __init__(self):
        self.ok = 0
        self.not_ok = 0
        self.skip = 0
        self.rows = []

    def log(self, status, name, detail=""):
        self.rows.append((status, name, detail))
        if status == "ok":
            self.ok += 1
            mark = "✓"
        elif status == "skip":
            self.skip += 1
            mark = "—"
        else:
            self.not_ok += 1
            mark = "✗"
        # ASCII fallback for Windows consoles
        try:
            print(f"{mark}  {status:7s} {name:55s} {detail}")
        except UnicodeEncodeError:
            sys.stdout.reconfigure(encoding="utf-8")
            print(f"{mark}  {status:7s} {name:55s} {detail}")

    def summary(self):
        print()
        print(f"# summary: ok={self.ok}  not_ok={self.not_ok}  skip={self.skip}")
        return 0 if self.not_ok == 0 else 1


def resolve_token(args) -> Optional[str]:
    if args.token:
        return args.token
    if os.environ.get("HA_TOKEN"):
        return os.environ["HA_TOKEN"]
    if args.token_from_haos:
        try:
            result = subprocess.run(
                ["ssh", "-p", "22222", "-o", "BatchMode=yes",
                 "root@192.168.0.125", "cat /tmp/ha_token.txt"],
                capture_output=True, text=True, check=True, timeout=10,
            )
            return result.stdout.strip()
        except Exception as e:
            print(f"ERROR: could not fetch token from HAOS: {e}", file=sys.stderr)
            return None
    print("ERROR: --token required (or HA_TOKEN env, or --token-from-haos)", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=os.environ.get("HA_URL", DEFAULT_HA_URL),
                    help="HA base URL (default: $HA_URL or 192.168.0.125:8123)")
    ap.add_argument("--token", help="HA long-lived access token (or set HA_TOKEN env)")
    ap.add_argument("--token-from-haos", action="store_true",
                    help="ssh-fetch token from /tmp/ha_token.txt on HAOS")
    ap.add_argument("--live", action="store_true",
                    help="run live state-mutating tests (default: read-only checks only)")
    ap.add_argument("--yes", action="store_true",
                    help="skip interactive confirmation for --live")
    args = ap.parse_args()

    token = resolve_token(args)
    if not token:
        return 2
    url = args.url.rstrip("/")
    print(f"# probe-lights-drawer — url={url} | token=<{len(token)} chars, redacted>")
    print(f"# mode: {'LIVE (mutates state)' if args.live else 'READ-ONLY'}")

    if args.live and not args.yes:
        if not sys.stdin.isatty():
            print("ERROR: --live requires --yes when not running interactively", file=sys.stderr)
            return 2
        try:
            ans = input("Live mode will briefly change input_numbers + may change light state. Continue? [y/N] ")
        except KeyboardInterrupt:
            return 2
        if ans.lower() not in ("y", "yes"):
            return 2

    reporter = Reporter()
    log = reporter.log

    # Always-run read-only checks
    case_helper_inventory(url, token, log)
    case_al_vs_classifier(url, token, log)

    if not args.live:
        print("# (skipping mutating cases; use --live --yes to run them)")
        return reporter.summary()

    # Mutating scenario tests
    case_persistence_roundtrip(url, token, log)
    case_working_hours_floor(url, token, log)
    case_working_hours_present(url, token, log)
    case_vacant_day_pct(url, token, log)
    case_working_hours_ct_override(url, token, log)
    case_warmth_bias(url, token, log)
    case_brightness_bias_per_zone(url, token, log)
    case_actual_light_follows_classifier(url, token, log)
    case_bulb_ct_direct_push(url, token, log)
    case_ct_slider_drives_actual_bulb(url, token, log)

    return reporter.summary()


if __name__ == "__main__":
    raise SystemExit(main())
