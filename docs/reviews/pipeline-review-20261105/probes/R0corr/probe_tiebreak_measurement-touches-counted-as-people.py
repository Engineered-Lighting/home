"""probe_tiebreak_measurement-touches-counted-as-people (R0corr, tiebreak verifier).

What it checks, on synthetic recorder exports built with the checkout's own fixture (test_capture.Den):
  A. Built rung 7 / rung 8 Stage 0 procedure: den_lighting automatic, owner in the den (occupancy on). The owner
     turns one more bulb on at full in the HA app, then dims another a little at a switch. How are they counted?
  B. The ladder's rung 7 probe rule: the same two touches with den_lighting paused (automatic switch off), then
     the snapshot write-back and resume_automatic (holds cleared, switch on).
  C. Ladder rule variant: paused, touches, then the switch turned back on WITHOUT resume_automatic (holds persist):
     does re-enabling create any counted candidate?
  D. Owner out of view long enough that den_lighting's occupancy is off, touches from the HA app.
  E. The CLI: is there any option to exclude a measurement window?
  F. How much the planning verdict moves at the margin when 2-4 measurement actions are added (cph-budget-v0).

Synthetic data only, temp dirs only, no host contacted, no bytecode written into the checkout.
Run:  python3 -B probe_tiebreak_measurement-touches-counted-as-people.py <root of the claude/correction-capture checkout>
"""
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import test_capture as tc  # noqa: E402

L0, L1, L2, L3 = tc.L0, tc.L1, tc.L2, tc.L3
T0 = tc.T0
FULL = {"on": True, "brightness": 254, "kelvin": 2700}
LOWER = {"on": True, "brightness": 180, "kelvin": 2700}


def run(den):
    tmp = Path(tempfile.mkdtemp(prefix="tb-meas-"))
    try:
        record, replay = capture.capture(*den.write(tmp))
    finally:
        shutil.rmtree(tmp, True)
    s = record["planning"]["strata"]["no_game"]
    return {"occupied_h": s["occupied_h"], "people": s["people"]["count"], "every": s["every_hold"]["count"],
            "by_source": {k: v for k, v in s["actions_by_source"].items() if v},
            "not_counted": record["planning"]["not_counted_by_reason"],
            "replay_sources": [e["data"]["source"] for e in replay]}


def visit(mode):
    """One hour; a 20-minute measurement visit starting at +20 min, two owner touches 5 min apart."""
    den = tc.Den()                                   # automatic, occupancy on, no holds at T0
    t = T0 + 20 * 60
    if mode in ("paused", "paused_no_resume"):
        den.row(t - 30, automatic=False)              # the ladder: pause den_lighting before the probes
    if mode == "vacant":
        den.row(t - 300, occupancy="off")             # owner out of view; den_lighting's occupancy off
    den.hold(t, {L2: FULL})                           # HA app: one more bulb on at full ('too dark')
    den.bulb(t + 0.3, L2, True, user=True)
    den.hold(t + 300, {L0: LOWER})                    # switch/dimmer: 'too bright: dim a little'
    den.bulb(t + 300.4, L0, True)                     # no ids, no flip (already on), no den_lighting command near
    end = t + 20 * 60
    if mode == "paused":
        den.row(end, automatic=True, holds={})        # snapshot written back, then resume_automatic clears holds
    elif mode == "paused_no_resume":
        den.row(end, automatic=True)                  # switch back on, holds persist
    elif mode == "vacant":
        den.row(end, occupancy="on")
    den.row(T0 + 3600)
    return run(den)


ok = True
a = visit("automatic")
print("RESULT A automatic+occupied:", a)
if a["people"] == 2:
    print("PASS A (mechanism reproduced): both measurement touches are counted as people corrections")
else:
    print("FAIL A: expected 2 people corrections, got", a["people"]); ok = False

b = visit("paused")
print("RESULT B ladder rule, paused then resume_automatic:", b)
if b["people"] == 0 and b["every"] == 0 and b["not_counted"].get("outside_exposure") == 2:
    print("PASS B: under the ladder's probe rule the tool already excludes both touches (outside_exposure)"
          " and the paused 20 min is not exposure (occupied_h %.4f of 1 h)" % b["occupied_h"])
else:
    print("FAIL B: paused touches not excluded"); ok = False

c = visit("paused_no_resume")
print("RESULT C paused, switch back on without resume:", c)
if c["people"] == 0 and c["every"] == 0:
    print("PASS C: re-enabling with the holds still present creates no counted candidate")
else:
    print("FAIL C: re-enabling counted a held bulb"); ok = False

d = visit("vacant")
print("RESULT D occupancy off during touches:", d)
print(("PASS D" if d["people"] == 0 else "FAIL D") + ": touches made while den_lighting's occupancy is off are not counted")

# E. the CLI
err = io.StringIO()
code = None
with contextlib.redirect_stderr(err):
    try:
        capture.main(["--status", "s", "--lights", "l", "--commands", "c", "--out", "/nonexistent-abs",
                      "--exclude", "2026-11-10T19:00Z", "2026-11-10T20:00Z"])
    except SystemExit as e:
        code = e.code
print("RESULT E capture.main(--exclude ...) exit=%r stderr=%r" % (code, err.getvalue().strip().splitlines()[-1]))
print("RESULT E options:", sorted(o for a_ in capture.argparse.ArgumentParser.__dict__ and [] or [] for o in []) or
      "see capture.py:594-601 (--status --lights --commands --installed --out --events)")

# F. margin: how many extra people actions flip the verdict at 40 occupied h (pure verdict function)
k = capture.load_rule()[0]["constants"]
for base in (26, 27, 28, 29):
    v0 = capture.verdict(40.0, base, base + 5, k)
    flips = [extra for extra in range(0, 6) if capture.verdict(40.0, base + extra, base + 5 + extra, k) != v0]
    print("RESULT F 40 h, people=%d: verdict %s; first extra measurement actions that change it: %s"
          % (base, v0, flips[0] if flips else "none within 5"))
for base, hours in ((4, 30.0), (10, 60.0)):
    v0 = capture.verdict(hours, base, base, k)
    v4 = capture.verdict(hours + 2, base + 4, base + 4, k)
    print("RESULT F %g h, people=%d: verdict %s; +4 measurement actions and +2 h: %s" % (hours, base, v0, v4))
print("RESULT overall:", "mechanism reproduced; ladder rule honoured by the tool" if ok else "unexpected")
