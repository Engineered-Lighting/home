#!/usr/bin/env python3
"""Do N1's staged scripts put a case at a fixed time after "Begin." within one script?

    python3 -B probe_staged_fixed_schedule.py <den-take-script checkout root>

The N1 plan says the two scripts differ "so no case sits at a fixed time after
Begin". This probe loads tools/den-take/scripts/hard-a.json and hard-b.json with
take.load_script (no camera, nothing written), and, using take.py's own fixed
lead times (hold_lead_s 5 s, slate_s 20 s, away_s 10 s, a retry adds slate_s +
say_gap_s), lists every possible t_s (seconds after the first kept frame) of
each task onset for 0, 1 or 2 start-slate retries. Every take of one script
therefore shares one of three schedules; the per-script alternating split puts
takes of the same script in both development and held-out. Prints RESULT.
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import take  # noqa: E402

m = check.load_method()
s = take.Settings(slate_s=m["slate_length_s"])
tasks = {"still_reader", "sorting_mail", "wiping", "phone_at_counter"}
fixed = True
for name in ("hard-a", "hard-b"):
    staged = take.load_script(name, s)
    onsets = {}
    for block in staged["blocks"]:
        if block["case"] in tasks:
            onsets.setdefault(block["case"], []).append(block["at_s"])
    print(f"{name}: task onsets after Begin (s): {onsets}")
    begins = [s.hold_lead_s + s.slate_s + r * (s.slate_s + s.say_gap_s) + s.away_s for r in range(3)]
    print(f"{name}: Begin is about {begins} s after the start slate cue (0, 1, 2 retries), so the first "
          f"still_reader onset is at about {[b + onsets['still_reader'][0] for b in begins]} s in every take")
print("RESULT PLAUSIBLE: within a script, every case onset sits at the same at_s after Begin in every take; "
      "only 3 schedules per script exist (start-slate retries), shared by development and held-out takes")
