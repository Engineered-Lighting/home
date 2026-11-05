"""verify_last-obs-ids-in-ambiguous-interval: checks that den-track writes
observation ids into last_obs_ids on records whose association is
`ambiguous`, how many of those ids are (per the synthetic truth) the OTHER
person's, and whether the scorer's reported-vs-silent split depends on them.

For each synthetic scene and seeds 0-4 (method's proposed values, synth's
room) it tracks in memory and counts:
  - ambiguous records, and ambiguous records with a non-empty last_obs_ids;
  - listed ids whose labelled person differs from the person the track holds
    most often over the take (joints K7 would read off the wrong person);
then re-scores with last_obs_ids emptied on ambiguous records (the literal
"never ... from an ambiguous interval" reading) and compares silent and
reported switch counts.

Synthetic data only; in memory; no network.

Run:  python3 verify_last-obs-ids-in-ambiguous-interval.py <root of the rung-3-track checkout>
"""
import collections
import copy
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
sys.path.insert(0, TOOL)
import method  # noqa: E402
import pipeline  # noqa: E402
import synth  # noqa: E402


def main():
    values = dict(method.Method().values, room=synth.ROOM_MODEL)
    amb = amb_with = listed = other = 0
    takes, blanked = [], []
    for name in sorted(synth.SCENES):
        for seed in range(5):
            take_id, recs, truth = pipeline.synthetic_take(synth.scenario(name, seed, 'typical'), values)
            labels = truth['labels']
            held = collections.defaultdict(collections.Counter)
            for r in recs:
                for o in r['last_obs_ids']:
                    if o in labels and labels[o][1] is not None:
                        held[r['track_id']][labels[o][1]] += 1
            major = {t: c.most_common(1)[0][0] for t, c in held.items()}
            for r in recs:
                if r['association']['status'] != 'ambiguous':
                    continue
                amb += 1
                if r['last_obs_ids']:
                    amb_with += 1
                for o in r['last_obs_ids']:
                    listed += 1
                    p = labels.get(o, (None, None))[1]
                    if p is not None and p != major.get(r['track_id']):
                        other += 1
            takes.append((take_id, recs, truth))
            b = copy.deepcopy(recs)
            for r in b:
                if r['association']['status'] == 'ambiguous':
                    r['last_obs_ids'] = []
            blanked.append((take_id, b, truth))
    print('ambiguous records: %d; with non-empty last_obs_ids: %d; ids listed: %d; '
          "ids that are the other person's: %d" % (amb, amb_with, listed, other))
    a = pipeline.score_takes(takes, values)
    b = pipeline.score_takes(blanked, values)
    for tag, rep in (('as built', a), ('ambiguous emptied', b)):
        print('%-18s silent %3d  reported %3d  windows failed %3d/%d  pooled IDF1 %.4f  gate %s'
              % (tag, rep['silent_swaps'], rep['reported_switches'], rep['crossings_with_silent_swap'],
                 rep['crossings'], rep['pooled']['idf1'], rep['gate']['passed']))
    print('RESULT: ambiguous records list observations: %s; emptying them changes the silent count: %s'
          % ('YES' if amb_with else 'NO', 'YES' if a['silent_swaps'] != b['silent_swaps'] else 'NO'))


if __name__ == '__main__':
    main()
