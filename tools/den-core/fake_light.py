"""A fake of rung 8's light model, built from its plan (docs/plans/rung-8-light-model/PLAN.md).

Stage 1's own predict() is being built in another session, so this stands in for it until it lands. Only
light_adapter.py imports this module, and swapping the fake for the real one changes that one file.

    Ê(p) = Σ_c g_c(u_c) · B_c(p, k_c), over the channels that are on

u is the Hue command (level x 254), g a dimming table, linear in u between its levels, and B a channel alone at full,
linear in mired between its captured temperatures and clamped outside them. Electric light only: rung 8 does not
model daylight. `build()` writes a light-model.json dict with the plan's fields, plus `surfaces` (the rung 9 map from
a surface to its blocks) and `fake: true`, which score.py refuses as evidence.

The numbers are invented: they make the room's shape easy to reason about, and they say nothing about the den.
Standard library only; no clock, randomness, file or network.
"""
from events import LIGHTS

FULL = 254
LEVELS = (1, 2, 4, 8, 16, 32, 64, 96, 128, 176, 224, 254)  # the plan's dimming steps, plus full
KELVINS = (2202, 2700, 6500)
VERSION = "lm-fake-rung8-plan-1"
# Lux at each surface with one bulb alone at full and 2700 K. rear_left never reaches the counter, so a bound at the
# counter still leaves it free to serve the armchair (case V62).
SHARES = {
    "light.den_front_left": {"counter-1": 300.0, "armchair-book": 60.0, "wall-west": 80.0},
    "light.front_right": {"counter-1": 120.0, "armchair-book": 90.0, "wall-west": 40.0},
    "light.den_rear_left": {"counter-1": 0.0, "armchair-book": 260.0, "wall-west": 90.0},
    "light.den_rear_right": {"counter-1": 30.0, "armchair-book": 60.0, "wall-west": 40.0},
}
CCT_SCALE = {2202: 0.9, 2700: 1.0, 6500: 1.2}
BLOCKS_PER_SURFACE = 4


def channel_key(entity_id):
    return entity_id.replace("light.", "") + ".white"


def _curve(u):
    return round((u / FULL) ** 1.5, 9)  # invented: bulbs dim faster than the command


def _ripple(n):
    return 1.0 + 0.04 * (n - (BLOCKS_PER_SURFACE - 1) / 2)  # a little spread, so a patch is not one flat number


def build(filler=0, unit="lx", glare=None, spot=None, surfaces=True, error=None):
    """The fake light-model.json. filler adds that many blocks outside every named surface (the real grid is 32 x 18),
    glare is {entity_id: {eye: {"G": full-output G, "sd": sd}}}, spot is {entity_id: {aim: {surface: lux}}}."""
    names = sorted(next(iter(SHARES.values())))
    blocks = {name: ["%s/%d" % (name, n) for n in range(BLOCKS_PER_SURFACE)] for name in names}
    extra = ["other/%d" % n for n in range(filler)]
    points = [p for name in names for p in blocks[name]] + extra
    channels, basis, cct, dimming = [], {}, {}, {}
    for entity_id in LIGHTS:
        key = channel_key(entity_id)
        channels.append({"channel_key": key, "fixture_id": key.split(".")[0], "channel_id": "white",
                         "entity_id": entity_id, "kind": "bulb", "cct_range_k": [KELVINS[0], KELVINS[-1]]})
        keys = []
        for kelvin in KELVINS:
            row = {}
            for name in names:
                for n, point in enumerate(blocks[name]):
                    row[point] = round(SHARES[entity_id][name] * CCT_SCALE[kelvin] * _ripple(n), 6)
            row.update({point: 1.0 for point in extra})
            keys.append("%s@%dK" % (key, kelvin))
            basis[keys[-1]] = row
        cct[key] = {"kelvin": list(KELVINS), "basis_keys": keys, "entity_id": entity_id}
        dimming[key] = {"levels": list(LEVELS), "g": [_curve(u) for u in LEVELS], "shared_with": None}
    model = {
        "version": VERSION, "room_version": "fake-room-1", "fake": True,
        "sample_points": [{"point_id": p, "kind": None, "xyz": None, "normal": None} for p in points],
        "channels": channels, "basis": basis, "basis_unit": unit, "cct": cct, "dimming": dimming,
        "bounce": "measured", "spot_model": None, "glare": None,
        "error": error or {"median_pct": None, "p90_pct": None},
    }
    if surfaces:
        model["surfaces"] = blocks
    if glare:
        model["glare"] = {channel_key(e): {eye: {**row, "model_status": "unvalidated"} for eye, row in rows.items()}
                          for e, rows in glare.items()}
    if spot:
        model["spot_model"] = {channel_key(e): {aim: {p: lux[name] for name in names for p in blocks[name]}
                                                for aim, lux in aims.items()} for e, aims in spot.items()}
    return model


def _table(levels, values, u):
    if u <= levels[0]:
        return values[0]
    for lo, hi, a, b in zip(levels, levels[1:], values, values[1:]):
        if u <= hi:
            return a + (b - a) * (u - lo) / (hi - lo)
    return values[-1]


def _temperature_mix(cct, kelvin):
    """[(basis key, weight)] for a colour temperature, linear in mired between neighbours and clamped outside."""
    pairs = sorted(zip(cct["kelvin"], cct["basis_keys"]), key=lambda pair: 1e6 / pair[0])
    target = 1e6 / kelvin
    if target <= 1e6 / pairs[0][0]:
        return [(pairs[0][1], 1.0)]
    for (cool, cool_key), (warm, warm_key) in zip(pairs, pairs[1:]):
        if target <= 1e6 / warm:
            share = (target - 1e6 / cool) / (1e6 / warm - 1e6 / cool)
            return [(cool_key, 1.0 - share), (warm_key, share)]
    return [(pairs[-1][1], 1.0)]


def predict(scene, model):
    """rung 8's published call: predict({channel_key: {level 0..1, cct_k[, aim]}}, model) ->
    {"surfaces": {point_id: value or None, ..., "unit": unit}, "version": version, "daylight": "excluded"}."""
    points = [p["point_id"] for p in model["sample_points"]]
    known = {c["channel_key"] for c in model["channels"]}
    total = dict.fromkeys(points, 0.0)
    for key, setting in scene.items():
        if key not in known:
            raise ValueError("the light model has no channel %r" % key)
        level = setting.get("level")
        if not level:
            continue
        dim = model["dimming"][key]
        g = _table(dim["levels"], dim["g"], level * FULL)
        aim = setting.get("aim")
        if aim is not None:
            mix = [(model["spot_model"][key][aim], 1.0)]
        else:
            if not setting.get("cct_k"):
                raise ValueError("%s is on and needs cct_k" % key)
            mix = [(model["basis"][basis_key], weight) for basis_key, weight in _temperature_mix(model["cct"][key],
                                                                                                setting["cct_k"])]
        for row, weight in mix:
            for p in points:
                value = row.get(p)
                if value is None:
                    total[p] = None
                elif total[p] is not None:
                    total[p] += g * weight * value
    return {"surfaces": {**total, "unit": model["basis_unit"]}, "version": model["version"], "daylight": "excluded"}
