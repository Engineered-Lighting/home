#!/usr/bin/env python3
"""(R7 reproduce lens) Is the M1 mutant (refuse only EMPTY open slots, card.py:144) non-equivalent?

Builds METHOD-V1 with its three empty owner slots filled but every other `open` mark kept (Method.replace keeps
marks), and prints card.ready_problems for the checkout given. The real code must refuse (non-empty list);
the M1 copy returns [] (would allow --new-day/--capture/--score while proposals are still unconfirmed).
Run:
    python3 verify_mutant-open-proposals-survives.py <root of a checkout or mutated copy>
"""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(ROOT / "tools" / "den-photometry"), str(ROOT / "tools" / "den-light")]
import card  # noqa: E402
m = card.load_method().replace(k_L_scope="single", meter_tolerance_lx=10, meter_class="class-b")
print("still open:", ", ".join(m.open))
p = card.ready_problems(m)
print("ready_problems:", p)
print("RESULT:", "REFUSES (correct)" if p else "ALLOWS a scored run with open proposals")
