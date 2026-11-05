#!/usr/bin/env python3
"""probe_period_with_no_box.py: per_person_period_s counts a track that never gets a request.

What it checks
  router_request/v0's per_person_period_s is min_interval_s x len(tracks), with
  tracks = every eligible track at the start, including tracks whose bundles
  are no_box and are never sent. Scene: the tool's own two_tracks fixture with
  B's image boxes removed. A is served every 4 s, yet every record says 8 s.

How to run
  python3 probe_period_with_no_box.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network, no files written.
"""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(sys.argv[1], 'tools', 'den-bundle'))

import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
import synth    # noqa: E402


def main():
    scene = synth.two_tracks()
    for o in scene['observations']:
        if o['obs_id'].startswith('o-B-'):
            o['image_box'] = None
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock)
    r = router.Router(st, method.load_method(), method.load_router(), clock, sender)
    recs = [x for x in router.run(r, clock, sender, synth.ts(30.0)) if x['outcome'] != 'no_box']
    starts = [x['sent_ts'] for x in recs]
    gaps = sorted({round(b - a, 3) for a, b in zip(starts, starts[1:])})
    stated = sorted({x['per_person_period_s'] for x in recs})
    ok = stated == gaps
    print('%s A served with gaps %s s; records state per_person_period_s %s' % ('PASS' if ok else 'FAIL', gaps, stated))
    print('RESULT %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
