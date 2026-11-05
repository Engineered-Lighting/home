#!/usr/bin/env python3
"""probe_router_schedule.py: recompute router v0's schedule by hand and compare.

What it checks
  Router v0 (tools/den-bundle/router.py, run on its fake clock and fake sender)
  against an independent, time-stepped simulator of the ladder's caps:
  at most one start every 4 s, at most 15 starts in any 60 s, one in flight,
  round-robin across tracks, a 30 s back-off counted from a refusal or error
  answer (and from a timeout), timeout_s at its proposed 20 s.
  Constructed cases: one track; two tracks; a refusal; a 15-a-minute cap made
  binding with min_interval_s = 1; a slow service time (6.5 s); a service time
  longer than the timeout (25 s); a refusal on a one-track scene.

  The simulator steps in whole centiseconds and shares no code with router.py;
  it only reads the tool's rule numbers.

How to run
  python3 probe_router_schedule.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network, no files written (bytecode writing is off).
Prints one PASS/FAIL line per case and a RESULT line.
"""
import copy
import os
import sys

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402

T0 = 1793066400.0


def scene(track_ids, until=200.0, dt=0.5):
    """Each track seen every dt seconds, confirmed and confident throughout."""
    obs, lines = [], []
    k = 0
    while k * dt <= until + 1e-9:
        t = round(T0 + k * dt, 3)
        for n, tid in enumerate(track_ids):
            x0, y0 = 600 + 500 * n, 300
            oid = 'p-%s-%d' % (tid, k)
            obs.append({'obs_id': oid, 'source': {'sensor_id': 'den-oak', 'model': 'm', 'model_version': 'v'},
                        'capture_ts': t, 'kind': 'keypoints', 'image_box': [x0, y0, x0 + 190, y0 + 520],
                        'keypoints': [[x0 + 10, y0 + 10, 0.9]] * 17, 'joint_set': 'coco17',
                        'joints_room': [{'j': j, 'xyz': [1.0, 1.0, 1.0], 'cov': [[0.003, 0, 0], [0, 0.003, 0], [0, 0, 0.003]],
                                         'status': 'supported', 'lift': 'stereo'} for j in range(17)],
                        'depth_ts': round(t - 0.033, 3), 'transform_id': 'den-oak@2026-10-18', 'validity': 'valid'})
            lines.append({'track_id': tid, 'state': 'confirmed', 'position': {'x': 1.0 + n, 'y': 1.0, 'cov': [[0.03, 0], [0, 0.06]]},
                          'association': {'status': 'confident', 'hypotheses': [], 'since_ts': T0},
                          'linked_objects': [], 'last_obs_ids': [oid]})
        k += 1
    return obs, lines


def tool_schedule(tracks, start, until, latency, script, rule):
    obs, lines = scene(tracks)
    st = records.Store(obs, lines)
    m = method.load_method()
    clock = fakes.FakeClock(T0 + start)
    sender = fakes.FakeSender(clock, latency_s=latency, script=script)
    r = router.Router(st, m, rule, clock, sender)
    recs = router.run(r, clock, sender, T0 + until)
    return [(round(x['sent_ts'] - T0, 2), x['track_id'], x['outcome']) for x in recs if x['outcome'] != 'no_box']


def hand_schedule(tracks, start, until, latency, script, min_interval, cap, minute, backoff, timeout):
    """Independent time-stepped simulator in centiseconds."""
    cs = lambda s: int(round(s * 100))
    t, end = cs(start), cs(until)
    starts, out = [], []
    inflight = None           # [sent_cs, reply_cs or None, outcome, index in out]
    backoff_until = None
    last = None
    n = 0
    while t <= end:
        if inflight is not None:
            sent, reply, outcome, idx = inflight
            if reply is not None and t >= reply and reply <= sent + cs(timeout):
                done = reply
            elif t >= sent + cs(timeout):
                done, outcome = sent + cs(timeout), 'timeout'
            else:
                done = None
            if done is not None:
                out[idx] = (out[idx][0], out[idx][1], outcome)
                if outcome != 'ok':
                    backoff_until = done + cs(backoff)
                inflight = None
        free = inflight is None
        spaced = not starts or t >= starts[-1] + cs(min_interval)
        rested = backoff_until is None or t >= backoff_until
        recent = sum(1 for s in starts if s > t - cs(minute))
        if free and spaced and rested and recent < cap:
            i = (tracks.index(last) + 1) % len(tracks) if last in tracks else 0
            tid = tracks[i]
            last = tid
            n += 1
            outcome = script.get(n, 'ok')
            starts.append(t)
            out.append((round(t / 100.0, 2), tid, None))
            if outcome == 'raise':
                out[-1] = (out[-1][0], tid, 'error')
                backoff_until = t + cs(backoff)
            else:
                reply = None if outcome == 'silent' else t + cs(latency)
                inflight = [t, reply, outcome, len(out) - 1]
        t += 1
    return out


def main():
    R = method.load_router()
    base = {k: R[k] for k in ('min_interval_s', 'max_per_minute', 'minute_s', 'backoff_s', 'timeout_s')}
    one_s = copy.deepcopy(R.block)
    one_s['min_interval_s'] = 1
    R1 = method.router_from(one_s)
    cases = [
        ('one track, one per 4 s', ['A'], 2.0, 40.0, 1.25, {}, R, base),
        ('two tracks round-robin, each one per 8 s', ['A', 'B'], 2.0, 40.0, 1.25, {}, R, base),
        ('two tracks, refusal on request 3 then 30 s back-off', ['A', 'B'], 2.0, 70.0, 1.25, {3: 'refused'}, R, base),
        ('one track, refusal on request 1', ['A'], 2.0, 60.0, 1.25, {1: 'refused'}, R, base),
        ('one track, error on request 2', ['A'], 2.0, 60.0, 1.25, {2: 'error'}, R, base),
        ('15 a minute binds (min_interval 1 s)', ['A'], 2.0, 100.0, 0.2, {}, R1, dict(base, min_interval_s=1)),
        ('slow service 6.5 s, one in flight', ['A'], 2.0, 40.0, 6.5, {}, R, base),
        ('service 25 s > timeout 20 s', ['A'], 2.0, 90.0, 25.0, {}, R, base),
        ('silent answer times out', ['A', 'B'], 2.0, 90.0, 1.25, {2: 'silent'}, R, base),
    ]
    bad = 0
    for name, tracks, start, until, latency, script, rule, nums in cases:
        got = tool_schedule(tracks, start, until, latency, script, rule)
        want = hand_schedule(tracks, start, until, latency, script, nums['min_interval_s'], nums['max_per_minute'],
                             nums['minute_s'], nums['backoff_s'], nums['timeout_s'])
        # the last request may still be in flight at `until` in one and finished in the other
        g = [(a, b) for a, b, _ in got]
        w = [(a, b) for a, b, _ in want]
        go = [c for _, _, c in got][:-1]
        wo = [c for _, _, c in want][:-1]
        ok = g == w and go == wo
        bad += not ok
        print('%s %s: tool %d starts, hand %d starts' % ('PASS' if ok else 'FAIL', name, len(g), len(w)))
        if not ok:
            print('    tool:', got[:20])
            print('    hand:', want[:20])
        if tracks == ['A', 'B'] and not script:
            per = {}
            for a, b in g:
                per.setdefault(b, []).append(a)
            gaps = {k: sorted(set(round(y - x, 2) for x, y in zip(v, v[1:]))) for k, v in per.items()}
            print('    per-track gaps (s):', gaps)
    print('RESULT %s: %d of %d cases differ' % ('PASS' if not bad else 'FAIL', bad, len(cases)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
