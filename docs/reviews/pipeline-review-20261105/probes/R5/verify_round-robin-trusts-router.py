"""Verify (reproduce lens): does rung 5's gate catch a router whose eligibility is wrong?

What it checks
  The finding says gate.check_requests computes round_robin only from each record's own
  `tracks` list (what the router says was eligible), never from the track store, so a router
  that wrongly drops a confirmed, visible person passes. Instead of rewriting a log by hand,
  this probe builds a real faulty router: a copy of tools/den-bundle where Router.eligible()
  returns only the first-seen track (stand-in for any eligibility bug: dating, state, window).
  It then runs the tool's own CLI end to end on the two_tracks fixture
  (fixtures -> route --from 2 --to 70 -> gate --requests) and prints the gate's exit code and
  router clauses. For each send time it also asks the UNMUTATED pinned Router.eligible (the
  ROUTER-V0 rule applied to the store) whether B was eligible.
  Control: the same pipeline with the unmutated copy.

How to run
  python3 verify_round-robin-trusts-router.py <root of the branch checkout> [<mutation work dir>]
  Synthetic fixtures only; copies go to a fresh temp dir (under the work dir if given).
  The checkout is never modified.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
WORK = sys.argv[2] if len(sys.argv) > 2 else None
SRC = os.path.join(ROOT, 'tools', 'den-bundle')
sys.path.insert(0, SRC)
import fakes    # noqa: E402,F401
import method   # noqa: E402
import records  # noqa: E402
import router as pinned_router  # noqa: E402

OLD = "                out.append(tid)\n        return out\n"
NEW = "                out.append(tid)\n        return out[:1]\n"


def pipeline(tool, tmp):
    cli = [sys.executable, '-B', os.path.join(tool, 'den_bundle.py')]
    fx = os.path.join(tmp, 'fx')
    subprocess.run(cli + ['fixtures', '--out', fx], capture_output=True, check=True)
    o, t = os.path.join(fx, 'two_tracks', 'observations.jsonl'), os.path.join(fx, 'two_tracks', 'tracks.jsonl')
    rq, bd = os.path.join(tmp, 'rq.jsonl'), os.path.join(tmp, 'bd.jsonl')
    subprocess.run(cli + ['route', '--observations', o, '--tracks', t, '--from', '2', '--to', '70',
                          '--requests', rq, '--bundles', bd], capture_output=True, check=True)
    g = subprocess.run(cli + ['gate', '--observations', o, '--tracks', t, '--bundles', bd, '--requests', rq],
                       capture_output=True, text=True)
    return o, t, records.read_jsonl(rq), g


with tempfile.TemporaryDirectory(dir=WORK) as tmp:
    results = {}
    for name in ('control', 'mutant'):
        tool = os.path.join(tmp, name, 'den-bundle')
        shutil.copytree(SRC, tool, ignore=shutil.ignore_patterns('__pycache__'))
        if name == 'mutant':
            p = os.path.join(tool, 'router.py')
            text = open(p, encoding='utf-8').read()
            assert text.count(OLD) == 1, 'mutation anchor not found exactly once'
            open(p, 'w', encoding='utf-8', newline='\n').write(text.replace(OLD, NEW))
        o, t, recs, g = pipeline(tool, os.path.join(tmp, name))
        results[name] = (o, t, recs, g)
        served = {}
        for r in recs:
            served[r['track_id']] = served.get(r['track_id'], 0) + 1
        rr = [l for l in g.stdout.splitlines() if l.startswith(('round_robin', 'verdict'))]
        print('RESULT %s: requests by track %s; gate exit %d; %s' % (name, served, g.returncode, ' | '.join(rr)))

    # ROUTER-V0's eligibility, recomputed from the store with the pinned (unmutated) router code.
    o, t, recs, g = results['mutant']
    st = records.Store(records.read_jsonl(o), records.read_jsonl(t))
    ref = pinned_router.Router(st, method.load_method(), method.load_router(), lambda: 0.0, None)
    b_eligible = sum(1 for r in recs if 'B' in ref.eligible(r['sent_ts']))
    b_served = sum(1 for r in recs if r['track_id'] == 'B')
    print('RESULT mutant log: %d requests, B served %d, B eligible per ROUTER-V0 (store) at %d send times, '
          'every record lists tracks %s' % (len(recs), b_served, b_eligible, sorted({tuple(r['tracks']) for r in recs})))
    if g.returncode == 0 and b_served == 0 and b_eligible > 0:
        print('FAIL gate passes (exit 0) a routed log that starves an eligible track B')
    else:
        print('PASS gate catches the starving router (exit %d)' % g.returncode)
