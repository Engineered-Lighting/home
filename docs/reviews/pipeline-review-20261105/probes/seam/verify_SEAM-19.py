"""verify_SEAM-19: den-track dates each Track line by `capture_ts`; den-bundle ignores it.

Checks, on den-track's own synthetic takes (den_track.py synth, then track):
  1. every den-track Track line carries `capture_ts` (its open slot record.time_field);
  2. den-bundle's records.Store dates a line with last_obs_ids [] at the previous
     line's date, so such lines (confirmed-unseen, coasting, ended) are placed
     earlier than their capture_ts;
  3. consequence: at a request time t, den-bundle reads Track lines den-track
     wrote AFTER t (future leakage), e.g. sees a track 'ended' up to max_coast_s
     before it ended, and router v0's eligibility differs from the eligibility
     the same lines give when dated by capture_ts;
  4. router.run over each take, both datings, request counts per track.

Run:
  python3 -B verify_SEAM-19.py <rung-3-track checkout> [<rung-5-bundle checkout>]
The second root defaults to a sibling folder named rung-5-bundle.
Synthetic data only, temp dir only, no network.
"""
import json
import os
import subprocess
import sys
import tempfile

EPS = 1e-6


def main():
    r3 = os.path.abspath(sys.argv[1])
    r5 = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(r3), 'rung-5-bundle')
    track_cli = os.path.join(r3, 'tools', 'den-track', 'den_track.py')
    bdir = os.path.join(r5, 'tools', 'den-bundle')
    sys.path.insert(0, bdir)
    import records, router, method, fakes  # noqa: E402  (den-bundle's own modules)

    tmp = tempfile.mkdtemp(prefix='verify_seam19_')
    sess = os.path.join(tmp, 'session')
    subprocess.run([sys.executable, '-B', track_cli, 'synth', '--out', sess, '--seeds', '1'],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, '-B', track_cli, 'track', '--session', sess],
                   check=True, stdout=subprocess.DEVNULL)
    m = method.load_method(method.METHOD_PATH)
    rule = method.load_router(method.ROUTER_PATH)

    tot = dict(lines=0, with_capture_ts=0, empty=0, empty_early=0, coast_end_early=0,
               max_shift=0.0, ended=0, ended_early=0, leak_ended=0, grid=0, grid_diff=0)
    req_rows = []
    for take in sorted(os.listdir(sess)):
        folder = os.path.join(sess, take)
        obs = records.read_jsonl(os.path.join(folder, 'observations.jsonl'))
        for o in obs:   # den-track's synth camera is 'synth-cam'; den-bundle knows frame_px for den-oak only
            src = o.setdefault('source', {})
            if src.get('sensor_id') not in m['frame_px']:
                src['sensor_id'] = 'den-oak'
        trk = records.read_jsonl(os.path.join(folder, 'tracks.jsonl'))
        tot['lines'] += len(trk)
        tot['with_capture_ts'] += sum(1 for r in trk if isinstance(r.get('capture_ts'), (int, float)))
        st = records.Store(obs, trk)
        # 2: dates of lines with no observation
        for tid, lines in st.lines.items():
            for ln in lines:
                c = ln.rec['capture_ts']
                if not ln.rec.get('last_obs_ids'):
                    tot['empty'] += 1
                    if ln.ts < c - EPS:
                        tot['empty_early'] += 1
                        tot['max_shift'] = max(tot['max_shift'], c - ln.ts)
                        if ln.rec['state'] in ('coasting', 'ended'):
                            tot['coast_end_early'] += 1
                if ln.rec['state'] == 'ended':
                    tot['ended'] += 1
                    if ln.ts < c - EPS:
                        tot['ended_early'] += 1
                        # 3: at the date den-bundle gives it, den-bundle already sees 'ended'
                        latest = st.latest(tid, ln.ts)
                        truth = [l for l in lines if l.rec['capture_ts'] <= ln.ts + EPS]
                        if latest.rec['state'] == 'ended' and truth and truth[-1].rec['state'] != 'ended':
                            tot['leak_ended'] += 1
        # the same lines dated by capture_ts
        st2 = records.Store(obs, trk)
        for lines in st2.lines.values():
            for ln in lines:
                ln.ts = ln.rec['capture_ts']
        t_first = min(o['capture_ts'] for o in obs)
        t_last = max(o['capture_ts'] for o in obs)
        fake_c = fakes.FakeClock(t_first)
        r_a = router.Router(st, m, rule, fake_c, fakes.FakeSender(fake_c, latency_s=1.25))
        r_b = router.Router(st2, m, rule, fake_c, fakes.FakeSender(fake_c, latency_s=1.25))
        t = t_first
        while t <= t_last:
            tot['grid'] += 1
            if set(r_a.eligible(t)) != set(r_b.eligible(t)):
                tot['grid_diff'] += 1
            t += 0.25
        counts = []
        for s in (st, st2):
            clock = fakes.FakeClock(t_first + m['window_s'])
            sender = fakes.FakeSender(clock, latency_s=1.25)
            r = router.Router(s, m, rule, clock, sender)
            recs = router.run(r, clock, sender, t_last)
            by = {}
            for rec in recs:
                by[rec['track_id']] = by.get(rec['track_id'], 0) + 1
            counts.append(by)
        req_rows.append((take, counts[0], counts[1]))

    print('RESULT track lines %d, with capture_ts %d' % (tot['lines'], tot['with_capture_ts']))
    print('RESULT lines with last_obs_ids [] : %d; dated by den-bundle earlier than their capture_ts: %d '
          '(coasting/ended among them: %d); max shift %.3f s'
          % (tot['empty'], tot['empty_early'], tot['coast_end_early'], tot['max_shift']))
    print('RESULT ended lines %d; dated early %d; at that date den-bundle already sees ended while '
          'capture_ts says not yet: %d' % (tot['ended'], tot['ended_early'], tot['leak_ended']))
    print('RESULT router v0 eligibility differs at %d of %d grid points (0.25 s)' % (tot['grid_diff'], tot['grid']))
    for take, a, b in req_rows:
        flag = '' if a == b else '   <- differs'
        print('RESULT requests %-32s den-bundle dating %-22s capture_ts dating %-22s%s'
              % (take, json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), flag))
    ok_fact = tot['with_capture_ts'] == tot['lines'] and tot['empty_early'] > 0
    print('%s: den-track writes capture_ts on every line; den-bundle dates %d no-observation lines earlier '
          'than capture_ts (up to %.3f s)' % ('FAIL' if ok_fact else 'PASS', tot['empty_early'], tot['max_shift']))
    print('%s: future Track lines read at request time (ended seen early) in %d cases'
          % ('FAIL' if tot['leak_ended'] else 'PASS', tot['leak_ended']))
    print('(FAIL above = the defect SEAM-19 describes is present). temp dir: %s' % tmp)


if __name__ == '__main__':
    main()
