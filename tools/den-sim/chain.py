"""One click of the den simulation: the pinned pipeline tools on a prepared take.

The take was recorded once by scene/prepare.py (hops 1 to 5). On each click this re-checks its frames by
hash, then runs the tools after it, each from `git archive` of its pinned ref and each in its own process,
because they share module names (synth, records, method, check). Every process refuses all sockets.

It is never told the scene. It gets a take folder and the set it sits in, and finds the take's truth by
the take's folder id. Until a real detector is built (step 3), fake_detector places the joints from that
truth, and until Qwen is asked (step 4) the answers are typed from the truth's held object. Both rows say
so. The one piece that exists nowhere else is person_to_aim, part of hop 17's row.

Reads {"take", "take_digest", "take_json_sha256", "check_file", "check_sha256", "set", "room_model":
{"path", "sha256", "by"}, "refs", "tools_root", "work"} as JSON on stdin: the digests are the ones the
take's prepare-receipt.json recorded for frames.csv, take.json and its check. Prints hop 3's re-check by hash ({"n": 3, "recheck": ...}), the
picture ({"picture": ...}), then one JSON line per hop, each with its word, the files it read and wrote
with their digests, its tools against their pins, and its stand-ins; then {"hop": "done", ...} for the
brain, or {"hop": "stopped", ...} at the hop the click was at when it stopped, never a hop 0.
"""
import csv
import hashlib
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True  # Nothing is written beside any tool.


# prepare.py's list: every connect, bind and send, and every name lookup, which can send DNS on its own.
NETWORK = ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.sendmsg', 'socket.getaddrinfo',
           'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo')


def refuse_network(event, _args):
    if event in NETWORK:
        raise RuntimeError('the den simulation chain has no network')


sys.addaudithook(refuse_network)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TREES = {'observe': ('rung-2-observe', ['tools/den-observe', 'tools/den-take']),
         'track': ('claude/rung-3-track-local-514d6b', ['tools/den-track']),
         'bundle': ('rung-5-bundle', ['tools/den-bundle']),
         'core': ('den-core-live', ['tools/den-core', 'ha-config/custom_components/den_lighting',
                                    'stack/services/perception-lab/la_fact_log.py', 'stack/services/perception-lab/qwen_scene.py'])}
CAMERA_ID, FRAME_PX = 'den-virtual', [640, 360]
ROUTE_FROM_S, ROUTE_TO_S = 2.0, 19.5  # inside the check's 20 s usable part: requests at 2, 6, 10, 14 and 18 s
TEMPLATE_FOR_HELD = {'book': 'reading', 'phone': 'phone_held'}
COCO17 = ('nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
          'left_wrist', 'right_wrist', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')
READING_STATES = ('engaged', 'ending')
TABLE = {hop['n']: hop for hop in json.loads((HERE / 'hops.json').read_text(encoding='utf-8'))['hops']}


def named(n, *which):
    """The finished stand-ins hops.json names for hop n, so a row and the table use the same words."""
    return [TABLE[n]['stand_ins'][i] for i in which]


class Stop(Exception):
    """The click ends at hop n. answer: the pipeline answered (nobody, no track), rather than broke."""

    def __init__(self, hop, why, answer=False):
        super().__init__(why)
        self.hop, self.why, self.answer = hop, why, answer


AT = [3]  # the hop this click is at: a tool or helper that fails stops the click there, never at a hop 0


def at(n):
    AT[0] = n


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def emit(n, word, summary, started=None, ran='ran now', **extra):
    row = dict(n=n, word=word, summary=summary, ran=ran, **extra)
    if started is not None:
        row['ms'] = round((time.monotonic() - started) * 1000)
    print(json.dumps(row, allow_nan=False), flush=True)


def git_lines(*args):
    done = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, encoding='utf-8')
    if done.returncode:
        raise Stop(AT[0], 'git %s: %s' % (args[0], done.stderr.strip()[-200:]))
    return [line for line in done.stdout.splitlines() if line]


def blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def tree_tools(tree, sha, ref_name, prefixes):
    """Every file under prefixes in an archived tree, against its blob at the pinned commit. A file
    edited, added or gone since the archive was written shows, whatever the tree's marker says."""
    expected = {}
    for line in git_lines('ls-tree', '-r', sha, '--', *prefixes):
        meta, path = line.split('\t', 1)
        expected[path] = meta.split()[2]
    actual = {}
    for prefix in prefixes:
        for file in sorted((tree / prefix).rglob('*')) if (tree / prefix).is_dir() else [tree / prefix]:
            if file.is_file():
                actual[file.relative_to(tree).as_posix()] = blob(file.read_bytes())
    pin = 'refs.json tools.%s' % ref_name
    return [dict(path=path, pin=pin, expected=expected.get(path), actual=actual.get(path)) for path in sorted(set(expected) | set(actual))]


def head_tools(*paths):
    """This branch's files against their blobs at HEAD: an uncommitted edit shows."""
    expected = {}
    for line in git_lines('ls-tree', 'HEAD', '--', *paths):
        meta, path = line.split('\t', 1)
        expected[path] = meta.split()[2]
    done = subprocess.run(['git', '-C', str(ROOT), 'hash-object', '--stdin-paths'], input='\n'.join(paths) + '\n', capture_output=True, encoding='utf-8')
    actual = dict(zip(paths, done.stdout.split())) if not done.returncode else {}
    return [dict(path=path, pin='HEAD', expected=expected.get(path), actual=actual.get(path)) for path in paths]


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
            raise Stop(AT[0], 'git archive %s (%s): %s' % (ref_name, sha, done.stderr.decode('utf-8', 'replace').strip()[-200:]))
        tree.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
            archive.extractall(tree, filter='data')
        (tree / '.complete').write_text(sha + '\n', encoding='utf-8')
    return trees


class Click:
    def __init__(self, request):
        self.request, self.work = request, Path(request['work'])
        self.take, self.set = Path(request['take']), Path(request['set'])

    def guarded(self, script, *args):
        """A tool's own command line, in a process with this file's socket refusal. Its argv is logged."""
        argv = [sys.executable, '-B', __file__, '--guarded', str(script), *map(str, args)]
        with open(self.work / 'argv.jsonl', 'a', encoding='utf-8', newline='\n') as log:
            log.write(json.dumps({'argv': argv[3:]}) + '\n')
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run(argv, capture_output=True, encoding='utf-8', errors='replace', env=env, timeout=120)
        if done.returncode:
            raise Stop(AT[0], '%s exit %d: %s' % (Path(script).name, done.returncode, (done.stderr or done.stdout).strip().split('\n')[-1][:300]))
        return done.stdout

    def own_step(self, name, request):
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, '-B', __file__, '--step', name], input=json.dumps(request), capture_output=True,
                              encoding='utf-8', errors='replace', env=env, timeout=120)
        if done.returncode:
            raise Stop(AT[0], '%s: %s' % (name, (done.stderr or done.stdout).strip().split('\n')[-1][:300]))
        return json.loads(done.stdout)

    def file(self, path):
        """A file this click wrote, as a receipt names it: its path under the click folder, and its digest."""
        return dict(path=Path(path).relative_to(self.work).as_posix(), sha256=sha256_file(path))

    def read(self, path, from_hop):
        return dict(self.file(path), from_hop=from_hop)

    def recorded(self, label, path, kind, by, sha256):
        """A recorded file this click read: its digest now (None when it is gone) beside the one recorded."""
        return dict(path=label, sha256=sha256_file(path) if Path(path).is_file() else None, recorded=dict(kind=kind, by=by, sha256=sha256))

    def truth(self):
        """The take's truth folder, found by the take's id, never by a scene's name: (folder or None, the
        other records opened to find it, as reads). Finding it opens every scene's truth/*/take.json, so each
        of those is a read too. Labels are the take's own or "another take", so a folder's name (a scene's
        random word) is never written down."""
        found, others = None, []
        for record in sorted((self.set / 'truth').glob('*/take.json')):
            if found is None and json.loads(record.read_text(encoding='utf-8')).get('take') == self.take.name:
                found = record.parent
            else:
                others.append(sha256_file(record))
        return found, [dict(path='truth/(another take, %d of %d)/take.json' % (i, len(others)), sha256=digest) for i, digest in enumerate(others, 1)]


def usable_rows(take, check):
    """frames.csv rows inside the check's usable part, so the slate's flashes never reach a detector."""
    spans = [(seg['first_n'], seg['last_n']) for seg in check['usable']['segments']]
    rows = list(csv.DictReader(io.StringIO((take / 'frames.csv').read_text(encoding='utf-8'))))
    return [row for row in rows if any(a <= int(row['n']) <= b for a, b in spans)]


def nv12_to_rgb(np, nv12, width=640, height=360):
    """BT.601 limited-range NV12 (Y plane then interleaved UV) -> uint8 RGB: the Lab's own decode,
    room_mapper.py's nv12_to_rgb at refs.json's base, copied unchanged."""
    y = nv12[:height].astype(np.float32)
    uv = nv12[height:height+height//2].reshape(height//2, width//2, 2).astype(np.float32)
    u = np.repeat(np.repeat(uv[:, :, 0], 2, axis=0), 2, axis=1)-128
    v = np.repeat(np.repeat(uv[:, :, 1], 2, axis=0), 2, axis=1)-128
    c = 1.164*(y-16)
    rgb = np.stack([c+1.596*v, c-0.392*u-0.813*v, c+2.017*u], axis=-1)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def step_picture(request):
    """The picture the panel shows: one usable frame read back out of the take's own NV12 plane."""
    import numpy as np
    from PIL import Image
    data = Path(request['frame']).read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        nv12 = np.load(io.BytesIO(archive.read('rgb_nv12.npy')), allow_pickle=False)
    out = io.BytesIO()
    Image.fromarray(nv12_to_rgb(np, nv12)).save(out, format='PNG', optimize=False)
    Path(request['out']).write_bytes(out.getvalue())
    return dict(width=FRAME_PX[0], height=FRAME_PX[1])


def step_rows(request):
    """Hop 6, SCRIPTED: fake_detector places the figure's true joints, frame by frame, from the truth
    folder. A truth with no person gives rows with no detection on every usable frame."""
    tools = Path(request['observe_tree']) / 'tools'
    sys.path[:0] = [str(tools / 'den-observe'), str(tools / 'den-take')]
    import detections
    import fake_detector
    truth = Path(request['truth'])
    record = json.loads((truth / 'take.json').read_text(encoding='utf-8'))
    poses = json.loads((truth / 'poses.json').read_text(encoding='utf-8'))['poses'] if record['person'] else None
    rows = []
    for row in request['rows']:
        t, seq = float(row['t_host_s']), int(row['rgb_seq'])
        if poses is None:
            rows.append(detections.validate({'schema': detections.SCHEMA, 'frame': {'capture_ts': t, 'clock': 'host_monotonic', 'rgb_seq': seq,
                                                                                  'width': FRAME_PX[0], 'height': FRAME_PX[1]},
                                             'available_ts': t + fake_detector.LATENCY_S,
                                             'detector': {'boxes': fake_detector.NAME, 'boxes_version': fake_detector.NAME, 'keypoints': fake_detector.NAME,
                                                          'keypoints_version': fake_detector.NAME, 'keypoint_layout': 'coco17'},
                                             'detections': []}))
            continue
        pixels = {name: tuple(xy) for name, xy in poses[record['pose_of_rgb_seq'][str(seq)]]['rgb_px'].items()}
        rows.append(fake_detector.row(t, rgb_seq=seq, pixels=pixels, sway_px=0.0))
    detections.write_rows(request['out'], rows)
    people = sum(1 for row in rows if row['detections'])
    last = next((row for row in reversed(rows) if row['detections']), None)
    return dict(rows=len(rows), with_person=people, detector=fake_detector.NAME,
                box_xyxy=last['detections'][0]['box_xyxy'] if last else None, keypoints=last['detections'][0]['keypoints'] if last else None)


def step_core(request):
    """Hops 12 and 13: answers typed through the real fact_line, then need v0 in den-core's real runner.
    Not run.py: its --facts path stamps synthetic false, and these inputs are synthetic."""
    sys.path.insert(0, str(Path(request['core_tree']) / 'tools' / 'den-core'))
    import runner
    import sources
    import synthetic

    template = synthetic.TEMPLATES[request['template']]
    items, lines = [], []
    for index, observed in enumerate(request['observed_at']):
        line = synthetic.fact_line(index, observed, observed + request['reply_after_s'], facts=template)
        lines.append(line)
        value = json.loads(line)
        items.append((sources.fact_time(value['available_at']), 'facts', value))
    header, log = runner.run(runner.tagged(items, 'synthetic'), runner.load_policies(['need']), synthetic=True, sources=['synthetic'])
    Path(request['facts']).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    Path(request['log']).write_text('\n'.join(json.dumps(row) for row in [header, *log]) + '\n', encoding='utf-8')
    # The runner's ticks go on after the last reply, until need calls the support expired. This take is up to the last reply.
    last_input = max(t for t, _, _ in items)
    states = [row for row in log if row['kind'] == 'person_state' and row['t'] <= last_input]
    latest = states[-1]['data'] if states else {}
    engagement = latest.get('focused_engagement') or {}
    return dict(states=[row['data']['focused_engagement']['state'] for row in states], facts=len(lines), events=len(log),
                person=dict(state=engagement.get('state'), kind=engagement.get('kind'),
                            support=((latest.get('field_support') or {}).get('focused_engagement') or {}).get('support_status')))


def person_to_aim(bundles, person):
    """Part of hop 17's row, standing in for rung 6 and K7: PersonState has no shoulders, and nothing in
    the pipeline joins room-level reading to a track. It joins them here because there is exactly one
    track, and takes the joints from that track's newest bundle. Head pitch is left unknown, because
    nothing in the chain measures it. It asserts jev and deep_stack health, which brain_run.py passes on."""
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


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def camera_position(room_model):
    """The invented camera's place in the room model this click used, for the page's marker only."""
    entry = json.loads(Path(room_model).read_text(encoding='utf-8').splitlines()[-1])
    camera = [c for c in entry['cameras'] if c['camera_id'] == CAMERA_ID][-1]
    return [round(row[3], 4) for row in camera['pose']['room_from_camera'][:3]]


def chain(request):
    click = Click(request)
    work, take, refs = click.work, click.take, request['refs']
    work.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    own = ['tools/den-sim/chain.py', 'tools/den-sim/hops.json']

    # Hop 3 on this click: is this still the take prepare recorded?
    at(3)
    sys.path.insert(0, str(HERE / 'scene'))
    import frame_hash
    hashed = frame_hash.check_take(take, expected_digest=request['take_digest'])
    by_take = 'prepare-receipt.json of take %s' % take.name
    # replay reads take.json too (its times, its cues), so it is held to the digest prepare recorded as well.
    take_read = click.recorded('%s/frames.csv' % take.name, take / 'frames.csv', 'take', by_take, request['take_digest'])
    record_read = click.recorded('%s/take.json' % take.name, take / 'take.json', 'take', by_take, request['take_json_sha256'])
    why = None if hashed['ok'] else hashed['reason']
    if not why and record_read['sha256'] != request['take_json_sha256']:
        why = 'take.json is not the one prepare recorded'
    print(json.dumps(dict(n=3, recheck=dict(ok=why is None, frames=hashed['frames'], reason=why, ms=round((time.monotonic() - started) * 1000)),
                          tools=head_tools('tools/den-sim/scene/frame_hash.py'), read=[take_read, record_read])), flush=True)
    if why:
        raise Stop(3, why)

    # Hop 4 on this click: the check prepare recorded, and no other. replay reads the NEWEST check-N.json
    # for its clock, so another one beside it (a second run of check.py writes one) would re-clock the take.
    at(4)
    name = request.get('check_file')
    if not name:
        raise Stop(4, 'prepare recorded no check for this take')
    check_read = click.recorded('%s/%s' % (take.name, name), take / name, 'check', by_take, request['check_sha256'])
    others = sorted(path.name for path in take.glob('check-*.json') if re.fullmatch(r'check-[0-9]+\.json', path.name) and path.name != name)
    why = '%s is not the one prepare recorded' % name if check_read['sha256'] != request['check_sha256'] else None
    if not why and others:
        why = '%s beside the %s prepare recorded, and replay reads the newest check' % (' and '.join(others), name)
    print(json.dumps(dict(n=4, recheck=dict(ok=why is None, reason=why), read=[check_read])), flush=True)
    if why:
        raise Stop(4, why)
    rows = usable_rows(take, json.loads((take / name).read_text(encoding='utf-8')))
    if not rows:
        raise Stop(4, 'the check left no usable part')

    # The picture, before any tool reads the take. A display: if it can't be read back, the click goes on without it.
    middle = rows[len(rows) // 2]
    try:
        picture = click.own_step('picture', dict(frame=str(take / middle['file']), out=str(work / 'picture.png')))
        picture.update(click.file(work / 'picture.png'), n=int(middle['n']), rgb_seq=int(middle['rgb_seq']))
    except Stop as failed:
        picture = dict(error=failed.why)
    print(json.dumps(dict(picture=picture)), flush=True)

    # Hop 6, SCRIPTED: rows from the truth. The archives are this hop's and every later tool's.
    at(6)
    started = time.monotonic()
    trees = archive_trees(refs, request['tools_root'])
    tools = {key: tree_tools(trees[key], refs[ref_name], ref_name, paths) for key, (ref_name, paths) in TREES.items()}
    observe_tools = [t for t in tools['observe'] if t['path'].startswith('tools/den-observe/')]
    truth, scanned = click.truth()
    if truth is None:
        raise Stop(6, 'no truth folder holds this take, so the scripted detector has nothing to place')
    # No receipt records the truth's digests, so nothing vouches for these reads, nor for the other scenes'
    # records opened to find this one, and the row says so.
    truth_reads = [dict(path='truth/(this take)/%s' % name, sha256=sha256_file(truth / name)) for name in ('take.json', 'poses.json') if (truth / name).exists()]
    detections = work / 'detections.jsonl'
    found = click.own_step('rows', dict(observe_tree=str(trees['observe']), truth=str(truth), rows=rows, out=str(detections)))
    six = dict(tools=[t for t in observe_tools if t['path'].endswith(('fake_detector.py', 'detections.py'))] + head_tools(*own),
               read=[take_read, check_read, *truth_reads, *scanned], wrote=[click.file(detections)],
               stand_ins=["fake_detector places the figure's true joints from the truth folder: no detector looks at the picture"],
               rows=found['rows'], with_person=found['with_person'])
    if not found['with_person']:
        emit(6, 'SCRIPTED', 'fake-scripted-1 wrote %d rows over the usable part, none with a person: the truth says nobody is there.' % found['rows'], started, **six)
        raise Stop(6, 'the detector saw nobody, nothing was asked', answer=True)
    emit(6, 'SCRIPTED', '%s placed the joints from the truth on %d of %d usable frames, and the box comes from them. No detector ran.'
         % (found['detector'], found['with_person'], found['rows']), started, frame_px=FRAME_PX, box_xyxy=found['box_xyxy'], keypoints=found['keypoints'], **six)

    # Hop 7: observations, on the room model this page placed.
    at(7)
    room = request['room_model']
    started = time.monotonic()
    observe = work / 'observe'
    click.guarded(trees['observe'] / 'tools' / 'den-observe' / 'replay.py', '--take', take, '--room-model', room['path'], '--camera', CAMERA_ID,
                  '--detections', detections, '--out', observe)
    run = json.loads((observe / 'run.json').read_text(encoding='utf-8'))
    if (run.get('take_check') or {}).get('file') != name:
        raise Stop(7, 'replay read %s, not the check prepare recorded' % ((run.get('take_check') or {}).get('file') or 'no check'))
    room_read = dict(path='%s/room-model.jsonl' % Path(room['path']).parent.name, sha256=sha256_file(room['path']),
                     recorded=dict(kind='room model', by=room['by'], sha256=room['sha256']))
    emit(7, 'REAL', 'replay.py wrote %d observations, unscored: %d open slots on proposed values, on rendered depth.'
         % (run['gate']['records'], len(run['method']['open_slots'])), started,
         tools=tools['observe'], read=[take_read, record_read, check_read, room_read, click.read(detections, 6)],
         wrote=[click.file(observe / 'observations.jsonl'), click.file(observe / 'run.json')], stand_ins=[])

    # Hop 8: tracks.
    at(8)
    started = time.monotonic()
    output = click.guarded(trees['track'] / 'tools' / 'den-track' / 'den_track.py', 'track', '--observations', observe / 'observations.jsonl', '--out', work / 'track')
    lines = read_jsonl(work / 'track' / 'tracks.jsonl')
    track_run = json.loads((work / 'track' / 'track-run.json').read_text(encoding='utf-8'))
    emit(8, 'REAL', 'den_track.py wrote %d lines for %d track(s), unscored: %d open slots.' % (len(lines), track_run['counts']['tracks'], len(track_run['method']['open'])), started,
         tools=tools['track'], read=[click.read(observe / 'observations.jsonl', 7)],
         wrote=[click.file(work / 'track' / 'tracks.jsonl'), click.file(work / 'track' / 'track-run.json')], stand_ins=[])
    if not lines:
        raise Stop(8, 'a person was seen on %d frames, and no track formed: %s' % (found['with_person'], ' '.join(output.split('\n')[1:2]) or 'den_track.py made no lines'), answer=True)

    # Hop 9: the real router with its fake sender, on a method copy that knows this camera's frame size.
    at(9)
    started = time.monotonic()
    method = trees['bundle'] / 'tools' / 'den-bundle' / 'METHOD-V1.md'
    text = method.read_text(encoding='utf-8')
    if '"frame_px": {"den-oak": [1920, 1080]}' not in text:
        raise Stop(9, "den-bundle's METHOD-V1.md no longer has the frame_px line the copy extends")
    copy = work / 'METHOD-V1-den-virtual.md'
    copy.write_text(text.replace('"frame_px": {"den-oak": [1920, 1080]}', '"frame_px": {"den-oak": [1920, 1080], "%s": %s}' % (CAMERA_ID, json.dumps(FRAME_PX))), encoding='utf-8')
    click.guarded(trees['bundle'] / 'tools' / 'den-bundle' / 'den_bundle.py', '--method', copy, 'route', '--observations', observe / 'observations.jsonl',
                  '--tracks', work / 'track' / 'tracks.jsonl', '--from', ROUTE_FROM_S, '--to', ROUTE_TO_S, '--requests', work / 'requests.jsonl', '--bundles', work / 'bundles.jsonl')
    bundles, routed = read_jsonl(work / 'bundles.jsonl'), read_jsonl(work / 'requests.jsonl')
    emit(9, 'ADAPTER', 'den_bundle.py route: %d requests, %d bundles, with its fake sender. Practice only.' % (len(routed), len(bundles)), started,
         tools=tools['bundle'], read=[click.read(observe / 'observations.jsonl', 7), click.read(work / 'track' / 'tracks.jsonl', 8), click.read(copy, 9)],
         wrote=[click.file(copy), click.file(work / 'requests.jsonl'), click.file(work / 'bundles.jsonl')], stand_ins=named(9, 0, 1, 2))
    if not bundles:
        raise Stop(9, 'a person was seen for %d frames, too briefly to ask' % found['with_person'], answer=True)

    emit(10, 'SCRIPTED', 'Asserted: the brain is told the room is occupied. Nothing reads the track list for it yet.',
         tools=head_tools('tools/den-sim/brain_run.py'), stand_ins=['occupancy asserted to the brain by a typed event'])
    emit(11, 'MISSING', 'Not built: the bundles go nowhere, and no model is asked.', ran='not built', note='no request is built from the bundles')

    # Hops 12 and 13: typed answers, then need v0, in one process: a failure there is hop 12's.
    at(12)
    started = time.monotonic()
    held = (json.loads((truth / 'take.json').read_text(encoding='utf-8'))['person'] or {}).get('held')
    template = TEMPLATE_FOR_HELD.get(held, 'rest')
    core = click.own_step('core', dict(core_tree=str(trees['core']), template=template, reply_after_s=1.25, facts=str(work / 'facts.jsonl'),
                                       log=str(work / 'den-core-log.jsonl'), observed_at=[bundle['interval'][1] for bundle in bundles]))
    core_tools = tools['core']
    emit(12, 'SCRIPTED', '%d answers typed through the real fact_line, one per bundle. No model ran.' % core['facts'],
         tools=[t for t in core_tools if t['path'].endswith(('synthetic.py', 'la_fact_log.py', 'qwen_scene.py'))] + head_tools(*own),
         read=[click.read(work / 'bundles.jsonl', 9), *[r for r in truth_reads if r['path'].endswith('take.json')]], wrote=[click.file(work / 'facts.jsonl')],
         stand_ins=["answers typed from the truth folder's held object (a book reads, a phone is held), through the real fact_line: no model is asked"])
    emit(13, 'ADAPTER', 'need v0 on those lines: %s. Track room, no position.' % (' then '.join(core['states']) or 'no change'), started,
         tools=[t for t in core_tools if t['path'].startswith('tools/den-core/')], read=[click.read(work / 'facts.jsonl', 12)],
         wrote=[click.file(work / 'den-core-log.jsonl')], stand_ins=named(13, 0), person=core['person'])

    at(17)
    adapter, problem = person_to_aim(bundles, core['person'])
    if problem:
        raise Stop(17, problem)
    print(json.dumps(dict(hop='done', ok=True, adapter=adapter, camera_m=camera_position(room['path']),
                          adapter_reads=[click.read(work / 'bundles.jsonl', 9), click.read(work / 'den-core-log.jsonl', 13)],
                          adapter_tools=head_tools(*own), work=str(work)), allow_nan=False), flush=True)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--guarded':
        script = sys.argv[2]
        sys.argv = sys.argv[2:]
        sys.path.insert(0, str(Path(script).parent))
        runpy.run_path(script, run_name='__main__')
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--step':
        print(json.dumps(dict(rows=step_rows, core=step_core, picture=step_picture)[sys.argv[2]](json.load(sys.stdin)), allow_nan=False))
        return
    request = json.load(sys.stdin)
    try:
        chain(request)
    except Stop as stop:
        print(json.dumps(dict(hop='stopped', ok=False, stop=dict(hop=stop.hop, why=stop.why[:600], answer=stop.answer))), flush=True)
    except Exception as error:  # noqa: BLE001  anything else breaks the click at the hop it was at
        print(json.dumps(dict(hop='stopped', ok=False, stop=dict(hop=AT[0], why=('%s: %s' % (type(error).__name__, error))[:600], answer=False))), flush=True)


if __name__ == '__main__':
    main()
