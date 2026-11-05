"""probe_atk_heading_convention: does the tracker's heading follow schemas.json?

schemas.json (frames): "Bearings are measured clockwise from +y, seen from
above." Track.heading: "body facing, computed from the shoulders and hips in
Observation.joints_room"; Track.heading_as_of: "capture time of the newest
accepted shoulder and hip evidence; it moves only on accepted evidence".

The probe feeds the real TrackerCore clean floor positions of one person
walking (a) along +y, (b) along +x, (c) backwards along -y while facing +y
(only position is given: there is no shoulder or hip evidence at all), and
prints heading_deg and whether heading_as_of moved. Spec bearings:
(a) 0 deg, (b) 90 deg, (c) facing 0 deg; heading_as_of must not move without
shoulder/hip evidence. Tracker parameters are illustrative (no METHOD.md).

Run:  python3 -B probe_atk_heading_convention.py <branch checkout root>
The tool folder is copied to a temp dir first. Synthetic data only.
"""
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

PARAMS = {
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
    import tracker_core
    return tracker_core


def walk(tracker_core, start, v, steps=16, dt=0.25):
    tc = tracker_core.TrackerCore(PARAMS)
    R = [[0.0016, 0.0], [0.0, 0.0016]]
    tr = None
    moved = 0
    last_asof = None
    for k in range(steps):
        t = 1000.0 + k * dt
        xy = (start[0] + v[0] * k * dt, start[1] + v[1] * k * dt)
        live, _ = tc.step(t, [{'id': 'o%02d' % k, 'xy': xy, 'R': R}])
        if live:
            tr = live[0]
            if tr.heading_as_of is not None and tr.heading_as_of != last_asof:
                moved += 1
                last_asof = tr.heading_as_of
    return tr, moved


def main(root):
    tc = load(root)
    bad = 0
    cases = (('(a) walking +y', (1.0, 1.0), (0.0, 1.0), 0.0),
             ('(b) walking +x', (0.5, 2.0), (1.0, 0.0), 90.0),
             ('(c) stepping back along -y, facing +y', (1.0, 4.0), (0.0, -0.6), 0.0))
    for name, start, v, spec in cases:
        tr, moved = walk(tc, start, v)
        diff = abs(((tr.heading_deg - spec) + 180) % 360 - 180)
        print('%-40s heading_deg=%.1f spec_bearing=%.1f off_by=%.1f heading_as_of moved %d times '
              'with no shoulder/hip evidence' % (name, tr.heading_deg, spec, diff, moved))
        if diff > 20 or moved:
            bad += 1
    print('RESULT: %s (%d of 3 cases disagree with schemas.json bearing/heading_as_of rules)'
          % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_heading_convention.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))
