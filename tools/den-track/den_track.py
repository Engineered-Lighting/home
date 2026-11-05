"""den-track: rung 3's geometry-only tracker and E2's scorer.

    python den_track.py method
    python den_track.py hash --file OBSERVATIONS
    python den_track.py practice [--seeds 5] [--noise typical]
    python den_track.py synth --out DIR [--seeds 1] [--noise typical]
    python den_track.py track --session DIR
    python den_track.py score --session DIR [--practice]

`track` reads each take folder's observations.jsonl and writes tracks.jsonl
next to it. `score` pools every take's windows into E2's gate; without
--practice it refuses while METHOD.md has an open slot or any take was
tracked under another method. Nothing here opens a network connection, a
camera, Home Assistant or a light. Output is plain ASCII, so it reads the
same in Windows PowerShell 5.1.

Exit codes: 0 done (score: gate passed), 1 score computed but gate not met,
2 refused or an input error.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import method as method_mod    # noqa: E402
import pipeline                # noqa: E402
import records                 # noqa: E402
import synth                   # noqa: E402
import truth as truth_mod      # noqa: E402

if sys.version_info < (3, 8):
    sys.exit('den-track needs Python 3.8 or newer')


def _pct(x):
    return 'n/a' if x is None else '%.1f%%' % (100 * x)


def _num(x, nd=3):
    return 'n/a' if x is None else ('%.' + str(nd) + 'f') % x


def cmd_method(args, m):
    print('method   %s' % m.version)
    print('path     %s' % m.path)
    print('print    %s' % m.fingerprint)
    if not m.open:
        print('open slots: none (scored runs allowed)')
        return 0
    print('open slots: %d (a scored run refuses until each is closed in METHOD.md)' % len(m.open))
    for name, slot in m.open:
        print('  %s' % name)
        print('    proposed: %s' % json.dumps(slot.get('proposed'), sort_keys=True))
        print('    why: %s' % slot.get('why', ''))
    return 0


def cmd_hash(args, m):
    """The hash a truth file names in observations_sha256: of the file's lines
    with LF endings, so a CRLF copy hashes the same."""
    print(records.file_sha256(args.file))
    return 0


def cmd_synth(args, m):
    scenes = args.scenes.split(',') if args.scenes else sorted(synth.SCENES)
    for name in scenes:
        if name not in synth.SCENES:
            raise pipeline.RunError('no synthetic scene %r (have: %s)' % (name, ', '.join(sorted(synth.SCENES))))
    os.makedirs(args.out, exist_ok=True)
    n = 0
    for name in scenes:
        for seed in range(args.first_seed, args.first_seed + args.seeds):
            take = os.path.join(args.out, '%s-%d-%s' % (name, seed, args.noise))
            pipeline.write_synthetic_take(take, synth.scenario(name, seed, args.noise, fps=args.fps))
            n += 1
    print('wrote %d synthetic take folder(s) under %s' % (n, args.out))
    return 0


def cmd_track(args, m):
    if m.open:
        print('note: %d open slot(s) in METHOD.md; tracks use the proposed values and cannot be scored'
              % len(m.open))
    for d in pipeline.take_dirs(args.session):
        run = pipeline.track_take(d, m)
        skipped = sum(v for k, v in run['observation_counts'].items() if k.startswith('skipped_'))
        print('%-40s frames %5d  used %5d  skipped %4d  tracks %3d  observations %s'
              % (os.path.basename(d), run['frames'], run['observation_counts']['used'], skipped,
                 run['tracks'], run['observations_sha256'][:19]))
    return 0


def print_report(rep):
    head = 'PRACTICE: not a score' if rep.get('practice') else 'SCORED'
    print('E2 geometry-only arm: %s' % head)
    if rep.get('method_fingerprint'):
        print('method %s %s' % (rep.get('method_version'), rep['method_fingerprint']))
    if rep.get('open_slots'):
        print('open slots (proposed values used): %s' % ', '.join(rep['open_slots']))
    for p in rep.get('problems') or []:
        print('problem: %s' % p)
    print('takes %d, windows %d' % (len(rep['takes']), rep['crossings']))
    print('silent swaps: %d (windows with one: %d; outside windows: %d)'
          % (rep['silent_swaps'], rep['crossings_with_silent_swap'], rep['silent_swaps_outside_windows']))
    print('reported switches (made while ambiguous): %d' % rep['reported_switches'])
    print('pooled IDF1: %s (gate %s)' % (_num(rep['pooled']['idf1']), rep['gate']['idf1_at_least']))
    print('one-sided %d%% upper bound on the silent-swap rate: %s (%d of %d windows failed)'
          % (round(100 * (1 - rep['alpha'])), _pct(rep['silent_swap_rate_upper_95']),
             rep['crossings_with_silent_swap'], rep['crossings']))
    print('ambiguous share of linked observations in windows: %s' % _num(rep['ambiguous_share'], 2))
    g = rep['gate']
    if g['passed']:
        print('gate: PASSED')
    else:
        why = []
        if not g['silent_swaps_zero']:
            why.append('silent swaps')
        if not g['idf1_met']:
            why.append('IDF1 below %s' % g['idf1_at_least'])
        if not g['windows_met']:
            why.append('fewer than %d windows' % g['windows_at_least'])
        print('gate: NOT MET (%s)' % ', '.join(why))
    print('')
    print('%-36s %-8s %-18s %6s %6s %8s %9s' % ('take', 'window', 'kind', 'IDF1', 'silent', 'reported', 'ambiguous'))
    for r in rep['windows']:
        print('%-36s %-8s %-18s %6s %6d %8d %9s' % (r['take'][:36], r['id'][:8], (r['kind'] or '')[:18],
                                                   _num(r['idf1']), r['silent_swaps'], r['reported_switches'],
                                                   _num(r['ambiguous_share'], 2)))


def cmd_score(args, m):
    rep = pipeline.score_session(args.session, m, practice=args.practice)
    out = args.out or os.path.join(args.session, 'e2-report.json')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(rep, f, indent=2, sort_keys=True)
        f.write('\n')
    print_report(rep)
    print('')
    print('full report: %s' % out)
    return 0 if rep['gate']['passed'] else 1


def cmd_practice(args, m):
    values = dict(m.values, room=synth.ROOM_MODEL)
    scenes = args.scenes.split(',') if args.scenes else None
    rep = pipeline.practice(values, scenes=scenes, seeds=args.seeds, noise=args.noise,
                            first_seed=args.first_seed, fps=args.fps)
    rep.update({'method_version': m.version, 'method_fingerprint': m.fingerprint,
                'open_slots': [name for name, _ in m.open]})
    print_report(rep)
    print('')
    print('%-26s %7s %6s %8s %6s %9s' % ('scene', 'windows', 'failed', 'reported', 'IDF1', 'ambiguous'))
    for name, k in sorted(rep['by_scene'].items()):
        print('%-26s %7d %6d %8d %6s %9s' % (name, k['windows'], k['failed'], k['reported'], _num(k['idf1']),
                                            _num(k['ambiguous_share'], 2)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='den_track.py', description=__doc__.split('\n\n')[0])
    ap.add_argument('--method', default=method_mod.METHOD_PATH, help='METHOD.md to read (default: next to this file)')
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('method', help='print the method version, fingerprint and open slots')
    p = sub.add_parser('hash', help='print the observations_sha256 a truth file names, without tracking')
    p.add_argument('--file', required=True)
    p = sub.add_parser('synth', help='write synthetic take folders (observations and truth)')
    p.add_argument('--out', required=True)
    p.add_argument('--seeds', type=int, default=1)
    p.add_argument('--first-seed', type=int, default=0)
    p.add_argument('--noise', default='typical', choices=sorted(synth.NOISE))
    p.add_argument('--fps', type=float, default=4.0)
    p.add_argument('--scenes', help='comma-separated scene names (default: all)')
    p = sub.add_parser('track', help='track every take folder in a session')
    p.add_argument('--session', required=True)
    p = sub.add_parser('score', help='score a tracked session against its truth files')
    p.add_argument('--session', required=True)
    p.add_argument('--practice', action='store_true', help='allow open slots; the report says it is not a score')
    p.add_argument('--out', help='where to write the full JSON report (default: SESSION/e2-report.json)')
    p = sub.add_parser('practice', help='synthetic E2 in memory, with the synthetic den: the tuning harness')
    p.add_argument('--seeds', type=int, default=5)
    p.add_argument('--first-seed', type=int, default=0)
    p.add_argument('--noise', default='typical', choices=sorted(synth.NOISE))
    p.add_argument('--fps', type=float, default=4.0)
    p.add_argument('--scenes', help='comma-separated scene names (default: all)')
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        m = method_mod.Method(args.method)
        return {'method': cmd_method, 'hash': cmd_hash, 'synth': cmd_synth, 'track': cmd_track, 'score': cmd_score,
                'practice': cmd_practice}[args.cmd](args, m)
    except (method_mod.MethodError, pipeline.RunError, records.RecordError, truth_mod.TruthError, OSError) as e:
        print('den-track: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
