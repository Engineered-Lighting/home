"""probe_atk_silent_swap_outside_windows: is a confident identity switch that
happens between annotated windows part of the gate?

Synthetic take, two people A and B on tracks T1 and T2, confidence 0.99 on
every record. Windows W1 [0, 10] and W2 [30, 40]. At t = 20 s (between the
windows) the tracker swaps them silently: from then on T1 carries B and T2
carries A.

  run 1  the owner's labels cover only the windows (links outside are None);
  run 2  the SAME tracker output, with the owner's labels covering the whole take;
  run 3  labels in the windows only, and the tracker's confidence falls to 0.6 at
         t = 25 s for an unrelated reason (it never rises again): the swap was
         made at 0.99, but the scorer first sees it at t = 30 s and judges it by
         the confidence there.

The ladder's gate is "0 silent swaps, meaning no identity switch while the
tracker reported confidence". Both runs hold the same silent swap, so both
should fail. The probe prints each verdict.

Run:  python3 -B probe_atk_silent_swap_outside_windows.py <branch checkout root>
The tool folder is copied to a temp dir first. Synthetic data only.
"""
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True


def load(root):
    tmp = tempfile.mkdtemp(prefix='r3atk-')
    shutil.copytree(os.path.join(root, 'tools', 'den-track'), os.path.join(tmp, 'den-track'))
    sys.path.insert(0, os.path.join(tmp, 'den-track'))
    import scorer_core
    return scorer_core


def take(labelled_everywhere, windows, decay_at=None):
    links = []
    for f in range(0, 41 * 4):          # 0 .. 40.75 s at 4 fps
        t = f * 0.25
        inside = any(w['start'] <= t <= w['end'] for w in windows)
        swapped = t >= 20.0
        for person in ('A', 'B'):
            track = {('A', False): 'T1', ('B', False): 'T2', ('A', True): 'T2', ('B', True): 'T1'}[(person, swapped)]
            label = person if (labelled_everywhere or inside) else None
            conf = 0.6 if (decay_at is not None and t >= decay_at) else 0.99
            links.append((t, 'o-%04d-%s' % (f, person), label, track, conf))
    return links


def main(root):
    sc = load(root)
    windows = [{'id': 'W1', 'kind': 'crossing', 'start': 0.0, 'end': 10.0},
               {'id': 'W2', 'kind': 'crossing', 'start': 30.0, 'end': 40.0}]
    out = {}
    for name, everywhere, decay in (('labels in windows only', False, None),
                                    ('labels over the whole take', True, None),
                                    ('windows only, conf 0.6 from 25 s', False, 25.0)):
        res = sc.score_windows(take(everywhere, windows, decay), windows, 0.9, 0.9)
        out[name] = res
        print('%-28s silent_swaps_in_windows=%d silent_switches_outside_windows=%d pooled_idf1=%.3f passed=%s'
              % (name, res['silent_swaps'], res['silent_switches_outside_windows'],
                 res['pooled']['idf1'], res['gate']['passed']))
    a = out['labels in windows only']['gate']['passed']
    b = out['labels over the whole take']['gate']['passed']
    c = out['windows only, conf 0.6 from 25 s']['gate']['passed']
    bad = 0
    if (not a) and b:
        print('FAIL run 2: the same silent swap fails the gate with sparse labels and PASSES it once the owner '
              'labels more of the take (silent switches outside windows are reported but not gated)')
        bad += 1
    if c:
        print('FAIL run 3: a swap made at confidence 0.99 is scored as flagged because the scorer reads the '
              'confidence of the first labelled link after it (0.6), and the gate passes')
        bad += 1
    print('RESULT: %s (verdicts: %s / %s / %s)' % ('FAIL' if bad else 'PASS', a, b, c))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_silent_swap_outside_windows.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))
