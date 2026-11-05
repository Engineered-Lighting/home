"""probe_atk_confidence_monotone: once a track's confidence falls, can any later
swap on it still be "silent"?

Drives the real TrackerCore (tools/den-track/tracker_core.py) on a synthetic
two-person take built with synth.Walker: A and B walk back and forth across the
made-up den and cross N times (an evening of staged crossings in one take).
Observations get a floor covariance R from synth's along-ray depth sigma and
its lateral sigma (no such adapter exists in the branch; this probe adds one).
Tracker parameters are ILLUSTRATIVE: the branch has no METHOD.md, so there are
no frozen values; the conclusion does not depend on them, because
tracker_core.py only ever multiplies confidence by (1 - risk) <= 1.

Reports, per crossing window, each confirmed track's confidence when the window
opens, and how many windows open with every track already below the scorer's
confident_at (0.9 here, also 0.5): in those windows a swap can only be scored
as "flagged", so the gate's "0 silent swaps" half holds by construction.
It also scores the take end to end (links -> scorer_core.score_windows).

Run:  python3 -B probe_atk_confidence_monotone.py <branch checkout root> [crossings=12] [noise=typical]
The tool folder is copied to a temp dir first. Synthetic data only.
"""
import math
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

PARAMS = {  # illustrative only: the branch freezes no values
    'clutter_density': 0.02, 'birth_density': 0.02, 'exit_radius_m': 0.4, 'exit_birth_weight': 0.5,
    'floor_margin_m': 0.3, 'coast_velocity_tau_s': 1.0, 'accel_sigma': 1.5, 'p_detect': 0.9,
    'gate_chi2': 13.8, 'max_hypotheses': 20000, 'risk_floor': 0.01, 'robust_d2': 6.0,
    'heading_min_speed': 0.25, 'confirm_window': 6, 'confirm_hits': 3, 'exit_coast_s': 1.0,
    'max_coast_s': 5.0, 'new_track_speed_sigma': 1.0,
}


def load(root):
    tmp = tempfile.mkdtemp(prefix='r3atk-')
    shutil.copytree(os.path.join(root, 'tools', 'den-track'), os.path.join(tmp, 'den-track'))
    sys.path.insert(0, os.path.join(tmp, 'den-track'))
    import synth
    import tracker_core
    import scorer_core
    return synth, tracker_core, scorer_core


def floor_R(xy, along, lateral):
    r = math.hypot(xy[0], xy[1])
    ux, uy = xy[0] / r, xy[1] / r
    a, l = along ** 2, lateral ** 2
    return [[a * ux * ux + l * uy * uy, (a - l) * ux * uy], [(a - l) * ux * uy, a * uy * uy + l * ux * ux]]


def repeated(synth, n_cross, gap=0.5):
    def scene(rng):
        ya, yb = 2.3, 2.3 + gap
        wa, wb = [], []
        for k in range(n_cross + 1):
            left = (k % 2 == 0)
            wa.append((-1.9 if left else 1.9, ya, 1.0))
            wb.append((1.9 if left else -1.9, yb, 1.0))
        a = synth.Walker('A', wa, 0.9)
        b = synth.Walker('B', wb, 1.0)
        times = [k / 20 for k in range(int(max(a.t_end, b.t_end) * 20) + 1)]
        close = [t for t in times if math.dist(a.at(t), b.at(t)) < 1.0]
        events, start, prev = [], None, None
        for t in close:
            if start is None:
                start = t
            elif t - prev > 0.5:
                events.append((start, prev))
                start = t
            prev = t
        if start is not None:
            events.append((start, prev))
        return [a, b], [{'id': 'c%02d' % i, 'kind': 'crossing', 'start': s - 1.0, 'end': e + 1.0}
                        for i, (s, e) in enumerate(events)]
    return scene


def run_take(synth, tracker_core, scene, noise):
    lateral = synth.NOISE[noise]['lateral']
    tc = tracker_core.TrackerCore(PARAMS, exits=[synth.DOOR],
                                  floor=(synth.ROOM['x'][0], synth.ROOM['y'][0], synth.ROOM['x'][1], synth.ROOM['y'][1]))
    links, conf_at = [], []
    for fr in scene['frames']:
        obs = [{'id': o['id'], 'xy': o['xy'], 'R': floor_R(o['xy'], o['depth_sigma'], lateral)} for o in fr['obs']]
        live, _ = tc.step(fr['t'], obs)
        on = {}
        for tr in live:
            if tr.last_seen == fr['t']:
                on[tr.last_obs_ids[0]] = tr
        for o in fr['obs']:
            tr = on.get(o['id'])
            links.append((fr['t'], o['id'], o['pid'], tr.id if tr else None, tr.confidence if tr else None))
        conf_at.append((fr['t'], {tr.id: tr.confidence for tr in live}))
    return links, conf_at


def main(root, n_cross=12, noise='typical'):
    synth, tracker_core, scorer_core = load(root)
    synth.SCENES['repeated'] = repeated(synth, n_cross)
    scene = synth.scenario('repeated', 0, noise=noise)
    links, conf_at = run_take(synth, tracker_core, scene, noise)
    windows = scene['events']
    print('take: %d frames, %d crossing windows, noise=%s' % (len(scene['frames']), len(windows), noise))
    below = {0.9: 0, 0.5: 0}
    for w in windows:
        snap = [c for t, c in conf_at if t <= w['start']]
        c = snap[-1] if snap else {}
        top = max(c.values()) if c else None
        for th in below:
            if top is not None and top < th:
                below[th] += 1
        print('  %s opens at %.1f: track confidences %s' % (
            w['id'], w['start'] - scene['frames'][0]['t'],
            ', '.join('T%d=%.3f' % (k, v) for k, v in sorted(c.items())) or '(no confirmed track)'))
    increases = 0
    last = {}
    for t, c in conf_at:
        for k, v in c.items():
            if k in last and v > last[k] + 1e-15:
                increases += 1
            last[k] = v
    print('confidence increases seen on any track: %d' % increases)
    for th, n in below.items():
        print('windows that open with every confirmed track below %.1f: %d of %d' % (th, n, len(windows)))
    res = scorer_core.score_windows(links, windows, 0.9, 0.9)
    print('end to end at confident_at 0.9: switches=%d silent=%d flagged=%d pooled_idf1=%s passed=%s '
          'pooled_low_conf_share=%s' % (
              sum(r['switches'] for r in res['windows']), res['silent_swaps'], res['flagged_switches'],
              res['pooled']['idf1'], res['gate']['passed'],
              '%.3f' % (sum(1 for l in links if l[3] is not None and l[4] < 0.9)
                        / max(1, sum(1 for l in links if l[3] is not None)))))
    late = below[0.9]
    if late >= len(windows) // 2:
        print('FAIL: %d of %d windows open with no track at or above confident_at; any swap there scores as '
              'flagged, so the silent-swap half of the gate is met by construction' % (late, len(windows)))
        print('RESULT: FAIL')
        return 1
    print('RESULT: PASS')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_confidence_monotone.py <branch checkout root> [crossings] [noise]')
    sys.exit(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 12,
                  sys.argv[3] if len(sys.argv) > 3 else 'typical'))
