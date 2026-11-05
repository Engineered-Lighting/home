"""den-track, rung 3: Tracks from geometry, and E2's scorer.

    python -B tools\\den-track\\den_track.py track --observations <file> --out <folder>
    python -B tools\\den-track\\den_track.py score --take <observations> <tracks> <truth> --out <folder>
    python -B tools\\den-track\\den_track.py practice
    python -B tools\\den-track\\den_track.py synth --scene near_pass --out <folder>
    python -B tools\\den-track\\den_track.py slots

Exit codes: 0 written or passed, 1 the gate failed, 2 refused (nothing written).
It reads and writes files on this PC and never contacts anything.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import method as method_mod     # noqa: E402
import records                  # noqa: E402
import score as score_mod       # noqa: E402
import synth                    # noqa: E402
import truth as truth_mod       # noqa: E402

EXIT_OK, EXIT_GATE, EXIT_REFUSED = 0, 1, 2
TRACKS_FILE, TRACK_RUN_FILE = 'tracks.jsonl', 'track-run.json'
SCORE_FILE, CROSSINGS_FILE = 'score.json', 'crossings.jsonl'


class Refused(Exception):
    pass


def text_sha256(path):
    """Over the file's text, so CRLF and LF hash alike."""
    with open(path, encoding='utf-8-sig', newline=None) as f:
        return 'sha256:' + hashlib.sha256(f.read().encode('utf-8')).hexdigest()


def inside_private_risk(path):
    """Why a folder may not hold output: inside a git checkout, or under OneDrive."""
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, '.git')):
            return 'it is inside the git checkout at %s' % p
        if 'onedrive' in os.path.basename(p).lower():
            return 'it is under OneDrive'
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def check_out_folder(path, names):
    why = inside_private_risk(path)
    if why:
        raise Refused('refusing --out %s: %s. Output stays outside git and OneDrive.' % (path, why))
    for name in names:
        if os.path.exists(os.path.join(path, name)):
            raise Refused('refusing --out %s: it already holds %s, and nothing is overwritten.' % (path, name))
    os.makedirs(path, exist_ok=True)


def write_json(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write('\n')


def method_stamp(method):
    return {'version': method.version, 'fingerprint': method.fingerprint,
            'open': [name for name, _ in method.open]}


def print_open(method):
    if method.open:
        print('UNSCORED: %d open slot(s) use their proposed values: %s'
              % (len(method.open), ', '.join(name for name, _ in method.open)))


def load_method(args):
    return method_mod.Method(args.method or method_mod.METHOD_PATH)


def real_room(method):
    room = method.room
    return room['floor_m'], room['exits_m']


# -- track ---------------------------------------------------------------------

def cmd_track(args):
    method = load_method(args)
    check_out_folder(args.out, (TRACKS_FILE, TRACK_RUN_FILE))
    observations = records.read_jsonl(args.observations)
    floor, exits = real_room(method)
    lines, counts = records.run_tracker(observations, method.tracker, floor=floor, exits=exits)
    tracks_path = os.path.join(args.out, TRACKS_FILE)
    records.write_jsonl(tracks_path, lines)
    write_json(os.path.join(args.out, TRACK_RUN_FILE), {
        'tool': 'den-track', 'command': 'track',
        'method': method_stamp(method),
        'observations_sha256': text_sha256(args.observations),
        'tracks_sha256': text_sha256(tracks_path),
        'counts': counts,
    })
    print('wrote %d track lines for %d tracks from %d frames to %s'
          % (counts['track_lines'], counts['tracks'], counts['frames'], tracks_path))
    print('left out: %d not a person, %d rejected, %d with no position'
          % (counts['not_person'], counts['rejected'], counts['no_position']))
    gap = counts['median_frame_gap_s']
    if gap:
        print('frames arrive about %.1f times a second; the method was tuned at 4 and is weak at 2' % (1.0 / gap))
    print_open(method)
    return EXIT_OK


# -- score ---------------------------------------------------------------------

def load_take(triple):
    obs_path, tracks_path, truth_path = triple
    truth = truth_mod.read(truth_path)
    name = truth.take if isinstance(truth.take, str) and truth.take else os.path.basename(os.path.dirname(truth_path))
    return {'name': name, 'observations': records.read_jsonl(obs_path), 'tracks': records.read_jsonl(tracks_path),
            'truth': truth, 'paths': triple}


def check_scored(method, takes, report):
    """Everything a scored run needs that a practice run does not. Raises Refused."""
    try:
        method.require_closed()
    except method_mod.MethodError as e:
        raise Refused(str(e))
    problems = []
    for take in takes:
        obs_path, tracks_path, _ = take['paths']
        run_path = os.path.join(os.path.dirname(tracks_path), TRACK_RUN_FILE)
        if not os.path.exists(run_path):
            problems.append('%s: no %s beside its tracks' % (take['name'], TRACK_RUN_FILE))
            continue
        with open(run_path, encoding='utf-8-sig') as f:
            run = json.load(f)
        if run.get('method', {}).get('fingerprint') != method.fingerprint:
            problems.append('%s: its tracks were made under another method; run track again' % take['name'])
        if run.get('observations_sha256') != text_sha256(obs_path):
            problems.append('%s: its observations are not the ones tracked' % take['name'])
        if run.get('tracks_sha256') != text_sha256(tracks_path):
            problems.append('%s: its tracks changed after they were written' % take['name'])
    need = method.scoring['min_crossings']
    if report['crossings'] < need:
        problems.append('%d crossings, and a scored run needs at least %d' % (report['crossings'], need))
    if report['left_out']['unlabelled']:
        problems.append('%d observations have no label' % report['left_out']['unlabelled'])
    empty = [r['id'] for r in report['windows'] if not r['observations']]
    if empty:
        problems.append('crossings with no labelled observation: %s' % ', '.join(empty))
    for take in takes:
        rows = sorted((r for r in report['windows'] if r['take'] == take['name']), key=lambda r: r['start'])
        for a, b in zip(rows, rows[1:]):
            if b['start'] <= a['end']:
                problems.append('%s: crossings %s and %s overlap once padded, so they are not two crossings'
                                % (take['name'], a['id'], b['id']))
    if problems:
        raise Refused('refusing a scored run: ' + '; '.join(problems))


def fmt(v, spec='%.3f'):
    return '-' if v is None else spec % v


def print_report(report):
    print('%-24s %-18s %5s %7s %6s %7s %8s %6s' % ('take', 'crossing', 'obs', 'switches', 'silent', 'IDF1', 'contact', 'face'))
    for r in report['windows']:
        print('%-24s %-18s %5d %7d %6d %7s %8s %6s' % (
            r['take'][:24], ('%s %s' % (r['id'], r['kind']))[:18], r['observations'], r['switches'], r['silent_swaps'],
            fmt(r['idf1']), fmt(r['contact_ambiguous_share'], '%.2f'), fmt(r['face_visible_share'], '%.2f')))
    pooled = report['pooled']
    print('crossings %d, with a silent swap %d; 95%% upper bound on the silent-swap rate: %s'
          % (report['crossings'], report['crossings_with_silent_swap'], fmt(report['silent_swap_rate_upper_95'])))
    print('silent swaps %d, flagged switches %d; outside crossings: %d switches, %d silent'
          % (report['silent_swaps'], report['flagged_switches'], report['switches_outside_crossings'],
             report['silent_swaps_outside_crossings']))
    print('IDF1 pooled over crossings %s; whole takes %s' % (fmt(pooled['idf1']), fmt(report['whole_take']['idf1'])))
    print('ambiguous share outside crossings: %s; face visible (stand-in), mean per crossing: %s'
          % (fmt(report['ambiguous_share_outside_crossings'], '%.2f'), fmt(report['face_visible_share_mean'], '%.2f')))


def cmd_score(args):
    method = load_method(args)
    check_out_folder(args.out, (SCORE_FILE, CROSSINGS_FILE))
    takes = [load_take(t) for t in args.take]
    names = [t['name'] for t in takes]
    if len(set(names)) != len(names):
        raise Refused('two takes share a name: %s' % names)
    scoring = method.scoring
    body = score_mod.score_session(takes, scoring)
    if args.scored:
        check_scored(method, takes, body)
    crossing_lines = body.pop('crossing_lines')
    report = dict(body, tool='den-track', command='score', scored=bool(args.scored), method=method_stamp(method),
                  labelling='a single labeller, the owner, blind to system output',
                  takes=[{'name': t['name'], 'observations_sha256': text_sha256(t['paths'][0]),
                          'tracks_sha256': text_sha256(t['paths'][1]), 'truth_sha256': text_sha256(t['paths'][2])}
                         for t in takes])
    write_json(os.path.join(args.out, SCORE_FILE), report)
    records.write_jsonl(os.path.join(args.out, CROSSINGS_FILE), crossing_lines)
    print_report(report)
    gate = report['gate']
    print('wrote %s' % os.path.join(args.out, SCORE_FILE))
    if not args.scored:
        print_open(method)
        print('PRACTICE: this is not a score. The gate would %s.' % ('pass' if gate['passed'] else 'fail'))
        return EXIT_OK
    print('SCORED under %s: the gate %s (0 silent swaps: %s; IDF1 >= %s: %s)'
          % (method.fingerprint, 'PASSED' if gate['passed'] else 'FAILED', gate['silent_swaps_zero'],
             gate['idf1_at_least'], gate['idf1_met']))
    return EXIT_OK if gate['passed'] else EXIT_GATE


# -- synth and practice --------------------------------------------------------

def scene_take(scene, seed, noise, fps, with_boxes):
    data = synth.scenario(scene, seed=seed, noise=noise, fps=fps)
    observations, frames, crossings = synth.to_records(data, with_boxes=with_boxes)
    truth = truth_mod.Truth('%s-%d' % (scene, seed), ['A', 'B'], {oid: p for _, row in frames for oid, p in row.items()},
                            {oid: ts for ts, row in frames for oid in row}, crossings)
    return observations, truth, frames, crossings


def synth_room():
    x0, x1 = synth.ROOM['x']
    y0, y1 = synth.ROOM['y']
    return [x0, y0, x1, y1], [list(synth.DOOR)]


def cmd_synth(args):
    check_out_folder(args.out, ('observations.jsonl', 'truth.jsonl'))
    observations, truth, frames, crossings = scene_take(args.scene, args.seed, args.noise, args.fps, args.with_boxes)
    records.write_jsonl(os.path.join(args.out, 'observations.jsonl'), observations)
    truth_mod.write(os.path.join(args.out, 'truth.jsonl'), truth.take, truth.people, frames, crossings)
    print('wrote %d synthetic observations and their truth to %s' % (len(observations), args.out))
    return EXIT_OK


def practice_rows(method, scenes, seeds, noise, fps, with_boxes=False):
    """Per scene: every seed tracked and scored. No file is read or written."""
    floor, exits = synth_room()
    rows = []
    for scene in scenes:
        takes, tracks = [], 0
        for seed in seeds:
            observations, truth, _, _ = scene_take(scene, seed, noise, fps, with_boxes)
            lines, counts = records.run_tracker(observations, method.tracker, floor=floor, exits=exits)
            tracks += counts['tracks']
            takes.append({'name': truth.take, 'observations': observations, 'tracks': lines, 'truth': truth})
        report = score_mod.score_session(takes, method.scoring)
        report['scene'] = scene
        report['tracks_per_run'] = tracks / len(seeds)
        rows.append(report)
    return rows


def parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        raise Refused('--set wants name=json, got %r' % text)


def cmd_practice(args):
    method = load_method(args)
    for item in args.set or []:
        name, _, value = item.partition('=')
        try:
            method.override(name, parse_value(value))
        except method_mod.MethodError as e:
            raise Refused(str(e))
    scenes = args.scene or sorted(synth.SCENES)
    seeds = range(args.first_seed, args.first_seed + args.seeds)
    rows = practice_rows(method, scenes, seeds, args.noise, args.fps, args.with_boxes)
    print('%-26s %5s %8s %7s %7s %8s %8s %7s %7s' % ('scene', 'runs', 'switches', 'silent', 'IDF1', 'contact', 'outside', 'tracks', 'face'))
    silent = 0
    for r in rows:
        contacts = [w['contact_ambiguous_share'] for w in r['windows'] if w['contact_ambiguous_share'] is not None]
        silent += r['silent_swaps'] + r['silent_swaps_outside_crossings']
        print('%-26s %5d %8d %7d %7s %8s %8s %7.1f %7s' % (
            r['scene'], r['crossings'], r['silent_swaps'] + r['flagged_switches'],
            r['silent_swaps'] + r['silent_swaps_outside_crossings'], fmt(r['pooled']['idf1']),
            fmt(sum(contacts) / len(contacts) if contacts else None, '%.2f'),
            fmt(r['ambiguous_share_outside_crossings'], '%.2f'), r['tracks_per_run'],
            fmt(r['face_visible_share_mean'], '%.2f')))
    print('noise %s, %g frames a second, %d silent swaps. Synthetic: never a score.' % (args.noise, args.fps, silent))
    print_open(method)
    return EXIT_OK


def cmd_slots(args):
    method = load_method(args)
    if not method.open:
        print('no open slots; method fingerprint %s' % method.fingerprint)
    for name, slot in method.open:
        print('%s: open, proposed %s\n    %s' % (name, json.dumps(slot.get('proposed')), slot.get('why')))
    return EXIT_OK


def parser():
    ap = argparse.ArgumentParser(prog='den_track.py', description='Rung 3: Tracks from geometry, and E2\'s scorer.')
    ap.add_argument('--method', help='another METHOD.md; the default is the one beside this file')
    sub = ap.add_subparsers(dest='command', required=True)

    p = sub.add_parser('track', help='observation lines in, track lines out')
    p.add_argument('--observations', required=True)
    p.add_argument('--out', required=True, help='a folder outside git and OneDrive')
    p.set_defaults(fn=cmd_track)

    p = sub.add_parser('score', help="E2's scorer, over one or more takes")
    p.add_argument('--take', nargs=3, action='append', required=True, metavar=('OBSERVATIONS', 'TRACKS', 'TRUTH'))
    p.add_argument('--out', required=True, help='a folder outside git and OneDrive')
    p.add_argument('--scored', action='store_true', help='a real score: refuses while anything is open')
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser('synth', help='write one synthetic scene as observations and truth')
    p.add_argument('--scene', required=True, choices=sorted(synth.SCENES))
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--noise', default='typical', choices=sorted(synth.NOISE))
    p.add_argument('--fps', type=float, default=4.0)
    p.add_argument('--with-boxes', action='store_true', help='a person_box beside each keypoints')
    p.add_argument('--out', required=True)
    p.set_defaults(fn=cmd_synth)

    p = sub.add_parser('practice', help='track and score synthetic scenes; writes nothing')
    p.add_argument('--scene', action='append', choices=sorted(synth.SCENES))
    p.add_argument('--seeds', type=int, default=20)
    p.add_argument('--first-seed', type=int, default=0)
    p.add_argument('--noise', default='typical', choices=sorted(synth.NOISE))
    p.add_argument('--fps', type=float, default=4.0)
    p.add_argument('--with-boxes', action='store_true')
    p.add_argument('--set', action='append', metavar='SECTION.NAME=JSON', help='try another value, for tuning')
    p.set_defaults(fn=cmd_practice)

    p = sub.add_parser('slots', help='list the open slots')
    p.set_defaults(fn=cmd_slots)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except (Refused, method_mod.MethodError, records.RecordError, truth_mod.TruthError, score_mod.ScoreError,
            OSError) as e:
        print('REFUSED: %s' % e, file=sys.stderr)
        return EXIT_REFUSED


if __name__ == '__main__':
    sys.exit(main())
