"""A stand-in for rung 8 Stage 1's predict(), built from its plan.

`origin/claude/rung-8-stage1` did not exist when rung 10 was built, so this
follows docs/plans/rung-8-light-model/PLAN.md on claude/plan-rung-8:

    Ê(b) = Σ_c g_c(u_c) · B_c(b, k_c), over the bulbs that are on

with u the brightness command (0 to 254), k the colour temperature in kelvin,
B_c(b, k) a bulb alone at full minus all-off (interpolated piecewise-linearly
in mired between captured temperatures, clamped outside), and g_c(u) a dimming
table, linear in u between levels, g(0) = 0. The electric light only: "rung 8
does not model daylight". `light-model.json` carries the plan's fields:
`version`, `room_version`, `sample_points`, `basis` (per bulb, per kelvin, per
point), `basis_unit`, `dimming` (a table per bulb, or {"same_as": bulb} for a
shared curve), `bounce`, `glare`, `error`.

Only light_model.py calls this module.
"""


def _interp(table, x):
    pts = sorted((float(a), float(b)) for a, b in table)
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def dimming(model, bulb, u):
    table = model["dimming"][bulb]
    while isinstance(table, dict):
        table = model["dimming"][table["same_as"]]
    if u <= 0:
        return 0.0
    return _interp(table, u)


def basis(model, bulb, k):
    """B_c(·, k) at every point, interpolated in mired between captured kelvins."""
    by_k = model["basis"][bulb]
    kelvins = sorted((float(kk), kk) for kk in by_k)
    if k is None:
        if len(kelvins) != 1:
            raise ValueError(f"{bulb} needs a colour temperature: basis has {len(kelvins)}")
        return dict(by_k[kelvins[0][1]])
    mired = 1e6 / float(k)
    # mired falls as kelvin rises, so walk from the warm end (highest mired).
    pts = sorted(((1e6 / kv, name) for kv, name in kelvins), reverse=True)
    if mired >= pts[0][0]:
        return dict(by_k[pts[0][1]])
    if mired <= pts[-1][0]:
        return dict(by_k[pts[-1][1]])
    for (m0, n0), (m1, n1) in zip(pts, pts[1:]):
        if m1 <= mired <= m0:
            w = (m0 - mired) / (m0 - m1)
            a, b = by_k[n0], by_k[n1]
            return {p: a[p] + (b[p] - a[p]) * w for p in a}
    raise AssertionError("unreachable")


def predict(model, scene):
    """scene: {bulb: {"u": 0..254, "k": kelvin or None}} -> {point_id: Ê}."""
    points = [p["point_id"] for p in model["sample_points"]]
    total = {p: 0.0 for p in points}
    for bulb, s in scene.items():
        g = dimming(model, bulb, float(s.get("u") or 0))
        if g == 0:
            continue
        b = basis(model, bulb, s.get("k"))
        for p in points:
            total[p] += g * float(b.get(p, 0.0))
    return total
