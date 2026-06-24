#!/usr/bin/env python3
"""test-probe-lighting-latency.py - Addendum 35 Phase 5 self-test (AR35-11).

Validates inference logic, not event-capture. Constructs a Harness in-memory,
synthesizes state via direct method calls, asserts the right inference records
are emitted to the in-memory lists.

8 scenarios:
  S1: hardware-touch + later classifier transition -> suppression_skip (high or low confidence per AR35-1)
  S2: zone-A vacant + zone-B active, target unchanged -> guard_skip_inferred
  S3: actuator fires within cooldown window -> cooldown_bypass_fired
  S4: Apple TV -> playing -> MovieModeTrace opens; flips 6 zones; idle -> closes
  S5: frigate_event with entered_zones, then matching occupancy -> frigate_to_state_latency
  S6: mirror-pair divergence sustained ≥2s -> mirror_divergences entry
  S7: predicted-vs-actual delta - schedule sample, settle, sample
  S8: dwell-freeze - dwell_at_open=0 + dwell_at_close=2000 -> freeze_ok=false

Run: py -3 tools/test-probe-lighting-latency.py
"""
from __future__ import annotations

import os
import sys
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Add tools dir to sys.path so we can import the harness module by file path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Direct import of the harness - its module name is the file stem
import importlib.util
spec = importlib.util.spec_from_file_location("probe_harness", HERE / "probe-lighting-latency.py")
ph = importlib.util.module_from_spec(spec)
# Register BEFORE exec_module so dataclass decorator can resolve cls.__module__
sys.modules["probe_harness"] = ph
spec.loader.exec_module(ph)

# Pull names we need
Harness = ph.Harness
MovieModeTrace = ph.MovieModeTrace
ZoneTrace = ph.ZoneTrace
ZONES = ph.ZONES
OCCUPANCY_BY_SLUG = ph.OCCUPANCY_BY_SLUG
CLASSIFIER_BY_SLUG = ph.CLASSIFIER_BY_SLUG
ACTUATOR_BY_SLUG = ph.ACTUATOR_BY_SLUG
TARGETS_BY_SLUG = ph.TARGETS_BY_SLUG
TARGET_TO_SLUGS = ph.TARGET_TO_SLUGS
HARDWARE_TO_ZONES = ph.HARDWARE_TO_ZONES
APPLE_TV_ENTITY = ph.APPLE_TV_ENTITY
LIVING_ROOM_ZONES = ph.LIVING_ROOM_ZONES
SUPPRESSION_WINDOW_S = ph.SUPPRESSION_WINDOW_S
COOLDOWN_THRESHOLD_S = ph.COOLDOWN_THRESHOLD_S
MIRROR_PAIRS = ph.MIRROR_PAIRS
MIRROR_DIVERGE_THRESHOLD_S = ph.MIRROR_DIVERGE_THRESHOLD_S


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"


def make_harness() -> Harness:
    """Construct a Harness without invoking run() - skips file IO + WS connect."""
    h = Harness.__new__(Harness)
    Harness.__init__(h, token="test-token", duration_s=60, output_dir=Path(os.devnull).parent)
    # Replace jsonl_fh with an in-memory buffer so writes don't error
    h.jsonl_fh = io.StringIO()
    return h


def assert_(cond, label):
    if not cond:
        print(f"  [FAIL] {label}", file=sys.stderr)
        raise AssertionError(label)
    print(f"  [OK]   {label}")


def s1_suppression_skip():
    """Hardware-touch + classifier transition where actuator did NOT fire."""
    print("\n[S1] suppression_skip inference")
    h = make_harness()
    zone = "dining_left"
    now = time.time()
    # Step 1: simulate a hardware automation fire (Pilar kitchen affects dining_left)
    h._handle_per_zone_hardware_fire("automation.pilar_knob_kitchen", now)
    assert_(h.zone_last_hardware_fire_ts[zone] == now, "zone last_hw_fire_ts set")
    # Step 2: pre-load a transition history so AR35-1 counter-factual will mark high-conf
    # Inject one prior identical transition (BEFORE the hw_ts) where the actuator FIRED.
    h.zone_transition_actuation_log[zone].append({
        "ts": now - 1000,  # 17 min before now (within 1h)
        "from": "vacant", "to": "active_presence",
        "actuator_fired": True,
    })
    # Step 3: classifier transition with actuator NOT firing (suppression)
    h._check_suppression_skip(zone, "vacant", "active_presence", now + 5, actuator_fired_now=False)
    assert_(len(h.suppression_skips) == 1, "1 suppression_skip recorded")
    sk = h.suppression_skips[0]
    assert_(sk["zone"] == zone, "right zone")
    assert_(sk["confidence"] == "high", "high confidence (prior precedent)")
    # Step 4: outside suppression window -> no inference
    h2 = make_harness()
    h2._handle_per_zone_hardware_fire("automation.pilar_knob_kitchen", now)
    h2._check_suppression_skip(zone, "vacant", "active_presence", now + SUPPRESSION_WINDOW_S + 10, actuator_fired_now=False)
    assert_(len(h2.suppression_skips) == 0, "outside window: no skip recorded")


def s2_guard_skip():
    """Zone goes vacant; light unchanged 8s later; sibling zone non-vacant -> guard inference."""
    print("\n[S2] guard_skip_inferred (with noop disambiguation)")
    h = make_harness()
    vacated = "front_left"
    sibling = "sofa"  # shares light.front_left target
    target = "light.front_left"
    now = time.time()
    # Pre-load entity_last_state: target light on at 60%, classifier predicts vacant=8%
    h.entity_last_state[target] = {
        "state": "on", "attrs": {"brightness": 154}, "ts": now,
    }
    h.entity_last_state[CLASSIFIER_BY_SLUG[vacated]] = {
        "state": "linger", "attrs": {"predicted_brightness_pct": 8}, "ts": now,
    }
    # Sibling is active
    h.entity_last_state[OCCUPANCY_BY_SLUG[sibling]] = {
        "state": "on", "attrs": {}, "ts": now,
    }
    # Trigger schedule_guard_check
    h._schedule_guard_check(vacated, now)
    assert_(len(h.pending_guard_checks) >= 1, "guard check scheduled")
    # Simulate 8s elapsed; target state didn't change
    h.entity_last_state[target] = {
        "state": "on", "attrs": {"brightness": 154}, "ts": now + 8,
    }
    h._process_pending_guard_checks(now + 9)
    # Should have classified as guard
    guard_skips = [g for g in h.guard_skips if g["target"] == target]
    assert_(len(guard_skips) >= 1, "guard_skip recorded")
    assert_(guard_skips[0]["inference_kind"] == "guard", f"inference_kind=guard (got {guard_skips[0]['inference_kind']})")
    assert_(guard_skips[0]["blocking_zone"] == sibling, f"blocking_zone={sibling}")


def s2b_noop_disambiguation():
    """Zone goes vacant; light brightness ~= predicted-vacant; sibling non-vacant -> noop, NOT guard."""
    print("\n[S2b] guard_skip_inferred - noop branch (AR35-2)")
    h = make_harness()
    vacated = "front_left"
    sibling = "sofa"
    target = "light.front_left"
    now = time.time()
    # Light brightness already matches predicted-vacant of 25%
    bri_255 = int(25 * 2.55)  # ~63
    h.entity_last_state[target] = {
        "state": "on", "attrs": {"brightness": bri_255}, "ts": now,
    }
    h.entity_last_state[CLASSIFIER_BY_SLUG[vacated]] = {
        "state": "linger", "attrs": {"predicted_brightness_pct": 25}, "ts": now,
    }
    h.entity_last_state[OCCUPANCY_BY_SLUG[sibling]] = {
        "state": "on", "attrs": {}, "ts": now,
    }
    h._schedule_guard_check(vacated, now)
    # 8s elapsed; brightness unchanged
    h._process_pending_guard_checks(now + 9)
    guard_skips = [g for g in h.guard_skips if g["target"] == target]
    assert_(len(guard_skips) >= 1, "skip recorded")
    assert_(guard_skips[0]["inference_kind"] == "noop", f"inference_kind=noop (got {guard_skips[0]['inference_kind']})")


def s3_cooldown_bypass():
    """Actuator fires twice within 5s cooldown -> bypass_fired."""
    print("\n[S3] cooldown_bypass_fired")
    h = make_harness()
    zone = "dining_left"
    actuator = ACTUATOR_BY_SLUG[zone]
    now = time.time()
    # First fire - sets last_triggered_seen
    iso1 = _iso(now)
    h._check_cooldown_bypass(zone, actuator, {"new_state": {"attributes": {"last_triggered": iso1}}}, now)
    # No bypass yet (no prior)
    assert_(len(h.cooldown_bypasses) == 0, "first fire: no bypass")
    # Second fire 2s later - within cooldown
    iso2 = _iso(now + 2)
    h._check_cooldown_bypass(zone, actuator, {"new_state": {"attributes": {"last_triggered": iso2}}}, now + 2)
    assert_(len(h.cooldown_bypasses) == 1, "second fire within 5s: bypass recorded")
    cb = h.cooldown_bypasses[0]
    assert_(cb["zone"] == zone, "right zone")
    assert_(1.5 < cb["cooldown_gap_s"] < 2.5, f"gap ~= 2s (got {cb['cooldown_gap_s']})")
    # Third fire 10s later - outside cooldown, NO new bypass
    iso3 = _iso(now + 12)
    h._check_cooldown_bypass(zone, actuator, {"new_state": {"attributes": {"last_triggered": iso3}}}, now + 12)
    assert_(len(h.cooldown_bypasses) == 1, "outside cooldown: no new bypass")


def s4_movie_mode_trace():
    """Apple TV -> playing -> trace opens; 6 LR zones flip to movie_mode; idle -> trace closes."""
    print("\n[S4] MovieModeTrace open/close + flip tracking")
    h = make_harness()
    now = time.time()
    # Step 1: Apple TV transitions to 'playing'
    h._handle_apple_tv({
        "old_state": {"state": "paused"},
        "new_state": {"state": "playing", "last_changed": _iso(now)},
    }, now)
    assert_(h.live_movie_trace is not None, "trace opened")
    # Step 2: each of the 6 living_room zones flips to movie_mode
    for slug in LIVING_ROOM_ZONES:
        h.last_classifier_state[slug] = "movie_mode"
        h._record_classifier_for_movie_trace(slug, "movie_mode", _iso(now + 1))
    assert_(len(h.live_movie_trace.zone_flip_ha_ts) >= 6, f"6 zones flipped (got {len(h.live_movie_trace.zone_flip_ha_ts)})")
    # Step 3: a kitchen zone tries to flip (false-positive guard)
    # Note: kitchen zones aren't expected to enter movie_mode
    h.last_classifier_state["dining_left"] = "movie_mode"
    h._record_classifier_for_movie_trace("dining_left", "movie_mode", _iso(now + 1))
    assert_("dining_left" in h.live_movie_trace.false_positive_flips, "false-positive recorded")
    # Step 4: Apple TV transitions to paused -> trace closes
    h._handle_apple_tv({
        "old_state": {"state": "playing"},
        "new_state": {"state": "paused", "last_changed": _iso(now + 30)},
    }, now + 30)
    assert_(h.live_movie_trace is None, "trace closed (live_movie_trace None)")
    assert_(len(h.closed_movie_traces) == 1, "1 closed trace")


def s5_frigate_event_latency():
    """frigate_event with entered_zones, then matching occupancy -> latency recorded."""
    print("\n[S5] frigate_event -> occupancy latency")
    h = make_harness()
    now = time.time()
    # frigate_event 200ms before binary_sensor occupancy fires
    h._handle_frigate_event(
        {"after": {"camera": "dining_room", "entered_zones": ["dining_left"]}},
        _iso(now), now,
    )
    assert_(len(h.frigate_event_log) == 1, "frigate_event logged")
    # Occupancy on at +200ms
    occ_ts = now + 0.200
    h._attach_frigate_latency_on_occupancy("dining_left", "on", _iso(occ_ts), occ_ts)
    latencies = h.frigate_event_to_state_latencies.get("dining_room") or []
    assert_(len(latencies) >= 1, "latency recorded")
    assert_(150 < latencies[0] < 350, f"latency ~= 200ms (got {latencies[0]}ms)")


def s6_mirror_divergence():
    """Light side ON + switch side OFF sustained ≥2s -> divergence recorded on resolve."""
    print("\n[S6] mirror divergence (AR35-13)")
    h = make_harness()
    now = time.time()
    light_ent = "light.ambient_light_left_mss110_main_channel"
    switch_ent = "switch.ambient_light_left_mss110_main_channel"
    # Both start on (aligned)
    h._check_mirror_divergence(light_ent, {"new_state": {"state": "on"}}, now)
    h._check_mirror_divergence(switch_ent, {"new_state": {"state": "on"}}, now + 0.05)
    # Switch flips off; light stays on (diverged)
    h._check_mirror_divergence(switch_ent, {"new_state": {"state": "off"}}, now + 1.0)
    # 3s later, switch flips back on (aligned again)
    h._check_mirror_divergence(switch_ent, {"new_state": {"state": "on"}}, now + 4.0)
    assert_(len(h.mirror_divergences) == 1, "divergence recorded on resolve")
    md = h.mirror_divergences[0]
    assert_(2.5 < md["duration_s"] < 3.5, f"duration ~= 3s (got {md['duration_s']})")


def s7_predicted_vs_actual_delta():
    """Schedule delta sample, advance time, settle -> delta written back on trace."""
    print("\n[S7] predicted-vs-actual delta sampling (AR35-7 settle)")
    h = make_harness()
    zone = "dining_left"
    target = "light.dining_table_left"
    now = time.time()
    trace = ZoneTrace(zone=zone, opened_ts=now, opened_ha_ts=_iso(now))
    trace.t1_predicted_bri = 80
    trace.t1_predicted_ct = 2700
    h.live_traces[zone] = trace
    # Set entity_last_state for the target (post-settle, the actual brightness lands)
    h.entity_last_state[target] = {
        "state": "on", "attrs": {"brightness": 204, "color_temp_kelvin": 2700}, "ts": now + 5,
    }
    # Schedule + advance time
    h._schedule_delta_sample(trace, target, now)
    assert_(len(h.pending_delta_samples) == 1, "1 sample pending")
    # Process at sample_ts (now + 5)
    h._process_pending_delta_samples(now + 6)
    assert_(target in trace.delta_settled, "delta written back to trace")
    ds = trace.delta_settled[target]
    # 204/255 ~= 80%; predicted=80 -> delta near 0
    assert_(abs(ds["actual_bri_pct"] - 80) < 1.0, f"actual_bri ~= 80% (got {ds['actual_bri_pct']})")


def s8_dwell_freeze():
    """ZoneTrace with dwell_at_open=0 + dwell_at_close=2000 -> freeze_ok=false."""
    print("\n[S8] dwell_freeze invariant (AR35-5)")
    h = make_harness()
    zone = "dining_left"
    now = time.time()
    classifier_ent = CLASSIFIER_BY_SLUG[zone]
    # Pre-load classifier state with dwell_ms = 0 (about to open)
    h.entity_last_state[classifier_ent] = {
        "state": "vacant", "attrs": {"dwell_ms": 0}, "ts": now,
    }
    # Open trace
    h._open_or_extend_trace(zone, {"new_state": {"last_changed": _iso(now)}}, now)
    trace = h.live_traces[zone]
    assert_(trace.dwell_at_open == 0, f"dwell_at_open=0 (got {trace.dwell_at_open})")
    # Now update classifier state with dwell_ms=2000 (sustained presence)
    h.entity_last_state[classifier_ent] = {
        "state": "linger", "attrs": {"dwell_ms": 2000}, "ts": now + 5,
    }
    # Close trace - dwell_at_close should snapshot 2000
    h._close_trace(zone, "occupancy_off", now + 5)
    assert_(len(h.dwell_freeze_results) >= 1, "freeze result recorded")
    r = h.dwell_freeze_results[-1]
    assert_(r["delta_ms"] == 2000, f"delta = 2000ms (got {r['delta_ms']})")
    assert_(r["freeze_ok"] is False, "freeze_ok=False (broken - delta > 200ms threshold)")
    # Inverse: small delta should pass
    h2 = make_harness()
    h2.entity_last_state[classifier_ent] = {
        "state": "vacant", "attrs": {"dwell_ms": 10000}, "ts": now,
    }
    h2._open_or_extend_trace(zone, {"new_state": {"last_changed": _iso(now)}}, now)
    h2.entity_last_state[classifier_ent] = {
        "state": "linger", "attrs": {"dwell_ms": 10100}, "ts": now + 5,  # 100ms diff < 200ms threshold
    }
    h2._close_trace(zone, "occupancy_off", now + 5)
    r2 = h2.dwell_freeze_results[-1]
    assert_(r2["freeze_ok"] is True, f"small delta passes (got freeze_ok={r2['freeze_ok']})")


def main() -> int:
    print("Running Addendum 35 Phase 5 inference-logic self-test (AR35-11)...")
    print()
    scenarios = [
        ("S1 suppression_skip",      s1_suppression_skip),
        ("S2 guard_skip (guard)",    s2_guard_skip),
        ("S2b guard_skip (noop)",    s2b_noop_disambiguation),
        ("S3 cooldown_bypass",       s3_cooldown_bypass),
        ("S4 movie_mode_trace",      s4_movie_mode_trace),
        ("S5 frigate_event latency", s5_frigate_event_latency),
        ("S6 mirror divergence",     s6_mirror_divergence),
        ("S7 predicted-vs-actual",   s7_predicted_vs_actual_delta),
        ("S8 dwell_freeze",          s8_dwell_freeze),
    ]
    failed = []
    for name, fn in scenarios:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e)))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))

    print()
    if not failed:
        # Format matching run-test-coverage.py FOOTER_RE pattern
        print(f"{len(scenarios)} pass . 0 fail")
        return 0
    else:
        print(f"{len(scenarios) - len(failed)} pass . {len(failed)} fail")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
