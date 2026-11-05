#!/usr/bin/env python3
"""Run den-track's tracker (TrackerCore) on synthetic scenes and score it with
the tool's own E2 scorer, to check what the gate actually measures.

den-track has no METHOD.md at this commit, so no tracker parameters exist; this
probe supplies a plausible set (PARAMS below, marked as the probe's own).

Checks:
  A. entrypoint: Method() on the checkout (METHOD.md missing?) and whether any
     module calls require_closed() or reads/writes JSON Lines records.
  B. confidence is monotone: in a session where A and B cross several times,
     once a crossing makes a track's confidence fall below the scorer's
     threshold it never comes back, so every later swap on that track is
     'flagged' by construction (the silent-swap criterion stops testing).
  C. causality: feeding the same frames with and without future frames gives
     identical tracks up to each time (no look-ahead).
  D. Track fields vs schemas.json: heading convention (schema: bearing
     clockwise from +y; heading from shoulders and hips), heading_as_of moving
     with no shoulder/hip evidence, last_obs_ids after a missed frame.
  E. the pooled per-window IDF1 the gate uses vs one global matching on the
     same links (id_counts on all in-window links, checked independently in
     probe_r3_stats_recompute.py).

Run:
    python3 probe_r3_tracker_session.py <root of the branch checkout>
Synthetic data only, nothing leaves the process.
"""
import copy
import math
import os
import random
import sys

sys.dont_write_bytecode = True
if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402
import tracker_core  # noqa: E402

# the probe's own parameters (den-track has none: no METHOD.md)
PARAMS = {
    'coast_velocity_tau_s': 1.0, 'accel_sigma': 1.0, 'p_detect': 0.9, 'gate_chi2': 13.8,
    'clutter_density': 0.01, 'birth_density': 0.01, 'exit_radius_m': 0.5, 'exit_birth_weight': 0.5,
    'floor_margin_m': 0.3, 'max_hypotheses': 20000, 'risk_floor': 0.01, 'robust_d2': 9.0,
    'heading_min_speed': 0.3, 'confirm_window': 5, 'confirm_hits': 3, 'exit_coast_s': 1.0,
    'max_coast_s': 3.0, 'new_track_speed_sigma': 1.0,
}
CONFIDENT_AT = 0.9

# ---------------------------------------------------------------- A. entrypoint
print('--- A. entrypoint')
try:
    m = method.Method()
    print('INFO Method() loaded, open slots:', [n for n, _ in m.open])
except Exception as e:  # noqa: BLE001
    print('FAIL Method() on the checkout raises %s: %s' % (type(e).__name__, e))
callers, io = [], []
for name in sorted(os.listdir(TOOL)):
    if name.endswith('.py') and name != 'method.py':
        text = open(os.path.join(TOOL, name), encoding='utf-8').read()
        if 'require_closed' in text:
            callers.append(name)
        code = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith(('#', '"', "'")))
        if 'import json' in code or 'open(' in code or "['room_position']" in code or "['capture_ts']" in code:
            io.append(name)
print('%s modules calling require_closed(): %s' % ('PASS' if callers else 'FAIL', callers or 'none'))
print('%s modules (other than method.py) that open files, import json or read Observation fields: %s' % (
    'PASS' if io else 'FAIL', io or 'none'))
print('%s a CLI (__main__ outside tests): %s' % (
    'PASS' if any('__main__' in open(os.path.join(TOOL, n), encoding='utf-8').read()
                  for n in os.listdir(TOOL) if n.endswith('.py') and not n.startswith('test_')) else 'FAIL', 'checked'))


def obs_R(xy, depth_sigma, lateral):
    r = math.hypot(xy[0], xy[1])
    ux, uy = xy[0] / r, xy[1] / r
    a, l = depth_sigma ** 2, lateral ** 2
    return [[a * ux * ux + l * uy * uy, (a - l) * ux * uy], [(a - l) * ux * uy, a * uy * uy + l * ux * ux]]


def run(scene, params=PARAMS):
    trk = tracker_core.TrackerCore(params, exits=[synth.DOOR],
                                   floor=(synth.ROOM['x'][0], synth.ROOM['y'][0], synth.ROOM['x'][1], synth.ROOM['y'][1]))
    lateral = synth.NOISE[scene['noise']]['lateral'] if isinstance(scene['noise'], str) else 0.04
    links, snaps = [], []
    for f in scene['frames']:
        obs = [{'id': o['id'], 'xy': o['xy'], 'R': obs_R(o['xy'], o['depth_sigma'], lateral)} for o in f['obs']]
        live, _ = trk.step(f['t'], obs)
        on = {}
        for tr in live:
            if tr.last_seen == f['t']:
                for oid in tr.last_obs_ids:
                    on[oid] = tr
        for o in f['obs']:
            tr = on.get(o['id'])
            links.append((f['t'], o['id'], o['pid'], tr.id if tr else None, tr.confidence if tr else None))
        snaps.append((f['t'], {tr.id: (round(tr.x[0], 9), round(tr.x[1], 9), tr.confidence) for tr in live}))
    return links, snaps, trk


# ------------------------------------------------- B. multi-crossing session
def multi_cross(rng, passes=6, gap=0.30):
    ya, yb = 2.4, 2.4 + gap
    wa, wb = [(-2.0, ya, 0.3)], [(2.0, yb, 0.3)]
    for k in range(passes):
        wa.append(((2.0 if k % 2 == 0 else -2.0), ya, 0.6))
        wb.append(((-2.0 if k % 2 == 0 else 2.0), yb, 0.6))
    a, b = synth.Walker('A', wa, 1.0), synth.Walker('B', wb, 1.0)
    times = [k / 20 for k in range(int(a.t_end * 20) + 1)]
    close = [t for t in times if math.dist(a.at(t), b.at(t)) < 1.0]
    events, start, prev = [], None, None
    for t in close:
        if start is None:
            start = t
        elif t - prev > 0.2:
            events.append({'id': 'c%d' % len(events), 'kind': 'crossing', 'start': start - 1.5, 'end': prev + 1.5})
            start = t
        prev = t
    if start is not None:
        events.append({'id': 'c%d' % len(events), 'kind': 'crossing', 'start': start - 1.5, 'end': prev + 1.5})
    return [a, b], events


synth.SCENES['probe_multi_cross'] = multi_cross
print('--- B. confidence across a session of repeated crossings (probe params, typical noise)')
worst = None
for seed in range(6):
    scene = synth.scenario('probe_multi_cross', seed=seed, noise='typical', fps=4.0)
    links, snaps, trk = run(scene)
    res = scorer_core.score_windows(links, scene['events'], CONFIDENT_AT, 0.9)
    per = []
    for w in res['windows']:
        confs = [l[4] for l in links if w['start'] <= l[0] <= w['end'] and l[4] is not None]
        per.append('%s:min=%.2f,low=%.2f,sw=%d/%d' % (w['id'], min(confs) if confs else float('nan'),
                                                     w['low_confidence_share'] or 0.0, w['switches'], w['silent_swaps']))
    # monotone check per track
    hist = {}
    for t, s in snaps:
        for tid, (_, _, c) in s.items():
            hist.setdefault(tid, []).append(c)
    rises = sum(1 for h in hist.values() for a, b in zip(h, h[1:]) if b > a + 1e-15)
    print('INFO seed %d: windows=%d pooled_idf1=%.3f switches=%d silent=%d flagged=%d gate=%s rises=%d | %s' % (
        seed, res['crossings'], res['pooled']['idf1'] or 0, sum(w['switches'] for w in res['windows']),
        res['silent_swaps'], res['flagged_switches'], res['gate']['passed'], rises, ' '.join(per)))
    # after the first window in which a track's confidence fell below the threshold, how many later windows still test it?
    for tid, h in hist.items():
        below = [c < CONFIDENT_AT for c in h]
        if any(below):
            k = below.index(True)
            if all(below[k:]):
                worst = (seed, tid, k, len(h))
print('%s confidence never rises once lowered (tracks with a rise: see rises=)' % 'INFO')
if worst:
    print('RESULT track %s in seed %d fell below %.2f at its frame %d of %d and stayed below for the rest of '
          'the session: every later swap on it is flagged by construction' % (worst[1], worst[0], CONFIDENT_AT, worst[2], worst[3]))

# ------------------------------------------------------------- C. causality
print('--- C. causality (prefix runs equal the full run up to each time)')
scene = synth.scenario('touching_crossing', seed=3, noise='typical')
_, full, _ = run(scene)
ok = True
for cut in (len(scene['frames']) // 3, len(scene['frames']) // 2, len(scene['frames']) - 3):
    pre = dict(scene, frames=scene['frames'][:cut])
    _, part, _ = run(pre)
    if part != full[:cut]:
        ok = False
print('%s tracker output up to t depends only on frames up to t' % ('PASS' if ok else 'FAIL'))

# ------------------------------------------------------- D. Track fields
print('--- D. Track fields vs schemas.json')
trk = tracker_core.TrackerCore(PARAMS)
R = [[0.0016, 0.0], [0.0, 0.0016]]
for k in range(12):          # A walks straight along +y at 1 m/s, seen every frame but k = 9
    obs = [] if k == 9 else [{'id': 'o%d' % k, 'xy': (0.0, 1.0 + 0.25 * k), 'R': R}]
    live, _ = trk.step(1000.0 + 0.25 * k, obs)
    if k == 8:
        before = (live[0].last_obs_ids[:], live[0].heading_as_of)
    if k == 9:
        tr = live[0]
        print('%s last_obs_ids on a frame with no associated observation: %s (schema: every Observation associated '
              'in that update)' % ('FAIL' if tr.last_obs_ids else 'PASS', tr.last_obs_ids))
tr = live[0]
schema_bearing = math.degrees(math.atan2(tr.x[2], tr.x[3])) % 360.0       # clockwise from +y
print('%s heading for a walk along +y: tracker heading_deg=%.1f, schema bearing (clockwise from +y)=%.1f' % (
    'PASS' if abs(((tr.heading_deg - schema_bearing + 180) % 360) - 180) < 5 else 'FAIL', tr.heading_deg, schema_bearing))
print('%s heading_as_of=%.2f set with no shoulder/hip evidence in any input (inputs carry only id, xy, R)' % (
    'FAIL' if tr.heading_as_of is not None else 'PASS', tr.heading_as_of or -1))
print('INFO Track attributes:', tracker_core.Track.__slots__)
print('INFO schema needs: track_id, state (tentative|confirmed|coasting|ended), position{x,y,cov}, velocity{vx,vy}, '
      'heading{deg,sd_deg}, heading_as_of, association{status,hypotheses[],since_ts}, identity, last_obs_ids')

# ------------------------------------------- E. per-window vs global IDF1
print('--- E. IDF1 on the tracker output: pooled per-window (tool) vs one global matching (standard)')
for seed in range(6):
    scene = synth.scenario('probe_multi_cross', seed=seed, noise='typical', fps=4.0)
    links, _, _ = run(scene)
    res = scorer_core.score_windows(links, scene['events'], CONFIDENT_AT, 0.9)
    inside = [l for l in links if any(w['start'] <= l[0] <= w['end'] for w in scene['events'])]
    glob = scorer_core.idf1(scorer_core.id_counts(inside))   # id_counts over all links = one global mapping
    verdict = 'FAIL' if res['gate']['passed'] and glob < 0.9 else 'PASS'
    print('%s seed %d: gate.passed=%s pooled per-window IDF1 %.3f, global IDF1 on the same links %.3f' % (
        verdict, seed, res['gate']['passed'], res['pooled']['idf1'], glob))
