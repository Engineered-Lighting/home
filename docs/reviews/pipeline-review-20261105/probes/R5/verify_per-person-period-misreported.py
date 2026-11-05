"""Verify (reproduce lens): router v0's per_person_period_s (router.py `_record`) is the nominal
max(min_interval_s, minute_s / max_per_minute) x len(tracks); does it match the realised gap
between starts for the same track?

What it checks (pinned Router, FakeClock, FakeSender; synthetic two_tracks scene)
  1) one track, answers in 6.0 s (one in flight)      : realised gaps vs the field
  2) two tracks, answers in 6.0 s                     : per-track realised gaps vs the field
  3) two tracks, answers in 1.25 s (R3's median)      : control, should agree
  4) one track, request 2 refused (30 s back-off)     : the gap after it vs the field

How to run
  python3 verify_per-person-period-misreported.py <root of the branch checkout>
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from router import Router, run  # noqa: E402

M, R = method.load_method(), method.load_router()


def scene(two):
    s = synth.two_tracks()
    if not two:
        s['observations'] = [o for o in s['observations'] if o['obs_id'].startswith('o-A-')]
        s['tracks'] = [l for l in s['tracks'] if l['track_id'] == 'A']
    return s


def go(two, latency, script=None, until=66.0):
    s = scene(two)
    st = records.Store(s['observations'], s['tracks'])
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock, latency_s=latency, script=script)
    return run(Router(st, M, R, clock, sender), clock, sender, synth.ts(until))


bad = 0
for name, two, lat, script in (('1 one track, 6.0 s answers', False, 6.0, None),
                               ('2 two tracks, 6.0 s answers', True, 6.0, None),
                               ('3 two tracks, 1.25 s answers (control)', True, 1.25, None),
                               ('4 one track, request 2 refused', False, 1.25, {2: 'refused'})):
    recs = [x for x in go(two, lat, script) if x['outcome'] != 'no_box']
    per = {}
    for x in recs:
        per.setdefault(x['track_id'], []).append(x)
    gaps = {t: sorted({round(b['sent_ts'] - a['sent_ts'], 3) for a, b in zip(v, v[1:])}) for t, v in per.items()}
    fields = sorted({x['per_person_period_s'] for x in recs})
    mism = any(g != f for t, v in per.items() for a, b in zip(v, v[1:])
               for g, f in [(round(b['sent_ts'] - a['sent_ts'], 3), b['per_person_period_s'])])
    bad += mism and not name.startswith('3')
    print('RESULT %s: realised per-track gaps %s; per_person_period_s values %s -> %s' % (
        name, gaps, fields, 'MISMATCH' if mism else 'agree'))
print('FAIL per_person_period_s differs from the realised per-person period' if bad else 'PASS field matches')
