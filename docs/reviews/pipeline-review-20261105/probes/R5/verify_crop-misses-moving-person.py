"""Verify: how much of each frame's own person box lies inside that frame's crop_ref, in the
tool's own crossing fixture, for bundles assembled every 0.5 s over 2..12 s.

Prints the distribution of 'share of own box area inside the crop' (so 1 px overhangs are not
counted as misses), the number of frames where under half of the person is in the crop, and the
number where the crop holds more of the other person's box than of this person's.
Also reports a standing person (missing_depth fixture: A at the counter) as the control.

How to run
  python3 verify_crop-misses-moving-person.py <root of the branch checkout>
Synthetic data only; no network.
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


def inter(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


for name, scene, tids in (('crossing', synth.crossing(), ('A', 'B')), ('standing (missing_depth)', synth.missing_depth(), ('A',))):
    st = records.Store(scene['observations'], scene['tracks'])
    shares, other_more, under_half, bundles = [], 0, 0, []
    for i in range(0, 21):
        t1 = synth.ts(2.0 + 0.5 * i)
        for tid in tids:
            b = assemble(st, M, tid, t1)
            bundles.append(b)
            for k, f in enumerate(b['frames']):
                if f['image_box'] is None or f['crop_ref'] is None:
                    continue
                c, box = f['crop_ref']['box'], f['image_box']
                s = inter(c, box) / float(area(box))
                shares.append(s)
                if s < 0.5:
                    under_half += 1
                for o in b['others']:
                    ob = o['image_box'][k]
                    if ob is not None and inter(c, ob) > inter(c, box):
                        other_more += 1
    shares.sort()
    n = len(shares)
    q = lambda p: shares[min(n - 1, int(p * n))]  # noqa: E731
    res = gate.check(bundles, st, M)
    print('RESULT %s: %d frames; share of own box inside crop: min %.2f, p10 %.2f, median %.2f; '
          '%d frames under 50%%; %d frames crop holds more of the other person; gate aligned failures %d' % (
              name, n, shares[0], q(0.1), q(0.5), under_half, other_more, len(res['clauses']['aligned']['failures'])))
