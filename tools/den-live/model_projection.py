"""Public-safe views of producer-owned evidence and separately observed model stages."""
import math
import re

import forecast_spatial
import motion

STAGE_SCHEMA = "den-model-stages-1"
STATES = frozenset(("disabled", "not_admitted", "provisioned", "preparing", "ready", "idle", "running",
                    "draining", "released", "degraded", "unavailable", "externally_managed"))
WORD = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def _text(value):
    return value if isinstance(value, str) and WORD.fullmatch(value) else None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def stage(value, now_mono):
    """A status sample never becomes fresh merely because the people stream republishes it."""
    observed, until = value.get("observed_mono"), value.get("valid_until_mono")
    fresh = (all(_finite(v) for v in (observed, until, now_mono))
             and observed <= now_mono < until <= observed + 5)
    state = value.get("state") if value.get("state") in STATES else "unavailable"
    if not fresh:
        state = "unavailable"
    output = {"id": _text(value.get("id")), "role": _text(value.get("role")), "state": state,
              "model_id": _text(value.get("model_id")), "model_revision": _text(value.get("model_revision")),
              "profile_digest": _text(value.get("profile_digest")),
              "device": _text(value.get("device")) if fresh else None,
              "reason": _text(value.get("reason")) if fresh else "stage_observation_stale",
              "observed_mono": observed if _finite(observed) else None,
              "valid_until_mono": until if _finite(until) else None,
              "clock": "producer_monotonic"}
    for key in ("configured", "provisioned", "loaded"):
        v = value.get(key)
        output[key] = v if type(v) is bool and (fresh or key == "configured") else None
    for key in ("queued", "running"):
        v = value.get(key)
        output[key] = v if fresh and type(v) is int and 0 <= v <= 128 else None
    output["request_id"] = _text(value.get("request_id")) if fresh else None
    return output


def project(event, snapshot, now_mono, *, stages=(), hints=()):
    """Decorate a copied event; existing marker/body observations are never modified."""
    out = dict(event)
    health = dict(event.get("health") or {})
    health["model_stages"] = {"schema": STAGE_SCHEMA, "sampled_mono": now_mono,
                              "clock": "producer_monotonic",
                              "stages": [stage(value, now_mono) for value in tuple(stages)[:16]]}
    out["health"] = health
    tracks = {track.track_id: track for track in snapshot.tracks}
    projected = []
    for marker in event.get("tracks") or ():
        row = dict(marker)
        track = tracks.get(row.get("track_id"))
        packet, reason = None, "motion_unavailable"
        if track is not None:
            value = motion.forecast(track.motion, now_mono, frame=snapshot.frame, track_id=track.track_id,
                                    association_revision=track.association_revision)
            packet, reason = forecast_spatial.qualify(value, snapshot, track, now_mono)
        row["motion_forecast"] = packet
        row["motion_forecast_reason"] = reason
        projected.append(row)
    out["tracks"] = projected
    out["intent_hints"] = [hint.public(now_mono=now_mono, snapshot=snapshot,
                                       track=tracks.get(hint.dependencies["track_id"]))
                           for hint in tuple(hints)[-2:]]
    return out
