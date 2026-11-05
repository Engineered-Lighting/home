#!/usr/bin/env python3
"""verify_tiebreak_backdated-track-lines.py: rung 5's track-line dating on rung 3's own output.

What it checks
  Finding backdated-track-lines says den-bundle dates a Track line that names
  no observation (missed frame, coasting, ended) at the previous line's date,
  so a replay reads lines before they were written. This probe feeds rung 5 the
  output of rung 3's real tracker (den-track pipeline.track_frames on den-track's
  synthetic scenes, all 8 scenes x 3 seeds, 'typical' noise, 4 fps). Rung 3
  writes the time of each record in a `capture_ts` field (its open slot
  record.time_field, proposed 'capture_ts', "Rung 5 and K7 must read the same
  name"); rung 5 never reads that field.

  It reports:
    1. how many rung 3 lines rung 5 dates earlier than rung 3's own capture_ts,
       and by how much (max);
    2. a causal comparison at every frame time t (+10 ms): rung 5 on the whole
       replay versus rung 5 on only the records that exist by t (lines with
       capture_ts <= t, observations captured by t):
       - router eligibility differences,
       - association_safe differences for each track's bundle at t, split into
         replay-false/causal-true (replay hides a safe->unsafe change that live
         would not yet know; inside an annotated window this can turn a gate
         FAIL into a PASS) and replay-true/causal-false,
       - bundle positions taken from lines written after t.

How to run
  python3 verify_tiebreak_backdated-track-lines.py <rung-5-bundle checkout> [<rung-3-track checkout>]
  (the second defaults to the sibling folder rung-3-track)

Synthetic data only, temp dirs only, no network.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True


def gen(r3, out):
    """Child mode: run rung 3's tracker on its synthetic scenes and write JSONL."""
    sys.path.insert(0, os.path.join(r3, 'tools', 'den-track'))
    import method as m3
    import pipeline
    import synth
    M = m3.Method()
    values = dict(M.values, room=synth.ROOM_MODEL)
    takes = []
    for name in sorted(synth.SCENES):
        for seed in range(3):
            sc = synth.scenario(name, seed, 'typical', fps=4.0)
            obs, truth = synth.records(sc)
            frames = pipeline.observation_frames(obs, values['input'])
            recs = pipeline.track_frames(frames, values)
            d = os.path.join(out, '%s-%d' % (name, seed))
            os.makedirs(d)
            for fn, rows in (('obs.jsonl', obs), ('tracks.jsonl', recs)):
                with open(os.path.join(d, fn), 'w', encoding='utf-8', newline='\n') as f:
                    for r in rows:
                        f.write(json.dumps(r) + '\n')
            with open(os.path.join(d, 'events.json'), 'w') as f:
                json.dump({'events': sc['events'], 'frames': [fr['t'] for fr in sc['frames']],
                           'time_field': values['record']['time_field']}, f)
            takes.append(d)
    print(json.dumps(takes))


def main():
    r5 = os.path.abspath(sys.argv[1])
    r3 = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(r5), 'rung-3-track')
    tmp = tempfile.mkdtemp(prefix='r5-tiebreak-')
    p = subprocess.run([sys.executable, '-B', os.path.abspath(__file__), '--gen', r3, tmp],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if p.returncode != 0:
        print('RESULT ERROR: rung 3 generation failed\n' + p.stderr)
        return 2
    takes = json.loads(p.stdout.strip().splitlines()[-1])

    sys.path.insert(0, os.path.join(r5, 'tools', 'den-bundle'))
    import fakes
    import method
    import records
    import router
    from assemble import assemble, association_safe

    M, R = method.load_method(), method.load_router()
    W = M['window_s']
    n_lines = n_back = n_noobs = 0
    max_back = 0.0
    back_by_state = {}
    elig_diff = []
    safe_rf_ct = []      # replay false, causal true
    safe_rt_cf = []      # replay true, causal false
    safe_rf_ct_in_window = []
    future_pos = 0
    future_pos_bundles = 0
    samples = 0
    for d in takes:
        obs = records.read_jsonl(os.path.join(d, 'obs.jsonl'))
        recs = records.read_jsonl(os.path.join(d, 'tracks.jsonl'))
        meta = json.load(open(os.path.join(d, 'events.json')))
        tf = meta['time_field']
        st = records.Store(obs, recs)
        for tid, lines in st.lines.items():
            for l in lines:
                n_lines += 1
                w = l.rec[tf]
                if not l.rec.get('last_obs_ids'):
                    n_noobs += 1
                if l.ts < w - 1e-6:
                    n_back += 1
                    back_by_state[l.rec['state']] = back_by_state.get(l.rec['state'], 0) + 1
                    max_back = max(max_back, w - l.ts)
        name = os.path.basename(d)
        for ft in meta['frames']:
            t = ft + 0.01
            c_obs = [o for o in obs if o['capture_ts'] <= t + 1e-9]
            c_recs = [r for r in recs if r[tf] <= t + 1e-9]
            if not c_recs:
                continue
            cst = records.Store(c_obs, c_recs)
            clk = fakes.FakeClock(t)
            e_rep = router.Router(st, M, R, clk, fakes.FakeSender(clk)).eligible(t)
            e_cau = router.Router(cst, M, R, clk, fakes.FakeSender(clk)).eligible(t)
            if e_rep != e_cau:
                elig_diff.append((name, round(t - meta['frames'][0], 2), e_rep, e_cau))
            for tid in cst.order:
                samples += 1
                a_rep = association_safe(st, tid, t - W, t)
                a_cau = association_safe(cst, tid, t - W, t)
                meets = any(e['start'] <= t + 1e-9 and e['end'] >= t - W - 1e-9 for e in meta['events'])
                if a_rep is False and a_cau is True:
                    safe_rf_ct.append((name, tid, round(t - meta['frames'][0], 2)))
                    if meets:
                        safe_rf_ct_in_window.append((name, tid, round(t - meta['frames'][0], 2)))
                elif a_rep is True and a_cau is False:
                    safe_rt_cf.append((name, tid, round(t - meta['frames'][0], 2)))
                # the same selection as assemble.py's positions (rung 3's synthetic camera
                # has no frame_px in METHOD-V1, so assemble() itself is not called here)
                fut = [l for l in st.lines_of(tid, t) if l.rec[tf] > t + 1e-9
                       and t - W - 1e-6 <= l.ts <= t + 1e-6 and isinstance(l.rec.get('position'), dict)]
                if fut:
                    future_pos += len(fut)
                    future_pos_bundles += 1

    print('INFO takes: %d (8 rung-3 synthetic scenes x 3 seeds, typical noise, 4 fps)' % len(takes))
    print('%s rung 3 lines rung 5 dates before their own capture_ts: %d of %d (lines naming no observation: %d); '
          'by state %s; max back-dating %.2f s'
          % ('FAIL' if n_back else 'PASS', n_back, n_lines, n_noobs, back_by_state, max_back))
    print('%s router eligibility differs from causal at %d of the sampled frame times; first: %s'
          % ('FAIL' if elig_diff else 'PASS', len(elig_diff), elig_diff[:4]))
    print('%s association_safe replay-false/causal-true: %d of %d track-bundles (%d meet an annotated window); first: %s'
          % ('FAIL' if safe_rf_ct else 'PASS', len(safe_rf_ct), samples, len(safe_rf_ct_in_window), safe_rf_ct_in_window[:6] or safe_rf_ct[:6]))
    print('%s association_safe replay-true/causal-false: %d; first: %s'
          % ('FAIL' if safe_rt_cf else 'PASS', len(safe_rt_cf), safe_rt_cf[:4]))
    print('%s bundles holding positions from lines written after t1: %d bundles, %d positions'
          % ('FAIL' if future_pos else 'PASS', future_pos_bundles, future_pos))
    bad = bool(n_back) + bool(elig_diff) + bool(safe_rf_ct) + bool(safe_rt_cf) + bool(future_pos)
    print('RESULT %s: %d of 5 checks show replay reading rung 3 lines before their capture_ts' % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--gen':
        gen(sys.argv[2], sys.argv[3])
    else:
        sys.exit(main())
