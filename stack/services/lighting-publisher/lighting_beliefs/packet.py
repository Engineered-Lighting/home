"""Build and validate the house-level ``lighting-beliefs-state/v1`` packet.

The packet is the only thing the publisher ever sends to Jev. It is built from
the observer's typed observations (``semantic()`` output plus the per-camera
snapshot), never from raw captions of unbounded length, and it carries:

- per camera: ``coverage`` (status and age), ``occupancy.zones``, anonymous
  ``people`` (opaque track label, position, posture, activities), ``claims``
  (short fallible observations), ``account`` (one short narrative) and
  ``objects.person_adjacent``;
- ``devices.media`` (role, state, source_kind, age_s);
- ``quiet.credible_activity_age_s``.

No modes, light levels, presence flags, clock, entity ids, names or
relationships. The builder is fail-closed on its own: every key must appear
in ``ALLOWED_KEYS`` (enforced recursively, lists only where the schema says
list); every key and scalar is run through the leak guard's roster-free
patterns (entity ids, addresses, hostnames, URLs, tokens, model names, digit
runs, dates, times of day, format and non-ASCII characters) and a hit raises
``PacketError`` naming the path, never the text. Error paths name schema keys
only: a camera or zone name, and any key that is not in the allow-list, is
reported by its position (``<key#N>``), so no caller-supplied key text is ever
echoed, whether or not the builder can recognise it as sensitive (it cannot
see the roster). Free text is NFKC-normalised,
stripped of format characters and capped at ``TEXT_CAP`` characters; ages are
integer seconds and an age above ``AGE_CAP_S`` (a day) raises, so an epoch
passed by mistake is a caller error, not a day-old belief. Cameras, zones,
media rows, people, claims and objects are capped in count; a camera or zone
name that collides with another after capping raises. Household names and the
OS username are not checkable here and remain the job of
``leak_guard.assert_clean`` at the call site.

``content_signature()`` hashes the packet with ages bucketed (by schema path,
not by bare key name) so a call is triggered by a change in content, not by
every tick.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from . import leak_guard

PACKET_SCHEMA = "lighting-beliefs-state/v1"
TEXT_CAP = 240
AGE_CAP_S = 86_400
AGE_SKEW_S = 5.0
MAX_CAMERAS = 16
MAX_ZONES = 32
MAX_MEDIA = 16
MAX_CLAIMS = 8
MAX_PEOPLE = 6
MAX_ACTIVITIES = 6
MAX_ADJACENT_OBJECTS = 8
NAME_CAP = 40
ANY = "*"

COVERAGE_STATUSES = ("fresh", "stale", "unavailable")
ZONE_STATES = ("occupied", "clear", "unknown")
MEDIA_STATES = ("playing", "paused", "idle", "off", "unknown")

# Nested allow-list. A dict lists the allowed child keys and requires a
# mapping; ANY allows any key (camera and zone names); a one-element list
# requires a list whose items each match the element spec; None marks a leaf
# whose value must be a scalar or a list of scalars.
PERSON_KEYS: dict = {"track": None, "position": None, "posture": None, "activities": None}
MEDIA_KEYS: dict = {"role": None, "state": None, "source_kind": None, "age_s": None}
ALLOWED_KEYS: dict = {
    "schema": None,
    "cameras": {
        ANY: {
            "coverage": {"status": None, "age_s": None},
            "occupancy": {"zones": {ANY: None}},
            "people": [PERSON_KEYS],
            "claims": None,
            "account": None,
            "objects": {"person_adjacent": None},
        }
    },
    "devices": {"media": [MEDIA_KEYS]},
    "quiet": {"credible_activity_age_s": None},
}

# Schema paths whose numeric value is an age in seconds; bucketed for the
# signature. ``*`` stands for a wildcard key and ``[]`` for a list item.
AGE_PATHS = frozenset({
    "$.cameras.*.coverage.age_s",
    "$.devices.media[].age_s",
    "$.quiet.credible_activity_age_s",
})
AGE_BUCKETS_S = (30, 120, 300, 900, 1800, 3600)

# Schema paths of lists with a maximum length.
LIST_CAPS = {
    "$.cameras.*.people": MAX_PEOPLE,
    "$.cameras.*.people[].activities": MAX_ACTIVITIES,
    "$.cameras.*.claims": MAX_CLAIMS,
    "$.cameras.*.objects.person_adjacent": MAX_ADJACENT_OBJECTS,
    "$.devices.media": MAX_MEDIA,
}
# Schema paths of wildcard mappings with a maximum size.
MAP_CAPS = {
    "$.cameras": MAX_CAMERAS,
    "$.cameras.*.occupancy.zones": MAX_ZONES,
}

# Fields the observer attaches to a person that must never reach the packet.
STRIPPED_PERSON_FIELDS = ("name", "name_source", "relationships", "relationship_source", "track_id")


class PacketError(ValueError):
    """The packet violates the schema, the allow-list, a cap or the leak patterns."""


def cap_text(value: Any, limit: int = TEXT_CAP) -> str:
    """Coerce to a single-line, NFKC-normalised string of at most ``limit`` characters.

    Unicode format characters (zero-width spaces and joiners) are removed so
    they cannot split an identifier past the leak patterns; other non-ASCII
    characters are left in place for ``validate_packet`` to reject.
    """
    text = leak_guard.normalise_text(str(value if value is not None else ""))
    return " ".join(text.split())[:limit]


def clamp_age(value: Any) -> int | None:
    """Integer seconds in [0, AGE_CAP_S]; None when unknown or non-numeric.

    A finite number above ``AGE_CAP_S`` raises ``PacketError``: no legitimate
    age exceeds a day, so a larger value is an epoch or a unit mistake at the
    call site. NaN and infinities raise too. A negative within ``AGE_SKEW_S``
    (clock skew) clamps to 0; anything more negative raises.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        raise PacketError("age is not a finite number")
    if seconds < -AGE_SKEW_S:
        raise PacketError("age is negative")
    if seconds > AGE_CAP_S:
        raise PacketError(f"age exceeds {AGE_CAP_S} s; ages are seconds, not timestamps")
    return int(min(max(seconds, 0.0), AGE_CAP_S))


def _enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _capped_names(names: Iterable[Any], path: str, limit: int) -> list[str]:
    """Cap each name and raise on collision after capping."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for index, name in enumerate(names):
        capped = cap_text(name, NAME_CAP)
        if capped in seen:
            raise PacketError(f"{path}: name collision after capping (entries {seen[capped]} and {index})")
        seen[capped] = index
        out.append(capped)
    if len(out) > limit:
        raise PacketError(f"{path}: {len(out)} entries, more than {limit}")
    return out


def _people(entries: Iterable[Any] | None) -> list[dict]:
    """Anonymise people: opaque per-packet labels, no names or relationships.

    Malformed entries (anything but a mapping) are skipped; a crowd larger
    than ``MAX_PEOPLE`` is truncated, which is still a crowd.
    """
    out = []
    for person in entries or []:
        if not isinstance(person, Mapping):
            continue
        if len(out) >= MAX_PEOPLE:
            break
        activities = person.get("activities") or []
        out.append({
            "track": f"p{len(out) + 1}",
            "position": cap_text(person.get("position"), 60) or None,
            "posture": cap_text(person.get("posture"), 60) or None,
            "activities": sorted({cap_text(a, 40) for a in activities if isinstance(a, str) and a})[:MAX_ACTIVITIES],
        })
    return out


def _claims(semantic: Mapping[str, Any] | None, extra: Iterable[str] | None) -> list[str]:
    """Short fallible claims from the typed observation and caller-supplied lines."""
    claims: list[str] = []
    if isinstance(semantic, Mapping):
        summary = cap_text(semantic.get("summary"))
        if summary:
            claims.append(summary)
        for label in semantic.get("activities") or []:
            if isinstance(label, str) and label:
                claims.append(cap_text(f"activity hypothesis: {label}", 80))
    for line in extra or []:
        text = cap_text(line)
        if text:
            claims.append(text)
    return claims[:MAX_CLAIMS]


def _account(semantic: Mapping[str, Any] | None) -> str | None:
    """One capped narrative from the observer's cognitive account, if any."""
    if not isinstance(semantic, Mapping):
        return None
    context = semantic.get("context") or {}
    account = context.get("cognitive_account") if isinstance(context, Mapping) else None
    if isinstance(account, Mapping):
        account = account.get("summary") or account.get("text") or account.get("narrative")
    text = cap_text(account) if account else ""
    return text or None


def build_camera(camera: Mapping[str, Any], path: str = "$.cameras.*") -> dict:
    """One camera block from a typed input dict.

    Input keys (all optional): ``coverage_status``, ``coverage_age_s``,
    ``zones`` (name -> state), ``people`` (position, posture, activities; any
    name or relationship field is dropped), ``semantic`` (the observer's
    ``semantic()`` result), ``claims`` (extra short lines) and
    ``person_adjacent`` (object labels near a person).
    """
    if not isinstance(camera, Mapping):
        raise PacketError(f"{path}: camera input must be a mapping")
    zones = camera.get("zones") or {}
    if not isinstance(zones, Mapping):
        raise PacketError(f"{path}.occupancy.zones: zones must be a mapping")
    zone_names = _capped_names(zones.keys(), f"{path}.occupancy.zones", MAX_ZONES)
    zone_states = [_enum(s, ZONE_STATES, "unknown") for s in zones.values()]
    return {
        "coverage": {
            "status": _enum(camera.get("coverage_status"), COVERAGE_STATUSES, "unavailable"),
            "age_s": clamp_age(camera.get("coverage_age_s")),
        },
        "occupancy": {"zones": dict(zip(zone_names, zone_states))},
        "people": _people(camera.get("people")),
        "claims": _claims(camera.get("semantic"), camera.get("claims")),
        "account": _account(camera.get("semantic")),
        "objects": {"person_adjacent": sorted({cap_text(o, 40) for o in (camera.get("person_adjacent") or []) if o})[:MAX_ADJACENT_OBJECTS]},
    }


def build_media(entries: Iterable[Mapping[str, Any]] | None) -> list[dict]:
    """``devices.media`` rows: role, state, source_kind, age_s. No entity ids."""
    out = []
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        out.append({
            "role": cap_text(entry.get("role"), 40) or "unknown",
            "state": _enum(entry.get("state"), MEDIA_STATES, "unknown"),
            "source_kind": cap_text(entry.get("source_kind"), 40) or "unknown",
            "age_s": clamp_age(entry.get("age_s")),
        })
    if len(out) > MAX_MEDIA:
        raise PacketError(f"$.devices.media: {len(out)} rows, more than {MAX_MEDIA}")
    return out


def _camera_path(index: int) -> str:
    """Error-message path for a camera: always positional, never the name.

    The builder cannot tell a harmless camera name from a household name (it
    has no roster), so the name is never echoed; the caller has the index.
    """
    return f"$.cameras.<key#{index}>"


def build_packet(cameras: Mapping[str, Mapping[str, Any]],
                 media: Iterable[Mapping[str, Any]] | None = None,
                 credible_activity_age_s: Any = None) -> dict:
    """Assemble and validate a ``lighting-beliefs-state/v1`` packet."""
    if not isinstance(cameras, Mapping):
        raise PacketError("$.cameras: cameras must be a mapping of name -> camera")
    names = _capped_names(cameras.keys(), "$.cameras", MAX_CAMERAS)
    packet = {
        "schema": PACKET_SCHEMA,
        "cameras": {name: build_camera(camera, _camera_path(index))
                    for index, (name, camera) in enumerate(zip(names, cameras.values()))},
        "devices": {"media": build_media(media)},
        "quiet": {"credible_activity_age_s": clamp_age(credible_activity_age_s)},
    }
    validate_packet(packet)
    return packet


def _check_leaks(text: str, path: str) -> None:
    hits = leak_guard.scan_text(text)
    if hits:
        names = ", ".join(sorted({name for name, _ in hits}))
        raise PacketError(f"{path}: leak pattern ({names})")


def _validate(node: Any, spec: Any, path: str, schema_path: str) -> None:
    if spec is None:
        if isinstance(node, Mapping):
            raise PacketError(f"{path}: object where a scalar or list was expected")
        if isinstance(node, list):
            _check_list_cap(node, path, schema_path)
            for i, item in enumerate(node):
                if isinstance(item, (Mapping, list)):
                    raise PacketError(f"{path}[{i}]: nested value where a scalar was expected")
                _validate_scalar(item, f"{path}[{i}]")
            return
        _validate_scalar(node, path)
        return
    if isinstance(spec, list):
        if not isinstance(node, list):
            raise PacketError(f"{path}: expected a list")
        _check_list_cap(node, path, schema_path)
        for i, item in enumerate(node):
            _validate(item, spec[0], f"{path}[{i}]", schema_path + "[]")
        return
    if not isinstance(node, Mapping):
        raise PacketError(f"{path}: expected an object")
    if schema_path in MAP_CAPS and len(node) > MAP_CAPS[schema_path]:
        raise PacketError(f"{path}: {len(node)} entries, more than {MAP_CAPS[schema_path]}")
    for index, (key, value) in enumerate(node.items()):
        if not isinstance(key, str):
            raise PacketError(f"{path}: non-string key")
        placeholder = f"{path}.<key#{index}>"
        _check_leaks(key, placeholder)
        if key in spec:
            child, segment, child_path = spec[key], key, f"{path}.{key}"
        elif ANY in spec:
            child, segment, child_path = spec[ANY], ANY, placeholder
        else:
            raise PacketError(f"{placeholder}: key not allowed")
        _validate(value, child, child_path, f"{schema_path}.{segment}")


def _check_list_cap(node: list, path: str, schema_path: str) -> None:
    if schema_path in LIST_CAPS and len(node) > LIST_CAPS[schema_path]:
        raise PacketError(f"{path}: {len(node)} items, more than {LIST_CAPS[schema_path]}")


def _validate_scalar(value: Any, path: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > TEXT_CAP:
            raise PacketError(f"{path}: text longer than {TEXT_CAP}")
        _check_leaks(value, path)
        return
    if isinstance(value, (int, float)):
        if value != value or value < 0 or value > AGE_CAP_S:
            raise PacketError(f"{path}: number outside 0..{AGE_CAP_S}")
        _check_leaks(json.dumps(value), path)
        return
    raise PacketError(f"{path}: unsupported value type {type(value).__name__}")


def validate_packet(packet: Mapping[str, Any]) -> None:
    """Raise PacketError unless every key is allowed, every value is bounded and
    nothing matches a leak pattern. Error messages carry the path only, with
    wildcard (camera, zone) and unknown keys replaced by ``<key#N>``."""
    if not isinstance(packet, Mapping):
        raise PacketError("$: packet must be an object")
    if packet.get("schema") != PACKET_SCHEMA:
        raise PacketError("schema must be " + PACKET_SCHEMA)
    _validate(packet, ALLOWED_KEYS, "$", "$")


def bucket_age(seconds: int | float | None) -> int | None:
    """Coarsen an age to the bucket index it falls in (None stays None)."""
    if seconds is None:
        return None
    for index, edge in enumerate(AGE_BUCKETS_S):
        if seconds < edge:
            return index
    return len(AGE_BUCKETS_S)


def _coarsen(node: Any, spec: Any = ALLOWED_KEYS, schema_path: str = "$") -> Any:
    """Bucket ages by schema path so a zone or camera named ``age_s`` is untouched."""
    if isinstance(node, Mapping):
        out = {}
        for key, value in node.items():
            if isinstance(spec, Mapping) and key in spec:
                child, segment = spec[key], key
            elif isinstance(spec, Mapping) and ANY in spec:
                child, segment = spec[ANY], ANY
            else:
                child, segment = None, key
            out[key] = _coarsen(value, child, f"{schema_path}.{segment}")
        return out
    if isinstance(node, list):
        child = spec[0] if isinstance(spec, list) and spec else None
        return [_coarsen(v, child, schema_path + "[]") for v in node]
    if schema_path in AGE_PATHS and isinstance(node, (int, float)) and not isinstance(node, bool):
        return bucket_age(node)
    return node


def content_signature(packet: Mapping[str, Any]) -> str:
    """SHA-256 of the packet with ages bucketed; stable across key order."""
    canonical = json.dumps(_coarsen(packet), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
