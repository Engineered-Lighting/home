#!/usr/bin/env python3
"""Verify (intent/spec lens) finding period-counts-no-box-tracks.

What it checks
  router_request/v0's per_person_period_s against each served person's measured gap between
  their own starts, in three replays on the tool's own synthetic fixtures (fake clock, fake
  sender, nothing leaves the process):
    1) two_tracks with B's image boxes removed (B eligible but no_box every turn): the
       finding's case.
    2) two_tracks, every request answered after 6.5 s (one in flight holds the next start).
    3) two_tracks, request 3 refused (30 s back-off).
  It also prints the eligible `tracks` list stamped on each record, to compare with the
  documented formula in ROUTER-V0.md ("min_interval_s times the number of eligible tracks").
  Prints RESULT lines. Synthetic data only; no files written; the checkout is not modified.

How to run
  python3 verify_intent_period-counts-no-box-tracks.py <root of the claude/rung-5-bundle checkout>
"""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.abspath(sys.argv[1]), 'tools', 'den-bundle'))

import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
import synth    # noqa: E402


def replay(scene, until, latency=1.25, script=None):
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock, latency_s=latency, script=script)
    r = router.Router(st, method.load_method(), method.load_router(), clock, sender)
    return router.run(r, clock, sender, synth.ts(until))


def report(label, recs):
    sent = [x for x in recs if x['outcome'] != 'no_box']
    by_track = {}
    for x in sent:
        by_track.setdefault(x['track_id'], []).append(x['sent_ts'])
    gaps = {t: sorted({round(b - a, 3) for a, b in zip(s, s[1:])}) for t, s in by_track.items()}
    stated = sorted({x['per_person_period_s'] for x in sent})
    tracks = sorted({tuple(x['tracks']) for x in recs})
    no_box = sum(1 for x in recs if x['outcome'] == 'no_box')
    ok = all(g == stated for g in gaps.values())
    print('RESULT %s %s: measured per-person gaps %s; stated per_person_period_s %s; eligible tracks %s; no_box records %d'
          % ('MATCH' if ok else 'DIFFERS', label, gaps, stated, tracks, no_box))


def main():
    s = synth.two_tracks()
    for o in s['observations']:
        if o['obs_id'].startswith('o-B-'):
            o['image_box'] = None
    report('no_box B', replay(s, 30.0))
    report('slow answers 6.5 s', replay(synth.two_tracks(), 40.0, latency=6.5))
    report('request 3 refused', replay(synth.two_tracks(), 70.0, script={3: 'refused'}))


if __name__ == '__main__':
    main()
