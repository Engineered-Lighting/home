#!/usr/bin/env python3
"""verify_tiebreak_backdated-track-lines_gate.py: does rung 5's line dating change its own gate's crossing clause?

What it checks
  Runs router v0 twice over each of rung 3's synthetic takes (den-track's real
  tracker output: 8 scenes x 3 seeds, typical noise, 4 fps), with the same
  20 Hz polling loop and the same fake sender:
    replay - the Store holds every record (what `den_bundle.py route` does);
    causal - before each tick the Store holds only records that exist by now
             (track lines whose rung-3 `capture_ts` <= now, observations
             captured by now), as a live router would.
  Then runs gate.check on each bundle set with crossings made from the scene's
  annotated windows (track_ids = every track with a line written inside the
  window) and compares the `crossing` clause: a take where the causal run has
  crossing failures and the replay has fewer is a take where the replay gate
  passes bundles that a live router would have sent unsafe-but-marked-safe.
  rung 3's synthetic camera 'synth-cam' is given den-oak's frame size in memory.

How to run
  python3 verify_tiebreak_backdated-track-lines_gate.py <rung-5-bundle checkout> [<rung-3-track checkout>]

Synthetic data only, temp dirs only, no network.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    r5 = os.path.abspath(sys.argv[1])
    r3 = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(r5), 'rung-3-track')
    tmp = tempfile.mkdtemp(prefix='r5-tiebreak-gate-')
    gen = os.path.join(HERE, 'verify_tiebreak_backdated-track-lines.py')
    p = subprocess.run([sys.executable, '-B', gen, '--gen', r3, tmp], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    if p.returncode:
        print('RESULT ERROR\n' + p.stderr)
        return 2
    takes = json.loads(p.stdout.strip().splitlines()[-1])
    sys.path.insert(0, os.path.join(r5, 'tools', 'den-bundle'))
    import fakes
    import gate
    import method
    import records
    import router

    M, R = method.load_method(), method.load_router()
    M.values['frame_px'] = dict(M.values['frame_px'], **{'synth-cam': [1920, 1080]})
    tot = {'replay': [0, 0], 'causal': [0, 0], 'routed': 0, 'routed_fut': 0, 'fut_pos': 0, 'max_ahead': 0.0}
    hidden_takes = []
    for d in takes:
        obs = records.read_jsonl(os.path.join(d, 'obs.jsonl'))
        recs = records.read_jsonl(os.path.join(d, 'tracks.jsonl'))
        meta = json.load(open(os.path.join(d, 'events.json')))
        tf = meta['time_field']
        full = records.Store(obs, recs)
        start, end = meta['frames'][0] + 0.01, meta['frames'][-1]
        crossings = []
        for e in meta['events']:
            tids = sorted({r['track_id'] for r in recs if e['start'] - 1e-9 <= r[tf] <= e['end'] + 1e-9})
            crossings.append({'crossing_id': e['id'], 'track_ids': tids, 'interval': [e['start'], e['end']]})
        res = {}
        for mode in ('replay', 'causal'):
            clock = fakes.FakeClock(start)
            sender = fakes.FakeSender(clock)
            rt = router.Router(full, M, R, clock, sender)
            n_seen = -1
            t = start
            while t <= end + 1e-9:
                clock.set(t)
                if mode == 'causal':
                    c_recs = [r for r in recs if r[tf] <= t + 1e-9]
                    if len(c_recs) != n_seen:
                        n_seen = len(c_recs)
                        rt.store = records.Store([o for o in obs if o['capture_ts'] <= t + 1e-9], c_recs)
                for rid, outcome in sender.due(t):
                    rt.on_result(rid, outcome)
                rt.tick()
                t = round(t + 0.05, 6)
            if mode == 'replay':
                for b in rt.bundles:
                    t0b, t1b = b['interval']
                    fut = [l for l in full.lines_of(b['track_id'], t1b) if l.rec[tf] > t1b + 1e-9
                           and t0b - 1e-6 <= l.ts <= t1b + 1e-6 and isinstance(l.rec.get('position'), dict)]
                    tot['routed'] += 1
                    if fut:
                        tot['routed_fut'] += 1
                        tot['fut_pos'] += len(fut)
                        tot['max_ahead'] = max(tot['max_ahead'], max(l.rec[tf] - t1b for l in fut))
            g = gate.check(rt.bundles, full, M, crossings)
            c = g['clauses']['crossing']
            res[mode] = (c['checked'], len(c['failures']), c['failures'][:2])
            tot[mode][0] += c['checked']
            tot[mode][1] += len(c['failures'])
        name = os.path.basename(d)
        if res['causal'][1] > res['replay'][1]:
            hidden_takes.append((name, res['replay'][:2], res['causal'][:2], res['causal'][2]))
    print('INFO crossing clause over %d takes: replay checked %d, failures %d; causal checked %d, failures %d'
          % (len(takes), tot['replay'][0], tot['replay'][1], tot['causal'][0], tot['causal'][1]))
    print('INFO replay routed bundles: %d; holding positions from lines written after the bundle end: %d (%d positions, up to %.2f s ahead)'
          % (tot['routed'], tot['routed_fut'], tot['fut_pos'], tot['max_ahead']))
    for h in hidden_takes:
        print('INFO take %s: replay (checked, failures) %s; causal %s; e.g. %s' % h)
    print('RESULT %s: %d takes where the replay gate shows fewer crossing failures than a causal run'
          % ('FAIL' if hidden_takes else 'PASS', len(hidden_takes)))
    return 1 if hidden_takes else 0


if __name__ == '__main__':
    sys.exit(main())
