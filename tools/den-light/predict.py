#!/usr/bin/env python3
"""Rung 8's light model, evaluated. Pure: standard library only, no I/O, nothing runs on import.

    import json, predict
    model = json.load(open(r"C:\\den-light\\<session-id>\\light-model.json", encoding="utf-8"))
    out = predict.predict({"den_front_left.white": {"level": 0.5, "cct_k": 2700}}, model)
    out["surfaces"]["r03c12"]   # one point's predicted electric light
    out["surfaces"]["unit"]     # "relative" (normalised counts) until a room version is anchored

predict(scene, model, daylight=None) -> {"surfaces": {point_id: value | None, ..., "unit": str},
                                         "version": str, "daylight": "excluded" | "included"}

- scene: per channel, keyed as the light_model's `channels` name them
  (`<fixture_id>.<channel_id>`, as Decision.requested_scene does): {"level": 0..1,
  "cct_k": kelvin}. level is the command as a share of full (Hue's 254); a
  channel left out, or with level 0 or None, is off. A channel that is on needs
  cct_k. Unknown channels raise ValueError.
- model: the light-model.json dict (or prepare(model), to skip re-reading it on
  every call).
- daylight: optional {point_id: D}; when given, it is added (rung 8 does not
  model daylight; the camera's all-off reading is D).
- the result's `surfaces` has every sample point, in light-model.json's order,
  and the `unit` key, as Decision.predicted.surfaces holds them. A point the
  fit excluded is None.

Ê(p) = Σ_c g_c(u_c) · B_c(p, k_c): g_c linear in the command u between the
fitted levels, B_c linear in mired between the captured temperatures and
clamped outside them.
"""
FULL_LEVEL = 254


def mired(kelvin):
    return 1e6 / kelvin


def g_at(levels, values, u):
    """A dimming table at command u, linear between levels, clamped to its ends."""
    if u <= levels[0]:
        return values[0]
    for (u0, g0), (u1, g1) in zip(zip(levels, values), zip(levels[1:], values[1:])):
        if u <= u1:
            return g0 + (g1 - g0) * (u - u0) / (u1 - u0)
    return values[-1]


def basis_weights(temperatures, kelvin):
    """[(temperature, weight)] for a kelvin: linear in mired between neighbours, clamped outside."""
    temps = sorted(temperatures, key=mired)  # warm (high mired) last
    m = mired(kelvin)
    if m <= mired(temps[0]):
        return [(temps[0], 1.0)]
    if m >= mired(temps[-1]):
        return [(temps[-1], 1.0)]
    for a, b in zip(temps, temps[1:]):
        if mired(a) <= m <= mired(b):
            w = (m - mired(a)) / (mired(b) - mired(a))
            return [(a, 1.0 - w), (b, w)]
    raise ValueError("unreachable")


def electric(scene, channels, points):
    """Ê at each point. scene {key: {"u": command, "kelvin": k}}; channels {key: {"temperatures": [k],
    "basis": {k: [value | None per point]}, "levels": [u], "values": [g]}}."""
    out = [0.0] * points
    for key, setting in scene.items():
        u = setting.get("u") or 0
        if u <= 0:
            continue
        channel = channels[key]
        g = g_at(channel["levels"], channel["values"], u)
        for k, w in basis_weights(channel["temperatures"], setting["kelvin"]):
            row = channel["basis"][k]
            for p in range(points):
                if out[p] is None:
                    continue
                if row[p] is None:
                    out[p] = None
                else:
                    out[p] += g * w * row[p]
    return out


class Prepared:
    """light-model.json read once into per-channel tables."""

    def __init__(self, model):
        self.version = model["version"]
        self.unit = model["basis_unit"]
        self.points = [p["point_id"] for p in model["sample_points"]]
        self.channels = {}
        for channel in model["channels"]:
            key = channel["channel_key"]
            cct, dimming = model["cct"][key], model["dimming"][key]
            self.channels[key] = {
                "temperatures": list(cct["kelvin"]),
                "basis": {k: [model["basis"][bk].get(pid) for pid in self.points]
                          for k, bk in zip(cct["kelvin"], cct["basis_keys"])},
                "levels": list(dimming["levels"]), "values": list(dimming["g"])}


def prepare(model):
    return model if isinstance(model, Prepared) else Prepared(model)


def predict(scene, model, daylight=None):
    """The predicted light at every sample point of the model for one scene (see the module doc)."""
    prepared = prepare(model)
    settings = {}
    for key, value in (scene or {}).items():
        if key not in prepared.channels:
            raise ValueError(f"the light model has no channel {key!r}")
        level = (value or {}).get("level")
        if level is None or level <= 0:
            continue
        if not 0 < level <= 1:
            raise ValueError(f"{key}: level must be a share of full, 0 to 1")
        kelvin = value.get("cct_k")
        if type(kelvin) not in (int, float) or kelvin <= 0:
            raise ValueError(f"{key}: a channel that is on needs cct_k")
        settings[key] = {"u": level * FULL_LEVEL, "kelvin": kelvin}
    values = electric(settings, prepared.channels, len(prepared.points))
    if daylight is not None:
        values = [None if v is None or daylight.get(pid) is None else v + daylight[pid]
                  for v, pid in zip(values, prepared.points)]
    surfaces = dict(zip(prepared.points, values))
    surfaces["unit"] = prepared.unit
    return {"surfaces": surfaces, "version": prepared.version,
            "daylight": "excluded" if daylight is None else "included"}


def scene_from_ha(ha_scene, model):
    """A Home Assistant scene {entity_id: {on, brightness, kelvin}} as predict()'s per-channel scene."""
    by_entity = {c["entity_id"]: c["channel_key"] for c in model["channels"]}
    out = {}
    for entity_id, s in ha_scene.items():
        if entity_id not in by_entity:
            raise ValueError(f"the light model has no channel for {entity_id}")
        on = s.get("on") and s.get("brightness")
        out[by_entity[entity_id]] = {"level": min(FULL_LEVEL, s["brightness"]) / FULL_LEVEL if on else 0,
                                     "cct_k": s.get("kelvin")}
    return out
