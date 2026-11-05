"""The one adapter between rung 10 and rung 8's light model.

Every prediction rung 10 makes goes through `LightModel` here, and only
`_backend_predict` touches the model's code. Today that is fake_model.py,
built from rung 8's plan, because rung 8 Stage 1 did not exist when rung 10 was
built. When Stage 1's model.py lands, replace `_backend_predict`'s body with a
call to its predict() and keep its argument shape: {bulb: {"u", "k"}}.

The state rung 10 holds is per channel {level 0-1, cct_k}, as in
`Decision.applied_scene[].readback`. The adapter turns a level into rung 8's
brightness command u = level x 254 (Zigbee caps at 254).

rung 8 v1's sample points are image blocks with no surface id. A model may map
surfaces to blocks with `"surfaces": {surface_id: [point_id, ...]}`; a surface's
prediction is then the mean over its blocks. Without that map, point ids are
taken to be surface ids, as in the fake model.
"""
import json
from pathlib import Path

import fake_model

U_FULL = 254.0
BACKEND = "fake_model (rung 8 plan; Stage 1 not built)"


def _backend_predict(model, scene):
    return fake_model.predict(model, scene)


class LightModel:
    def __init__(self, data):
        self.data = data
        self.version = data["version"]
        self.basis_unit = data.get("basis_unit", "relative")
        self.backend = BACKEND
        self.sample_points = {p["point_id"]: p for p in data["sample_points"]}
        self.surfaces = data.get("surfaces") or {p: [p] for p in self.sample_points}
        self.channels = set(data["basis"])

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
        for channel, s in state.items():
            level = float(s.get("level") or 0)
            if level <= 0:
                continue
            if channel not in self.channels:
                raise KeyError(f"light model {self.version} has no basis for {channel}")
            scene[channel] = {"u": level * U_FULL, "k": s.get("cct_k")}
        return scene

    def predict(self, state):
        """Electric light at every surface for a state {channel: {level, cct_k}}."""
        by_point = _backend_predict(self.data, self._scene(state))
        return {sid: sum(by_point[b] for b in blocks) / len(blocks)
                for sid, blocks in self.surfaces.items()}

    def contributions(self, state):
        """Each channel's part of predict(state), surface by surface."""
        return {channel: self.predict({channel: s}) for channel, s in state.items()}
