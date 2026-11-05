"""Probe: recompute router v0's schedule on constructed cases and compare with hand values.

Cases (tool's own Router, FakeClock, FakeSender; synthetic scenes from synth.py):
  1 one track (two_tracks with B removed), 2..42 s, answers in 1.25 s  -> starts every 4.0 s
  2 two tracks round-robin, 2..66 s                                     -> each track every 8.0 s, alternating
  3 refusal on request 2 (one track)                                    -> next start = refusal answer + 30 s
  4 15-per-minute cap with min_interval_s lowered to 1 s                -> 15 starts, then the 16th at first+60 s
  5 one in flight with a 6 s service time                               -> starts 6.0 s apart
  6 silent request 2 (timeout_s proposed 20)                            -> timeout at sent+20, next start at sent+50
Also checks the gate's router clauses on each log, and prints `per_person_period_s` vs the realised period.

How to run
  python3 probe_router-schedule.py <root of the branch checkout>
"""
import copy
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes    # noqa: E402
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from router import Router, run  # noqa: E402

M, R = method.load_method(), method.load_router()
T0 = synth.T0


def rule_with(**kw):
    b = copy.deepcopy(R.block)
    b.update(kw)
    return method.router_from(b)


def go(scene, start, until, latency=1.25, script=None, rule=R):
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(synth.ts(start))
    sender = fakes.FakeSender(clock, latency_s=latency, script=script)
    r = Router(st, M, rule, clock, sender)
    recs = run(r, clock, sender, synth.ts(until))
    g = gate.check_requests(recs, rule, st.order)
    return recs, {k: v for k, v in g.items() if v}


def rel(x):
    return round(x - T0, 3)


def one_track():
    s = synth.two_tracks()
    keep = {o['obs_id'] for o in s['observations'] if o['obs_id'].startswith('o-A-')}
    s['observations'] = [o for o in s['observations'] if o['obs_id'] in keep]
    s['tracks'] = [l for l in s['tracks'] if l['track_id'] == 'A']
    return s


ok = True


def report(name, got, want, gate_fail, extra=''):
    global ok
    match = got == want and not gate_fail
    ok &= match
    print('%s %s: tool %s | hand %s | gate router failures %s %s' % ('PASS' if match else 'FAIL', name, got, want, gate_fail or 'none', extra))


recs, gf = go(one_track(), 2, 42)
starts = [rel(x['sent_ts']) for x in recs if x['outcome'] != 'no_box']
gaps = sorted({round(b - a, 3) for a, b in zip(starts, starts[1:])})
report('1 one track gap', gaps, [4.0], gf, 'starts %s period_field %s' % (starts[:4], recs[0]['per_person_period_s']))

recs, gf = go(synth.two_tracks(), 2, 66)
per = {}
for x in recs:
    per.setdefault(x['track_id'], []).append(rel(x['sent_ts']))
gaps = {t: sorted({round(b - a, 3) for a, b in zip(v, v[1:])}) for t, v in per.items()}
alt = all(a['track_id'] != b['track_id'] for a, b in zip(recs, recs[1:]))
report('2 two tracks per-track gap', gaps, {'A': [8.0], 'B': [8.0]}, gf, 'alternating %s period_field %s' % (alt, recs[0]['per_person_period_s']))

recs, gf = go(one_track(), 2, 60, script={2: 'refused'})
s = [(rel(x['sent_ts']), x['outcome'], x['done_ts'] and rel(x['done_ts'])) for x in recs]
nxt = rel(recs[2]['sent_ts'])
report('3 refusal back-off next start', nxt, round(s[1][2] + 30.0, 3), gf, 'log %s' % s[:4])

recs, gf = go(one_track(), 2, 70, latency=0.5, rule=rule_with(min_interval_s=1))
st = [rel(x['sent_ts']) for x in recs]
report('4 cap: 16th start', st[15] if len(st) > 15 else None, round(st[0] + 60.0, 3), gf, 'first 16 %s' % st[:16])

recs, gf = go(one_track(), 2, 40, latency=6.0)
st = [rel(x['sent_ts']) for x in recs]
gaps = sorted({round(b - a, 3) for a, b in zip(st, st[1:])})
report('5 slow service gap', gaps, [6.0], gf, 'period_field %s (realised 6.0)' % recs[0]['per_person_period_s'])

recs, gf = go(one_track(), 2, 70, script={2: 'silent'})
s = [(rel(x['sent_ts']), x['outcome'], x['done_ts'] and rel(x['done_ts'])) for x in recs]
report('6 timeout then back-off', (s[1][1], s[1][2], s[2][0]), ('timeout', s[1][0] + 20.0, s[1][0] + 50.0), gf, 'log %s' % s[:3])

print('OVERALL', 'PASS' if ok else 'FAIL')
