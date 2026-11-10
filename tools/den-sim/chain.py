"""One click of the den simulation: the pinned pipeline tools on a prepared take.

The take was recorded once by scene/prepare.py (hops 1 to 6). On each click this re-checks its frames by
hash, holds the detector rows prepare recorded beside it to hop 6's conditions, then runs the tools after
it on those rows, each from `git archive` of its pinned ref and each in its own process, because they share
module names (synth, records, method, check). Every process refuses all sockets.

It is never told the scene. It gets a take folder, the set it sits in, and the reply store. Hops 11 and 12
rebuild each router request's exact body from the take's own pixels and look Qwen's recorded reply up by
the body's sha256 (step_ask); nothing here opens the truth folder or the display file. The one piece that
exists nowhere else is person_to_aim, part of hop 17's row.

Reads {"take", "take_digest", "take_json_sha256", "check_file", "check_sha256", "detector", "set",
"room_model": {"path", "sha256", "by"}, "seat_top_m", "refs", "tools_root", "work", "replies", "last_ask"}
as JSON on stdin, and "owed" for prepare.py --owed: the digests are the ones the take's prepare-receipt.json
recorded for frames.csv, take.json and its check, and detector is its hop 6 part. Prints hop 3's and hop 4's
re-checks by hash ({"n": 3, "recheck": ...}), the picture with the person boxes of its frame's row
({"picture": ...}), one JSON line per hop, each with its word, the files it read and wrote with their
digests, its tools against their pins, and its stand-ins, and the torso height against the limit
({"torso": ...}); then {"hop": "done", ...} for the brain, {"hop": "owed", ...} for --owed, or
{"hop": "stopped", ...} at the hop the click was at when it stopped, never a hop 0.
"""
import csv
import hashlib
import io
import json
import os
import re
import runpy
import shutil
import statistics
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
sys.path.insert(0, str(ROOT / 'tools' / 'den-detect'))
import detect  # noqa: E402  its constants and the lock's parser; the models load only in the venv, at prepare
# observe's tree is also detect.py's --tools-root: den-observe, den-take and the Lab's workers at one pin.
LAB = 'stack/services/perception-lab/'
# qwen_live.spread's import closure, measured: 8 perception-lab modules and no third-party package (plan, hop 11).
QWEN_LIVE = ['qwen_live.py', 'qwen_scene.py', 'qwen_transport.py', 'occupancy_gate.py', 'analysis.py', 'analysis_result.py', 'experiment_history.py', 'reasoning.py']
TREES = {'observe': ('rung-2-observe', ['tools/den-observe', 'tools/den-take', *detect.WORKERS]),
         'track': ('claude/rung-3-track-local-514d6b', ['tools/den-track']),
         'bundle': ('rung-5-bundle', ['tools/den-bundle']),
         'core': ('den-core-live', ['tools/den-core', 'ha-config/custom_components/den_lighting', LAB + 'la_fact_log.py', *(LAB + name for name in QWEN_LIVE)])}
CAMERA_ID, FRAME_PX = 'den-virtual', [640, 360]
ROUTE_FROM_S, ROUTE_TO_S = 2.0, 19.5  # inside the check's 20 s usable part: requests at 2, 6, 10, 14 and 18 s
# Hops 11 and 12 (plan lines 477-521). The time label is fixed so bodies stay stable; the JPEG settings are the model test's.
TIME_LABEL, JOIN_S = 'evening 20:00', 0.001
JPEG = dict(format='JPEG', quality=85, subsampling=2, optimize=False, progressive=False)
SITE_ID, ENGINE_EPOCH, SOURCE_ID, TEXT_SCOPE = 'den-sim', 'den-sim-direct', 'den_sim_la_direct', 'reply_kept_render_only'
FAKE_ROOT, NOT_SEEN = 'fake-vllm', 'Qwen has not seen these exact frames'
REPLY_FIELDS = {'asked_unix', 'host', 'root', 'http_status', 'raw', 'elapsed_s'}
COCO17 =('nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
          'left_wrist', 'right_wrist', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')
READING_STATES = ('engaged', 'ending')
TORSO = ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip')
ON_COUCH_ABOVE_SEAT_M, TORSO_WINDOW_S = 0.45, 2.0   # hop 15 step 5: the seat's top plus 0.45 m, over 2 s
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
    """One folder per pinned ref, written once. A folder whose marker is missing, or names other paths, is rebuilt."""
    trees = {}
    for key, (ref_name, paths) in TREES.items():
        sha = refs[ref_name]
        tree = Path(tools_root) / ('%s-%s' % (key, sha))
        trees[key] = tree
        marker = '%s %s\n' % (sha, ' '.join(paths))
        if (tree / '.complete').is_file() and (tree / '.complete').read_text(encoding='utf-8') == marker:
            continue
        done = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', sha, *paths], capture_output=True)
        if done.returncode:
            raise Stop(AT[0], 'git archive %s (%s): %s' % (ref_name, sha, done.stderr.decode('utf-8', 'replace').strip()[-200:]))
        shutil.rmtree(tree, ignore_errors=True)
        tree.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
            archive.extractall(tree, filter='data')
        (tree / '.complete').write_text(marker, encoding='utf-8')
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


def rgb_plane(np, frame):
    """One frame file's picture plane, decoded as the Lab decodes it."""
    with zipfile.ZipFile(io.BytesIO(Path(frame).read_bytes())) as archive:
        return nv12_to_rgb(np, np.load(io.BytesIO(archive.read('rgb_nv12.npy')), allow_pickle=False))


def step_picture(request):
    """The picture the panel shows: one usable frame read back out of the take's own NV12 plane."""
    import numpy as np
    from PIL import Image
    out = io.BytesIO()
    Image.fromarray(rgb_plane(np, request['frame'])).save(out, format='PNG', optimize=False)
    Path(request['out']).write_bytes(out.getvalue())
    return dict(width=FRAME_PX[0], height=FRAME_PX[1])


class Refused(Exception):
    """Hop 11 refuses this take's requests: the click stops there, MISSING with why."""


def session_of(take):
    """The take's camera session, as take.py held every frame to it."""
    session = ((json.loads((Path(take) / 'take.json').read_text(encoding='utf-8')).get('health_before') or {}).get('body') or {}).get('session')
    if not isinstance(session, str) or not session.startswith('virtual-'):
        # This path keeps reply text, which is only fair for renders (plan, hop 11 step 1).
        raise Refused("the take's session does not start virtual-, and only a render's reply text may be kept")
    return session


def join(frames, offset_s, rows):
    """Hop 11 step 3: each bundle frame's ts, less run.json's clock offset, to the one frames.csv row within 1 ms."""
    out = []
    for frame in frames:
        t = frame['ts'] - offset_s
        near = [row for row in rows if abs(float(row['t_host_s']) - t) <= JOIN_S]
        if len(near) != 1:
            raise Refused('a bundle frame joins %d frames.csv rows within 1 ms, not one' % len(near))
        out.append((frame, near[0]))
    return out


def still_bytes(np, Image, scene, take, files):
    """Hop 11 step 4: decode each still and encode it with Pillow at fixed settings. Refuses a wrong size, too
    many bytes, or the same bytes twice, since one still repeated is one look."""
    images = []
    for file in files:
        rgb = rgb_plane(np, Path(take) / file)
        if rgb.shape != (scene.IMAGE_SIZE[1], scene.IMAGE_SIZE[0], 3):
            raise Refused('a still is %dx%d, not %dx%d' % (rgb.shape[1], rgb.shape[0], *scene.IMAGE_SIZE))
        out = io.BytesIO()
        Image.fromarray(rgb).save(out, **JPEG)
        if len(out.getvalue()) > scene.MAX_IMAGE_BYTES:
            raise Refused('a still is %d bytes as a JPEG, over %d' % (len(out.getvalue()), scene.MAX_IMAGE_BYTES))
        images.append(out.getvalue())
    if len(set(images)) != len(images):
        raise Refused('two stills of one request are the same bytes, which is one look')
    return images


def bodies(request, np, Image, live, scene):
    """Hops 11 and 12's one path to a body, for a click and for prepare.py --owed: each router request in
    requests.jsonl, its bundle, the stills qwen_live.spread chooses from the bundle's frames (the frame's file
    name stands in for media_id, since no JPEG exists yet: it only breaks ties), their bytes, and the real
    request_body. Yields (ask row, body bytes or None, stills)."""
    take = Path(request['take'])
    session = session_of(take)
    offset = json.loads(Path(request['run_json']).read_text(encoding='utf-8'))['clock']['offset_s']
    rows = list(csv.DictReader(io.StringIO((take / 'frames.csv').read_text(encoding='utf-8'))))
    by_id = {bundle['bundle_id']: bundle for bundle in read_jsonl(request['bundles'])}
    context, seen = scene.model_context(TIME_LABEL), set()
    for routed in read_jsonl(request['requests']):
        if routed['request_id'] in seen:
            raise Refused('request id %s repeats' % routed['request_id'])
        seen.add(routed['request_id'])
        row = dict(request_id=routed['request_id'], bundle_id=routed['bundle_id'], track_id=routed['track_id'], sent_ts=routed['sent_ts'],
                   association_safe=routed['association_safe'], body_sha256=None, stills=None, not_asked=None, root=None,
                   status=None, facts=None, invalid_reason=None, elapsed_s=None)
        bundle = by_id.get(routed['bundle_id'])
        if bundle is None:   # the router writes these when the window held no box
            yield dict(row, not_asked='the router wrote no bundle for this request'), None, None
            continue
        pool = [dict(media_id=line['file'], frame_id=line['rgb_seq'], capture_epoch=session, observed_at=frame['ts'],
                     valid_until=frame['ts'] + scene.MAX_AGE_SECONDS) for frame, line in join(bundle['frames'], offset, rows)]
        try:
            chosen = live.spread(pool, routed['sent_ts'])
        except live.FramesUnavailable as why:   # need gets no input from it, nothing is owed, and the walk goes on
            yield dict(row, not_asked='qwen_live.spread: %s' % why), None, None
            continue
        images = still_bytes(np, Image, scene, take, [still['media_id'] for still in chosen])
        body = scene.canonical(scene.request_body(context, images, [still['observed_at'] for still in chosen])).encode()
        stills = [dict(rgb_seq=int(still['frame_id']), file=still['media_id'], observed_at=still['observed_at'], jpeg_sha256=hashlib.sha256(image).hexdigest(),
                       bytes=len(image)) for still, image in zip(chosen, images)]
        yield dict(row, body_sha256=hashlib.sha256(body).hexdigest(), stills=stills), body, stills


def reply_line(scene, la_fact_log, row, stills, reply, session, take_digest):
    """Hop 12's field table (plan line 499): the real reply_from_proof, build_observation, first_receipt and
    la_fact_log.line, never den-core's synthetic line builder, with the clocks moved onto the chain's and the source id and
    text scope rewritten. Returns (line, receipt)."""
    try:
        response = json.loads(reply['raw'])
    except (TypeError, ValueError):
        response = None
    content, finish = scene.reply_from_proof({'response': response})
    available = row['sent_ts'] + reply['elapsed_s']   # the real ask time stays in the reply file only
    frames = [dict(media_id=still['jpeg_sha256'], frame_id=str(still['rgb_seq']), capture_epoch=session, observed_at=still['observed_at']) for still in stills]
    draft = scene.build_observation(site_id=SITE_ID, camera_id=CAMERA_ID, session_id=take_digest[:32], request_id=row['body_sha256'], frames=frames,
                                    generated_at=available, prompt_version=scene.PROMPT_VERSION, context=scene.model_context(TIME_LABEL),
                                    body_sha256=row['body_sha256'], engine_epoch=ENGINE_EPOCH, artifact_sha256=None, content=content,
                                    finish_reason=finish, prior_supplied=False)
    receipt = dict(scene.first_receipt(draft, available), text_scope=TEXT_SCOPE)
    text, _ = la_fact_log.line(request_id=row['body_sha256'], session_id=take_digest[:32], observed_at=stills[-1]['observed_at'], sent_at=row['sent_ts'],
                               available_at=available, engine_epoch=ENGINE_EPOCH, prompt_version=scene.PROMPT_VERSION, outcome='completed', observation=receipt)
    value = dict(json.loads(text), source_id=SOURCE_ID)   # the builder hard-codes the live source
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False), receipt


def read_reply(path):
    reply = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(reply, dict) or set(reply) != REPLY_FIELDS or not isinstance(reply['root'], str) or type(reply['elapsed_s']) not in (int, float) or reply['elapsed_s'] < 0:
        raise Refused('the reply on file for body %s is not the six fields ask_la.py files' % Path(path).stem[:12])
    return reply


def step_ask(request):
    """Hops 11 and 12 in one process: every body, then each one's reply by its sha256 in the store, or, for
    prepare.py --owed, each body still owed filed under owed/. Writes asks.jsonl (the join, one row per
    request) and, when every asked request has a reply, facts.jsonl."""
    lab = Path(request['core_tree']) / LAB
    sys.path.insert(0, str(lab))
    import numpy as np
    import PIL
    from PIL import Image
    import la_fact_log
    import qwen_live
    import qwen_scene as scene
    work, replies = Path(request['work']), Path(request['replies'])
    asks, lines, missing, filed, roots, days = [], [], [], [], set(), set()
    try:
        session = session_of(request['take'])
        for row, body, stills in bodies(request, np, Image, qwen_live, scene):
            asks.append(row)
            if body is None:
                continue
            if any(other['body_sha256'] == row['body_sha256'] for other in asks[:-1]):
                raise Refused('two requests have the same body, %s' % row['body_sha256'][:12])
            path = replies / (row['body_sha256'] + '.json')
            if not path.is_file():
                missing.append(row['body_sha256'])
                if request.get('owed'):
                    owed = Path(request['owed']) / (row['body_sha256'] + '.body')
                    if not owed.is_file() or sha256_file(owed) != row['body_sha256']:
                        owed.with_suffix('.part').write_bytes(body)
                        os.replace(owed.with_suffix('.part'), owed)
                    filed.append(row['body_sha256'])
                continue
            reply = read_reply(path)
            line, receipt = reply_line(scene, la_fact_log, row, stills, reply, session, request['take_digest'])
            row.update(root=reply['root'], status=receipt['status'], facts=receipt['facts'], invalid_reason=receipt['invalid_reason'], elapsed_s=reply['elapsed_s'])
            roots.add(reply['root'])
            days.add(time.strftime('%Y-%m-%d', time.localtime(reply['asked_unix'])) if type(reply['asked_unix']) in (int, float) else 'an unknown day')
            lines.append(line)
    except Refused as why:
        return dict(refused=str(why), asks=asks)
    (work / 'asks.jsonl').write_text(''.join(json.dumps(row, sort_keys=True, allow_nan=False) + '\n' for row in asks), encoding='utf-8', newline='\n')
    if lines and not missing:
        (work / 'facts.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    return dict(refused=None, asks=asks, missing=missing, filed=filed, roots=sorted(roots), days=sorted(days), lines=len(lines), pillow=PIL.__version__,
                read=[dict(path='replies/%s.json' % row['body_sha256'], sha256=sha256_file(replies / (row['body_sha256'] + '.json')))
                      for row in asks if row['root'] is not None])


def rows_hold(take, rows, hop, pins, take_digest, check_sha256):
    """Hop 6's conditions on the rows prepare recorded beside the take (plan line 442): (held, None), or
    (None, why). Anything less reads MISSING, never "nobody". rows: frames.csv's usable part; hop: the take's
    prepare-receipt.json hop 6; pins: requirements.lock's weight digests; take_digest and check_sha256: the
    take and the check this click holds, which detector-run.json must name, so rows recorded for another take
    can't stand in."""
    if not rows:
        return None, "the check's usable part is empty, so no rows can cover it"
    if not hop:
        return None, 'prepare recorded no detector rows for this take'
    if not (hop.get('checked') or {}).get('ok'):
        return None, 'prepare did not check these rows against the model'
    run_file, rows_file = take / 'detector-run.json', take / 'detections.jsonl'
    if not run_file.is_file() or sha256_file(run_file) != hop['run']['sha256']:
        return None, 'detector-run.json is not the one prepare recorded'
    if not rows_file.is_file() or sha256_file(rows_file) != hop['rows']['sha256']:
        return None, 'detections.jsonl is not the one prepare recorded'
    try:
        run = json.loads(run_file.read_text(encoding='utf-8'))
        if run['take_digest'] != take_digest:
            return None, 'detector-run.json was recorded for another take'
        if run['check']['sha256'] != check_sha256:
            return None, "detector-run.json was recorded against another check than this take's"
        if any(run['weights'].get(name) != pins[name] for name in detect.WEIGHTS):
            return None, "detector-run.json does not hold both pinned weight digests"
        recorded = [json.loads(line) for line in rows_file.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not recorded:
            return None, 'detections.jsonl holds no rows'
        seqs, usable = sorted(row['frame']['rgb_seq'] for row in recorded), sorted(int(row['rgb_seq']) for row in rows)
        if seqs != usable:
            return None, 'the rows cover %d of the %d usable frames' % (len(set(seqs) & set(usable)), len(usable))
        people = [row for row in recorded if detect.person_boxes(row)]
        joints = [row for row in people if any(d['class'] == 'person' and d['keypoints'] for d in row['detections'])]
        if (run['counts']['frames'], run['counts']['with_person']) != (len(recorded), len(people)):
            return None, "the rows' counts are not detector-run.json's"
        names = {row['detector']['boxes'] for row in recorded} | {row['detector']['keypoints'] for row in recorded if 'keypoints' in row['detector']}
        if not names <= set(detect.MODELS):
            return None, 'the rows name %s, not a real model' % ', '.join(sorted(map(str, names - set(detect.MODELS))))
        late = sum(1 for row in recorded if row['available_ts'] - row['frame']['capture_ts'] > detect.LIMIT_S)
        if late or run['counts']['over_limit']:
            return None, '%d frames took over %g s of compute, so den-observe rejects them: prepare again on a quiet PC' % (max(late, run['counts']['over_limit']), detect.LIMIT_S)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, 'detections.jsonl or detector-run.json is not what detect.py writes'
    return dict(rows=recorded, run=run, with_person=len(people), with_joints=len(joints), names=sorted(names)), None


def found_line(held):
    """Hop 6's sentence on rows that hold a person: on how many frames, and on how many of them RTMPose-m placed
    joints. A person box under 0.5 is never cropped, so it can be box only."""
    joints = ('RTMPose-m placed joints on %d of them' % held['with_joints'] if held['with_joints']
              else 'RTMPose-m placed no joints on any of them: box only')
    return 'YOLO26s found a person on %d of %d usable frames, and %s' % (held['with_person'], len(held['rows']), joints)


def weight_tools(take, pins):
    """The weight digests detector-run.json holds, against requirements.lock's, read whenever the file parses,
    so rows that fail hop 6's other checks never read as changed weights. A file that can't be read gives none:
    hop 6 reads MISSING for it already."""
    try:
        weights = json.loads((take / 'detector-run.json').read_text(encoding='utf-8'))['weights']
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not isinstance(weights, dict):
        return []
    return [dict(path='weights %s' % file, pin='tools/den-detect/requirements.lock', expected=pins[file], actual=weights.get(file))
            for file in (*detect.WEIGHTS, 'artifact.json')]


PERSON_KINDS = ('keypoints', 'person_box')   # den-observe's kinds for a person: with joints, or a box only


def no_track(with_person, frames, observations, output):
    """Hop 8's sentence when the rows hold a person and no track formed: what was seen, and why no track formed,
    from the person observations' validity (replay's run.json counts every kind together, the furniture's boxes
    too) and den_track.py's own line. It never says nobody."""
    counts = {}
    for obs in observations:
        if obs['kind'] in PERSON_KINDS:
            counts[obs['validity']] = counts.get(obs['validity'], 0) + 1
    seen = ', '.join('%s %d' % (key, value) for key, value in sorted(counts.items())) or 'none'
    return 'the detector saw a person on %d of %d usable frames, and no track formed: person observations %s. %s' % (
        with_person, frames, seen, ' '.join(output.split('\n')[1:2]) or 'den_track.py made no lines')


def torso(observations, seat_top_m):
    """How far the median height of supported shoulders and hips over the take's last 2 s sits from hop 15's
    "on the couch" limit, the seat's top plus 0.45 m. Only the difference leaves: an absolute height in a real
    room is a coordinate."""
    people = [obs for obs in observations if obs['kind'] == 'keypoints' and obs['validity'] == 'valid']
    if not people or seat_top_m is None:
        return None
    newest = max(obs['capture_ts'] for obs in people)
    heights = [joint['xyz'][2] for obs in people if obs['capture_ts'] >= newest - TORSO_WINDOW_S
               for joint in obs['joints_room'] if COCO17[joint['j']] in TORSO and joint['status'] == 'supported']
    if not heights:
        return dict(margin_m=None, joints=0, line='torso: no supported shoulder or hip in the last 2 s, so no height against the limit')
    margin = round(statistics.median(heights) - (seat_top_m + ON_COUCH_ABOVE_SEAT_M), 3)
    words = '%.2f m %s the limit' % (abs(margin), 'under' if margin <= 0 else 'over')
    return dict(margin_m=margin, joints=len(heights), line='torso %s (median of %d supported shoulder and hip points over the last 2 s; '
                'the limit is the seat\'s top plus 0.45 m)' % (words, len(heights)))


def step_core(request):
    """Hop 13: need v0 in den-core's real runner, on hop 12's fact lines. Not run.py: its --facts path stamps
    synthetic false, and these pixels are synthetic even when the words are real. The lines go to the runner
    directly, as #424 does, because sources.py would refuse the rewritten source id."""
    sys.path.insert(0, str(Path(request['core_tree']) / 'tools' / 'den-core'))
    import runner
    import sources

    items = []
    for line in Path(request['facts']).read_text(encoding='utf-8').splitlines():
        value = json.loads(line)
        items.append((sources.fact_time(value['available_at']), 'facts', value))
    header, log = runner.run(runner.tagged(items, 'synthetic'), runner.load_policies(['need']), synthetic=True, sources=['synthetic'])
    Path(request['log']).write_text('\n'.join(json.dumps(row) for row in [header, *log]) + '\n', encoding='utf-8')
    # The runner's ticks go on after the last reply, until need calls the support expired. This take is up to the last reply.
    last_input = max(t for t, _, _ in items)
    states = [row for row in log if row['kind'] == 'person_state' and row['t'] <= last_input]
    latest = states[-1]['data'] if states else {}
    engagement = latest.get('focused_engagement') or {}
    return dict(states=[row['data']['focused_engagement']['state'] for row in states], facts=len(items), events=len(log),
                person=dict(state=engagement.get('state'), kind=engagement.get('kind'),
                            support=((latest.get('field_support') or {}).get('focused_engagement') or {}).get('support_status')))


def person_to_aim(bundles, person):
    """Part of hop 17's row, standing in for rung 6 and K7: PersonState has no shoulders, and nothing in
    the pipeline joins room-level reading to a track. It joins them here because there is exactly one
    track, and takes the joints from the newest entry of that track's bundles that has both shoulders, so a
    real detector's frame that lost a shoulder can't stop the beam. Head pitch is left unknown, because nothing
    in the chain measures it. It asserts jev and deep_stack health, which brain_run.py passes on."""
    tracks = sorted({bundle['track_id'] for bundle in bundles})
    if len(tracks) != 1:
        return None, 'the adapter joins room reading to the only track, and found %d' % len(tracks)
    series = [entry for bundle in bundles for entry in bundle['keypoint_series']]
    if not series:
        return None, 'the bundles hold no keypoints'
    def joints_of(entry):
        return {COCO17[joint['j']] if isinstance(joint['j'], int) else joint['j']: joint.get('xyz') for joint in entry['joints_room']}
    both = [entry for entry in series if joints_of(entry).get('left_shoulder') and joints_of(entry).get('right_shoulder')]
    if not both:
        return None, 'no bundle entry has both shoulders in the room frame'
    newest = max(both, key=lambda entry: entry['ts'])
    joints = joints_of(newest)
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

    # The picture, before any tool reads the take: one usable frame read back out of its own NV12 plane. A
    # display: if it can't be read back, the click goes on without it. It's printed with its row's boxes below.
    at(6)
    picture, middle = dict(error='the usable part is empty'), rows[len(rows) // 2] if rows else None
    if middle:
        try:
            picture = click.own_step('picture', dict(frame=str(take / middle['file']), out=str(work / 'picture.png')))
            picture.update(click.file(work / 'picture.png'), n=int(middle['n']), rgb_seq=int(middle['rgb_seq']))
        except Stop as failed:
            picture = dict(error=failed.why)

    # Hop 6, recorded by prepare: the rows YOLO26s and RTMPose-m wrote beside the take, held to its conditions.
    hop = request.get('detector') or {}
    pins = detect.lock()['weights']
    held, why = rows_hold(take, rows, hop, pins, request['take_digest'], request['check_sha256'])
    run = held['run'] if held else {}
    rows_read = click.recorded('%s/detections.jsonl' % take.name, take / 'detections.jsonl', 'rows', by_take, (hop.get('rows') or {}).get('sha256'))
    run_read = click.recorded('%s/detector-run.json' % take.name, take / 'detector-run.json', 'rows', by_take, (hop.get('run') or {}).get('sha256'))
    six = dict(tools=head_tools(*own, 'tools/den-detect/detect.py', 'tools/den-detect/requirements.lock') + weight_tools(take, pins),
               read=[take_read, check_read, rows_read, run_read], wrote=[], stand_ins=named(6, 0, 1, 2, 3, 4))
    # The person boxes of the shown frame's row. None, not an empty list, when the rows don't hold: the panel
    # then says no row passed hop 6's checks, never that the frame holds no person.
    boxes = [dict(score=round(d['score'], 3), box_xyxy=[round(v, 1) for v in d['box_xyxy']],
                  keypoints=[None if k is None else [round(k[0], 1), round(k[1], 1), round(k[2], 3)] for k in d['keypoints']] if d['keypoints'] else None)
             for row in held['rows'] if row['frame']['rgb_seq'] == picture.get('rgb_seq') for d in row['detections'] if d['class'] == 'person'] if held else None
    print(json.dumps(dict(picture=dict(picture, boxes=boxes, boxes_from='%s/detections.jsonl' % take.name if held else None))), flush=True)
    if not held:
        emit(6, 'MISSING', 'The recorded rows do not hold: %s.' % why, ran='recorded %s' % request.get('recorded'), note=why, **six)
        raise Stop(6, why)
    checked, people = hop['checked'], held['with_person']
    ran = 'recorded %s, rows checked against the model on %s' % (request.get('recorded') or checked['on'], checked['on'])
    measured = 'median %.0f ms a frame on this PC\'s CPU; rows checked against the model on %s, on %d frames, within %g px' % (
        run['ms']['median'], checked['on'], len(checked['frames']), checked['max_px'])
    if not people:
        emit(6, 'ADAPTER', 'YOLO26s found no person on any of the %d usable frames, so RTMPose-m had nothing to crop; %s.' % (len(held['rows']), measured), ran=ran, **six)
        raise Stop(6, 'the detector saw nobody, nothing was asked', answer=True)
    emit(6, 'ADAPTER', '%s; %s. Rows name %s.' % (found_line(held), measured, ' and '.join(held['names'])), ran=ran, rows=len(held['rows']), with_person=people, **six)
    found = dict(with_person=people, rows=len(held['rows']))

    # Hop 7: observations, on the room model this page placed. The archives are this hop's and every later tool's.
    at(7)
    room = request['room_model']
    started = time.monotonic()
    trees = archive_trees(refs, request['tools_root'])
    tools = {key: tree_tools(trees[key], refs[ref_name], ref_name, paths) for key, (ref_name, paths) in TREES.items()}
    observe_tools = [tool for tool in tools['observe'] if tool['path'].startswith(('tools/den-observe/', 'tools/den-take/'))]
    observe = work / 'observe'
    click.guarded(trees['observe'] / 'tools' / 'den-observe' / 'replay.py', '--take', take, '--room-model', room['path'], '--camera', CAMERA_ID,
                  '--detections', take / 'detections.jsonl', '--out', observe)
    run_json = json.loads((observe / 'run.json').read_text(encoding='utf-8'))
    if (run_json.get('take_check') or {}).get('file') != name:
        raise Stop(7, 'replay read %s, not the check prepare recorded' % ((run_json.get('take_check') or {}).get('file') or 'no check'))
    room_read = dict(path='%s/room-model.jsonl' % Path(room['path']).parent.name, sha256=sha256_file(room['path']),
                     recorded=dict(kind='room model', by=room['by'], sha256=room['sha256']))
    emit(7, 'REAL', 'replay.py wrote %d observations, unscored: %d open slots on proposed values, on rendered depth.'
         % (run_json['gate']['records'], len(run_json['method']['open_slots'])), started,
         tools=observe_tools, read=[take_read, record_read, check_read, room_read, rows_read],
         wrote=[click.file(observe / 'observations.jsonl'), click.file(observe / 'run.json')], stand_ins=[])
    line = torso(read_jsonl(observe / 'observations.jsonl'), request.get('seat_top_m'))
    if line:
        print(json.dumps(dict(torso=line)), flush=True)

    # Hop 8: tracks.
    at(8)
    started = time.monotonic()
    output = click.guarded(trees['track'] / 'tools' / 'den-track' / 'den_track.py', 'track', '--observations', observe / 'observations.jsonl', '--out', work / 'track')
    lines = read_jsonl(work / 'track' / 'tracks.jsonl')
    track_run = json.loads((work / 'track' / 'track-run.json').read_text(encoding='utf-8'))
    emit(8, 'REAL', 'den_track.py wrote %d lines for %d track(s), unscored: %d open slots.' % (len(lines), track_run['counts']['tracks'], len(track_run['method']['open'])), started,
         tools=tools['track'], read=[click.read(observe / 'observations.jsonl', 7)],
         wrote=[click.file(work / 'track' / 'tracks.jsonl'), click.file(work / 'track' / 'track-run.json')], stand_ins=[])
    # Zero tracks stops the chain before route, which exits 2 on them. The rows held a person, so never "nobody".
    if not track_run['counts']['tracks']:
        raise Stop(8, no_track(people, len(held['rows']), read_jsonl(observe / 'observations.jsonl'), output), answer=True)

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

    emit(10, 'SCRIPTED', 'The track list decides whether the older brain runs at all: %d track(s), so it does. That brain\'s own occupancy is still '
         'typed in brain_run.py until step 6.' % track_run['counts']['tracks'],
         tools=head_tools('tools/den-sim/brain_run.py'), stand_ins=["the older brain's occupancy typed in brain_run.py; the track list only decides that it runs"])
    # Hops 11 and 12: each request's exact body, rebuilt from the take's own pixels, and its recorded reply by
    # the body's sha256. A failure in that process is hop 11's.
    at(11)
    started = time.monotonic()
    asked = click.own_step('ask', dict(core_tree=str(trees['core']), take=str(take), take_digest=request['take_digest'], run_json=str(observe / 'run.json'),
                                       requests=str(work / 'requests.jsonl'), bundles=str(work / 'bundles.jsonl'), replies=request['replies'],
                                       work=str(work), owed=request.get('owed')))
    qwen_tools = [tool for tool in tools['core'] if tool['path'].startswith(LAB)]
    eleven = dict(tools=head_tools(*own, 'tools/den-sim/ask_la.py', 'tools/den-sim/ask_one.py') + qwen_tools, wrote=[], stand_ins=named(11, 0, 1, 2),
                  read=[take_read, record_read, click.read(observe / 'run.json', 7), click.read(work / 'requests.jsonl', 9), click.read(work / 'bundles.jsonl', 9)])
    if asked['refused']:
        emit(11, 'MISSING', 'No body was built: %s.' % asked['refused'], started, note=asked['refused'], **eleven)
        raise Stop(11, asked['refused'])
    rows = asked['asks']
    built = [row for row in rows if row['body_sha256']]
    thin = sorted({row['not_asked'] for row in rows if row['not_asked']})
    if request.get('owed'):
        print(json.dumps(dict(hop='owed', requests=len(rows), built=len(built), owed=asked['filed'], not_asked=thin)), flush=True)
        return
    last, days = read_last_ask(request.get('last_ask')), asked['days']
    eleven['ran'] = 'recorded %s, bodies rebuilt now' % ', '.join(days) if days and not asked['missing'] else 'bodies rebuilt now; Qwen not asked about all of them yet'
    counts = ' or '.join(map(str, sorted({len(row['stills']) for row in built}))) or 'no'
    emit(11, 'ADAPTER', '%d of %d requests built into exact bodies: whole frames, %s stills each chosen by qwen_live.spread, JPEG by Pillow %s at '
         'fixed settings, time label %s.%s' % (len(built), len(rows), counts, asked['pillow'], TIME_LABEL, ' Not asked: %s.' % '; '.join(thin) if thin else ''), started, **eleven)
    if not built:
        raise Stop(11, 'no request had two stills to ask with: %s' % '; '.join(thin), answer=True)

    at(12)
    replies = [dict(read, recorded=dict(kind='replies', by='last-ask.json', sha256=((last or {}).get('filed', {}).get(read['path'][8:-5]) or {}).get('sha256')))
               for read in asked['read']]
    twelve = dict(tools=head_tools(*own) + [tool for tool in qwen_tools if tool['path'].endswith(('qwen_scene.py', 'la_fact_log.py'))],
                  read=[click.read(work / 'requests.jsonl', 9), *replies], wrote=[click.file(work / 'asks.jsonl')], asks=asks_view(rows))
    if asked['missing']:
        why = '%s: %d of %d bodies have no reply on file' % (NOT_SEEN, len(asked['missing']), len(built))
        emit(12, 'MISSING', why + '. Need gets no input, so the beam stays off.', note=why, owed=asked['missing'], **twelve, stand_ins=[])
        raise Stop(12, why)
    if len(asked['roots']) > 1:
        emit(12, 'MISSING', 'Replies from two models (%s): a click uses one.' % ', '.join(asked['roots']), note='replies from two models', **twelve, stand_ins=[])
        raise Stop(12, 'replies from two models')
    root, said = asked['roots'][0], words(built)
    twelve['said'] = said
    twelve['wrote'].append(click.file(work / 'facts.jsonl'))
    if root == FAKE_ROOT:
        emit(12, 'SCRIPTED', 'Scripted words from a fake vLLM (root %s), not Qwen: %s.' % (root, said), root=root, reply='fake', **twelve,
             stand_ins=['replies from a fake vLLM on 127.0.0.1, a test\'s scripted words, not Qwen\'s'])
    else:
        emit(12, 'ADAPTER', 'Qwen\'s real words, from %s, recorded once on %s and looked up by each body\'s sha256: %s. Its clocks are moved onto '
             'the chain\'s and its source id rewritten, and a re-ask could differ.' % (root, ', '.join(days) or 'an unknown day', said), root=root, reply='real',
             ran='recorded %s, looked up now' % ', '.join(days) if days else 'looked up now', **twelve, stand_ins=named(12, 0, 1))

    # Hop 13: need v0 on those lines, in den-core's real runner.
    at(13)
    started = time.monotonic()
    core = click.own_step('core', dict(core_tree=str(trees['core']), facts=str(work / 'facts.jsonl'), log=str(work / 'den-core-log.jsonl')))
    emit(13, 'ADAPTER', 'need v0 on those lines: %s. Track room, no position.' % (' then '.join(core['states']) or 'no change'), started,
         tools=[t for t in tools['core'] if t['path'].startswith('tools/den-core/')], read=[click.read(work / 'facts.jsonl', 12)],
         wrote=[click.file(work / 'den-core-log.jsonl')], stand_ins=named(13, 0), person=core['person'])

    at(17)
    adapter, problem = person_to_aim(bundles, core['person'])
    if problem:
        raise Stop(17, problem)
    print(json.dumps(dict(hop='done', ok=True, adapter=adapter, camera_m=camera_position(room['path']), reply='fake' if root == FAKE_ROOT else 'real', root=root,
                          adapter_reads=[click.read(work / 'bundles.jsonl', 9), click.read(work / 'den-core-log.jsonl', 13)],
                          adapter_tools=head_tools(*own), work=str(work)), allow_nan=False), flush=True)


def read_last_ask(path):
    """last-ask.json, which ask_la.py writes: what each paste filed, by body hash, with each reply's digest."""
    try:
        last = json.loads(Path(path).read_text(encoding='utf-8'))
        return last if isinstance(last.get('filed'), dict) else None
    except (OSError, TypeError, ValueError, AttributeError):
        return None


def said_by(row):
    facts = row['facts'] or {}
    if row['status'] == 'valid':
        return '%s, holding %s, people %s' % (facts['activity_hypothesis'], facts['held_object'], facts['people_visible'])
    return 'invalid (%s)' % row['invalid_reason'] if row['status'] else 'no reply on file' if row['body_sha256'] else None


def words(rows):
    """What Qwen said, request by request, from the facts: its activity and held object, or why a reply is invalid."""
    return '; '.join('%s %s' % (row['request_id'], said_by(row)) for row in rows)


def asks_view(rows):
    """The panel's view of asks.jsonl: per request its stills by frame, how far each is before the newest, and Qwen's words."""
    return [dict(request_id=row['request_id'], association_safe=row['association_safe'], body=row['body_sha256'] and row['body_sha256'][:12],
                 stills=[still['rgb_seq'] for still in row['stills'] or []], not_asked=row['not_asked'], said=said_by(row),
                 seconds=[round(row['stills'][-1]['observed_at'] - still['observed_at'], 1) for still in row['stills'] or []]) for row in rows]


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--guarded':
        script = sys.argv[2]
        sys.argv = sys.argv[2:]
        sys.path.insert(0, str(Path(script).parent))
        runpy.run_path(script, run_name='__main__')
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--step':
        print(json.dumps(dict(ask=step_ask, core=step_core, picture=step_picture)[sys.argv[2]](json.load(sys.stdin)), allow_nan=False))
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
