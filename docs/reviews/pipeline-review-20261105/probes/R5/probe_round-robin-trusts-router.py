"""Probe: the gate's round_robin clause checks each request against the eligible set the router
itself wrote (`tracks`), never against the track records; a router that starves a confirmed,
visible person passes. Also a dense association_safe sweep across the annotated crossing.

What it checks
  1) two_tracks fixture routed by the tool (A and B both confirmed and seen for 70 s); the log
     is rewritten as a starving router would write it: every request to A, `tracks` = ['A'],
     4 s apart. gate.check_requests with the store's order must flag B's starvation.
  2) dense sweep: assemble A and B every 0.1 s for t1 in 2..13 s on the crossing fixture and
     count bundles whose interval meets the annotated crossing but have association_safe true.

How to run
  python3 probe_round-robin-trusts-router.py <root of the branch checkout>
"""
import copy
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes    # noqa: E402
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from assemble import assemble  # noqa: E402
from router import Router, run  # noqa: E402

M, R = method.load_method(), method.load_router()
sc = synth.two_tracks()
st = records.Store(sc['observations'], sc['tracks'])
clock = fakes.FakeClock(synth.ts(2.0))
sender = fakes.FakeSender(clock)
r = Router(st, M, R, clock, sender)
recs = run(r, clock, sender, synth.ts(66.0))
starved = copy.deepcopy(recs)
for x in starved:
    x['track_id'], x['tracks'], x['per_person_period_s'] = 'A', ['A'], 4
fails = gate.check_requests(starved, R, st.order)
eligible_b = sum(1 for x in recs if 'B' in r.eligible(x['sent_ts']))
print('RESULT starving log: %d requests all to A; B eligible by the store at %d of those send times; '
      'gate router failures: %s' % (len(starved), eligible_b, {k: len(v) for k, v in fails.items() if v} or 'none'))
print('FAIL round_robin passes a log that never serves B' if not any(fails.values()) else 'PASS')

cr = synth.crossing()
st2 = records.Store(cr['observations'], cr['tracks'])
c0, c1 = cr['crossings'][0]['interval']
met = unsafe = bad = 0
for i in range(0, 111):
    t1 = synth.ts(2.0 + 0.1 * i)
    for tid in ('A', 'B'):
        b = assemble(st2, M, tid, t1)
        t0 = b['interval'][0]
        if t0 <= c1 and c0 <= t1:
            met += 1
            if b['association_safe'] is False:
                unsafe += 1
            else:
                bad += 1
print('RESULT dense sweep: %d bundles meet the crossing, %d association_safe false, %d true' % (met, unsafe, bad))
print('PASS dense crossing sweep' if bad == 0 else 'FAIL dense crossing sweep')
