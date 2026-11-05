"""Probe (attack lens): recompute rung 10's statistics by hand and compare with den-verify.

What it checks
  - tolerance boundary: exactly 15% (115 vs 100, 85 vs 100) within; 15.0001% outside;
    the residual is relative to the PREDICTION (85.5 vs 100 is -14.5% of the
    prediction, within; it would be 16.96% of the measurement);
  - V11: (357 - 368) / 368 = -2.989%; the tool writes residual_pct -2.99;
    the ladder prints -2.9 (a truncation; one-decimal rounding gives -3.0);
  - a zero-lux prediction (background zero, all bulbs off): unverifiable, not a hit;
  - the all-excluded case and the no-decision case: verdict "insufficient", not pass
    (and the command line exits 0 either way);
  - the gate compared exactly: 17/20 pass, 33/39 fail, 849/1000 fail;
  - the session bootstrap for two sessions (17/20, 10/20): the exact bootstrap
    distribution has mass 1/4 at 0.5 and 0.85, so the 95% interval is [0.5, 0.85].

Run
  python3 probe_atk-stats-boundary.py <den-verify branch checkout root>
Synthetic data only; no network; temp dirs only.
"""
import contextlib
import io
import json
import sys
import tempfile

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run, within  # noqa: E402

TEXT = method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")
M = method.parse(method.closed_copy(TEXT)).scored()
B0, B1 = syn.HUE[:2]
ok = True


def check(label, tool, hand):
    global ok
    match = tool == hand
    ok &= match
    print(f"RESULT {label}: tool={tool} hand={hand} {'match' if match else 'MISMATCH'}")


check("within(115,100) exactly 15%", within(115, 100, 0.15), True)
check("within(85,100) exactly -15%", within(85, 100, 0.15), True)
check("within(115.0001,100) 15.0001%", within(115.0001, 100, 0.15), False)
check("within(85.5,100) relative to prediction", within(85.5, 100, 0.15), abs(85.5 - 100) / 100 <= 0.15)

d, f, c = syn.v11_evening()
_, states, _ = Run(M, LightModel(syn.v11_model()), d, f, corrections=c, clock=Clock(syn.UTC_OFFSET_S)).run()
r = next(e for e in next(s for s in states if s["ts"] == syn.T0 + 40.0)["residual"] if e["surface_id"] == "counter-1")
hand = (357 - 368) / 368 * 100
check("V11 residual_pct (2 dp)", r["residual_pct"], round(hand, 2))
print(f"RESULT V11 hand={hand:.4f}%  1-dp rounding={round(hand, 1)}  ladder text=-2.9 (truncated)")

# zero-lux prediction: bulbs off before and after, background zero
mz = method.parse(method.closed_copy(TEXT, {"background": "zero"})).scored()
lm = LightModel(syn.hue_model({"desk-1": {B0: 400.0}}))
t = syn.T0
ref = syn.light_state(t, {"desk-1": 0.0}, anchored=False, ident="ls-ref")
aft = syn.light_state(t + 5, {"desk-1": 0.0}, anchored=False)
dec = syn.hue_decision("d0", t + 0.5, {B0: 0.0}, {B1: {"level": 0.0, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                       "ls-ref", targets={"A": {"surface_id": "desk-1"}})
dd, _, rep = Run(mz, lm, [dec], [ref, aft], clock=Clock(syn.UTC_OFFSET_S)).run()
check("zero prediction status", dd[0]["verify"]["points"][0]["status"], "unverifiable")
check("zero prediction verdict", rep["hit_rate"]["verdict"], "insufficient")


def cli_run(decisions, frames):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "d.jsonl").write_text("".join(json.dumps(x) + "\n" for x in decisions), encoding="utf-8")
        (tmp / "l.jsonl").write_text("".join(json.dumps(x) + "\n" for x in frames), encoding="utf-8")
        (tmp / "m.json").write_text(json.dumps(syn.hue_model({"desk-1": {B0: 400.0, B1: 400.0}})), encoding="utf-8")
        (tmp / "V.md").write_text(method.closed_copy(TEXT), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--method", str(tmp / "V.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                             "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"),
                             "--utc-offset-h", "-7", "--out", str(tmp / "o")])
        rep = json.loads((tmp / "o" / "report.json").read_text("utf-8"))
        return code, rep["hit_rate"]["verdict"], buf.getvalue().strip().splitlines()[0]


# all excluded: every point misses where a held bulb gives half the light
lmh = LightModel(syn.hue_model({"desk-1": {B0: 400.0, B1: 400.0}}))
pb = lmh.predict({B0: {"level": 0.2, "cct_k": 2700}, B1: {"level": 0.5, "cct_k": 2700}})["desk-1"]
pa = lmh.predict({B0: {"level": 0.8, "cct_k": 2700}, B1: {"level": 0.5, "cct_k": 2700}})["desk-1"]
ref = syn.light_state(t, {"desk-1": pb}, anchored=False, ident="ls-ref")
aft = syn.light_state(t + 5, {"desk-1": pa * 0.5}, anchored=False)
dec = syn.hue_decision("dh", t + 0.5, {B0: 0.2, B1: 0.5}, {B0: {"level": 0.8, "steps": [
    {"kind": "send", "sent_at": t + 0.6}]}, B1: {"status": "held", "level": 0.5}}, "ls-ref",
    targets={"A": {"surface_id": "desk-1"}})
code, verdict, line = cli_run([dec], [ref, aft])
check("all-excluded verdict", verdict, "insufficient")
print(f"RESULT all-excluded exit code {code}: {line}")
code, verdict, line = cli_run([], [ref, aft])
check("no decisions verdict", verdict, "insufficient")
print(f"RESULT no-decision exit code {code}: {line}")

hr = lambda days: Run.hit_rate(type("R", (), {"m": M})(), days)  # noqa: E731
check("17/20", hr({1: (17, 20)})["verdict"], "pass")
check("33/39", hr({1: (33, 39)})["verdict"], "fail")
check("849/1000", hr({1: (849, 1000)})["verdict"], "fail")
check("bootstrap two sessions (17/20, 10/20)", hr({1: (17, 20), 2: (10, 20)})["interval"], [0.5, 0.85])

print("PASS: every recomputed statistic matches" if ok else "FAIL: a recomputed statistic differs")
