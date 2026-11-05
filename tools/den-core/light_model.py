"""The light model adapter: every call den-core makes to rung 8's light model goes through LightModel here.

Rung 8 Stage 1 (tools/den-light, branch claude/rung-8-stage1) is not built yet, so this adapter predicts with the
formula rung 8's plan (docs/plans/rung-8-light-model/PLAN.md on claude/plan-rung-8) gives for its
light-model.json, and fake() builds a model in that shape from invented numbers:

    E(b) = sum over the bulbs that are on of g_c(u_c) * B_c(b, k_c)

- b is a sample point (a block of rung 8's grid; v1 blocks have no kind, xyz or normal);
- u_c is the bulb's brightness command, 1 to 254 (den_lighting's 255 is sent as Zigbee's 254);
- g_c is the bulb's dimming table, linear in u between its levels, with g(0) = 0 and g(254) = 1;
- B_c(b, k) is the bulb alone at full at temperature k, piecewise-linear in mired between the captured
  temperatures and clamped outside them, in basis_unit (relative until rung 7 anchors the room);
- rung 8 does not model daylight, and writes glare: null.

When rung 8 Stage 1 lands, LightModel.predict calls its predict() instead of _predict, and load() reads its
light-model.json; nothing else in den-core changes. A fake model is marked fake, and score.py refuses a scored run
on one. Standard library only; it reads no file, clock or network itself (run.py reads a light-model.json and
hands this module the parsed object).
"""
import copy
import hashlib
import json

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
FORMAT = "den-light-model (den-core's reading of rung 8's plan for light-model.json)"
U_MAX = 254


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode("ascii")).hexdigest()


def fake():
    """A light-model.json-shaped model with invented numbers: four bulbs and five named points in a den, the
    plan's dimming steps and three captured temperatures. Plumbing only: never evidence."""
    points = ("desk-1", "armchair-1", "wall-front", "wall-rear", "floor-centre")
    at_mid = {  # bulb alone at full, middle temperature, relative units: the fronts light the desk, the rears
        LIGHTS[0]: (1.00, 0.00, 0.60, 0.05, 0.40),  # the armchair, and the rear left reaches both
        LIGHTS[1]: (0.45, 0.00, 0.60, 0.05, 0.40),
        LIGHTS[2]: (0.15, 0.45, 0.05, 0.60, 0.40),
        LIGHTS[3]: (0.00, 1.00, 0.05, 0.60, 0.40),
    }
    captured = {"2202": 0.9, "3290": 1.0, "6500": 1.05}  # warm end all four share, middle in mired, cool end
    levels = [0, 1, 2, 4, 8, 16, 32, 64, 96, 128, 176, 224, 254]
    curve = [round((u / U_MAX) ** 1.8, 6) for u in levels]
    return {
        "format": FORMAT,
        "version": "fake-plan-v0",
        "fake": True,
        "fake_note": "invented numbers in the shape of rung 8's light-model.json; plumbing only, never evidence",
        "room_version": None,
        "basis_unit": "relative",
        "sample_points": [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None} for p in points],
        "basis": {e: {k: {p: round(v * f, 6) for p, v in zip(points, at_mid[e])} for k, f in captured.items()}
                  for e in LIGHTS},
        "dimming": {e: {"levels": levels, "g": curve} for e in LIGHTS},
        "dimming_shared": "fronts share one curve, rears another (here all four are equal)",
        "cct": {"captured_k": sorted(int(k) for k in captured), "interpolation": "piecewise-linear in mired, clamped"},
        "bounce": "measured",
        "glare": None,
        "error": None,
    }


def check(model):
    """Raise ValueError unless model has what predict() reads."""
    try:
        points = [p["point_id"] for p in model["sample_points"]]
        if not isinstance(model["version"], str) or len(set(points)) != len(points):
            raise ValueError("version and unique point ids")
        for e in LIGHTS:
            table = model["dimming"][e]
            levels, g = table["levels"], table["g"]
            if levels[0] != 0 or levels[-1] != U_MAX or len(levels) != len(g) or levels != sorted(set(levels)):
                raise ValueError("dimming levels for %s" % e)
            if g[0] != 0 or g[-1] != 1 or any(b < a for a, b in zip(g, g[1:])):
                raise ValueError("dimming curve for %s" % e)
            for k, basis in model["basis"][e].items():
                int(k)
                if set(basis) != set(points):
                    raise ValueError("basis points for %s at %s K" % (e, k))
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("not a light model: %r" % (error,)) from None


class LightModel:
    """One light model version. predict() and contributions() are the only ways den-core asks it anything."""

    def __init__(self, model):
        check(model)
        self.model = copy.deepcopy(model)
        self.version = model["version"]
        self.fake = bool(model.get("fake"))
        self.basis_unit = model.get("basis_unit")
        self.sha256 = _sha(model)
        self.points = tuple(p["point_id"] for p in model["sample_points"])
        self.glare_model = model.get("glare")
        self._basis = {e: sorted((1e6 / int(k), basis) for k, basis in model["basis"][e].items()) for e in LIGHTS}

    def describe(self):
        return {"version": self.version, "sha256": self.sha256, "fake": self.fake, "basis_unit": self.basis_unit,
                "glare": "none: the model writes glare null" if self.glare_model is None else "modelled"}

    def _g(self, entity_id, u):
        table = self.model["dimming"][entity_id]
        levels, g = table["levels"], table["g"]
        u = min(max(u, 0), U_MAX)
        for n in range(1, len(levels)):
            if u <= levels[n]:
                lo, hi = levels[n - 1], levels[n]
                return g[n - 1] + (g[n] - g[n - 1]) * (u - lo) / (hi - lo)
        return 1.0

    def _b(self, entity_id, kelvin, point):
        mired, rows = 1e6 / kelvin, self._basis[entity_id]
        if mired <= rows[0][0]:
            return rows[0][1][point]
        for (m0, b0), (m1, b1) in zip(rows, rows[1:]):
            if mired <= m1:
                return b0[point] + (b1[point] - b0[point]) * (mired - m0) / (m1 - m0)
        return rows[-1][1][point]

    def contribution(self, entity_id, scene, points):
        """{point: this bulb's part of E} for one bulb scene; None where its level or temperature is unknown."""
        if scene is None:
            return {p: None for p in points}
        if not scene["on"]:
            return {p: 0.0 for p in points}
        b, k = scene.get("brightness"), scene.get("kelvin")
        if b is None or k is None:
            return {p: None for p in points}
        u = 0 if b <= 0 else max(1, min(b, U_MAX))
        g = self._g(entity_id, u)
        return {p: g * self._b(entity_id, k, p) for p in points}

    def contributions(self, scenes, points=None):
        """{bulb: {point: part}} for a scene of the four bulbs; a bulb absent from scenes is unknown."""
        points = self.points if points is None else tuple(points)
        return {e: self.contribution(e, scenes.get(e), points) for e in LIGHTS}

    def predict(self, scenes, points=None):
        """{point: E} for a scene of the four bulbs, in basis_unit; None where any bulb's part is unknown."""
        parts = self.contributions(scenes, points)
        points = self.points if points is None else tuple(points)
        out = {}
        for p in points:
            values = [parts[e][p] for e in LIGHTS]
            out[p] = None if any(v is None for v in values) else sum(values)
        return out

    def glare(self, scenes, eyes):
        """Predicted glare at each eye position, or None: rung 8's plan writes glare: null, so nothing is modelled
        yet and no hard glare constraint can be checked numerically."""
        return None
