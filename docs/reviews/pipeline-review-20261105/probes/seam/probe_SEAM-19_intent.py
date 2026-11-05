"""probe_SEAM-19_intent.py: how far den-bundle's Store misdates den-track's
track lines that name no observation, and what that changes downstream.

Verifier probe (intent/spec lens) for finding SEAM-19. Synthetic data only.

    python3 -B probe_SEAM-19_intent.py <rung-3-track root> <rung-5-bundle root> [observations.jsonl]

With no observations file it makes one with den-track's own `synth` (scene
`occlusion`-like scenes, which coast). The observations are copied into a temp
session folder; den-track's real `track --session` writes tracks.jsonl there.
Then den-bundle's real records.Store dates every line, and the probe compares
that date with den-track's own `capture_ts` for the same line, by state.
Downstream: router v0 replayed (fake clock and sender) with the Store as is,
and with a Store that dates lines by capture_ts; the probe counts requests
that differ, bundle positions whose ts is not their line's capture_ts, and
bundles whose association_safe differs.
"""
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True


def main():
    track_root, bundle_root = sys.argv[1], sys.argv[2]
    obs_src = sys.argv[3] if len(sys.argv) > 3 else None
    den_track = os.path.join(track_root, 'tools', 'den-track', 'den_track.py')
    with tempfile.TemporaryDirectory(prefix='seam19-') as tmp:
        session = os.path.join(tmp, 'session')
        take = os.path.join(session, 'evening')
        os.makedirs(take)
        if obs_src:
            shutil.copyfile(obs_src, os.path.join(take, 'observations.jsonl'))
        else:
            synth_out = os.path.join(tmp, 'synth')
            p = subprocess.run([sys.executable, '-B', den_track, 'synth', '--out', synth_out, '--seeds', '1'],
                               capture_output=True, text=True)
            print('synth rc=%d %s' % (p.returncode, p.stderr.strip()[-200:]))
            takes = sorted(os.listdir(synth_out))
            # pick the first take that has a coasting line after tracking; just track them all
            shutil.rmtree(session)
            shutil.copytree(synth_out, session)
        p = subprocess.run([sys.executable, '-B', den_track, 'track', '--session', session],
                           capture_output=True, text=True)
        print('den-track track rc=%d\n%s' % (p.returncode, p.stdout.strip()[-600:]))
        if p.returncode != 0:
            print(p.stderr[-800:])
            return

        sys.path.insert(0, os.path.join(bundle_root, 'tools', 'den-bundle'))
        import records as brec  # den-bundle's
        import method as bmethod
        import router as brouter
        import fakes as bfakes

        totals = collections.Counter()
        lag_by_state = collections.defaultdict(list)
        diffs = collections.Counter()
        for name in sorted(os.listdir(session)):
            folder = os.path.join(session, name)
            tp = os.path.join(folder, 'tracks.jsonl')
            if not os.path.exists(tp):
                continue
            obs = brec.read_jsonl(os.path.join(folder, 'observations.jsonl'))
            tracks = brec.read_jsonl(tp)
            store = brec.Store(obs, tracks)
            totals['lines'] += len(tracks)
            totals['undated'] += store.undated
            for tid, ls in store.lines.items():
                for l in ls:
                    ct = l.rec.get('capture_ts')
                    lag = ct - l.ts
                    if not l.rec.get('last_obs_ids'):
                        totals['empty_last_obs_ids'] += 1
                    if lag > 1e-6:
                        totals['misdated'] += 1
                        lag_by_state[(l.rec['state'], l.rec['association']['status'])].append(lag)

            # a Store dated by den-track's capture_ts, for comparison
            class CapStore(brec.Store):
                def __init__(self, observations, track_lines):
                    super().__init__(observations, [])
                    for i, rec in enumerate(track_lines):
                        tid = rec['track_id']
                        if tid not in self.lines:
                            self.lines[tid] = []
                            self.order.append(tid)
                        self.lines[tid].append(brec.Line(rec['capture_ts'], rec, i))

            m = bmethod.load_method(bmethod.METHOD_PATH)
            rule = bmethod.load_router(bmethod.ROUTER_PATH)
            t_first = min(o['capture_ts'] for o in obs)
            t_last = max(o['capture_ts'] for o in obs)
            outs = {}
            for label, st in (('store', store), ('capture_ts', CapStore(obs, tracks))):
                clock = bfakes.FakeClock(t_first + 2.5)
                sender = bfakes.FakeSender(clock, latency_s=1.25)
                r = brouter.Router(st, m, rule, clock, sender)
                recs = brouter.run(r, clock, sender, t_last)
                outs[label] = (recs, r.bundles)
            a_recs, a_b = outs['store']
            c_recs, c_b = outs['capture_ts']
            ka = [(x['track_id'], round(x['sent_ts'], 3), x['outcome']) for x in a_recs]
            kc = [(x['track_id'], round(x['sent_ts'], 3), x['outcome']) for x in c_recs]
            diffs['requests_store'] += len(ka)
            diffs['requests_capture_ts'] += len(kc)
            diffs['requests_only_in_one'] += len(set(ka) ^ set(kc))
            # positions stamped earlier than their line's capture_ts
            for b in a_b:
                diffs['positions'] += len(b['positions'])
            # same-request bundles: association_safe / missing differences
            cb = {x['bundle_id']: x for x in c_b}
            for b in a_b:
                o = cb.get(b['bundle_id'])
                if o is None:
                    continue
                diffs['paired_bundles'] += 1
                if o['association_safe'] != b['association_safe']:
                    diffs['association_safe_differs'] += 1
                if o['missing'] != b['missing']:
                    diffs['missing_differs'] += 1
                if [p['ts'] for p in o['positions']] != [p['ts'] for p in b['positions']]:
                    diffs['position_ts_differs'] += 1
                dup = len(b['positions']) - len({p['ts'] for p in b['positions']})
                diffs['duplicate_position_ts'] += dup

        print('RESULT lines=%d undated=%d empty_last_obs_ids=%d misdated(earlier than capture_ts)=%d' % (
            totals['lines'], totals['undated'], totals['empty_last_obs_ids'], totals['misdated']))
        for k, v in sorted(lag_by_state.items()):
            v = sorted(v)
            print('RESULT misdated %-28s n=%4d median lag %.3f s, max lag %.3f s' % (k, len(v), v[len(v) // 2], v[-1]))
        print('RESULT router: %s' % dict(diffs))
        print('RESULT %s' % ('CONFIRMED: Store dates no-observation lines earlier than den-track says they happened'
                             if totals['misdated'] else 'NOT REPRODUCED'))


if __name__ == '__main__':
    main()
