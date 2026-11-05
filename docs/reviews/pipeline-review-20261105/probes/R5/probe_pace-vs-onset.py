"""Probe: what router v0's pace leaves for per-person onset and coverage, with one and two people.

What it measures (tool's own Router on synth.two_tracks, FakeSender answering in 1.25 s,
R3's live median):
  - per-person period and the share of each person's time covered by some bundle window
    (window_s proposed 3.0);
  - "onset" in the owner's sense (first touch -> first output): for touches every 0.1 s in
    10..50 s, the time to the answer of the first request for that person sent at or after
    touch + L, where L is the evidence latency (0, or R5's 1.46 s capture-to-completion).
    The owner's target is a 3 s median (owner-choices 3); rung 6's gate uses it.
Nothing here is a gate of rung 5 (the ladder sets no latency threshold for bundles); it is
the premise check the unit asks for: bundle rate vs the 4 s pace with one and two people.

How to run
  python3 probe_pace-vs-onset.py <root of the branch checkout>
"""
import os
import statistics
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from router import Router, run  # noqa: E402

M, R = method.load_method(), method.load_router()


def routed(scene):
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock, latency_s=1.25)
    r = Router(st, M, R, clock, sender)
    return run(r, clock, sender, synth.ts(66.0))


def one_track():
    s = synth.two_tracks()
    s['observations'] = [o for o in s['observations'] if o['obs_id'].startswith('o-A-')]
    s['tracks'] = [l for l in s['tracks'] if l['track_id'] == 'A']
    return s


for name, scene in (('one person', one_track()), ('two people', synth.two_tracks())):
    recs = routed(scene)
    a = [x for x in recs if x['track_id'] == 'A' and x['outcome'] == 'ok']
    sends = [x['sent_ts'] - synth.T0 for x in a]
    period = statistics.median([b - c for c, b in zip(sends, sends[1:])])
    cover = M['window_s'] / period
    for L in (0.0, 1.46):
        onsets = []
        t = 10.0
        while t <= 50.0:
            nxt = next((x for x in a if x['sent_ts'] - synth.T0 >= t + L - 1e-9), None)
            if nxt is not None:
                onsets.append(nxt['done_ts'] - synth.T0 - t)
            t = round(t + 0.1, 1)
        med = statistics.median(onsets)
        print('RESULT %s: A served every %.1f s; bundle windows cover %.0f%% of A\'s time; '
              'evidence latency %.2f s -> onset median %.2f s, p90 %.2f s (target median 3 s) %s' % (
                  name, period, 100 * cover, L, med, sorted(onsets)[int(0.9 * len(onsets))],
                  'FAIL' if med > 3.0 else 'PASS'))
