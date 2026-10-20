"""Private fixture/zone geometry export (schema lighting-brain-geometry-1).

The real file is exported from Home's private equipment state (lane AL-4) and
never committed. config/geometry.example.json is synthetic and clearly fake.

Coordinates are the registered den room frame: Z-up, metres, floor at z=0.
For each fixture, `aim_origin_m` and `horizontal_world_reference` are the
values Home's virtual fixture descriptor uses for aimAtTarget
(`display_origin_m` and `horizontal_world_reference` in
app/src/home-3d/equipment-virtual-fixtures.mjs), so the brain's pan/tilt
check matches what Home will solve.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import GEOMETRY_SCHEMA

CONTRACT_FIXTURE_IDS = (
    "victoria-den-proposed-fixture-01",
    "victoria-den-proposed-fixture-02",
    "victoria-den-mount-03-virtual-fixture",
    "victoria-den-mount-04-virtual-fixture",
)
ZONE_NAMES = ("desk", "sofa")
DEFAULT_LIMITS = {"pan_min_deg": -180.0, "pan_max_deg": 180.0, "tilt_min_deg": -90.0, "tilt_max_deg": 90.0}

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    aim_origin_m: Vec | None
    horizontal_world_reference: Vec
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))


@dataclass(frozen=True)
class Zone:
    name: str
    near_fixtures: tuple[str, ...]
    seat_m: Vec | None = None


@dataclass(frozen=True)
class Geometry:
    home: str
    room: str
    frame: str
    fixtures: tuple[Fixture, ...]
    zones: dict[str, Zone]
    camera_m: Vec | None = None
    synthetic: bool = False

    def fixture(self, fixture_id: str) -> Fixture | None:
        return next((f for f in self.fixtures if f.fixture_id == fixture_id), None)

    @property
    def fixture_ids(self) -> tuple[str, ...]:
        return tuple(f.fixture_id for f in self.fixtures)


def _vec(value, label: str, allow_none: bool = False) -> Vec | None:
    if value is None and allow_none:
        return None
    if (not isinstance(value, list) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise ValueError(f"{label} must be [x, y, z] in metres")
    return (float(value[0]), float(value[1]), float(value[2]))


def parse_geometry(data: dict) -> Geometry:
    if not isinstance(data, dict) or data.get("schema") != GEOMETRY_SCHEMA:
        raise ValueError(f"geometry schema must be {GEOMETRY_SCHEMA}")
    for key in ("home", "room", "frame"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"geometry.{key} is required")
    fixtures = []
    for i, raw in enumerate(data.get("fixtures") or []):
        fid = raw.get("fixture_id")
        if not isinstance(fid, str) or not fid:
            raise ValueError(f"geometry.fixtures[{i}].fixture_id is required")
        ref = _vec(raw.get("horizontal_world_reference", [0, 1, 0]), f"{fid}.horizontal_world_reference")
        if math.hypot(ref[0], ref[1]) < 1e-6:
            raise ValueError(f"{fid}.horizontal_world_reference needs a horizontal component")
        limits = {**DEFAULT_LIMITS, **(raw.get("limits") or {})}
        if set(limits) != set(DEFAULT_LIMITS):
            raise ValueError(f"{fid}.limits has unknown keys")
        fixtures.append(Fixture(fid, _vec(raw.get("aim_origin_m"), f"{fid}.aim_origin_m", allow_none=True),
                                ref, {k: float(v) for k, v in limits.items()}))
    ids = [f.fixture_id for f in fixtures]
    if len(set(ids)) != len(ids):
        raise ValueError("geometry fixture ids must be unique")
    if data["home"] == "victoria" and data["room"] == "den":
        missing = [fid for fid in CONTRACT_FIXTURE_IDS if fid not in ids]
        if missing:
            raise ValueError(f"geometry is missing contract fixtures: {', '.join(missing)}")
    zones = {}
    for name, raw in (data.get("zones") or {}).items():
        if name not in ZONE_NAMES:
            raise ValueError(f"unknown zone {name!r}")
        near = tuple(raw.get("near_fixtures") or ())
        unknown = [fid for fid in near if fid not in ids]
        if unknown:
            raise ValueError(f"zone {name} lists unknown fixtures: {', '.join(unknown)}")
        zones[name] = Zone(name, near, _vec(raw.get("seat_m"), f"zones.{name}.seat_m", allow_none=True))
    return Geometry(home=data["home"], room=data["room"], frame=data["frame"], fixtures=tuple(fixtures),
                    zones=zones, camera_m=_vec(data.get("camera_m"), "camera_m", allow_none=True),
                    synthetic=bool(data.get("synthetic", False)))


def load_geometry(path: str | Path) -> Geometry:
    return parse_geometry(json.loads(Path(path).read_text(encoding="utf-8")))
