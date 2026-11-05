"""Probe: one crop per camera (from the latest boxed frame) applied to every frame of the bundle
does not hold the person in earlier frames when they walk, and can hold the other person;
the gate's `aligned` clause only checks the latest frame's box.

What it checks
  The tool's own crossing fixture (A and B walk past each other at ~0.17 m/s each) and a
  faster variant (1.0 m/s, a normal walking pace, x speed 1.0 m/s):
  for every bundle assembled every 0.5 s across 2..12 s, count frames whose own image_box is
  not inside the bundle's crop, and frames where the crop holds more of the other track's
  box than of this track's box. Then runs gate.check on those bundles: PASS of the gate
  alongside non-zero counts shows the clause does not look at earlier frames.

How to run
  python3 probe_crop-misses-moving-person.py <root of the branch checkout>
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


def crossing_at(speed):
    obs, tracks = [], []
    for t in synth.steps(0.0, 12.0, 0.5):
        amb = 4.5 <= t <= 7.0
        status, since = ('ambiguous', 4.5) if amb else ('confident', 7.5 if t >= 7.5 else 0.0)
        xa, xb = 0.5 + speed * t / 2.0, 3.5 - speed * t / 2.0
        synth.seen(obs, tracks, 'A', t, round(xa, 3), 1.0, status=status, since=since)
        synth.seen(obs, tracks, 'B', t, round(xb, 3), 1.2, status=status, since=since)
    return records.Store(obs, tracks)


fails = 0
for name, st in (('fixture crossing', records.Store(synth.crossing()['observations'], synth.crossing()['tracks'])),
                 ('walk 0.5 m/s each', crossing_at(1.0))):
    frames = outside = other_more = 0
    bundles = []
    for i in range(0, 21):
        t1 = synth.ts(2.0 + 0.5 * i)
        for tid in ('A', 'B'):
            b = assemble(st, M, tid, t1)
            bundles.append(b)
            for k, f in enumerate(b['frames']):
                if f['image_box'] is None or f['crop_ref'] is None:
                    continue
                frames += 1
                c, box = f['crop_ref']['box'], f['image_box']
                if not (c[0] <= box[0] and c[1] <= box[1] and c[2] >= box[2] and c[3] >= box[3]):
                    outside += 1
                for o in b['others']:
                    ob = o['image_box'][k]
                    if ob is not None and overlap(c, ob) > overlap(c, box):
                        other_more += 1
    res = gate.check(bundles, st, M)
    aligned = res['clauses']['aligned']
    print('RESULT %s: %d frames; %d not inside their crop (%.0f%%); %d where the crop holds more of the other '
          'person than of this one; gate aligned failures %d' % (
              name, frames, outside, 100.0 * outside / max(frames, 1), other_more, len(aligned['failures'])))
    if outside and not aligned['failures']:
        fails += 1
print('FAIL: crops miss the person in earlier frames and the aligned clause does not notice' if fails else 'PASS')
