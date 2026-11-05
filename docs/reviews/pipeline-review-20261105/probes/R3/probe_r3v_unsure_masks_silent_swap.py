"""probe_r3v_unsure_masks_silent_swap.py -- R3 (den-track), validate lens.

What it checks
  How a switch is judged silent. scorer_core.switches() marks a switch
  silent only when the record that took the NEW person's first LABELLED
  observation said `confident`. Observations the owner marks "?" (cannot
  tell, left out of the score by truth.py) are skipped. So a track that takes
  the other person while still `confident`, during frames the owner cannot
  tell apart, and only turns `ambiguous` a frame later, is scored as a
  REPORTED switch.

How
  One take, frames 0..11, people A and B, tracks T1 and T2, written as
  den-track-truth/v1 and track/v1-shaped records and scored with
  pipeline.score_takes:
    k 0-4  T1 takes A's obs, T2 takes B's, both confident
    k 5-6  T1 takes B's obs, T2 takes A's, both still CONFIDENT (the swap)
    k 7-9  same, both ambiguous
    k 10-11 same, confident again
  Case 1: the owner labels k 5-6 "?". Case 2: the owner labels them A/B.
  The tracker output is identical. FAIL if case 1 reports 0 silent swaps
  while case 2 reports silent swaps (the verdict depends on whether the owner
  could tell who was who, and the unknown is resolved in the tracker's
  favour). An independent conservative rule (a switch is possibly silent if
  any record of that track that took an observation between the last label
  of the old person and the first label of the new one said confident) is
  printed beside it.

Run (stdlib only, synthetic records, temp folders only):
  python3 -B probe_r3v_unsure_masks_silent_swap.py <branch checkout root>
"""
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import method  # noqa: E402
import pipeline  # noqa: E402
import truth as truth_mod  # noqa: E402

VALUES = method.Method().values


def rec(k, tid, obs, conf):
    return {'schema_version': 'track/v1', 'capture_ts': float(k), 'track_id': tid, 'state': 'confirmed',
            'position': {'x': 0.0, 'y': 0.0, 'cov': [[0.01, 0], [0, 0.01]]}, 'velocity': {'vx': 0.0, 'vy': 0.0},
            'heading': None, 'heading_as_of': None, 'head_pose': None, 'posture': None, 'linked_objects': None,
            'association': {'status': 'confident' if conf else 'ambiguous',
                            'hypotheses': [] if conf else [{'hypothesis': 'same', 'track_id': tid, 'p': 0.6},
                                                           {'hypothesis': 'swapped', 'track_id': 'T2' if tid == 'T1'
                                                            else 'T1', 'p': 0.4}],
                            'since_ts': float(k)},
            'identity': {'binding': 'unbound', 'person_id': None, 'method': None, 'confidence': None, 'as_of': None},
            'last_obs_ids': [obs]}


def records():
    out = []
    for k in range(12):
        conf = not (7 <= k <= 9)
        swapped = k >= 5
        out.append(rec(k, 'T1', ('b%d' if swapped else 'a%d') % k, conf))
        out.append(rec(k, 'T2', ('a%d' if swapped else 'b%d') % k, conf))
    return out


def truth(tmp, unsure):
    lines = [truth_mod.take_line('t1', ['A', 'B'], observations_sha256='sha256:x')]
    for k in range(12):
        pa, pb = ('?', '?') if (unsure and k in (5, 6)) else ('A', 'B')
        lines.append(truth_mod.frame_line('cam', float(k), [('a%d' % k, pa), ('b%d' % k, pb)]))
    lines.append(truth_mod.window_line('c1', 'crossing', 0.0, 11.0))
    p = os.path.join(tmp, 'truth-%s.jsonl' % unsure)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(json.dumps(l) + '\n' for l in lines)
    return truth_mod.read(p)


def conservative_silent(recs, tr):
    """Independent rule: per track, walk its records in time order; between the
    last observation labelled for the old person and the first labelled for
    the new one, any confident record that took an observation makes the
    switch possibly silent."""
    labels = tr['labels']
    by_track = {}
    for r in sorted(recs, key=lambda r: r['capture_ts']):
        if r['state'] == 'confirmed' and r['last_obs_ids']:
            by_track.setdefault(r['track_id'], []).append(r)
    n = 0
    for tid, rs in by_track.items():
        last_person, gap_confident = None, False
        for r in rs:
            people = [labels[o][1] for o in r['last_obs_ids'] if o in labels and labels[o][1] is not None]
            conf = r['association']['status'] == 'confident'
            if not people:
                gap_confident = gap_confident or conf
                continue
            p = people[0]
            if last_person is not None and p != last_person and (conf or gap_confident):
                n += 1
            last_person, gap_confident = p, False
    return n


def main():
    tmp = tempfile.mkdtemp(prefix='probe-r3v-unsure-')
    try:
        recs = records()
        out = {}
        for unsure in (True, False):
            tr = truth(tmp, unsure)
            rep = pipeline.score_takes([('t1', recs, tr)], VALUES)
            w = rep['windows'][0]
            out[unsure] = w
            print('RESULT overlap frames labelled %-9s: tool silent swaps %d, reported %d, window failed %s; '
                  'independent conservative count of possibly-silent switches %d'
                  % ('"?"' if unsure else 'A/B', w['silent_swaps'], w['reported_switches'],
                     w['silent_swaps'] > 0, conservative_silent(recs, tr)))
        if out[True]['silent_swaps'] == 0 and out[False]['silent_swaps'] > 0:
            print('FAIL the same confident swap is scored silent when the owner can tell who it was, and '
                  'reported (window passes) when the owner marks the overlap frames "cannot tell"')
            return 1
        print('PASS')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
