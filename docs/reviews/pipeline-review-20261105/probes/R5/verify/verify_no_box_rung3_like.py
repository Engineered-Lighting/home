"""Verifier probe for finding no-box-ends-replay: the same 60 s one-person scene, but with the
detector stall handled the way rung 3 handles rejected observations (it skips them, so its
lines during 10-15 s list no observation; state stays confirmed for the first misses, then
coasting). Shows whether router.run() still stops early. Then a variant where the confirmed
line names a box-less person observation (the only way an eligible track gets no_box).

Run: python3 verify_no_box_rung3_like.py <root of the rung-5-bundle checkout>
Synthetic data only.
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes, method, records, router, synth  # noqa

def replay(obs, tracks):
    st = records.Store(obs, tracks)
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock, latency_s=1.25)
    r = router.Router(st, method.load_method(), method.load_router(), clock, sender)
    recs = router.run(r, clock, sender, synth.ts(60.0))
    return [(round(x['sent_ts'] - synth.T0, 1), x['outcome']) for x in recs]

# rung-3-like: rejected results never listed; lines continue with [] (confirmed 3 misses, then coasting)
obs, tracks = [], []
miss = 0
for t in synth.steps(0.0, 60.0, 0.5):
    synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
    if 10.0 <= t < 15.0:
        obs[-1]['validity'] = 'rejected:detector_result_expired'
        miss += 1
        tracks[-1]['last_obs_ids'] = []
        tracks[-1]['state'] = 'confirmed' if miss <= 3 else 'coasting'
    else:
        miss = 0
out = replay(obs, tracks)
print('RESULT rung3-like stall: %d records, last at %.1f s, outcomes %s' % (len(out), out[-1][0], out[:8]))
print('PASS replay continues' if out[-1][0] > 50 else 'FAIL replay stopped')
