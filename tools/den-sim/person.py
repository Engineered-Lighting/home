"""Hop 15: person states, in canon's names (docs/architecture/live-v1/schemas.json `person_state`, at 6772ed39).

Rules plus unchanged need v0, standard library only. Rung 6 isn't built: this stands in for it, so a real rung 6 can
replace it. In: asks.jsonl (hop 12's join), bundles.jsonl (9), the fact lines (12), tracks.jsonl (8), observations.jsonl
(7, only to date and time each Track line, which carries no time of its own) and the room model (14). Out:
person_states.jsonl, and a card per track with no coordinate in it. need.py is loaded by file path from den-core's
archive, one instance per track (hop 13). Per reply, in the order they became available, it uses only what was
known by the reply's available_at (plan, hop 15 steps 1 to 8):

1. asks.jsonl gives the bundle and the track.
2. A bundle not association-safe, or a Track not confident, holds the interpreted fields: held_ambiguous, flag
   association_uncertain, and need is not handed the reply.
3. Otherwise that track's own need gets it; its focused_engagement keeps kind and onset_ts, the dialect canon allows.
4. Position: the bundle's newest positions[] entry. Heading: the newest Track line whose observations had all
   arrived, dated by the newest capture_ts it names; with none, held.
5. On the couch: the position within REACH_M of the seat's footprint, and the median height of supported shoulders
   and hips over WINDOW_S at most the seat's top plus ABOVE_SEAT_M; with no supported hip, shoulders alone at most
   SHOULDERS_ONLY_M above the floor (the Lab's zone_word and posture_word numbers).
   Both halves compare in whole millimetres, so a float's last bits can't flip a test that sits on a limit.
6. Region: seated gives sofa-book; else handheld, which isn't in the model, so no target. No wrists are needed.
7. Head: the median of supported face joints.
8. field_support, sources (the bundle and sofa-seat) and flags. A support time is the capture time of the newest
   evidence the field rests on, never the reply's arrival, and a held field keeps the one it had. A record is
   emitted when a field's value or support status changed, as need v0's own _changes keys it.

Left null on purpose: head_dir, head_cov, every confidence, visual_demand.level, embedding, episodes, motion,
affected. posture is canon's "unknown": nothing here has evidence for it. It reads no clock, file or environment
of its own beyond what it is handed.
"""
import importlib.util
import json
import math
import statistics

REACH_M = 0.15            # step 5: within 0.15 m of the footprint polygon (room_facts.zone_word)
ABOVE_SEAT_M = 0.45       # step 5: torso median at most the seat's top plus 0.45 m (zone_word)
SHOULDERS_ONLY_M = 1.20   # step 5: no supported hip: shoulders at most 1.20 m above the floor (posture_word, room_facts.py:309)
WINDOW_S = 2.0            # step 5: the joints of the bundle's last 2 s
SEAT, SEAT_LANDMARK, SPOT, HANDHELD, FORM = 'sofa', 'sofa-seat', 'sofa-book', 'handheld', 'polygon-3d/1'
SHOULDERS, HIPS, FACE = (5, 6), (11, 12), (0, 1, 2, 3, 4)   # COCO-17
COCO17 = ('nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
          'left_wrist', 'right_wrist', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')
PLACES, CARD_PLACES = 6, 2   # records in micrometres, as den-register rounds metres; the card in centimetres
COMPARE_PLACES = 3           # step 5's two halves compare in millimetres
INTERPRETER = {'path': 'rules', 'version': 'den-sim-person-1'}
NULL_REGION = {'center': None, 'extent_m': None, 'normal': None, 'confidence': None}


class NoCouch(Exception):
    """The room model holds no couch for the test: hop 15 reads MISSING, and says why."""


def load_need(path):
    """den-core's need.py, unchanged, from its archive by file path."""
    spec = importlib.util.spec_from_file_location('need', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def couch_of(room_lines):
    """The seat surface hop 14 wrote into the room model's current revision, and its landmark."""
    room = room_lines[-1] if room_lines else {}
    seats = [s for s in room.get('surfaces') or [] if s.get('surface_id') == SEAT and s.get('kind') == 'seat' and (s.get('geometry') or {}).get('form') == FORM]
    marks = [m for m in room.get('landmarks') or [] if m.get('landmark_id') == SEAT_LANDMARK and m.get('surface_id') == SEAT]
    if len(seats) != 1 or len(marks) != 1:
        raise NoCouch("the room model's current revision holds no seat surface %s with its landmark %s: hop 14 wrote none" % (SEAT, SEAT_LANDMARK))
    return seats[0]['geometry']


def inside(x, y, polygon):
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def edge_distance(x, y, polygon):
    """0 inside the polygon, else the distance to its nearest edge."""
    if inside(x, y, polygon):
        return 0.0
    best = math.inf
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / ((dx * dx + dy * dy) or 1.0)))
        best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
    return best


def joints(bundle, which, t):
    """[(ts, xyz)] of the supported joints named, from the bundle's keypoints of the WINDOW_S before its newest known at t."""
    series = [entry for entry in bundle.get('keypoint_series') or [] if entry['ts'] <= t]
    if not series:
        return []
    newest = max(entry['ts'] for entry in series)
    index = lambda j: j if isinstance(j, int) else COCO17.index(j)
    return [(entry['ts'], joint['xyz']) for entry in series if entry['ts'] >= newest - WINDOW_S for joint in entry['joints_room']
            if index(joint['j']) in which and joint.get('status') == 'supported' and joint.get('xyz')]


def seated(xy, bundle, couch, t):
    """Step 5: on the couch or not, and which half failed: 'position', 'height', or 'torso' when no joint can say.
    captured: the newest capture time of the joints it used."""
    polygon = [vertex[:2] for vertex in couch['vertices_m']]
    away = edge_distance(xy[0], xy[1], polygon)
    hips, shoulders = joints(bundle, HIPS, t), joints(bundle, SHOULDERS, t)
    if hips:
        torso, limit, basis = statistics.median(p[2] for _, p in hips + shoulders), couch['seat_point_m'][2] + ABOVE_SEAT_M, 'shoulders and hips'
    elif shoulders:
        torso, limit, basis = statistics.median(p[2] for _, p in shoulders), SHOULDERS_ONLY_M, 'shoulders alone'
    else:
        torso, limit, basis = None, None, None
    over = None if torso is None else round(torso - limit, COMPARE_PLACES)
    failed = (['position'] if round(away, COMPARE_PLACES) > REACH_M else []) + (['torso'] if torso is None else ['height'] if over > 0 else [])
    return dict(on=not failed, failed=failed, footprint_m=round(away, CARD_PLACES), torso_margin_m=None if torso is None else round(torso - limit, CARD_PLACES),
                torso_from=basis, captured=max((ts for ts, _ in hips + shoulders), default=None))


def median_point(points):
    return [round(statistics.median(p[i] for p in points), PLACES) for i in range(3)]


def track_lines(tracks, observations, track_id, t):
    """The Track lines of this track known by t: every observation each names had arrived by t, and its date (the newest
    capture_ts it names) is no later. Lines naming none can't be dated, so they are left out."""
    known = []
    for line in tracks:
        if line.get('track_id') != track_id or not line.get('last_obs_ids'):
            continue
        named = [observations.get(oid) for oid in line['last_obs_ids']]
        if any(obs is None for obs in named):
            continue
        arrived = max(((obs.get('freshness') or {}).get('available_ts') or obs['capture_ts']) for obs in named)
        dated = max(obs['capture_ts'] for obs in named)
        if arrived <= t and dated <= t:
            known.append((dated, line))
    return known


def support(capture, value_set, status, hold=None):
    return {'last_support_capture_ts': capture, 'value_set_ts': value_set, 'support_status': status, 'hold_reason': hold, 'deadband': None, 'cov_ts': None}


def changes(record):
    """What emits a record when it changes: each field's value and support status, never a time (need v0's _changes)."""
    return json.dumps([record['position'] and record['position']['xyz'], record['working_region'], record['focused_engagement']['state'],
                       record['attention']['head_xyz'], record['heading'], record['posture'], record['flags'],
                       {name: value['support_status'] for name, value in record['field_support'].items()}], sort_keys=True)


def replies(asks, facts):
    """(ask row, fact line) per reply used: hop 12's fact line names the body's sha256 as its request id."""
    by_request = {line['request_id']: line for line in facts}
    return [(row, by_request[row['body_sha256']]) for row in asks if row.get('body_sha256') in by_request]


class Person:
    """One track: its own need, and what it last held."""

    def __init__(self, track_id, need_module):
        self.track_id, self.need = track_id, need_module.make_policy()
        self.engagement, self.engagement_support, self.engagement_sources, self.need_flags = None, None, [], []
        self.region, self.head, self.couch_test = None, None, None
        # (last_support_capture_ts, value_set_ts) of the working region and the head: capture times of the evidence used
        self.region_times, self.head_times = (None, None), (None, None)
        self.need_states, self.last_key = [], None
        self.replies, self.taken, self.engaged_reply = 0, 0, None   # this track's replies, those need was handed, and when it engaged

    def update(self, t, row, fact, bundle, known, couch):
        safe = bool(row.get('association_safe')) and bool(known) and (known[-1][1].get('association') or {}).get('status') == 'confident'
        self.replies += 1
        if safe:   # step 3: this track's own need
            self.taken += 1
            for out in self.need.handle({'t': t, 'kind': 'facts', 'data': fact}):
                self.need_states.append(out['data']['focused_engagement']['state'])
            record = self.need._record(t)
            self.engagement = {key: record['focused_engagement'][key] for key in ('state', 'kind', 'since_s', 'onset_ts', 'confidence')}
            self.engagement_support = dict(support(None, None, None), **record['field_support']['focused_engagement'])
            self.engagement_sources, self.need_flags = record['sources']['focused_engagement'], record['flags']
            if self.engaged_reply is None and self.engagement['state'] == 'engaged':
                self.engaged_reply = self.replies
        positions = [entry for entry in bundle.get('positions') or [] if entry['ts'] <= t]
        newest = max(positions, key=lambda entry: entry['ts']) if positions else None
        position = newest and {'xyz': [newest['x'], newest['y'], 0.0], 'cov': newest.get('cov'), 'cov_ts': newest['ts'], 'predicted': False}
        line = known[-1] if known else None
        heading = line and line[1].get('heading')
        if safe and position:   # steps 5 to 7, each supported at the capture time of the newest evidence it used
            self.couch_test = seated(position['xyz'], bundle, couch, t)
            face = joints(bundle, FACE, t)
            region, head = dict(NULL_REGION, surface_id=SPOT if self.couch_test['on'] else HANDHELD), median_point([p for _, p in face]) if face else None
            captured = max(ts for ts in (newest['ts'], self.couch_test['captured']) if ts is not None)
            self.region_times = (captured, captured if region != self.region else self.region_times[1])
            face_at = max((ts for ts, _ in face), default=None)
            self.head_times = (face_at, face_at if head != self.head else self.head_times[1]) if head else (None, None)
            self.region, self.head = region, head
        held = lambda times: support(*times, 'held_ambiguous', 'association')   # a held field keeps the support it had
        engagement_support = self.engagement_support if safe else dict(self.engagement_support or support(None, None, None), support_status='held_ambiguous', hold_reason='association')
        field_support = {
            'position': dict(support(newest['ts'], newest['ts'], 'observed'), cov_ts=newest['ts']) if newest else support(None, None, 'held_unavailable', 'occluded'),
            'heading': support(line[1].get('heading_as_of'), line[1].get('heading_as_of'), 'observed') if heading else support(None, None, 'held_unavailable', 'source_silent'),
            'working_region': support(*self.region_times, 'observed') if safe and position else held(self.region_times),
            'attention': (support(*self.head_times, 'observed') if self.head else support(None, None, 'held_unavailable', 'occluded')) if safe and position else held(self.head_times),
            'focused_engagement': engagement_support,
        }
        record = {
            'schema_version': 'person_state/v1', 'state_id': '%s@%r' % (self.track_id, float(t)), 'emitted_ts': t, 'track_id': self.track_id, 'identity': 'unbound',
            'position': position,
            'working_region': self.region,
            'focused_engagement': self.engagement or {'state': 'none', 'kind': 'reading', 'since_s': None, 'onset_ts': None, 'confidence': None},
            'visual_demand': {'level': None, 'basis': None, 'confidence': None},
            'attention': {'head_xyz': self.head, 'head_cov': None, 'head_dir': None, 'sd_deg': None, 'target': None, 'confidence': None},
            'heading': heading or None, 'posture': 'unknown', 'motion': None, 'affected': None, 'embedding': None, 'episodes': None,
            'flags': sorted(set(([] if safe else ['association_uncertain']) + list(self.need_flags))),
            'sources': {'position': [{'origin': 'rules', 'ref': bundle['bundle_id']}] if newest else [],
                        'heading': [{'origin': 'rules', 'ref': line[1]['last_obs_ids'][-1]}] if heading else [],
                        'working_region': [{'origin': 'rules', 'ref': bundle['bundle_id']}, {'origin': 'rules', 'ref': SEAT_LANDMARK}] if self.region else [],
                        'attention': [{'origin': 'rules', 'ref': bundle['bundle_id']}] if self.head else [],
                        'focused_engagement': self.engagement_sources},
            'field_support': field_support,
            'field_as_of': {name: value['last_support_capture_ts'] for name, value in field_support.items()},
            'interpreter': INTERPRETER,
        }
        changed, self.last_key = changes(record) != self.last_key, changes(record)
        return record, changed, safe


def states(asks, bundles, facts, tracks, observations, room_lines, need_module):
    """(records to emit, in order, and one card per track). Only what was known by each reply's available_at is used."""
    couch = couch_of(room_lines)
    by_bundle, by_obs = {bundle['bundle_id']: bundle for bundle in bundles}, {obs['obs_id']: obs for obs in observations}
    timed = sorted(((need_module.parse_iso(fact['available_at']), row['request_id'], row, fact) for row, fact in replies(asks, facts)), key=lambda item: item[:2])
    people, out, last = {}, [], {}
    for t, _, row, fact in timed:
        bundle = by_bundle.get(row['bundle_id'])
        if bundle is None:
            continue
        if row['track_id'] not in people:   # one need per track, made once
            people[row['track_id']] = Person(row['track_id'], need_module)
        person = people[row['track_id']]
        record, changed, safe = person.update(t, row, fact, bundle, track_lines(tracks, by_obs, row['track_id'], t), couch)
        if changed:
            out.append(record)
        last[row['track_id']] = (record, safe)
    return out, [card(people[track], record, safe, couch) for track, (record, safe) in sorted(last.items())]


def card(person, record, safe, couch):
    """What the page's person card says, with no coordinate: distances, words and states only."""
    position, test = record['position'], person.couch_test or {}
    seat = couch['seat_point_m']
    return dict(track_id=person.track_id, seat_distance_m=round(math.hypot(position['xyz'][0] - seat[0], position['xyz'][1] - seat[1]), CARD_PLACES) if position else None,
                on_couch=test.get('on'), failed=test.get('failed'), footprint_m=test.get('footprint_m'), torso_margin_m=test.get('torso_margin_m'),
                torso_from=test.get('torso_from'), region=(record['working_region'] or {}).get('surface_id'), engagement=record['focused_engagement']['state'],
                support=record['field_support']['focused_engagement']['support_status'], held=not safe, need_states=person.need_states,
                replies=person.replies, need_replies=person.taken, engaged_reply=person.engaged_reply)


def dumps(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def write(path, records):
    with open(path, 'w', encoding='utf-8', newline='\n') as sink:
        sink.write(''.join(dumps(record) + '\n' for record in records))
