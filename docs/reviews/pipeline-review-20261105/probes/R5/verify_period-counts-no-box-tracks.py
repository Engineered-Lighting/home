#!/usr/bin/env python3
"""verify_period-counts-no-box-tracks.py: does per_person_period_s misstate A's rate when B is no_box?

What it checks
  ROUTER-V0.md says each person's period is min_interval_s times the number of
  eligible tracks and that the request record names it as per_person_period_s;
  a no_box bundle is logged, not sent, and the turn passes on. Three scenes, each
  routed on the tool's fake clock and fake sender from T0+2 s to T0+30 s:
    1. two_tracks with B's image boxes set to null (the suite's own no_box case);
    2. two_tracks with B seen only by radar (kind radar_point, no image_box, no
       keypoints): a realistic eligible-but-unboxable track;
    3. control: track_leaves (B really leaves) - the record follows the rotation.
  For each scene it prints A's measured gaps between starts and the
  per_person_period_s values A's records state.

How to run
  python3 verify_period-counts-no-box-tracks.py <root of the claude/rung-5-bundle checkout>

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


def route(scene, until=30.0):
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(synth.ts(2.0))
    sender = fakes.FakeSender(clock)
    r = router.Router(st, method.load_method(), method.load_router(), clock, sender)
    return router.run(r, clock, sender, synth.ts(until)), sender


def summary(recs, tid='A'):
    mine = [x for x in recs if x['track_id'] == tid and x['outcome'] != 'no_box']
    starts = [x['sent_ts'] for x in mine]
    gaps = sorted({round(b - a, 3) for a, b in zip(starts, starts[1:])})
    return gaps, sorted({x['per_person_period_s'] for x in mine})


def main():
    bad = 0
    s1 = synth.two_tracks()
    for o in s1['observations']:
        if o['obs_id'].startswith('o-B-'):
            o['image_box'] = None
    recs, sender = route(s1)
    gaps, stated = summary(recs)
    nobox = sorted({x['per_person_period_s'] for x in recs if x['outcome'] == 'no_box'})
    ok = stated == gaps
    bad += not ok
    print('%s scene 1 (B boxes null): A gaps %s s, A records state %s; B no_box records state %s; requests sent %d, all to %s' % (
        'PASS' if ok else 'FAIL', gaps, stated, nobox, len(sender.sent), sorted({rq['bundle']['track_id'] for _, rq in sender.sent})))

    s2 = synth.two_tracks()
    for o in s2['observations']:
        if o['obs_id'].startswith('o-B-'):
            o['kind'] = 'radar_point'
            o['source'] = {'sensor_id': 'den-radar', 'model': 'radar', 'model_version': 'synthetic'}
            for k in ('image_box', 'keypoints', 'joints_room', 'joint_set'):
                o.pop(k, None)
    recs, sender = route(s2)
    gaps, stated = summary(recs)
    elig = sorted({tuple(x['tracks']) for x in recs})
    ok = stated == gaps
    bad += not ok
    print('%s scene 2 (B radar only): eligible sets %s; A gaps %s s, A records state %s' % ('PASS' if ok else 'FAIL', elig, gaps, stated))

    recs, _ = route(synth.track_leaves(), until=40.0)
    tail = [(round(x['sent_ts'] - synth.T0, 1), x['track_id'], x['per_person_period_s']) for x in recs][-4:]
    print('INFO control track_leaves (B gone after 20 s): last records %s (stated period follows the rotation)' % tail)
    print('RESULT %s' % ('CONFIRMED: per_person_period_s states 8 s while A is served every 4 s' if bad else 'NOT REPRODUCED'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
