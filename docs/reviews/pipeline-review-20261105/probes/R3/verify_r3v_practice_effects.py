"""verify_r3v_practice_effects.py -- R3 (den-track) verifier, intent/spec lens.

Two read-only checks on the tool's own synthetic practice harness
(pipeline.practice), run in memory on the branch checkout (nothing written
there; no network):

1. r3v-mut-single-silent-switch: on the fresh seeds 200-239 that METHOD.md's
   tuning record reports (typical and noisy noise), list every window with a
   silent swap and how many silent swaps it holds. Windows holding exactly
   one are the ones a '> 1' regression in scorer_core.pool would stop
   counting as failed.
2. r3v-mut-scored-states: the same harness on development seeds 0-19
   (typical), scored once with scoring.scored_states = ['confirmed'] (as
   frozen) and once with every state scored (what removing the filter does),
   to size the effect on pooled IDF1, silent swaps and the gate.
3. r3v-mut-scored-states, per scene: touching_crossing and
   crossing_behind_armchair on fresh seeds 200-239 (typical), frozen vs
   every state scored, to see whether the IDF1 verdict (>= 0.9) flips.

Run (stdlib only, a few minutes):
  python3 -B verify_r3v_practice_effects.py <branch checkout root> [part: 1|2|3|both]
"""
import copy
import os
import sys
import time

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
PART = sys.argv[2] if len(sys.argv) > 2 else 'both'
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-track'))

import method   # noqa: E402
import pipeline  # noqa: E402
import synth    # noqa: E402

VALUES = dict(method.Method().values, room=synth.ROOM_MODEL)


def part1():
    total_failed, single = 0, 0
    for noise in ('typical', 'noisy'):
        t0 = time.time()
        rep = pipeline.practice(VALUES, seeds=40, noise=noise, first_seed=200)
        failing = [r for r in rep['windows'] if r['silent_swaps'] > 0]
        print('seeds 200-239 %s: %d windows, %d failed (%.0fs)' % (noise, rep['crossings'],
                                                                  rep['crossings_with_silent_swap'], time.time() - t0))
        for r in failing:
            print('  failing window %s/%s silent_swaps=%d reported=%d' % (r['take'], r['id'], r['silent_swaps'],
                                                                         r['reported_switches']))
        total_failed += len(failing)
        single += sum(1 for r in failing if r['silent_swaps'] == 1)
    print('RESULT part1: %d failing windows, %d with exactly one silent swap' % (total_failed, single))


def part2():
    frozen = VALUES
    loose = copy.deepcopy(VALUES)
    loose['scoring'] = dict(loose['scoring'], scored_states=['tentative', 'confirmed', 'coasting', 'ended'])
    out = {}
    for label, vals in (('frozen', frozen), ('all-states', loose)):
        rep = pipeline.practice(vals, seeds=20, noise='typical', first_seed=0)
        out[label] = rep
        print('%-10s windows %d failed %d silent %d reported %d pooled IDF1 %.4f gate %s' % (
            label, rep['crossings'], rep['crossings_with_silent_swap'], rep['silent_swaps'],
            rep['reported_switches'], rep['pooled']['idf1'], rep['gate']['passed']))
    a, b = out['frozen'], out['all-states']
    print('RESULT part2: IDF1 %.4f -> %.4f, failed windows %d -> %d' % (
        a['pooled']['idf1'], b['pooled']['idf1'], a['crossings_with_silent_swap'], b['crossings_with_silent_swap']))


def part3():
    loose = copy.deepcopy(VALUES)
    loose['scoring'] = dict(loose['scoring'], scored_states=['tentative', 'confirmed', 'coasting', 'ended'])
    flips = 0
    for scene in ('touching_crossing', 'crossing_behind_armchair'):
        res = {}
        for label, vals in (('frozen', VALUES), ('all-states', loose)):
            rep = pipeline.practice(vals, scenes=[scene], seeds=40, noise='typical', first_seed=200)
            res[label] = rep
        a, b = res['frozen'], res['all-states']
        flip = a['gate']['idf1_met'] != b['gate']['idf1_met']
        flips += flip
        print('%-26s IDF1 frozen %.4f (met %s) -> all states %.4f (met %s)%s' % (
            scene, a['pooled']['idf1'], a['gate']['idf1_met'], b['pooled']['idf1'], b['gate']['idf1_met'],
            '  <- IDF1 verdict flips' if flip else ''))
    print('RESULT part3: %d of 2 scenes flip the IDF1 verdict when every state is scored' % flips)


if __name__ == '__main__':
    if PART in ('1', 'both'):
        part1()
    if PART in ('2', 'both'):
        part2()
    if PART in ('3', 'both'):
        part3()
