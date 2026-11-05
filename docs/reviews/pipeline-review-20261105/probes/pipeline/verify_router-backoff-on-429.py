"""verify_router-backoff-on-429.py: router v0's refusal and timeout handling, with no sampling noise.

What it checks (independent re-check of findings router-backoff-on-429 and
router-timeout-vs-live-deadline)
  1. Reads main's qwen_live constants (BACKOFF_REFUSALS, BACKOFF_SECONDS, interval) by
     import only; nothing is started.
  2. Drives den-bundle's own Router with FakeClock/FakeSender and a DETERMINISTIC
     refusal pattern (exactly p = 0.1 and p = 0.3 of attempts refused, evenly spread),
     one and two tracks, and reports answered requests per minute and each person's
     mean and max refresh gap. The live pump's figure under the same pattern is
     15 x (1 - p) answered per minute (one attempt per 4 s tick, no back-off on a 429:
     qwen_live.tick only backs off for BACKOFF_REFUSALS).
  3. A silent (lost) reply: next send after the lost request, with the proposed
     timeout_s (20 s) and with a timeout closed at the live bound (8 s deadline + 3 s
     response grace = 11 s), to separate the timeout value from the 30 s back-off.
  Prints RESULT lines.

How to run
  python3 -I verify_router-backoff-on-429.py <claude/rung-5-bundle root> <origin/main root>

Synthetic data only; nothing is written; no network.
"""
import copy
import os
import sys


def main():
    bundle_root, main_root = map(os.path.abspath, sys.argv[1:3])
    sys.path.insert(0, os.path.join(main_root, 'stack', 'services', 'perception-lab'))
    sys.path.insert(0, os.path.join(bundle_root, 'tools', 'den-bundle'))
    import qwen_live as live
    import fakes
    import method
    import records
    import router
    import synth

    print('RESULT main qwen_live BACKOFF_REFUSALS=%s BACKOFF_SECONDS=%s interval=%s MAX_DEADLINE_SECONDS=%s'
          % (sorted(live.BACKOFF_REFUSALS), live.BACKOFF_SECONDS, live.DEFAULTS['interval_seconds'],
             live.MAX_DEADLINE_SECONDS))
    m, rule = method.load_method(), method.load_router()

    def store_for(n, horizon):
        obs, tracks = [], []
        for t in synth.steps(0.0, horizon, 0.5):
            synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
            if n == 2:
                synth.seen(obs, tracks, 'B', t, 3.2, 2.6, standing=False)
        return records.Store(obs, tracks)

    def route(store, rule_, script, until, S=1.25):
        clock = fakes.FakeClock(synth.T0 + 3.0)
        sender = fakes.FakeSender(clock, latency_s=S, script=script)
        r = router.Router(store, m, rule_, clock, sender)
        return [x for x in router.run(r, clock, sender, synth.T0 + until) if x['outcome'] != 'no_box']

    horizon = 1200.0
    for p, refused_slots in ((0.1, {9}), (0.3, {2, 5, 8})):
        script = {i: 'refused' for i in range(1, 3000) if (i - 1) % 10 in refused_slots}
        for n in (1, 2):
            recs = route(store_for(n, horizon + 10), rule, script, horizon)
            oks = [x for x in recs if x['outcome'] == 'ok']
            refused = [x for x in recs if x['outcome'] == 'refused']
            gaps = []
            for tid in ('A', 'B')[:n]:
                ts_ = [x['done_ts'] for x in oks if x['track_id'] == tid]
                gaps += [b - a for a, b in zip(ts_, ts_[1:])]
            print('RESULT router v0 p=%.1f (realised %.3f) tracks=%d: %.2f answered/min; per-person refresh mean %.1f s, max %.1f s'
                  % (p, len(refused) / float(len(recs)), n, len(oks) / (horizon - 3) * 60,
                     sum(gaps) / len(gaps), max(gaps)))
        print('RESULT live pump p=%.1f (same pattern, one attempt per 4 s, no back-off on 429): %.2f answered/min'
              % (p, 15 * (1 - p)))

    for t_out in (None, 11):
        rule_ = rule
        if t_out is not None:
            block = copy.deepcopy(rule.block)
            block['timeout_s'] = t_out
            rule_ = method.router_from(block, 'ROUTER-V0 (timeout closed at %s s)' % t_out)
        recs = route(store_for(1, 150.0), rule_, {1: 'silent'}, 140.0)
        print('RESULT lost reply, timeout_s=%s: outcome %s at +%.1f s; next send %.1f s after the lost request'
              % (rule_['timeout_s'], recs[0]['outcome'], recs[0]['done_ts'] - recs[0]['sent_ts'],
                 recs[1]['sent_ts'] - recs[0]['sent_ts']))


if __name__ == '__main__':
    main()
