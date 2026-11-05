"""One click of the den simulation: the pinned pipeline tools on a scripted scene.

Each tool comes from `git archive` of its pinned ref into the output folder and
runs in its own process, because they share module names (synth, records, method,
check). Every process refuses all sockets. den-observe's synthetic take has blank
RGB and fake-scripted-1 joints, so nothing looks at a picture. Qwen's answers are
typed through the real fact_line. The one piece that exists nowhere else is
person_to_aim below, labelled ADAPTER.

Reads {"scenario", "seat_m", "facing_xy", "refs", "tools_root", "work"} as JSON on
stdin. Prints one JSON line per hop, then {"hop": "done", ...} for the brain.
"""
import io
import json
import math
import os
import runpy
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # Nothing is written beside any tool.


def refuse_network(event, _args):
    if event in ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.getaddrinfo'):
        raise RuntimeError('the den simulation chain has no network')


sys.addaudithook(refuse_network)

ROOT = Path(__file__).resolve().parents[2]
TREES = {'observe': ('rung-2-observe', ['tools/den-observe', 'tools/den-take']),
         'track': ('claude/rung-3-track-local-514d6b', ['tools/den-track']),
         'bundle': ('rung-5-bundle', ['tools/den-bundle']),
         'core': ('den-core-live', ['tools/den-core', 'ha-config/custom_components/den_lighting',
                                    'stack/services/perception-lab/la_fact_log.py', 'stack/services/perception-lab/qwen_scene.py'])}
FRAMES, FRAME_S, T_HOST = 42, .25, 5000.0  # 4 Hz, as den-track was tuned, and long enough for two router requests.
ROUTE_FROM_S, ROUTE_TO_S, FAKE_REPLY_S = 2.0, 10.0, 1.25
SKIPPED = 'den-take (capture), den-register, den-label, den-n2, den-verify'
COCO17 = ('nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
          'left_wrist', 'right_wrist', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')
READING_STATES = ('engaged', 'ending')


class HopFailed(Exception):
    pass


def emit(hop, status, summary, started=None, **extra):
    row = dict(hop=hop, ok=True, status=status, summary=summary, **extra)
    if started is not None:
        row['ms'] = round((time.monotonic() - started) * 1000)
    print(json.dumps(row, allow_nan=False), flush=True)


def archive_trees(refs, tools_root):
    """One folder per pinned ref, written once. A folder without its marker is rebuilt."""
    trees = {}
    for key, (ref_name, paths) in TREES.items():
        sha = refs[ref_name]
        tree = Path(tools_root) / ('%s-%s' % (key, sha))
        trees[key] = tree
        if (tree / '.complete').is_file():
            continue
        done = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', sha, *paths], capture_output=True)
        if done.returncode:
            raise HopFailed('git archive %s (%s): %s' % (ref_name, sha, done.stderr.decode('utf-8', 'replace').strip()[-200:]))
        tree.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
            archive.extractall(tree, filter='data')
        (tree / '.complete').write_text(sha + '\n', encoding='utf-8')
    return trees


def guarded(script, *args):
    """A tool's own command line, in a process with this file's socket refusal."""
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    done = subprocess.run([sys.executable, '-B', __file__, '--guarded', str(script), *map(str, args)], capture_output=True, encoding='utf-8', errors='replace', env=env, timeout=120)
    if done.returncode:
        raise HopFailed('%s exit %d: %s' % (Path(script).name, done.returncode, (done.stderr or done.stdout).strip().split('\n')[-1][:300]))
    return done.stdout


def own_step(name, request):
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    done = subprocess.run([sys.executable, '-B', __file__, '--step', name], input=json.dumps(request), capture_output=True, encoding='utf-8', errors='replace', env=env, timeout=120)
    if done.returncode:
        raise HopFailed('%s: %s' % (name, (done.stderr or done.stdout).strip().split('\n')[-1][:300]))
    return json.loads(done.stdout)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def step_take(request):
    """den-observe's own synth, with its flat figure seated. The scene is drawn in synth's
    frame (camera at the origin, figure 3 m ahead), and the camera entry moves that whole
    frame so the figure is on the seat with the camera behind it, at an invented place."""
    tools = Path(request['observe_tree']) / 'tools'
    sys.path[:0] = [str(tools / 'den-observe'), str(tools / 'den-take')]
    import detections
    import fake_detector
    import synth

    seat_z = request['seat_m'][2]
    synth.JOINTS.update({'nose': (0.0, seat_z + .72), 'left_eye': (.03, seat_z + .76), 'right_eye': (-.03, seat_z + .76),
                         'left_ear': (.07, seat_z + .73), 'right_ear': (-.07, seat_z + .73),
                         'left_shoulder': (.18, seat_z + .5), 'right_shoulder': (-.18, seat_z + .5),
                         'left_elbow': (.22, seat_z + .22), 'right_elbow': (-.22, seat_z + .22),
                         'left_wrist': (.12, seat_z + .12), 'right_wrist': (-.12, seat_z + .12),
                         'left_hip': (.11, seat_z + .05), 'right_hip': (-.11, seat_z + .05),
                         'left_knee': (.10, seat_z), 'right_knee': (-.10, seat_z),
                         'left_ankle': (.10, .08), 'right_ankle': (-.10, .08)})
    work = Path(request['work'])
    scene = synth.Scene()
    frames = [(T_HOST + FRAME_S * i, T_HOST + FRAME_S * i - .004) for i in range(FRAMES)]
    folder = synth.make_take(work / 'take', frames, scene.depth_mm())
    synth.write_check(folder)

    length = math.hypot(*request['facing_xy'])
    fx, fy = request['facing_xy'][0] / length, request['facing_xy'][1] / length
    rotation, position = synth.camera_pose()
    # Turn synth's +y onto the facing direction, then slide the figure onto the seat.
    turn = [[fy, fx, 0.0], [-fx, fy, 0.0], [0.0, 0.0, 1.0]]
    rotated = [[sum(turn[i][k] * rotation[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    back = synth.PANEL_Y
    camera_m = [request['seat_m'][0] - back * fx, request['seat_m'][1] - back * fy, position[2]]
    room = work / 'room-model.json'
    room.write_text(json.dumps(synth.room_model([synth.camera_entry(room_from_left=(rotated, camera_m))]), indent=1), encoding='utf-8')

    pixels = {name: scene.joint_rgb(name) for name in synth.JOINTS}
    rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, sway_px=0.0) for i, (t, _) in enumerate(frames)]
    detections.write_rows(work / 'detections.jsonl', rows)
    last = rows[-1]
    return dict(take=str(folder), room_model=str(room), detections=str(work / 'detections.jsonl'), camera_m=camera_m, frames=len(frames),
                detector=last['detector']['boxes'], frame_px=[last['frame']['width'], last['frame']['height']],
                box_xyxy=last['detections'][0]['box_xyxy'], keypoints=last['detections'][0]['keypoints'])


def step_core(request):
    """Real fact_line, typed answers, then runner.run. Not run.py: its --facts path stamps
    synthetic false, and these inputs are synthetic."""
    sys.path.insert(0, str(Path(request['core_tree']) / 'tools' / 'den-core'))
    import decide
    import runner
    import sources
    import synthetic

    template = synthetic.TEMPLATES['reading' if request['scenario'] == 'reading_on_couch' else 'phone_held']
    items, lines = [], []
    for index, observed in enumerate(request['observed_at']):
        line = synthetic.fact_line(index, observed, observed + FAKE_REPLY_S, facts=template)
        lines.append(line)
        value = json.loads(line)
        items.append((sources.fact_time(value['available_at']), 'facts', value))
    header, log = runner.run(runner.tagged(items, 'synthetic'), runner.load_policies(['need', 'incumbent', 'decide']), synthetic=True, assume_occupied=True, sources=['synthetic'])
    work = Path(request['work'])
    (work / 'facts.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (work / 'den-core-log.jsonl').write_text('\n'.join(json.dumps(row) for row in [header, *log]) + '\n', encoding='utf-8')
    # The runner's ticks and assumed vacancy go on for minutes after the last reply, by which time need
    # calls the support expired and decide turns the bulbs off. This scene is up to the last reply.
    last_input = max(t for t, _, _ in items)
    states = [row for row in log if row['kind'] == 'person_state' and row['t'] <= last_input]
    decisions = [row for row in log if row['kind'] == 'decision']
    present = [row['data'] for row in decisions if row['source'] == 'decide' and row['t'] <= last_input]
    last = present[-1] if present else None
    latest = states[-1]['data'] if states else {}
    return dict(states=[row['data']['focused_engagement']['state'] for row in states],
                person=dict(state=(latest.get('focused_engagement') or {}).get('state'), kind=(latest.get('focused_engagement') or {}).get('kind'),
                            support=((latest.get('field_support') or {}).get('focused_engagement') or {}).get('support_status')),
                facts=len(lines), events=len(log), synthetic=header['synthetic'], actuation=header['actuation'],
                decide_total=sum(1 for row in decisions if row['source'] == 'decide'), incumbent_total=sum(1 for row in decisions if row['source'] == 'incumbent'),
                decide=dict(ok=True, source='%s:tools/den-core/decide.py' % request['core_sha'], decisions=len(present),
                            why=last['why'] if last else 'No decision while the person was there: it has no scene to ask for.',
                            actuator_domain=last['actuator_domain'] if last else None, requested_scene=last['requested_scene'] if last else None,
                            look_status=decide.DEFAULT_CONFIG['looks'][decide.LOOK]['status'] if last else None))


def person_to_aim(bundles, person):
    """ADAPTER, standing in for rung 6 and K7: PersonState has no shoulders, and nothing in
    the pipeline joins room-level reading to a track. It joins them here because there is
    exactly one track, and takes the joints from that track's newest bundle. Head pitch is
    left unknown, because nothing in the chain measures it, so the brain assumes level. It
    asserts jev and deep_stack health, which brain_run.py passes on: neither ran."""
    tracks = sorted({bundle['track_id'] for bundle in bundles})
    if len(tracks) != 1:
        return None, 'the adapter joins room reading to the only track, and found %d' % len(tracks)
    series = [entry for bundle in bundles for entry in bundle['keypoint_series']]
    if not series:
        return None, 'the bundles hold no keypoints'
    newest = max(series, key=lambda entry: entry['ts'])
    joints = {COCO17[entry['j']] if isinstance(entry['j'], int) else entry['j']: entry.get('xyz') for entry in newest['joints_room']}
    if not (joints.get('left_shoulder') and joints.get('right_shoulder')):
        return None, 'the newest bundle has no shoulders in the room frame'
    reading = person['state'] in READING_STATES and person['kind'] == 'reading' and person['support'] == 'observed'
    return dict(label='reading' if reading else 'other', person=dict(track_id=str(tracks[0]), shoulders_m=dict(left=joints['left_shoulder'], right=joints['right_shoulder']),
                                                                    head_m=joints.get('nose'), head_pitch_deg=None, approximate=True),
                asserted=dict(jev=True, deep_stack=True, camera=True), head_pitch='unknown', from_bundle_at=newest['ts']), None


def chain(request):
    started = time.monotonic()
    work = Path(request['work'])
    work.mkdir(parents=True, exist_ok=False)
    refs = request['refs']
    trees = archive_trees(refs, request['tools_root'])
    emit('Pinned tools', 'REAL', 'git archive of %s, in %s' % (', '.join('%s %s' % (name, refs[name]) for name, _ in TREES.values()), request['tools_root']), started)
    emit('Skipped', 'MISSING', SKIPPED)

    started = time.monotonic()
    take = own_step('take', dict(observe_tree=str(trees['observe']), work=str(work), seat_m=request['seat_m'], facing_xy=request['facing_xy']))
    emit('Input image', 'SCRIPTED', 'None. RGB is blank and nothing looks at a picture.')
    emit('Frames', 'SCRIPTED', '%d frames at %.0f Hz, written in the real take format by den-observe synth.' % (take['frames'], 1 / FRAME_S), started)
    emit('Camera registration', 'MISSING', 'A synth camera entry at an INVENTED place, marked method synthetic.', camera_m=take['camera_m'])
    emit('Detector and box', 'SCRIPTED', '%s places the joints, and the box comes from them. No detector ran.' % take['detector'],
         frame_px=take['frame_px'], box_xyxy=take['box_xyxy'], keypoints=take['keypoints'])

    observe_tools, track_tools, bundle_tools = (trees[key] / 'tools' for key in ('observe', 'track', 'bundle'))
    started = time.monotonic()
    guarded(observe_tools / 'den-observe' / 'replay.py', '--take', take['take'], '--room-model', take['room_model'], '--detections', take['detections'], '--out', work / 'observe')
    run = json.loads((work / 'observe' / 'run.json').read_text(encoding='utf-8'))
    open_slots = len(run['method']['open_slots'])
    emit('Observations', 'REAL', 'replay.py wrote %d, unscored: %d open slots on proposed values, on synthetic depth.' % (run['gate']['records'], open_slots), started)

    started = time.monotonic()
    output = guarded(track_tools / 'den-track' / 'den_track.py', 'track', '--observations', work / 'observe' / 'observations.jsonl', '--out', work / 'track')
    lines = read_jsonl(work / 'track' / 'tracks.jsonl')
    track_run = json.loads((work / 'track' / 'track-run.json').read_text(encoding='utf-8'))
    emit('Tracks', 'REAL', 'den_track.py wrote %d lines for %d track(s), unscored: %d open slots.' % (len(lines), track_run['counts']['tracks'], len(track_run['method']['open'])), started,
         left_out=track_run['counts'].get('rejected', 0) + track_run['counts'].get('no_position', 0))
    if not lines:
        raise HopFailed('den_track.py made no track lines: ' + ' '.join(output.split('\n')[1:2]))

    started = time.monotonic()
    guarded(bundle_tools / 'den-bundle' / 'den_bundle.py', 'route', '--observations', work / 'observe' / 'observations.jsonl', '--tracks', work / 'track' / 'tracks.jsonl',
            '--from', ROUTE_FROM_S, '--to', ROUTE_TO_S, '--requests', work / 'requests.jsonl', '--bundles', work / 'bundles.jsonl')
    bundles, routed = read_jsonl(work / 'bundles.jsonl'), read_jsonl(work / 'requests.jsonl')
    emit('Bundles and router', 'REAL', 'den_bundle.py route: %d requests, %d bundles, with its fake sender. Practice only.' % (len(routed), len(bundles)), started)
    if not bundles:
        raise HopFailed('den_bundle.py route made no bundles')
    emit('Bundle to Qwen', 'MISSING', 'Not built. The bundles go nowhere, and no model is asked.')

    started = time.monotonic()
    core = own_step('core', dict(core_tree=str(trees['core']), core_sha=refs['den-core-live'], work=str(work), scenario=request['scenario'], observed_at=[bundle['interval'][1] for bundle in bundles]))
    emit('Qwen reply', 'SCRIPTED', '%d answers typed through the real fact_line. No model ran.' % core['facts'])
    emit('Room-level need', 'REAL', 'need v0 on those lines: %s. Track room, no position.' % (' then '.join(core['states']) or 'no change'), started, person=core['person'])
    emit('decide v0', 'REAL', '%s Log only: nothing reconciles it with the brain.' % core['decide']['why'], decisions=core['decide']['decisions'], actuation=core['actuation'], synthetic=core['synthetic'])

    adapter, problem = person_to_aim(bundles, core['person'])
    if problem:
        raise HopFailed(problem)
    emit('Person to aim', 'ADAPTER', 'Joins room-level "%s" to the only track, with its newest joints. Asserts jev and deep_stack health. Head pitch unknown.' % adapter['label'])
    print(json.dumps(dict(hop='done', ok=True, adapter=adapter, camera_m=take['camera_m'], decide=core['decide'], work=str(work)), allow_nan=False), flush=True)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--guarded':
        script = sys.argv[2]
        sys.argv = sys.argv[2:]
        sys.path.insert(0, str(Path(script).parent))
        runpy.run_path(script, run_name='__main__')
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--step':
        print(json.dumps(dict(take=step_take, core=step_core)[sys.argv[2]](json.load(sys.stdin)), allow_nan=False))
        return
    request = json.load(sys.stdin)
    try:
        chain(request)
    except (HopFailed, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(hop='failed', ok=False, reason=str(error)[:600])), flush=True)


if __name__ == '__main__':
    main()
