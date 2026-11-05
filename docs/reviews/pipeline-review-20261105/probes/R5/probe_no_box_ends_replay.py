#!/usr/bin/env python3
"""probe_no_box_ends_replay.py: a no_box turn with no other boxed track ends router.run().

What it checks
  ROUTER-V0.md: "A bundle with no boxed frame ... is logged as no_box and not
  sent; it uses no cap, and the turn passes to the next track." When every
  eligible track is no_box at one tick, Router.next_wake() returns the current
  time, router.run() drops wakes that are not strictly later, finds none, and
  stops: the replay ends silently long before --to, and the track gets no more
  requests even after its boxes come back.

  Scene: one confirmed track A seen every 0.5 s from 0 to 60 s. Case 1: its
  results before 10 s are rejected (as den-observe marks an expired detector
  result, validity "rejected:detector_result_expired") but still listed by the
  track. Case 2: the same with image_box null instead. From 10 s on all results
  are valid and boxed. An independent expectation: from the first tick at which
  the window holds a valid boxed frame (t = 10.0 s), one request every 4 s up to
  60 s, i.e. 13 sends.

  Also runs the same scene through the command line (`route`) and prints its
  summary, to show the owner sees no sign of the early stop.

How to run
  python3 probe_no_box_ends_replay.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network; temp files only.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import den_bundle  # noqa: E402
import fakes       # noqa: E402
import method      # noqa: E402
import records     # noqa: E402
import router      # noqa: E402

T0 = 1793066400.0


def scene(mode):
    obs, lines = [], []
    for k in range(0, 121):
        t = round(T0 + 0.5 * k, 3)
        oid = 'p-A-%d' % k
        o = {'obs_id': oid, 'source': {'sensor_id': 'den-oak', 'model': 'm', 'model_version': 'v'},
             'capture_ts': t, 'kind': 'keypoints', 'image_box': [700, 300, 890, 820],
             'keypoints': [[750, 350, 0.9]] * 17, 'joint_set': 'coco17',
             'joints_room': [{'j': j, 'xyz': [1.0, 1.0, 1.0], 'cov': [[0.003, 0, 0], [0, 0.003, 0], [0, 0, 0.003]],
                              'status': 'supported', 'lift': 'stereo'} for j in range(17)],
             'depth_ts': round(t - 0.033, 3), 'transform_id': 'den-oak@2026-10-18', 'validity': 'valid'}
        if 0.5 * k < 10.0:
            if mode == 'rejected':
                o['validity'] = 'rejected:detector_result_expired'
            else:
                o['image_box'] = None
        obs.append(o)
        lines.append({'track_id': 'A', 'state': 'confirmed', 'position': {'x': 1.0, 'y': 1.0, 'cov': [[0.03, 0], [0, 0.06]]},
                      'association': {'status': 'confident', 'hypotheses': [], 'since_ts': T0},
                      'linked_objects': [], 'last_obs_ids': [oid]})
    return obs, lines


def main():
    bad = 0
    m, R = method.load_method(), method.load_router()
    for mode in ('rejected', 'null_box'):
        obs, lines = scene(mode)
        st = records.Store(obs, lines)
        clock = fakes.FakeClock(T0 + 2.0)
        sender = fakes.FakeSender(clock)
        r = router.Router(st, m, R, clock, sender)
        recs = router.run(r, clock, sender, T0 + 60.0)
        got = [(round(x['sent_ts'] - T0, 2), x['outcome']) for x in recs]
        sends = len(sender.sent)
        expect = [10.0 + 4 * i for i in range(13)]
        ok = sends == len(expect)
        bad += not ok
        print('%s %s: run() stopped at clock %.2f s of 60; %d requests sent (expected %d, at %s ...); records %s' % (
            'PASS' if ok else 'FAIL', mode, clock() - T0, sends, len(expect), expect[:3], got))
        # what a live loop would see: next_wake() keeps answering "now"
        print('    next_wake(now) - now = %r s (a driver that sleeps until next_wake would spin)' % (
            None if r.next_wake(clock()) is None else round(r.next_wake(clock()) - clock(), 3)))

    tmp = tempfile.mkdtemp(prefix='probe-r5-nobox-')
    try:
        obs, lines = scene('rejected')
        records.write_jsonl(os.path.join(tmp, 'obs.jsonl'), obs)
        records.write_jsonl(os.path.join(tmp, 'tracks.jsonl'), lines)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = den_bundle.main(['route', '--observations', os.path.join(tmp, 'obs.jsonl'),
                                    '--tracks', os.path.join(tmp, 'tracks.jsonl'), '--from', '2', '--to', '60',
                                    '--requests', os.path.join(tmp, 'rq.jsonl'), '--bundles', os.path.join(tmp, 'bd.jsonl')])
        print('CLI route --from 2 --to 60 exit %d, output:' % code)
        for line in out.getvalue().splitlines():
            print('    ' + line)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = den_bundle.main(['gate', '--observations', os.path.join(tmp, 'obs.jsonl'),
                                    '--tracks', os.path.join(tmp, 'tracks.jsonl'),
                                    '--bundles', os.path.join(tmp, 'bd.jsonl'), '--requests', os.path.join(tmp, 'rq.jsonl')])
        print('CLI gate on that replay: exit %d, last line: %s' % (code, out.getvalue().strip().splitlines()[-1]))
    finally:
        shutil.rmtree(tmp, True)
    print('RESULT %s: %d of 2 replays stopped early' % ('PASS' if not bad else 'FAIL', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
