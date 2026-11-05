#!/usr/bin/env python3
"""probe_bundle_recompute.py: recompute bundle coverage, association_safe and the verbatim copy by hand.

What it checks (independent code; only the tool's synthetic fixtures and
assemble() are imported)
  1. coverage: every bundle's interval is exactly [t1 - window_s, t1] and every
     frame, keypoint entry and position lies in it;
  2. keypoint_series is exactly the set of association-gated observations,
     recomputed here: a track line is dated by the newest capture_ts it names,
     never earlier than the line before it; an observation is gated when it is
     a valid keypoints observation captured in the window and every line of the
     track dated by t1 that names it is `confident`;
  3. association_safe across the annotated crossing: unsafe exactly when the
     window meets the span over which the latest track line was not
     `confident` ([4.5, 7.5) in the `crossing` fixture), recomputed from the
     lines; also on a variant where the tracker holds observations while
     ambiguous (ambiguous lines with empty last_obs_ids, as the Track schema's
     last_obs_ids note allows);
  4. verbatim: after a JSON Lines round trip of the bundles, each copied field
     (obs_id, ts<-capture_ts, camera_id<-source.sensor_id, transform_id,
     keypoints, joints_room) serialises to the same bytes as the observation's.

How to run
  python3 probe_bundle_recompute.py <root of the claude/rung-5-bundle checkout>

Synthetic data only, no network; temp files only.
"""
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, 'tools', 'den-bundle'))

import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from assemble import assemble  # noqa: E402

E = 1e-6


def my_dates(obs, lines):
    cap = {o['obs_id']: o['capture_ts'] for o in obs}
    last, out = {}, []
    for l in lines:
        known = [cap[i] for i in l.get('last_obs_ids') or [] if i in cap]
        d = max(known) if known else None
        if l['track_id'] in last:
            d = last[l['track_id']] if d is None else max(d, last[l['track_id']])
        if d is None:
            continue
        last[l['track_id']] = d
        out.append((d, l))
    return out


def my_gated(obs, dated, tid, t0, t1):
    byid = {o['obs_id']: o for o in obs}
    st = {}
    for d, l in dated:
        if l['track_id'] != tid or d > t1 + E:
            continue
        for i in l.get('last_obs_ids') or []:
            st.setdefault(i, []).append(l['association']['status'])
    out = []
    for i, s in st.items():
        o = byid.get(i)
        if o and o['kind'] == 'keypoints' and o.get('validity', 'valid') == 'valid' and set(s) == {'confident'} \
                and t0 - E <= o['capture_ts'] <= t1 + E:
            out.append(i)
    return sorted(out)


def unsafe_spans(dated, tid):
    """[start, end) spans over which the track's latest line is not confident."""
    spans, start = [], None
    for d, l in dated:
        if l['track_id'] != tid:
            continue
        bad = l['association']['status'] != 'confident'
        if bad and start is None:
            start = d
        if not bad and start is not None:
            spans.append((start, d))
            start = None
    if start is not None:
        spans.append((start, float('inf')))
    return spans


def held_variant():
    scene = synth.crossing()
    for l in scene['tracks']:
        if l['association']['status'] != 'confident':
            l['last_obs_ids'] = []
    return scene


def main():
    m = method.load_method()
    w = m['window_s']
    fails = {'coverage': 0, 'gated': 0, 'assoc': 0, 'verbatim': 0}
    counts = {'bundles': 0, 'entries': 0, 'crossing_bundles': 0}
    tmp = tempfile.mkdtemp(prefix='probe-r5-recompute-')
    try:
        scenes = dict((k, f()) for k, f in synth.SCENES.items())
        scenes['crossing_held'] = held_variant()
        for name, scene in sorted(scenes.items()):
            obs, lines = scene['observations'], scene['tracks']
            st = records.Store(obs, lines)
            dated = my_dates(obs, lines)
            tids = sorted({l['track_id'] for _, l in dated})
            bundles = []
            for tid in tids:
                for k in range(0, 141):
                    t1 = synth.ts(k * 0.1)
                    b = assemble(st, m, tid, t1)
                    bundles.append(b)
                    t0 = t1 - w
                    counts['bundles'] += 1
                    if b['interval'] != [t0, t1] or any(not (t0 - E <= x['ts'] <= t1 + E)
                                                        for x in b['frames'] + b['keypoint_series'] + b['positions']):
                        fails['coverage'] += 1
                    want = my_gated(obs, dated, tid, t0, t1)
                    have = sorted(x['obs_id'] for x in b['keypoint_series'])
                    if want != have:
                        fails['gated'] += 1
                    if name.startswith('crossing'):
                        spans = unsafe_spans(dated, tid)
                        expect_safe = not any(t0 < e - E and t1 >= s - E for s, e in spans)
                        if b['association_safe'] != expect_safe:
                            fails['assoc'] += 1
                            if fails['assoc'] <= 3:
                                print('    assoc mismatch %s %s t1=%.1f: tool %s, hand %s (spans %s)' % (
                                    name, tid, t1 - synth.T0, b['association_safe'], expect_safe,
                                    [(round(s - synth.T0, 2), round(e - synth.T0, 2)) for s, e in spans]))
                        c0, c1 = scene['crossings'][0]['interval']
                        if t0 <= c1 + E and c0 <= t1 + E:
                            counts['crossing_bundles'] += 1
                            if b['association_safe'] is not False:
                                fails['assoc'] += 1
            path = os.path.join(tmp, name + '.jsonl')
            records.write_jsonl(path, bundles)
            byid = {o['obs_id']: o for o in obs}
            dump = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False)
            for b in records.read_jsonl(path):
                for e in b['keypoint_series']:
                    o = byid[e['obs_id']]
                    counts['entries'] += 1
                    pairs = (('obs_id', o['obs_id']), ('ts', o['capture_ts']), ('camera_id', o['source']['sensor_id']),
                             ('transform_id', o['transform_id']), ('keypoints', o['keypoints']),
                             ('joints_room', o['joints_room']))
                    if any(dump(e[k]) != dump(v) for k, v in pairs) or set(e) != {k for k, _ in pairs}:
                        fails['verbatim'] += 1
    finally:
        shutil.rmtree(tmp, True)
    for k, v in fails.items():
        print('%s %s: %d mismatches' % ('PASS' if not v else 'FAIL', k, v))
    print('    %d bundles, %d keypoint entries byte-compared, %d bundles meeting an annotated crossing' % (
        counts['bundles'], counts['entries'], counts['crossing_bundles']))
    bad = sum(fails.values())
    print('RESULT %s: %d mismatches' % ('PASS' if not bad else 'FAIL', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
