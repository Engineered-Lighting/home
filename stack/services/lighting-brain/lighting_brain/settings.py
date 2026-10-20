"""Adjustable rule settings (CONTRACT "Rules v1").

Every level, colour temperature and timing the rules use lives here. A private
JSON file may override any field; unknown fields are refused so a typo can't
silently fall back to a default.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


@dataclass(frozen=True)
class Settings:
    settings_version: str = "v1.1"
    # IANA zone for the night window ("UTC" and "+HH:MM" also work offline).
    timezone: str = "America/Vancouver"

    # room_off
    room_off_after_s: float = 120.0
    room_off_fade_s: float = 3.0
    # Occupancy is unknown (not absent) when it expires while Frigate reports
    # unhealthy: hold, and turn off only after this long with no person
    # evidence from any source.
    unknown_occupancy_off_s: float = 1800.0

    # proximity_ambient (contract v1.1): level by distance from the nearest
    # fresh person position to each fixture, linear between far and near.
    prox_far_m: float = 3.0
    prox_far_level: float = 0.10
    prox_near_m: float = 1.0
    prox_near_level: float = 0.60
    prox_fade_s: float = 0.8
    # A fixture's level only moves when the new level differs by more than this.
    prox_deadband: float = 0.05
    # The nearest-fixture name in the why line only switches when another
    # fixture is this much closer.
    prox_nearest_hysteresis_m: float = 0.25
    # occupied_ambient: occupancy but no fresh position.
    occupied_level: float = 0.30
    ambient_cct_k: int = 2700
    ambient_fade_s: float = 2.0
    night_start: str = "23:00"
    night_end: str = "06:00"
    night_factor: float = 0.35
    night_cct_k: int = 2200

    # reading_spot_on / reading_spot_off
    reading_hold_on_s: float = 3.0
    reading_off_after_s: float = 10.0
    reading_min_confidence: float = 0.0
    spot_level: float = 0.8
    spot_cct_k: int = 3500
    spot_fade_s: float = 1.5
    # The spot target only moves when the hand point moves more than this.
    spot_target_deadband_m: float = 0.10

    # Aim planner (CONTRACT "Hand-region point").
    hand_forward_m: float = 0.35
    hand_down_m: float = 0.25
    head_clearance_m: float = 0.15
    # Used only when a pose arrives without head_m.
    head_fallback_up_m: float = 0.25
    head_pitch_min_deg: float = -45.0
    head_pitch_max_deg: float = 75.0
    # Fixture choice: refuse fixtures in front of the reader (cosine of the
    # angle between "fixture direction from the person" and "behind").
    min_behind_cos: float = 0.0
    max_beam_m: float = 8.0
    # "At the sofa": a fresh position (or the posed reader) this close,
    # horizontally, to the sofa seat.
    pose_seat_max_m: float = 1.5

    # manual_pause
    pause_max_s: float = 1800.0

    # Ingest
    max_future_skew_s: float = 5.0

    # Output
    decision_log_size: int = 20
    stream_period_s: float = 5.0
    # State changes reach the stream at most this often (contract v1.1: 4/s).
    max_push_hz: float = 4.0
    tick_s: float = 0.25

    def __post_init__(self) -> None:
        for name in ("prox_far_level", "prox_near_level", "occupied_level", "night_factor", "spot_level",
                     "reading_min_confidence"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"settings.{name} must be within 0..1")
        for name in ("night_start", "night_end"):
            if not _HHMM.match(getattr(self, name)):
                raise ValueError(f"settings.{name} must be HH:MM")
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if field.name.endswith(("_s", "_m")) and value < 0:
                raise ValueError(f"settings.{field.name} must not be negative")
        if self.decision_log_size < 1 or self.tick_s <= 0 or self.stream_period_s <= 0 or self.max_push_hz <= 0:
            raise ValueError("settings.decision_log_size, tick_s, stream_period_s and max_push_hz must be positive")
        if self.prox_near_m >= self.prox_far_m:
            raise ValueError("settings.prox_near_m must be less than prox_far_m")
        resolve_timezone(self.timezone)

    def tz(self) -> tzinfo:
        return resolve_timezone(self.timezone)

    def is_night(self, now: float) -> bool:
        local = datetime.fromtimestamp(now, self.tz())
        minute = local.hour * 60 + local.minute
        start = _minutes(self.night_start)
        end = _minutes(self.night_end)
        if start == end:
            return False
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def resolve_timezone(name: str) -> tzinfo:
    if name == "UTC":
        return timezone.utc
    match = _OFFSET.match(name)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))
    from zoneinfo import ZoneInfo  # tzdata comes from the OS or requirements.txt

    return ZoneInfo(name)


def load_settings(path: str | Path | None) -> Settings:
    if not path:
        return Settings()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("settings file must hold a JSON object")
    data = {k: v for k, v in data.items() if not k.startswith("_")}  # "_note" comments
    known = {field.name for field in dataclasses.fields(Settings)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(unknown)}")
    return Settings(**data)
