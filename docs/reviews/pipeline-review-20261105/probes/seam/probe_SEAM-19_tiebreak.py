"""probe_SEAM-19_tiebreak.py: does den-bundle's track-line dating (last_obs_ids,
else the previous line's date) ever flip a rung-5 flag the UNSAFE way, compared
with dating the same den-track lines by den-track's own capture_ts?

Tiebreak verifier probe for finding SEAM-19. Synthetic data only, temp dir only,
no network. Runs den-track's real `synth` and `track` CLIs, then den-bundle's real
records.Store / assemble / router / gate modules (imported read-only, -B).

    python3 -B probe_SEAM-19_tiebreak.py <rung-3-track root> [<rung-5-bundle root>] [seeds] [noise,noise]

Defaults: rung-5-bundle is a sibling folder, seeds 3, noise clean,typical,noisy.
Set SEAM_TMP to choose where the temp dir is made.

For every take, track, and request time t on a 0.25 s grid it compares
A = den-bundle's Store as written, C = the same lines dated by capture_ts:
  - future leak: A's latest line at t was written by den-track after t;
  - eligibility (router v0): eligible in A but not C (unsafe way) / C but not A;
  - association_safe: True in A, False in C (unsafe way) / converse;
  - missing: a source flagged in C but not in A (unsafe way) / converse;
  - positions: A bundles with two positions at one ts but different x/y.
Then router.run on both datings (request targets that differ) and gate.check
(practice) on both: same verdict means the gate cannot see the dating.
"""
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter

sys.dont_write_bytecode = True
EPS = 1e-6


def main():
    r3 = os.path.abspath(sys.argv[1])
    r5 = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(r3), 'rung-5-bundle')
    seeds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    noises = (sys.argv[4] if len(sys.argv) > 4 else 'clean,typical,noisy').split(',')
    cli = os.path.join(r3, 'tools', 'den-track', 'den_track.py')
    sys.path.insert(0, os.path.join(r5, 'tools', 'den-bundle'))
    import records, router, method, fakes, gate  # den-bundle's own modules
    from assemble import assemble

    class CapStore(records.Store):
        """The same Store, but every line dated by den-track's capture_ts."""
        def __init__(self, observations, track_lines):
            super().__init__(observations, [])
            for i, rec in enumerate(track_lines):
                tid = rec['track_id']
                if tid not in self.lines:
                    self.lines[tid] = []
                    self.order.append(tid)
                self.lines[tid].append(records.Line(rec['capture_ts'], rec, i))

    m = method.load_method(method.METHOD_PATH)
    rule = method.load_router(method.ROUTER_PATH)
    c = Counter()
    examples = []
    with tempfile.TemporaryDirectory(prefix='seam19tb-', dir=os.environ.get('SEAM_TMP')) as tmp:
        for noise in noises:
            sess = os.path.join(tmp, noise)
            subprocess.run([sys.executable, '-B', cli, 'synth', '--out', sess, '--seeds', str(seeds),
                            '--noise', noise], check=True, stdout=subprocess.DEVNULL)
            subprocess.run([sys.executable, '-B', cli, 'track', '--session', sess], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for take in sorted(os.listdir(sess)):
                folder = os.path.join(sess, take)
                if not os.path.isfile(os.path.join(folder, 'tracks.jsonl')):
                    continue
                obs = records.read_jsonl(os.path.join(folder, 'observations.jsonl'))
                for o in obs:  # den-bundle knows frame_px for its configured camera(s) only
                    src = o.setdefault('source', {})
                    if src.get('sensor_id') not in m['frame_px']:
                        src['sensor_id'] = sorted(m['frame_px'])[0]
                trk = records.read_jsonl(os.path.join(folder, 'tracks.jsonl'))
                c['takes'] += 1
                c['lines'] += len(trk)
                c['lines_with_capture_ts'] += sum(isinstance(r.get('capture_ts'), (int, float)) for r in trk)
                A, C = records.Store(obs, trk), CapStore(obs, trk)
                c['undated_A'] += A.undated
                for tid, ls in A.lines.items():
                    for ln in ls:
                        if ln.ts < ln.rec['capture_ts'] - EPS:
                            c['misdated_%s' % ln.rec['state']] += 1
                t_first = min(o['capture_ts'] for o in obs)
                t_last = max(o['capture_ts'] for o in obs)
                clk = fakes.FakeClock(t_first)
                ra = router.Router(A, m, rule, clk, fakes.FakeSender(clk))
                rc = router.Router(C, m, rule, clk, fakes.FakeSender(clk))
                t = t_first + m['window_s']
                while t <= t_last + EPS:
                    ea, ec = set(ra.eligible(t)), set(rc.eligible(t))
                    c['elig_A_not_C(unsafe way)'] += len(ea - ec)
                    c['elig_C_not_A'] += len(ec - ea)
                    for tid in A.order:
                        la, lc = A.latest(tid, t), C.latest(tid, t)
                        if la is None:
                            continue
                        c['grid_track_points'] += 1
                        if la.rec['capture_ts'] > t + EPS:
                            c['future_line_read'] += 1
                            if lc is None or la.rec['state'] != lc.rec['state']:
                                c['future_state_differs'] += 1
                                key = (la.rec['state'], None if lc is None else lc.rec['state'])
                                c['future_state %s(A) vs %s(C)' % key] += 1
                        ba, bc = assemble(A, m, tid, t), assemble(C, m, tid, t)
                        if ba['association_safe'] and not bc['association_safe']:
                            c['assoc_safe_A_true_C_false(unsafe way)'] += 1
                            if len(examples) < 5:
                                examples.append((take, tid, t))
                        if bc['association_safe'] and not ba['association_safe']:
                            c['assoc_safe_C_true_A_false'] += 1
                        if set(bc['missing']) - set(ba['missing']):
                            c['missing_in_C_not_A(unsafe way)'] += 1
                        if set(ba['missing']) - set(bc['missing']):
                            c['missing_in_A_not_C'] += 1
                        seen = {}
                        for p in ba['positions']:
                            if p['ts'] in seen and seen[p['ts']] != (p['x'], p['y']):
                                c['A_bundles_dup_ts_diff_xy'] += 1
                                break
                            seen[p['ts']] = (p['x'], p['y'])
                    t += 0.25
                runs = {}
                for label, st in (('A', A), ('C', C)):
                    clock = fakes.FakeClock(t_first + m['window_s'])
                    sender = fakes.FakeSender(clock, latency_s=1.25)
                    r = router.Router(st, m, rule, clock, sender)
                    recs = router.run(r, clock, sender, t_last)
                    g = gate.check(r.bundles, st, m, requests=recs, rule=rule)
                    runs[label] = ([(x['track_id'], round(x['sent_ts'], 3)) for x in recs], g['verdict'])
                c['requests_A'] += len(runs['A'][0])
                c['requests_C'] += len(runs['C'][0])
                if runs['A'][0] != runs['C'][0]:
                    c['takes_with_different_requests'] += 1
                c['gate_A_%s' % runs['A'][1]] += 1
                c['gate_C_%s' % runs['C'][1]] += 1
                if runs['A'][1] != runs['C'][1]:
                    c['gate_verdict_differs'] += 1
    for k in sorted(c):
        print('RESULT %-44s %d' % (k, c[k]))
    for e in examples:
        print('RESULT example unsafe-way association_safe: take %s track %s t %.3f' % e)
    unsafe = c['elig_A_not_C(unsafe way)'] + c['assoc_safe_A_true_C_false(unsafe way)'] + c['missing_in_C_not_A(unsafe way)']
    print('%s: den-bundle dates no-observation lines earlier than capture_ts (%d lines) and reads future lines (%d points)'
          % ('FAIL' if c['future_line_read'] else 'PASS', sum(v for k, v in c.items() if k.startswith('misdated_')),
             c['future_line_read']))
    print('%s: misdating flips an eligibility/association_safe/missing flag the unsafe way in %d cases'
          % ('FAIL' if unsafe else 'PASS', unsafe))
    print('%s: gate verdict differs between datings in %d takes (0 = gate blind to the dating)'
          % ('INFO', c['gate_verdict_differs']))


if __name__ == '__main__':
    main()
