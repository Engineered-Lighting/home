"""One click of the den simulation: the pinned pipeline tools on a prepared take.

The take was recorded once by scene/prepare.py (hops 1 to 6). On each click this re-checks its frames by
hash, holds the detector rows prepare recorded beside it to hop 6's conditions, then runs the tools after
it on those rows, each from `git archive` of its pinned ref and each in its own process, because they share
module names (synth, records, method, check). Every process refuses all sockets.

It is never told the scene. It gets a take folder, the set it sits in, and the reply store. Hops 11 and 12
rebuild each router request's exact body from the take's own pixels and look Qwen's recorded reply up by
the body's sha256 (step_ask); nothing here opens the truth folder or the display file. Hop 10 is one occupancy
event from the track list, hop 15 is person.py (step_person), with need v0 once per track, and hop 17 is rung 9's
coordinator in spot mode, run once on those two through den-core's real runner (step_decide).

Reads {"take", "take_digest", "take_json_sha256", "check_file", "check_sha256", "detector", "set",
"room_model": {"path", "sha256", "by", "label"}, "light_model": {"path", "sha256", "label"}, "refs", "tools_root",
"work", "replies", "last_ask"} as JSON on stdin, and "owed" for prepare.py --owed: the digests are the ones the take's
prepare-receipt.json recorded for frames.csv, take.json and its check, and detector is its hop 6 part. The room model
is the one hop 14 wrote the couch into at page start, and the light model the one hop 16 built then. Prints hop 3's and
hop 4's re-checks by hash ({"n": 3, "recheck": ...}), the picture with the person boxes of its frame's row
({"picture": ...}), one JSON line per hop, each with its word, the files it read and wrote with their digests, its tools
against their pins, and its stand-ins; then {"hop": "done", ...} when a Decision lights a spot, {"hop": "owed", ...} for
--owed, or {"hop": "stopped", ...} at the hop the click was at when it stopped, never a hop 0.
"""
import base64
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import runpy
import shutil
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
         # The lighting brain's package beside den-core, at the same commit: spot_rule.py imports its aim.py (plan, hop 17's Out).
         'core': ('den-core-live', ['tools/den-core', 'stack/services/lighting-brain/lighting_brain', 'ha-config/custom_components/den_lighting',
                                    LAB + 'la_fact_log.py', *(LAB + name for name in QWEN_LIVE)])}
CAMERA_ID, FRAME_PX = 'den-virtual', [640, 360]
ROUTE_FROM_S, ROUTE_TO_S = 2.0, 19.5  # inside the check's 20 s usable part: requests at 2, 6, 10, 14 and 18 s
# Hops 11 and 12 (plan lines 477-521). The time label is fixed so bodies stay stable; the JPEG settings are the model test's.
TIME_LABEL, JOIN_S = 'evening 20:00', 0.001
JPEG = dict(format='JPEG', quality=85, subsampling=2, optimize=False, progressive=False)
SITE_ID, ENGINE_EPOCH, SOURCE_ID, TEXT_SCOPE = 'den-sim', 'den-sim-direct', 'den_sim_la_direct', 'reply_kept_render_only'
FAKE_ROOT, NOT_SEEN = 'fake-vllm', 'Qwen has not seen these exact frames'
REPLY_FIELDS = {'asked_unix', 'host', 'root', 'http_status', 'raw', 'elapsed_s'}
# Hop 17: the coordinator's spot mode on hop 16's four channels (plan, hop 17).
CHANNELS, SPOT_DOMAIN = ['F1.spot', 'F2.spot', 'F3.spot', 'F4.spot'], 'virtual-den-4'
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


def archive_trees(refs, tools_root, keys=None):
    """One folder per pinned ref, written once. A folder whose marker is missing, or names other paths, is rebuilt."""
    trees = {}
    for key, (ref_name, paths) in TREES.items():
        if keys and key not in keys:
            continue
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


def spot_rule(core_tree):
    """den-core's spot_rule.py at its pin, from its archive by file path: the lighting brain's aim.py on a PersonState."""
    spec = importlib.util.spec_from_file_location('den_sim_spot_rule', Path(core_tree) / 'tools' / 'den-core' / 'spot_rule.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def step_startup(request):
    """The startup line, with your two files saved: each fixture of hop 16's light model against the stated facing and the
    spot, through spot_rule.py, in words only."""
    core = archive_trees(request['refs'], request['tools_root'], keys=('core',))['core']
    fixtures = json.loads(Path(request['light_model']).read_text(encoding='utf-8'))['fixtures']
    return dict(startup=spot_rule(core).startup_line(fixtures, request['seat_m'], request['facing_unit'], request['spot_m']))


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


def well_formed(reply):
    """The rule a reply file must meet, here and in ask_la.py's accept(), which files only what meets it: the six
    fields, a root that names a model, and the seconds it took. test_ask.py holds the two to the same cases."""
    return (isinstance(reply, dict) and set(reply) == REPLY_FIELDS and isinstance(reply['root'], str) and reply['root'] != ''
            and type(reply['elapsed_s']) in (int, float) and reply['elapsed_s'] >= 0)


def read_reply(path):
    try:
        reply = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        reply = None
    if not well_formed(reply):
        raise Refused('the reply on file for body %s is not the six fields ask_la.py files' % Path(path).stem[:12])
    return reply


def body_images(body):
    """The JPEGs a body carries, in order: the very bytes Qwen is sent, for the panel to show."""
    parts = json.loads(body)['messages'][1]['content']
    return [base64.b64decode(part['image_url']['url'].split(',', 1)[1]) for part in parts if part['type'] == 'image_url']


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
    asks, lines, missing, malformed, filed, roots, days = [], [], [], [], [], set(), set()
    try:
        session = session_of(request['take'])
        for row, body, stills in bodies(request, np, Image, qwen_live, scene):
            asks.append(row)
            if body is None:
                continue
            if any(other['body_sha256'] == row['body_sha256'] for other in asks[:-1]):
                raise Refused('two requests have the same body, %s' % row['body_sha256'][:12])
            if not request.get('owed'):   # the stills this body sends, by their sha256, for the panel: a display, read by no hop
                (work / 'stills').mkdir(exist_ok=True)
                for image in body_images(body):
                    (work / 'stills' / (hashlib.sha256(image).hexdigest() + '.jpg')).write_bytes(image)
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
            try:
                reply = read_reply(path)
            except Refused:   # hop 12's to say: the bodies were built, and this file is not a reply ask_la.py files
                row.update(status='malformed')
                malformed.append(row['body_sha256'])
                continue
            line, receipt = reply_line(scene, la_fact_log, row, stills, reply, session, request['take_digest'])
            row.update(root=reply['root'], status=receipt['status'], facts=receipt['facts'], invalid_reason=receipt['invalid_reason'], elapsed_s=reply['elapsed_s'])
            roots.add(reply['root'])
            days.add(time.strftime('%Y-%m-%d', time.localtime(reply['asked_unix'])) if type(reply['asked_unix']) in (int, float) else 'an unknown day')
            lines.append(line)
    except Refused as why:
        return dict(refused=str(why), asks=asks)
    (work / 'asks.jsonl').write_text(''.join(json.dumps(row, sort_keys=True, allow_nan=False) + '\n' for row in asks), encoding='utf-8', newline='\n')
    if lines and not missing and not malformed:
        (work / 'facts.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    return dict(refused=None, asks=asks, missing=missing, malformed=malformed, filed=filed, roots=sorted(roots), days=sorted(days), lines=len(lines), pillow=PIL.__version__,
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


def step_person(request):
    """Hop 15: person.py on this click's files, need v0 loaded by file path from den-core's archive, one per track.
    A room model with no couch reads MISSING, with why."""
    import person
    work = Path(request['work'])
    need = person.load_need(Path(request['core_tree']) / 'tools' / 'den-core' / 'need.py')
    try:
        records, cards = person.states(read_jsonl(work / 'asks.jsonl'), read_jsonl(work / 'bundles.jsonl'), read_jsonl(work / 'facts.jsonl'),
                                       read_jsonl(work / 'track' / 'tracks.jsonl'), read_jsonl(work / 'observe' / 'observations.jsonl'),
                                       read_jsonl(request['room_model']), need)
    except person.NoCouch as why:
        return dict(missing=str(why))
    person.write(work / 'person_states.jsonl', records)
    return dict(missing=None, states=len(records), tracks=cards)


def person_words(card):
    """One track's card in words, with no coordinate: hop 15's row and the console say this."""
    if card['on_couch'] is None:
        couch = 'on the couch: not tested, %s' % (('the Track was not confident' if card.get('held_why') == 'track' else 'the bundle was not safe to tie to one person') if card['held'] else 'no position')
    elif card['on_couch']:
        couch = 'on the couch: yes'
    else:
        couch = 'on the couch: no, the %s failed' % ' and '.join({'position': 'position half', 'height': 'height half', 'torso': 'height half (no supported shoulder or hip)'}[f]
                                                              for f in card['failed'])
    distance = '%.2f m from the seat point' % card['seat_distance_m'] if card['seat_distance_m'] is not None else 'no position'
    return '%s %s, %s, spot %s, engagement %s' % (card['track_id'], distance, couch, card['region'] or 'none', card['engagement'])


def need_words(cards):
    """Hop 13's row: need v0 runs once per track, in person.py, on only the replies its bundle was safe to tie to one person."""
    tracks = []
    for card in cards:
        held = card['replies'] - card['need_replies']
        tracks.append('%s took %d of %d replies%s: %s' % (card['track_id'], card['need_replies'], card['replies'],
                                                         ', %d held as not safe to tie to one person' % held if held else '', ' then '.join(card['need_states']) or 'no change'))
    return 'need v0 once per track, in person.py (hop 15), on the replies its bundle was safe to take: %s. Nothing else runs need on a click.' % ('; '.join(tracks) or 'not run')


def first_confirmed(tracks, observations):
    """Hop 10: the time of the first confirmed Track line, dated by the newest capture_ts of the observations it names, as
    person.py dates a Track line, which carries no time of its own. None when no track confirmed."""
    seen = {obs['obs_id']: obs for obs in observations}
    times = [(max(seen[oid]['capture_ts'] for oid in line['last_obs_ids']), line['track_id']) for line in tracks
             if line.get('state') == 'confirmed' and line.get('last_obs_ids') and all(oid in seen for oid in line['last_obs_ids'])]
    return min(times) if times else (None, None)


def step_decide(request):
    """Hop 17: rung 9's coordinator in spot mode, once, on hop 10's occupancy and hop 15's person states, through den-core's
    real runner with the plant off and no tail (plan, hop 17's In); then events.write, coordinator_outputs in a new folder,
    with its two printed lines kept off stdout, and check.py's replay of the log. A click with no Decision still writes an
    empty decisions.jsonl. Returns the Decisions' words, never a coordinate."""
    sys.path.insert(0, str(Path(request['core_tree']) / 'tools' / 'den-core'))
    import check
    import coordinator
    import events
    import run
    import runner
    items = [(row['t'], row['kind'], row['data']) for row in read_jsonl(request['occupancy'])]
    items += [(state['emitted_ts'], 'person_state', state) for state in read_jsonl(request['person_states'])]
    items.sort(key=lambda item: (item[0], item[1] != 'occupancy'))   # occupancy first when the times are equal
    policy = coordinator.make_policy(dict(channels=CHANNELS, actuator_domain=SPOT_DOMAIN, light_model=dict(source='file', path=request['light_model'])))
    started = time.perf_counter()
    header, log = runner.run(runner.tagged(items, 'synthetic'), [policy], synthetic=True, tail_s=0, plant=False)
    ms = (time.perf_counter() - started) * 1000
    out = Path(request['out'])
    out.mkdir()
    events.write(out / 'log.jsonl', header, log)
    with contextlib.redirect_stdout(io.StringIO()):
        run.coordinator_outputs(out, log, policy.light)
    checked = check.check(out / 'log.jsonl')
    words = coordinator._spot_rule().words
    decisions = read_jsonl(out / 'decisions.jsonl')
    return dict(checked=dict(ok=checked['ok'], reason=checked['reason']), events=len(items), lines=len(log) + 1, ms=round(ms, 1), model=policy.light.version,
                decisions=[decision_view(d, words) for d in decisions])


def decision_view(decision, words):
    """One Decision in words for the card: what it lit and at what level against the target, and each refusal."""
    lit = [channel for channel in CHANNELS if decision['requested_scene'][channel]['on']]
    target = next(iter(decision['targets'].values()), None)
    return dict(decision_id=decision['decision_id'], trigger=decision['trigger'], lit=lit, level=[decision['requested_scene'][c]['level'] for c in lit],
                predicts=decision['predicted']['surfaces'].get('sofa-book'), asked=target and target['value'], note=target and target['note'],
                readers=sorted(decision['targets']), refusals=[dict(channel=row['channel'], track_id=row['track_id'], reason=row['reason'], words=words(row['reason']))
                                                           for row in decision['skipped']],
                costlier=sorted({c for row in decision['candidates'] if row['why'] != 'chosen' for c in row['scene']} - set(lit)),
                steps={c: [step['kind'] for step in decision['applied_scene'][c]['steps']] for c in CHANNELS if decision['applied_scene'][c]['steps']},
                why=decision['why'])


def no_reader(cards):
    """Why the coordinator wrote no Decision, read from the PersonStates (hop 15's cards): nobody was reading."""
    said = ['%s: engagement %s%s' % (card['track_id'], card['engagement'], '' if card['on_couch'] is None else ', on the couch %s' % ('yes' if card['on_couch'] else 'no'))
            for card in cards]
    return 'nobody is reading (%s)' % ('; '.join(said) or 'no person state')


def no_beam(view):
    """Why a Decision lit nothing: every fixture refused, or no reader at a spot."""
    if view['refusals'] and view['readers']:
        return 'every fixture refused: %s' % ', '.join('%s %s' % (row['channel'].split('.')[0], row['words']) for row in view['refusals'])
    return 'no beam: %s' % view['why'].split(': ', 1)[-1].rstrip('.')


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
    room_read = dict(path=room.get('label') or '%s/room-model.jsonl' % Path(room['path']).parent.name, sha256=sha256_file(room['path']),
                     recorded=dict(kind='room model', by=room['by'], sha256=room['sha256']))
    emit(7, 'REAL', 'replay.py wrote %d observations, unscored: %d open slots on proposed values, on rendered depth.'
         % (run_json['gate']['records'], len(run_json['method']['open_slots'])), started,
         tools=observe_tools, read=[take_read, record_read, check_read, room_read, rows_read],
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

    # Hop 10: one occupancy event at the first confirmed track, from the track list. No track confirmed, no event, and the
    # coordinator is never run (plan, hop 10).
    at(10)
    first, track = first_confirmed(lines, read_jsonl(observe / 'observations.jsonl'))
    ten = dict(tools=head_tools(*own), read=[click.read(observe / 'observations.jsonl', 7), click.read(work / 'track' / 'tracks.jsonl', 8)], stand_ins=named(10, 0))
    if first is None:
        emit(10, 'ADAPTER', 'No track confirmed, so no occupancy event: the coordinator is not run.', wrote=[], **ten)
        raise Stop(10, 'no track confirmed, so nobody is in the room for the coordinator', answer=True)
    (work / 'occupancy.jsonl').write_text(json.dumps(dict(t=first, kind='occupancy', data=dict(value='on', via='den-track')), sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    emit(10, 'ADAPTER', 'One occupancy event, on via den-track, at the first confirmed Track line (%s), dated by the observations it names: what the '
         'coordinator reads as someone in the room. The live source, Frigate on a Pi with Hailo, has no virtual stream.' % track, wrote=[click.file(work / 'occupancy.jsonl')], **ten)
    # Hops 11 and 12: each request's exact body, rebuilt from the take's own pixels, and its recorded reply by
    # the body's sha256. A failure in that process is hop 11's.
    at(11)
    started = time.monotonic()
    asked = click.own_step('ask', dict(core_tree=str(trees['core']), take=str(take), take_digest=request['take_digest'], run_json=str(observe / 'run.json'),
                                       requests=str(work / 'requests.jsonl'), bundles=str(work / 'bundles.jsonl'), replies=request['replies'],
                                       work=str(work), owed=request.get('owed')))
    qwen_tools = [tool for tool in tools['core'] if tool['path'].startswith(LAB)]
    # The ask itself is recorded: once replies are used, ask_la.py and ask_one.py are the ones the paste that filed them
    # ran, as last-ask.json recorded them. Before that they are this branch's, at HEAD, the ones a paste would run.
    last = read_last_ask(request.get('last_ask'))
    used = [row['body_sha256'] for row in asked.get('asks') or [] if row.get('root') is not None]
    asking = paste_tools(last, used) if used and not asked.get('missing') else head_tools(*PASTE_TOOLS)
    eleven = dict(tools=head_tools(*own) + asking + qwen_tools, wrote=[], stand_ins=named(11, 0, 1, 2),
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
    days = asked['days']
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
    if asked['malformed']:
        why = 'the reply on file for body %s is not the six fields ask_la.py files' % asked['malformed'][0][:12]
        emit(12, 'MISSING', '%s%s: an agent removes it, and the paste asks again. Need gets no input, so the beam stays off.' % (
             why[0].upper() + why[1:], ' (and %d more)' % (len(asked['malformed']) - 1) if len(asked['malformed']) > 1 else ''), note=why, **twelve, stand_ins=[])
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

    # Hop 13 and 15: person.py, with need v0 once per track (the runner's pass fed only the older brain, and went with it).
    at(15)
    started = time.monotonic()
    core_tools = [t for t in tools['core'] if t['path'].startswith('tools/den-core/')]
    try:
        person = click.own_step('person', dict(core_tree=str(trees['core']), work=str(work), room_model=room['path']))
    except Stop as failed:
        person = dict(missing=failed.why)
    person_ms = round((time.monotonic() - started) * 1000)
    emit(13, 'ADAPTER', need_words(person.get('tracks') or []), ms=person_ms, tools=[t for t in core_tools if t['path'] == 'tools/den-core/need.py'] + head_tools('tools/den-sim/person.py'),
         read=[click.read(work / 'facts.jsonl', 12)], wrote=[], stand_ins=named(13, 0))
    fifteen = dict(tools=head_tools(*own, 'tools/den-sim/person.py') + [t for t in core_tools if t['path'] == 'tools/den-core/need.py'],
                   read=[click.read(observe / 'observations.jsonl', 7), click.read(work / 'track' / 'tracks.jsonl', 8), click.read(work / 'bundles.jsonl', 9),
                         click.read(work / 'asks.jsonl', 12), click.read(work / 'facts.jsonl', 12), room_read])
    if person['missing']:
        emit(15, 'MISSING', 'No person state: %s.' % person['missing'], started, note=person['missing'], wrote=[], stand_ins=[], **fifteen)
        raise Stop(15, person['missing'])
    emit(15, 'ADAPTER', 'person.py wrote %d person states in canon\'s names, from need v0 per track and rules on the bundles\' joints, unscored: %s. The coordinator '
         'reads them (hop 17).' % (person['states'], '; '.join(map(person_words, person['tracks'])) or 'no reply to state'), started,
         wrote=[click.file(work / 'person_states.jsonl')], stand_ins=named(15, 0, 1), person=person['tracks'], **fifteen)

    # Hop 17: the coordinator in spot mode, once, on the light model hop 16 built at page start.
    at(17)
    started = time.monotonic()
    model = request.get('light_model') or {}
    if not model.get('path'):
        raise Stop(17, 'no light model was built at page start, so the coordinator has nothing to decide with')
    model_read = dict(path=model['label'], sha256=sha256_file(model['path']) if Path(model['path']).is_file() else None,
                      recorded=dict(kind='light model', by='light_model.mjs at page start', sha256=model['sha256']))
    decided = click.own_step('decide', dict(core_tree=str(trees['core']), occupancy=str(work / 'occupancy.jsonl'), person_states=str(work / 'person_states.jsonl'),
                                            light_model=model['path'], out=str(work / 'decide')))
    brain = [t for t in tools['core'] if t['path'].startswith('stack/services/lighting-brain/')]
    seventeen = dict(tools=head_tools(*own) + core_tools + brain, read=[click.read(work / 'occupancy.jsonl', 10), click.read(work / 'person_states.jsonl', 15), model_read],
                     wrote=[click.file(work / 'decide' / name) for name in ('log.jsonl', 'decisions.jsonl', 'surfaces.json')], stand_ins=named(17, 0, 1),
                     decisions=decided['decisions'], checked=decided['checked'], model=decided['model'])
    replay = 'check.py: %s' % decided['checked']['reason']
    if not decided['checked']['ok']:
        emit(17, 'MISSING', 'The coordinator ran, and its log did not replay: %s.' % replay, started, note=replay, **seventeen)
        raise Stop(17, replay)
    views, ran = decided['decisions'], 'rung 9\'s coordinator in spot mode (virtual-den-4, synthetic) ran once on %d events in %.0f ms, %s' % (
        decided['events'], decided['ms'], replay)
    lit = [view for view in views if view['lit']]
    if not views:
        why = no_reader(person['tracks'])
        emit(17, 'ADAPTER', '%s, and wrote no Decision: %s, so every spot stays off.' % (ran, why), started, **seventeen)
        raise Stop(17, why, answer=True)
    last = views[-1]
    if not lit:
        why = no_beam(last)
        emit(17, 'ADAPTER', '%s. %d Decision%s, no beam: %s' % (ran, len(views), '' if len(views) == 1 else 's', last['why']), started, **seventeen)
        raise Stop(17, why, answer=True)
    chosen = lit[-1]
    emit(17, 'ADAPTER', '%s. %d Decision%s: %s' % (ran, len(views), '' if len(views) == 1 else 's', chosen['why']), started, **seventeen)
    states = read_jsonl(work / 'person_states.jsonl')
    placed = [state['position']['xyz'][:2] for state in states if state.get('position')]
    print(json.dumps(dict(hop='done', ok=True, camera_m=camera_position(room['path']), reply='fake' if root == FAKE_ROOT else 'real', root=root,
                          person_m=placed[-1] if placed else None, decide='decide', work=str(work)), allow_nan=False), flush=True)


def read_last_ask(path):
    """last-ask.json, which ask_la.py writes: what each paste filed, by body hash, with each reply's digest and the
    commit and blobs of ask_la.py and ask_one.py the paste that filed it ran."""
    try:
        last = json.loads(Path(path).read_text(encoding='utf-8'))
        return last if isinstance(last.get('filed'), dict) else None
    except (OSError, TypeError, ValueError, AttributeError):
        return None


PASTE_TOOLS = ('tools/den-sim/ask_la.py', 'tools/den-sim/ask_one.py')


def paste_tools(last, names):
    """Hop 11's recorded tools: for the pastes that filed these replies, the blobs of ask_la.py and ask_one.py each ran,
    as last-ask.json recorded them, against their blobs at the commit it ran at, which must be on this branch. An
    uncommitted edit at the paste, or a paste that recorded none, shows."""
    runs = {}
    for name in names:
        entry = ((last or {}).get('filed') or {}).get(name) or {}
        runs.setdefault((entry.get('head') or '', json.dumps(entry.get('blobs'), sort_keys=True)), entry)
    out = []
    for (head, _), entry in sorted(runs.items()):
        if not re.fullmatch(r'[0-9a-f]{40}', head):
            out += [dict(path=path, pin='last-ask.json', expected=None, actual=None, why='was not recorded by the paste that filed a reply') for path in PASTE_TOOLS]
            continue
        ours = subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', head, 'HEAD'], capture_output=True).returncode == 0
        expected = {line.split('\t', 1)[1]: line.split()[2] for line in git_lines('ls-tree', head, '--', *PASTE_TOOLS)} if ours else {}
        blobs = entry.get('blobs') if isinstance(entry.get('blobs'), dict) else {}
        out += [dict(path=path, pin='the commit the paste ran at (%s)' % head[:8], expected=expected.get(path), actual=blobs.get(path),
                     **({} if ours else dict(why='ran at %s, which is not on this branch' % head[:8]))) for path in PASTE_TOOLS]
    return out


def said_by(row):
    facts = row['facts'] or {}
    if row['status'] == 'valid':
        return '%s, holding %s, people %s' % (facts['activity_hypothesis'], facts['held_object'], facts['people_visible'])
    if row['status'] == 'malformed':
        return 'a reply file that is not the six fields ask_la.py files'
    return 'invalid (%s)' % row['invalid_reason'] if row['status'] else 'no reply on file' if row['body_sha256'] else None


def words(rows):
    """What Qwen said, request by request, from the facts: its activity and held object, or why a reply is invalid."""
    return '; '.join('%s %s' % (row['request_id'], said_by(row)) for row in rows)


def asks_view(rows):
    """The panel's view of asks.jsonl: per request its stills by frame and by their JPEG's sha256 (the click wrote each
    under stills/), how far each is before the newest, and Qwen's words, with all of its fields."""
    return [dict(request_id=row['request_id'], association_safe=row['association_safe'], body=row['body_sha256'] and row['body_sha256'][:12],
                 stills=[still['rgb_seq'] for still in row['stills'] or []], jpegs=[still['jpeg_sha256'] for still in row['stills'] or []],
                 not_asked=row['not_asked'], said=said_by(row), facts=row['facts'],
                 seconds=[round(row['stills'][-1]['observed_at'] - still['observed_at'], 1) for still in row['stills'] or []]) for row in rows]


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--guarded':
        script = sys.argv[2]
        sys.argv = sys.argv[2:]
        sys.path.insert(0, str(Path(script).parent))
        runpy.run_path(script, run_name='__main__')
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--step':
        print(json.dumps(dict(ask=step_ask, decide=step_decide, person=step_person, picture=step_picture, startup=step_startup)[sys.argv[2]](json.load(sys.stdin)), allow_nan=False))
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
