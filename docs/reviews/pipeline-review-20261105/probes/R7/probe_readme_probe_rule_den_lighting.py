#!/usr/bin/env python3
"""probe_readme_probe_rule_den_lighting: rung 7's README (step 5) leaves den_lighting running and has
the owner set the den lights by hand for each illuminant class ("front left and front right at full",
"daylight only, no bulbs"). The ladder's rung 7 'tested' rule requires den_lighting paused, every probe
logged as non-human and excluded from corrections, and the saved normal scene written back from a
pre-probe snapshot before den_lighting.resume_automatic. This probe:

  1. checks the branch's tools/den-photometry README and METHOD for any pause / snapshot / write-back /
     resume_automatic / occupancy-abort step;
  2. replays the README's session through den_lighting's own Controller (policy.py, copied to a temp
     dir and imported from there, so nothing is written next to the source) to show that the owner's
     photometry light settings replace den_lighting's saved normal scene and come back on the next
     arrival.

    python3 -B probe_readme_probe_rule_den_lighting.py <branch-root> <den_lighting-dir>

<den_lighting-dir> is ha-config/custom_components/den_lighting from a checkout that has it (the incumbent
is not on the rung 7 branch). Read only. No Home Assistant, no network.
"""
import importlib.util
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
DL = Path(sys.argv[2]).resolve()

failures = 0
readme = (ROOT / "tools/den-photometry/README.md").read_text(encoding="utf-8")
method = (ROOT / "tools/den-photometry/METHOD-V1.md").read_text(encoding="utf-8")
both = readme + "\n" + method
for label, pattern in [("pause den_lighting", r"pause"), ("pre-probe snapshot", r"snapshot"),
                       ("resume_automatic", r"resume_automatic"), ("occupancy gate abort", r"occupancy"),
                       ("logged as non-human / excluded from corrections", r"non-human|correction")]:
    hits = [m.group(0) for m in re.finditer(pattern, both, re.I)]
    print(f"RESULT README+METHOD mention of {label}: {len(hits)} ({'none' if not hits else hits[:3]})")
running = re.search(r"den_lighting keeps running as usual", readme)
print(f"RESULT README says den_lighting keeps running: {bool(running)}")
never_pauses = re.search(r"never pauses\s+or resumes den_lighting", method)
print(f"RESULT METHOD says the tool never pauses or resumes den_lighting: {bool(never_pauses)}")
example = re.search(r'--light "front left and front right at full"', readme)
print(f"RESULT README's capture line sets a specific bulb scene: {bool(example)}")

with tempfile.TemporaryDirectory() as tmp:
    shutil.copy(DL / "policy.py", Path(tmp) / "den_policy_copy.py")
    spec = importlib.util.spec_from_file_location("den_policy_copy", Path(tmp) / "den_policy_copy.py")
    policy = importlib.util.module_from_spec(spec)
    sys.modules["den_policy_copy"] = policy
    spec.loader.exec_module(policy)
    Scene, VAC = policy.Scene, policy.VACANCY_SECONDS
    print(f"RESULT incumbent VACANCY_SECONDS={VAC}")
    c = policy.Controller()
    c.set_enabled(True, 0.0)
    evening = {e: Scene(True, 120, 2700) for e in policy.LIGHTS}
    for e, s in evening.items():
        c.observe(e, s, 0.0)
    c.set_occupancy("on", 1.0)
    before = dict(c.normal)
    # README step 5/6: the owner sets the light to measure, by hand (an external change den_lighting did not send;
    # __init__.py routes such a change to policy.manual(e, scene, now) with update_normal left True).
    t = 10.0
    photometry_scene = {"light.den_front_left": Scene(True, 254, 4000), "light.front_right": Scene(True, 254, 4000),
                        "light.den_rear_left": Scene(False), "light.den_rear_right": Scene(False)}
    for e, s in photometry_scene.items():
        c.manual(e, s, t)
    # The capture: out of view about 40 s, then back to read the meter, then done and out of the den.
    c.set_occupancy("off", 20.0)
    c.set_occupancy("on", 60.0)
    c.set_occupancy("off", 90.0)
    c.reconcile(90.0 + VAC + 1)          # vacancy: lights off
    # Next evening: someone arrives; den_lighting restores its normal scene.
    arrive = 90.0 + VAC + 3600
    c.set_occupancy("on", arrive)
    restored = {e: c.desired(e, arrive) for e in policy.LIGHTS}
    for e in policy.LIGHTS:
        print(f"RESULT {e}: normal before session={before[e]} ; restored on next arrival={restored[e]}")
    lost = any(not before[e].matches(restored[e]) for e in policy.LIGHTS)
    if lost and running and not re.search(r"snapshot|resume_automatic", both, re.I):
        failures += 1
        print("FAIL the README's light step replaces den_lighting's saved normal scene with the photometry "
              "scene, and the README has no snapshot/write-back/resume step (ladder rung 7 probe rule)")
    else:
        print("PASS den_lighting's normal scene survives the documented photometry session")

print("RESULT", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
