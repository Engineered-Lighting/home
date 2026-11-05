"""Rung 5's gate, checked on bundles and router request records.

Bundle clauses (ladder rung 5):
- window:    each bundle's interval is window_s long;
- aligned:   every frame, keypoint entry and position lies inside the interval;
             every keypoint entry has its frame (same camera, same time); each
             other track's boxes line up with the frames; every crop is even,
             inside the frame, and holds the latest boxed frame's box;
- verbatim:  keypoint_series holds every association-gated Observation in the
             interval and nothing else, each copied field equal to the record's;
- missing:   any source the bundle shows as absent or stale is in `missing`;
- crossing:  association_safe is false for every bundle of a track whose
             interval meets an annotated crossing of that track;
- no_wait:   each routed bundle ends at its request's send time.
Router clauses (ROUTER-V0.md): in_flight, interval, per_minute, backoff,
round_robin, stamped.

The checks are written apart from assemble.py so they don't repeat its
mistakes. A practice check runs with open slots and says so; a scored check
refuses while any slot is open.
"""
from records import EPS, camera_of, is_valid

BUNDLE_CLAUSES = ('window', 'aligned', 'verbatim', 'missing', 'crossing', 'no_wait')
ROUTER_CLAUSES = ('in_flight', 'interval', 'per_minute', 'backoff', 'round_robin', 'stamped')


def _within(ts, t0, t1):
    return isinstance(ts, (int, float)) and t0 - EPS <= ts <= t1 + EPS


def _holes(times, t0, t1, max_gap):
    pts = [t0] + sorted(times) + [t1]
    return not times or any(b - a > max_gap + EPS for a, b in zip(pts, pts[1:]))


def _gated_ids(store, track_id, t0, t1):
    statuses = {}
    for line in store.lines.get(track_id, []):
        if line.ts > t1 + EPS:
            continue
        for oid in line.rec.get('last_obs_ids') or []:
            statuses.setdefault(oid, set()).add((line.rec.get('association') or {}).get('status'))
    out = set()
    for oid, st in statuses.items():
        o = store.obs.get(oid)
        if o and o.get('kind') == 'keypoints' and is_valid(o) and st == {'confident'} \
                and _within(o['capture_ts'], t0, t1):
            out.add(oid)
    return out


def _depth_ok(o, skew):
    d = o.get('depth_ts')
    if not isinstance(d, (int, float)) or abs(d - o['capture_ts']) > skew + EPS:
        return False
    return not any(str(j.get('lift') or '').startswith('plane') for j in o.get('joints_room') or [])


def check_bundle(b, store, method):
    """{clause: [failure text]} for one bundle (crossing and no_wait are checked across bundles)."""
    f = {c: [] for c in BUNDLE_CLAUSES}
    t0, t1 = b['interval']
    if abs((t1 - t0) - method['window_s']) > EPS:
        f['window'].append('interval is %.3f s, not %.3f s' % (t1 - t0, method['window_s']))

    frames, kps, pos = b['frames'], b['keypoint_series'], b['positions']
    for what, items in (('frame', frames), ('keypoint entry', kps), ('position', pos)):
        for item in items:
            if not _within(item.get('ts'), t0, t1):
                f['aligned'].append('%s at %r is outside the interval' % (what, item.get('ts')))
    frame_keys = {(fr['camera_id'], fr['ts']) for fr in frames}
    for k in kps:
        if (k['camera_id'], k['ts']) not in frame_keys:
            f['aligned'].append('keypoint entry %s has no frame' % k['obs_id'])
    for other in b['others']:
        if len(other['image_box']) != len(frames):
            f['aligned'].append('others %s: %d boxes for %d frames' % (other['track_id'], len(other['image_box']), len(frames)))
    for cam in {fr['camera_id'] for fr in frames}:
        boxed = [fr for fr in frames if fr['camera_id'] == cam and fr['image_box'] is not None]
        refs = [fr['crop_ref'] for fr in frames if fr['camera_id'] == cam]
        if not boxed:
            if any(r is not None for r in refs):
                f['aligned'].append('%s has a crop but no boxed frame' % cam)
            continue
        latest = max(boxed, key=lambda fr: fr['ts'])
        if any(r != refs[0] for r in refs) or refs[0] is None:
            f['aligned'].append('%s: frames do not share one crop' % cam)
            continue
        crop, (w, h) = refs[0]['box'], method['frame_px'][cam]
        x0, y0, x1, y1 = latest['image_box']
        if abs(refs[0]['from_ts'] - latest['ts']) > EPS:
            f['aligned'].append('%s: crop is not from the latest boxed frame' % cam)
        if any(v % 2 for v in crop) or crop[0] < 0 or crop[1] < 0 or crop[2] > w or crop[3] > h:
            f['aligned'].append('%s: crop %r is not even or leaves the frame' % (cam, crop))
        if not (crop[0] <= max(0, x0) and crop[1] <= max(0, y0) and crop[2] >= min(w, x1) and crop[3] >= min(h, y1)):
            f['aligned'].append('%s: crop %r does not hold the box %r' % (cam, crop, latest['image_box']))

    want = _gated_ids(store, b['track_id'], t0, t1)
    have = [k['obs_id'] for k in kps]
    if len(set(have)) != len(have):
        f['verbatim'].append('an observation appears twice')
    for oid in sorted(want - set(have)):
        f['verbatim'].append('association-gated %s is left out' % oid)
    for oid in sorted(set(have) - want):
        f['verbatim'].append('%s is not association-gated' % oid)
    for k in kps:
        o = store.obs.get(k['obs_id'])
        if o is None:
            continue
        expect = {'ts': o['capture_ts'], 'camera_id': camera_of(o), 'transform_id': o.get('transform_id'),
                  'keypoints': o.get('keypoints'), 'joints_room': o.get('joints_room')}
        for field, value in expect.items():
            if k.get(field) != value:
                f['verbatim'].append('%s: %s differs from the observation' % (k['obs_id'], field))

    gap, missing = method['max_gap_s'], set(b['missing'])
    shows = {
        'track': _holes([p['ts'] for p in pos], t0, t1, gap),
        'person_box': _holes([fr['ts'] for fr in frames if fr['image_box'] is not None], t0, t1, gap),
        'keypoints': _holes([k['ts'] for k in kps], t0, t1, gap),
        'depth': not kps or not all(_depth_ok(store.obs[k['obs_id']], method['depth_skew_s'])
                                    for k in kps if k['obs_id'] in store.obs),
        'object_box': any(ob['room_position'] is None for ob in b['objects']),
    }
    for source, absent in shows.items():
        if absent and source not in missing:
            f['missing'].append('%s is absent or stale but not flagged' % source)
    return f


def check(bundles, store, method, crossings=(), requests=None, scored=False, rule=None):
    """The whole gate. Returns {'verdict', 'practice', 'open', 'clauses': {name: {'checked', 'failures'}}}."""
    settings = [method] + ([rule] if rule is not None else [])
    if scored:
        for s in settings:
            s.require_closed('a scored gate check')
    clauses = {c: {'checked': 0, 'failures': []} for c in BUNDLE_CLAUSES}
    for b in bundles:
        for clause, fails in check_bundle(b, store, method).items():
            if clause in ('crossing', 'no_wait'):
                continue
            clauses[clause]['checked'] += 1
            clauses[clause]['failures'] += ['%s: %s' % (b['bundle_id'], x) for x in fails]

    for c in crossings:
        c0, c1 = c['interval']
        for b in bundles:
            t0, t1 = b['interval']
            if b['track_id'] in c['track_ids'] and t0 <= c1 + EPS and c0 <= t1 + EPS:
                clauses['crossing']['checked'] += 1
                if b['association_safe'] is not False:
                    clauses['crossing']['failures'].append(
                        '%s meets crossing %s but association_safe is %r' % (b['bundle_id'], c['crossing_id'], b['association_safe']))

    if requests is not None:
        by_id = {b['bundle_id']: b for b in bundles}
        for r in requests:
            b = by_id.get(r['bundle_id'])
            clauses['no_wait']['checked'] += 1
            if b is None:
                if r.get('outcome') != 'no_box':
                    clauses['no_wait']['failures'].append('%s: bundle %s is not in the bundles' % (r['request_id'], r['bundle_id']))
            elif abs(b['interval'][1] - r['sent_ts']) > EPS:
                clauses['no_wait']['failures'].append('%s: bundle ends %.3f s before its send' % (r['request_id'], r['sent_ts'] - b['interval'][1]))
        if rule is not None:
            for clause, fails in check_requests(requests, rule, store.order).items():
                clauses[clause] = {'checked': len(requests), 'failures': fails}

    failed = any(c['failures'] for c in clauses.values())
    open_slots = ['%s:%s' % (s.source, k) for s in settings for k in s.open]
    return {'verdict': 'fail' if failed else 'pass', 'practice': bool(open_slots),
            'open': open_slots, 'clauses': clauses}


def check_requests(requests, rule, order):
    """{clause: [failure text]} for a router request log, against ROUTER-V0's rule."""
    f = {c: [] for c in ROUTER_CLAUSES}
    sent = [r for r in requests if r.get('outcome') != 'no_box']
    for prev, r in zip(sent, sent[1:]):
        if prev.get('done_ts') is None or r['sent_ts'] < prev['done_ts'] - EPS:
            f['in_flight'].append('%s started while %s was in flight' % (r['request_id'], prev['request_id']))
        if r['sent_ts'] - prev['sent_ts'] < rule['min_interval_s'] - EPS:
            f['interval'].append('%s started %.3f s after %s' % (r['request_id'], r['sent_ts'] - prev['sent_ts'], prev['request_id']))
        if prev.get('outcome') != 'ok' and prev.get('done_ts') is not None \
                and r['sent_ts'] < prev['done_ts'] + rule['backoff_s'] - EPS:
            f['backoff'].append('%s started %.3f s after %s ended %s' % (
                r['request_id'], r['sent_ts'] - prev['done_ts'], prev['request_id'], prev.get('outcome')))
    cap, minute = rule['max_per_minute'], rule['minute_s']
    for i, r in enumerate(sent):
        window = [s for s in sent[:i + 1] if s['sent_ts'] > r['sent_ts'] - minute + EPS]
        if len(window) > cap:
            f['per_minute'].append('%s is start %d in %g s' % (r['request_id'], len(window), minute))
    last = None
    for r in requests:
        if r['track_id'] not in r['tracks']:
            f['round_robin'].append('%s served %s, which was not eligible' % (r['request_id'], r['track_id']))
        elif last is not None and last in order:
            i = order.index(last)
            expected = next(t for t in order[i + 1:] + order[:i + 1] if t in r['tracks'])
            if r['track_id'] != expected:
                f['round_robin'].append('%s served %s, not %s' % (r['request_id'], r['track_id'], expected))
        last = r['track_id']
        stamp = r.get('rule') or {}
        if stamp.get('version') != rule.version or stamp.get('sha256') != rule.sha256:
            f['stamped'].append('%s does not carry %s %s' % (r['request_id'], rule.version, rule.sha256[:12]))
    return f
