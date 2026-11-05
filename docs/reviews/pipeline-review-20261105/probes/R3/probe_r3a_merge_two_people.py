"""probe_r3a_merge_two_people: den-observe writes ONE observation per
detector box (kind `keypoints` when RTMPose gave the box a pose, else
`person_box`), so a person_box and a keypoints observation in one frame are
two different boxes.  den-track's reader merges a person_box and a keypoints
observation from different models whose image boxes overlap by merge_iou
(0.5) as "one person".  When A walks behind B and A's box gets no pose (the
usual case for an occluded, low-score box), A's observation is merged into
B's measurement: B's track lists A's observation in last_obs_ids, A's track
goes unseen and coasts while A is detected every frame.

Records are built the way den-observe's observe.py writes them (no
schema_version, validity "valid", source.model "yolo26s" for boxes and
"rtmpose-m" for keypoints, room_position with a 3x3 cov, freshness).

Run:  python3 probe_r3a_merge_two_people.py <root of the rung-3-track checkout>
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402

VALUES = method_mod.Method().values      # proposed values: no exits, no floor


def joints():
    return [{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(17)]


def obs(ms, idx, kind, box, xy, t):
    o = {'obs_id': 'o-den-oak-%d-%d' % (ms, idx),
         'source': {'sensor_id': 'den-oak', 'model': 'rtmpose-m' if kind == 'keypoints' else 'yolo26s',
                    'model_version': None},
         'kind': kind, 'capture_ts': t, 'transform_id': 'den-oak@synthetic', 'depth_ts': t,
         'image_box': box, 'score': 0.8 if kind == 'keypoints' else 0.45,
         'room_position': {'x': xy[0], 'y': xy[1], 'z': 1.1,
                           'cov': [[0.01, 0, 0], [0, 0.02, 0], [0, 0, 0.003]]},
         'visibility': 'visible' if kind == 'keypoints' else 'partial', 'validity': 'valid',
         'freshness': {'status': 'fresh', 'depth': 'used'}}
    if kind == 'keypoints':
        o.update(keypoints=[[None, None, None]] * 17, joint_set='coco17', joints_room=joints())
    return o


def main():
    recs, labels, fps, t0 = [], {}, 4.0, 1793066600.0
    behind = []
    for k in range(int(8 * fps)):
        t = round(t0 + k / fps, 4)
        ms = int(round(t * 1000))
        xa = -2.0 + 0.5 * k / fps                       # A walks across, 0.6 m behind B
        b = obs(ms, 0, 'keypoints', [860, 200, 1060, 900], (0.0, 2.0), t)
        if abs(xa) < 0.35:                              # A behind B: box only, inside B's box
            a = obs(ms, 1, 'person_box', [880, 240, 1040, 860], (xa, 2.6), t)
            behind.append(t)
        else:
            u = 960 + 600 * xa / 2.6
            a = obs(ms, 1, 'keypoints', [u - 58, 260, u + 58, 830], (xa, 2.6), t)
        for o, p in ((b, 'B'), (a, 'A')):
            recs.append(o)
            labels[o['obs_id']] = (t, p)
    frames = pipeline.observation_frames(recs, VALUES['input'])
    merged = sum(1 for _, _, ms in frames for m in ms if len(m['ids']) > 1)
    tr = pipeline.track_frames(frames, VALUES)
    mixed = [(r['capture_ts'], r['track_id'], r['association']['status'], r['last_obs_ids']) for r in tr
             if len({labels[o][1] for o in r['last_obs_ids']}) > 1]
    a_tracks = {}
    for r in tr:
        for o in r['last_obs_ids']:
            if labels[o][1] == 'A' and len(r['last_obs_ids']) == 1:
                a_tracks.setdefault(r['track_id'], 0)
                a_tracks[r['track_id']] += 1
    a_main = max(a_tracks, key=a_tracks.get)
    a_states = [(r['capture_ts'], r['state']) for r in tr if r['track_id'] == a_main and behind[0] <= r['capture_ts'] <= behind[-1] + 0.01]
    print('frames where A is box-only behind B: %d; measurements merged from two observations: %d' % (len(behind), merged))
    for m in mixed[:6]:
        print('  t=%.2f %s %s last_obs_ids=%s (persons %s)' % (m[0] - t0, m[1], m[2], m[3], sorted({labels[o][1] for o in m[3]})))
    print("  A's own track %s while A is detected behind B: %s" % (a_main, sorted({s for _, s in a_states})))
    truth = {'take': {}, 'labels': labels, 'unsure': 0,
             'windows': [{'id': 'c1', 'kind': 'crossing', 'start': t0 + 2.0, 'end': t0 + 6.0}]}
    rep = pipeline.score_takes([('take', tr, truth)], VALUES)
    print('  scorer: switches in window %d (silent %d, reported %d), IDF1 %.3f, A obs missed %d'
          % (rep['windows'][0]['switches'], rep['silent_swaps'], rep['reported_switches'], rep['pooled']['idf1'],
             rep['windows'][0]['idfn']))
    bad = merged > 0 and bool(mixed)
    print('RESULT: %s' % ('FAIL: two people\'s observations merged into one measurement; %d track record(s) list both A and B' % len(mixed) if bad else 'PASS'))


if __name__ == '__main__':
    main()
