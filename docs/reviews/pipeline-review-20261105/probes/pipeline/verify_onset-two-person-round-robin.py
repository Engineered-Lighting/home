"""verify_onset-two-person-round-robin.py: the onset floor that router v0's schedule alone sets.

What it checks (independent re-check of finding onset-two-person-round-robin)
  Drives den-bundle's own Router (ROUTER-V0 block as published) with FakeClock and a
  FakeSender that answers instantly-ish (latency S), over one and two always-visible
  synthetic tracks, and reads each person's actual start times. For touches spread
  uniformly over time it computes the most favourable onset: the wait from the touch
  to that person's next start, plus S (no detector latency, the first reply already
  reads the touch, no dwell). Prints per-person start spacing and the onset median
  for S = 0 (schedule alone) and S = 0.54 s (R3's fastest offline receipt), with
  PASS/FAIL against the 3 s median (owner choice 3).

How to run
  python3 -I verify_onset-two-person-round-robin.py <claude/rung-5-bundle root>

Synthetic data only; nothing is written; no network.
"""
import os
import statistics
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.path.insert(0, os.path.join(root, 'tools', 'den-bundle'))
    import fakes
    import method
    import records
    import router
    import synth

    m, rule = method.load_method(), method.load_router()
    horizon = 160.0
    for n in (1, 2):
        obs, tracks = [], []
        for t in synth.steps(0.0, horizon, 0.25):
            synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
            if n == 2:
                synth.seen(obs, tracks, 'B', t, 3.2, 2.6, standing=False)
        store = records.Store(obs, tracks)
        for S in (0.0001, 0.54):
            clock = fakes.FakeClock(synth.T0 + 3.0)
            sender = fakes.FakeSender(clock, latency_s=S)
            r = router.Router(store, m, rule, clock, sender)
            recs = [x for x in router.run(r, clock, sender, synth.T0 + horizon) if x['outcome'] == 'ok']
            starts = sorted(x['sent_ts'] for x in recs if x['track_id'] == 'A')
            spacing = sorted({round(b - a, 3) for a, b in zip(starts, starts[1:])})
            onsets, tau = [], starts[0] + 0.001
            while tau < starts[-1] - 0.001:
                nxt = next(s for s in starts if s >= tau)
                onsets.append(nxt - tau + S)
                tau += 0.0371
            med = statistics.median(onsets)
            print('%s tracks=%d S=%.2f: A start spacing %s s; best-case onset median %.2f s (target <= 3 s)'
                  % ('PASS' if med <= 3.0 else 'FAIL', n, S, spacing, med))


if __name__ == '__main__':
    main()
