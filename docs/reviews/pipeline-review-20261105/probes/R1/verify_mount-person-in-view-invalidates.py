#!/usr/bin/env python3
"""Verify finding mount-person-in-view-invalidates (rung 1, den-register check-mount).

REGISTER-V1.md ('A moved camera') says `unknown` is "too few comparable cells,
for example a person in front of the camera", and README says MOUNT UNKNOWN
means "too much of the view blocked". This probe registers on the synthetic
scene (fx = 450 px, as the tests use), then runs check-mount on later takes of
the SAME unmoved camera in which an occluder stands in front of it:
  - one person-sized slab (0.5 m wide, full frame height) centred, at 1.2,
    0.8, 0.6 and 0.5 m, with valid depth at that distance;
  - two people side by side at 1.2 m, three at 1.2 m;
  - a blocker too close for stereo (depth 0) over the middle 80% of the frame.
Each case gets a fresh registration, because CHANGED invalidates. It prints
the status and whether the transform_id was invalidated. It also checks that
check-mount's default frame is the middle of the take's activity part.

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B verify_mount-person-in-view-invalidates.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import struct
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import scene  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402

W, H, FX = 640, 400, 450.0
text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
M = method.parse(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$",
                        lambda g: f"{g[1]} = {g[2]}  # closed for a probe", text, flags=re.M))
DEPTH, LEFT, SEEN = synthetic.render()
BASE = list(struct.unpack(f"<{W * H}H", DEPTH))


def occluded(spans, value_mm):
    """The registration frame with columns [x0, x1) of every span set to value_mm over the full height."""
    values = list(BASE)
    for x0, x1 in spans:
        for y in range(H):
            for x in range(max(0, x0), min(W, x1)):
                values[y * W + x] = value_mm
    return struct.pack(f"<{W * H}H", *values)


def people(n, d):
    width = FX * 0.5 / d
    total = n * width
    x0 = W / 2 - total / 2
    return [(int(round(x0 + i * width)), int(round(x0 + (i + 1) * width))) for i in range(n)], width


def case(label, depth_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        take = synthetic.write_take(tmp / "reg", [synthetic.bundle(synthetic.metadata(), DEPTH, LEFT)] * 3)
        work = tmp / "work"
        work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
        sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
        session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, M)
        for pid, (u, v, _) in SEEN.items():
            session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        session.done()
        code, fit = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        assert fit["result"] == "registered", fit["reasons"]
        later = synthetic.write_take(tmp / "later", [synthetic.bundle(synthetic.metadata(), depth_bytes, LEFT)] * 3)
        meta = json.loads((later / "take.json").read_text(encoding="utf-8"))
        meta["ended_unix"] = 1_800_001_000.0
        (later / "take.json").write_text(json.dumps(meta), encoding="utf-8")
        out = io.StringIO()
        code, mount = register.run_check_mount(work, later, m=M, now=lambda: 1_800_002_000.0, out=out)
        found = records.resolve(records.read_jsonl(work / "room-model.jsonl", "x"), fit["transform_id"])
        chk = mount.get("check") or {}
        print(f"RESULT {label:<52} -> {mount['result']:<9} ({chk.get('changed_cells')}/{chk.get('compared_cells')}"
              f" cells moved), exit {code}, invalidated {bool(found['invalidated'])}")
        return mount["result"]


results = {}
for d in (1.2, 0.8, 0.6, 0.5):
    spans, width = people(1, d)
    results[f"1@{d}"] = case(f"one person at {d} m ({width:.0f} px wide, {width / W:.0%} of width)",
                             occluded(spans, int(d * 1000)))
for n in (2, 3):
    spans, width = people(n, 1.2)
    results[f"{n}@1.2"] = case(f"{n} people side by side at 1.2 m ({n * width / W:.0%} of width)",
                               occluded(spans, 1200))
results["lens"] = case("blocker too close for stereo (depth 0), middle 80%", occluded([(64, 576)], 0))

# default frame of check-mount: frame_number(take, None) without a work folder -> scene.default_frame
with tempfile.TemporaryDirectory() as tmp:
    take = synthetic.write_take(Path(tmp) / "t", [synthetic.bundle(synthetic.metadata(), DEPTH, LEFT)] * 9)
    t, frames = scene.read_take(take)
    print(f"RESULT check-mount default frame of a 9-frame take: {register.frame_number(take, None)} "
          f"(middle of begin..hold_phone_end cues: {scene.default_frame(t, frames)})")

valid_depth_changed = [k for k, v in results.items() if k != "lens" and v == "changed"]
print(f"RESULT valid-depth occluders that gave CHANGED (registration invalidated): {valid_depth_changed}")
print(f"RESULT zero-depth blocker gave: {results['lens']}")
reproduced = bool(valid_depth_changed)
print(("PASS(defect reproduced)" if reproduced else "FAIL(not reproduced)")
      + ": an occluder with valid depth in front of an unmoved camera can give MOUNT CHANGED, not UNKNOWN")
