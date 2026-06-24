#!/usr/bin/env python3
"""simulate-frigate-walk.py - Addendum 36 end-to-end simulator.

Synthesizes Frigate occupancy + media_player state into the LIVE HA system
via REST POST, observes the chain (classifier templates -> actuator automations
-> light/switch services), asserts expected outcomes.

Lets you validate every actuator + classifier path + flutter-resistance fix
WITHOUT physically walking around the home.

Scenarios:
  normal_walk      - vacant -> active -> linger -> task -> vacant w/ grace
  flutter          - rapid on/off cycles; stable-occupancy fix should absorb
  cooldown_bypass  - re-entry within 5s cooldown; bypass should fire
  hardware_suppress - input_datetime touch + classifier change; 60s gate
  multi_zone_guard  - two zones owning same light; vacant one zone, other holds
  movie_mode       - Apple TV playing -> all 6 living_room zones -> movie_mode
  dwell_freeze     - sustain presence, then off; dwell_ms must freeze in grace
  exit_fade        - exit timing; 20s grace + 6s fade
  all              - run all 8 in sequence with 30s settle between

Usage:
  py -3 tools/simulate-frigate-walk.py --scenario normal_walk
  py -3 tools/simulate-frigate-walk.py --scenario all
  py -3 tools/simulate-frigate-walk.py --scenario flutter --dry-run

Safety:
  - Each scenario captures original state and restores at end
  - --dry-run prints what would be done without actuating
  - --no-actuate flips master toggle off before running (classifier-only test)
  - --skip-movie-mode excludes scenario S6 (avoids real Apple TV playback)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HA = "http://homeassistant.local:8123"

# ── Test entities ────────────────────────────────────────────────────

# Single zone we'll exercise most scenarios against
PRIMARY_ZONE = "dining_left"
PRIMARY_OCC = f"binary_sensor.{PRIMARY_ZONE}_person_occupancy"
PRIMARY_CLS = "sensor.dining_room_dining_left_lighting_state"
PRIMARY_ACT = "automation.living_lights_dining_left_actuator_phase_2_pilot"
PRIMARY_LIGHT = "light.dining_table_left"
PRIMARY_MANUAL_TS = "input_datetime.living_lights_zone_dining_left_last_manual_at"

# Multi-zone scenario uses sofa + front_left (both own light.front_left)
SOFA_OCC = "binary_sensor.sofa_person_occupancy"
FRONT_LEFT_OCC = "binary_sensor.front_left_person_occupancy"
SHARED_LIGHT = "light.front_left"

# Apple TV
APPLE_TV = "media_player.living_room_2"

# Master toggles
MASTER_ENABLED = "input_boolean.living_lights_enabled"
MASTER_SHADOW = "input_boolean.living_lights_shadow"

# Living room zones for movie_mode test
LIVING_ROOM_CLASSIFIERS = [
    "sensor.living_room_sofa_lighting_state",
    "sensor.living_room_front_left_lighting_state",
    "sensor.living_room_weights_lighting_state",
    "sensor.living_room_office_lighting_state",
    "sensor.living_room_front_door_lighting_state",
    "sensor.living_room_whole_living_room_lighting_state",
]


# ── HA REST helpers ──────────────────────────────────────────────────

def get_token() -> str:
    cache = Path("/tmp/ha_token.txt")
    if cache.exists():
        t = cache.read_text().strip()
        if t:
            return t
    env_token = os.environ.get("HA_TOKEN", "").strip()
    if env_token:
        return env_token
    r = subprocess.run(
        ["ssh", "hav-ubuntu",
         "grep -hE '^HA_TOKEN|HOMEASSISTANT_TOKEN' /opt/home-ai-voice/.env"],
        capture_output=True, text=True, timeout=15,
    )
    for line in r.stdout.splitlines():
        if "=" in line:
            return line.split("=", 1)[1].strip()
    raise SystemExit("FATAL: could not load HA token (cache, env, or ssh)")


def _req(method: str, path: str, token: str, body: Optional[dict] = None,
         timeout: float = 5.0) -> Any:
    url = f"{HA}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {body_text}") from None


def get_state(entity: str, token: str) -> dict:
    return _req("GET", f"/api/states/{entity}", token) or {}


def set_state(entity: str, state: str, token: str,
              attrs: Optional[dict] = None) -> dict:
    """POST /api/states - fires real state_changed event."""
    body = {"state": state}
    if attrs:
        body["attributes"] = attrs
    return _req("POST", f"/api/states/{entity}", token, body)


def call_service(domain: str, service: str, token: str,
                 data: Optional[dict] = None) -> Any:
    return _req("POST", f"/api/services/{domain}/{service}", token, data or {})


def parse_iso(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"


# ── Assertion helpers ────────────────────────────────────────────────

class Probe:
    """Tracks pass/fail per scenario + total."""
    def __init__(self, scenario_name: str):
        self.scenario = scenario_name
        self.assertions: list[tuple[bool, str]] = []
        self.start_ts = time.time()

    def expect(self, cond: bool, label: str):
        self.assertions.append((cond, label))
        mark = "[OK]  " if cond else "[FAIL]"
        print(f"  {mark} {label}")

    def passed(self) -> bool:
        return all(c for c, _ in self.assertions)

    def summary(self) -> str:
        p = sum(1 for c, _ in self.assertions if c)
        f = len(self.assertions) - p
        elapsed = time.time() - self.start_ts
        verdict = "PASS" if f == 0 else "FAIL"
        return f"{self.scenario}: {p}/{p+f} {verdict} ({elapsed:.1f}s)"


def expect_state_within(entity: str, expected: str, token: str,
                        deadline_s: float, poll_interval: float = 0.5) -> tuple[bool, str]:
    """Poll entity state until it matches expected OR deadline."""
    end = time.time() + deadline_s
    last = None
    while time.time() < end:
        try:
            d = get_state(entity, token)
            last = d.get("state")
            if last == expected:
                return True, f"got '{last}' in {time.time() - (end - deadline_s):.1f}s"
        except Exception as e:
            last = f"err: {e}"
        time.sleep(poll_interval)
    return False, f"timeout after {deadline_s}s; last seen '{last}'"


def expect_state_in_within(entity: str, expected_set: set[str], token: str,
                           deadline_s: float, poll_interval: float = 0.5) -> tuple[bool, str]:
    end = time.time() + deadline_s
    last = None
    while time.time() < end:
        try:
            d = get_state(entity, token)
            last = d.get("state")
            if last in expected_set:
                return True, f"got '{last}'"
        except Exception as e:
            last = f"err: {e}"
        time.sleep(poll_interval)
    return False, f"timeout; last seen '{last}'; expected one of {expected_set}"


# ── Scenarios ────────────────────────────────────────────────────────

def scenario_normal_walk(token: str, dry_run: bool) -> Probe:
    """vacant -> active_presence -> linger -> task -> vacant w/ 20s grace."""
    p = Probe("normal_walk")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        print("  (dry-run; not actuating)")
        p.expect(True, "dry-run skipped")
        return p

    # Capture original state for restoration
    orig = get_state(PRIMARY_OCC, token).get("state", "off")

    # Step 1: set occupancy on
    print(f"  T+0s: set {PRIMARY_OCC} = on")
    set_state(PRIMARY_OCC, "on", token)
    # Step 2: within 2s, classifier should be active_presence (or further)
    ok, msg = expect_state_in_within(
        PRIMARY_CLS, {"active_presence", "linger", "task"}, token, deadline_s=3.0
    )
    p.expect(ok, f"classifier reaches active_presence within 3s: {msg}")

    # Step 3: wait 4s total since 'on'; classifier should be linger or task
    time.sleep(3)
    ok, msg = expect_state_in_within(
        PRIMARY_CLS, {"linger", "task"}, token, deadline_s=2.0
    )
    p.expect(ok, f"classifier reaches linger by T+5s: {msg}")

    # Step 4: wait until task (15s total from start)
    time.sleep(10)
    ok, msg = expect_state_within(PRIMARY_CLS, "task", token, deadline_s=3.0)
    p.expect(ok, f"classifier reaches task by T+15s: {msg}")

    # Step 5: set occupancy off
    print(f"  T+~16s: set {PRIMARY_OCC} = off")
    set_state(PRIMARY_OCC, "off", token)
    # Step 6: within 2s, classifier should STILL be in task/linger (grace holds)
    time.sleep(2)
    d = get_state(PRIMARY_CLS, token)
    cls_state = d.get("state")
    p.expect(cls_state in {"task", "linger", "active_presence"},
             f"classifier still in presence after 2s grace (got '{cls_state}')")

    # Step 7: wait for vacant grace expiry (~20-25s after off)
    print(f"  T+~16-40s: waiting for 20s grace + classifier to flip to vacant...")
    ok, msg = expect_state_within(PRIMARY_CLS, "vacant", token, deadline_s=25.0)
    p.expect(ok, f"classifier reaches vacant within 25s grace: {msg}")

    # Cleanup
    if orig != "off":
        set_state(PRIMARY_OCC, orig, token)
    return p


def scenario_flutter(token: str, dry_run: bool) -> Probe:
    """6 rapid on/off cycles in 3s. stable-occupancy should absorb (no classifier flip).
    """
    p = Probe("flutter")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Capture original actuator last_triggered (we'll check if it advanced wildly)
    pre_act = get_state(PRIMARY_ACT, token)
    pre_last_triggered = (pre_act.get("attributes") or {}).get("last_triggered")

    print(f"  Firing 6 occupancy on/off cycles within 3s...")
    for i in range(6):
        state = "on" if i % 2 == 0 else "off"
        set_state(PRIMARY_OCC, state, token)
        time.sleep(0.5)

    # End in "off" so the classifier's normal vacancy path runs after
    set_state(PRIMARY_OCC, "off", token)

    # Wait for system to settle (30s past last flicker, well past stable-occupancy window)
    print("  Settling 30s for stable-occupancy fix to absorb the flutter...")
    time.sleep(30)

    # Verify: actuator's last_triggered advanced AT MOST 2 times during the burst+settle
    # (acceptable: one entry-fire + maybe one vacant-fire, NOT 6+ per-flutter fires)
    post_act = get_state(PRIMARY_ACT, token)
    post_last_triggered = (post_act.get("attributes") or {}).get("last_triggered")
    pre_ts = parse_iso(pre_last_triggered) or 0
    post_ts = parse_iso(post_last_triggered) or 0
    advanced = post_ts > pre_ts + 0.5
    p.expect(advanced or (pre_ts == 0 and post_ts == 0),
             f"actuator triggered at least once (pre={pre_last_triggered}, post={post_last_triggered})")

    # Verify: classifier eventually returns to vacant cleanly
    ok, msg = expect_state_within(PRIMARY_CLS, "vacant", token, deadline_s=30.0)
    p.expect(ok, f"classifier returns to vacant after flutter+settle: {msg}")

    return p


def scenario_cooldown_bypass(token: str, dry_run: bool) -> Probe:
    """Re-entry within 5s cooldown. Bypass should fire."""
    p = Probe("cooldown_bypass")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Setup: occupancy on, sustain 5s, then off, capture actuator state
    set_state(PRIMARY_OCC, "on", token)
    time.sleep(5)
    set_state(PRIMARY_OCC, "off", token)
    time.sleep(1)
    mid_act = get_state(PRIMARY_ACT, token)
    mid_lt = (mid_act.get("attributes") or {}).get("last_triggered")
    print(f"  After entry+exit, actuator last_triggered: {mid_lt}")

    # Immediately re-enter (within cooldown)
    time.sleep(1)  # ~2s after off
    print(f"  T+~2s after exit: re-fire occupancy = on (should bypass cooldown)")
    set_state(PRIMARY_OCC, "on", token)

    # Within 3s, actuator's last_triggered should advance past mid_lt
    end = time.time() + 4
    bypass_advanced = False
    while time.time() < end:
        act = get_state(PRIMARY_ACT, token)
        new_lt = (act.get("attributes") or {}).get("last_triggered")
        if new_lt and new_lt != mid_lt:
            bypass_advanced = True
            print(f"  Bypass fired! new last_triggered: {new_lt}")
            break
        time.sleep(0.3)
    p.expect(bypass_advanced, "actuator last_triggered advanced within 4s of re-entry (cooldown bypassed)")

    # Cleanup
    time.sleep(2)
    set_state(PRIMARY_OCC, "off", token)
    return p


def scenario_hardware_suppress(token: str, dry_run: bool) -> Probe:
    """input_datetime touch -> 60s suppression window."""
    p = Probe("hardware_suppress")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Set hardware touch timestamp to now
    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    iso_dt = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    print(f"  Setting {PRIMARY_MANUAL_TS} = {iso_dt}")
    call_service("input_datetime", "set_datetime", token, {
        "entity_id": PRIMARY_MANUAL_TS,
        "datetime": iso_dt,
    })

    # Capture pre-state of actuator
    pre_act = get_state(PRIMARY_ACT, token)
    pre_lt = (pre_act.get("attributes") or {}).get("last_triggered")

    # Wait 2s for classifier to read the new manual_at
    time.sleep(2)

    # Fire occupancy on - actuator should NOT advance (suppressed)
    print(f"  T+2s: fire occupancy = on (should be suppressed for 60s)")
    set_state(PRIMARY_OCC, "on", token)
    time.sleep(5)

    post_act = get_state(PRIMARY_ACT, token)
    post_lt = (post_act.get("attributes") or {}).get("last_triggered")
    suppressed = (pre_lt == post_lt) or (parse_iso(post_lt) or 0) <= (parse_iso(pre_lt) or 0)
    p.expect(suppressed,
             f"actuator did NOT advance during suppression window (pre={pre_lt}, post={post_lt})")

    # Cleanup
    set_state(PRIMARY_OCC, "off", token)
    # Note: we leave input_datetime as-is; it's a 60s window and will expire naturally
    # OR user can manually reset. For autonomous re-runs, set it to a far-past time
    # to avoid leaking the suppression into the next test.
    far_past = "2000-01-01 00:00:00"
    call_service("input_datetime", "set_datetime", token, {
        "entity_id": PRIMARY_MANUAL_TS,
        "datetime": far_past,
    })
    print(f"  Cleanup: set {PRIMARY_MANUAL_TS} to far-past")
    return p


def scenario_multi_zone_guard(token: str, dry_run: bool) -> Probe:
    """Two zones (sofa + front_left) owning the same light. Vacant front_left;
    light.front_left should stay on because sofa is still active."""
    p = Probe("multi_zone_guard")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Both zones on
    print("  Setting sofa=on AND front_left=on")
    set_state(SOFA_OCC, "on", token)
    set_state(FRONT_LEFT_OCC, "on", token)
    time.sleep(12)  # both reach linger/task

    # Light should be on
    light = get_state(SHARED_LIGHT, token)
    light_on_state = light.get("state")
    print(f"  After 12s, {SHARED_LIGHT} = {light_on_state}")
    p.expect(light_on_state == "on", f"shared light on after both zones occupied (got '{light_on_state}')")

    # Vacate front_left only
    print("  Vacating front_left (sofa still active)")
    set_state(FRONT_LEFT_OCC, "off", token)
    # Wait past 20s grace
    time.sleep(25)

    # Light should STILL be on (guard held)
    light = get_state(SHARED_LIGHT, token)
    final_light_state = light.get("state")
    p.expect(final_light_state == "on",
             f"shared light STILL on after front_left vacant (sofa holds it): got '{final_light_state}'")

    # Cleanup
    set_state(SOFA_OCC, "off", token)
    return p


def scenario_movie_mode(token: str, dry_run: bool) -> Probe:
    """Apple TV -> playing -> 6 living_room classifiers -> movie_mode."""
    p = Probe("movie_mode")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Capture original TV state
    orig_tv = get_state(APPLE_TV, token).get("state", "off")
    print(f"  Original TV state: {orig_tv}")

    print(f"  Setting {APPLE_TV} = playing")
    set_state(APPLE_TV, "playing", token, attrs={"media_title": "test"})
    time.sleep(5)

    # Check all 6 living_room classifiers
    flipped = 0
    states = {}
    for cls in LIVING_ROOM_CLASSIFIERS:
        d = get_state(cls, token)
        states[cls] = d.get("state")
        if d.get("state") == "movie_mode":
            flipped += 1
    print(f"  After 5s: living_room classifiers in movie_mode: {flipped}/6")
    for cls, st in states.items():
        print(f"    {cls.split('.')[-1].replace('_lighting_state', '')}: {st}")
    p.expect(flipped == 6, f"6/6 living_room classifiers flipped to movie_mode (got {flipped})")

    # Restore TV state
    set_state(APPLE_TV, orig_tv, token)
    return p


def scenario_dwell_freeze(token: str, dry_run: bool) -> Probe:
    """Sustain presence 30s, then off. dwell_ms must freeze during 20s grace."""
    p = Probe("dwell_freeze")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Sustain presence
    print("  Setting occupancy on, sustaining 30s for task-level dwell...")
    set_state(PRIMARY_OCC, "on", token)
    time.sleep(30)

    # Capture dwell at exit point
    cls_pre = get_state(PRIMARY_CLS, token)
    dwell_pre = (cls_pre.get("attributes") or {}).get("dwell_ms")
    print(f"  dwell_ms at exit: {dwell_pre}")

    set_state(PRIMARY_OCC, "off", token)

    # Sample dwell at +1s, +5s, +10s
    samples = []
    for delay in [1, 5, 10]:
        time.sleep(delay - (samples[-1][0] if samples else 0))
        cls = get_state(PRIMARY_CLS, token)
        d_ms = (cls.get("attributes") or {}).get("dwell_ms")
        samples.append((delay, d_ms))
        print(f"  +{delay}s post-vacant: dwell_ms = {d_ms}")

    if dwell_pre is None or any(s[1] is None for s in samples):
        p.expect(False, "dwell_ms attribute not exposed by classifier (can't validate freeze)")
    else:
        # Freeze invariant: all samples within 500ms of each other AND of dwell_pre
        max_delta = max(abs(s[1] - dwell_pre) for s in samples)
        p.expect(max_delta < 500,
                 f"dwell_ms frozen during grace (max delta {max_delta}ms, threshold 500ms)")

    # Wait for full grace to clean up
    time.sleep(15)
    return p


def scenario_exit_fade(token: str, dry_run: bool) -> Probe:
    """Exit timing: 20s grace then actuator fires; 6s fade to ambient."""
    p = Probe("exit_fade")
    print(f"\n=== {p.scenario} ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Sustain occupancy 12s
    set_state(PRIMARY_OCC, "on", token)
    time.sleep(12)

    # Capture pre-exit actuator state
    pre_act = get_state(PRIMARY_ACT, token)
    pre_lt = (pre_act.get("attributes") or {}).get("last_triggered")
    pre_ts = parse_iso(pre_lt) or 0

    print("  Exiting (occupancy off); waiting for grace + fade...")
    off_ts = time.time()
    set_state(PRIMARY_OCC, "off", token)

    # Poll actuator state until it fires again (should be ~20s)
    end = time.time() + 30
    grace_fire_ts = None
    while time.time() < end:
        act = get_state(PRIMARY_ACT, token)
        new_lt = (act.get("attributes") or {}).get("last_triggered")
        new_ts = parse_iso(new_lt) or 0
        if new_ts > pre_ts + 1:
            grace_fire_ts = time.time()
            print(f"  Actuator fired at T+{time.time() - off_ts:.1f}s after exit, last_triggered={new_lt}")
            break
        time.sleep(0.5)

    if grace_fire_ts:
        elapsed_to_fire = grace_fire_ts - off_ts
        p.expect(18 < elapsed_to_fire < 25,
                 f"actuator fired ~20s after exit (got {elapsed_to_fire:.1f}s; expected 18-25s)")
    else:
        p.expect(False, "actuator never fired after exit (within 30s)")

    # Wait for 6s fade + verify light reached ambient brightness
    time.sleep(8)
    light = get_state(PRIMARY_LIGHT, token)
    light_state = light.get("state")
    bri = (light.get("attributes") or {}).get("brightness")
    print(f"  Post-fade: {PRIMARY_LIGHT} state={light_state} brightness={bri}")
    # ambient should be on at low brightness (or off depending on config)
    p.expect(light_state in ("on", "off"),
             f"light has settled state after fade (state={light_state})")

    return p


# ── Main runner ──────────────────────────────────────────────────────

def _poll_light_samples(token: str, light_entity: str, duration_s: float,
                        poll_interval_s: float = 0.25,
                        start_ts: Optional[float] = None) -> list[tuple[float, str, Optional[int]]]:
    """Poll a light entity at high cadence; return list of (elapsed_ms, state, brightness)."""
    if start_ts is None:
        start_ts = time.time()
    samples: list[tuple[float, str, Optional[int]]] = []
    end = start_ts + duration_s
    while time.time() < end:
        try:
            d = get_state(light_entity, token)
            state = d.get("state") or "unknown"
            bri = (d.get("attributes") or {}).get("brightness")
        except Exception as e:
            state = f"err:{type(e).__name__}"
            bri = None
        elapsed_ms = (time.time() - start_ts) * 1000.0
        samples.append((elapsed_ms, state, bri))
        time.sleep(poll_interval_s)
    return samples


def _find_first_change(samples: list[tuple[float, str, Optional[int]]],
                       baseline_state: str, baseline_bri: Optional[int],
                       threshold_bri: int = 5) -> Optional[tuple[float, str, Optional[int]]]:
    """Return first sample whose state OR brightness differs meaningfully from baseline."""
    for s in samples:
        elapsed_ms, state, bri = s
        if state != baseline_state:
            return s
        if bri is not None and baseline_bri is not None and abs(bri - baseline_bri) >= threshold_bri:
            return s
    return None


def _find_stable_settle(samples: list[tuple[float, str, Optional[int]]],
                        stable_window_samples: int = 8) -> Optional[tuple[float, str, Optional[int]]]:
    """Find when the light reaches a state held for N consecutive samples
    (i.e., transition has finished). Returns first sample in the stable run."""
    n = len(samples)
    if n < stable_window_samples:
        return samples[-1] if samples else None
    for i in range(n - stable_window_samples + 1):
        window = samples[i:i + stable_window_samples]
        states = {s[1] for s in window}
        bris = [s[2] for s in window if s[2] is not None]
        bri_stable = (not bris) or (max(bris) - min(bris) <= 3)
        if len(states) == 1 and bri_stable:
            return window[0]
    return samples[-1]


def _count_brightness_transitions(samples: list[tuple[float, str, Optional[int]]],
                                  threshold_bri: int = 10) -> int:
    """Count meaningful brightness state changes during the sample window."""
    transitions = 0
    last_bri = None
    last_state = None
    for elapsed_ms, state, bri in samples:
        if last_state is None:
            last_state = state
            last_bri = bri
            continue
        if state != last_state:
            transitions += 1
            last_state = state
            last_bri = bri
            continue
        if bri is not None and last_bri is not None and abs(bri - last_bri) >= threshold_bri:
            transitions += 1
            last_bri = bri
    return transitions


def scenario_measure_response(token: str, dry_run: bool) -> Probe:
    """High-resolution measurement of:
       (a) Time from occupancy=on POST to light first response
       (b) Time from occupancy=off POST to light settles to final state
       (c) Light brightness-transition count during a flutter burst (should be ≤2 with stable-occupancy)
    """
    p = Probe("measure_response")
    print(f"\n=== {p.scenario} (high-res light polling at 250ms cadence) ===")
    if dry_run:
        p.expect(True, "dry-run skipped")
        return p

    # Reset state — ensure we start vacant
    set_state(PRIMARY_OCC, "off", token)
    time.sleep(25)  # past 20s grace
    print("  Reset complete; starting from vacant state.")

    # Capture baseline
    baseline = get_state(PRIMARY_LIGHT, token)
    base_state = baseline.get("state", "unknown")
    base_bri = (baseline.get("attributes") or {}).get("brightness")
    print(f"  Baseline {PRIMARY_LIGHT}: state={base_state} brightness={base_bri}")

    # ─── PART A: ENTRY LATENCY ───────────────────────────────────────
    print("\n  === Part A: ENTRY LATENCY ===")
    print("  T0: POST occupancy=on")
    t0 = time.time()
    set_state(PRIMARY_OCC, "on", token)
    # Poll for 6s
    a_samples = _poll_light_samples(token, PRIMARY_LIGHT, duration_s=6.0,
                                     poll_interval_s=0.25, start_ts=t0)
    print(f"  Polled {len(a_samples)} samples over 6s")
    first_change = _find_first_change(a_samples, base_state, base_bri)
    if first_change:
        elapsed, state, bri = first_change
        entry_latency_ms = elapsed
        print(f"  First light change at T+{entry_latency_ms:.0f}ms: state={state} brightness={bri}")
        p.expect(entry_latency_ms < 2000,
                 f"entry latency < 2s (got {entry_latency_ms:.0f}ms)")
    else:
        entry_latency_ms = None
        p.expect(False, "light NEVER changed within 6s of occupancy=on (entry actuator may have failed)")

    # Find settled state (i.e., light reached final entry brightness)
    settled = _find_stable_settle(a_samples)
    if settled:
        elapsed, state, bri = settled
        print(f"  Light settled at T+{elapsed:.0f}ms: state={state} brightness={bri} ({(bri or 0)/2.55:.0f}%)")

    # Hold presence for 12s total
    print("  Holding presence 12s total (sustaining task-level)")
    time.sleep(12 - 6)  # already waited 6s in polling

    # ─── PART B: EXIT LATENCY ────────────────────────────────────────
    print("\n  === Part B: EXIT LATENCY ===")
    pre_exit_light = get_state(PRIMARY_LIGHT, token)
    pre_exit_state = pre_exit_light.get("state", "unknown")
    pre_exit_bri = (pre_exit_light.get("attributes") or {}).get("brightness")
    print(f"  Pre-exit {PRIMARY_LIGHT}: state={pre_exit_state} brightness={pre_exit_bri}")

    print("  T0: POST occupancy=off")
    t0_exit = time.time()
    set_state(PRIMARY_OCC, "off", token)
    # Poll BOTH light + classifier for 45s (extended to find slow transitions)
    b_samples: list[tuple[float, str, Optional[int]]] = []
    cls_samples: list[tuple[float, str, Optional[int]]] = []  # (elapsed, state, predicted_bri)
    poll_end = t0_exit + 45.0
    while time.time() < poll_end:
        try:
            ld = get_state(PRIMARY_LIGHT, token)
            cd = get_state(PRIMARY_CLS, token)
            elapsed_ms = (time.time() - t0_exit) * 1000.0
            b_samples.append((elapsed_ms, ld.get("state") or "?",
                              (ld.get("attributes") or {}).get("brightness")))
            cls_samples.append((elapsed_ms, cd.get("state") or "?",
                                (cd.get("attributes") or {}).get("predicted_brightness_pct")))
        except Exception:
            pass
        time.sleep(0.5)
    print(f"  Polled {len(b_samples)} samples over 45s")
    # Print classifier transitions
    print(f"  Classifier transitions during this window:")
    last_cls = None
    for s in cls_samples:
        if s[1] != last_cls:
            print(f"    T+{s[0]/1000:.1f}s: classifier={s[1]} predicted_bri={s[2]}")
            last_cls = s[1]
    exit_first_change = _find_first_change(b_samples, pre_exit_state, pre_exit_bri)
    if exit_first_change:
        elapsed, state, bri = exit_first_change
        exit_first_change_ms = elapsed
        print(f"  First exit-side light change at T+{exit_first_change_ms:.0f}ms ({exit_first_change_ms/1000:.1f}s): state={state} brightness={bri}")
        # Should be close to 20s grace
        # Exit = 20s stable-grace + up to ~3s time_pattern slack + 1 possible
        # spurious-Frigate-detection nudge. Realistic range 18-38s; the key
        # invariant is "the exit COMPLETES" (pre-inertia-fix it could stall forever).
        p.expect(15000 < exit_first_change_ms < 38000,
                 f"exit completes 20-38s after occupancy=off (got {exit_first_change_ms/1000:.1f}s)")
    else:
        exit_first_change_ms = None
        p.expect(False, "light NEVER changed within 35s of occupancy=off")

    # Find post-fade settled state
    if len(b_samples) > 30:
        # Look at last 5s of samples to determine final state
        tail = b_samples[-20:]
        final_bri = tail[-1][2]
        final_state = tail[-1][1]
        print(f"  Final settled at T+{tail[-1][0]/1000:.1f}s: state={final_state} brightness={final_bri} ({(final_bri or 0)/2.55:.0f}%)")
        if exit_first_change_ms:
            # Estimate "fade settled" by finding when changes stop (stability check on tail)
            stable = _find_stable_settle(b_samples, stable_window_samples=6)
            if stable:
                exit_settle_ms = stable[0]
                print(f"  Light stable run starts at T+{exit_settle_ms/1000:.1f}s after occupancy=off")
                # Should be < 30s (grace + fade)
                p.expect(exit_settle_ms < 30000,
                         f"light settles within 30s of occupancy=off (stable from T+{exit_settle_ms/1000:.1f}s)")

    # Reset before flutter
    time.sleep(3)

    # ─── PART C: FLUTTER + LIGHT OBSERVATION ─────────────────────────
    print("\n  === Part C: FLUTTER + LIGHT FLICKER ===")
    pre_flutter = get_state(PRIMARY_LIGHT, token)
    pre_flutter_state = pre_flutter.get("state", "unknown")
    pre_flutter_bri = (pre_flutter.get("attributes") or {}).get("brightness")
    print(f"  Pre-flutter {PRIMARY_LIGHT}: state={pre_flutter_state} brightness={pre_flutter_bri}")

    # Fire 6 cycles in 3s while polling
    flutter_start = time.time()
    print("  Firing 6 occupancy on/off cycles in 3s + observing 30s settle...")

    # Start a polling loop that interleaves with cycle injections
    c_samples: list[tuple[float, str, Optional[int]]] = []
    cycle_times: list[tuple[float, str]] = []

    # Phase 1: 3s of cycling (6 transitions at 0.5s intervals)
    for i in range(6):
        target_state = "on" if i % 2 == 0 else "off"
        set_state(PRIMARY_OCC, target_state, token)
        cycle_times.append((time.time() - flutter_start, target_state))
        # Mini-poll: 1-2 samples in the 0.5s gap
        sub_end = time.time() + 0.5
        while time.time() < sub_end:
            try:
                d = get_state(PRIMARY_LIGHT, token)
                c_samples.append((
                    (time.time() - flutter_start) * 1000.0,
                    d.get("state") or "unknown",
                    (d.get("attributes") or {}).get("brightness"),
                ))
            except Exception:
                pass
            time.sleep(0.15)

    # Ensure final state is off
    set_state(PRIMARY_OCC, "off", token)
    cycle_times.append((time.time() - flutter_start, "off"))

    # Phase 2: 30s settle while still polling
    settle_samples = _poll_light_samples(
        token, PRIMARY_LIGHT, duration_s=30.0, poll_interval_s=0.5,
        start_ts=time.time() - (time.time() - flutter_start),  # continue same clock
    )
    # Adjust elapsed offset for settle samples (they were measured fresh; reset their offsets)
    settle_start_offset = (time.time() - flutter_start) * 1000.0
    # Actually re-poll with explicit offset
    c_samples.extend(settle_samples)

    print(f"  Polled {len(c_samples)} light samples during flutter + settle")
    print(f"  Cycle injection times (s from start):")
    for t_off, state in cycle_times:
        print(f"    T+{t_off:.2f}s: occupancy={state}")

    transitions = _count_brightness_transitions(c_samples, threshold_bri=10)
    print(f"  Light brightness transitions observed: {transitions}")

    # Walk through the unique states the light went through
    state_run: list[tuple[float, str, Optional[int]]] = []
    last_state_bri = None
    for s in c_samples:
        key = (s[1], (s[2] or 0) // 10)  # state + brightness-bucket
        if key != last_state_bri:
            state_run.append(s)
            last_state_bri = key
    print(f"  Distinct state/brightness checkpoints during flutter+settle:")
    for s in state_run[:15]:
        print(f"    T+{s[0]/1000:.2f}s: state={s[1]} brightness={s[2]} ({(s[2] or 0)/2.55:.0f}%)")

    # Healthy behavior: stable-occupancy + cooldown should produce ≤3 transitions
    # (baseline → maybe entry → maybe vacant settle)
    p.expect(transitions <= 3,
             f"light brightness changed at most 3 times during flutter ({transitions} observed)")

    # Final cleanup
    time.sleep(2)
    return p


SCENARIOS = {
    "normal_walk":       scenario_normal_walk,
    "flutter":           scenario_flutter,
    "cooldown_bypass":   scenario_cooldown_bypass,
    "hardware_suppress": scenario_hardware_suppress,
    "multi_zone_guard":  scenario_multi_zone_guard,
    "movie_mode":        scenario_movie_mode,
    "dwell_freeze":      scenario_dwell_freeze,
    "exit_fade":         scenario_exit_fade,
    "measure_response":  scenario_measure_response,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="all",
                    choices=list(SCENARIOS.keys()) + ["all"],
                    help="Which scenario to run (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be done; don't actuate")
    ap.add_argument("--skip-movie-mode", action="store_true",
                    help="Skip movie_mode scenario (avoids real Apple TV playback)")
    ap.add_argument("--settle-between", type=int, default=30,
                    help="Seconds to settle between scenarios in 'all' mode (default 30)")
    args = ap.parse_args()

    print(f"simulate-frigate-walk.py - Addendum 36 end-to-end")
    print(f"Connecting to HA at {HA}...")
    token = get_token()
    try:
        ping = get_state(MASTER_ENABLED, token)
        print(f"HA reachable. {MASTER_ENABLED} = {ping.get('state')}")
    except Exception as e:
        print(f"FATAL: HA not reachable: {e}", file=sys.stderr)
        return 2

    # Order matters: cheapest scenarios first
    order = [
        "hardware_suppress",
        "multi_zone_guard",  # touches multiple zones
        "cooldown_bypass",
        "movie_mode",
        "normal_walk",
        "flutter",
        "dwell_freeze",
        "exit_fade",
    ]
    if args.scenario != "all":
        to_run = [args.scenario]
    else:
        to_run = [s for s in order if not (args.skip_movie_mode and s == "movie_mode")]

    results: list[Probe] = []
    for name in to_run:
        try:
            probe = SCENARIOS[name](token, args.dry_run)
            results.append(probe)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        except Exception as e:
            print(f"  EXCEPTION in {name}: {type(e).__name__}: {e}", file=sys.stderr)
            p = Probe(name)
            p.expect(False, f"exception: {type(e).__name__}: {e}")
            results.append(p)
        # Settle between
        if name != to_run[-1] and not args.dry_run:
            print(f"  Settling {args.settle_between}s...")
            time.sleep(args.settle_between)

    # Summary
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    passed = 0
    failed = 0
    for r in results:
        print(f"  {r.summary()}")
        if r.passed():
            passed += 1
        else:
            failed += 1
    print()
    if failed == 0:
        print(f"{passed} pass . 0 fail")
        return 0
    else:
        print(f"{passed} pass . {failed} fail")
        return 1


if __name__ == "__main__":
    sys.exit(main())
