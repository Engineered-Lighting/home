"""probe_bundle_not_causal.py: does a replayed bundle hold only what a live router could have had?

What it checks
  den-bundle's assembler reads "only what is known by t1" by capture time: an
  observation counts when capture_ts <= t1, and a track line is dated by the newest
  capture_ts it names (records.py, assemble.py). A detector result arrives later than
  its capture: den-observe writes that time as freshness.available_ts (OBSERVE-V1,
  its proposed `freshness` slot); R5 measured about 1.46 s capture to completion,
  R6 about 0.28-0.46 s camera to GPU.
  The probe builds a 5 Hz keypoints stream for A with den-bundle's own synth helpers,
  adds den-observe's freshness block (available_ts = capture_ts + L_det) to every
  observation, assembles A's bundle at t1 with den-bundle's assemble(), and counts the
  frames whose result was not out by t1. It then assembles the bundle a live router
  would have built (only observations with available_ts <= t1) and compares `missing`.
  Finally it runs den-bundle's own gate on the replayed bundle (clauses `missing`,
  `no_wait`). FAIL = the replayed bundle holds frames that did not exist at its send
  time and the gate passes it.

How to run
  python3 -I probe_bundle_not_causal.py <root of the claude/rung-5-bundle checkout>

Synthetic data only; nothing is written; no network.
"""
import copy
import os
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.path.insert(0, os.path.join(root, 'tools', 'den-bundle'))
    import assemble as asm
    import gate
    import method
    import records
    import synth

    m = method.load_method()
    rule = method.load_router()
    print('RESULT method window_s=%s max_gap_s=%s (open: %s)' % (m['window_s'], m['max_gap_s'], ', '.join(m.open)))
    fails = 0
    for l_det, why in ((1.46, 'R5 capture-to-completion'), (0.37, 'R6 camera-to-GPU mid')):
        for phase in (0.0, 0.07, 0.13):
            obs, tracks = [], []
            for t in synth.steps(0.0, 20.0, 0.2):
                synth.seen(obs, tracks, 'A', round(t + phase, 3), 1.95, 0.95)
            for o in obs:
                o['freshness'] = {'available_ts': round(o['capture_ts'] + l_det, 3), 'age_s': l_det,
                                  'max_age_s': 1.0 if l_det <= 1.0 else 2.0, 'status': 'fresh',
                                  'valid_until': o['capture_ts'] + 3.0, 'depth': 'used', 'depth_gap_s': 0.033}
            t1 = synth.T0 + 12.0
            replay = asm.assemble(records.Store(obs, tracks), m, 'A', t1)
            by_id = {o['obs_id']: o for o in obs}
            ts_avail = {o['capture_ts']: o['freshness']['available_ts'] for o in obs}
            frames = [f for f in replay['frames'] if f['image_box'] is not None]
            future = [f for f in frames if ts_avail[f['ts']] > t1 + 1e-9]
            kp_future = [k for k in replay['keypoint_series'] if by_id[k['obs_id']]['freshness']['available_ts'] > t1 + 1e-9]
            live_obs = [o for o in obs if o['freshness']['available_ts'] <= t1 + 1e-9]
            live_ids = {o['obs_id'] for o in live_obs}
            live_tracks = [tl for tl in tracks if all(i in live_ids for i in tl['last_obs_ids'])]
            live = asm.assemble(records.Store(live_obs, live_tracks), m, 'A', t1)
            newest_live = max(f['ts'] for f in live['frames'] if f['image_box'] is not None)
            print('RESULT L_det=%.2f (%s) phase=%.2f: replay bundle %d frames, %d not out by send time (%d keypoint entries);'
                  ' replay missing=%s | live bundle %d frames, end hole %.2f s, live missing=%s'
                  % (l_det, why, phase, len(frames), len(future), len(kp_future), replay['missing'] or '[]',
                     len([f for f in live['frames'] if f['image_box'] is not None]), t1 - newest_live,
                     live['missing'] or '[]'))
            res = gate.check([replay], records.Store(obs, tracks), m, [], None, scored=False, rule=rule)
            cl = res['clauses']
            print('RESULT   den-bundle gate on the replayed bundle: missing=%s no_wait=%s verdict=%s'
                  % ('ok' if not cl['missing']['failures'] else 'FAIL', 'ok' if not cl['no_wait']['failures'] else 'FAIL',
                     res['verdict']))
            if future and not cl['missing']['failures'] and not cl['no_wait']['failures']:
                fails += 1
    print('%s %d of 6 replayed bundles hold detector results that were not out at their send time, and the gate passes them'
          % ('FAIL' if fails else 'PASS', fails))


if __name__ == '__main__':
    main()
