"""verify_spec_surviving-gate-mutants: shows that the two surviving gate
mutants (silent_swaps > 1, idf1_gate - 0.1) flip E2's verdict on realistic
synthetic link sets, i.e. they are not equivalent mutants.

Case 1 (door hand-over): 20 windows; in one, track 1 holds A, A leaves, and
track 1 then takes B confidently (one silent switch, no reverse switch).
Case 2: 20 clean windows whose pooled IDF1 is 0.85 (between 0.8 and 0.9).

For each case: gate verdict with the checkout's scorer_core.py, and with the
mutated source loaded from a temp copy.  Synthetic data only.

Run:  python3 verify_spec_surviving-gate-mutants.py <root of the rung-3-track checkout>
"""
import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mutated(old, new, name):
    tmp = tempfile.mkdtemp(prefix='r3spec_m_')
    src = open(os.path.join(TOOL, 'scorer_core.py'), encoding='utf-8').read()
    assert src.count(old) == 1, old
    p = os.path.join(tmp, 'scorer_core.py')
    open(p, 'w', encoding='utf-8').write(src.replace(old, new))
    try:
        return load(p, name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_door():
    links, windows, k = [], [], 0
    for w in range(20):
        start = float(k)
        for f in range(6):
            if w == 7 and f >= 3:
                links.append((float(k), 'o%d' % k, 'B', 1, True))     # track 1 now holds B, confidently
            else:
                links.append((float(k), 'o%d' % k, 'A', 1, True))
            k += 1
        windows.append({'id': 'w%d' % w, 'kind': 'entry_exit', 'start': start, 'end': float(k - 1)})
        k += 2
    return [('take', windows, links)]


def case_idf1():
    links, windows, k = [], [], 0
    for w in range(20):
        start = float(k)
        for f in range(20):
            # 17 of 20 linked to the right track, 3 missed: IDF1 = 34 / 37 ... tune to ~0.85
            t = 1 if f < 15 else None
            links.append((float(k), 'o%d' % k, 'A', t, True if t else None))
            k += 1
        windows.append({'id': 'w%d' % w, 'kind': 'crossing', 'start': start, 'end': float(k - 1)})
        k += 2
    return [('take', windows, links)]


def verdict(mod, takes):
    res = mod.pool([(tid, mod.score_take(links, windows)) for tid, windows, links in takes], 0.9, 20)
    return res['gate']['passed'], res['silent_swaps'], res['pooled']['idf1']


def main():
    orig = load(os.path.join(TOOL, 'scorer_core.py'), 'sc_orig')
    m5 = mutated("failed = sum(1 for r in rows if r['silent_swaps'] > 0)",
                 "failed = sum(1 for r in rows if r['silent_swaps'] > 1)", 'sc_m5')
    m3 = mutated("'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate,",
                 "'idf1_met': pooled_idf1 is not None and pooled_idf1 >= idf1_gate - 0.1,", 'sc_m3')
    door = case_door()
    a, b = verdict(orig, door), verdict(m5, door)
    print('door case original: passed=%s silent_swaps=%d idf1=%.3f' % a)
    print('door case M5      : passed=%s silent_swaps=%d idf1=%.3f' % b)
    print('RESULT M5 flips the verdict: %s' % (a[0] != b[0]))
    low = case_idf1()
    a, b = verdict(orig, low), verdict(m3, low)
    print('idf1 case original: passed=%s silent_swaps=%d idf1=%.3f' % a)
    print('idf1 case M3      : passed=%s silent_swaps=%d idf1=%.3f' % b)
    print('RESULT M3 flips the verdict: %s' % (a[0] != b[0]))


if __name__ == '__main__':
    main()
