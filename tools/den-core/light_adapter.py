"""The one adapter between den-core and rung 8's light model.

Every prediction den-core makes goes through LightModel here, both arms alike, and only `_predict` touches the
model's code. Today that is fake_light.py, because rung 8 Stage 1's predict.py is still being built. When it lands:
replace `_predict`'s body with `predict.predict(scene, model)` (same arguments, same result) and set BACKEND, and
nothing else changes.

den-core speaks Hue scenes {on, brightness 0-255, kelvin}; rung 8 speaks {channel_key: {level 0..1, cct_k}}, with
level the command as a share of Hue's 254. A surface's value is the mean over its blocks: rung 8 v1's points are image
blocks with no surface id, so a surface map comes from the model's own `surfaces` or from `surfaces=` here. There is
always a surface 'room', for a person with no working region: the blocks a map names for it, else the whole view.

A bulb that is on with no brightness or no kelvin (HA's recorder keeps neither) has no prediction: its value is None,
and so is every total it is part of. Nothing guesses a level.
"""
import json
from pathlib import Path

import events
import fake_light

BACKEND = "fake_light (rung 8 plan; Stage 1's predict.py not merged)"
HUE_FULL = 254
ROOM = "room"


def _predict(scene, model):
    return fake_light.predict(scene, model)


def bad_surfaces(surfaces, points):
    """The surfaces of a map with no block, or with a block the model lacks, which its mean would leave out with no word."""
    known = set(points)
    return sorted(name for name, blocks in surfaces.items() if not blocks or not known.issuperset(blocks))


def describe(spec):
    """(model dict, what to log) for a config's light_model spec: {source: fake, params} or {source: file, path}."""
    if spec["source"] == "fake":
        model = fake_light.build(**spec.get("params", {}))
        logged = {"source": "fake", "params": spec.get("params", {})}
    elif spec["source"] == "file":
        try:
            model = json.loads(Path(spec["path"]).read_bytes().decode("utf-8-sig"))
        except OSError as error:
            raise ValueError("light model unreadable: %s" % error) from None
        logged = {"source": "file", "path": str(spec["path"])}
    else:
        raise ValueError("light_model source must be fake or file: %r" % (spec["source"],))
    sha = events.sha256_json(model)
    if spec.get("sha256", sha) != sha:
        raise ValueError("light model changed: %s" % spec.get("path", "fake"))
    logged.update(sha256=sha, version=model["version"], unit=model["basis_unit"], fake=bool(model.get("fake")))
    if spec.get("surfaces"):
        logged["surfaces"] = spec["surfaces"]
    return model, logged


class LightModel:
    """One light-model.json, as a function of Hue scenes. It keeps each channel's prediction per setting, since Ê is a
    sum over channels and a solve asks for the same settings many times."""

    def __init__(self, model, surfaces=None):
        self.model = model
        self.version = model["version"]
        self.unit = model["basis_unit"]
        self.fake = bool(model.get("fake")) or BACKEND.startswith("fake")
        self.points = [p["point_id"] for p in model["sample_points"]]
        self.keys = {c["entity_id"]: c["channel_key"] for c in model["channels"]}
        self.surfaces = {ROOM: self.points, **(surfaces or model.get("surfaces") or {})}  # 'room' unmapped: the whole view
        self.error = model.get("error") or {}
        self.glare_table = model.get("glare")
        self.spots = model.get("spot_model") or {}
        self._cache = {}

    def _point_values(self, entity_id, scene):
        """rung 8's answer for one channel: {point_id: value} (values may be None), or None without a level."""
        if not scene["on"]:
            return {}
        aim = scene.get("aim")
        if scene.get("brightness") is None or (aim is None and scene.get("kelvin") is None):
            return None
        tag = (entity_id, scene["brightness"], scene.get("kelvin"), aim)
        if tag not in self._cache:
            setting = {"level": min(HUE_FULL, scene["brightness"]) / HUE_FULL, "cct_k": scene.get("kelvin")}
            if aim is not None:
                setting["aim"] = aim
            answer = _predict({self.keys[entity_id]: setting}, self.model)["surfaces"]
            values = {p: answer[p] for p in self.points}
            self._cache[tag] = (values, {name: self._mean(values, blocks) for name, blocks in self.surfaces.items()})
        return self._cache[tag][0]

    @staticmethod
    def _mean(values, blocks):
        known = [values[p] for p in blocks if values.get(p) is not None]
        return sum(known) / len(known) if known else None

    def contribution(self, entity_id, scene):
        """One channel's part at every surface, or None when the scene has no level."""
        if scene is None or entity_id not in self.keys:
            return None
        if not scene["on"]:
            return dict.fromkeys(self.surfaces, 0.0)
        if self._point_values(entity_id, scene) is None:
            return None
        return self._cache[(entity_id, scene["brightness"], scene.get("kelvin"), scene.get("aim"))][1]

    def predict(self, scene):
        """{surface: Ê} for a whole scene {entity_id: Hue scene}. A value is None while any bulb has no level."""
        total = dict.fromkeys(self.surfaces, 0.0)
        for entity_id, one in scene.items():
            part = self.contribution(entity_id, one)
            for name in total:
                total[name] = None if part is None or total[name] is None or part[name] is None else total[name] + part[name]
        return total

    def patch(self, scene, surface_id):
        """The per-block values of one surface for a whole scene, or None while any bulb has no level. A block the
        model excluded is None."""
        blocks = self.surfaces[surface_id]
        total = dict.fromkeys(blocks, 0.0)
        for entity_id, one in scene.items():
            values = self._point_values(entity_id, one) if one is not None else None
            if values is None:
                return None
            for p in blocks:
                total[p] = None if total[p] is None or (one["on"] and values.get(p) is None) else total[p] + values.get(p, 0.0)
        return [total[p] for p in blocks]

    def aims(self, entity_id):
        """The aims a channel's spot model offers; [None] for a channel with no aim, which is every Hue bulb."""
        return sorted(self.spots.get(self.keys.get(entity_id), {})) or [None]

    def glare(self, entity_id, eye):
        """rung 8's glare row {G: at full output, sd, model_status} for a channel and eye, or None. Its plan writes
        `glare: null`: no eye positions yet, and comfort-policy is pending."""
        return ((self.glare_table or {}).get(self.keys.get(entity_id)) or {}).get(eye)
