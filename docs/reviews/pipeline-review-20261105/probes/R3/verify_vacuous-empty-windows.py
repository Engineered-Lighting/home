"""verify_vacuous-empty-windows: independent re-check of finding
vacuous-empty-windows (E2 gate counts windows that hold no labelled
observation as passed trials).

Realistic variant (not the reporter's 19-windows-after-the-take one):
17 normal synthetic clean_crossing takes plus 3 takes whose recording was
cut short before the staged event (observations and labels after the
window start are dropped, as if capture stopped early; the window line from
the staged script stays).  The 3 windows hold nothing.  Scored with the
same pipeline.score_takes / scorer_core.pool the CLI uses.

Expect a sound gate: 17 admitted windows -> NOT MET (fewer than 20).
Prints the tool's verdict for (a) the 17 good takes alone and (b) the 17
plus the 3 cut-short takes.

Run:  python3 verify_vacuous-empty-windows.py <root of the rung-3-track checkout>
Synthetic data only, in memory; no network.
"""
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import pipeline  # noqa: E402
import synth  # noqa: E402

VALUES = dict(method_mod.Method(os.path.join(TOOL, 'METHOD.md')).values, room=synth.ROOM_MODEL)


def take(seed, cut=False):
    scene = synth.scenario('clean_crossing', seed, 'typical')
    obs, lines = synth.records(scene)
    if cut:
        w = [l for l in lines if l['line'] == 'window'][0]
        stop = w['start_ts'] - 0.5
        obs = [o for o in obs if o['capture_ts'] < stop]
        lines = [l for l in lines if l['line'] != 'frame' or l['capture_ts'] < stop]
    frames = pipeline.observation_frames(obs, VALUES['input'])
    recs = pipeline.track_frames(frames, VALUES)
    return ('cc-%d%s' % (seed, '-cut' if cut else ''), recs, pipeline.truth_from_lines(lines))


def verdict(rep):
    empty = sum(1 for r in rep['windows'] if r['observations'] == 0)
    return ('windows %d (empty %d), silent %d, outside-silent %d, pooled IDF1 %.3f, bound %.4f, gate passed %s'
            % (rep['crossings'], empty, rep['silent_swaps'], rep['silent_swaps_outside_windows'],
               rep['pooled']['idf1'], rep['silent_swap_rate_upper_95'], rep['gate']['passed']))


good = [take(s) for s in range(200, 217)]
cut = [take(s, cut=True) for s in range(217, 220)]
ra = pipeline.score_takes(good, VALUES)
rb = pipeline.score_takes(good + cut, VALUES)
print('a) 17 good takes:            %s' % verdict(ra))
print('b) 17 good + 3 cut-short:    %s' % verdict(rb))
bad = (not ra['gate']['passed']) and rb['gate']['passed']
print('RESULT: %s' % ('FAIL (defect reproduced): 3 windows with no observation turn NOT MET into PASSED'
                      if bad else 'PASS/not reproduced'))
