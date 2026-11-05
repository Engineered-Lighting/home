#!/usr/bin/env python3
"""probe_causality_available_ts.py: bundles use observations that had not arrived by emission.

What it checks
  METHOD-V1.md says a bundle requested at t1 reads "only records known by t1:
  observations captured by then and track lines dated by then", and router v0
  sends "at once with whatever evidence is there". Rung 2 (den-observe) writes,
  on every Observation, `freshness.available_ts`: when the detector's result
  was complete (note R5: capture to completion about 1.46 s in the den). The
  assembler and the store ignore it, so in a replay every bundle holds
  keypoint entries, frames, the crop and positions captured in the last
  ~1.46 s before its send, which a live router could not have had. The gate
  checks bundles with the same capture-time view, so it passes them.

  Part 1: synthetic observations shaped like den-observe's output (fields and
  the `freshness` dict as observe.freshness() writes them), latency 1.46 s,
  one track every 0.5 s; router v0 replays 2..30 s; for each request it counts
  the entries whose source observation arrived after sent_ts, and compares the
  tool's `missing` with a bundle assembled only from observations available
  by sent_ts (the causal bundle) at a 1.6 s latency.
  Part 2 (optional, second argument): runs den-observe's own synthetic demo
  (`replay.py --demo`, in a temp folder) and assembles a bundle at the last
  capture from its real output, counting entries not yet available.

How to run
  python3 probe_causality_available_ts.py <rung-5 checkout root> [<rung-2 checkout root>]

Synthetic data only, no network; temp files only.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
R2 = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
from assemble import assemble  # noqa: E402

T0 = 1793066400.0


def r2_like(latency, until=40.0, dt=0.5):
    obs, lines = [], []
    k = 0
    while k * dt <= until + 1e-9:
        t = round(T0 + k * dt, 6)
        oid = 'o-den-oak-%d-0' % int(round(t * 1000))
        obs.append({'obs_id': oid, 'source': {'sensor_id': 'den-oak', 'model': 'm', 'model_version': 'v'},
                    'kind': 'keypoints', 'capture_ts': t, 'transform_id': 'den-oak@2026-10-18',
                    'depth_ts': round(t - 0.004, 6), 'image_box': [700.0, 300.0, 890.0, 820.0], 'score': 0.9,
                    'keypoints': [[750.0, 350.0, 0.9]] * 17, 'joint_set': 'coco17',
                    'joints_room': [{'j': j, 'xyz': [1.0, 1.0, 1.0], 'cov': [[0.003, 0, 0], [0, 0.003, 0], [0, 0, 0.003]],
                                     'status': 'supported', 'lift': 'stereo'} for j in range(17)],
                    'room_position': {'x': 1.0, 'y': 1.0, 'z': 1.0, 'cov': [[0.003, 0, 0], [0, 0.003, 0], [0, 0, 0.003]]},
                    'visibility': 'visible', 'validity': 'valid',
                    'freshness': {'available_ts': round(t + latency, 6), 'age_s': latency, 'max_age_s': 2.0,
                                  'status': 'fresh', 'valid_until': round(t + 3.0, 6), 'depth': 'used',
                                  'depth_gap_s': 0.004}})
        lines.append({'track_id': 'A', 'state': 'confirmed', 'position': {'x': 1.0, 'y': 1.0, 'cov': [[0.03, 0], [0, 0.06]]},
                      'association': {'status': 'confident', 'hypotheses': [], 'since_ts': T0},
                      'linked_objects': [], 'last_obs_ids': [oid]})
        k += 1
    return obs, lines


def avail(o):
    return o['freshness']['available_ts']


def future_counts(bundle, st, at):
    kp = sum(1 for k in bundle['keypoint_series'] if avail(st.obs[k['obs_id']]) > at + 1e-6)
    by_key = {(o['source']['sensor_id'], o['capture_ts']): o for o in st.obs.values()}
    fr = sum(1 for f in bundle['frames'] if avail(by_key[(f['camera_id'], f['ts'])]) > at + 1e-6)
    crop_src = bundle['frames'][-1]['crop_ref']['from_ts'] if bundle['frames'] else None
    crop_future = crop_src is not None and avail(by_key[('den-oak', crop_src)]) > at + 1e-6
    return kp, len(bundle['keypoint_series']), fr, len(bundle['frames']), crop_future


def causal_bundle(obs, lines, m, at):
    known = [o for o in obs if avail(o) <= at + 1e-6]
    ids = {o['obs_id'] for o in known}
    kl = [l for l in lines if all(i in ids for i in l['last_obs_ids'])]
    return assemble(records.Store(known, kl), m, 'A', at)


def part1():
    m, R = method.load_method(), method.load_router()
    bad = 0
    for latency in (1.46, 1.6):
        obs, lines = r2_like(latency)
        st = records.Store(obs, lines)
        clock = fakes.FakeClock(T0 + 2.0)
        sender = fakes.FakeSender(clock)
        r = router.Router(st, m, R, clock, sender)
        router.run(r, clock, sender, T0 + 30.0)
        print('latency %.2f s:' % latency)
        for rec, b in zip([x for x in r.records if x['outcome'] != 'no_box'], r.bundles):
            at = rec['sent_ts']
            kp, nkp, fr, nfr, crop_future = future_counts(b, st, at)
            cb = causal_bundle(obs, lines, m, at)
            bad += kp > 0
            print('  sent %5.2f: %d of %d keypoint entries and %d of %d frames arrived after the send; crop from a frame '
                  'not yet arrived: %s; missing tool=%s causal=%s' % (at - T0, kp, nkp, fr, nfr, crop_future,
                                                                       b['missing'], cb['missing']))
    return bad


def part2():
    if not R2:
        print('part 2 skipped (no rung-2 checkout given)')
        return 0
    tmp = tempfile.mkdtemp(prefix='probe-r5-causal-')
    try:
        env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp)
        p = subprocess.run([sys.executable, '-B', os.path.join(R2, 'tools', 'den-observe', 'replay.py'), '--demo'],
                           cwd=tmp, env=env, capture_output=True, text=True, timeout=300)
        files = glob.glob(os.path.join(tmp, '**', 'observations.jsonl'), recursive=True)
        if p.returncode != 0 or not files:
            print('part 2: den-observe demo did not run (exit %d): %s' % (p.returncode, p.stderr[-400:]))
            return 0
        obs = records.read_jsonl(files[0])
        lines = [{'track_id': 'A', 'state': 'confirmed', 'position': {'x': 0.0, 'y': 3.0, 'cov': [[0.03, 0], [0, 0.06]]},
                  'association': {'status': 'confident', 'hypotheses': [], 'since_ts': obs[0]['capture_ts']},
                  'linked_objects': [], 'last_obs_ids': [o['obs_id']]} for o in sorted(obs, key=lambda o: o['capture_ts'])]
        st = records.Store(obs, lines)
        m = method.load_method()
        frame_px = dict(m.values['frame_px'])
        at = max(o['capture_ts'] for o in obs)
        b = assemble(st, m, 'A', at)
        late = [k['obs_id'] for k in b['keypoint_series'] if st.obs[k['obs_id']]['freshness']['available_ts'] > at + 1e-6]
        print('part 2: den-observe demo wrote %d observations with freshness.available_ts - capture_ts = %s s; '
              'a bundle at the last capture (%.3f) holds %d keypoint entries, %d of them not yet available (%s)' % (
                  len(obs), sorted({round(o['freshness']['available_ts'] - o['capture_ts'], 3) for o in obs}),
                  at, len(b['keypoint_series']), len(late), late))
        return 1 if late else 0
    finally:
        shutil.rmtree(tmp, True)


def main():
    bad1 = part1()
    bad2 = part2()
    print('RESULT %s: %d routed bundles (and %s demo bundle) used observations that arrived after their send' % (
        'FAIL' if bad1 or bad2 else 'PASS', bad1, 'the' if bad2 else 'no'))
    return 1 if bad1 or bad2 else 0


if __name__ == '__main__':
    sys.exit(main())
