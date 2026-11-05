"""verify_truth-producer-missing: checks the premise that E2's
per-observation, blind identity truth (den-track-truth/v1) has no producer.

In the given checkout it checks:
  1. den-track's README.md and truth.py name rung 4's labeller as the writer;
  2. ladder.json's rung 4: its name, its inputs ('track') and whether its
     'tested' text marks things per track;
  3. which files outside tools/den-track mention den-track-truth (a writer
     would have to);
  4. whether tools/den-label (N1's labeller), if present, ends a pass on a
     second person;
  5. how many observation ids one synthetic take asks the owner to label
     (synth's default 4 fps), scaled to the 30-take plan.

Read-only on the checkout; synthetic data in a temp dir only.

Run:  python3 verify_truth-producer-missing.py <root of the rung-3-track checkout>
"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)


def main():
    readme = open(os.path.join(TOOL, 'README.md'), encoding='utf-8').read()
    truth_src = open(os.path.join(TOOL, 'truth.py'), encoding='utf-8').read()
    print('1. README names rung 4 as writer: %s; truth.py does: %s'
          % ("Rung 4's two-person labeller is meant to write it" in readme,
             "Rung 4's two-person labeller is meant to write this file" in truth_src))
    lad = json.load(open(os.path.join(ROOT, 'docs', 'architecture', 'live-v1', 'ladder.json'), encoding='utf-8'))
    r4 = next(r for r in lad['rungs'] if r.get('n') == 4)
    print('2. ladder rung 4 name=%r in=%r out=%r; tested says "marks, per track": %s'
          % (r4['name'], r4['in'], r4['out'], 'The owner marks, per track' in r4['tested']))
    hits = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in ('.git', 'node_modules', '__pycache__')]
        if os.path.abspath(dp).startswith(TOOL):
            continue
        for f in fn:
            if f.endswith(('.py', '.md', '.json', '.mjs', '.js', '.ps1', '.html')):
                p = os.path.join(dp, f)
                try:
                    if 'den-track-truth' in open(p, encoding='utf-8', errors='replace').read():
                        hits.append(os.path.relpath(p, ROOT))
                except OSError:
                    pass
    print('3. files outside tools/den-track mentioning den-track-truth: %s' % (hits or 'none'))
    dl = os.path.join(ROOT, 'tools', 'den-label', 'README.md')
    if os.path.isfile(dl):
        t = open(dl, encoding='utf-8').read()
        print('4. tools/den-label present; ends a pass on a second person: %s' % ('second person' in t))
    else:
        print('4. tools/den-label not in this checkout')
    import synth
    import pipeline
    with tempfile.TemporaryDirectory() as d:
        counts = {}
        for name in sorted(synth.SCENES):
            take = os.path.join(d, name)
            pipeline.write_synthetic_take(take, synth.scenario(name, 0, 'typical'))
            with open(os.path.join(take, 'truth.jsonl'), encoding='utf-8') as f:
                lines = [json.loads(l) for l in f if l.strip()]
            labels = sum(len(l.get('labels') or []) for l in lines if l['line'] == 'frame')
            frames = sum(1 for l in lines if l['line'] == 'frame')
            counts[name] = (frames, labels)
        per_take = sum(v[1] for v in counts.values()) / len(counts)
        for name, (fr, lab) in sorted(counts.items()):
            print('5. %-28s frames %4d  observation labels %5d' % (name, fr, lab))
        print('5. mean labels per synthetic take at 4 fps: %.0f; x30 takes: %.0f; at 15 fps: about %.0f'
              % (per_take, 30 * per_take, 30 * per_take * 15 / 4))
    print('RESULT: premise %s' % ('HOLDS (no producer for per-observation blind identity truth found)'
                                  if not hits and r4['in'] == ['track'] else 'DOES NOT HOLD'))


if __name__ == '__main__':
    main()
