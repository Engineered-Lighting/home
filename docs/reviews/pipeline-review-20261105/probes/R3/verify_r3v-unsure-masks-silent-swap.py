"""verify_r3v-unsure-masks-silent-swap.py -- R3 (den-track), reproduce lens.

What it checks
  Whether the "?" (cannot tell) masking effect reported for
  scorer_core.switches() shows up on the REAL tracker's output, not only on
  hand-built track records. For synthetic crossing scenes (the tool's own
  synth.py, the synthetic den closed as the room), each take is tracked once
  and scored twice:
    (1) with the full A/B truth synth.py writes;
    (2) with every labelled observation in an "overlap" frame (the two true
        positions closer than D metres) treated as "?" -- dropped from the
        labels exactly as truth.read() drops "?".
  The tracker output is the same in both. It counts windows whose verdict
  changes: silent -> not silent (the unknown moment resolved in the
  tracker's favour) and not silent -> silent (the other way).

Run (stdlib only, synthetic data in memory, nothing written to the checkout):
  python3 -B verify_r3v-unsure-masks-silent-swap.py <branch checkout root> [seeds] [noise] [D]
"""
import math
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
SEEDS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
NOISE = sys.argv[3] if len(sys.argv) > 3 else 'typical'
D = float(sys.argv[4]) if len(sys.argv) > 4 else 0.6
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import method  # noqa: E402
import pipeline  # noqa: E402
import scorer_core  # noqa: E402
import synth  # noqa: E402

values = dict(method.Method().values, room=synth.ROOM_MODEL)
scored = values['scoring']['scored_states']
lenient = harsher = windows = masked_total = 0
examples = []
for name in ('touching_crossing', 'close_crossing', 'crossing_behind_armchair', 'near_pass'):
    for seed in range(SEEDS):
        scene = synth.scenario(name, seed, NOISE)
        obs, lines = synth.records(scene)
        frames = pipeline.observation_frames(obs, values['input'])
        recs = pipeline.track_frames(frames, values)
        truth = pipeline.truth_from_lines(lines)
        overlap_ts = set()
        for f in scene['frames']:
            pos = list(f['truth_xy'].values())
            if len(pos) == 2 and math.dist(pos[0], pos[1]) < D:
                overlap_ts.add(float(f['t']))
        masked = dict(truth, labels={o: v for o, v in truth['labels'].items() if v[0] not in overlap_ts})
        masked_total += len(truth['labels']) - len(masked['labels'])
        res_full = scorer_core.score_take(pipeline.links_for_take(recs, truth, scored)[0], truth['windows'])
        res_mask = scorer_core.score_take(pipeline.links_for_take(recs, masked, scored)[0], masked['windows'])
        for a, b in zip(res_full['windows'], res_mask['windows']):
            windows += 1
            if a['silent_swaps'] > 0 and b['silent_swaps'] == 0:
                lenient += 1
                examples.append(('silent->passes', '%s-%d' % (name, seed), a['silent_swaps'], b['silent_swaps'],
                                 b['reported_switches']))
            elif a['silent_swaps'] == 0 and b['silent_swaps'] > 0:
                harsher += 1
                examples.append(('passes->silent', '%s-%d' % (name, seed), a['silent_swaps'], b['silent_swaps'],
                                 b['reported_switches']))
        lenient_out = res_full['silent_swaps_outside_windows'] > 0 and res_mask['silent_swaps_outside_windows'] == 0
        if lenient_out:
            examples.append(('outside silent->none', '%s-%d' % (name, seed), res_full['silent_swaps_outside_windows'],
                             0, 0))
print('RESULT noise %s, seeds 0-%d, overlap distance %.2f m: windows %d, labels masked as "?" %d'
      % (NOISE, SEEDS - 1, D, windows, masked_total))
print('RESULT windows that fail with full labels but pass with overlap frames "?": %d' % lenient)
print('RESULT windows that pass with full labels but fail with overlap frames "?": %d' % harsher)
for e in examples:
    print('  %-22s %-30s silent full %d, silent masked %d, reported masked %d' % e)
