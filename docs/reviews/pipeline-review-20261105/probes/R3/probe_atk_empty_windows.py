"""probe_atk_empty_windows: do crossing windows with no data count as clean trials?

Checks scorer_core.score_windows (tools/den-track) with synthetic links only:
  case 1  30 annotated windows, tracker/observation data present for only 3 of
          them (a truncated track file, or windows past the end of the take);
  case 2  30 windows, all with observations, but only 3 labelled by the owner;
  case 3  30 windows where only ONE person is ever labelled (no crossing at all).
A sound gate would refuse, or count only windows that hold labelled data of
two people. The probe prints the gate verdict, the reported sample size and
the reported one-sided 95% bound for each case.

Run:  python3 -B probe_atk_empty_windows.py <branch checkout root>
      e.g. .../scratchpad/wt/rung-3-track
The tool folder is copied to a temp dir first, so nothing is written to the
checkout. Synthetic data only.
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


def window(k):
    return {'id': 'c%02d' % k, 'kind': 'crossing', 'start': 100.0 * k, 'end': 100.0 * k + 10.0}


def crossing_links(k, labelled=True, people=('A', 'B')):
    """Perfect tracking through window k, 4 fps, confidence 0.99."""
    out = []
    for f in range(41):
        t = 100.0 * k + f * 0.25
        for p, tr in zip(people, ('T1', 'T2')):
            out.append((t, 'o-%d-%d-%s' % (k, f, p), p if labelled else None, tr, 0.99))
    return out


def report(name, res):
    g = res['gate']
    with_data = sum(1 for w in res['windows'] if w['observations'] > 0)
    labelled = sum(1 for w in res['windows'] if (w['idtp'] + w['idfn']) > 0)
    print('%s: crossings=%d windows_with_any_observation=%d windows_with_labels=%d '
          'pooled_idf1=%s upper95=%.4f passed=%s' % (
              name, res['crossings'], with_data, labelled, res['pooled']['idf1'],
              res['silent_swap_rate_upper_95'], g['passed']))
    return g['passed'], res['crossings'], labelled


def main(root):
    sc = load(root)
    windows = [window(k) for k in range(30)]
    bad = 0

    # case 1: data for only 3 of 30 windows
    links = [l for k in range(3) for l in crossing_links(k)]
    passed, n, lab = report('case1 truncated data', sc.score_windows(links, windows, 0.9, 0.9))
    if passed and n == 30 and lab == 3:
        print('FAIL case1: gate passed and reports n=30 (bound 0.095) although only 3 windows held any data')
        bad += 1
    else:
        print('PASS case1')

    # case 2: observations everywhere, labels in 3 windows only
    links = [l for k in range(30) for l in crossing_links(k, labelled=(k < 3))]
    passed, n, lab = report('case2 unlabelled windows', sc.score_windows(links, windows, 0.9, 0.9))
    if passed and n == 30 and lab == 3:
        print('FAIL case2: 27 unlabelled windows counted as clean crossings')
        bad += 1
    else:
        print('PASS case2')

    # case 3: one person only, never a crossing
    links = [l for k in range(30) for l in crossing_links(k, people=('A',))]
    passed, n, lab = report('case3 single person', sc.score_windows(links, windows, 0.9, 0.9))
    if passed and n == 30:
        print('FAIL case3: 30 one-person windows counted as 30 crossing trials')
        bad += 1
    else:
        print('PASS case3')

    # case 4: a single window is enough to pass (no floor on n; ladder tests 20-30)
    links = crossing_links(0)
    res = sc.score_windows(links, windows[:1], 0.9, 0.9)
    report('case4 one window', res)
    if res['gate']['passed']:
        print('FAIL case4: gate passed on 1 crossing (ladder: 20-30 staged crossings); bound %.3f'
              % res['silent_swap_rate_upper_95'])
        bad += 1
    else:
        print('PASS case4')
    print('RESULT: %d of 4 cases give a pass the data does not support' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_empty_windows.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))
