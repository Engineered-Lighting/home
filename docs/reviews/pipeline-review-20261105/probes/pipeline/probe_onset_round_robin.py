"""probe_onset_round_robin.py: what router v0's own schedule does to the onset median.

What it checks
  The ladder gates engagement onset at <= 3 s median (N2, rung 6), timed from the
  first touch to the first output that leaves 'none' (owner choice 3), on one-person
  (N1) and two-person (rung 4) sessions, and rung 5's checkpoint re-runs N2's gate
  "one track and then two" under router v0's round-robin.
  This probe drives den-bundle's own Router (ROUTER-V0 block, METHOD-V1 proposed
  values), FakeClock and FakeSender (each answer S seconds after its send) over a
  synthetic 5 Hz observation stream (R6's detector ran at about 4.6-4.9 Hz), for one
  track and for two tracks. For touches at uniformly spread times it measures the
  delay to the first 'ok' answer for that person whose bundle holds a frame captured
  at or after the touch. This is the most favourable case: the first reply that sees
  the touch already reads it (m = 0), K7 adds nothing, and the frame alone shows it.
  Replay view: frames as den-bundle assembles them (dated by capture_ts).
  Live view: the same schedule, but a frame counts only once the detector result is
  out, L_det after capture (R5: about 1.46 s capture to completion; R6: about
  0.28-0.46 s camera to GPU, before inference).
  Prints the median and p90 onset, the share over the 3 s target, and the share over
  the owner's detection grace (6 s one person, about 10 s two), with PASS/FAIL lines
  against the 3 s median.

How to run
  python3 -I probe_onset_round_robin.py <root of the claude/rung-5-bundle checkout>

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

    m = method.load_method()
    rule = method.load_router()
    print('RESULT router %s sha %s: min_interval_s=%s max_per_minute=%s backoff_s=%s order=%s timeout_s=%s(open=%s)' % (
        rule.version, rule.sha256[:12], rule['min_interval_s'], rule['max_per_minute'], rule['backoff_s'],
        rule['order'], rule['timeout_s'], 'timeout_s' in rule.open))

    horizon = 400.0

    def scene(n_tracks):
        obs, tracks = [], []
        for t in synth.steps(0.0, horizon, 0.2):
            synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
            if n_tracks == 2:
                synth.seen(obs, tracks, 'B', t, 3.2, 2.6, standing=False)
        return records.Store(obs, tracks)

    def schedule(store, S):
        clock = fakes.FakeClock(synth.T0 + 3.0)
        sender = fakes.FakeSender(clock, latency_s=S)
        r = router.Router(store, m, rule, clock, sender)
        recs = router.run(r, clock, sender, synth.T0 + horizon)
        bundles = {b['bundle_id']: b for b in r.bundles}
        out = {}
        for rec in recs:
            if rec['outcome'] != 'ok':
                continue
            b = bundles[rec['bundle_id']]
            frame_ts = sorted(f['ts'] for f in b['frames'] if f['image_box'] is not None)
            out.setdefault(rec['track_id'], []).append((rec['sent_ts'], rec['done_ts'], frame_ts))
        periods = sorted({rec['per_person_period_s'] for rec in recs if rec['outcome'] == 'ok'})
        return out, periods

    def onsets(sched, tid, l_det):
        rows = sched[tid]
        res = []
        tau = synth.T0 + 30.0
        while tau < synth.T0 + horizon - 30.0:
            best = None
            for sent, done, frame_ts in rows:
                usable = [f for f in frame_ts if f <= sent - l_det + 1e-9] if l_det else frame_ts
                if usable and usable[-1] >= tau - 1e-9:
                    best = done - tau
                    break
            res.append(best if best is not None else float('inf'))
            tau += 0.037   # off-grid step so touches fall at every phase
        return res

    def q(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    failures = 0
    for n_tracks, grace in ((1, 6.0), (2, 10.0)):
        store = scene(n_tracks)
        for S in (1.25, 0.54):
            sched, periods = schedule(store, S)
            for l_det, why in ((0.0, 'N2: N1 boxes, no detector'), (0.37, 'R6 camera-to-GPU mid'), (1.46, 'R5 capture-to-completion')):
                d = onsets(sched, 'A', l_det)
                med = statistics.median(d)
                over3 = sum(1 for x in d if x > 3.0) / len(d)
                overg = sum(1 for x in d if x > grace) / len(d)
                line = ('tracks=%d per_person_period_s=%s S=%.2f L_det=%.2f (%s): onset median %.2f s, p90 %.2f s, '
                        '>3 s %.0f%%, > grace %.0f s %.0f%%' % (n_tracks, periods, S, l_det, why, med, q(d, 0.9),
                                                               100 * over3, grace, 100 * overg))
                verdict = 'PASS' if med <= 3.0 else 'FAIL'
                if verdict == 'FAIL':
                    failures += 1
                print('%s %s' % (verdict, line))
    # The arithmetic behind it: wait to the person's next start is uniform on [0, P*n),
    # so median = P*n/2 + frame spacing/2 + L_det + S.
    for n in (1, 2):
        for S in (1.25, 0.54):
            print('RESULT analytic tracks=%d: %d*4/2 + 0.1 + S(%.2f) = %.2f s (+ L_det live)' % (n, n, S, n * 2 + 0.1 + S))
    print('RESULT %d of 12 cases miss the 3 s onset median on schedule alone' % failures)


if __name__ == '__main__':
    main()
