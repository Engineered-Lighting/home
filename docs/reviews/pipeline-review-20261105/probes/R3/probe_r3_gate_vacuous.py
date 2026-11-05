#!/usr/bin/env python3
"""Can den-track's E2 gate (scorer_core.score_windows()['gate']['passed']) pass
vacuously or with a silent swap? Rung 3 gate: 0 silent swaps, IDF1 >= 0.9, on
20-30 owner-annotated crossings, the sample's uncertainty reported.

Cases (all synthetic links (time, obs_id, person, track, confidence)):
  empty_windows       30 annotated windows, 29 hold no observations at all (e.g.
                      windows on another clock than the records): does the gate
                      pass and claim the 30-crossing bound (0.095)?
  one_window          a single crossing: does the gate pass (bound 0.95)?
  outside_window      a confident (silent) swap whose first post-swap link
                      lands after the annotated window end: does the gate pass
                      while reporting silent_switches_outside_windows > 0?
  missing_confidence  a full swap where the link carries no confidence (None,
                      e.g. a record field the link builder did not find): is it
                      silent, and does the gate pass?
  never_confident     the tracker reports confidence 0 on every link and swaps:
                      silent swaps are 0 by construction; does the gate pass
                      though low_confidence_share is 1.0 in every window?
  one_person_window   a 'crossing' window in which only one person is labelled:
                      counted as a crossing?

A line 'FAIL' means the gate passes where the ladder's gate should not.

Run:
    python3 probe_r3_gate_vacuous.py <root of the branch checkout>
"""
import os
import sys

sys.dont_write_bytecode = True
if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
sys.path.insert(0, os.path.join(os.path.abspath(sys.argv[1]), 'tools', 'den-track'))
import scorer_core  # noqa: E402

THR, GATE = 0.9, 0.9
bad = 0


def mk(rows, t0=0.0, conf=0.99):
    return [(t0 + k, 'o%d' % (int(t0 * 10) + k), p, t, conf if t is not None else None)
            for k, (p, t) in enumerate(rows)]


def report(case, res, should_pass, note):
    global bad
    passed = res['gate']['passed']
    ok = passed == should_pass
    bad += 0 if ok else 1
    print('%s %-19s gate.passed=%-5s crossings=%-3d silent_in_windows=%d silent_outside=%d pooled_idf1=%s '
          'upper95=%s  (%s)' % ('PASS' if ok else 'FAIL', case, passed, res['crossings'], res['silent_swaps'],
                                res['silent_switches_outside_windows'],
                                None if res['pooled']['idf1'] is None else round(res['pooled']['idf1'], 3),
                                None if res['silent_swap_rate_upper_95'] is None else round(res['silent_swap_rate_upper_95'], 4),
                                note))


# empty windows inflate n
links = mk([('A', 1), ('B', 2)] * 10)                              # t 0..19
wins = [{'id': 'c0', 'kind': 'crossing', 'start': 0, 'end': 19}]
wins += [{'id': 'c%d' % k, 'kind': 'crossing', 'start': 1000 + 100 * k, 'end': 1050 + 100 * k} for k in range(1, 30)]
res = scorer_core.score_windows(links, wins, THR, GATE)
print('INFO empty_windows: observations per window =', sorted({r['observations'] for r in res['windows']}))
report('empty_windows', res, False, '29 of 30 windows hold no observation, yet each counts as a crossing')

# one window
res = scorer_core.score_windows(mk([('A', 1), ('B', 2)] * 10), [{'id': 'c0', 'start': 0, 'end': 19}], THR, GATE)
report('one_window', res, False, 'ladder tests 20-30 crossings; one crossing passes')

# silent swap just after the window end
links = mk([('A', 1), ('B', 2)] * 5) + mk([('B', 1), ('A', 2)] * 5, t0=10.0)   # swap seen from t=10
res = scorer_core.score_windows(links, [{'id': 'c0', 'start': 0, 'end': 9.5}], THR, GATE)
report('outside_window', res, False, 'silent swap reported outside the window, gate ignores it')

# missing confidence
rows = [('A', 1), ('B', 2)] * 19 + [('B', 1), ('A', 2)]          # confident-looking swap at the end
links = [(float(k), 'o%d' % k, p, t, None) for k, (p, t) in enumerate(rows)]
res = scorer_core.score_windows(links, [{'id': 'c%d' % k, 'start': 2 * k, 'end': 2 * k + 1} for k in range(20)], THR, GATE)
sw = scorer_core.switches(links, THR)
print('INFO missing_confidence: switches =', [(s['track'], s['from'], s['to'], s['confidence'], s['silent']) for s in sw])
report('missing_confidence', res, False, 'a swap with no reported confidence is counted as flagged, not refused')

# never confident
rows = [('A', 1), ('B', 2)] * 19 + [('B', 1), ('A', 2)]
links = mk(rows, conf=0.0)
res = scorer_core.score_windows(links, [{'id': 'c%d' % k, 'start': 2 * k, 'end': 2 * k + 1} for k in range(20)], THR, GATE)
print('INFO never_confident: low_confidence_share per window =', sorted({r['low_confidence_share'] for r in res['windows']}),
      '; gate keys =', sorted(res['gate']))
report('never_confident', res, False, 'confidence 0 everywhere: the silent-swap criterion is vacuous and not guarded')

# one-person window
links = mk([('A', 1)] * 10)
res = scorer_core.score_windows(links, [{'id': 'c%d' % k, 'kind': 'crossing', 'start': k, 'end': k} for k in range(10)], THR, GATE)
report('one_person_window', res, False, 'windows with a single labelled person count as crossings')

print()
print('RESULT: %d case(s) where the gate passes and should not' % bad)
