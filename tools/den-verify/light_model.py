"""The one adapter between rung 10 and rung 8's light model.

Every prediction rung 10 makes goes through `LightModel` here, and only
`_backend_predict` touches the model's code: rung 8 Stage 1's
`predict(scene, model)` in tools/den-light/predict.py (standard library only,
no I/O). That folder goes on the END of sys.path, because both folders hold a
`synthetic.py` and rung 10's own must stay first.

`light-model.json` is the record `model.light_model` writes: `channels` lists
each channel's `channel_key` (`<fixture_id>.<channel_id>`) and its Home
Assistant `entity_id`; `basis` is keyed `<channel_key>@<kelvin>K`, and `cct`
and `dimming` are keyed by channel_key.

The state rung 10 holds is per channel {level 0-1, cct_k}, as in
`Decision.applied_scene[].readback`, which is predict()'s own scene shape. A
state is keyed as its Decision keys it: by channel key (`F1.spot`, as
schemas.json's walkthrough does) or by entity id (`light.den_front_left`, as
den-core's hue-den-4 Decisions do); an entity id is looked up in `channels`.
A level above full counts as full, as predict.scene_from_ha caps it. A channel
that is on with no cct_k is predicted only when the model captured a single
temperature for it, because predict() then gives the same light at any kelvin.
A state the model cannot predict raises KeyError, which score.py reports as
the decision's reason for not being verified.

predict() gives None at a point the fit excluded, in every scene. Such a point
(or one a basis lacks a number for) is no surface here. rung 8 v1's sample
points are image blocks with no surface id. A model may map surfaces to blocks
with `"surfaces": {surface_id: [point_id, ...]}`; a surface's prediction is
then the mean over its blocks. Without that map, each point is a surface.
"""
import json
import sys
from pathlib import Path

DEN_LIGHT = Path(__file__).resolve().parent.parent / "den-light"
if str(DEN_LIGHT) not in sys.path:
    sys.path.append(str(DEN_LIGHT))

import predict  # noqa: E402  rung 8 Stage 1's, from DEN_LIGHT

BACKEND = "predict.py (rung 8 Stage 1, tools/den-light)"


def _backend_predict(scene, model):
    return predict.predict(scene, model)["surfaces"]


class LightModel:
    def __init__(self, data):
        self.data = data
        self.version = data["version"]
        self.basis_unit = data.get("basis_unit", "relative")
        self.backend = BACKEND
        self.sample_points = {p["point_id"]: p for p in data["sample_points"]}
        rows = list(data["basis"].values())
        unknown = {pid for pid, p in self.sample_points.items()
                   if p.get("excluded") or any(row.get(pid) is None for row in rows)}
        given = data.get("surfaces") or {p: [p] for p in self.sample_points}
        known = {sid: [b for b in blocks if b not in unknown] for sid, blocks in given.items()}
        self.surfaces = {sid: blocks for sid, blocks in known.items() if blocks}
        self.channels = {c["channel_key"] for c in data["channels"]}
        self.keys = {c["entity_id"]: c["channel_key"] for c in data["channels"] if c.get("entity_id")}
        self.keys.update({key: key for key in self.channels})
        self.temperatures = {key: list(data["cct"][key]["kelvin"]) for key in self.channels}
        self._prepared = predict.prepare(data)

    @classmethod
    def load(cls, path):
        text = Path(path).read_bytes().decode("utf-8-sig")
        return cls(json.loads(text))

    def has(self, surface_id):
        return surface_id in self.surfaces

    def kind(self, surface_id):
        blocks = self.surfaces.get(surface_id) or []
        return self.sample_points[blocks[0]].get("kind") if len(blocks) == 1 else None

    def xy(self, surface_id):
        blocks = self.surfaces.get(surface_id) or []
        xyz = self.sample_points[blocks[0]].get("xyz") if len(blocks) == 1 else None
        return (float(xyz[0]), float(xyz[1])) if xyz else None

    def _scene(self, state):
        scene = {}
        for key, s in state.items():
            level = float(s.get("level") or 0)
            if level <= 0:
                continue
            channel = self.keys.get(key, key)  # an unknown key reaches predict(), which names it
            cct_k = s.get("cct_k")
            if cct_k is None and len(self.temperatures.get(channel, ())) == 1:
                cct_k = self.temperatures[channel][0]
            scene[channel] = {"level": min(level, 1.0), "cct_k": cct_k}
        return scene

    def predict(self, state):
        """Electric light at every surface for a state {channel: {level, cct_k}}."""
        try:
            by_point = _backend_predict(self._scene(state), self._prepared)
        except ValueError as e:
            raise KeyError(f"light model {self.version}: {e}") from None
        return {sid: sum(by_point[b] for b in blocks) / len(blocks)
                for sid, blocks in self.surfaces.items()}

    def contributions(self, state):
        """Each channel's part of predict(state), surface by surface."""
        return {channel: self.predict({channel: s}) for channel, s in state.items()}
