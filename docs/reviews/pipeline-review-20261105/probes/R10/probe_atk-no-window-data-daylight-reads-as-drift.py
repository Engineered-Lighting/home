"""Probe (attack lens): with no window_daylight in the frames, a daylight change is explained (and flagged) as drift.

What it checks
  score.py explain() tests `daylight` only when the verify frame and the read
  frame share a window id (`if common and ...`); the `drift` test that follows
  does not require that daylight was known to be steady. den-photometry, the
  only LightState writer, writes "window_daylight": []. A cloud clearing
  between the read frame and the verify frame (+60 units of daylight on the
  surface; the bulb change itself lands exactly) is run twice:
    1. frames carry window_daylight (0.2 -> 0.6): cause daylight;
    2. the same frames with window_daylight [] : cause drift, residual flag drift.

Run
  python3 probe_atk-no-window-data-daylight-reads-as-drift.py <den-verify branch checkout root>
Synthetic data only; no network; writes nothing.
"""
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()
LM = LightModel(syn.hue_model({"desk-1": {B0: 400.0}}))


def case(with_windows):
    t = syn.T0
    st = lambda lv: {B0: {"level": lv, "cct_k": 2700}}  # noqa: E731
    pb, pa = LM.predict(st(0.2))["desk-1"], LM.predict(st(0.5))["desk-1"]
    w0 = {"west-1": 0.2} if with_windows else None
    w1 = {"west-1": 0.6} if with_windows else None
    ref = syn.light_state(t, {"desk-1": 40.0 + pb}, anchored=False, windows=w0, ident="ls-ref")
    after = syn.light_state(t + 5, {"desk-1": 100.0 + pa}, anchored=False, windows=w1)
    if not with_windows:
        ref["window_daylight"], after["window_daylight"] = [], []
    dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.5, "steps": [
        {"kind": "send", "sent_at": t + 0.6}]}}, "ls-ref", targets={"A": {"surface_id": "desk-1"}})
    d, s, rep = Run(M, LM, [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
    e = d[0]["verify"]["explanation"]
    flags = next(x for x in s[1]["residual"] if x["surface_id"] == "desk-1")["flags"]
    return (e[0]["cause"] if e else None), flags, d[0]["verify"]["points"][0]["residual"]


a, b = case(True), case(False)
print(f"RESULT with window data:    cause={a[0]} flags={a[1]} residual={a[2]}")
print(f"RESULT without window data: cause={b[0]} flags={b[1]} residual={b[2]}")
print("FAIL: unknown daylight falls through to drift" if a[0] == "daylight" and b[0] == "drift" else "PASS")
