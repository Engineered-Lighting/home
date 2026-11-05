#!/usr/bin/env python3
"""verify_stage0-bulb-change-counts-as-correction

Checks whether the Stage 0 owner step "turn a bulb on (at full) in the HA app"
(rung-8-stage0 tools/den-light/README.md step 5 and sweep.py's "too dark"
refusal text) ends up as a person `app` Correction in correction-capture's
output, and as den_lighting's normal scene, with nothing in Stage 0 that a
consumer could use to exclude it.

Parts:
  A. text evidence in the Stage 0 checkout (README step 5, "Nothing is paused",
     sweep.py too-dark text, no mention of corrections, no window record that
     correction-capture reads).
  B. den_lighting policy.py (HA-independent Controller): manual() of the HA-app
     change sets a hold AND the normal scene; the next arrival lights it at full.
  C. correction-capture capture(): a synthetic Stage 0 evening (den_lighting
     automatic, occupied; owner turns a bulb on at full in the HA app, a second
     bulb on after a too-dark refusal, then sets both back by hand) versus the
     same evening without those owner steps. Also the variant where the owner is
     out of the den (occupancy off) when using the app: not counted in CPH but
     still written to the --events replay as source `app`.
  D. correction-capture verdict(): how 2-4 such extra person actions move the
     cph-budget-v0 planning verdict at the margin (planning only, not a gate).

Synthetic data only; temp dirs only; no network; no bytecode written into the
checkouts (sys.dont_write_bytecode).

Run:
  python3 -B verify_stage0-bulb-change-counts-as-correction.py \
      <rung-8-stage0 checkout root> <correction-capture checkout root>
"""
import sys
sys.dont_write_bytecode = True

import importlib.util
import re
import shutil
import tempfile
from pathlib import Path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def say(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    return ok


def part_a(stage0):
    readme = (stage0 / "tools/den-light/README.md").read_text("utf-8")
    sweep = (stage0 / "tools/den-light/sweep.py").read_text("utf-8")
    method = (stage0 / "tools/den-light/METHOD-V1.md").read_text("utf-8")
    print("== A. Stage 0 text evidence")
    say("turn one\non in the HA app; den_lighting keeps that as its normal scene" in readme,
        "README step 5 tells the owner to turn a bulb on in the HA app; it names only the normal-scene side effect")
    say("Nothing\nis paused and no light is written" in readme,
        "README approval 1: den_lighting is not paused during Stage 0 (so the HA-app change is an in-charge hold)")
    say("at full in the HA app" in readme and "at full in the HA app" in sweep,
        "README and sweep.py too-dark refusal both tell the owner to turn one more bulb on at full in the HA app")
    say(not re.search(r"correction", readme + method, re.I),
        "README and METHOD-V1 never mention corrections (owner is not told the change is logged as one)")
    say("session.json" not in sweep and "probe_session" not in sweep,
        "sweep.py writes no session.json / probe_session window (its only wall times are started_pc_unix and "
        "written_pc_unix inside sweep-<n>/response.json, which start after the step-5 light-up)")


def part_b(cc):
    print("== B. den_lighting policy.py manual(): hold + normal; next arrival")
    policy = load_module("den_lighting_policy_probe", cc / "ha-config/custom_components/den_lighting/policy.py")
    Scene, Controller, LIGHTS = policy.Scene, policy.Controller, policy.LIGHTS
    c = Controller()
    c.set_enabled(True, 0)
    for e in LIGHTS:
        c.observe(e, Scene(True, 60, 2700), 0)     # arrival scene: all four on, dim
    c.set_occupancy("on", 0)
    full = Scene(True, 255, 2700)
    c.manual(LIGHTS[0], full, 10)                   # owner: one bulb to full in the HA app (README step 5 / too dark)
    say(c.holds[LIGHTS[0]].scene == full and c.normal[LIGHTS[0]] == full,
        "manual() stores the instrument change as a hold and as normal: holds=%s normal=%s"
        % (c.holds[LIGHTS[0]].scene, c.normal[LIGHTS[0]]))
    c.set_occupancy("off", 200)
    c.reconcile(200 + 130)                          # hold expires 120 s after vacancy
    c.set_occupancy("on", 2000)                     # next evening's arrival
    d = c.desired(LIGHTS[0], 2001)
    say(d == full, "next arrival after the hold expired lights that bulb at %s" % (d,))


def part_c(cc):
    print("== C. correction-capture on a synthetic Stage 0 evening")
    tools = cc / "tools/correction-capture"
    sys.path.insert(0, str(tools))
    import capture  # noqa: E402
    import test_capture as tc  # noqa: E402  the repo's own synthetic-export builder (Den)
    L0, L1 = tc.L0, tc.L1
    FULL = {"on": True, "brightness": 255, "kelvin": 2700}
    DIMMED = {"on": True, "brightness": 60, "kelvin": 2700}
    results = {}
    for variant in ("control", "stage0_occupied", "stage0_owner_outside"):
        tmp = Path(tempfile.mkdtemp(prefix="r8s0-verify-"))
        try:
            occ = "off" if variant == "stage0_owner_outside" else "on"
            den = tc.Den(occupancy=occ)
            t0 = tc.T0
            # den_lighting's arrival scene: L0 and L1 on, dim (own commands).
            den.command(t0 + 5, L0, DIMMED)
            den.command(t0 + 6, L1, DIMMED)
            if variant != "control":
                # README step 5: owner raises L0 to full in the HA app (hold opens, bulb row carries the user).
                den.hold(t0 + 60, {L0: FULL})
                den.bulb(t0 + 60.3, L0, True, user=True)
                # sweep.py "too dark": owner turns one more bulb (L1) on at full in the HA app, then reruns.
                den.hold(t0 + 140, {L1: FULL})
                den.bulb(t0 + 140.3, L1, True, user=True)
                # After the sweep: "set it back by hand afterwards if you want" (L0, then L1, 20 s apart).
                den.hold(t0 + 600, {L0: DIMMED})
                den.bulb(t0 + 600.3, L0, True, user=True)
                den.hold(t0 + 620, {L1: DIMMED})
                den.bulb(t0 + 620.3, L1, True, user=True)
            den.row(t0 + 3600)                       # end of the export: one hour of exposure
            record, replay = capture.capture(*den.write(tmp))
            corr = record["corrections"]
            app_events = [ev for ev in replay if ev["data"]["source"] == "app"]
            results[variant] = (corr, record, app_events)
            print("RESULT %-22s corrections=%d sources=%s people=%s corrections_total=%s "
                  "not_counted=%s replay_app_events=%d"
                  % (variant, len(corr), [c["source"] for c in corr], [c["people"] for c in corr],
                     record["exposure"]["corrections_total"], record["planning"]["not_counted_by_reason"],
                     len(app_events)))
            ns = record["planning"]["strata"]["no_game"]
            print("RESULT %-22s no_game people=%s hours_per_arm=%s verdict=%s"
                  % (variant, ns["people"].get("count", ns["people"]), ns["people"].get("hours_per_arm"),
                     ns["verdict"]))
        finally:
            shutil.rmtree(tmp, True)
    ctl = results["control"][0]
    occ_corr, _, occ_app = results["stage0_occupied"]
    out_corr, out_rec, out_app = results["stage0_owner_outside"]
    say(len(ctl) == 0, "control evening (no owner HA-app steps): 0 corrections")
    say(len(occ_corr) == 4 and all(c["source"] == "app" and c["people"] for c in occ_corr),
        "Stage 0 evening, owner in the den: the two instructed light-ups and the two set-backs are 4 person "
        "`app` corrections in exposure.json (counted in the CPH numerator), not only in the --events replay")
    say(len(occ_app) == 4, "Stage 0 evening, owner in the den: --events replay writes 4 correction events, source `app`")
    say(len(out_corr) == 0 and len(out_app) == 4,
        "owner outside the den: 0 counted (outside_exposure) but --events replay still writes 4 `app` corrections")
    print("RESULT capture.py CLI arguments: --status --lights --commands --installed --out --events "
          "(no exclusion window input exists at this commit)")
    # D. What the extra person corrections can do to cph-budget-v0's planning verdict (at the margin).
    rule, _ = capture.load_rule()
    k = rule["constants"]
    for genuine, extra in ((13, 0), (13, 4), (15, 0), (15, 2)):
        n = genuine + extra
        print("RESULT planning verdict at 20 occupied h: %d genuine + %d Stage 0 = %d people (every_hold = %d) -> %s"
              % (genuine, extra, n, n, capture.verdict(20.0, n, n, k)))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    stage0, cc = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    part_a(stage0)
    part_b(cc)
    part_c(cc)


if __name__ == "__main__":
    main()
