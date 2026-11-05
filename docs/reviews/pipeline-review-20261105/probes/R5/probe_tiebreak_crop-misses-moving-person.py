"""Tiebreak probe for finding crop-misses-moving-person (rung 5, den-bundle).

What it checks
  Two synthetic scenes the tool's fixtures do not cover, with the tracker confident
  throughout (so every bundle is association_safe=true and no crossing is annotated):
    1. walk_in: A alone walks in at 1.0 m/s and stops at the counter at t=4 s.
    2. approach_seated_B: B sits still; A walks in at 1.0 m/s and stops beside B at t=4 s.
  For bundles assembled every 0.5 s over 2..8 s it counts, per frame, whether the frame's own
  image_box lies inside the bundle's single crop_ref, and whether the crop holds more of the
  other track's box than of this track's box. It also runs gate.check (practice) on the bundles.
  It prints the counts and whether each condition shows up inside association_safe=true bundles.
  This separates the crossing fixture (where the other-dominant frames are all unsafe) from
  ordinary walking scenes.

How to run
  python3 -B probe_tiebreak_crop-misses-moving-person.py <root of the rung-5-bundle checkout>
Synthetic data only; nothing is written; no network.
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from assemble import assemble  # noqa: E402

M = method.load_method()


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def overlap(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def scene(with_b):
    obs, tracks = [], []
    for t in synth.steps(0.0, 8.0, 0.5):
        xa = min(0.5 + 1.0 * t, 2.7)          # A walks at 1 m/s from x=0.5 and stops at x=2.7
        synth.seen(obs, tracks, 'A', t, round(xa, 3), 2.4)
        if with_b:
            synth.seen(obs, tracks, 'B', t, 3.2, 2.6, standing=False)   # B seated, still
    return records.Store(obs, tracks)


for name, st, tids in (('walk_in', scene(False), ('A',)),
                       ('approach_seated_B', scene(True), ('A', 'B'))):
    n = outside = outside_safe = other_more = other_more_safe = 0
    bundles = []
    for i in range(13):
        t1 = synth.ts(2.0 + 0.5 * i)
        for tid in tids:
            b = assemble(st, M, tid, t1)
            bundles.append(b)
            if tid != 'A':
                continue
            for k, f in enumerate(b['frames']):
                if f['image_box'] is None or f['crop_ref'] is None:
                    continue
                n += 1
                c, box = f['crop_ref']['box'], f['image_box']
                inside = c[0] <= box[0] and c[1] <= box[1] and c[2] >= box[2] and c[3] >= box[3]
                if not inside:
                    outside += 1
                    outside_safe += bool(b['association_safe'])
                for o in b['others']:
                    ob = o['image_box'][k]
                    if ob is not None and overlap(c, ob) > overlap(c, box):
                        other_more += 1
                        other_more_safe += bool(b['association_safe'])
    res = gate.check(bundles, st, M)
    print('RESULT %s: A frames %d; own box not inside crop %d (in safe bundles %d); crop holds more of '
          'the other person %d (in safe bundles %d); gate verdict %s, aligned failures %d' % (
              name, n, outside, outside_safe, other_more, other_more_safe, res['verdict'],
              len(res['clauses']['aligned']['failures'])))
