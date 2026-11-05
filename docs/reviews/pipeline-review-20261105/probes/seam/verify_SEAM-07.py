"""verify_SEAM-07: den-bundle `route` (and then `gate`) on synthetic observations with an empty tracks file.

Uses den-bundle's own `fixtures` command to write synthetic observations, then
runs `route` with an empty tracks file, then `gate` on what route wrote.
Prints exit codes, output, and line counts of the written files.

Run:
  python3 -B verify_SEAM-07.py <rung-5-bundle checkout>
Synthetic data only, temp dir only, no network.
"""
import os
import subprocess
import sys
import tempfile


def lines(path):
    with open(path, encoding='utf-8') as f:
        return sum(1 for l in f if l.strip())


def main():
    root = os.path.abspath(sys.argv[1])
    cli = os.path.join(root, 'tools', 'den-bundle', 'den_bundle.py')
    tmp = tempfile.mkdtemp(prefix='verify_seam07_')
    subprocess.run([sys.executable, '-B', cli, 'fixtures', '--out', os.path.join(tmp, 'fx')], check=True,
                   stdout=subprocess.DEVNULL)
    scene = sorted(os.listdir(os.path.join(tmp, 'fx')))[0]
    obs = os.path.join(tmp, 'fx', scene, 'observations.jsonl')
    empty = os.path.join(tmp, 'tracks-empty.jsonl')
    open(empty, 'w').close()
    req, bun = os.path.join(tmp, 'requests.jsonl'), os.path.join(tmp, 'bundles.jsonl')
    r = subprocess.run([sys.executable, '-B', cli, 'route', '--observations', obs, '--tracks', empty,
                        '--from', '2', '--to', '60', '--requests', req, '--bundles', bun],
                       capture_output=True, text=True)
    print('RESULT route on %s (%d observations) with an empty tracks file: exit %d; stdout %r; stderr %r'
          % (scene, lines(obs), r.returncode, r.stdout.strip(), r.stderr.strip()[-200:]))
    print('RESULT requests.jsonl %d lines, bundles.jsonl %d lines' % (lines(req), lines(bun)))
    g = subprocess.run([sys.executable, '-B', cli, 'gate', '--observations', obs, '--tracks', empty,
                        '--bundles', bun, '--requests', req], capture_output=True, text=True)
    print('RESULT gate on those files: exit %d; last line %r' % (g.returncode, g.stdout.strip().splitlines()[-1]))
    bad = r.returncode == 0 and lines(req) == 0 and lines(bun) == 0
    print('%s: route exits 0 with no requests and no bundles and names no missing tracks'
          % ('FAIL' if bad else 'PASS'))
    print('(FAIL = the defect SEAM-07 describes is present). temp dir: %s' % tmp)


if __name__ == '__main__':
    main()
