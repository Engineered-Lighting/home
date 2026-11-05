"""probe_atk_confidence_fail_open: what does the scorer do with a swap whose
confidence is missing (None), NaN, or judged against a bad threshold?

20 synthetic crossing windows, A on T1 and B on T2. In window c05 the tracker
swaps the two people for the last 2 of its 41 frames (so pooled IDF1 stays
above 0.9). The swap is made while the tracker was confident: the reference
run gives every record confidence 0.99 and confident_at 0.9, and must FAIL.

Then the same links are scored with
  - confidence None on every record (the Track record in schemas.json has no
    numeric confidence field; an adapter that finds none passes None),
  - confidence NaN,
  - confident_at NaN, and confident_at 9 (a typo for 0.9).
Each of these should refuse or fail; the probe prints what it does.

Run:  python3 -B probe_atk_confidence_fail_open.py <branch checkout root>
The tool folder is copied to a temp dir first. Synthetic data only.
"""
import math
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


def links_and_windows(conf):
    links, windows = [], []
    for k in range(20):
        t0 = 100.0 * k
        windows.append({'id': 'c%02d' % k, 'kind': 'crossing', 'start': t0, 'end': t0 + 10.0})
        for f in range(41):
            t = t0 + f * 0.25
            swapped = (k == 5 and f >= 39)
            for person in ('A', 'B'):
                track = ('T2' if person == 'A' else 'T1') if swapped else ('T1' if person == 'A' else 'T2')
                links.append((t, 'o-%02d-%02d-%s' % (k, f, person), person, track, conf))
        # the swap is undone between windows (both tracks go back), so later windows are clean
    return links, windows


def run(sc, name, conf, confident_at):
    links, windows = links_and_windows(conf)
    try:
        res = sc.score_windows(links, windows, confident_at, 0.9)
    except Exception as e:  # a refusal would land here
        print('%-34s REFUSED: %s: %s' % (name, type(e).__name__, e))
        return None
    print('%-34s switches=%d silent=%d flagged=%d pooled_idf1=%.4f passed=%s' % (
        name, sum(w['switches'] for w in res['windows']), res['silent_swaps'], res['flagged_switches'],
        res['pooled']['idf1'], res['gate']['passed']))
    return res['gate']['passed']


def main(root):
    sc = load(root)
    ref = run(sc, 'reference conf=0.99 at=0.9', 0.99, 0.9)
    if ref is not False:
        print('RESULT: setup error, the reference run should fail')
        return 2
    bad = 0
    for name, conf, at in (('conf=None at=0.9', None, 0.9),
                           ('conf=NaN at=0.9', math.nan, 0.9),
                           ('conf=0.99 at=NaN', 0.99, math.nan),
                           ('conf=0.99 at=9 (typo)', 0.99, 9)):
        if run(sc, name, conf, at):
            print('FAIL %s: the same confident swap now passes the gate' % name)
            bad += 1
    print('RESULT: %d of 4 variants turn a silent swap into a gate pass' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_confidence_fail_open.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))
