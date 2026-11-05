#!/usr/bin/env python3
"""probe_stage1-model-unreadable: can den-verify use the light-model.json that rung 8 Stage 1 writes?

What it checks (synthetic only, temp dirs, no network). Rung 8 Stage 1
(claude/rung-8-stage1 @028981bc, tools/den-light/model.py light_model() and
predict.py) writes light-model.json with:
  channels[{channel_key "<fixture>.<channel>", entity_id}], basis keyed
  "<channel_key>@<K>K" -> {point_id: value}, cct[key]{kelvin, basis_keys},
  dimming[key]{levels, g, ...}, basis_unit "relative"
and its predict(scene, model) takes {channel_key: {level, cct_k}} and returns
{"surfaces": {point: value, ..., "unit"}, ...}. den-verify's light_model.py /
fake_model.py read basis[bulb][kelvin][point] and dimming[bulb] = [[u, g], ...]
or {"same_as": bulb}.

The probe builds a small model in Stage 1's shape (two of den_lighting's
bulbs, three temperatures), optionally checks it with Stage 1's own predict()
when a second argument gives the rung-8-stage1 checkout, then runs den-verify's
command line on it with every slot closed, once with Decision channels keyed by
Stage 1's channel_key and once by HA entity id.

Run:
    python3 probe_stage1-model-unreadable.py <root of rung-10-verify> [<root of rung-8-stage1>]
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from records import write_jsonl  # noqa: E402

TEMPS = [2202, 3100, 6500]
CH = [("den_front_left.white", "light.den_front_left"), ("front_right.white", "light.front_right")]
POINTS = ["r00c00", "r00c01"]


def stage1_model():
    basis, cct, dimming, channels = {}, {}, {}, []
    for key, entity in CH:
        fixture, channel = key.split(".")
        channels.append({"channel_key": key, "fixture_id": fixture, "channel_id": channel, "entity_id": entity,
                         "kind": "white", "cct_range_k": [2202, 6500]})
        keys = []
        for k in TEMPS:
            bk = f"{key}@{k}K"
            keys.append(bk)
            basis[bk] = {p: 100.0 + 10 * i for i, p in enumerate(POINTS)}
        cct[key] = {"kelvin": TEMPS, "basis_keys": keys, "entity_id": entity}
        dimming[key] = {"levels": [0, 127, 254], "g": [0.0, 0.5, 1.0], "shared_with": None,
                        "command_unit": "Hue brightness 0 to 254", "monotone": "pool-adjacent-violators"}
    return {"record": "light_model", "contract": "live-v1", "version": "lm-den-s1", "room_version": "den-oak-view/x",
            "sample_points": [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": [0, 0, 40, 40],
                               "excluded": None} for p in POINTS],
            "channels": channels, "basis": basis, "basis_unit": "relative", "spot_model": None, "dimming": dimming,
            "cct": cct, "bounce": "measured", "glare": None, "error": {"median_pct": None, "p90_pct": None}}


model = stage1_model()
if len(sys.argv) > 2:
    sys.path.insert(0, str(Path(sys.argv[2]).resolve() / "tools" / "den-light"))
    import predict as stage1  # noqa: E402
    out = stage1.predict({"den_front_left.white": {"level": 0.5, "cct_k": 2700}}, model)
    print(f"RESULT rung 8 Stage 1 predict() on this model: {out['surfaces']} (version {out['version']})")

text = method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))
t = syn.T0
for label, keyof in (("channel_key", lambda i: CH[i][0]), ("entity_id", lambda i: CH[i][1])):
    a, b = keyof(0), keyof(1)
    ref = syn.light_state(t, {"r00c00": 30.0, "r00c01": 33.0}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 5, {"r00c00": 80.0, "r00c01": 88.0}, anchored=False)
    dec = syn.hue_decision("d-1", t + 0.5, {a: 0.3, b: 0.0}, {a: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                           "ls-ref", targets={"A": {"surface_id": "r00c00"}}, model="lm-den-s1")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        write_jsonl(tmp / "d.jsonl", [dec])
        write_jsonl(tmp / "l.jsonl", [ref, after])
        (tmp / "light-model.json").write_text(json.dumps(model), encoding="utf-8")
        (tmp / "VERIFY-V1.md").write_text(text, encoding="utf-8")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = cli.main(["--method", str(tmp / "VERIFY-V1.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                                 "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "light-model.json"),
                                 "--utc-offset-h", "-7", "--out", str(tmp / "out")])
        except Exception as e:  # noqa: BLE001
            print(f"RESULT keyed by {label}: den-verify raised {type(e).__name__}: {e}")
            print("FAIL den-verify cannot run on rung 8 Stage 1's light-model.json")
            continue
        rep = json.loads((tmp / "out" / "report.json").read_text(encoding="utf-8"))
        print(f"RESULT keyed by {label}: exit {code}; {buf.getvalue().strip().splitlines()[0]}")
        print(f"RESULT keyed by {label}: not_verified {rep['decisions']['not_verified']}; "
              f"model backend {rep['model']['backend']!r}")
        if rep["hit_rate"]["hits"] + rep["hit_rate"]["misses"] == 0:
            print(f"FAIL keyed by {label}: nothing can be verified against Stage 1's model")
