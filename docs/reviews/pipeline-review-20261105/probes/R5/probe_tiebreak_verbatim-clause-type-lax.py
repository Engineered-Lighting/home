"""Tiebreak probe for finding verbatim-clause-type-lax (rung 5, den-bundle).

What it checks
  Whether the stricter comparison the finding suggests (json.dumps of each copied field)
  would reject a copy whose JSON *values* are identical, as written by another ordinary
  serializer. It assembles bundles from the tool's crossing fixture, round-trips each
  bundle's keypoint_series through Node's JSON.parse/JSON.stringify (which prints 563.0 as
  563), parses the result back with Python's json, and then compares every copied field
  with the observation two ways: Python == (what gate.py:115 does) and json.dumps text.
  It also runs gate.check on the round-tripped bundles.
  Needs `node` on PATH; prints SKIP otherwise. Synthetic data only; a temp dir only.

How to run
  python3 -B probe_tiebreak_verbatim-clause-type-lax.py <root of the rung-5-bundle checkout>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from assemble import assemble  # noqa: E402

if shutil.which('node') is None:
    print('SKIP node not found')
    sys.exit(0)

M = method.load_method()
scene = synth.crossing()
st = records.Store(scene['observations'], scene['tracks'])
bundles = [assemble(st, M, tid, synth.ts(2.0 + 0.5 * i)) for i in range(21) for tid in ('A', 'B')]

tmp = tempfile.mkdtemp(prefix='r5-verbatim-')
try:
    src = os.path.join(tmp, 'in.jsonl')
    with open(src, 'w', encoding='utf-8') as fh:
        for b in bundles:
            fh.write(json.dumps(b) + '\n')
    js = ("const fs=require('fs');const out=fs.readFileSync(process.argv[1],'utf8').split('\\n')"
          ".filter(Boolean).map(l=>JSON.stringify(JSON.parse(l))).join('\\n');process.stdout.write(out);")
    text = subprocess.run(['node', '-e', js, src], check=True, stdout=subprocess.PIPE).stdout.decode('utf-8')
    rt = [json.loads(line) for line in text.splitlines()]
finally:
    shutil.rmtree(tmp)

eq_differs = dumps_differs = copies = 0
for b in rt:
    for k in b['keypoint_series']:
        o = st.obs[k['obs_id']]
        for field, value in (('keypoints', o.get('keypoints')), ('joints_room', o.get('joints_room'))):
            copies += 1
            eq_differs += k.get(field) != value
            dumps_differs += json.dumps(k.get(field), sort_keys=True) != json.dumps(value, sort_keys=True)
res = gate.check(rt, st, M)
print('RESULT node round-trip: %d copied keypoints/joints_room fields; differ under == %d; differ under '
      'json.dumps compare %d; gate verbatim failures %d' % (
          copies, eq_differs, dumps_differs, len(res['clauses']['verbatim']['failures'])))
print('RESULT the suggested json.dumps compare would reject %s equal-valued copies' % (
    'these' if dumps_differs and not eq_differs else 'no'))
