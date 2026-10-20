"""Evaluate the stereo head-pose prototype (head3d) against a saved den site map.

Operator tool on the Victoria Jetson; not wired into JEV and not part of the
Lab release. Reads the Lab ledger read-only and (live mode) the local camera
owner. Output is words, angles and counts; no imagery leaves the Jetson.

  python head3d_eval.py ledger --map SITE_MAP.json [--ledger PATH] SESSION_PREFIX [...]
      Recorded sessions: pairs each single-person pose output with the depth
      bundle recorded for the same RGB frame. Only recordings whose recipe
      requests 'depth' store bundles; as of 2026-09-30 none of the 49 Victoria
      sessions has one, so this mode reports that and evaluates nothing until
      a depth-recording session exists.

  python head3d_eval.py live --map SITE_MAP.json [--seconds 60] [--expect monitor]
      During an owner-operated Lab recording with pose: fetches /frame.npz at
      <=4/s into a short ring, reads new pose outputs from the ledger and pairs
      each with the bundle of the same RGB device timestamp (<=0.15 s). Prints
      one line per frame; writes a JSON summary to <room-map dir>/eval/.
"""
import argparse
from collections import Counter, deque
import json
import math
import os
from pathlib import Path
import sys
import time

import head3d
import room_mapper


class Tally:
    """Per-arm counts, stability and timing for evaluated frames."""

    def __init__(self, expect=None):
        self.expect = expect
        self.arms = {}

    def add(self, arm, observed_at, result, *, skew_s=None, elapsed_ms=None):
        arm_state = self.arms.setdefault(arm or '-', {'stability': head3d.Stability(), 'frames': [], 'last_t': None})
        t = observed_at if isinstance(observed_at, (int, float)) else time.time()
        if arm_state['last_t'] is not None and t < arm_state['last_t']:
            t = arm_state['last_t']
        arm_state['last_t'] = t
        room = result.get('room')
        state = arm_state['stability'].update(t, result['looking_at'], room['forward'] if room else None)
        head = result['head']
        frame = {'t': round(t, 3), 'looking_at': result['looking_at'], 'stability': state,
                 'method': head.get('method'), 'fusion': head.get('fusion'), 'reason': result['target'].get('reason'),
                 'agreement_deg': None if head.get('agreement_deg') is None else round(head['agreement_deg'], 1),
                 'pitch_deg': None if not room else round(room['pitch_deg'], 1),
                 'best': (result['target'].get('candidates') or [None])[0],
                 'skew_s': None if skew_s is None else round(skew_s, 3),
                 'elapsed_ms': None if elapsed_ms is None else round(elapsed_ms, 2)}
        arm_state['frames'].append(frame)
        return frame

    def summary(self):
        out = {}
        for arm, arm_state in self.arms.items():
            frames = arm_state['frames']
            words = Counter(f['looking_at'] for f in frames)
            timing = sorted(f['elapsed_ms'] for f in frames if f['elapsed_ms'] is not None)
            entry = {'frames': len(frames), 'looking_at': dict(words),
                     'stability': dict(Counter(f['stability'] for f in frames)),
                     'method': dict(Counter('%s/%s' % (f['method'], f['fusion']) for f in frames)),
                     'unknown_reasons': dict(Counter(f['reason'] for f in frames if f['looking_at'] == 'unknown'))}
            if timing:
                entry['elapsed_ms_p50_p95'] = [timing[len(timing)//2], timing[min(len(timing)-1, math.ceil(0.95*len(timing))-1)]]
            if self.expect and frames:
                hits = words.get(self.expect, 0)
                decided = sum(v for k, v in words.items() if k != 'unknown')
                entry['expect'] = {'target': self.expect, 'hit_fraction_all': hits/len(frames),
                                   'hit_fraction_decided': hits/decided if decided else None}
            out[arm] = entry
        return out


def evaluate_frame(pose, bundle, calibration, matrix, targets):
    model = room_mapper.rgbd_model(bundle['meta'], calibration)
    started = time.perf_counter()
    result = head3d.looking_at(pose, bundle['depth_mm'], model, matrix, targets)
    return result, 1000*(time.perf_counter()-started)


def print_frame(arm, frame):
    best = frame['best'] or {}
    print('%s arm %s  looking_at=%-13s %-12s %-18s agree=%-6s pitch=%-6s best=%s %.0fdeg  skew=%s  %sms' % (
        time.strftime('%H:%M:%S', time.localtime(frame['t'])), arm, frame['looking_at'], frame['stability'],
        '%s/%s' % (frame['method'], frame['fusion']), frame['agreement_deg'], frame['pitch_deg'],
        best.get('anchor'), best.get('effective_deg', float('nan')), frame['skew_s'], frame['elapsed_ms']), flush=True)


# ---------------------------------------------------------------- recorded sessions

def recorded_bundles(db, session_id):
    rows = db.execute("""SELECT id,metadata FROM media WHERE session_id=?
        AND json_extract(metadata,'$.stream')='rgbd_bundle'""", (session_id,)).fetchall()
    bundles = []
    for media_id, metadata in rows:
        meta = json.loads(metadata)
        rgb = ((meta.get('source_metadata') or {}).get('frames') or {}).get('rgb') or {}
        bundles.append({'id': media_id, 'epoch': meta.get('capture_epoch'), 'sequence': rgb.get('sequence'),
                        'device_s': rgb.get('device_timestamp_seconds'), 'calibration': meta.get('calibration')})
    return bundles


def match_bundle(frame, bundles, *, tolerance_s=0.05):
    """The recorded bundle of the output's RGB frame: same capture epoch and RGB
    sequence, else the nearest RGB device timestamp within tolerance_s."""
    same = [b for b in bundles if b['epoch'] == frame.get('capture_epoch')]
    exact = [b for b in same if frame.get('sequence') is not None and b['sequence'] == frame['sequence']]
    if exact:
        return exact[0]
    timed = [(abs(b['device_s']-frame['device_s']), i) for i, b in enumerate(same)
             if isinstance(b['device_s'], (int, float)) and isinstance(frame.get('device_s'), (int, float))]
    if timed and min(timed)[0] <= tolerance_s:
        return same[min(timed)[1]]
    return None


def cmd_ledger(args):
    room_map, matrix, _ = room_mapper.load_site_map(args.map)
    root = Path(args.ledger).parent
    reader = room_mapper.LedgerReader(args.ledger)
    db = reader.db
    report = {}
    try:
        sessions = [r[0] for r in db.execute('SELECT id FROM sessions ORDER BY started_at DESC')
                    if any(r[0].startswith(p) for p in args.sessions)]
        if not sessions:
            print('no session matches %s' % args.sessions)
        for sid in sessions:
            bundles = recorded_bundles(db, sid)
            if not bundles:
                print('session %s: no depth bundles recorded (recipe without the depth requirement); '
                      'offline 3D evaluation is impossible here: use live mode or record with a depth recipe' % sid[:8])
                report[sid] = {'status': 'no_depth_bundles'}
                continue
            tally, skipped = Tally(args.expect), Counter()
            rows = db.execute("""SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output'
                ORDER BY id""", (sid,)).fetchall()
            for row in rows:
                output = reader._output(row)
                if output is None:
                    continue
                if len(output['poses']) != 1 or output['people'] > 1:
                    skipped['not_one_person'] += 1
                    continue
                chosen = match_bundle(output['frame'] or {}, bundles)
                if chosen is None:
                    skipped['no_bundle_for_frame'] += 1
                    continue
                data = (root/'media'/sid/(chosen['id']+'.bin')).read_bytes()
                bundle = room_mapper.decode_bundle(data)
                result, elapsed = evaluate_frame(output['poses'][0], bundle, chosen['calibration'], matrix, room_map['targets'])
                tally.add(output['arm'], output['observed_at'], result, elapsed_ms=elapsed)
            report[sid] = {'status': 'evaluated', 'skipped': dict(skipped), 'arms': tally.summary()}
            print('session %s: %s' % (sid[:8], json.dumps(report[sid], sort_keys=True)))
    finally:
        reader.close()
    return report


# ---------------------------------------------------------------- live

def cmd_live(args):
    room_map, matrix, document = room_mapper.load_site_map(args.map)
    camera = room_mapper.CameraReader(args.camera_env)
    calibration = camera.calibration()
    registration = document['model']['meta']['camera_registration']
    if registration.get('calibration_content_id') and registration['calibration_content_id'] != room_mapper.calibration_id(calibration):
        print('WARNING: camera calibration content differs from the map registration')
    ring = deque(maxlen=max(4, int(args.buffer_s/room_mapper.MIN_BUNDLE_INTERVAL_S)))
    for _ in range(3):
        ring.append(camera.bundle())
    if registration.get('mount_signature') and not args.ignore_mount:
        from spatial_validation import check_mount
        depth, _, _ = room_mapper.median_depth([b['depth_mm'] for b in ring])
        mount = check_mount(registration['mount_signature'], depth)
        print('mount check: %s' % mount)
        if mount['status'] == 'changed':
            print('camera moved since the map was registered: re-run room_mapper capture/floor/register', file=sys.stderr)
            return 2
    ledger = room_mapper.LedgerReader(args.ledger)
    tally, skipped = Tally(args.expect), Counter()
    last = ledger.last_event_id()
    end = time.time()+args.seconds
    print('evaluating for %.0f s; map %s r%s; expect=%s' % (args.seconds, document['binding']['map_id'],
                                                          document['binding']['revision'], args.expect), flush=True)
    try:
        while time.time() < end:
            ring.append(camera.bundle())
            for output in ledger.outputs_after(last):
                last = max(last, output['event_id'])
                if len(output['poses']) != 1 or output['people'] > 1:
                    skipped['not_one_person'] += 1
                    continue
                bundle, skew = room_mapper.pair_bundle(output['frame'], list(ring))
                if bundle is None:
                    skipped['no_bundle_within_0.15s'] += 1
                    continue
                result, elapsed = evaluate_frame(output['poses'][0], bundle, calibration, matrix, room_map['targets'])
                print_frame(output['arm'], tally.add(output['arm'], output['observed_at'], result, skew_s=skew, elapsed_ms=elapsed))
    finally:
        ledger.close()
    summary = {'map_id': document['binding']['map_id'], 'revision': document['binding']['revision'],
               'model_sha256': document['binding']['model_sha256'], 'seconds': args.seconds, 'expect': args.expect,
               'skipped': dict(skipped), 'arms': tally.summary(), 'provenance': head3d.PROVENANCE,
               'frames': {arm: s['frames'] for arm, s in tally.arms.items()}}
    workspace = room_mapper.Workspace(Path(args.dir)/'eval').ensure()
    path = workspace.write_json('head3d-live-%s.json' % time.strftime('%Y%m%dT%H%M%S'), summary)
    print(json.dumps({k: summary[k] for k in ('skipped', 'arms')}, indent=2, sort_keys=True))
    print('summary written to %s' % path)
    return 0


def main(argv=None):
    top = argparse.ArgumentParser(description='head3d evaluator (prototype, not wired into JEV)')
    top.add_argument('--dir', default=os.environ.get('ROOM_MAP_DIR', room_mapper.DEFAULT_DIR))
    top.add_argument('--ledger', default=room_mapper.LEDGER)
    top.add_argument('--camera-env', default=room_mapper.CAMERA_ENV)
    sub = top.add_subparsers(dest='command', required=True)
    p = sub.add_parser('ledger')
    p.add_argument('--map', required=True)
    p.add_argument('--expect')
    p.add_argument('sessions', nargs='+')
    p = sub.add_parser('live')
    p.add_argument('--map', required=True)
    p.add_argument('--seconds', type=float, default=60.0)
    p.add_argument('--expect', help='the anchor the owner is told to look at (hit rate)')
    p.add_argument('--buffer-s', type=float, default=3.0)
    p.add_argument('--ignore-mount', action='store_true')
    args = top.parse_args(argv)
    try:
        if args.command == 'ledger':
            cmd_ledger(args)
            return 0
        return cmd_live(args)
    except ValueError as error:
        print('error: %s' % error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
