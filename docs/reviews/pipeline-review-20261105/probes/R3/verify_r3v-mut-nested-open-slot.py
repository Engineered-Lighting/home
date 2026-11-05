"""verify_r3v-mut-nested-open-slot.py -- R3 verifier, reproduce lens.

Checks finding r3v-mut-nested-open-slot. method.open_slots() walks the whole
METHOD.md block. The reported mutant limits the walk to the top level
("walk(k, v) if not prefix else None") and survives the den-track suite. This
probe shows the mutant is NOT equivalent and that the gap is real end to end:

  A METHOD.md copy closes only the top-level 'room' slot (at its proposed
  value) and leaves the nested slots (record.time_field,
  tracker.confident_at, tracker.coast_after_s, scoring.crossings_plan) open.
  With the original code, a scored run (`score` without --practice) refuses
  (exit 2) and names the nested slots. With the mutant, the same run is
  SCORED (exit 0 or 1), because Method.open and tracks.run.json's open_slots
  both come from the same walk.

Run (stdlib only, synthetic data, temp copies of tools/den-track; touches
nothing in the checkout and opens no network connection):
  python3 -B verify_r3v-mut-nested-open-slot.py <branch checkout root>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
OLD = "                walk('%s.%s' % (prefix, k) if prefix else k, v)"
NEW = "                walk(k, v) if not prefix else None"
FENCE = '```json den-track-method'


def copy_tool(dst, mutate):
    shutil.copytree(os.path.join(ROOT, 'tools', 'den-track'), dst,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if mutate:
        p = os.path.join(dst, 'method.py')
        src = open(p, encoding='utf-8').read()
        assert src.count(OLD) == 1, 'pinned line not found'
        open(p, 'w', encoding='utf-8', newline='\n').write(src.replace(OLD, NEW))


def room_only_closed(tool, dst):
    text = open(os.path.join(tool, 'METHOD.md'), encoding='utf-8').read()
    start = text.index(FENCE)
    body_start = start + len(FENCE) + 1
    end = text.index('\n```', body_start)
    block = json.loads(text[body_start:end])
    assert block['room'].get('open') is True
    block['room'] = block['room']['proposed']          # close only the top-level slot
    open(dst, 'w', encoding='utf-8', newline='\n').write(
        text[:start] + FENCE + '\n' + json.dumps(block, indent=2) + text[end:])
    return dst


def run(tool, *argv):
    p = subprocess.run([sys.executable, '-B', os.path.join(tool, 'den_track.py')] + list(argv),
                       capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    return p.returncode, p.stdout, p.stderr


results = {}
tmp = tempfile.mkdtemp(prefix='verify-r3-nested-')
for label, mutate in (('original', False), ('mutant', True)):
    base = os.path.join(tmp, label)
    tool = os.path.join(base, 'den-track')
    copy_tool(tool, mutate)
    meth = room_only_closed(tool, os.path.join(base, 'METHOD.room-closed.md'))
    code, out, err = run(tool, '--method', meth, 'method')
    print('RESULT %s `method`: %s' % (label, ' | '.join(l.strip() for l in out.splitlines() if 'open slot' in l or l.startswith('  ') and not l.startswith('    '))))
    session = os.path.join(base, 'session')
    code, out, err = run(tool, '--method', meth, 'synth', '--out', session, '--scenes', 'clean_crossing', '--seeds', '1')
    assert code == 0, err
    code, out, err = run(tool, '--method', meth, 'track', '--session', session)
    assert code == 0, err
    take = sorted(os.listdir(session))[0]
    run_json = json.load(open(os.path.join(session, take, 'tracks.run.json'), encoding='utf-8'))
    code, out, err = run(tool, '--method', meth, 'score', '--session', session)
    head = next((l for l in out.splitlines() if l.startswith('E2 geometry-only arm')), '')
    print('RESULT %s scored run: exit %d; tracks.run.json open_slots=%s; %s %s'
          % (label, code, run_json['open_slots'], head, err.strip().replace('\n', ' ')[:300]))
    results[label] = code
ok = results['original'] == 2 and results['mutant'] in (0, 1)
print('%s original refuses a scored run with nested slots open; mutant scores it (exit %d)'
      % ('PASS' if ok else 'FAIL', results['mutant']))
shutil.rmtree(tmp, ignore_errors=True)
