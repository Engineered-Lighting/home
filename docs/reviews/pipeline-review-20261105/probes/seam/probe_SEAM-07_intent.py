"""probe_SEAM-07_intent.py: what den-bundle's `route` (and then `gate`) do when
the tracks file is empty, or names no observation in the observations file.

Verifier probe (intent/spec lens) for finding SEAM-07. Synthetic data only:
den-bundle writes its own fixtures into a temp dir; nothing touches a network.

    python3 -B probe_SEAM-07_intent.py <root of a claude/rung-5-bundle checkout>

Cases:
  A  empty tracks file (0 bytes), real fixture observations     -> route exit? requests? bundles?
  B  two_tracks fixture with every last_obs_id renamed (tracks from another
     evening: all track lines undated)   -> route exit? does it say anything?
  C  gate on case A's empty bundles + requests                  -> verdict?
  D  control: matched crossing fixture                           -> requests > 0
  E  gate --scored on case A's output, every slot closed at its proposed value
     in temp copies of METHOD-V1.md / ROUTER-V0.md                -> verdict?
Prints RESULT lines; PASS/FAIL refer to the finding's claim (exit 0, nothing written, nothing flagged).
"""
import json
import os
import subprocess
import sys
import tempfile


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def lines(path):
    with open(path, encoding='utf-8') as f:
        return [l for l in f if l.strip()]


def main():
    root = sys.argv[1]
    tool = os.path.join(root, 'tools', 'den-bundle', 'den_bundle.py')
    py = [sys.executable, '-B', tool]
    with tempfile.TemporaryDirectory(prefix='seam07-') as tmp:
        fx = os.path.join(tmp, 'fx')
        rc, out, err = run(py + ['fixtures', '--out', fx])
        print('fixtures rc=%d' % rc)
        empty = os.path.join(tmp, 'tracks-empty.jsonl')
        open(empty, 'w').close()

        # A: empty tracks
        rq, bd = os.path.join(tmp, 'rqA.jsonl'), os.path.join(tmp, 'bdA.jsonl')
        rc, out, err = run(py + ['route', '--observations', os.path.join(fx, 'two_tracks', 'observations.jsonl'),
                                 '--tracks', empty, '--from', '2.5', '--to', '60', '--requests', rq, '--bundles', bd])
        print('A empty tracks: rc=%d requests=%d bundles=%d stdout=%r stderr=%r' % (rc, len(lines(rq)), len(lines(bd)), out, err))
        print('RESULT A: %s (finding claims exit 0 and two empty files)' % ('PASS' if rc == 0 and not lines(rq) and not lines(bd) else 'FAIL'))

        # B: mismatched observations/tracks (every line undated): the two_tracks track lines with
        # every last_obs_id renamed, as if the tracks came from another evening's observations
        other = os.path.join(tmp, 'tracks-other-evening.jsonl')
        with open(os.path.join(fx, 'two_tracks', 'tracks.jsonl'), encoding='utf-8') as f, \
                open(other, 'w', encoding='utf-8', newline='\n') as g:
            for l in f:
                if l.strip():
                    r = json.loads(l)
                    r['last_obs_ids'] = ['other-' + o for o in r.get('last_obs_ids') or []]
                    g.write(json.dumps(r) + '\n')
        rq2, bd2 = os.path.join(tmp, 'rqB.jsonl'), os.path.join(tmp, 'bdB.jsonl')
        rc, out, err = run(py + ['route', '--observations', os.path.join(fx, 'two_tracks', 'observations.jsonl'),
                                 '--tracks', other,
                                 '--from', '2.5', '--to', '60', '--requests', rq2, '--bundles', bd2])
        ntl = len(lines(other))
        print('B mismatched (%d track lines, none resolvable): rc=%d requests=%d bundles=%d stdout=%r stderr=%r' % (
            ntl, rc, len(lines(rq2)), len(lines(bd2)), out, err))
        print('RESULT B: %s (exit 0, nothing written, no word about %d undated track lines)' % (
            'PASS' if rc == 0 and not lines(rq2) and 'undated' not in (out + err) else 'FAIL', ntl))

        # C: gate on A's empty output
        rc, out, err = run(py + ['gate', '--observations', os.path.join(fx, 'two_tracks', 'observations.jsonl'),
                                 '--tracks', empty, '--bundles', bd, '--requests', rq])
        print('C gate on empty route output: rc=%d\n%s\nstderr=%r' % (rc, out, err))
        print('RESULT C: gate verdict on 0 bundles = %s' % ('pass (vacuous, every clause checked 0)' if rc == 0 else 'not pass'))

        # E: the same gate, scored, with every slot closed at its proposed value in temp copies
        import re
        closed = {}
        for name in ('METHOD-V1.md', 'ROUTER-V0.md'):
            src = open(os.path.join(root, 'tools', 'den-bundle', name), encoding='utf-8').read()
            dst = re.sub(r'\{"open": true, "proposed": ([^,]+), "why": "[^"]*"\}', r'\1', src)
            closed[name] = os.path.join(tmp, name)
            open(closed[name], 'w', encoding='utf-8', newline='\n').write(dst)
        rc, out, err = run([sys.executable, '-B', tool, '--method', closed['METHOD-V1.md'], '--rule', closed['ROUTER-V0.md'],
                            'gate', '--observations', os.path.join(fx, 'two_tracks', 'observations.jsonl'),
                            '--tracks', empty, '--bundles', bd, '--requests', rq, '--scored'])
        print('E scored gate, slots closed, 0 bundles: rc=%d last=%r stderr=%r' % (rc, out.splitlines()[-1] if out else '', err))
        print('RESULT E: scored gate on 0 bundles = %s' % ('pass, exit 0' if rc == 0 else 'rc %d' % rc))

        # D: control
        rq3, bd3 = os.path.join(tmp, 'rqD.jsonl'), os.path.join(tmp, 'bdD.jsonl')
        rc, out, err = run(py + ['route', '--observations', os.path.join(fx, 'crossing', 'observations.jsonl'),
                                 '--tracks', os.path.join(fx, 'crossing', 'tracks.jsonl'),
                                 '--from', '2.5', '--to', '13', '--requests', rq3, '--bundles', bd3])
        print('D control matched crossing: rc=%d requests=%d bundles=%d stdout=%r' % (rc, len(lines(rq3)), len(lines(bd3)), out))


if __name__ == '__main__':
    main()
