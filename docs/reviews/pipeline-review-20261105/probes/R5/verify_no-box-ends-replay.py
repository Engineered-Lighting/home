"""Verify: router.run() stops the replay at a tick where every eligible track is no_box.

What it checks
  1) two_tracks fixture (A and B, 1 Hz, 70 s) with BOTH tracks' image_box set to null for
     observations captured in [20, 30) s: does the replay continue after the no_box tick?
     Also prints next_wake(now) right after that tick (equal to now means run() drops it).
  2) control, the way the pinned rung 3 reader behaves (it skips non-valid observations, so a
     detector stall leaves the track with no associated observation): the same stretch with the
     observations removed and the track lines listing nothing. Does the replay continue?

How to run
  python3 verify_no-box-ends-replay.py <root of the branch checkout>
Synthetic data only; no network.
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
import synth    # noqa: E402

M, RULE = method.load_method(), method.load_router()


def replay(scene, start=2.0, until=70.0):
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(synth.ts(start))
    sender = fakes.FakeSender(clock, latency_s=1.25)
    r = router.Router(st, M, RULE, clock, sender)
    recs = router.run(r, clock, sender, synth.ts(until))
    return r, recs, clock


def stretch(o):
    t = o['capture_ts'] - synth.T0
    return 20.0 - 1e-6 <= t < 30.0 - 1e-6


s1 = synth.two_tracks()
for o in s1['observations']:
    if stretch(o):
        o['image_box'] = None
r, recs, clock = replay(s1)
last = max(x['sent_ts'] for x in recs) - synth.T0
print('RESULT 1) both tracks boxless 20-30 s: %d records, last at %.1f s, tail %s; clock stopped at %.1f s; next_wake(now)-now = %r' % (
    len(recs), last, [(round(x['sent_ts'] - synth.T0, 1), x['track_id'], x['outcome']) for x in recs[-3:]],
    clock() - synth.T0, None if r.next_wake(clock()) is None else round(r.next_wake(clock()) - clock(), 6)))
print('FAIL 1) replay ended at the no_box tick' if last < 60 else 'PASS 1)')

s2 = synth.two_tracks()
drop = {o['obs_id'] for o in s2['observations'] if stretch(o)}
s2['observations'] = [o for o in s2['observations'] if o['obs_id'] not in drop]
for line in s2['tracks']:
    line['last_obs_ids'] = [x for x in line['last_obs_ids'] if x not in drop]
r, recs, clock = replay(s2)
last = max(x['sent_ts'] for x in recs) - synth.T0
print('RESULT 2) control, stretch observations dropped (as rung 3 skips non-valid ones): %d records, last at %.1f s, outcomes %s' % (
    len(recs), last, sorted({str(x['outcome']) for x in recs})))
