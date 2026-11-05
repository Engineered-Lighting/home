#!/usr/bin/env python3
"""mutate_den_bundle.py: targeted mutants of den-bundle's gate, router and assembler.

What it does
  For each mutant: copies <checkout>/tools/den-bundle to a fresh temp folder,
  applies one exact text replacement (it must match exactly once), runs the
  tool's own suite there with failfast
      python3 -B -m unittest discover -s <copy> -f
  and records "caught" (suite fails) or "survived" (suite passes). Prints a
  caught/survived table and a RESULT line. The checkout is never modified.

How to run
  python3 mutate_den_bundle.py <root of the claude/rung-5-bundle checkout> [mutant-id ...]

Synthetic data only (the suite's own fixtures), no network.
"""
import os
import shutil
import subprocess
import sys
import tempfile

MUTANTS = [
    # id, file, old, new, what it breaks
    ('G1', 'gate.py', "if b['association_safe'] is not False:", "if b['association_safe'] is None:",
     'crossing clause accepts association_safe true'),
    ('G2', 'gate.py', "if b['track_id'] in c['track_ids'] and t0 <= c1 + EPS and c0 <= t1 + EPS:",
     "if b['track_id'] in c['track_ids'] and c0 <= t0 + EPS and t0 <= c1 + EPS:",
     'crossing clause skips bundles that start before the crossing and end inside it'),
    ('G3', 'gate.py', "'keypoints': o.get('keypoints'), ", "",
     'verbatim clause no longer compares keypoints'),
    ('G4', 'gate.py', "'transform_id': o.get('transform_id'),", "",
     'verbatim clause no longer compares transform_id'),
    ('G5', 'gate.py', "expect = {'ts': o['capture_ts'], 'camera_id': camera_of(o), ", "expect = {",
     'verbatim clause no longer compares ts and camera_id'),
    ('G6', 'gate.py', "and is_valid(o) and st == {'confident'}", "and is_valid(o) and 'confident' in st",
     'gate counts an observation also named by an ambiguous line as association-gated'),
    ('G7', 'gate.py', "and is_valid(o) and st == {'confident'}", "and st == {'confident'}",
     'gate counts a rejected observation as association-gated'),
    ('G8', 'gate.py', "'object_box': any(ob['room_position'] is None for ob in b['objects']),", "'object_box': False,",
     'missing clause never asks for object_box'),
    ('G9', 'gate.py', "return not any(str(j.get('lift') or '').startswith('plane') for j in o.get('joints_room') or [])",
     "return True", 'missing clause ignores plane-lifted joints (depth)'),
    ('G10', 'gate.py', "if abs((t1 - t0) - method['window_s']) > EPS:", "if (t1 - t0) - method['window_s'] > EPS:",
     'window clause accepts a too-short interval'),
    ('G11', 'gate.py', "elif abs(b['interval'][1] - r['sent_ts']) > EPS:", "elif r['sent_ts'] - b['interval'][1] > EPS:",
     'no_wait clause accepts a bundle whose interval ends after its send'),
    ('G12', 'gate.py', "if prev.get('done_ts') is None or r['sent_ts'] < prev['done_ts'] - EPS:",
     "if prev.get('done_ts') is not None and r['sent_ts'] < prev['done_ts'] - EPS:",
     'in_flight clause accepts a start while an unanswered request is still in flight'),
    ('G13', 'gate.py', "if prev.get('outcome') != 'ok' and prev.get('done_ts') is not None \\",
     "if prev.get('outcome') == 'refused' and prev.get('done_ts') is not None \\",
     'backoff clause ignores errors and timeouts'),
    ('M1', 'method.py', "    if open_slots:\n        raise MethodError('%s refuses: open slots",
     "    if open_slots and False:\n        raise MethodError('%s refuses: open slots",
     'scored runs no longer refuse open slots'),
    ('A1', 'assemble.py', "in_effect = at_start[-1:] + [l for l in lines if l.ts > t0 + EPS]",
     "in_effect = [l for l in lines if l.ts > t0 + EPS]",
     'association_safe ignores the line in effect at t0'),
    ('R1', 'router.py', "self.backoff_until = at + self.rule['backoff_s']",
     "self.backoff_until = rec['sent_ts'] + self.rule['backoff_s']",
     'back-off counted from the send, not the answer'),
    ('A3', 'assemble.py', "for oid, statuses in store.listed(track_id, t1).items():",
     "for oid, statuses in store.listed(track_id, None).items():",
     'keypoint_series reads track lines dated after t1 (non-causal)'),
    ('A4', 'assemble.py', "    for oid in store.listed(track_id, t1):\n        o = store.obs[oid]",
     "    for oid in store.listed(track_id, None):\n        o = store.obs[oid]",
     'frames read track lines dated after t1 (non-causal)'),
    ('G14', 'gate.py', "        if line.ts > t1 + EPS:\n            continue\n", "",
     'verbatim clause counts observations listed only by lines dated after t1'),
]


def run(root, ids):
    src = os.path.join(root, 'tools', 'den-bundle')
    rows = []
    for mid, fname, old, new, what in MUTANTS:
        if ids and mid not in ids:
            continue
        tmp = tempfile.mkdtemp(prefix='mut-r5-%s-' % mid)
        try:
            dst = os.path.join(tmp, 'den-bundle')
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__'))
            path = os.path.join(dst, fname)
            with open(path, encoding='utf-8') as f:
                text = f.read()
            n = text.count(old)
            if n != 1:
                rows.append((mid, fname, what, 'not-run', 'pattern matched %d times' % n))
                continue
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text.replace(old, new))
            p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', dst, '-f'],
                               cwd=tmp, capture_output=True, text=True, timeout=600)
            tail = p.stderr.strip().splitlines()
            first_fail = next((l for l in tail if l.startswith(('FAIL:', 'ERROR:'))), '')
            rows.append((mid, fname, what, 'caught' if p.returncode else 'survived', first_fail or (tail[-1] if tail else '')))
        finally:
            shutil.rmtree(tmp, True)
        print('%-4s %-11s %-9s %s | %s' % (rows[-1][0], rows[-1][1], rows[-1][3], rows[-1][2], rows[-1][4]), flush=True)
    survived = [r for r in rows if r[3] == 'survived']
    print('RESULT %s: %d mutants, %d caught, %d survived (%s)' % (
        'FAIL' if survived else 'PASS', len(rows), sum(r[3] == 'caught' for r in rows), len(survived),
        ', '.join(r[0] for r in survived)))
    return 1 if survived else 0


if __name__ == '__main__':
    sys.exit(run(sys.argv[1], set(sys.argv[2:])))
