#!/usr/bin/env python3
"""probe-lighting-latency.py — Living Lights latency + flicker harness
(Addendum 34).

30-minute (default) unattended HA WebSocket capture. Subscribes to
state_changed + call_service events for a hardcoded watchlist of ~55
entities + 14 hardware-control automations. Correlates per-zone:

    T0  occupancy on        binary_sensor.{zone}_person_occupancy
    T1  classifier transition  sensor.{cam}_{zone}_lighting_state
    T2  actuator fired      automation.living_lights_{zone}_actuator
    T2.5 service called     call_service event (light/switch domain)
    T3  light responded     state_changed on each target entity

Outputs:
    tools/reports/lighting-latency-<ts>.jsonl  (raw events + traces)
    tools/reports/lighting-latency-<ts>.md     (human report)

Read-only. No mutations except: at start, toggles
input_boolean.test_scenario_flag once for timestamp-precision probe.

Reuses:
    - WSCapture WS pattern from tools/diagnose-identity.py
    - write_report markdown pattern from tools/validate-living-lights-phase.py
    - token loader from tools/probe-living-lights-acceptance.py

Per Addendum 34 AR34-9 through AR34-23.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import websockets  # type: ignore
except ImportError:
    print("FATAL: install websockets first: py -3 -m pip install websockets", file=sys.stderr)
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO / "tools" / "reports"
HA_HTTP = os.environ.get("HA_HTTP", "http://homeassistant.local:8123").rstrip("/")
HA_WS = os.environ.get(
    "HA_WS",
    HA_HTTP.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket",
)

# ─────────────────────────────────────────────────────────────────────
# Watchlist — every entity the harness cares about.
# ─────────────────────────────────────────────────────────────────────

ZONES = [
    # (slug, camera, occupancy_entity, classifier_entity, actuator_entity, targets)
    ("dining_left",       "dining_room", "binary_sensor.dining_left_person_occupancy",       "sensor.dining_room_dining_left_lighting_state",       "automation.living_lights_dining_left_actuator_phase_2_pilot", ["light.dining_table_left"]),
    ("dining_right",      "dining_room", "binary_sensor.dining_right_person_occupancy",      "sensor.dining_room_dining_right_lighting_state",      "automation.living_lights_dining_right_actuator",              ["light.dining_table_right"]),
    ("sink",              "kitchen",     "binary_sensor.sink_person_occupancy",              "sensor.kitchen_sink_lighting_state",                  "automation.living_lights_sink_actuator",                      ["light.sink"]),
    ("island_left",       "kitchen",     "binary_sensor.island_left_person_occupancy",       "sensor.kitchen_island_left_lighting_state",           "automation.living_lights_island_left_actuator",               ["light.island_left"]),
    ("island_right",      "kitchen",     "binary_sensor.island_right_person_occupancy",      "sensor.kitchen_island_right_lighting_state",          "automation.living_lights_island_right_actuator",              ["light.island_right"]),
    ("sofa",              "living_room", "binary_sensor.sofa_person_occupancy",              "sensor.living_room_sofa_lighting_state",              "automation.living_lights_sofa_actuator",                      ["light.front_left", "light.front_right", "switch.ambient_light_left_mss110_main_channel", "switch.ambient_light_right_mss110_main_channel"]),
    ("front_left",        "living_room", "binary_sensor.front_left_person_occupancy",        "sensor.living_room_front_left_lighting_state",        "automation.living_lights_front_left_actuator",                ["light.front_left"]),
    ("weights",           "living_room", "binary_sensor.weights_person_occupancy",           "sensor.living_room_weights_lighting_state",           "automation.living_lights_weights_actuator",                   ["light.front_right", "light.rear_right"]),
    ("office",            "living_room", "binary_sensor.office_person_occupancy",            "sensor.living_room_office_lighting_state",            "automation.living_lights_office_actuator",                    ["light.office"]),  # neon plug REMOVED per Addendum 34 Step 0
    ("front_door",        "living_room", "binary_sensor.front_door_person_occupancy",        "sensor.living_room_front_door_lighting_state",        "automation.living_lights_front_door_actuator",                ["light.front_right"]),
    # Observability-only zones (no actuator)
    ("whole_living_room", "living_room", "binary_sensor.whole_living_room_person_occupancy", "sensor.living_room_whole_living_room_lighting_state", None, []),
    ("whole_dining_room", "dining_room", "binary_sensor.whole_dining_room_person_occupancy", "sensor.dining_room_whole_dining_room_lighting_state", None, []),
    ("whole_kitchen",     "kitchen",     "binary_sensor.whole_kitchen_person_occupancy",     "sensor.kitchen_whole_kitchen_lighting_state",         None, []),
    ("workshop_zone",     "workshop",    "binary_sensor.workshop_zone_person_occupancy",     "sensor.workshop_workshop_zone_lighting_state",        None, []),
    ("e28",               "driveway",    "binary_sensor.e28_person_occupancy",               "sensor.driveway_e28_lighting_state",                  None, []),
]

# Hardware-control automations (their fires tag light state_changes as origin=hardware)
HARDWARE_AUTOMATIONS = {
    "automation.pilar_knob_kitchen",
    "automation.pilar_knob_living_room",
    "automation.front_door_living_room",
    "automation.office_symphonisk_double_click",
    "automation.office_symphonisk_toggle_neon",
    "automation.aqara_living_room_switch_1_all_lights_on",
    "automation.aqara_test_auto",  # alias for the OFF variant
    "automation.workshop_aqara_switch_2_controls_workshop_lights_group",
    "automation.zha_lutron_aurora_dimmer_toggle_v1_0",
    "automation.aurora_apply_virtual_knob_smooth",
}

# Addendum 35 item #2 — which zones a hardware fire suppresses.
# Source: Plan §G + automations.yaml (Pilar/Symfonisk/Aurora/Aqara already write
# input_datetime.living_lights_zone_<slug>_last_manual_at for these zones).
HARDWARE_TO_ZONES: dict[str, set[str]] = {
    "automation.pilar_knob_kitchen":           {"dining_left", "dining_right", "sink", "island_left", "island_right"},
    "automation.pilar_knob_living_room":       {"sofa", "front_left", "weights", "office", "front_door"},
    "automation.front_door_living_room":       {"front_door"},
    "automation.office_symphonisk_double_click":         {"office"},
    "automation.office_symphonisk_toggle_neon":          {"office"},
    "automation.aqara_living_room_switch_1_all_lights_on": {"sofa", "front_left", "weights", "office", "front_door"},
    "automation.aqara_test_auto":              {"sofa", "front_left", "weights", "office", "front_door"},
    "automation.workshop_aqara_switch_2_controls_workshop_lights_group": {"workshop_zone"},
    "automation.zha_lutron_aurora_dimmer_toggle_v1_0":   {"sofa", "front_left", "weights", "office"},
    "automation.aurora_apply_virtual_knob_smooth":       {"sofa", "front_left", "weights", "office"},
}

# Master toggle snapshot (any flip mid-run flagged)
MASTER_TOGGLES = {
    "input_boolean.living_lights_enabled",
    "input_boolean.living_lights_shadow",
}
# Plus per-zone enables
for slug, *_ in ZONES:
    MASTER_TOGGLES.add(f"input_boolean.living_lights_zone_{slug}_enabled")

# TOD profile (context)
TOD_PROFILE = "sensor.living_lights_profile"

# Build entity index for fast filtering
OCCUPANCY_BY_SLUG: dict[str, str] = {z[0]: z[2] for z in ZONES}
SLUG_BY_OCCUPANCY: dict[str, str] = {v: k for k, v in OCCUPANCY_BY_SLUG.items()}
CLASSIFIER_BY_SLUG: dict[str, str] = {z[0]: z[3] for z in ZONES}
SLUG_BY_CLASSIFIER: dict[str, str] = {v: k for k, v in CLASSIFIER_BY_SLUG.items()}
ACTUATOR_BY_SLUG: dict[str, Optional[str]] = {z[0]: z[4] for z in ZONES}
SLUG_BY_ACTUATOR: dict[str, str] = {v: k for k, v in ACTUATOR_BY_SLUG.items() if v}
TARGETS_BY_SLUG: dict[str, list[str]] = {z[0]: z[5] for z in ZONES}
TARGET_TO_SLUGS: dict[str, list[str]] = defaultdict(list)
for z in ZONES:
    for t in z[5]:
        TARGET_TO_SLUGS[t].append(z[0])

ALL_TARGETS: set[str] = set()
for ts in TARGETS_BY_SLUG.values():
    ALL_TARGETS.update(ts)

# Meross plug duplicates — only count switch.* for flicker (AR34-10)
LIGHT_MIRROR_OF_SWITCH = {
    "light.ambient_light_left_mss110_main_channel":  "switch.ambient_light_left_mss110_main_channel",
    "light.ambient_light_right_mss110_main_channel": "switch.ambient_light_right_mss110_main_channel",
}
FLICKER_IGNORE = set(LIGHT_MIRROR_OF_SWITCH.keys())
# AR35-13/#8 Meross mirror pairs — observe divergence between light.* and switch.*
MIRROR_PAIRS = LIGHT_MIRROR_OF_SWITCH  # alias for clarity in divergence detection

# Addendum 35 constants
APPLE_TV_ENTITY = "media_player.living_room_2"  # item #1
LIVING_ROOM_ZONES = {z[0] for z in ZONES if z[1] == "living_room"}  # 6 zones expected to flip movie_mode
NON_LIVING_ROOM_ZONES = {z[0] for z in ZONES if z[1] != "living_room"}  # false-positive guard
FRIGATE_HTTP = os.environ.get("FRIGATE_HTTP", "http://192.168.0.125:5000").rstrip("/")  # item #4 + AR35-3 heartbeat probe
SUPPRESSION_WINDOW_S = 60.0     # item #2 — match HA classifier's 60s gate
GUARD_SKIP_WAIT_S = 8.0          # item #3 — wait for light to respond on vacant
GUARD_VS_NOOP_BRI_PCT = 5.0      # AR35-2 — pre/predicted brightness gap > this = guard, else noop
FRIGATE_HEARTBEAT_S = 300.0      # AR35-3 — flag if no frigate_event in 5min while occupancy moves
COOLDOWN_THRESHOLD_S = 5.0       # AR35-4 — actuator cooldown window we expect bypass to override
MIRROR_DIVERGE_THRESHOLD_S = 2.0 # AR35-13 sustained divergence threshold
PREDICT_SETTLE_S = 5.0           # AR35-7 — wait for transition to settle before sampling actual
DWELL_FREEZE_THRESHOLD_MS = 200  # AR35-5 — max acceptable dwell_ms delta during grace

WATCHLIST: set[str] = set()
WATCHLIST.update(OCCUPANCY_BY_SLUG.values())
WATCHLIST.update(CLASSIFIER_BY_SLUG.values())
WATCHLIST.update(a for a in ACTUATOR_BY_SLUG.values() if a)
WATCHLIST.update(ALL_TARGETS)
WATCHLIST.update(HARDWARE_AUTOMATIONS)
WATCHLIST.update(MASTER_TOGGLES)
WATCHLIST.add(TOD_PROFILE)
WATCHLIST.add(APPLE_TV_ENTITY)  # Addendum 35 #1

# ─────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ZoneTrace:
    zone: str
    opened_ts: float            # T0 wall-clock
    opened_ha_ts: Optional[str] # T0 HA last_changed
    t1_ha_ts: Optional[str] = None
    t1_classifier_state: Optional[str] = None
    t1_predicted_bri: Optional[int] = None
    t1_predicted_ct: Optional[int] = None  # AR35-7 + #5 predicted color temp at T1
    t2_ha_ts: Optional[str] = None
    t2_actuator_last_triggered: Optional[str] = None
    service_calls: list[dict] = field(default_factory=list)  # T2.5
    target_responses: dict[str, dict] = field(default_factory=dict)  # T3 per target
    closed_ts: Optional[float] = None
    closed_reason: Optional[str] = None  # "occupancy_off" | "timeout" | "run_end"

    # Addendum 35 additions
    open_kind: Optional[str] = None       # "entry" | "exit" | "intra_presence" — AR35-8 fuller taxonomy
    from_state: Optional[str] = None      # prior classifier state at trace open
    to_state: Optional[str] = None        # target classifier state (when known)
    pre_actuator_last_triggered: Optional[str] = None  # AR35-4 cooldown-bypass evidence
    pre_target_bri: dict[str, int] = field(default_factory=dict)  # AR35-2 brightness BEFORE this transition, per target light
    delta_settled: dict[str, dict] = field(default_factory=dict)  # AR35-7 predicted-vs-actual delta per target (after settle)
    dwell_at_open: Optional[int] = None   # AR35-5 dwell freeze sample 1
    dwell_at_close: Optional[int] = None  # AR35-5 dwell freeze sample 2

    def to_json(self) -> dict:
        return {
            "kind": "trace",
            "zone": self.zone,
            "opened_ts": self.opened_ts,
            "opened_ha_ts": self.opened_ha_ts,
            "open_kind": self.open_kind,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "t1_ha_ts": self.t1_ha_ts,
            "t1_classifier_state": self.t1_classifier_state,
            "t1_predicted_bri": self.t1_predicted_bri,
            "t1_predicted_ct": self.t1_predicted_ct,
            "t2_ha_ts": self.t2_ha_ts,
            "t2_actuator_last_triggered": self.t2_actuator_last_triggered,
            "pre_actuator_last_triggered": self.pre_actuator_last_triggered,
            "pre_target_bri": self.pre_target_bri,
            "service_calls": self.service_calls,
            "target_responses": self.target_responses,
            "delta_settled": self.delta_settled,
            "dwell_at_open": self.dwell_at_open,
            "dwell_at_close": self.dwell_at_close,
            "expected_targets": TARGETS_BY_SLUG.get(self.zone, []),
            "closed_ts": self.closed_ts,
            "closed_reason": self.closed_reason,
        }


@dataclass
class MovieModeTrace:
    """Addendum 35 item #1: trace Apple TV → movie_mode integration.
    Opens on media_player state == 'playing'; closes on paused/stopped/standby."""
    tv_entity: str
    opened_ts: float
    opened_ha_ts: Optional[str]
    open_tv_state: str
    # Per-zone snapshot of classifier state at trace open (BEFORE flip)
    pre_zone_state: dict[str, str] = field(default_factory=dict)
    # Per-zone classifier state AFTER trace settle (5s post-open)
    post_zone_state: dict[str, str] = field(default_factory=dict)
    # Per-zone HA-timestamp at which classifier flipped to movie_mode
    zone_flip_ha_ts: dict[str, str] = field(default_factory=dict)
    # Per-target light: ha_ts of first state_changed during trace
    target_dim_ha_ts: dict[str, str] = field(default_factory=dict)
    # Non-living-room zones that flipped (false-positive guard)
    false_positive_flips: list[str] = field(default_factory=list)
    closed_ts: Optional[float] = None
    close_tv_state: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "kind": "movie_mode_trace",
            "tv_entity": self.tv_entity,
            "opened_ts": self.opened_ts,
            "opened_ha_ts": self.opened_ha_ts,
            "open_tv_state": self.open_tv_state,
            "pre_zone_state": self.pre_zone_state,
            "post_zone_state": self.post_zone_state,
            "zone_flip_ha_ts": self.zone_flip_ha_ts,
            "target_dim_ha_ts": self.target_dim_ha_ts,
            "false_positive_flips": self.false_positive_flips,
            "closed_ts": self.closed_ts,
            "close_tv_state": self.close_tv_state,
        }


# ─────────────────────────────────────────────────────────────────────
# Auth + token
# ─────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────
# Module-level helpers (lifted from write_report() closures per Addendum 35
# AR35-6 — verified pure; no closures over outer-scope locals).
# ─────────────────────────────────────────────────────────────────────

def _dt_ms(t1_iso: Optional[str], t0_iso: Optional[str]) -> Optional[float]:
    """Delta in milliseconds between two HA ISO timestamps. Returns None on parse failure."""
    if not t1_iso or not t0_iso:
        return None
    try:
        a = datetime.fromisoformat(t1_iso.replace("Z", "+00:00"))
        b = datetime.fromisoformat(t0_iso.replace("Z", "+00:00"))
        return (a - b).total_seconds() * 1000.0
    except Exception:
        return None


def _p(lst, q):
    """Percentile (p50 or p95) over a list of milliseconds. Returns formatted string."""
    vals = [v for v in lst if v is not None and v >= 0]
    if not vals:
        return "-"
    if q == 50:
        return f"{statistics.median(vals):.0f}ms"
    if q == 95:
        s = sorted(vals)
        return f"{s[max(0, int(0.95 * len(s)) - 1)]:.0f}ms"
    return "-"


# ─────────────────────────────────────────────────────────────────────
# WS subscriber
# ─────────────────────────────────────────────────────────────────────

class Harness:
    def __init__(self, token: str, duration_s: int, output_dir: Path):
        self.token = token
        self.duration_s = duration_s
        self.output_dir = output_dir
        self.start_ts = time.time()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        self.jsonl_path = output_dir / f"lighting-latency-{ts}.jsonl"
        self.md_path = output_dir / f"lighting-latency-{ts}.md"
        self.jsonl_fh = None
        self.ws = None
        self._next_id = 100
        self._gap_count = 0

        # Live traces per zone — None when no trace open
        self.live_traces: dict[str, ZoneTrace] = {}
        # All closed traces
        self.closed_traces: list[ZoneTrace] = []

        # Flicker: per-entity full log of (ts, old, new, origin) state-flip tuples
        self.flicker_log: dict[str, list[tuple]] = defaultdict(list)
        # Brightness step log per entity: list of (ts, old_bri, new_bri)
        self.bri_step_log: dict[str, list[tuple]] = defaultdict(list)

        # Native FLUTTER tracking — direction-reversal detection.
        # Per-entity: full smoothed brightness trajectory (samples only when
        # delta >= FLUTTER_IGNORE_PCT from prior surviving sample).
        # plus running state (prev_dir, total reversals, peak per 60s deque).
        self.FLUTTER_IGNORE_PCT = 5.0
        self.FLUTTER_PEAK_WINDOW_S = 60.0
        # Smoothed sample list (only the surviving samples after noise filter)
        self.flutter_samples: dict[str, list[tuple[float, float]]] = defaultdict(list)
        # Real-time direction tracking
        self.flutter_state: dict[str, dict] = defaultdict(lambda: {
            "prev_dir": None,    # +1, -1, or None
            "reversals": 0,
            "peak_60s": 0,
            "recent": deque(),   # deque[ts] of recent reversal timestamps for 60s peak
        })

        # Service-call thrash counter: per-entity, list of (ts, payload-snapshot)
        # for ALL call_service events targeting that entity. End-of-run we
        # cluster them and count clusters with N>=2 calls within 3s.
        self.service_call_log: dict[str, list[tuple[float, dict]]] = defaultdict(list)
        self.SERVICE_THRASH_WINDOW_S = 3.0

        # Hardware tag: ts of last hardware automation fire (used to tag light state_changes within 5s)
        self.last_hardware_fire_ts: float = 0.0

        # Spurious-vacancy: per-zone, list of (off_ts, was_re_detected_within_10s)
        self.vacancy_loss_log: dict[str, list[tuple]] = defaultdict(list)
        # Recent occupancy off events: zone -> ts (for re-detect lookup)
        self._recent_off: dict[str, float] = {}

        # Total events
        self.event_count = 0

        # Master toggle snapshot
        self.master_toggle_snapshot: dict[str, str] = {}
        self.master_toggle_flips: list[dict] = []

        # Classifier transition log per zone (for transition pattern histogram)
        self.classifier_transitions: dict[str, list[tuple]] = defaultdict(list)

        # ── Addendum 35 instrumentation state ──

        # #1 Movie_mode trace: live + closed
        self.live_movie_trace: Optional[MovieModeTrace] = None
        self.closed_movie_traces: list[MovieModeTrace] = []
        # Hold a copy of last seen classifier states (for movie_mode pre/post snapshots)
        self.last_classifier_state: dict[str, str] = {}  # slug -> state

        # #2 Per-zone hardware-touch timestamp (separate from global flicker-tagging ts)
        self.zone_last_hardware_fire_ts: dict[str, float] = defaultdict(float)
        # Per-zone counter-factual log: list of (ts, from_state, to_state, actuator_fired_bool)
        # rolling 1-hour window — used by AR35-1 counter-factual reasoning
        self.zone_transition_actuation_log: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        # Suppression-skip events (high vs low confidence per AR35-12)
        self.suppression_skips: list[dict] = []

        # #3 Guard-skip log (with noop disambiguation per AR35-2)
        self.guard_skips: list[dict] = []
        # Per-light: last seen brightness (for pre-vacant snapshot)
        self.light_last_bri: dict[str, int] = {}

        # #4 Frigate event latency
        self.frigate_event_log: list[dict] = []  # each: {ts, camera, entered_zones, raw}
        self.frigate_event_to_state_latencies: dict[str, list[float]] = defaultdict(list)  # camera -> ms list
        self.last_frigate_event_ts: float = 0.0  # for AR35-3 heartbeat
        # Per-zone: ts of most-recent occupancy state_change (for heartbeat correlation)
        self._recent_occ_change_by_zone: dict[str, float] = {}
        self.frigate_subscription_dead_warnings: list[dict] = []
        # First N frigate_events captured for schema-shape forensics (AR35-13 → AR35-19)
        self._frigate_event_samples: list[dict] = []

        # #7 Cooldown bypass detection (with AR35-4 prior-state evidence)
        self.cooldown_bypasses: list[dict] = []
        # Per-actuator: last_triggered seen most recently (so on next fire we know the PRIOR value)
        self.actuator_last_triggered_seen: dict[str, str] = {}

        # #8 Mirror divergence detection (AR35-13)
        self.mirror_divergences: list[dict] = []
        # Track current state of each side of a mirror pair, with ts of divergence start
        self.mirror_state: dict[str, str] = {}     # entity -> state
        self.mirror_diverge_start: dict[tuple[str, str], float] = {}  # (light_ent, switch_ent) -> ts when divergence began

        # #5 Predicted-vs-actual scheduling (AR35-7 deferred sampling)
        # List of (sample_ts, zone, target, t1_predicted_bri, t1_predicted_ct)
        # — when the harness reaches sample_ts, fetch actual bri/ct via REST + record delta
        self.pending_delta_samples: list[dict] = []

        # #10 Dwell-freeze checks per trace (computed at close)
        self.dwell_freeze_results: list[dict] = []

        # Track entity-level last seen state for #3/#10 retrieval (per AR35-19 attribute deltas)
        self.entity_last_state: dict[str, dict] = {}  # entity_id -> {state, attrs, ts}

        # #3 deferred guard-skip checks: list of {sample_ts, zone, target, pre_bri, predicted_vacant_bri}
        self.pending_guard_checks: list[dict] = []

    # ── WS auth + subscribe ──

    async def _subscribe_event_type(self, event_type: str) -> dict:
        sub_id = self._next_id
        self._next_id += 1
        await self.ws.send(json.dumps({"id": sub_id, "type": "subscribe_events", "event_type": event_type}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == sub_id:
                return message

    async def connect(self):
        self.ws = await websockets.connect(HA_WS, max_size=4 * 1024 * 1024, ping_interval=20)
        hello = json.loads(await self.ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected first WS msg: {hello}")
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        resp = json.loads(await self.ws.recv())
        if resp.get("type") != "auth_ok":
            raise RuntimeError(f"WS auth failed: {resp}")
        # Subscribe to state_changed
        ack = await self._subscribe_event_type("state_changed")
        if not ack.get("success"):
            raise RuntimeError(f"subscribe state_changed failed: {ack}")
        # Subscribe to call_service (T2.5)
        ack = await self._subscribe_event_type("call_service")
        if not ack.get("success"):
            raise RuntimeError(f"subscribe call_service failed: {ack}")
        # Subscribe to frigate_event (Addendum 35 item #4)
        ack = await self._subscribe_event_type("frigate_event")
        if not ack.get("success"):
            # Non-fatal — frigate_event subscription may be unavailable on some HA versions/configs
            print(f"  WARN: subscribe frigate_event failed: {ack.get('error', ack)} - upstream-latency disabled", file=sys.stderr)
        return resp.get("ha_version", "?")

    # ── Pre-flight: full health probe (Addendum 35 AR35-9) ──

    async def preflight(self) -> dict:
        """Health probe before WS subscription. Returns dict with:
        - abort_reasons: list[str]  — non-empty triggers abort
        - warnings: list[str]       — surface but proceed
        - checks_passed: list[str]
        - disabled_zones: list[str] — per-zone enables == off
        - precision_probe: dict
        """
        import urllib.request
        headers = {"Authorization": f"Bearer {self.token}"}
        info: dict = {
            "abort_reasons": [],
            "warnings": [],
            "checks_passed": [],
            "disabled_zones": [],
            "precision_probe": {"available": False},
        }

        # 1. HA reachable (ABORT-on-fail)
        try:
            req = urllib.request.Request(f"{HA_HTTP}/api/", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status != 200:
                    info["abort_reasons"].append(f"HA /api/ returned {r.status}")
                else:
                    info["checks_passed"].append("ha_reachable")
        except Exception as e:
            info["abort_reasons"].append(f"HA /api/ unreachable: {e}")

        # 2. Frigate reachable (WARN-on-fail; Frigate not strictly required for run to start)
        try:
            req = urllib.request.Request(f"{FRIGATE_HTTP}/api/version", headers={})
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    info["checks_passed"].append("frigate_reachable")
                else:
                    info["warnings"].append(f"Frigate {FRIGATE_HTTP}/api/version returned {r.status}")
        except Exception as e:
            info["warnings"].append(f"Frigate {FRIGATE_HTTP} unreachable: {type(e).__name__}")

        # 3. Snapshot master toggles + per-zone enables
        for ent in MASTER_TOGGLES:
            try:
                req = urllib.request.Request(f"{HA_HTTP}/api/states/{ent}", headers=headers)
                with urllib.request.urlopen(req, timeout=5) as r:
                    d = json.load(r)
                    self.master_toggle_snapshot[ent] = d.get("state", "unknown")
            except Exception:
                self.master_toggle_snapshot[ent] = "ERROR"

        # 4. All 15 occupancy binary_sensors must exist
        missing_occ: list[str] = []
        for occ in OCCUPANCY_BY_SLUG.values():
            try:
                req = urllib.request.Request(f"{HA_HTTP}/api/states/{occ}", headers=headers)
                with urllib.request.urlopen(req, timeout=3) as r:
                    json.load(r)
            except Exception:
                missing_occ.append(occ)
        if missing_occ:
            info["abort_reasons"].append(f"Missing occupancy binary_sensors ({len(missing_occ)}): {missing_occ[:5]}{'...' if len(missing_occ)>5 else ''}")
        else:
            info["checks_passed"].append("all_15_occupancy_present")

        # 5. All actuator automations must exist
        missing_act: list[str] = []
        for act in ACTUATOR_BY_SLUG.values():
            if not act:
                continue
            try:
                req = urllib.request.Request(f"{HA_HTTP}/api/states/{act}", headers=headers)
                with urllib.request.urlopen(req, timeout=3) as r:
                    json.load(r)
            except Exception:
                missing_act.append(act)
        if missing_act:
            info["abort_reasons"].append(f"Missing actuator automations ({len(missing_act)}): {missing_act}")
        else:
            info["checks_passed"].append("all_actuators_present")

        # 6. AR35-9: enumerate per-zone enables that are OFF (WARN)
        for slug, *_ in ZONES:
            ent = f"input_boolean.living_lights_zone_{slug}_enabled"
            if self.master_toggle_snapshot.get(ent) == "off":
                info["disabled_zones"].append(slug)
        if info["disabled_zones"]:
            info["warnings"].append(f"Per-zone enables OFF ({len(info['disabled_zones'])}): {info['disabled_zones']}")

        # 7. Master toggle sanity — WARN (not abort) if config makes the test meaningless
        if self.master_toggle_snapshot.get("input_boolean.living_lights_enabled") != "on":
            info["warnings"].append("⚠ living_lights_enabled is OFF — actuators won't fire (observability-only run)")
        if self.master_toggle_snapshot.get("input_boolean.living_lights_shadow") == "on":
            info["warnings"].append("⚠ living_lights_shadow is ON — actuators log only (observability-only run)")

        # 8. Timestamp-precision probe — confirms input_boolean.test_scenario_flag exists for synthetic-event tests
        try:
            req = urllib.request.Request(f"{HA_HTTP}/api/states/input_boolean.test_scenario_flag", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as r:
                json.load(r)
            info["precision_probe"]["available"] = True
        except Exception:
            info["precision_probe"]["available"] = False

        return info

    # ── Event handlers ──

    def _entity_in_watchlist(self, eid: str) -> bool:
        return eid in WATCHLIST

    def _open_or_extend_trace(self, zone: str, ev_data: dict, ws_ts: float):
        """Called when occupancy on→off→on or new on."""
        # Close any existing trace for this zone first (e.g., re-arrival)
        if zone in self.live_traces:
            self._close_trace(zone, "reopened", ws_ts)
        new_state = ev_data.get("new_state") or {}
        trace = ZoneTrace(
            zone=zone,
            opened_ts=ws_ts,
            opened_ha_ts=new_state.get("last_changed"),
        )
        # Addendum 35 AR35-8 fuller taxonomy: from/to classifier states
        classifier_ent = CLASSIFIER_BY_SLUG.get(zone)
        classifier_cache = self.entity_last_state.get(classifier_ent) or {}
        trace.from_state = classifier_cache.get("state") or "unknown"
        trace.open_kind = "entry"
        # Addendum 35 AR35-5 dwell freeze sample 1 (at open — should be near 0)
        trace.dwell_at_open = (classifier_cache.get("attrs") or {}).get("dwell_ms")
        # Addendum 35 AR35-4: capture PRIOR actuator last_triggered for cooldown-bypass evidence
        actuator_ent = ACTUATOR_BY_SLUG.get(zone)
        if actuator_ent:
            actuator_cache = self.entity_last_state.get(actuator_ent) or {}
            trace.pre_actuator_last_triggered = (actuator_cache.get("attrs") or {}).get("last_triggered")
        # Addendum 35 AR35-2: snapshot pre-target brightness for guard-skip later
        for target in TARGETS_BY_SLUG.get(zone, []):
            tcache = self.entity_last_state.get(target) or {}
            tattrs = tcache.get("attrs") or {}
            tbri = tattrs.get("brightness")
            if tbri is None:
                tbri_pct = 100 if tcache.get("state") == "on" else 0
            else:
                tbri_pct = int(min(100, max(0, tbri / 2.55)))
            trace.pre_target_bri[target] = tbri_pct
        self.live_traces[zone] = trace

    def _close_trace(self, zone: str, reason: str, ws_ts: float):
        trace = self.live_traces.pop(zone, None)
        if not trace:
            return
        trace.closed_ts = ws_ts
        trace.closed_reason = reason
        # Addendum 35 AR35-8: capture exit classifier state
        classifier_ent = CLASSIFIER_BY_SLUG.get(zone)
        classifier_cache = self.entity_last_state.get(classifier_ent) or {}
        trace.to_state = classifier_cache.get("state") or "unknown"
        # Addendum 35 AR35-5 dwell freeze sample 2 (at close — peak dwell value)
        trace.dwell_at_close = (classifier_cache.get("attrs") or {}).get("dwell_ms")
        # Compute freeze invariant (only meaningful when both samples present)
        if trace.dwell_at_open is not None and trace.dwell_at_close is not None:
            delta_ms = abs(trace.dwell_at_close - trace.dwell_at_open)
            freeze_ok = delta_ms < DWELL_FREEZE_THRESHOLD_MS
            self.dwell_freeze_results.append({
                "ts": ws_ts, "zone": zone,
                "delta_ms": delta_ms,
                "dwell_at_open": trace.dwell_at_open,
                "dwell_at_close": trace.dwell_at_close,
                "freeze_ok": freeze_ok,
            })
        self.closed_traces.append(trace)
        # Write to JSONL
        self._write_jsonl(trace.to_json())

    def _attach_classifier(self, zone: str, ev_data: dict):
        trace = self.live_traces.get(zone)
        if not trace:
            return
        ns = ev_data.get("new_state") or {}
        attrs = ns.get("attributes") or {}
        if trace.t1_ha_ts is None:
            trace.t1_ha_ts = ns.get("last_changed")
            trace.t1_classifier_state = ns.get("state")
            trace.t1_predicted_bri = attrs.get("predicted_brightness_pct")

    def _attach_actuator(self, zone: str, ev_data: dict):
        trace = self.live_traces.get(zone)
        if not trace:
            return
        ns = ev_data.get("new_state") or {}
        attrs = ns.get("attributes") or {}
        if trace.t2_ha_ts is None:
            trace.t2_ha_ts = ns.get("last_changed")
            trace.t2_actuator_last_triggered = attrs.get("last_triggered")

    def _attach_service_call(self, ev_data: dict):
        domain = ev_data.get("domain")
        if domain not in {"light", "switch"}:
            return
        svc = ev_data.get("service")
        sdata = ev_data.get("service_data") or {}
        # entity_id can be str, list, or via target
        ents: list[str] = []
        eid = sdata.get("entity_id")
        if isinstance(eid, str):
            ents = [eid]
        elif isinstance(eid, list):
            ents = list(eid)
        # Also check target.entity_id
        tgt = sdata.get("target") or {}
        teid = tgt.get("entity_id")
        if isinstance(teid, str):
            ents.append(teid)
        elif isinstance(teid, list):
            ents.extend(teid)
        now_ts = time.time()
        # Track each call for thrash detection at end-of-run (AR34 flutter section)
        snapshot = {
            "domain": domain, "service": svc,
            "brightness_pct": sdata.get("brightness_pct"),
            "color_temp_kelvin": sdata.get("color_temp_kelvin"),
            "transition": sdata.get("transition"),
        }
        for ent in ents:
            self.service_call_log[ent].append((now_ts, snapshot))
        # Attach to any trace whose expected targets include any of these
        for ent in ents:
            for zone in TARGET_TO_SLUGS.get(ent, []):
                trace = self.live_traces.get(zone)
                if trace:
                    trace.service_calls.append({
                        "domain": domain, "service": svc, "entity_id": ent,
                        "ts": now_ts,
                    })

    def _attach_target_response(self, ent: str, ev_data: dict, ws_ts: float):
        """Light/switch state changed — attach to any open trace expecting this target."""
        ns = ev_data.get("new_state") or {}
        attrs = ns.get("attributes") or {}
        new_state_str = ns.get("state")
        bri = attrs.get("brightness")
        ct = attrs.get("color_temp_kelvin")
        # Tag origin
        origin = "hardware" if (ws_ts - self.last_hardware_fire_ts) < 5.0 else "living_lights"
        for zone in TARGET_TO_SLUGS.get(ent, []):
            trace = self.live_traces.get(zone)
            if trace and ent not in trace.target_responses:
                trace.target_responses[ent] = {
                    "ts": ws_ts,
                    "ha_ts": ns.get("last_changed"),
                    "state": new_state_str,
                    "brightness": bri,
                    "color_temp_kelvin": ct,
                    "origin": origin,
                }

    def _flicker_register(self, ent: str, ev_data: dict, ws_ts: float):
        if ent in FLICKER_IGNORE:
            return
        old = (ev_data.get("old_state") or {}).get("state")
        new = (ev_data.get("new_state") or {}).get("state")
        if old != new:
            origin = "hardware" if (ws_ts - self.last_hardware_fire_ts) < 5.0 else "living_lights"
            self.flicker_log[ent].append((ws_ts, old, new, origin))
        # Brightness step
        oa = (ev_data.get("old_state") or {}).get("attributes") or {}
        na = (ev_data.get("new_state") or {}).get("attributes") or {}
        ob = oa.get("brightness")
        nb = na.get("brightness")
        if ob is not None and nb is not None and ob != nb:
            self.bri_step_log[ent].append((ws_ts, ob, nb))
        # FLUTTER — direction-reversal detection on smoothed brightness signal.
        # Brightness raw 0..255 → pct. Treat switches as 0/100 binary too so
        # ambient plugs get a flutter count.
        bri_pct = None
        if nb is not None:
            bri_pct = min(100.0, max(0.0, nb / 2.55))
        elif new == "on":
            bri_pct = 100.0
        elif new == "off":
            bri_pct = 0.0
        if bri_pct is None:
            return
        # Smoothing: only register if delta from prior surviving sample >= threshold
        samples = self.flutter_samples[ent]
        if samples and abs(bri_pct - samples[-1][1]) < self.FLUTTER_IGNORE_PCT:
            return
        samples.append((ws_ts, bri_pct))
        # Direction-reversal increment
        if len(samples) >= 2:
            delta = samples[-1][1] - samples[-2][1]
            if delta != 0:
                cur_dir = 1 if delta > 0 else -1
                st = self.flutter_state[ent]
                if st["prev_dir"] is not None and cur_dir != st["prev_dir"]:
                    st["reversals"] += 1
                    st["recent"].append(ws_ts)
                    # Prune to 60s window + update peak
                    cutoff = ws_ts - self.FLUTTER_PEAK_WINDOW_S
                    while st["recent"] and st["recent"][0] < cutoff:
                        st["recent"].popleft()
                    if len(st["recent"]) > st["peak_60s"]:
                        st["peak_60s"] = len(st["recent"])
                st["prev_dir"] = cur_dir

    def _write_jsonl(self, payload: dict):
        if self.jsonl_fh:
            self.jsonl_fh.write(json.dumps(payload) + "\n")
            self.jsonl_fh.flush()

    # ─── Addendum 35 helpers ───────────────────────────────────────────

    def _handle_apple_tv(self, ev_data: dict, ws_ts: float):
        """Item #1: Open / close MovieModeTrace based on Apple TV state."""
        ns = ev_data.get("new_state") or {}
        os_ = ev_data.get("old_state") or {}
        new_state = ns.get("state", "")
        old_state = os_.get("state", "")
        if old_state == new_state:
            return
        ACTIVE = {"playing"}
        IDLE = {"paused", "idle", "off", "standby", "stopped", "unknown", "unavailable"}
        # Active → close any prior trace, open new
        if new_state in ACTIVE and (self.live_movie_trace is None or self.live_movie_trace.close_tv_state is None):
            if self.live_movie_trace is not None:
                # New 'playing' before prior closed — re-trace (e.g., quick pause/play)
                self.live_movie_trace.closed_ts = ws_ts
                self.live_movie_trace.close_tv_state = "reopened"
                self.closed_movie_traces.append(self.live_movie_trace)
                self._write_jsonl(self.live_movie_trace.to_json())
            self.live_movie_trace = MovieModeTrace(
                tv_entity=APPLE_TV_ENTITY,
                opened_ts=ws_ts,
                opened_ha_ts=ns.get("last_changed"),
                open_tv_state=new_state,
            )
            # Snapshot all zone classifier states at trace open (BEFORE flip)
            for slug in LIVING_ROOM_ZONES | NON_LIVING_ROOM_ZONES:
                self.live_movie_trace.pre_zone_state[slug] = self.last_classifier_state.get(slug, "unknown")
            self._write_jsonl({"kind": "movie_mode_open", "ts": ws_ts, "tv_state": new_state})
        # Idle → close active trace
        elif new_state in IDLE and self.live_movie_trace is not None:
            mt = self.live_movie_trace
            mt.closed_ts = ws_ts
            mt.close_tv_state = new_state
            # Post-snapshot for any zones that DIDN'T flip during the trace
            for slug in LIVING_ROOM_ZONES | NON_LIVING_ROOM_ZONES:
                if slug not in mt.post_zone_state:
                    mt.post_zone_state[slug] = self.last_classifier_state.get(slug, "unknown")
            self.closed_movie_traces.append(mt)
            self._write_jsonl(mt.to_json())
            self.live_movie_trace = None

    def _record_classifier_for_movie_trace(self, zone: str, new_state: str, ha_ts: Optional[str]):
        """During a live movie trace: capture flip-to-movie_mode timing per zone + false positives."""
        mt = self.live_movie_trace
        if mt is None:
            return
        if new_state == "movie_mode" and zone not in mt.zone_flip_ha_ts:
            mt.zone_flip_ha_ts[zone] = ha_ts or ""
            # False-positive guard: a non-living_room zone flipped to movie_mode
            if zone in NON_LIVING_ROOM_ZONES:
                mt.false_positive_flips.append(zone)
        # Update post_zone_state continuously so close() captures latest seen state
        mt.post_zone_state[zone] = new_state

    def _handle_frigate_event(self, ev_data: dict, time_fired: Optional[str], ws_ts: float):
        """Item #4: upstream Frigate detection → HA state_changed latency."""
        self.last_frigate_event_ts = ws_ts
        after = ev_data.get("after") or ev_data
        camera = after.get("camera") if isinstance(after, dict) else None
        entered = after.get("entered_zones") if isinstance(after, dict) else None
        if not isinstance(entered, list):
            entered = []
        evt_record = {
            "ts": ws_ts,
            "time_fired": time_fired,
            "camera": camera,
            "entered_zones": entered,
            "label": (after or {}).get("label"),
            "sub_label": (after or {}).get("sub_label"),
        }
        self.frigate_event_log.append(evt_record)
        # Capture first 3 raw payloads for schema forensics (AR35-13/19)
        if len(self._frigate_event_samples) < 3:
            self._frigate_event_samples.append({"ts": ws_ts, "raw": ev_data})
        self._write_jsonl({"kind": "frigate_event", **evt_record})

    def _attach_frigate_latency_on_occupancy(self, zone: str, new_s: str, ha_ts: Optional[str], ws_ts: float):
        """Called when binary_sensor.{zone}_person_occupancy flips → match against recent frigate_event."""
        if not ha_ts or new_s != "on":
            return
        # Find latest frigate_event whose entered_zones contains this zone, BEFORE this occupancy event
        try:
            occ_ts = datetime.fromisoformat(ha_ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return
        # Scan recent frigate events (last 30s) for matching zone
        for evt in reversed(self.frigate_event_log[-200:]):
            if evt["ts"] > ws_ts:  # future event — skip
                continue
            if zone in (evt.get("entered_zones") or []):
                # ha_ts.last_changed - frigate_event.ts (wall-clock approximation)
                latency_ms = (occ_ts - evt["ts"]) * 1000.0
                if -5000 < latency_ms < 30000:  # filter implausibly large/negative deltas
                    camera = evt.get("camera") or "?"
                    self.frigate_event_to_state_latencies[camera].append(latency_ms)
                    self._write_jsonl({
                        "kind": "frigate_to_state_latency",
                        "zone": zone, "camera": camera,
                        "latency_ms": round(latency_ms, 1),
                    })
                break

    def _check_frigate_heartbeat(self, ws_ts: float):
        """AR35-3: alert if >=5min since last frigate_event but occupancy moved."""
        if not self._recent_occ_change_by_zone:
            return
        if self.last_frigate_event_ts == 0.0:
            return  # never received any — handled differently (preflight warns)
        gap_s = ws_ts - self.last_frigate_event_ts
        if gap_s < FRIGATE_HEARTBEAT_S:
            return
        # Any occupancy change in the same window?
        recent_occ = max(self._recent_occ_change_by_zone.values()) if self._recent_occ_change_by_zone else 0
        if recent_occ > self.last_frigate_event_ts:
            # Suppress repeat-warns within heartbeat window
            if self.frigate_subscription_dead_warnings and \
               (ws_ts - self.frigate_subscription_dead_warnings[-1]["ts"]) < FRIGATE_HEARTBEAT_S:
                return
            warning = {
                "ts": ws_ts,
                "gap_since_last_frigate_event_s": round(gap_s, 1),
                "last_occupancy_change_age_s": round(ws_ts - recent_occ, 1),
            }
            self.frigate_subscription_dead_warnings.append(warning)
            self._write_jsonl({"kind": "frigate_subscription_appears_dead", **warning})

    def _handle_per_zone_hardware_fire(self, eid: str, ws_ts: float):
        """Item #2: when a hardware automation fires, record the touch ts per affected zone."""
        zones = HARDWARE_TO_ZONES.get(eid, set())
        for z in zones:
            self.zone_last_hardware_fire_ts[z] = ws_ts

    def _record_classifier_transition_for_counterfactual(
        self, zone: str, from_state: str, to_state: str, ws_ts: float,
        actuator_last_triggered_iso: Optional[str],
    ):
        """AR35-1: keep a rolling log of (transition, did_actuator_fire?) for counter-factual reasoning."""
        # Capture whether the actuator fired around now (within 2s) by checking its current last_triggered
        actuator_ent = ACTUATOR_BY_SLUG.get(zone)
        actuator_fired = False
        if actuator_ent and actuator_last_triggered_iso:
            try:
                lt_ts = datetime.fromisoformat(actuator_last_triggered_iso.replace("Z", "+00:00")).timestamp()
                actuator_fired = abs(ws_ts - lt_ts) < 2.0
            except Exception:
                pass
        # Drop old entries (>1h)
        cutoff = ws_ts - 3600
        dq = self.zone_transition_actuation_log[zone]
        while dq and dq[0]["ts"] < cutoff:
            dq.popleft()
        dq.append({
            "ts": ws_ts, "from": from_state, "to": to_state,
            "actuator_fired": actuator_fired,
        })

    def _check_suppression_skip(self, zone: str, from_state: str, to_state: str, ws_ts: float,
                                  actuator_fired_now: bool):
        """Item #2 with AR35-1 counter-factual: did suppression block an expected fire?

        High-confidence skip = (a) within hardware suppression window AND (b) actuator didn't fire NOW
        AND (c) identical from→to transition DID fire actuator within prior 1h before suppression-window opened.
        """
        last_hw_ts = self.zone_last_hardware_fire_ts.get(zone, 0.0)
        if last_hw_ts == 0.0:
            return  # No hardware touch in this zone yet — not a suppression candidate
        if (ws_ts - last_hw_ts) > SUPPRESSION_WINDOW_S:
            return  # outside suppression window
        if actuator_fired_now:
            return  # actuator fired so not suppressed
        # AR35-1 counter-factual: did this from→to ever fire actuator BEFORE the hw_ts?
        dq = self.zone_transition_actuation_log.get(zone, deque())
        prior_fired = any(
            e["from"] == from_state and e["to"] == to_state and e["actuator_fired"] and e["ts"] < last_hw_ts
            for e in dq
        )
        confidence = "high" if prior_fired else "low"
        skip = {
            "ts": ws_ts, "zone": zone,
            "from": from_state, "to": to_state,
            "hw_touch_age_s": round(ws_ts - last_hw_ts, 1),
            "confidence": confidence,
        }
        self.suppression_skips.append(skip)
        self._write_jsonl({"kind": "suppression_skip_inferred", **skip})

    def _check_cooldown_bypass(self, zone: str, actuator_ent: str, ev_data: dict, ws_ts: float):
        """Item #7 with AR35-4: only bypass if prior last_triggered was within cooldown window."""
        ns = ev_data.get("new_state") or {}
        new_attrs = ns.get("attributes") or {}
        new_lt = new_attrs.get("last_triggered")
        if not new_lt:
            return
        prior_lt = self.actuator_last_triggered_seen.get(actuator_ent)
        # Update tracking AFTER computing (so we capture this fire's PRIOR value)
        self.actuator_last_triggered_seen[actuator_ent] = new_lt
        if not prior_lt or prior_lt == new_lt:
            return  # No actual advance
        try:
            new_ts = datetime.fromisoformat(new_lt.replace("Z", "+00:00")).timestamp()
            prior_ts = datetime.fromisoformat(prior_lt.replace("Z", "+00:00")).timestamp()
        except Exception:
            return
        cooldown_gap_s = new_ts - prior_ts
        if 0 < cooldown_gap_s < COOLDOWN_THRESHOLD_S:
            # This is a bypass — actuator fired again within the cooldown window
            self.cooldown_bypasses.append({
                "ts": ws_ts, "zone": zone, "actuator": actuator_ent,
                "cooldown_gap_s": round(cooldown_gap_s, 3),
                "prior_last_triggered": prior_lt, "new_last_triggered": new_lt,
            })
            self._write_jsonl({
                "kind": "cooldown_bypass_fired",
                "ts": ws_ts, "zone": zone,
                "cooldown_gap_s": round(cooldown_gap_s, 3),
            })

    def _schedule_guard_check(self, zone: str, ws_ts: float):
        """Item #3 AR35-2: when occupancy goes off, snapshot pre-vacant brightness per target;
        schedule a check 8s later to see if light state changed."""
        for target in TARGETS_BY_SLUG.get(zone, []):
            last = self.entity_last_state.get(target) or {}
            pre_state = last.get("state")
            attrs = last.get("attrs") or {}
            pre_bri = attrs.get("brightness")
            # Pre-vacant brightness in % (bri 0-255)
            if pre_bri is None and pre_state == "on":
                pre_bri_pct = 100.0
            elif pre_bri is None:
                pre_bri_pct = 0.0
            else:
                pre_bri_pct = min(100.0, max(0.0, pre_bri / 2.55))
            self.pending_guard_checks.append({
                "sample_ts": ws_ts + GUARD_SKIP_WAIT_S,
                "vacated_zone": zone,
                "target": target,
                "pre_bri_pct": pre_bri_pct,
                "pre_target_state": pre_state,
            })

    def _process_pending_guard_checks(self, ws_ts: float):
        """Periodic tick: process pending guard-skip checks whose sample_ts has elapsed."""
        kept: list[dict] = []
        for pg in self.pending_guard_checks:
            if pg["sample_ts"] > ws_ts:
                kept.append(pg)
                continue
            # Check: did the target's state change since pre snapshot?
            target = pg["target"]
            cur = self.entity_last_state.get(target) or {}
            cur_state = cur.get("state")
            cur_attrs = cur.get("attrs") or {}
            cur_bri = cur_attrs.get("brightness")
            if cur_bri is None and cur_state == "on":
                cur_bri_pct = 100.0
            elif cur_bri is None:
                cur_bri_pct = 0.0
            else:
                cur_bri_pct = min(100.0, max(0.0, cur_bri / 2.55))
            # Did target NOT respond?
            unchanged = (abs(cur_bri_pct - pg["pre_bri_pct"]) < 1.0) and (cur_state == pg["pre_target_state"])
            if not unchanged:
                continue  # Target DID respond — no guard skip
            # Is another zone owning this target still non-vacant?
            sibling_active = False
            blocking_zone = None
            for sibling in TARGET_TO_SLUGS.get(target, []):
                if sibling == pg["vacated_zone"]:
                    continue
                occ_ent = OCCUPANCY_BY_SLUG.get(sibling)
                sibling_occ_state = (self.entity_last_state.get(occ_ent) or {}).get("state") if occ_ent else None
                if sibling_occ_state == "on":
                    sibling_active = True
                    blocking_zone = sibling
                    break
            # AR35-2 noop disambiguation: if pre brightness ≈ predicted-vacant brightness,
            # classify as noop (no fire was expected). We approximate "predicted-vacant" via
            # the zone's classifier's predicted_brightness_pct attribute observed at last classifier event.
            classifier_ent = CLASSIFIER_BY_SLUG.get(pg["vacated_zone"])
            classifier_state = self.entity_last_state.get(classifier_ent) or {}
            predicted_vacant_bri = (classifier_state.get("attrs") or {}).get("predicted_brightness_pct")
            if isinstance(predicted_vacant_bri, (int, float)) and \
               abs(pg["pre_bri_pct"] - predicted_vacant_bri) < GUARD_VS_NOOP_BRI_PCT:
                inference_kind = "noop"
            elif sibling_active:
                inference_kind = "guard"
            else:
                inference_kind = "ambiguous"
            entry = {
                "ts": ws_ts,
                "vacated_zone": pg["vacated_zone"],
                "target": target,
                "blocking_zone": blocking_zone,
                "pre_bri_pct": round(pg["pre_bri_pct"], 1),
                "predicted_vacant_bri": predicted_vacant_bri,
                "inference_kind": inference_kind,
            }
            self.guard_skips.append(entry)
            self._write_jsonl({"kind": "guard_skip_inferred", **entry})
        self.pending_guard_checks = kept

    def _check_mirror_divergence(self, eid: str, ev_data: dict, ws_ts: float):
        """Item #8 AR35-13: Meross mirror pair divergence."""
        # Identify the partner
        partner = None
        if eid in MIRROR_PAIRS:
            partner = MIRROR_PAIRS[eid]  # light → switch
        else:
            # reverse lookup
            for light_ent, sw_ent in MIRROR_PAIRS.items():
                if eid == sw_ent:
                    partner = light_ent
                    break
        if not partner:
            return
        ns = ev_data.get("new_state") or {}
        new_state = ns.get("state")
        self.mirror_state[eid] = new_state
        partner_state = self.mirror_state.get(partner)
        if partner_state is None:
            return
        # Sustained divergence?
        # Normalize: a light "on" + switch "on" = aligned; light "off" + switch "off" = aligned.
        # Otherwise diverged.
        aligned = (new_state in ("on", "off")) and (partner_state in ("on", "off")) and (new_state == partner_state)
        # Always sort the pair-key by name for stable identity
        if eid in MIRROR_PAIRS:
            key = (eid, partner)
        else:
            key = (partner, eid)
        if aligned:
            # Cleared a previous divergence?
            start = self.mirror_diverge_start.pop(key, None)
            if start is not None:
                duration_s = ws_ts - start
                if duration_s >= MIRROR_DIVERGE_THRESHOLD_S:
                    entry = {
                        "ts": ws_ts, "pair": list(key),
                        "duration_s": round(duration_s, 2),
                        "resolved": True,
                    }
                    self.mirror_divergences.append(entry)
                    self._write_jsonl({"kind": "mirror_divergence", **entry})
        else:
            # Started diverging
            if key not in self.mirror_diverge_start:
                self.mirror_diverge_start[key] = ws_ts

    def _schedule_delta_sample(self, trace: ZoneTrace, target: str, ws_ts: float):
        """Item #5 AR35-7: schedule actual-bri/ct read after settle window."""
        # Settle = max(transition_s + 1, PREDICT_SETTLE_S) — but we don't know transition_s here;
        # use PREDICT_SETTLE_S as conservative default.
        if trace.t1_predicted_bri is None and trace.t1_predicted_ct is None:
            return
        self.pending_delta_samples.append({
            "sample_ts": ws_ts + PREDICT_SETTLE_S,
            "zone": trace.zone,
            "target": target,
            "predicted_bri": trace.t1_predicted_bri,
            "predicted_ct": trace.t1_predicted_ct,
            "trace": trace,  # reference so we can write back
        })

    def _process_pending_delta_samples(self, ws_ts: float):
        """Item #5 AR35-7: process settled samples."""
        kept: list[dict] = []
        for ps in self.pending_delta_samples:
            if ps["sample_ts"] > ws_ts:
                kept.append(ps)
                continue
            target = ps["target"]
            cur = self.entity_last_state.get(target) or {}
            cur_attrs = cur.get("attrs") or {}
            cur_bri = cur_attrs.get("brightness")
            cur_ct = cur_attrs.get("color_temp_kelvin")
            if cur_bri is None and cur.get("state") == "on":
                actual_bri_pct = 100.0
            elif cur_bri is None:
                actual_bri_pct = 0.0
            else:
                actual_bri_pct = min(100.0, max(0.0, cur_bri / 2.55))
            entry = {
                "predicted_bri": ps["predicted_bri"],
                "actual_bri_pct": round(actual_bri_pct, 1),
                "predicted_ct": ps["predicted_ct"],
                "actual_ct": cur_ct,
                "settled_ts": ws_ts,
            }
            # Write back onto the trace
            trace: ZoneTrace = ps["trace"]
            trace.delta_settled[target] = entry
            self._write_jsonl({
                "kind": "delta_sample",
                "zone": ps["zone"], "target": target,
                **entry,
            })
        self.pending_delta_samples = kept

    def _record_entity_state(self, eid: str, ev_data: dict, ws_ts: float):
        """Cache latest seen state + attrs of every watched entity (used by deferred checks)."""
        ns = ev_data.get("new_state") or {}
        self.entity_last_state[eid] = {
            "state": ns.get("state"),
            "attrs": ns.get("attributes") or {},
            "ts": ws_ts,
            "ha_ts": ns.get("last_changed"),
        }

    def _periodic_tick(self, ws_ts: float):
        """Called once per main-loop iteration to advance time-dependent checks."""
        self._process_pending_guard_checks(ws_ts)
        self._process_pending_delta_samples(ws_ts)
        self._check_frigate_heartbeat(ws_ts)
        # Auto-close MovieModeTrace if it's been open >5s — capture post-snapshot ONCE.
        # (Trace remains "live" until TV state flips to idle, but post_zone_state is fresh once.)

    # ── Main pump ──

    async def run(self):
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        self.jsonl_fh = open(self.jsonl_path, "w", encoding="utf-8")
        # Write run-header
        self._write_jsonl({
            "kind": "run_start",
            "ts": self.start_ts,
            "watchlist_count": len(WATCHLIST),
            "zones": len(ZONES),
            "duration_s": self.duration_s,
        })
        print(f"Harness - output: {self.jsonl_path.name}")
        print(f"Duration: {self.duration_s}s ({self.duration_s/60:.0f} min)")
        print(f"Watchlist: {len(WATCHLIST)} entities, {len(ZONES)} zones")

        # Pre-flight (Addendum 35 AR35-9)
        pf = await self.preflight()
        self._write_jsonl({
            "kind": "preflight",
            "master_toggles": self.master_toggle_snapshot,
            "abort_reasons": pf["abort_reasons"],
            "warnings": pf["warnings"],
            "checks_passed": pf["checks_passed"],
            "disabled_zones": pf["disabled_zones"],
            "precision_probe": pf["precision_probe"],
        })

        # Print summary
        print("Preflight:")
        for chk in pf["checks_passed"]:
            print(f"  [OK] {chk}")
        for warn in pf["warnings"]:
            print(f"  [WARN] {warn}")

        # ABORT-on-fail
        if pf["abort_reasons"]:
            print()
            print("FATAL - preflight abort:")
            for reason in pf["abort_reasons"]:
                print(f"  [FAIL] {reason}")
            self._write_jsonl({"kind": "abort", "reasons": pf["abort_reasons"], "ts": time.time()})
            try:
                self.jsonl_fh.close()
            except Exception:
                pass
            raise SystemExit(3)

        print()
        print(f"Master toggles ({len(self.master_toggle_snapshot)}):")
        for ent, s in sorted(self.master_toggle_snapshot.items()):
            tag = ""
            if s not in ("on", "off"):
                tag = " ⚠"
            elif ent.endswith("_enabled") and s == "off":
                tag = " (zone disabled)"
            print(f"  {ent} = {s}{tag}")
        print()
        print(">>> WALK SCRIPT: move through every room twice, sit on sofa 3+ min, kitchen 1 min,")
        print(">>>              dining 1 min, open front door briefly, end seated 5 min.")
        print(">>>              Don't touch Pilar/Aurora/Aqara knobs (they tag as hardware-origin).")
        print()

        deadline = self.start_ts + self.duration_s
        last_progress = 0
        try:
            await self.connect()
            print(f"WS connected. Listening for {self.duration_s}s...")
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                # Progress tick every 60s
                if time.time() - self.start_ts - last_progress > 60:
                    last_progress = time.time() - self.start_ts
                    elapsed_min = last_progress / 60
                    flicker_top = "-"
                    if self.flicker_log:
                        top_ent, top_list = max(
                            ((e, lst) for e, lst in self.flicker_log.items() if lst),
                            key=lambda x: len(x[1]),
                            default=(None, []),
                        )
                        if top_ent:
                            flicker_top = f"{top_ent}({len(top_list)})"
                    # Top flutter entity right now
                    flutter_top = "-"
                    if self.flutter_state:
                        top_ent, top_st = max(
                            ((e, st) for e, st in self.flutter_state.items() if st["reversals"] > 0),
                            key=lambda x: x[1]["reversals"],
                            default=(None, None),
                        )
                        if top_ent and top_st:
                            flutter_top = f"{top_ent.split('.')[-1]}({top_st['reversals']}r,pk{top_st['peak_60s']}/m)"
                    inc_count = sum(1 for t in self.closed_traces if not (set(t.target_responses.keys()) >= set(TARGETS_BY_SLUG.get(t.zone, []))))
                    print(f"[T+{elapsed_min:.1f}m] events:{self.event_count} traces:{len(self.closed_traces)} incomp:{inc_count} top-flick:{flicker_top} top-flutter:{flutter_top}")

                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=min(5.0, remaining))
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    print(f"WS disconnected. Reconnecting...")
                    self._gap_count += 1
                    self._write_jsonl({"kind": "ws_gap", "ts": time.time(), "gap_n": self._gap_count})
                    await asyncio.sleep(min(30, 2 ** self._gap_count))
                    try:
                        await self.connect()
                    except Exception as e:
                        print(f"  reconnect failed: {e}")
                        continue
                    continue

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") != "event":
                    continue

                ev = msg.get("event") or {}
                ev_type = ev.get("event_type")
                ev_data = ev.get("data") or {}
                ws_ts = time.time()
                self.event_count += 1

                # Addendum 35 periodic tick (process deferred samples + heartbeats)
                self._periodic_tick(ws_ts)

                if ev_type == "call_service":
                    self._attach_service_call(ev_data)
                    # Log the call_service raw
                    self._write_jsonl({
                        "kind": "call_service", "ts": ws_ts,
                        "domain": ev_data.get("domain"),
                        "service": ev_data.get("service"),
                        "data": ev_data.get("service_data"),
                    })
                    continue

                if ev_type == "frigate_event":
                    # Addendum 35 item #4 — upstream Frigate detection latency
                    self._handle_frigate_event(ev_data, ev.get("time_fired"), ws_ts)
                    continue

                if ev_type != "state_changed":
                    continue

                eid = ev_data.get("entity_id", "")
                if not self._entity_in_watchlist(eid):
                    continue

                # Always JSONL-log watchlist events
                ns = ev_data.get("new_state") or {}
                os_ = ev_data.get("old_state") or {}
                row = {
                    "kind": "state_changed",
                    "received_ts": ws_ts,
                    "ha_time_fired": ev.get("time_fired"),
                    "entity_id": eid,
                    "old_state": os_.get("state"),
                    "new_state": ns.get("state"),
                    "ha_last_changed": ns.get("last_changed"),
                    "ha_last_updated": ns.get("last_updated"),
                }
                # Pluck a few attrs that matter
                attrs = ns.get("attributes") or {}
                for k in ("brightness", "color_temp_kelvin", "predicted_brightness_pct",
                          "predicted_color_temp_kelvin", "last_triggered", "dwell_ms"):
                    if k in attrs:
                        row[f"attr_{k}"] = attrs[k]
                self._write_jsonl(row)

                # Addendum 35: cache entity state for deferred checks
                self._record_entity_state(eid, ev_data, ws_ts)

                # Dispatch
                # Hardware automation fired?
                if eid in HARDWARE_AUTOMATIONS and os_.get("state") != ns.get("state"):
                    # last_triggered changed via state_changed
                    new_lt = attrs.get("last_triggered")
                    old_lt = (os_.get("attributes") or {}).get("last_triggered")
                    if new_lt != old_lt:
                        self.last_hardware_fire_ts = ws_ts
                        # Addendum 35 item #2: per-zone touch tracking
                        self._handle_per_zone_hardware_fire(eid, ws_ts)

                # Master toggle flip?
                if eid in MASTER_TOGGLES:
                    if os_.get("state") != ns.get("state"):
                        self.master_toggle_flips.append({
                            "ts": ws_ts, "entity_id": eid,
                            "from": os_.get("state"), "to": ns.get("state"),
                        })

                # Apple TV — Addendum 35 item #1
                if eid == APPLE_TV_ENTITY:
                    self._handle_apple_tv(ev_data, ws_ts)
                    # No further dispatch needed for media_player
                    continue

                # Occupancy?
                if eid in SLUG_BY_OCCUPANCY:
                    zone = SLUG_BY_OCCUPANCY[eid]
                    new_s = ns.get("state")
                    old_s = os_.get("state")
                    self._recent_occ_change_by_zone[zone] = ws_ts
                    if old_s != "on" and new_s == "on":
                        # Check spurious-loss: was there a recent off in this zone?
                        recent_off_ts = self._recent_off.pop(zone, None)
                        if recent_off_ts and (ws_ts - recent_off_ts) <= 10:
                            self.vacancy_loss_log[zone].append(("spurious", recent_off_ts, ws_ts))
                        self._open_or_extend_trace(zone, ev_data, ws_ts)
                        # Addendum 35 item #4: correlate frigate_event → occupancy latency
                        self._attach_frigate_latency_on_occupancy(zone, new_s, ns.get("last_changed"), ws_ts)
                    elif old_s == "on" and new_s != "on":
                        self._recent_off[zone] = ws_ts
                        # Addendum 35 item #3: schedule guard-skip check before closing trace
                        self._schedule_guard_check(zone, ws_ts)
                        self._close_trace(zone, "occupancy_off", ws_ts)

                # Classifier?
                elif eid in SLUG_BY_CLASSIFIER:
                    zone = SLUG_BY_CLASSIFIER[eid]
                    old_s = os_.get("state")
                    new_s = ns.get("state")
                    if old_s != new_s:
                        self.classifier_transitions[zone].append((ws_ts, old_s, new_s))
                        self.last_classifier_state[zone] = new_s
                        # Addendum 35 item #1: record into live movie trace if active
                        self._record_classifier_for_movie_trace(zone, new_s, ns.get("last_changed"))
                        # Addendum 35 item #2 AR35-1: counter-factual transition log
                        actuator_ent = ACTUATOR_BY_SLUG.get(zone)
                        actuator_last_triggered_iso = None
                        if actuator_ent:
                            actuator_state = self.entity_last_state.get(actuator_ent) or {}
                            actuator_last_triggered_iso = (actuator_state.get("attrs") or {}).get("last_triggered")
                        self._record_classifier_transition_for_counterfactual(
                            zone, old_s, new_s, ws_ts, actuator_last_triggered_iso,
                        )
                        # Addendum 35 item #2: did suppression block an expected fire?
                        # actuator_fired_now ≈ actuator last_triggered advanced within 2s of this transition
                        actuator_fired_now = False
                        if actuator_last_triggered_iso:
                            try:
                                lt_ts = datetime.fromisoformat(actuator_last_triggered_iso.replace("Z", "+00:00")).timestamp()
                                actuator_fired_now = abs(ws_ts - lt_ts) < 2.0
                            except Exception:
                                pass
                        self._check_suppression_skip(zone, old_s, new_s, ws_ts, actuator_fired_now)
                    self._attach_classifier(zone, ev_data)

                # Actuator?
                elif eid in SLUG_BY_ACTUATOR:
                    zone = SLUG_BY_ACTUATOR[eid]
                    self._attach_actuator(zone, ev_data)
                    # Addendum 35 item #7: cooldown-bypass detection
                    self._check_cooldown_bypass(zone, eid, ev_data, ws_ts)
                    # Addendum 35 item #5: schedule predicted-vs-actual delta sample
                    trace = self.live_traces.get(zone)
                    if trace:
                        for target in TARGETS_BY_SLUG.get(zone, []):
                            self._schedule_delta_sample(trace, target, ws_ts)

                # Light/switch target?
                elif eid in ALL_TARGETS:
                    self._attach_target_response(eid, ev_data, ws_ts)
                    self._flicker_register(eid, ev_data, ws_ts)
                    # Addendum 35 item #8: mirror divergence
                    self._check_mirror_divergence(eid, ev_data, ws_ts)
                    # Addendum 35 item #1: light dim correlations during movie trace
                    if self.live_movie_trace is not None and eid not in self.live_movie_trace.target_dim_ha_ts:
                        self.live_movie_trace.target_dim_ha_ts[eid] = ns.get("last_changed") or ""

        finally:
            # Close any live traces
            for zone in list(self.live_traces.keys()):
                self._close_trace(zone, "run_end", time.time())
            # Write run-end + master-toggle flips
            self._write_jsonl({
                "kind": "run_end",
                "ts": time.time(),
                "event_count": self.event_count,
                "ws_gaps": self._gap_count,
                "master_toggle_flips": self.master_toggle_flips,
                "vacancy_loss_log": dict(self.vacancy_loss_log),
            })
            if self.jsonl_fh:
                self.jsonl_fh.close()
            if self.ws:
                try:
                    await self.ws.close()
                except Exception:
                    pass

    # ── Report ──

    def write_report(self) -> Path:
        out = self.md_path
        ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        elapsed_s = time.time() - self.start_ts

        lines: list[str] = []
        lines.append(f"# Lighting latency + flicker — {ts_str}")
        lines.append(f"_Run duration: {elapsed_s/60:.1f} min · Events captured: {self.event_count} · WS gaps: {self._gap_count}_")
        lines.append("")

        # Master toggle health
        lines.append("## Run-config health")
        for ent, s in sorted(self.master_toggle_snapshot.items()):
            lines.append(f"- {ent} = `{s}` (at start)")
        if self.master_toggle_flips:
            lines.append("")
            lines.append(f"**⚠ Master toggles flipped mid-run ({len(self.master_toggle_flips)} events):**")
            for f in self.master_toggle_flips:
                lines.append(f"  - {f['entity_id']}: {f['from']} → {f['to']} at T+{f['ts']-self.start_ts:.0f}s")
        else:
            lines.append("")
            lines.append("- ✓ No mid-run toggle flips")
        lines.append("")

        # Per-zone latency table
        lines.append("## Per-zone latency (T0=occupancy on, T3=target light responded)")
        lines.append("")
        lines.append("| Zone | Traces | Complete | T0→T1 p50 | T0→T1 p95 | T0→T2 p50 | T0→T2 p95 | T0→T3 p50 | T0→T3 p95 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for zone in [z[0] for z in ZONES if z[4]]:
            traces = [t for t in self.closed_traces if t.zone == zone]
            expected = set(TARGETS_BY_SLUG.get(zone, []))
            complete = [t for t in traces if expected and set(t.target_responses.keys()) >= expected]
            t01s = [_dt_ms(t.t1_ha_ts, t.opened_ha_ts) for t in traces]
            t02s = [_dt_ms(t.t2_ha_ts, t.opened_ha_ts) for t in traces]
            t03s = []
            for t in traces:
                if t.target_responses and t.opened_ha_ts:
                    earliest = min((r.get("ha_ts") for r in t.target_responses.values() if r.get("ha_ts")), default=None)
                    if earliest:
                        t03s.append(_dt_ms(earliest, t.opened_ha_ts))
            lines.append(f"| {zone} | {len(traces)} | {len(complete)} | {_p(t01s,50)} | {_p(t01s,95)} | {_p(t02s,50)} | {_p(t02s,95)} | {_p(t03s,50)} | {_p(t03s,95)} |")
        lines.append("")

        # Trace completeness: which targets missed
        lines.append("## Trace completeness — per zone, which targets missed?")
        lines.append("")
        lines.append("| Zone | Traces | Complete | Missing-target patterns |")
        lines.append("|---|---|---|---|")
        for zone in [z[0] for z in ZONES if z[4]]:
            traces = [t for t in self.closed_traces if t.zone == zone]
            expected = set(TARGETS_BY_SLUG.get(zone, []))
            complete = [t for t in traces if expected and set(t.target_responses.keys()) >= expected]
            incomplete = [t for t in traces if expected and not (set(t.target_responses.keys()) >= expected)]
            miss_patterns = defaultdict(int)
            for t in incomplete:
                missing = tuple(sorted(expected - set(t.target_responses.keys())))
                miss_patterns[missing] += 1
            patterns_str = "; ".join(f"missing {list(m)} ×{c}" for m, c in sorted(miss_patterns.items(), key=lambda x: -x[1]))
            lines.append(f"| {zone} | {len(traces)} | {len(complete)} | {patterns_str or '-'} |")
        lines.append("")

        # Flicker leaderboard (state on/off flips only)
        lines.append("## Flicker leaderboard — state on/off flips in run window")
        lines.append("")
        lines.append("| Entity | Total flips | Living-lights origin | Hardware origin | Brightness changes |")
        lines.append("|---|---|---|---|---|")
        flicker_rows = []
        for ent, log in self.flicker_log.items():
            total = len(log)
            ll = sum(1 for _, _, _, o in log if o == "living_lights")
            hw = sum(1 for _, _, _, o in log if o == "hardware")
            bri_steps = len(self.bri_step_log.get(ent, []))
            flicker_rows.append((ent, total, ll, hw, bri_steps))
        for row in sorted(flicker_rows, key=lambda x: -x[1])[:15]:
            ent, tot, ll, hw, bs = row
            flag = " ⚠" if tot > 20 else ""
            lines.append(f"| {ent}{flag} | {tot} | {ll} | {hw} | {bs} |")
        if not flicker_rows:
            lines.append("| (no flicker captured) | - | - | - | - |")
        lines.append("")

        # FLUTTER — direction-reversal in the brightness signal
        # (the "starts dimming then bounces back up then back down" pattern)
        lines.append("## Flutter leaderboard — brightness direction-reversals")
        lines.append("")
        lines.append(f"_Smoothing: ignore brightness changes < {self.FLUTTER_IGNORE_PCT:.0f}% as noise._")
        lines.append(f"_Peak/60s: most reversals in any 60-second sliding window._")
        lines.append("")
        lines.append("| Entity | Reversals | Peak/60s | Reversals/min | Smoothed samples | Service-call thrashes |")
        lines.append("|---|---|---|---|---|---|")
        run_min = elapsed_s / 60.0 if elapsed_s > 0 else 1.0
        # Compute service-call thrash per entity (clusters of N>=2 calls within WINDOW_S)
        thrash_count: dict[str, int] = {}
        for ent, calls in self.service_call_log.items():
            calls_sorted = sorted(calls, key=lambda x: x[0])
            cluster_size = 1
            for i in range(1, len(calls_sorted)):
                if calls_sorted[i][0] - calls_sorted[i-1][0] <= self.SERVICE_THRASH_WINDOW_S:
                    cluster_size += 1
                else:
                    if cluster_size > 1:
                        thrash_count[ent] = thrash_count.get(ent, 0) + (cluster_size - 1)
                    cluster_size = 1
            if cluster_size > 1:
                thrash_count[ent] = thrash_count.get(ent, 0) + (cluster_size - 1)
        flutter_rows = []
        for ent, st in self.flutter_state.items():
            rev = st["reversals"]
            pk = st["peak_60s"]
            sc = len(self.flutter_samples.get(ent, []))
            th = thrash_count.get(ent, 0)
            if rev > 0 or th > 0:
                flutter_rows.append((ent, rev, pk, sc, th))
        for ent, rev, pk, sc, th in sorted(flutter_rows, key=lambda x: -x[1] - 0.01 * x[4])[:15]:
            rpm = rev / run_min if run_min > 0 else 0
            flag = " ⚠" if rev > 10 or pk > 5 else ""
            lines.append(f"| {ent}{flag} | {rev} | {pk} | {rpm:.1f} | {sc} | {th} |")
        if not flutter_rows:
            lines.append("| (no flutter detected) | - | - | - | - | - |")
        lines.append("")

        # Top-flutter brightness traces (visual evidence)
        if flutter_rows:
            top3 = sorted(flutter_rows, key=lambda x: -x[1])[:3]
            lines.append("### Top-flutter brightness traces (first 30 smoothed samples)")
            lines.append("")
            for ent, rev, pk, sc, th in top3:
                samples = self.flutter_samples.get(ent, [])
                if not samples:
                    continue
                lines.append(f"**{ent}** — {len(samples)} smoothed samples ({rev} reversals, peak {pk}/min)")
                lines.append("```")
                t0 = samples[0][0]
                for ts, bri in samples[:30]:
                    rel = ts - t0
                    bar = "█" * int(bri / 5)
                    lines.append(f"  t+{rel:7.1f}s  {bri:5.1f}%  {bar}")
                if len(samples) > 30:
                    lines.append(f"  ... {len(samples) - 30} more samples")
                lines.append("```")
                lines.append("")

        # Spurious-vacancy leaderboard
        lines.append("## Spurious-vacancy leaderboard (Frigate dropping mid-presence)")
        lines.append("")
        lines.append("| Zone | Off events | Re-detected within 10s (spurious) | Spurious rate |")
        lines.append("|---|---|---|---|")
        for zone, losses in sorted(self.vacancy_loss_log.items(), key=lambda x: -len(x[1])):
            n = len(losses)
            sp = sum(1 for kind, *_ in losses if kind == "spurious")
            rate = (sp / max(1, n)) * 100
            flag = " ⚠" if rate > 30 else ""
            lines.append(f"| {zone}{flag} | {n} | {sp} | {rate:.0f}% |")
        if not self.vacancy_loss_log:
            lines.append("| (no spurious losses detected) | - | - | - |")
        lines.append("")

        # Classifier transition pattern histogram (most common 3-grams)
        lines.append("## Classifier 3-transition patterns per zone (most common)")
        lines.append("")
        lines.append("| Zone | Top patterns |")
        lines.append("|---|---|")
        for zone in [z[0] for z in ZONES if z[4]]:
            seq = [s2 for _, _, s2 in self.classifier_transitions.get(zone, [])]
            if len(seq) < 3:
                lines.append(f"| {zone} | (only {len(seq)} transitions) |")
                continue
            triples = defaultdict(int)
            for i in range(len(seq) - 2):
                triples[(seq[i], seq[i+1], seq[i+2])] += 1
            top = sorted(triples.items(), key=lambda x: -x[1])[:3]
            lines.append(f"| {zone} | " + "; ".join(f"`{'→'.join(t)}` ×{c}" for t, c in top) + " |")
        lines.append("")

        # ═══ Addendum 35 sections ═══

        # Movie_mode traces (item #1)
        lines.append("## Movie_mode traces (Apple TV → 6 living_room zones)")
        lines.append("")
        if not self.closed_movie_traces:
            lines.append("(no Apple TV state transitions during run)")
        else:
            lines.append("| Opened | Open→close | Zones flipped (LR) | Missed (LR) | False-positive flips |")
            lines.append("|---|---|---|---|---|")
            for mt in self.closed_movie_traces:
                dur = ((mt.closed_ts or mt.opened_ts) - mt.opened_ts)
                opened_iso = datetime.fromtimestamp(mt.opened_ts, tz=timezone.utc).strftime("%H:%M:%S")
                lr_flipped = sorted(z for z in mt.zone_flip_ha_ts.keys() if z in LIVING_ROOM_ZONES)
                lr_missed = sorted(z for z in LIVING_ROOM_ZONES if z not in mt.zone_flip_ha_ts)
                fp = sorted(mt.false_positive_flips) or "-"
                lines.append(f"| {opened_iso} | {dur:.1f}s | {lr_flipped} ({len(lr_flipped)}/6) | {lr_missed} | {fp} |")
        lines.append("")

        # Suppression-skip table (item #2 with AR35-1 high/low confidence)
        lines.append("## Hardware-touch suppression skips (item #2)")
        lines.append("")
        if not self.suppression_skips:
            lines.append("(no hardware-touch suppression skips inferred — either no knob touches OR no transitions during the 60s window)")
        else:
            by_zone_conf: dict[tuple[str, str], int] = defaultdict(int)
            for sk in self.suppression_skips:
                by_zone_conf[(sk["zone"], sk["confidence"])] += 1
            lines.append("| Zone | High-conf | Low-conf | Total |")
            lines.append("|---|---|---|---|")
            zones_seen = sorted({sk["zone"] for sk in self.suppression_skips})
            for zone in zones_seen:
                hi = by_zone_conf.get((zone, "high"), 0)
                lo = by_zone_conf.get((zone, "low"), 0)
                lines.append(f"| {zone} | {hi} | {lo} | {hi+lo} |")
            lines.append("")
            lines.append("_High-conf = identical from→to fired actuator within prior 1h before knob touch; low-conf = no prior precedent._")
        lines.append("")

        # Guard-skip table (item #3 with AR35-2 noop disambiguation)
        lines.append("## Multi-zone vacant-guard skips (item #3)")
        lines.append("")
        if not self.guard_skips:
            lines.append("(no multi-zone guard skips inferred)")
        else:
            kind_counts: dict[tuple[str, str, str], int] = defaultdict(int)
            for gs in self.guard_skips:
                kind_counts[(gs["vacated_zone"], gs.get("blocking_zone") or "-", gs["inference_kind"])] += 1
            lines.append("| Vacated zone | Blocking zone | Target | Inference | Count |")
            lines.append("|---|---|---|---|---|")
            tally: dict[tuple, int] = defaultdict(int)
            for gs in self.guard_skips:
                key = (gs["vacated_zone"], gs.get("blocking_zone") or "-", gs["target"], gs["inference_kind"])
                tally[key] += 1
            for (vz, bz, tgt, kind), n in sorted(tally.items(), key=lambda x: -x[1])[:20]:
                lines.append(f"| {vz} | {bz} | {tgt} | {kind} | {n} |")
            lines.append("")
            lines.append("_`guard` = sibling zone non-vacant + brightness DID NOT match predicted-vacant; `noop` = brightness already matches predicted-vacant; `ambiguous` = no sibling active or unclear cause._")
        lines.append("")

        # Frigate event latency (item #4 with AR35-3 heartbeat)
        lines.append("## Frigate event → HA state_changed latency (item #4)")
        lines.append("")
        if not self.frigate_event_to_state_latencies:
            if self.frigate_event_log:
                lines.append(f"(received {len(self.frigate_event_log)} frigate_events but no occupancy correlation — entered_zones may not match watchlist)")
            else:
                lines.append("(no frigate_event events received from HA during this run)")
        else:
            lines.append("| Camera | Samples | p50 latency | p95 latency | min | max |")
            lines.append("|---|---|---|---|---|---|")
            for cam, latencies in sorted(self.frigate_event_to_state_latencies.items()):
                if not latencies:
                    continue
                p50 = statistics.median(latencies)
                sorted_l = sorted(latencies)
                p95 = sorted_l[max(0, int(0.95 * len(sorted_l)) - 1)]
                lines.append(f"| {cam} | {len(latencies)} | {p50:.0f}ms | {p95:.0f}ms | {min(latencies):.0f}ms | {max(latencies):.0f}ms |")
            if self.frigate_subscription_dead_warnings:
                lines.append("")
                lines.append(f"⚠ Frigate subscription appears dead {len(self.frigate_subscription_dead_warnings)}× (occupancy moved without matching frigate_event for ≥5min)")
        lines.append("")

        # Predicted-vs-actual delta (item #5 with AR35-7 settle)
        lines.append("## Predicted-vs-actual brightness delta (item #5)")
        lines.append("")
        all_deltas_by_zone: dict[str, list[float]] = defaultdict(list)
        for t in self.closed_traces:
            for target, ds in (t.delta_settled or {}).items():
                pb = ds.get("predicted_bri")
                ab = ds.get("actual_bri_pct")
                if isinstance(pb, (int, float)) and isinstance(ab, (int, float)):
                    all_deltas_by_zone[t.zone].append(abs(ab - pb))
        if not all_deltas_by_zone:
            lines.append("(no delta samples — settle window may have exceeded run duration OR no traces had predicted attrs)")
        else:
            lines.append("| Zone | Samples | Median |delta| (%) | Max |delta| (%) |")
            lines.append("|---|---|---|---|")
            for zone in sorted(all_deltas_by_zone.keys()):
                deltas = all_deltas_by_zone[zone]
                med = statistics.median(deltas)
                mx = max(deltas)
                flag = " ⚠" if med > 20 else ""
                lines.append(f"| {zone} | {len(deltas)} | {med:.1f}{flag} | {mx:.1f} |")
        lines.append("")

        # Per-transition-pair latency (item #6 / AR35-8 fuller taxonomy)
        lines.append("## Per-transition-pair latency (item #6 fuller taxonomy)")
        lines.append("")
        pair_by_zone: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for t in self.closed_traces:
            if not (t.from_state and t.to_state):
                continue
            t01 = _dt_ms(t.t1_ha_ts, t.opened_ha_ts)
            if t01 is None or t01 < 0:
                continue
            pair_by_zone[(t.zone, t.from_state, t.to_state)].append(t01)
        if not pair_by_zone:
            lines.append("(no transition-pair data — traces may lack from/to states)")
        else:
            lines.append("| Zone | from→to | N | T0→T1 p50 | T0→T1 p95 |")
            lines.append("|---|---|---|---|---|")
            top_pairs = sorted(pair_by_zone.items(), key=lambda x: -len(x[1]))[:25]
            for (zone, frm, to), vals in top_pairs:
                p50 = statistics.median(vals)
                sv = sorted(vals)
                p95 = sv[max(0, int(0.95 * len(sv)) - 1)]
                lines.append(f"| {zone} | {frm}→{to} | {len(vals)} | {p50:.0f}ms | {p95:.0f}ms |")
        lines.append("")

        # Cooldown bypass (item #7 with AR35-4 prior-state)
        lines.append("## Cooldown bypass (item #7)")
        lines.append("")
        if not self.cooldown_bypasses:
            lines.append("(no cooldown bypasses detected — actuators never re-fired within 5s cooldown window)")
        else:
            by_zone: dict[str, list[dict]] = defaultdict(list)
            for cb in self.cooldown_bypasses:
                by_zone[cb["zone"]].append(cb)
            lines.append("| Zone | Bypasses | Avg cooldown gap | Min gap | Max gap |")
            lines.append("|---|---|---|---|---|")
            for zone in sorted(by_zone.keys()):
                evts = by_zone[zone]
                gaps = [e["cooldown_gap_s"] for e in evts]
                lines.append(f"| {zone} | {len(evts)} | {statistics.mean(gaps):.2f}s | {min(gaps):.2f}s | {max(gaps):.2f}s |")
        lines.append("")

        # Mirror divergence (item #8 AR35-13)
        lines.append("## Meross mirror divergence (item #8)")
        lines.append("")
        if not self.mirror_divergences:
            lines.append("(no sustained ≥2s mirror divergence detected — Meross light/switch pairs stayed aligned)")
        else:
            lines.append("| Pair | Divergences | Max duration |")
            lines.append("|---|---|---|")
            by_pair: dict[tuple, list[float]] = defaultdict(list)
            for md in self.mirror_divergences:
                key = tuple(md["pair"])
                by_pair[key].append(md["duration_s"])
            for pair, durs in sorted(by_pair.items(), key=lambda x: -len(x[1])):
                lines.append(f"| {pair[0]} ↔ {pair[1]} | {len(durs)} | {max(durs):.1f}s |")
        lines.append("")

        # Dwell-freeze pass/fail (item #10 / AR35-5)
        lines.append("## Dwell-freeze invariant (item #10)")
        lines.append("")
        if not self.dwell_freeze_results:
            lines.append("(no dwell-freeze samples — classifier may not expose dwell_ms attribute, OR no traces closed cleanly)")
        else:
            per_zone: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0, "max_delta": 0})
            for r in self.dwell_freeze_results:
                z = r["zone"]
                if r["freeze_ok"]:
                    per_zone[z]["pass"] += 1
                else:
                    per_zone[z]["fail"] += 1
                if r["delta_ms"] > per_zone[z]["max_delta"]:
                    per_zone[z]["max_delta"] = r["delta_ms"]
            lines.append("| Zone | Pass | Fail | Pass ratio | Max delta_ms |")
            lines.append("|---|---|---|---|---|")
            for z in sorted(per_zone.keys()):
                p = per_zone[z]["pass"]; f = per_zone[z]["fail"]
                ratio = p / max(1, p+f)
                flag = " ⚠" if ratio < 0.8 else ""
                lines.append(f"| {z} | {p} | {f} | {ratio*100:.0f}%{flag} | {per_zone[z]['max_delta']:.0f} |")
            lines.append("")
            lines.append(f"_Pass = dwell_ms delta < {DWELL_FREEZE_THRESHOLD_MS}ms between trace open + close. Failures indicate dwell_ms continued counting up during the 20s grace window (freeze broken)._")
        lines.append("")

        # ═══ end Addendum 35 sections ═══

        # Auto-recommendations
        lines.append("## Auto-recommendations")
        lines.append("")
        recs: list[str] = []
        if not self.closed_traces and not self.live_traces:
            if self.frigate_event_log:
                recs.append("**No occupancy traces**: HA received Frigate bus events, but none became watched `binary_sensor.<zone>_person_occupancy` transitions. Check zone names/object labels in `frigate/events` against the Living Lights watchlist.")
            else:
                recs.append("**No perception evidence**: no watched occupancy transitions and no `frigate_event` bus events arrived during the run. If someone was moving through an instrumented room, inspect Frigate's live events/API first; HA had no person-zone signal to feed the lighting classifier.")
        # Rule 1a: high flicker (state on/off)
        for ent, tot, ll, hw, _ in sorted(flicker_rows, key=lambda x: -x[1])[:5]:
            if tot > 20:
                recs.append(f"**High flicker**: `{ent}` flipped {tot}× in 30min ({ll} from living_lights, {hw} from hardware). "
                            f"Likely the matching actuator is firing on every classifier oscillation. "
                            f"Investigate matching zone's classifier transitions + add hysteresis.")
        # Rule 1b: high flutter (brightness direction-reversal)
        for ent, rev, pk, _, th in sorted(flutter_rows, key=lambda x: -x[1])[:5]:
            if rev > 10 or pk > 5:
                recs.append(f"**High flutter**: `{ent}` reversed brightness direction {rev}× total (peak {pk}/min, {th} service-call thrashes). "
                            f"The actuator is dispatching multiple `light.turn_on` calls with different brightness targets within 3s windows. "
                            f"Root cause is likely classifier ping-ponging between active_presence/linger/vacant. Consider: "
                            f"(a) dwell hysteresis (linger sticks until 15s+ vacancy), "
                            f"(b) per-zone cooldown after actuation (no re-fire for 2s), "
                            f"(c) raise Frigate `zone_inertia` for that zone's camera.")
        # Rule 2: missing T3
        for zone in [z[0] for z in ZONES if z[4]]:
            traces = [t for t in self.closed_traces if t.zone == zone]
            expected = set(TARGETS_BY_SLUG.get(zone, []))
            incomplete = [t for t in traces if expected and not (set(t.target_responses.keys()) >= expected)]
            if len(incomplete) >= 3:
                missing_set: set[str] = set()
                for t in incomplete:
                    missing_set.update(expected - set(t.target_responses.keys()))
                recs.append(f"**Actuator targets missing**: zone `{zone}` had {len(incomplete)} traces where these targets never responded: {sorted(missing_set)}. "
                            f"Check the actuator's service call payload + entity-id correctness.")
        # Rule 3: spurious-off rate
        for zone, losses in self.vacancy_loss_log.items():
            n = len(losses)
            sp = sum(1 for kind, *_ in losses if kind == "spurious")
            if n >= 5 and (sp / n) > 0.3:
                recs.append(f"**Frigate spurious losses**: zone `{zone}` lost occupancy {sp}/{n} times briefly. "
                            f"Try raising `zone_inertia` for {zone}'s camera or lowering `min_area`.")
        # Rule 4: master toggle flips
        if self.master_toggle_flips:
            recs.append(f"**Config instability**: master toggles flipped {len(self.master_toggle_flips)} times mid-run. "
                        f"Re-run harness with stable config for clean baseline.")
        # Rule 5: WS gaps
        if self._gap_count > 0:
            recs.append(f"**WS reconnects**: {self._gap_count} disconnects during run. Latency data in those gaps is missing.")

        # ─── Addendum 35 rules ─────────────────────────────────────────

        # Rule 7: Apple TV played but movie_mode never triggered
        # — A trace exists but no living_room zones flipped to movie_mode
        for mt in self.closed_movie_traces:
            lr_flipped = sum(1 for z in mt.zone_flip_ha_ts.keys() if z in LIVING_ROOM_ZONES)
            if lr_flipped == 0:
                recs.append(f"**Apple TV played but movie_mode never triggered**: trace opened at {datetime.fromtimestamp(mt.opened_ts, tz=timezone.utc).strftime('%H:%M:%S')} but 0/6 living_room zones flipped. "
                            f"Check `sensor.{{cam}}_{{zone}}_lighting_state` classifier conditions + `input_boolean.homeai_movie` wiring.")
                break  # only need to surface once

        # Rule 8: Multi-zone guard skipped N times (informational; not a bug if expected)
        guard_count = sum(1 for g in self.guard_skips if g["inference_kind"] == "guard")
        if guard_count > 5:
            recs.append(f"**Multi-zone guard active**: vacant-guard skipped actuator turn_off `{guard_count}` times where a sibling zone was active. "
                        f"This is healthy behavior IF the lights are shared across zones. Review TARGET_TO_SLUGS mapping if unexpected.")

        # Rule 9: Hardware suppression skipped actuator (high-confidence)
        hi_supp = sum(1 for s in self.suppression_skips if s["confidence"] == "high")
        if hi_supp > 0:
            recs.append(f"**Hardware-touch suppression confirmed**: blocked actuator fire `{hi_supp}` times (high-confidence — identical transition fired before knob touch). "
                        f"This proves the 60s gate is working.")

        # Rule 10: Light stuck on after vacant grace
        for ent, info in self.entity_last_state.items():
            if ent not in ALL_TARGETS:
                continue
            if info.get("state") != "on":
                continue
            ts_of_change = info.get("ts") or 0
            age_s = time.time() - ts_of_change
            if age_s < 120:
                continue
            # Is the OWNING zone vacant > 60s?
            owning_zones = TARGET_TO_SLUGS.get(ent, [])
            all_vacant_long = True
            for oz in owning_zones:
                cls_ent = CLASSIFIER_BY_SLUG.get(oz)
                cls_state = self.entity_last_state.get(cls_ent) or {}
                if cls_state.get("state") != "vacant":
                    all_vacant_long = False
                    break
                cls_age = time.time() - (cls_state.get("ts") or 0)
                if cls_age < 60:
                    all_vacant_long = False
                    break
            if all_vacant_long:
                recs.append(f"**Light stuck on after vacant**: `{ent}` is on but all owning zones vacant >60s. "
                            f"Actuator may have failed to dispatch vacant turn_off, OR guard skip blocked it.")
                break  # one per run is enough

        # Rule 11: Predicted-vs-actual delta >20%
        big_delta_zones = []
        for zone in sorted(all_deltas_by_zone.keys()):
            deltas = all_deltas_by_zone[zone]
            if statistics.median(deltas) > 20:
                big_delta_zones.append((zone, statistics.median(deltas), len(deltas)))
        for zone, med, n in big_delta_zones:
            recs.append(f"**Actuator config drift**: zone `{zone}` shows median |predicted-actual| delta of {med:.1f}% across {n} samples. "
                        f"Either: (a) actuator emits different brightness than template predicts, (b) device caps/rounds, (c) transition still in progress past settle window.")

        # Rule 12: Cooldown bypass ratio (informational)
        total_entries_in_cd = 0
        for zone in [z[0] for z in ZONES if z[4]]:
            actuator_ent = ACTUATOR_BY_SLUG.get(zone)
            if not actuator_ent:
                continue
            # Count classifier transitions where prior actuator last_triggered was within cooldown
            # (rough proxy via traces' pre_actuator_last_triggered + opened_ha_ts)
            for t in self.closed_traces:
                if t.zone != zone or not t.pre_actuator_last_triggered or not t.opened_ha_ts:
                    continue
                try:
                    lt = datetime.fromisoformat(t.pre_actuator_last_triggered.replace("Z", "+00:00")).timestamp()
                    op = datetime.fromisoformat(t.opened_ha_ts.replace("Z", "+00:00")).timestamp()
                    if 0 < (op - lt) < COOLDOWN_THRESHOLD_S:
                        total_entries_in_cd += 1
                except Exception:
                    pass
        bypass_count = len(self.cooldown_bypasses)
        if total_entries_in_cd > 0:
            ratio = bypass_count / total_entries_in_cd
            if ratio < 0.5 and total_entries_in_cd >= 3:
                recs.append(f"**Cooldown bypass under-firing**: {bypass_count} bypass events for {total_entries_in_cd} entries-in-cooldown ({ratio*100:.0f}%). "
                            f"Bypass should fire on most cooldown-clashing entries; investigate why these were blocked.")

        # Rule 13: Mirror divergence
        if self.mirror_divergences:
            longest = max(self.mirror_divergences, key=lambda m: m["duration_s"])
            recs.append(f"**Meross mirror divergence**: {len(self.mirror_divergences)} sustained divergences (longest {longest['duration_s']:.1f}s on {longest['pair']}). "
                        f"Indicates Meross integration cloud-vs-local sync issues. Consider meross_lan addon for stricter local control.")

        if not recs:
            recs.append("(no notable issues detected — system appears stable)")
        for r in recs:
            lines.append(f"- {r}")
        lines.append("")

        # Run footer
        lines.append("---")
        lines.append("")
        lines.append(f"Raw events + traces: `{self.jsonl_path.name}`")
        lines.append(f"Watchlist: {len(WATCHLIST)} entities including {sum(1 for z in ZONES if z[4])} actuated zones + {sum(1 for z in ZONES if not z[4])} observability-only zones")
        out.write_text("\n".join(lines), encoding="utf-8")
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--duration-min", type=float, default=30.0,
                    help="Run duration in minutes (default 30)")
    ap.add_argument("--output-dir", type=Path, default=REPORT_DIR,
                    help="Output dir for JSONL + MD (default tools/reports)")
    args = ap.parse_args()
    token = get_token()
    if not token:
        print("FATAL: no HA token", file=sys.stderr)
        return 2

    duration_s = int(args.duration_min * 60)
    h = Harness(token=token, duration_s=duration_s, output_dir=args.output_dir)

    try:
        asyncio.run(h.run())
    except KeyboardInterrupt:
        print("\nInterrupted - writing partial report...")

    path = h.write_report()
    print(f"\n[OK] Report written: {path}")
    print(f"  JSONL: {h.jsonl_path}")
    # Flutter is now NATIVE to the harness's main report (no post-call needed).
    return 0


if __name__ == "__main__":
    sys.exit(main())
