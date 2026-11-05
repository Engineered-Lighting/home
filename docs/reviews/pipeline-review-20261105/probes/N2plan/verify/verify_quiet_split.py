"""verify_quiet_split: split the phone-none 'scored quiet/no-need' count into quiet (idle_*) units
and phone no-need units, using the same N1 script encoding as n2_scripts.py.

Run: python3 -B verify_quiet_split.py <root of claude/plan-n2>   (root unused except for the plan text)
Synthetic only.
"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from n2_scripts import intervals_and_spans, scored_from

for phone in ("casual", "none"):
    tot_q = tot_p = 0
    for script, n in (("hard-a", 2), ("hard-b", 2)):
        ivs, spans = intervals_and_spans(script, phone)
        q = p = 0
        for sp in spans:
            if math.ceil(scored_from(sp["start"], ivs)) + 1 <= sp["end"]:
                if sp["case"].startswith("idle"):
                    q += 1
                else:
                    p += 1
        print(f"RESULT phone {phone} {script}: scored quiet (idle_*) units {q}, scored phone no-need units {p}")
        tot_q += n * q
        tot_p += n * p
    print(f"RESULT phone {phone} held-out (2 hard-a + 2 hard-b): quiet {tot_q}, phone {tot_p}, none-band total {tot_q + tot_p}")
