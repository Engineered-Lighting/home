#!/usr/bin/env python3
"""init-spatial-model.py — Addendum 38 Phase 1 (hand-drawn light map).

Writes the spatial_model.json SKELETON that the /spatial drawer's
hand-draw tool paints into: the indoor cameras (grid dims + Frigate zone
polygons) and the 16 controllable lights with empty footprints. Read-only
against Frigate + HA — toggles nothing.

WHY hand-drawn, not photometric: the earlier approach measured footprints
by toggling each light and diffing camera frames. After three runs it
still failed — camera auto-exposure plus uncontrollable ambient light
(pitch-black night → sensor noise; bright daytime → lights swamped)
corrupt the diff. Gradient lighting only needs *roughly* where each light
is centred, which the user can specify directly. So the user now
hand-draws each light's footprint as a polygon in the /spatial drawer.
See the plan: ~/.claude/plans/remind-me-why-we-peaceful-sonnet.md.

Usage:  py -3 tools/init-spatial-model.py
        → writes ha-config/spatial_model.json (backs up any existing one)
        then deploy:
        scp -P 22222 ha-config/spatial_model.json \\
            root@homeassistant.local:/config/spatial_model.json

Connection:
  Frigate  http://192.168.0.125:5000        (LAN, no auth)
  HA       http://homeassistant.local:8123  (token: tempdir/ha_token.txt)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Windows consoles default stdout to cp1252, which crashes on the
# box-drawing characters in this report. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ───────────────────────── constants ──────────────────────────

FRIGATE_URL = "http://192.168.0.125:5000"
HA_URL = "http://homeassistant.local:8123"

# Indoor-only (user directive): the driveway is outdoor — no indoor light
# reaches it and it has no controllable light. Dropped from the model.
EXCLUDE_CAMERAS = {"driveway"}

# The neon plug — a light with no Living-Lights zone (see EXTRA below).
NEON_PLUG = "switch.smart_plug_mini_mss110_main_channel"

# Lights with no Living-Lights actuator but still in a camera-covered
# indoor room — appended so the map covers EVERY controllable light: the
# neon plug + the three workshop lights.
EXTRA_CALIBRATION_TARGETS = [
    NEON_PLUG,
    "switch.workshop_light_left_mss110_main_channel",
    "switch.workshop_light_right_mss110_main_channel",
    "switch.smart_plug_mini_mss110_main_channel_2",   # "Workshop Light Rear"
]

# Home camera for the EXTRA targets — they have no Living-Lights zone, so
# the camera cannot be derived from it: the neon plug is in the office
# (living_room camera); the workshop lights are in the workshop.
EXTRA_LIGHT_CAMERA: dict = {
    NEON_PLUG: "living_room",
    "switch.workshop_light_left_mss110_main_channel": "workshop",
    "switch.workshop_light_right_mss110_main_channel": "workshop",
    "switch.smart_plug_mini_mss110_main_channel_2": "workshop",
}

GRID_COLS = 32                # 16:9 → 32x18 cells; 1280x720 → 40x40px cells

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "ha-config" / "spatial_model.json"


# ─────────────────────── token + HTTP I/O ─────────────────────


def find_token() -> str:
    """HA long-lived token. Git-Bash /tmp and native Windows %TEMP%
    resolve to different places — try both."""
    for p in ("/tmp/ha_token.txt",
              os.path.join(tempfile.gettempdir(), "ha_token.txt")):
        if os.path.exists(p):
            return Path(p).read_text(encoding="utf-8").strip()
    return ""


def http_get_json(url: str, headers: dict | None = None, timeout: int = 15):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ─────────────── light targets (single source of truth) ───────


def load_calibration_targets() -> list[str]:
    """All controllable lights to put in the map. The base set is derived
    from the Living-Lights actuator LIGHT_TARGETS dict (one source of
    truth); EXTRA_CALIBRATION_TARGETS appends lights that have no actuator
    (the neon plug + the workshop lights) so the map covers every
    controllable light in a camera-covered indoor room."""
    gen = REPO / "tools" / "build-living-lights-actuators.py"
    seen: list[str] = []
    try:
        spec = importlib.util.spec_from_file_location("_ll_gen", gen)
        mod = importlib.util.module_from_spec(spec)            # type: ignore
        spec.loader.exec_module(mod)                           # type: ignore
        for targets in mod.LIGHT_TARGETS.values():
            for tup in targets or []:
                eid = tup[1]
                if eid not in seen:
                    seen.append(eid)
    except Exception as e:                                     # pragma: no cover
        print(f"  ! could not import LIGHT_TARGETS ({e}); using fallback list")
        seen = [
            "light.dining_table_left", "light.dining_table_right",
            "light.front_right", "light.sink", "light.island_left",
            "light.island_right", "light.office", "light.rear_right",
            "light.front_left", "light.rear_left",
            "switch.ambient_light_left_mss110_main_channel",
            "switch.ambient_light_right_mss110_main_channel",
        ]
    for eid in EXTRA_CALIBRATION_TARGETS:
        if eid not in seen:
            seen.append(eid)
    return seen


def build_light_camera_map() -> dict:
    """{entity_id: home_camera} — the camera of the room each light is
    physically in. Used as the /spatial draw tool's default camera when a
    light is selected. Derived from the Living-Lights LIGHT_TARGETS →
    ZONE_CAMERA mapping; the EXTRA targets are mapped via
    EXTRA_LIGHT_CAMERA."""
    gen = REPO / "tools" / "build-living-lights-actuators.py"
    out: dict = {}
    try:
        spec = importlib.util.spec_from_file_location("_ll_gen_cam", gen)
        mod = importlib.util.module_from_spec(spec)            # type: ignore
        spec.loader.exec_module(mod)                           # type: ignore
        for zone, tlist in mod.LIGHT_TARGETS.items():
            cam = mod.ZONE_CAMERA.get(zone)
            if not cam:
                continue
            for tup in tlist or []:
                out.setdefault(tup[1], cam)
    except Exception as e:                                     # pragma: no cover
        print(f"  ! could not import LIGHT_TARGETS/ZONE_CAMERA ({e})")
    for eid, cam in EXTRA_LIGHT_CAMERA.items():
        out.setdefault(eid, cam)
    return out


# ════════════════════════ PURE HELPERS ════════════════════════
# Importable, side-effect-free, unit-tested by
# tools/test-init-spatial-model.py.


def grid_dims(width: int, height: int, target_cols: int = GRID_COLS):
    """(cols, rows, cell_w, cell_h) for a target_cols-wide grid that
    keeps cells square-ish. 1280x720 → (32, 18, 40, 40)."""
    cols = target_cols
    cell_w = max(1, round(width / cols))
    rows = max(1, round(height / cell_w))
    cell_h = max(1, round(height / rows))
    return cols, rows, cell_w, cell_h


def parse_zone_coordinates(raw) -> list:
    """Frigate zone `coordinates` → a list of [x, y] normalised [0,1]
    vertices. Frigate stores zone geometry as a flat comma-separated
    string "x1,y1,x2,y2,..." in the camera frame — the SAME normalised
    space the hand-drawn footprint polygons use, so the two overlay
    natively. A flat numeric list or an already-paired list of points are
    also accepted defensively. Malformed input → []."""
    if not raw:
        return []
    flat: list = []
    if isinstance(raw, str):
        for tok in raw.split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                flat.append(float(tok))
            except ValueError:
                return []
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, (list, tuple)):
                try:
                    return [[float(p[0]), float(p[1])] for p in raw
                            if len(p) >= 2]
                except (ValueError, TypeError):
                    return []
            try:
                flat.append(float(item))
            except (ValueError, TypeError):
                return []
    else:
        return []
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]


# ════════════════════════ FRIGATE / HA I/O ════════════════════


def frigate_config() -> dict:
    """{camera: {detect_w, detect_h, zones:{name:{...}}}} from live
    Frigate. Cameras in EXCLUDE_CAMERAS (driveway — outdoor) are dropped.

    Each zone keeps its polygon: Frigate stores zone geometry as
    normalised [0,1] vertices in the camera frame — the SAME coordinate
    space as the hand-drawn footprint polygons — so the /spatial drawer
    can overlay the real Frigate zones on the light map natively."""
    cfg = http_get_json(f"{FRIGATE_URL}/api/config")
    out = {}
    for cam, c in (cfg.get("cameras") or {}).items():
        if cam in EXCLUDE_CAMERAS:
            continue
        det = c.get("detect") or {}
        zones = {}
        for zname, z in (c.get("zones") or {}).items():
            z = z or {}
            objs = z.get("objects")
            zones[zname] = {
                "polygon": parse_zone_coordinates(z.get("coordinates")),
                "objects": list(objs) if objs else [],
                "inertia": z.get("inertia"),
                "loitering_time": z.get("loitering_time"),
                "color": list(z.get("color") or []),
            }
        out[cam] = {
            "detect_w": int(det.get("width") or 0),
            "detect_h": int(det.get("height") or 0),
            "zones": zones,
        }
    return out


def ha_state(token: str, entity: str) -> dict:
    try:
        return http_get_json(f"{HA_URL}/api/states/{entity}",
                             {"Authorization": f"Bearer {token}"})
    except Exception as e:
        return {"state": "unavailable", "error": str(e)}


# ═══════════════════════ MODEL SCAFFOLD ═══════════════════════


def build_blank_model() -> dict:
    """The spatial_model.json skeleton — indoor cameras (grid dims +
    Frigate zone polygons) and every controllable light with an empty
    footprint, ready for the /spatial hand-draw tool to paint into."""
    cams = frigate_config()
    grid = {cam: grid_dims(c["detect_w"] or 1280, c["detect_h"] or 720)
            for cam, c in cams.items()}
    targets = load_calibration_targets()
    light_cam = build_light_camera_map()
    return {
        "schema": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "hand_drawn",
        "grid": {"target_cols": GRID_COLS},
        "cameras": {cam: {"detect_w": c["detect_w"],
                          "detect_h": c["detect_h"],
                          "grid_cols": grid[cam][0],
                          "grid_rows": grid[cam][1],
                          "cell_w": grid[cam][2], "cell_h": grid[cam][3],
                          "zones": c["zones"]}
                    for cam, c in cams.items()},
        "light_camera": {e: light_cam.get(e) for e in targets},
        "lights": {e: {"footprint": {}, "footprint_polygon": {},
                       "footprint_empty": True, "human_verdict": None}
                   for e in targets},
    }


def main() -> int:
    print("Addendum 38 Phase 1 — spatial-model skeleton (hand-drawn map)")
    print("=" * 62)

    token = find_token()
    print(f"  HA token   : "
          f"{'present' if token else 'MISSING — light check skipped'}")

    try:
        model = build_blank_model()
    except Exception as e:
        print(f"  FAIL — could not build the model: {e}")
        print("  (is Frigate reachable at " + FRIGATE_URL + " ?)")
        return 2

    cams = model["cameras"]
    targets = list(model["lights"])
    print(f"  Cameras    : {len(cams)} — {', '.join(sorted(cams))}")
    for cam in sorted(cams):
        c = cams[cam]
        print(f"      {cam:14s} {c['detect_w']}x{c['detect_h']} "
              f"grid {c['grid_cols']}x{c['grid_rows']} "
              f"zones={len(c['zones'])}")
    print(f"  Lights     : {len(targets)}")

    if token:
        missing = [e for e in targets
                   if ha_state(token, e).get("state") in
                   (None, "unavailable", "unknown")]
        if missing:
            print(f"  WARN       : {len(missing)} light(s) unavailable "
                  f"in HA: {missing}")
        else:
            print(f"               all {len(targets)} reachable in HA")

    out = DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = out.with_name(f"spatial_model.backup-{stamp}.json")
        bak.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Backed up  : existing model → {bak.name}")

    out.write_text(json.dumps(model, indent=2), encoding="utf-8")
    print("=" * 62)
    print(f"  wrote {out}")
    print("  next — deploy it:")
    print("    scp -P 22222 ha-config/spatial_model.json "
          "root@homeassistant.local:/config/spatial_model.json")
    print("  then hand-draw each light's footprint in the /spatial drawer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
