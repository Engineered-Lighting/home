"""probe_atk_no_record_path: can rung 3 run on Observation records at all, and
does anything write Track records?

Ladder rung 3: in = observation, out = track; tested on E2's staged crossings.
Spec README: records travel as JSON Lines with field names and version.

Checks against tools/den-track (copied to a temp dir):
  1. an entry point (a module with a __main__ block or argparse) other than tests;
  2. any code (other than method.py's METHOD.md reader) that parses JSON or
     reads Observation fields (record['obs_id'], capture_ts, room_position, validity);
  3. feeding one Observation shaped like schemas.json's example (as
     den-observe writes it) to TrackerCore.step;
  4. which Track fields of schemas.json (track_id, state, position, velocity,
     heading, heading_as_of, association, identity, last_obs_ids) the
     tracker's Track object can supply;
  5. whether anything turns owner annotations into the scorer's
     (time, obs_id, person, track, confidence) links.

Run:  python3 -B probe_atk_no_record_path.py <branch checkout root>
Synthetic data only.
"""
import os
import re
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

OBS = {"obs_id": "o-den-oak-1793066610420-0", "source": {"sensor_id": "den-oak", "model": "RTMPose",
       "model_version": "rtmpose-m@den-1"}, "capture_ts": 1793066610.42, "kind": "keypoints",
       "depth_ts": 1793066610.4, "transform_id": "den-oak@2026-10-18", "score": 0.86,
       "room_position": {"x": 1.95, "y": 0.95, "z": 1.0,
                         "cov": [[0.003, 0, 0], [0, 0.009, 0], [0, 0, 0.003]]},
       "visibility": "visible", "validity": "valid"}

TRACK_FIELDS = ('track_id', 'state', 'position', 'velocity', 'heading', 'heading_as_of', 'head_pose',
                'posture', 'linked_objects', 'association', 'identity', 'last_obs_ids')


def main(root):
    tmp = tempfile.mkdtemp(prefix='r3atk-')
    tool = os.path.join(tmp, 'den-track')
    shutil.copytree(os.path.join(root, 'tools', 'den-track'), tool)
    sys.path.insert(0, tool)
    import tracker_core
    bad = 0
    entry, readers = [], []
    for name in sorted(os.listdir(tool)):
        if not name.endswith('.py'):
            continue
        src = open(os.path.join(tool, name), encoding='utf-8').read()
        if not name.startswith('test_') and re.search(r"__main__|argparse", src):
            entry.append(name)
        if re.search(r"jsonl|json\.loads?\(|room_position|capture_ts|\['obs_id'\]|\[\"obs_id\"\]|'validity'|\"validity\"", src) and name != 'method.py':
            readers.append(name)
    print('1. entry points (non-test): %s' % (entry or 'none'))
    print('2. modules that read JSON Lines or name Observation fields: %s' % (readers or 'none'))
    if not entry:
        bad += 1
    if not readers:
        bad += 1
    tc = tracker_core.TrackerCore(PARAMS)
    try:
        tc.step(OBS['capture_ts'], [OBS])
        print('3. TrackerCore.step accepted a schemas.json Observation')
    except Exception as e:
        print('3. TrackerCore.step on a schemas.json Observation: %s: %s' % (type(e).__name__, e))
        bad += 1
    tr = tracker_core.Track(1, [0, 0, 0, 0], None, 0.0, 'o1')
    have = [f for f in TRACK_FIELDS if f in tracker_core.Track.__slots__]
    print('4. Track fields the tracker object carries under their schema names: %s; missing: %s'
          % (have, [f for f in TRACK_FIELDS if f not in have]))
    print('   (tracker keeps a bare float confidence, no association.status/hypotheses/since_ts, no state enum;'
          ' step() returns confirmed tracks only)')
    if len(have) < len(TRACK_FIELDS):
        bad += 1
    annot = [n for n in os.listdir(tool) if n.endswith('.py') and
             re.search(r"def .*(label|annot|gold)", open(os.path.join(tool, n), encoding='utf-8').read())]
    print('5. code that builds scorer links from owner labels: %s' % (annot or 'none'))
    if not annot:
        bad += 1
    print('RESULT: %s (%d of 5 checks show a missing piece of the observation -> track -> score path)'
          % ('FAIL' if bad else 'PASS', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: python3 -B probe_atk_no_record_path.py <branch checkout root>')
    sys.exit(main(sys.argv[1]))
