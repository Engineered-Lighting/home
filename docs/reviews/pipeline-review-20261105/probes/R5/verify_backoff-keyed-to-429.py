"""Verify (reproduce lens): router v0 backs off 30 s after ANY non-ok answer, and names the GPU host's 429 as
the admission refusal; today's live pump backs off only after admission_closed / thermal_shed
(503) and retries a 429 on the next tick.

What it checks
  1) Router v0 (pinned): OUTCOMES, and whether a sender can report a typed skip that does not
     back off (on_result with 'skipped' / 'perceiver_busy').
  2) One track (two_tracks with B removed), FakeSender with request 2 answered 'refused' (how a
     sender must report the GPU host's 429 per router.py's docstring and ROUTER-V0.md): the next start.
  3) The live pump's rule, read (never imported, never run here) from
     <main checkout>/stack/services/perception-lab/qwen_live.py by AST: BACKOFF_REFUSALS and the
     429 entries of PRE_EXECUTION.

How to run
  python3 verify_backoff-keyed-to-429.py <root of the rung-5 checkout> <root of the main checkout>
  Synthetic data and fakes only; nothing is contacted.
"""
import ast
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
MAIN = os.path.abspath(sys.argv[2])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))
import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import router   # noqa: E402
import synth    # noqa: E402

M, R = method.load_method(), method.load_router()
print('RESULT router OUTCOMES %s; backoff_s %s' % (router.OUTCOMES, R['backoff_s']))

s = synth.two_tracks()
s['observations'] = [o for o in s['observations'] if o['obs_id'].startswith('o-A-')]
s['tracks'] = [l for l in s['tracks'] if l['track_id'] == 'A']
st = records.Store(s['observations'], s['tracks'])
clock = fakes.FakeClock(synth.ts(2.0))
sender = fakes.FakeSender(clock, script={2: 'refused'})
r = router.Router(st, M, R, clock, sender)
recs = router.run(r, clock, sender, synth.ts(60.0))
rel = lambda x: None if x is None else round(x - synth.T0, 3)
log = [(rel(x['sent_ts']), x['outcome'], rel(x['done_ts'])) for x in recs[:4]]
print('RESULT router v0 log with request 2 refused: %s' % log)
gap = recs[2]['sent_ts'] - recs[1]['done_ts']
print('RESULT next start after the refusal answer: +%.2f s' % gap)

for probe_outcome in ('skipped', 'perceiver_busy'):
    clock2 = fakes.FakeClock(synth.ts(2.0))
    snd = fakes.FakeSender(clock2, script={1: 'silent'})
    r2 = router.Router(st, M, R, clock2, snd)
    rec = r2.tick()
    try:
        r2.on_result(rec['request_id'], probe_outcome)
        print('RESULT on_result(%r) accepted' % probe_outcome)
    except ValueError as e:
        print('RESULT on_result(%r) raises ValueError: %s' % (probe_outcome, e))

src = open(os.path.join(MAIN, 'stack', 'services', 'perception-lab', 'qwen_live.py'), encoding='utf-8').read()
tree = ast.parse(src)
vals = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
            and node.targets[0].id in ('BACKOFF_REFUSALS', 'PRE_EXECUTION', 'BACKOFF_SECONDS'):
        v = node.value
        if isinstance(v, ast.Call):
            v = v.args[0]
        vals[node.targets[0].id] = ast.literal_eval(v)
four29 = sorted(reason for code, reason in vals['PRE_EXECUTION'] if code == 429)
print('RESULT live pump: BACKOFF_REFUSALS %s (codes %s), BACKOFF_SECONDS %s; 429 refusals (typed skips, no back-off): %s' % (
    sorted(vals['BACKOFF_REFUSALS']),
    sorted({code for code, reason in vals['PRE_EXECUTION'] if reason in vals['BACKOFF_REFUSALS']}),
    vals['BACKOFF_SECONDS'], four29))
mismatch = abs(gap - 30.0) < 1e-6 and 429 not in {c for c, x in vals['PRE_EXECUTION'] if x in vals['BACKOFF_REFUSALS']}
print('FAIL router v0 waits 30 s after a refusal it ties to the GPU host\'s 429; the live pump never backs off on a 429'
      if mismatch else 'PASS router and live pump agree')
