#!/usr/bin/env python3
"""B2 after-take /health and B4 rising-slope semantics, real check.py vs the two surviving mutants.

    python3 -B probe_R_intent_b2_b4_rules.py <checkout root>     # e.g. .../wt/rung-r

Synthetic frame dicts only (no bundles, no camera, no network). Calls check.check_rules
directly on (a) a single-session take whose only defect is take.json's health_after
(new session / no body / Ctrl+C record) and (b) a take whose d = t_host - t_device rises
at +2000 ppm (steps and line residual within limits), with the real source and with
the mutated source (exec'd in memory from a copy of the text; the checkout is not edited).
Prints PASS/FAIL per expectation and a RESULT line.
"""
import sys
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402

M = check.load_method()
SRC = Path(check.__file__).read_text(encoding="utf-8")


def mutant(old, new):
    assert SRC.count(old) == 1, old
    mod = types.ModuleType("check_mut")
    mod.__file__ = check.__file__
    exec(compile(SRC.replace(old, new), check.__file__, "exec"), mod.__dict__)
    return mod


M_B2 = mutant("and health_sessions == [next(iter(sessions))] * 2,", "and health_sessions[0] == next(iter(sessions)),")
M_B4 = mutant('and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]', 'and -slope * 1e6 <= m["max_two_clock_slope_ppm"]')


def frames(ppm, seconds=65.0, fps=4.0, session="sess-1"):
    out = []
    for i in range(int(seconds * fps)):
        p = i / fps
        t_host = 5000.0 + p
        t_device = t_host - 1200.0 - p * ppm * 1e-6  # d = t_host - t_device rises at +ppm
        out.append({"session": session, "rgb_seq": 1000 + i, "t_host": t_host, "t_device": t_device,
                    "n": i + 1, "recv": p, "y": None, "stale": False})
    return out


def take(after):
    return {"complete": True, "slates": [], "health_before": {"status": 200, "body": {"session": "sess-1"}},
            "health_after": after}


ok = True


def expect(label, got, want):
    global ok
    good = got == want
    ok &= good
    print(("PASS " if good else "FAIL ") + f"{label}: pass={got} (expected {want})")


GOOD_AFTER = {"status": 200, "body": {"session": "sess-1"}}
for name, after in (("clean", GOOD_AFTER),
                    ("after_new_session", {"status": 200, "body": {"session": "sess-2"}}),
                    ("after_500_no_body", {"status": 500, "body": None}),
                    ("after_ctrl_c", {"status": None, "body": None, "error": "interrupted"})):
    fr = frames(20)
    real = check.check_rules(take(after), [{}], fr, [], M)["B2_session"]["pass"]
    mut = M_B2.check_rules(take(after), [{}], fr, [], M)["B2_session"]["pass"]
    expect(f"B2 {name} real", real, name == "clean")
    expect(f"B2 {name} mutant", mut, True)

for ppm in (+2000, -2000, +900):
    fr = frames(ppm)
    r_real = check.check_rules(take(GOOD_AFTER), [{}], fr, [], M)["B4_two_clock"]
    r_mut = M_B4.check_rules(take(GOOD_AFTER), [{}], fr, [], M)["B4_two_clock"]
    print(f"INFO d slope {r_real['slope_ppm']:+.1f} ppm, max_step {r_real['max_step_s']:.6f} s, "
          f"residual {r_real['max_residual_s']:.2e} s")
    expect(f"B4 d rising {ppm:+d} ppm real", r_real["pass"], abs(ppm) <= 1000)
    expect(f"B4 d rising {ppm:+d} ppm mutant", r_mut["pass"], -ppm <= 1000)

print("RESULT", "CONFIRMED: real code fails B2 on after-health alone and B4 on a +2000 ppm rise; "
      "both mutants pass them" if ok else "UNEXPECTED")
