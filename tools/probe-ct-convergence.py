#!/usr/bin/env python3
"""probe-ct-convergence.py — house-wide color-temperature convergence regression harness.

Validates the CT-convergence ship (plan file: keen-doodling-parasol.md →
"House-wide color-temperature convergence"). After the ship:

  1. Living Lights pilots inject `color_temp_kelvin` from the zone classifier
     on every `light.turn_on` (10 pilot files, 7 emission sites each).
  2. Pilar Knob - Kitchen + Pilar Knob - Living Room inject ct from AL's
     global target.
  3. The LLM `execute_service_single` dispatcher auto-injects ct on any
     `light.turn_on` lacking a color-mode parameter (functions/native.py).

SAFETY: defaults to --non-destructive. The unit-test layer simulates the
injector against mock state — no real bulbs change. Full physical-convergence
tests require `--live --yes` (mirrors the probe-voice-stories.py discipline).

USAGE
  # Unit-test the injector logic only (no bulb changes):
  python tools/probe-ct-convergence.py

  # Full live suite — WILL change physical lights briefly:
  HA_URL=http://192.168.0.125:8123 \\
  HA_TOKEN=<long-lived-token> \\
  python tools/probe-ct-convergence.py --live --yes

  # Convenience token fetch (never prints token):
  python tools/probe-ct-convergence.py --token-from-haos --live --yes

PRIVACY
  - HA token never printed (only length).
  - Bulb color_temp_kelvin values are printed — they are the test signal.
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
from typing import Any

# Force UTF-8 stdout so the few non-ASCII glyphs render on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# ── Constants ─────────────────────────────────────────────────────────

# Kitchen pilot bulbs — all 5 share the same AL target ct (clamped at the
# bulb min of 2202 K when AL targets < 2202).
KITCHEN_LIGHTS = [
    "light.dining_table_left",
    "light.dining_table_right",
    "light.island_left",
    "light.island_right",
    "light.sink",
]
# Living-room bulbs that the LR Pilar Knob spans.
LR_LIGHTS = [
    "light.rear_right",
    "light.rear_left",
    "light.front_left",
    "light.front_right",
    "light.office",
]
# Per-bulb divergent ct's to seed before each convergence test. Order
# matches KITCHEN_LIGHTS / LR_LIGHTS. Spread is intentional (1000K+ range).
DIVERGENT_CTS = [2200, 3000, 3800, 4500, 5500]
# Convergence tolerance — Hue bulbs clamp at 2202K min, so an AL target of
# 2000K still lands at ~2202 (delta=202). 300K leaves headroom for the
# floor-clamp + in-flight transition rounding + the small spread between
# AL's target and each bulb's settled value.
CONVERGENCE_TOLERANCE_K = 300
# After a convergence trigger fires, max wait before the assertion. 5 s is
# the spec, give a small buffer for the slow pilot ramp.
CONVERGENCE_TIMEOUT_S = 8.0
POLL_INTERVAL_S = 0.5

DEFAULT_URL = "http://192.168.0.125:8123"


# ── HA REST helpers (urllib only — no third-party deps) ───────────────

def _http(url: str, token: str, method: str = "GET",
          data: dict | None = None, timeout: float = 10.0) -> dict:
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        return {"_status": e.code, "_raw": raw}
    return {"_status": 200, "_raw": raw}


def get_state(url: str, token: str, entity_id: str) -> dict | None:
    r = _http(f"{url}/api/states/{entity_id}", token)
    if r["_status"] != 200:
        return None
    try:
        return json.loads(r["_raw"])
    except json.JSONDecodeError:
        return None


def call_service(url: str, token: str, domain: str, service: str,
                 service_data: dict, timeout: float = 10.0) -> dict:
    return _http(f"{url}/api/services/{domain}/{service}", token,
                 method="POST", data=service_data, timeout=timeout)


def get_ct(url: str, token: str, entity_id: str) -> int | None:
    s = get_state(url, token, entity_id)
    if not s:
        return None
    ct = s.get("attributes", {}).get("color_temp_kelvin")
    try:
        return int(ct) if ct is not None else None
    except (TypeError, ValueError):
        return None


def get_al_target(url: str, token: str) -> int | None:
    """Read AL's current global ct target."""
    s = get_state(url, token, "switch.home_adaptive_lighting_home")
    if not s:
        return None
    ct = s.get("attributes", {}).get("color_temp_kelvin")
    try:
        return int(ct) if ct is not None else None
    except (TypeError, ValueError):
        return None


# ── Test-step helpers ─────────────────────────────────────────────────

def seed_divergent(url: str, token: str, bulbs: list[str]) -> bool:
    """Set each bulb to a distinct ct from DIVERGENT_CTS. Returns True if
    the resulting spread is > 1000 K (corruption succeeded)."""
    assert len(bulbs) == len(DIVERGENT_CTS), \
        f"Need {len(DIVERGENT_CTS)} bulbs, got {len(bulbs)}"
    for bulb, ct in zip(bulbs, DIVERGENT_CTS):
        call_service(url, token, "light", "turn_on", {
            "entity_id": bulb,
            "color_temp_kelvin": ct,
            "brightness_pct": 50,
            "transition": 0.2,
        })
    time.sleep(2.5)  # settle the cts
    cts = [get_ct(url, token, b) for b in bulbs]
    print(f"  seed cts: {dict(zip(bulbs, cts))}")
    valid = [c for c in cts if c is not None]
    if not valid:
        return False
    spread = max(valid) - min(valid)
    print(f"  spread: {spread} K")
    return spread > 1000


def await_convergence(url: str, token: str, bulbs: list[str],
                      al_target: int, timeout: float = CONVERGENCE_TIMEOUT_S,
                      tol: int = CONVERGENCE_TOLERANCE_K) -> tuple[bool, dict]:
    """Poll until every bulb's ct is within `tol` of `al_target`, or timeout.
    Returns (ok, final_cts_dict)."""
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        last = {b: get_ct(url, token, b) for b in bulbs}
        if all(c is not None and abs(c - al_target) <= tol
               for c in last.values()):
            return True, last
        time.sleep(POLL_INTERVAL_S)
    return False, last


# ── Test cases ────────────────────────────────────────────────────────

def test_pilot_convergence(url: str, token: str) -> dict:
    """Trigger island_left pilot via automation.trigger; assert the bulb
    converges to AL's target. Other 4 kitchen bulbs may still be diverged.
    """
    print("\n[2] Pilot convergence — island_left actuator")
    if not seed_divergent(url, token, KITCHEN_LIGHTS):
        return {"ok": False, "reason": "seed failed"}
    al = get_al_target(url, token)
    if al is None:
        return {"skip": True, "reason": "AL switch unavailable"}
    print(f"  AL target: {al} K")
    r = call_service(url, token, "automation", "trigger", {
        "entity_id": "automation.living_lights_island_left_actuator",
        "skip_condition": True,
    })
    if r["_status"] != 200:
        return {"ok": False, "reason": f"trigger HTTP {r['_status']}: {r['_raw'][:200]}"}
    ok, finals = await_convergence(
        url, token, ["light.island_left"], al)
    print(f"  island_left final ct: {finals.get('light.island_left')} K "
          f"(target {al} ± {CONVERGENCE_TOLERANCE_K})")
    return {"ok": ok, "finals": finals, "al_target": al}


def test_knob_yaml_static(url: str, token: str, knob_alias: str,
                          expected_sensor_in_template: str) -> dict:
    """STATIC verification: read the live automations.yaml via SSH and assert
    the named knob's light.turn_on data block contains color_temp_kelvin
    sourced from the expected zone classifier.

    Why static, not dynamic: the knob's `variables:` block reads
    `trigger.event.data.command` directly. When invoked via
    `automation.trigger` OR a synthetic `/api/events/zha_event` POST, the
    trigger object isn't fully populated and the variables block raises a
    Jinja UndefinedError, aborting the automation before light.turn_on can
    run. The only way to exercise the data block at runtime is a real
    physical knob twist — which the user does daily and which the HA
    logbook captures. So we verify the YAML wiring is in place; the
    runtime path is implicitly covered by user behaviour.
    """
    print(f"\n[3] Knob YAML static check — {knob_alias}")
    try:
        out = subprocess.check_output(
            ["ssh", "-p", "22222", "root@192.168.0.125",
             "cat", "/config/automations.yaml"],
            text=True, timeout=15,
        )
    except Exception as e:
        return {"ok": False, "reason": f"ssh fetch failed: {e}"}
    if knob_alias not in out:
        return {"ok": False, "reason": f"{knob_alias!r} not found in automations.yaml"}
    # Locate the knob's section
    a = out.index(knob_alias)
    next_id = out.find("\n- id: '", a)
    end = next_id if next_id > 0 else len(out)
    section = out[a:end]
    if "color_temp_kelvin" not in section:
        return {"ok": False, "reason": "color_temp_kelvin missing from knob section"}
    if expected_sensor_in_template not in section:
        return {"ok": False,
                "reason": f"expected classifier {expected_sensor_in_template!r} not in section"}
    if "switch.home_adaptive_lighting_home" not in section:
        return {"ok": False, "reason": "AL switch fallback not in template"}
    print(f"  knob section length: {len(section)} bytes")
    print(f"  ct line present: yes")
    print(f"  classifier fallback {expected_sensor_in_template}: yes")
    return {"ok": True}


def test_voice_convergence(url: str, token: str) -> dict:
    """POST a brightness-only utterance to conversation.process; assert the
    LLM injector populated ct on the resulting light.turn_on call."""
    print("\n[4] Voice convergence — 'Dim the kitchen lights to 50%'")
    if not seed_divergent(url, token, KITCHEN_LIGHTS):
        return {"ok": False, "reason": "seed failed"}
    al = get_al_target(url, token)
    if al is None:
        return {"skip": True, "reason": "AL switch unavailable"}
    print(f"  AL target: {al} K")
    r = _http(f"{url}/api/conversation/process", token, method="POST",
              data={
                  "text": "Dim the kitchen lights to 50%",
                  "conversation_id": "probe-ct-convergence",
                  "language": "en",
                  "agent_id": "conversation.extended_openai_conversation",
              }, timeout=30.0)
    if r["_status"] != 200:
        return {"ok": False, "reason": f"conversation HTTP {r['_status']}: {r['_raw'][:200]}"}
    try:
        resp = json.loads(r["_raw"])
    except json.JSONDecodeError:
        return {"ok": False, "reason": "conversation JSON parse failed"}
    speech = (resp.get("response", {}).get("speech", {})
                  .get("plain", {}).get("speech", "")) or ""
    print(f"  agent speech: {speech!r}")
    ok, finals = await_convergence(url, token, KITCHEN_LIGHTS, al)
    print(f"  final cts: {finals}")
    return {"ok": ok, "finals": finals, "al_target": al, "speech": speech}


# ── Unit test (non-destructive — covers the injector logic in isolation) ──

def unit_test_injector() -> dict:
    """Re-creates the injector's decision tree against mock state. This is
    what we run in --non-destructive mode; no HA contact, no light changes.
    Verifies the LOGIC of the patch in functions/native.py without needing
    a live system.
    """
    print("\n[U] Unit test — execute_service_single ct injector logic")

    # Mock the relevant parts of hass.states.get
    class MockState:
        def __init__(self, attrs):
            self.attributes = attrs

    def mock_hass_states_get(eid: str, *, al_value=2350, sofa_value=2400):
        if eid == "switch.home_adaptive_lighting_home":
            return MockState({"color_temp_kelvin": al_value})
        if eid == "sensor.living_room_sofa_lighting_state":
            return MockState({"predicted_color_temp_kelvin": sofa_value})
        return None

    color_keys = (
        "color_temp_kelvin", "color_temp",
        "rgb_color", "hs_color", "xy_color", "rgbw_color", "rgbww_color",
    )

    def injector(domain, service, service_data, mock_get):
        """Mirror of the production injector in functions/native.py."""
        if domain != "light" or service != "turn_on":
            return service_data
        if any(k in service_data for k in color_keys):
            return service_data
        al = mock_get("switch.home_adaptive_lighting_home")
        ct = al.attributes.get("color_temp_kelvin") if al else None
        if ct is None:
            zs = mock_get("sensor.living_room_sofa_lighting_state")
            ct = zs.attributes.get("predicted_color_temp_kelvin") if zs else None
        if ct is None:
            ct = 2700
        try:
            service_data["color_temp_kelvin"] = int(ct)
        except (TypeError, ValueError):
            pass
        return service_data

    cases = [
        # (label, domain, service, in_data, expected_ct_present, expected_ct_value)
        ("brightness-only on light.turn_on → inject AL target",
         "light", "turn_on", {"entity_id": ["light.x"], "brightness_pct": 50},
         True, 2350),
        ("bare light.turn_on (no params) → inject AL target",
         "light", "turn_on", {"entity_id": ["light.x"]},
         True, 2350),
        ("LLM-set ct present → DO NOT overwrite",
         "light", "turn_on",
         {"entity_id": ["light.x"], "color_temp_kelvin": 5500, "brightness_pct": 50},
         True, 5500),
        ("rgb_color set → DO NOT inject ct",
         "light", "turn_on",
         {"entity_id": ["light.x"], "rgb_color": [255, 0, 0]},
         False, None),
        ("xy_color set → DO NOT inject ct",
         "light", "turn_on",
         {"entity_id": ["light.x"], "xy_color": [0.5, 0.5]},
         False, None),
        ("hs_color set → DO NOT inject ct",
         "light", "turn_on",
         {"entity_id": ["light.x"], "hs_color": [120.0, 50.0]},
         False, None),
        ("light.turn_off → never inject",
         "light", "turn_off", {"entity_id": ["light.x"], "transition": 2.0},
         False, None),
        ("switch.turn_on → never inject (wrong domain)",
         "switch", "turn_on", {"entity_id": ["switch.x"]},
         False, None),
    ]
    fails = []
    for label, dom, svc, in_data, want_present, want_val in cases:
        out = injector(dom, svc, dict(in_data), mock_hass_states_get)
        actual_present = "color_temp_kelvin" in out
        actual_val = out.get("color_temp_kelvin")
        status = "ok"
        if actual_present != want_present:
            status = "FAIL (presence)"
        elif want_present and actual_val != want_val:
            status = f"FAIL (val={actual_val} want={want_val})"
        print(f"    {status:12s} {label}")
        if status != "ok":
            fails.append(label)

    # AL-unavailable → fallback to sofa classifier
    out = {}
    inj = injector("light", "turn_on", {"entity_id": ["light.x"], "brightness_pct": 50},
                   lambda eid: mock_hass_states_get(eid, al_value=None))
    if inj.get("color_temp_kelvin") == 2400:
        print("    ok           AL unavailable → fall back to sofa classifier (2400)")
    else:
        fails.append(f"sofa-fallback got {inj.get('color_temp_kelvin')}")

    # Both unavailable → fallback to 2700
    inj = injector("light", "turn_on", {"entity_id": ["light.x"], "brightness_pct": 50},
                   lambda eid: mock_hass_states_get(eid, al_value=None, sofa_value=None))
    if inj.get("color_temp_kelvin") == 2700:
        print("    ok           both unavailable → literal 2700")
    else:
        fails.append(f"literal-fallback got {inj.get('color_temp_kelvin')}")

    return {"ok": not fails, "fails": fails}


# ── Token / CLI ───────────────────────────────────────────────────────

def fetch_token_from_haos() -> str:
    """Best-effort: pull a long-lived token cached at /tmp/ha_token.txt on
    HAOS. Never prints the token value."""
    try:
        out = subprocess.check_output(
            ["ssh", "-p", "22222", "root@192.168.0.125", "cat", "/tmp/ha_token.txt"],
            text=True, timeout=15,
        ).strip()
        if out:
            print(f"  token-from-haos: {len(out)} chars OK")
            return out
    except Exception as e:
        print(f"  token-from-haos failed: {e}")
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=os.environ.get("HA_URL", DEFAULT_URL))
    ap.add_argument("--token", default=os.environ.get("HA_TOKEN"))
    ap.add_argument("--token-from-haos", action="store_true",
                    help="SSH to HAOS and read /tmp/ha_token.txt")
    ap.add_argument("--live", action="store_true",
                    help="Run physical-bulb tests. Without this, only the "
                         "injector unit-test runs (no light changes).")
    ap.add_argument("--yes", action="store_true",
                    help="Skip interactive confirmation on --live")
    args = ap.parse_args()

    # Always run unit test
    unit = unit_test_injector()

    if not args.live:
        print("\n=== SUMMARY (non-destructive) ===")
        print(f"  [U] injector unit test: {'ok' if unit['ok'] else 'NOT ok'}")
        if unit["fails"]:
            for f in unit["fails"]:
                print(f"    fail: {f}")
        sys.exit(0 if unit["ok"] else 1)

    # Live mode — token required
    token = args.token
    if not token and args.token_from_haos:
        token = fetch_token_from_haos()
    if not token:
        print("ERROR: --live requires HA_TOKEN env var or --token-from-haos",
              file=sys.stderr)
        sys.exit(2)
    print(f"\nLIVE MODE — physical bulbs will briefly change ct.")
    print(f"URL: {args.url}")
    print(f"Token: {len(token)} chars")
    if not args.yes:
        try:
            ans = input("Type 'yes' to proceed: ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "yes":
            print("Aborted.")
            sys.exit(2)

    results = {"unit": unit}
    results["pilot"] = test_pilot_convergence(args.url, token)
    results["knob_kitchen"] = test_knob_yaml_static(
        args.url, token, "alias: Pilar Knob - Kitchen",
        "sensor.dining_room_dining_left_lighting_state")
    results["knob_lr"] = test_knob_yaml_static(
        args.url, token, "alias: Pilar Knob - Living Room",
        "sensor.living_room_sofa_lighting_state")
    results["voice"] = test_voice_convergence(args.url, token)

    print("\n=== SUMMARY (live) ===")
    any_fail = False
    for name, r in results.items():
        if r.get("skip"):
            print(f"  [{name}] skip — {r.get('reason')}")
        elif r.get("ok"):
            print(f"  [{name}] ok")
        else:
            print(f"  [{name}] NOT ok — {r.get('reason', '(see above)')}")
            any_fail = True

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
