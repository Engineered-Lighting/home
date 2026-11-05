"""den-bundle: rung 5's person bundles and router v0, from the command line.

    python -B tools\\den-bundle\\den_bundle.py fixtures --out <folder>
    python -B tools\\den-bundle\\den_bundle.py assemble --observations F --tracks F --track A --at 7 --out F
    python -B tools\\den-bundle\\den_bundle.py route --observations F --tracks F --from 2 --to 70 --requests F --bundles F
    python -B tools\\den-bundle\\den_bundle.py gate --observations F --tracks F --bundles F [--crossings F] [--requests F] [--scored]

A time is Unix seconds, or, below 1e9, seconds after the first observation's
capture (`--at 7`).
`route` replays with a fake sender that answers `ok`; this tool has no real
sender and never talks to LA, the camera or anything else. Output files are
written by the tool itself (UTF-8, LF), so PowerShell 5.1's `>` is not needed.
Exit codes: 0 done or pass, 1 gate fail, 2 refused or bad input.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fakes      # noqa: E402
import gate       # noqa: E402
import method     # noqa: E402
import records    # noqa: E402
import router     # noqa: E402
import synth      # noqa: E402
from assemble import assemble  # noqa: E402


RELATIVE_BELOW = 1e9      # Unix seconds in 2026 are about 1.79e9


def when(text, store):
    """Unix seconds, or (below 1e9) seconds after the first observation's capture."""
    t = float(text)
    if t >= RELATIVE_BELOW:
        return t
    if not store.obs:
        raise records.RecordError('a relative time needs at least one observation')
    return min(o['capture_ts'] for o in store.obs.values()) + t


def load_store(args):
    return records.Store(records.read_jsonl(args.observations), records.read_jsonl(args.tracks))


def cmd_fixtures(args):
    for name, make in sorted(synth.SCENES.items()):
        scene = make()
        folder = os.path.join(args.out, name)
        os.makedirs(folder, exist_ok=True)
        for key in ('observations', 'tracks', 'crossings'):
            records.write_jsonl(os.path.join(folder, key + '.jsonl'), scene[key])
        print('%s: %d observations, %d track lines, %d crossings (%s)' % (
            name, len(scene['observations']), len(scene['tracks']), len(scene['crossings']), scene['about']))
    return 0


def cmd_assemble(args):
    store, m = load_store(args), method.load_method(args.method)
    bundle = assemble(store, m, args.track, when(args.at, store))
    records.write_jsonl(args.out, [bundle])
    print('%s: %d frames, %d keypoint entries, missing %s, association_safe %s' % (
        bundle['bundle_id'], len(bundle['frames']), len(bundle['keypoint_series']),
        bundle['missing'] or 'none', bundle['association_safe']))
    if m.open:
        print('practice: open slots %s' % ', '.join(m.open))
    return 0


def cmd_route(args):
    store, m, rule = load_store(args), method.load_method(args.method), method.load_router(args.rule)
    clock = fakes.FakeClock(when(args.start, store))
    sender = fakes.FakeSender(clock, latency_s=args.latency)
    r = router.Router(store, m, rule, clock, sender)
    recs = router.run(r, clock, sender, when(args.end, store))
    records.write_jsonl(args.requests, recs)
    records.write_jsonl(args.bundles, r.bundles)
    by_track = {}
    for rec in recs:
        by_track.setdefault(rec['track_id'], []).append(rec['outcome'] or 'in_flight')
    for tid, outs in sorted(by_track.items()):
        print('%s: %d requests (%s)' % (tid, len(outs), ', '.join('%d %s' % (outs.count(o), o) for o in sorted(set(outs)))))
    print('practice replay with a fake sender: rule %s %s, method %s %s' % (
        rule.version, rule.sha256[:12], m.version, m.sha256[:12]))
    return 0


def cmd_gate(args):
    store, m = load_store(args), method.load_method(args.method)
    rule = method.load_router(args.rule)
    bundles = records.read_jsonl(args.bundles)
    crossings = records.read_jsonl(args.crossings) if args.crossings else []
    requests = records.read_jsonl(args.requests) if args.requests else None
    result = gate.check(bundles, store, m, crossings, requests, scored=args.scored, rule=rule)
    for name, c in result['clauses'].items():
        print('%-12s %-4s checked %d' % (name, 'FAIL' if c['failures'] else 'ok', c['checked']))
        for line in c['failures'][:args.show]:
            print('    ' + line)
        if len(c['failures']) > args.show:
            print('    ... %d more' % (len(c['failures']) - args.show))
    print('verdict: %s%s' % (result['verdict'], ' (practice: open slots %s)' % ', '.join(result['open'])
                             if result['practice'] else ''))
    return 0 if result['verdict'] == 'pass' else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog='den_bundle.py', description="Rung 5's person bundles and router v0.")
    p.add_argument('--method', default=method.METHOD_PATH, help='METHOD-V1.md to use')
    p.add_argument('--rule', default=method.ROUTER_PATH, help='ROUTER-V0.md to use')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('fixtures', help='write the synthetic fixtures as JSON Lines')
    s.add_argument('--out', required=True)
    s.set_defaults(fn=cmd_fixtures)

    for name, fn, help_text in (('assemble', cmd_assemble, 'build one bundle'),
                                ('route', cmd_route, 'replay router v0 with a fake sender'),
                                ('gate', cmd_gate, "check rung 5's gate")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument('--observations', required=True)
        s.add_argument('--tracks', required=True)
        s.set_defaults(fn=fn)
        if name == 'assemble':
            s.add_argument('--track', required=True)
            s.add_argument('--at', required=True)
            s.add_argument('--out', required=True)
        elif name == 'route':
            s.add_argument('--from', dest='start', required=True)
            s.add_argument('--to', dest='end', required=True)
            s.add_argument('--latency', type=float, default=1.25, help='fake answer delay, s')
            s.add_argument('--requests', required=True)
            s.add_argument('--bundles', required=True)
        else:
            s.add_argument('--bundles', required=True)
            s.add_argument('--crossings')
            s.add_argument('--requests')
            s.add_argument('--scored', action='store_true', help='refuses while any slot is open')
            s.add_argument('--show', type=int, default=5, help='failures shown per clause')

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (method.MethodError, records.RecordError, ValueError, OSError) as e:
        print('refused: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
