"""Verifier probe for finding crop-misses-moving-person: how much of each frame's own box is
outside the bundle's single crop, and whether frames where the crop holds more of the other
person sit in association_safe=false bundles.

Run: python3 verify_crop_share.py <root of the rung-5-bundle checkout>
Synthetic fixtures only (the tool's own synth.crossing and a 1 m/s closing-speed variant).
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import method, records, synth  # noqa
from assemble import assemble  # noqa
M = method.load_method()

def area(b): return max(0, b[2]-b[0]) * max(0, b[3]-b[1])
def ov(a, b): return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])

def crossing_at(speed):
    obs, tracks = [], []
    for t in synth.steps(0.0, 12.0, 0.5):
        amb = 4.5 <= t <= 7.0
        status, since = ('ambiguous', 4.5) if amb else ('confident', 7.5 if t >= 7.5 else 0.0)
        xa, xb = 0.5 + speed*t/2.0, 3.5 - speed*t/2.0
        synth.seen(obs, tracks, 'A', t, round(xa, 3), 1.0, status=status, since=since)
        synth.seen(obs, tracks, 'B', t, round(xb, 3), 1.2, status=status, since=since)
    return records.Store(obs, tracks)

for name, st in (('fixture', records.Store(synth.crossing()['observations'], synth.crossing()['tracks'])),
                 ('1 m/s closing', crossing_at(1.0))):
    shares, other_more_safe, other_more_unsafe, half_out_safe = [], 0, 0, 0
    for i in range(21):
        t1 = synth.ts(2.0 + 0.5*i)
        for tid in ('A', 'B'):
            b = assemble(st, M, tid, t1)
            for k, f in enumerate(b['frames']):
                if f['image_box'] is None or f['crop_ref'] is None: continue
                c, box = f['crop_ref']['box'], f['image_box']
                out = 1 - ov(c, box) / float(area(box))
                shares.append(out)
                if out > 0.5 and b['association_safe']: half_out_safe += 1
                for o in b['others']:
                    ob = o['image_box'][k]
                    if ob is not None and ov(c, ob) > ov(c, box):
                        if b['association_safe']: other_more_safe += 1
                        else: other_more_unsafe += 1
    shares.sort()
    n = len(shares)
    print('RESULT %s: %d frames; share of own box outside crop: median %.2f, p90 %.2f, max %.2f; '
          'frames >50%% outside %d (in safe bundles %d); other-dominant frames: safe bundles %d, unsafe %d' % (
          name, n, shares[n//2], shares[int(n*0.9)], shares[-1], sum(1 for s in shares if s > 0.5),
          half_out_safe, other_more_safe, other_more_unsafe))
