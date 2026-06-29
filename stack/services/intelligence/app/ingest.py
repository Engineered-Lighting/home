from __future__ import annotations

import hashlib
import ast
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(token|api_key|password|secret)(['\"\s:=]+)([^,'\"\s}]+)"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    return hashlib.sha256(value).hexdigest()


def redact_text(text: str) -> str:
    out = text
    for pat in SECRET_PATTERNS:
        if pat.pattern.lower().startswith("(?i)(bearer"):
            out = pat.sub(r"\1<redacted>", out)
        else:
            out = pat.sub(r"\1\2<redacted>", out)
    return out


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            if re.search(r"(?i)(token|api[_-]?key|password|secret)", str(key)):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_payload(v) for v in payload]
    if isinstance(payload, str):
        return redact_text(payload)
    return payload


def bool_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "on", "yes", "1"):
            return 1
        if lowered in ("false", "off", "no", "0"):
            return 0
    return None


def float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def ha_config_dir() -> Path:
    raw = os.environ.get("INTELLIGENCE_HA_CONFIG_DIR")
    if raw:
        return Path(raw)
    win_default = Path("C:/Claude/home/ha-config")
    if win_default.exists():
        return win_default
    return Path("/config")


KNOWN_ZONES = [
    "whole_living_room",
    "whole_dining_room",
    "whole_kitchen",
    "workshop_zone",
    "dining_left",
    "dining_right",
    "island_left",
    "island_right",
    "front_door",
    "front_left",
    "office",
    "weights",
    "sofa",
    "sink",
    "e28",
]


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)) or value is None:
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return value


def iter_json_objects(raw: str) -> list[dict[str, Any]]:
    """Parse one or more JSON objects from a JSONL line.

    Home Assistant file writes can occasionally concatenate two JSON objects
    on one physical line. Treat that as recoverable instead of dropping both.
    """

    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    idx = 0
    text_len = len(raw)
    while idx < text_len:
        while idx < text_len and raw[idx].isspace():
            idx += 1
        if idx >= text_len:
            break
        obj, end = decoder.raw_decode(raw, idx)
        if not isinstance(obj, dict):
            raise ValueError("JSONL entry is not an object")
        objects.append(obj)
        idx = end
    if not objects:
        raise ValueError("JSONL line is empty")
    return objects


def zone_from_entity(entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    entity = entity_id
    if entity.startswith("sensor."):
        entity = entity.split(".", 1)[1]
    if entity.endswith("_lighting_state"):
        entity = entity[: -len("_lighting_state")]
    for zone in sorted(KNOWN_ZONES, key=len, reverse=True):
        if entity == zone or entity.endswith("_" + zone):
            return zone
    return None


def _insert_raw_event(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_id: str,
    payload: dict[str, Any],
    raw_text: str,
    source_path: str | None = None,
    line_no: int | None = None,
    source_ts: str | None = None,
) -> str:
    source_hash = stable_hash(raw_text)
    raw_id = f"raw:{source_hash[:24]}"
    redacted_payload = redact_payload(payload)
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_events
            (id, source, source_id, source_path, line_no, source_ts, ingest_ts,
             source_hash, redacted_payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_id,
            source,
            source_id,
            source_path,
            line_no,
            source_ts,
            utc_now(),
            source_hash,
            canonical_json(redacted_payload),
        ),
    )
    return raw_id


def _payload_hash_id(prefix: str, payload: dict[str, Any], raw_id: str) -> str:
    external_id = first_present(payload.get("context_id"), payload.get("evidence_id"))
    if external_id:
        return f"{prefix}:{external_id}"
    return f"{prefix}:{stable_hash(raw_id + canonical_json(payload))[:24]}"


def _extract_prediction(payload: dict[str, Any]) -> dict[str, Any]:
    predicted = payload.get("predicted") or payload.get("what_automation_would_have_done") or {}
    if not isinstance(predicted, dict):
        predicted = {}
    return predicted


def _extract_observed(payload: dict[str, Any]) -> dict[str, Any]:
    observed = payload.get("observed") or payload.get("captured_state") or {}
    if not isinstance(observed, dict):
        observed = {}
    return observed


def _context(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = payload.get("context")
    return ctx if isinstance(ctx, dict) else {}


def _dict_or_empty(value: Any) -> dict[str, Any]:
    parsed = parse_jsonish(value)
    return parsed if isinstance(parsed, dict) else {}


def _shadow_mode(payload: dict[str, Any], ctx: dict[str, Any]) -> int:
    return bool_int(first_present(payload.get("shadow_mode"), ctx.get("shadow_mode"))) or 0


def _int_or_none(value: Any) -> int | None:
    number = float_or_none(value)
    if number is None:
        return None
    return int(number)


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "unknown", "unavailable"}:
        return None
    return text


def _evidence_weight(
    *,
    kind: str,
    shadow: int,
    capture_provenance: dict[str, Any],
    prediction_consistency: dict[str, Any],
    occupancy: dict[str, Any],
) -> float:
    weight = 1.5 if kind == "pending_preference" else 1.0
    if shadow:
        weight *= 0.35
    if kind == "pending_preference":
        trigger = capture_provenance.get("capture_trigger")
        if trigger in {"manual_automation_trigger", "automation_trigger", "manual_trigger"}:
            weight *= 0.6
        elif not trigger:
            weight *= 0.85

        mismatch = float_or_none(prediction_consistency.get("prediction_mismatch_pct"))
        if mismatch is not None and mismatch != 0:
            weight *= 0.55 if abs(mismatch) > 10 else 0.75

        occupancy_age = float_or_none(occupancy.get("age_s"))
        if occupancy_age is not None:
            if occupancy_age < 5:
                weight *= 0.55
            elif occupancy_age < 10:
                weight *= 0.75

        flicker_count = float_or_none(occupancy.get("flicker_count_5min"))
        if flicker_count is not None and flicker_count >= 2:
            weight *= 0.7
    return max(weight, 0.05)


def _normal_observation_fields(payload: dict[str, Any], raw_id: str) -> dict[str, Any]:
    ctx = _context(payload)
    observed = _extract_observed(payload)
    predicted = _extract_prediction(payload)
    capture_provenance = _dict_or_empty(payload.get("capture_provenance"))
    prediction_consistency = _dict_or_empty(payload.get("prediction_consistency"))
    occupancy = _dict_or_empty(payload.get("occupancy"))
    occupancy_flicker = _int_or_none(occupancy.get("flicker_count_5min"))
    zone = first_present(payload.get("primary_zone"), payload.get("zone"), ctx.get("zone"))
    primary_zone = first_present(payload.get("primary_zone"), zone)
    actual = first_present(
        payload.get("actual_pct"),
        payload.get("actual_brightness_pct"),
        observed.get("brightness_pct"),
    )
    pred = first_present(
        payload.get("predicted_pct"),
        payload.get("predicted_brightness_pct"),
        prediction_consistency.get("would_have_done_brightness_pct"),
        predicted.get("brightness_pct"),
    )
    actual_f = float_or_none(actual)
    pred_f = float_or_none(pred)
    delta = float_or_none(payload.get("delta_pct"))
    if delta is None and actual_f is not None and pred_f is not None:
        delta = actual_f - pred_f
    light_state = first_present(payload.get("light_state"), observed.get("light_state"))
    if light_state == "off" and actual_f is None:
        actual_f = 0.0
    predictions_by_zone = parse_jsonish(payload.get("predictions_by_zone")) or {}
    if not isinstance(predictions_by_zone, dict):
        predictions_by_zone = {"raw": predictions_by_zone}
    profile = first_present(payload.get("profile"), ctx.get("profile"), ctx.get("tod_bucket"))
    state = first_present(payload.get("state"), ctx.get("state"))
    shadow = _shadow_mode(payload, ctx)
    kind = str(payload.get("kind") or "unknown")
    weight = _evidence_weight(
        kind=kind,
        shadow=shadow,
        capture_provenance=capture_provenance,
        prediction_consistency=prediction_consistency,
        occupancy=occupancy,
    )
    return {
        "id": _payload_hash_id(f"prefobs:{kind}", payload, raw_id),
        "raw_event_id": raw_id,
        "kind": kind,
        "ts": payload.get("ts"),
        "zone": str(zone or "unknown"),
        "primary_zone": str(primary_zone) if primary_zone else None,
        "context_id": _nullable_text(payload.get("context_id")),
        "evidence_id": _nullable_text(payload.get("evidence_id")),
        "dedupe_key": _nullable_text(payload.get("dedupe_key")),
        "dedupe_window_s": _int_or_none(payload.get("dedupe_window_s")),
        "capture_suppressed_count": _int_or_none(payload.get("capture_suppressed_count")) or 0,
        "related_override_context_id": _nullable_text(payload.get("related_override_context_id")),
        "light_entity": payload.get("light_entity"),
        "light_state": light_state,
        "actual_brightness_pct": actual_f,
        "predicted_brightness_pct": pred_f,
        "delta_pct": delta,
        "profile": profile,
        "state": state,
        "tv_playing": bool_int(first_present(payload.get("tv_playing"), ctx.get("tv_playing"))),
        # MC.1 — Steam-driven gaming context. Stored from payload root OR
        # the context blob (HA-side generator writes it to both for
        # override_event + pending_preference per the MC plan).
        "gaming_active": bool_int(first_present(payload.get("gaming_active"), ctx.get("gaming_active"))),
        "asleep": bool_int(first_present(payload.get("asleep"), ctx.get("asleep"))),
        "night_safe": bool_int(first_present(payload.get("night_safe"), ctx.get("night_safe"))),
        "user_at_home": bool_int(first_present(payload.get("user_at_home"), ctx.get("user_at_home"))),
        "shadow_mode": shadow,
        "weight": weight,
        "predictions_by_zone_json": canonical_json(predictions_by_zone),
        "capture_provenance_json": canonical_json(capture_provenance),
        "prediction_consistency_json": canonical_json(prediction_consistency),
        "occupancy_json": canonical_json(occupancy),
        "occupancy_flicker_count_5min": occupancy_flicker,
        "context_json": canonical_json(ctx),
    }


def insert_preference_observation(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    raw_id: str,
) -> str:
    fields = _normal_observation_fields(payload, raw_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO preference_observations
            (id, raw_event_id, kind, ts, zone, primary_zone,
             context_id, evidence_id, dedupe_key, dedupe_window_s,
             capture_suppressed_count, related_override_context_id,
             light_entity, light_state,
             actual_brightness_pct, predicted_brightness_pct, delta_pct,
             profile, state, tv_playing, gaming_active, asleep, night_safe, user_at_home,
             shadow_mode, weight, predictions_by_zone_json,
             capture_provenance_json, prediction_consistency_json, occupancy_json,
             occupancy_flicker_count_5min, context_json)
        VALUES
            (:id, :raw_event_id, :kind, :ts, :zone, :primary_zone,
             :context_id, :evidence_id, :dedupe_key, :dedupe_window_s,
             :capture_suppressed_count, :related_override_context_id,
             :light_entity, :light_state,
             :actual_brightness_pct, :predicted_brightness_pct, :delta_pct,
             :profile, :state, :tv_playing, :gaming_active, :asleep, :night_safe, :user_at_home,
             :shadow_mode, :weight, :predictions_by_zone_json,
             :capture_provenance_json, :prediction_consistency_json, :occupancy_json,
             :occupancy_flicker_count_5min, :context_json)
        """,
        fields,
    )
    return fields["id"]


def insert_override_event(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    raw_id: str,
) -> str:
    obs_id = insert_preference_observation(conn, payload, raw_id)
    fields = _normal_observation_fields(payload, raw_id)
    override_id = _payload_hash_id("override", payload, raw_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO override_events
            (id, raw_event_id, ts, zone, light_entity, light_state,
             actual_brightness_pct, predicted_brightness_pct, delta_pct,
             profile, state, tv_playing, gaming_active, asleep, night_safe, user_at_home,
             shadow_mode, predictions_by_zone_json, context_json)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            override_id,
            raw_id,
            fields["ts"],
            fields["zone"],
            fields["light_entity"],
            fields["light_state"],
            fields["actual_brightness_pct"],
            fields["predicted_brightness_pct"],
            fields["delta_pct"],
            fields["profile"],
            fields["state"],
            fields["tv_playing"],
            fields["gaming_active"],
            fields["asleep"],
            fields["night_safe"],
            fields["user_at_home"],
            fields["shadow_mode"],
            fields["predictions_by_zone_json"],
            fields["context_json"],
        ),
    )
    return obs_id


def insert_lighting_decision(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    raw_id: str,
) -> str:
    predicted = _extract_prediction(payload)
    decision_id = _payload_hash_id("lighting", payload, raw_id)
    entity_id = payload.get("entity_id")
    conn.execute(
        """
        INSERT OR IGNORE INTO lighting_decisions
            (id, raw_event_id, ts, entity_id, zone, from_state, to_state,
             predicted_brightness_pct, predicted_color_temp_kelvin,
             shadow_mode, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            raw_id,
            payload.get("ts"),
            entity_id,
            zone_from_entity(entity_id),
            payload.get("from_state"),
            payload.get("to_state"),
            float_or_none(predicted.get("brightness_pct")),
            float_or_none(predicted.get("color_temp_kelvin")),
            bool_int(payload.get("shadow_mode")) or 0,
            canonical_json(redact_payload(payload)),
        ),
    )
    return decision_id


def _state_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _color_temp_kelvin(state: dict[str, Any]) -> float | None:
    direct = float_or_none(state.get("color_temp_kelvin"))
    if direct is not None:
        return direct
    mired = float_or_none(state.get("color_temp"))
    if mired and mired > 0:
        return round(1000000.0 / mired)
    return None


def insert_lighting_change_event(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    raw_id: str,
) -> str:
    event_id = _nullable_text(payload.get("event_id")) or stable_hash(raw_id)[:24]
    obj_id = f"lightchg:{event_id}"
    from_state = _state_payload(payload.get("from"))
    to_state = _state_payload(payload.get("to"))
    ha_context = _dict_or_empty(payload.get("ha_context"))
    context = _dict_or_empty(payload.get("context"))
    conn.execute(
        """
        INSERT OR IGNORE INTO lighting_change_events
            (id, raw_event_id, event_id, ts, entity_id, light_entity, zone,
             from_state, to_state, from_brightness_pct, to_brightness_pct,
             from_color_temp_kelvin, to_color_temp_kelvin, command_id,
             source_hint, source_confidence, trigger_attribute,
             ha_context_json, context_json, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obj_id,
            raw_id,
            event_id,
            payload.get("ts"),
            first_present(payload.get("entity_id"), payload.get("light_entity")),
            payload.get("light_entity"),
            first_present(payload.get("zone"), context.get("zone")),
            from_state.get("state"),
            to_state.get("state"),
            float_or_none(from_state.get("brightness_pct")),
            float_or_none(to_state.get("brightness_pct")),
            _color_temp_kelvin(from_state),
            _color_temp_kelvin(to_state),
            _nullable_text(payload.get("command_id")),
            _nullable_text(payload.get("source_hint")),
            _nullable_text(payload.get("source_confidence")),
            _nullable_text(payload.get("trigger_attribute")),
            canonical_json(ha_context),
            canonical_json(context),
            canonical_json(redact_payload(payload)),
        ),
    )
    return obj_id


def insert_lighting_command_event(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    raw_id: str,
) -> str:
    command_id = _nullable_text(payload.get("command_id") or payload.get("event_id"))
    obj_id = f"lightcmd:{command_id or stable_hash(raw_id)[:24]}"
    requested = _state_payload(payload.get("requested"))
    baseline = _state_payload(payload.get("baseline"))
    conn.execute(
        """
        INSERT OR IGNORE INTO lighting_command_events
            (id, raw_event_id, command_id, ts, zone, entity_id, source,
             source_text, requested_brightness_pct, requested_color_temp_kelvin,
             baseline_json, requested_json, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obj_id,
            raw_id,
            command_id,
            payload.get("ts"),
            payload.get("zone"),
            payload.get("entity_id"),
            _nullable_text(payload.get("source")),
            _nullable_text(payload.get("source_text")),
            float_or_none(requested.get("brightness_pct")),
            float_or_none(requested.get("color_temp_kelvin")),
            canonical_json(baseline),
            canonical_json(requested),
            canonical_json(redact_payload(payload)),
        ),
    )
    return obj_id


def ingest_payload(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_id: str,
    payload: dict[str, Any],
    raw_text: str,
    source_path: str | None = None,
    line_no: int | None = None,
) -> dict[str, Any]:
    kind = payload.get("kind")
    raw_id = _insert_raw_event(
        conn,
        source=source,
        source_id=source_id,
        payload=payload,
        raw_text=raw_text,
        source_path=source_path,
        line_no=line_no,
        source_ts=payload.get("ts"),
    )
    if kind == "override_event":
        obj_id = insert_override_event(conn, payload, raw_id)
    elif kind == "pending_preference":
        obj_id = insert_preference_observation(conn, payload, raw_id)
    elif kind in ("lighting_decision", "gradient_decision"):
        obj_id = insert_lighting_decision(conn, payload, raw_id)
    elif kind == "lighting_change_event":
        obj_id = insert_lighting_change_event(conn, payload, raw_id)
    elif kind == "lighting_command_event":
        obj_id = insert_lighting_command_event(conn, payload, raw_id)
    else:
        obj_id = raw_id
    return {"raw_id": raw_id, "object_id": obj_id, "kind": kind or "unknown"}


def ingest_jsonl_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    source: str,
) -> dict[str, Any]:
    stats = {"source": source, "path": str(path), "read": 0, "ingested": 0, "errors": 0}
    if not path.exists():
        stats["missing"] = True
        return stats
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            stats["read"] += 1
            try:
                payloads = iter_json_objects(raw)
                if len(payloads) > 1:
                    stats["split_lines"] = int(stats.get("split_lines", 0)) + 1
                for part_no, payload in enumerate(payloads, start=1):
                    source_id = f"{path}:{line_no}"
                    if len(payloads) > 1:
                        source_id = f"{source_id}:{part_no}"
                    ingest_payload(
                        conn,
                        source=source,
                        source_id=source_id,
                        payload=payload,
                        raw_text=canonical_json(payload),
                        source_path=str(path),
                        line_no=line_no,
                    )
                    stats["ingested"] += 1
            except Exception as exc:
                stats["errors"] += 1
                stats.setdefault("error_samples", []).append(
                    {"line": line_no, "error": str(exc)[:240]}
                )
                if len(stats.get("error_samples", [])) > 5:
                    stats["error_samples"] = stats["error_samples"][:5]
    conn.commit()
    return stats


def ingest_default_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg = ha_config_dir()
    preference_path = Path(
        os.environ.get(
            "INTELLIGENCE_PREFERENCES_JSONL",
            str(cfg / "lighting_preferences_pending.jsonl"),
        )
    )
    decisions_path = Path(
        os.environ.get(
            "INTELLIGENCE_LIGHTING_DECISIONS_LOG",
            str(cfg / "lighting_decisions.log"),
        )
    )
    activity_path = Path(
        os.environ.get(
            "INTELLIGENCE_LIGHTING_ACTIVITY_JSONL",
            str(cfg / "lighting_activity.jsonl"),
        )
    )
    runs = [
        ingest_jsonl_file(conn, preference_path, source="lighting_preferences_pending"),
        ingest_jsonl_file(conn, decisions_path, source="lighting_decisions"),
        ingest_jsonl_file(conn, activity_path, source="lighting_activity"),
    ]
    return {
        "ok": all(r.get("errors", 0) == 0 for r in runs),
        "sources": runs,
        "ingested": sum(int(r.get("ingested", 0)) for r in runs),
        "errors": sum(int(r.get("errors", 0)) for r in runs),
    }
