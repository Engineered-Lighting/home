"""probe_v_merge_long_overlap: verifier probe for finding merge-joins-two-people.

Same den-observe-shaped records as probe_r3a_merge_two_people.py (one
observation per detector box: keypoints from 'rtmpose-m' when the box got a
pose, else person_box from 'yolo26s'), but A stands still behind B for
`hold` seconds (a near pass along the camera's view), box only, A's box
inside B's (IoU about 0.7, within an NMS-free detector's output). Prints how
the merged frames are scored: silent or reported switches, and A's track state.

Synthetic data, in memory, no network.
Run: python3 probe_v_merge_long_overlap.py <root of the rung-3-track checkout>
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402

VALUES = method_mod.Method(os.path.join(TOOL, 'METHOD.md')).values


def obs(ms, idx, kind, box, xy, t):
    o = {'obs_id': 'o-den-oak-%d-%d' % (ms, idx),
         'source': {'sensor_id': 'den-oak', 'model': 'rtmpose-m' if kind == 'keypoints' else 'yolo26s',
                    'model_version': None},
         'kind': kind, 'capture_ts': t, 'transform_id': 'den-oak@synthetic', 'depth_ts': t,
         'image_box': box, 'score': 0.8 if kind == 'keypoints' else 0.45,
         'room_position': {'x': xy[0], 'y': xy[1], 'z': 1.1, 'cov': [[0.01, 0, 0], [0, 0.02, 0], [0, 0, 0.003]]},
         'visibility': 'visible' if kind == 'keypoints' else 'partial', 'validity': 'valid'}
    if kind == 'keypoints':
        o.update(keypoints=[[None, None, None]] * 17, joint_set='coco17',
                 joints_room=[{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(17)])
    return o


def run(hold):
    recs, labels, fps, t0 = [], {}, 4.0, 1793066600.0
    walk_in = 3.0
    total = walk_in + hold + 3.0
    for k in range(int(total * fps)):
        t = round(t0 + k / fps, 4)
        lt = k / fps
        ms = int(round(t * 1000))
        b = obs(ms, 0, 'keypoints', [860, 200, 1060, 900], (0.0, 2.0), t)
        if lt < walk_in:
            xa = -1.5 + 1.5 * lt / walk_in
        elif lt < walk_in + hold:
            xa = 0.0
        else:
            xa = 1.5 * (lt - walk_in - hold) / 3.0
        if abs(xa) < 0.35:
            a = obs(ms, 1, 'person_box', [880, 240, 1040, 860], (xa, 2.6), t)
        else:
            u = 960 + 600 * xa / 2.6
            a = obs(ms, 1, 'keypoints', [u - 58, 260, u + 58, 830], (xa, 2.6), t)
        for o, p in ((b, 'B'), (a, 'A')):
            recs.append(o)
            labels[o['obs_id']] = (t, p)
    frames = pipeline.observation_frames(recs, VALUES['input'])
    tr = pipeline.track_frames(frames, VALUES)
    truth = {'take': {}, 'labels': labels, 'unsure': 0,
             'windows': [{'id': 'c1', 'kind': 'near_pass', 'start': t0 + 2.0, 'end': t0 + walk_in + hold + 1.5}]}
    rep = pipeline.score_takes([('take', tr, truth)], VALUES)
    mixed_conf = sum(1 for r in tr if len({labels[o][1] for o in r['last_obs_ids']}) > 1
                     and r['association']['status'] == 'confident' and r['state'] == 'confirmed')
    states = sorted({r['state'] for r in tr if r['track_id'] != 'T1'})
    print('hold %.1f s: silent %d, reported %d, IDF1 %.3f, confirmed+confident records listing both A and B: %d, '
          'tracks %s, gate silent_swaps_zero %s'
          % (hold, rep['silent_swaps'] + rep['silent_swaps_outside_windows'], rep['reported_switches'],
             rep['pooled']['idf1'], mixed_conf, sorted({r['track_id'] for r in tr}), rep['gate']['silent_swaps_zero']))


if __name__ == '__main__':
    for hold in (1.0, 3.0, 5.0, 8.0):
        run(hold)
