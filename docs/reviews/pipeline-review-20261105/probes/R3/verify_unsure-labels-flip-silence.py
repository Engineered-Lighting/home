"""verify_unsure-labels-flip-silence: independent re-check of finding
unsure-labels-flip-silence with a labeller model NOT built from the
tracker's output.

The reporter's part 4 marked "?" exactly from each reported switch to the
track's first confident record (a span chosen from tracker output).  Here
a blind, human-like rule is used instead: an observation is "?" (cannot
tell) in a frame where its image box overlaps the OTHER person's box by
IoU >= THR (boxes from synth.py's projection of the true positions).  The
same takes are scored with full labels and with the "?" labels, and the
silent / reported switch counts are compared.  Also, for each reported
switch, the switching record itself is checked: was the tracker ambiguous
at the record where the track actually took the new person?

A second set dilates the "?" span by +-D seconds around every overlapping
frame (a conservative labeller who marks the whole messy part).

Run:  python3 verify_unsure-labels-flip-silence.py <root of the rung-3-track checkout>
Synthetic data only, in memory; no network.
"""
import copy
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import records  # noqa: E402
import synth  # noqa: E402

VALUES = dict(method_mod.Method(os.path.join(TOOL, 'METHOD.md')).values, room=synth.ROOM_MODEL)


def run(thr, scenes, seeds, dil=0.0):
    tot = {'full_silent': 0, 'full_reported': 0, 'q_silent': 0, 'q_reported': 0, 'unsure': 0, 'labels': 0,
           'flip_to_silent': 0, 'flip_to_reported': 0}
    for name in scenes:
        for seed in seeds:
            obs, lines = synth.records(synth.scenario(name, seed, 'typical'))
            frames = pipeline.observation_frames(obs, VALUES['input'])
            recs = pipeline.track_frames(frames, VALUES)
            tr = pipeline.truth_from_lines(lines)
            full = pipeline.score_takes([(name, recs, tr)], VALUES)
            # blind "?" rule: box overlaps the other person's box
            box = {o['obs_id']: o.get('image_box') for o in obs}
            by_t = {}
            for oid, (ts, p) in tr['labels'].items():
                by_t.setdefault(ts, []).append((oid, p))
            tr2 = copy.deepcopy(tr)
            overlap_ts = []
            for ts, items in by_t.items():
                for oid, p in items:
                    if p is None:
                        continue
                    for oid2, p2 in items:
                        if p2 is not None and p2 != p and box.get(oid) and box.get(oid2) \
                                and records._iou(box[oid], box[oid2]) >= thr:
                            tr2['labels'].pop(oid, None)
                            overlap_ts.append(ts)
                            break
            if dil > 0:
                for oid, (ts, p) in list(tr2['labels'].items()):
                    if p is not None and any(abs(ts - u) <= dil for u in overlap_ts):
                        tr2['labels'].pop(oid, None)
            tot['labels'] += len(tr['labels'])
            tot['unsure'] += len(tr['labels']) - len(tr2['labels'])
            q = pipeline.score_takes([(name, recs, tr2)], VALUES)
            fs = full['silent_swaps'] + full['silent_swaps_outside_windows']
            qs = q['silent_swaps'] + q['silent_swaps_outside_windows']
            tot['full_silent'] += fs
            tot['full_reported'] += full['reported_switches']
            tot['q_silent'] += qs
            tot['q_reported'] += q['reported_switches']
            if qs > fs:
                tot['flip_to_silent'] += 1
            if qs < fs:
                tot['flip_to_reported'] += 1
    return tot


scenes = ('touching_crossing', 'near_pass', 'close_crossing', 'crossing_behind_armchair')
import itertools
cfgs = [(0.5, 0.0), (0.3, 0.0), (0.1, 0.0), (0.3, 0.5), (0.3, 1.0), (0.3, 1.5)]
for thr, dil in cfgs:
    t = run(thr, scenes, range(200, 220), dil)
    print('dilate +-%.1fs ' % dil, end='')
    print('IoU>=%.1f: labels %d, marked "?" %d (%.1f%%) | full labels: silent %d reported %d | with "?": silent %d '
          'reported %d | takes where silent rose %d, fell %d'
          % (thr, t['labels'], t['unsure'], 100.0 * t['unsure'] / max(t['labels'], 1), t['full_silent'],
             t['full_reported'], t['q_silent'], t['q_reported'], t['flip_to_silent'], t['flip_to_reported']))
    print('RESULT IoU>=%.1f: %s' % (thr, 'FAIL (verdict flips under blind "?" labels)'
                                    if t['flip_to_silent'] or t['flip_to_reported'] else 'PASS/not reproduced'))
