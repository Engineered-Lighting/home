#!/usr/bin/env python3
"""probe_backdated_lines.py: track lines with no observation take effect before they were written.

What it checks
  records.Store dates a track line by the newest capture_ts it names and never
  earlier than the line before it, so a line naming no observation (every
  coasting or ended update, by the Track schema's last_obs_ids rule) is dated
  at the previous line's time. Scene: A seen every 0.5 s from 0 to 5.0 s, then
  the detector loses A: coasting lines at 5.5 .. 8.0 s (predicted positions
  drifting toward the door, x from 2.25 to 3.75 m) and an `ended` line at 8.5 s,
  none naming an observation. A bundle at t1 = 5.2 s and the router's
  eligibility at 5.2 s should see only what existed by 5.2 s.

  Reports: positions in the 5.2 s bundle that come from lines written after
  5.2 s (all stamped ts = 5.0), and whether the router counts A as eligible at
  5.2 s (it reads the 8.5 s `ended` line as already in effect).

How to run
  python3 probe_backdated_lines.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network, no files written.
"""
import os
import sys

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
from assemble import assemble  # noqa: E402

T0 = 1793066400.0


def main():
    obs, lines, written = [], [], []
    for k in range(0, 11):
        t = round(T0 + 0.5 * k, 3)
        oid = 'p-A-%d' % k
        obs.append({'obs_id': oid, 'source': {'sensor_id': 'den-oak', 'model': 'm', 'model_version': 'v'},
                    'capture_ts': t, 'kind': 'keypoints', 'image_box': [700, 300, 890, 820],
                    'keypoints': [[750, 350, 0.9]] * 17, 'joint_set': 'coco17',
                    'joints_room': [{'j': j, 'xyz': [2.0, 1.0, 1.0], 'cov': [[0.003, 0, 0], [0, 0.003, 0], [0, 0, 0.003]],
                                     'status': 'supported', 'lift': 'stereo'} for j in range(17)],
                    'depth_ts': round(t - 0.033, 3), 'transform_id': 'den-oak@2026-10-18', 'validity': 'valid'})
        lines.append({'track_id': 'A', 'state': 'confirmed', 'position': {'x': 2.0, 'y': 1.0, 'cov': [[0.03, 0], [0, 0.06]]},
                      'association': {'status': 'confident', 'hypotheses': [], 'since_ts': T0},
                      'linked_objects': [], 'last_obs_ids': [oid]})
        written.append(0.5 * k)
    for i, t in enumerate([5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5]):
        lines.append({'track_id': 'A', 'state': 'ended' if t == 8.5 else 'coasting',
                      'position': {'x': round(2.0 + 0.25 * (i + 1), 2), 'y': 1.0, 'cov': [[0.3, 0], [0, 0.6]]},
                      'association': {'status': 'confident', 'hypotheses': [], 'since_ts': T0},
                      'linked_objects': [], 'last_obs_ids': []})
        written.append(t)
    st = records.Store(obs, lines)
    m, R = method.load_method(), method.load_router()
    t1 = T0 + 5.2
    b = assemble(st, m, 'A', t1)
    later = [(round(l.ts - T0, 2), l.rec['state'], l.rec['position']['x'], w)
             for l, w in zip(st.lines_of('A'), written) if l.ts <= t1 + 1e-6 and w > 5.2]
    xs_at_5 = [p['x'] for p in b['positions'] if abs(p['ts'] - (T0 + 5.0)) < 1e-6]
    clock = fakes.FakeClock(t1)
    r = router.Router(st, m, R, clock, fakes.FakeSender(clock))
    elig = r.eligible(t1)
    ok_pos = len(xs_at_5) == 1
    ok_elig = elig == ['A']
    print('%s bundle at 5.2 s: %d positions stamped 5.0 s, x = %s; lines written after 5.2 s but read as known: %s' % (
        'PASS' if ok_pos else 'FAIL', len(xs_at_5), xs_at_5, later))
    print('%s router eligibility at 5.2 s: %s (A was confirmed and seen at 5.0 s; the ended line is written at 8.5 s)' % (
        'PASS' if ok_elig else 'FAIL', elig))
    bad = (not ok_pos) + (not ok_elig)
    print('RESULT %s: %d of 2 checks read lines before they were written' % ('PASS' if not bad else 'FAIL', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
