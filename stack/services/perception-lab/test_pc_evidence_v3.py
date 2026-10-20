"""PC-input evidence v3 (session c88fe5b6 over-called computer_work):
computer_active_v3 buckets, PC input gated on a camera cue at the PC, a held
book/phone as a conflict, static scene objects kept out of activity cues and
phone relations judged against the phone's own length. Temp stores and
synthetic geometry; no network or model."""
from copy import deepcopy
import json
import unittest

import qwen_scene as scene
from ha_ingest import HAIngestor
from jev_evidence import QWEN_EGRESS, project
from jev_semantics import VERSION, semantic_state
from live_inputs import live_records
from test_ha_ingest import DERIVED, HAIngestTests, LAST_ACTIVE, SESSION, iso
from test_qwen_wiring import FACTS, draft
import test_paired_runtime as fixtures

RECENCY = {'session_entity': SESSION, 'unlocked_values': ['Unlocked'], 'last_active_entity': LAST_ACTIVE}
V3 = {**RECENCY, 'rule': 'computer_active_v3'}
BINDINGS = {'derived.computer_input_recency': 'computer_input_recency'}
COMPUTER = {**FACTS, 'held_object': 'none', 'book_state': 'not_visible', 'gaze_target': 'screen', 'typing_visible': 'yes',
            'screen_facing_subject': 'yes', 'activity_hypothesis': 'computer_work',
            'visible_basis': ['person_visible', 'hands_on_keyboard']}
ELSEWHERE = {**FACTS, 'held_object': 'none', 'book_state': 'not_visible', 'gaze_target': 'elsewhere', 'typing_visible': 'no',
             'screen_facing_subject': 'not_visible', 'activity_hypothesis': 'resting', 'visible_basis': ['person_visible']}


def qwen_records(facts, cutoff=100.0, session='s'):
    receipts = [{**draft({'id': session}, t, facts=facts, request='r%d' % i), 'available_at': t+.6, 'first_received_at': t+.6}
                for i, t in enumerate((cutoff-8, cutoff-4, cutoff-1))]
    record = scene.scene_record(scene.projection(receipts, cutoff), receipts, cutoff, site_id='victoria', camera_id='den',
                                session_id=session)
    return [dict(r, session_id=session) for r in scene.shared_records(record, arms={'a', 'b'}, generation=1)['a']]


def ha_record(recency, session='s', **extra):
    return {'id': '8', 'site_id': 'victoria', 'camera_id': 'den', 'session_id': session, 'arm': 'a', 'generation': 1,
            'source_kind': 'ha.observation', 'observed_at': 99.6, 'available_at': 99.7, 'valid_until': 102.6,
            'evidence_roots': ['ha:pc:1'],
            'ha': {'derived.computer_input_recency': {'state': recency}, **extra}}


def frame(ident, t, objects, poses=(), session='s'):
    return {'id': ident, 'site_id': 'victoria', 'camera_id': 'den', 'session_id': session, 'arm': 'a', 'generation': 1,
            'source_kind': 'perception.output', 'observed_at': t, 'available_at': t+.1, 'valid_until': t+2.5,
            'evidence_roots': [ident], 'quality': {'image_width': 1280, 'image_height': 720},
            'objects': [{'label': label, 'instance_id': '%s-%d' % (label, i), 'pixel_box': dict(zip('xywh', box))}
                        for i, (label, box) in enumerate(objects)],
            'pose_instances': [{'layout': 'coco17', 'instance_id': 'person-0',
                                'joints': [{'joint_id': j, 'pixel': list(p), 'score': .8, 'status': 'supported'} for j, p in joints]}
                               for joints in poses]}


PERSON = ('person', (500, 150, 260, 420))   # seated, 1280x720; diagonal ~494 px, so 0.08 is ~40 px


def numbers(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return []
    if isinstance(value, (int, float)):
        return [value]
    items = value.values() if isinstance(value, dict) else value
    return [n for item in items for n in numbers(item)]


class RecencyBucketTests(unittest.TestCase):
    setUp = HAIngestTests.setUp
    tearDown = HAIngestTests.tearDown

    def source(self, derived):
        return HAIngestor(self.store, site_id='victoria', camera_id='den', source_id='pc-ha', entities=[SESSION, LAST_ACTIVE],
                          derived=derived)

    def derive(self, derived, states):
        self.snapshots = getattr(self, 'snapshots', 0)+1
        self.store.clock = lambda: 400.0
        event_id = self.source(derived).ingest(self.session['id'], snapshot_id=f'pc{self.snapshots}', observed_at=399, states=states)
        return next(e for e in self.store.list_events(self.session['id']) if e['id'] == event_id)['payload']['ha']

    def test_v3_bucket_edges_lock_and_clock_skew(self):
        cases = [(399, 'under_15s'), (404, 'under_15s'),            # 5 s future skew tolerated
                 (404.5, None),                                     # beyond the skew allowance
                 (399-15, 'under_15s'), (399-15.5, '15_to_60s'),    # 15 s is inclusive
                 (399-60, '15_to_60s'), (399-61, '1_to_5_min'),
                 (399-300, '1_to_5_min'), (399-301, 'over_5_min')]
        for last, expected in cases:
            with self.subTest(last=last):
                fact = self.derive({'computer_input_recency': V3}, {SESSION: 'Unlocked', LAST_ACTIVE: iso(last)})['derived.computer_input_recency']
                self.assertEqual((fact['state'], fact['status']), (expected, 'unknown' if expected is None else 'known'))
                self.assertEqual(fact['provenance'], {'inputs': [SESSION, LAST_ACTIVE], 'rule': 'computer_active_v3'})
        for states, expected in (({SESSION: 'Locked', LAST_ACTIVE: iso(398)}, 'locked'),
                                 ({SESSION: 'Unknown', LAST_ACTIVE: iso(398)}, None),
                                 ({SESSION: 'Unlocked', LAST_ACTIVE: 'unavailable'}, None),
                                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(398, '')}, None)):
            with self.subTest(states=states):
                self.assertEqual(self.derive({'computer_input_recency': V3}, states)['derived.computer_input_recency']['state'], expected)

    def test_v1_and_v2_configs_keep_their_meaning(self):
        states = {SESSION: 'Unlocked', LAST_ACTIVE: iso(389)}
        for derived, rule in (({**DERIVED, 'computer_input_recency': RECENCY}, 'computer_active_v2'),
                              ({'computer_input_recency': {**RECENCY, 'rule': 'computer_active_v2'}}, 'computer_active_v2'),
                              ({**DERIVED, 'computer_input_recency': V3}, 'computer_active_v3')):
            with self.subTest(rule=rule):
                facts = self.derive(derived, states)
                self.assertEqual(facts['derived.computer_input_recency']['provenance']['rule'], rule)
                self.assertEqual(facts['derived.computer_input_recency']['state'],
                                 'under_15s' if rule == 'computer_active_v3' else 'under_60s')
                if 'computer_active' in derived:
                    self.assertEqual((facts['derived.computer_active']['state'],
                                      facts['derived.computer_active']['provenance']['rule']), ('on', 'computer_active_v1'))

    def test_rule_key_is_a_closed_choice_for_the_recency_fact_only(self):
        for derived in ({'computer_input_recency': {**RECENCY, 'rule': 'computer_active_v4'}},
                        {'computer_input_recency': {**RECENCY, 'rule': 'computer_active_v1'}},
                        {'computer_input_recency': {**RECENCY, 'rule': None}},
                        {'computer_input_recency': {**V3, 'max_idle_seconds': 60}},
                        {'computer_active': {**DERIVED['computer_active'], 'rule': 'computer_active_v3'}}):
            with self.subTest(derived=derived), self.assertRaises(ValueError):
                self.source(derived)

    def test_projection_carries_only_the_v3_bucket(self):
        stamp = iso(390)
        self.store.clock = lambda: 400.0
        self.source({'computer_input_recency': V3}).ingest(self.session['id'], snapshot_id='pc-a', observed_at=399,
                                                           states={SESSION: 'Unlocked', LAST_ACTIVE: stamp})
        projected = project(live_records(self.store, self.session, 1, 400), ha_bindings=BINDINGS)
        self.assertTrue(projected)
        for record in projected:
            self.assertEqual(record['ha'], {'computer_input_recency': 'under_15s'})
            text = json.dumps(record)
            for private in ('formd', 'lastactive', 'derived.', stamp, 'Unlocked', 'computer_active_v3', 'provenance'):
                self.assertNotIn(private, text)
        self.assertEqual(project(projected, ha_bindings=BINDINGS), projected)


class CorroborationTests(unittest.TestCase):
    def state(self, recency, facts, frames=(), **extra):
        records = project([*frames, ha_record(recency, **extra)],
                          ha_bindings={**BINDINGS, **{k: v for k, v in (('switch.pc', 'computer_active'),) if k in extra}})
        return semantic_state(records + project(qwen_records(facts)), 100)

    def computer(self, *args, **kw):
        return self.state(*args, **kw)['corroboration']['activities']['computer_work']

    def test_pc_input_counts_only_under_15s_with_a_camera_cue(self):
        work = self.computer('under_15s', COMPUTER)
        self.assertEqual((work['independent'], work['independent_support'], work['pc_input'], work['conflicts']),
                         (['computer_input_recency'], 'one', 'pc_input_under_15s_with_camera_cue', []))
        self.assertIn('qwen_scene', work['same_camera'])
        # Each Qwen cue on its own is enough.
        for key, value in (('gaze_target', 'screen'), ('typing_visible', 'yes'), ('screen_facing_subject', 'yes')):
            with self.subTest(cue=key):
                self.assertEqual(self.computer('under_15s', {**ELSEWHERE, key: value})['pc_input'], 'pc_input_under_15s_with_camera_cue')
        # A wrist near the keyboard is a detector cue.
        hands = frame('7', 99.5, [PERSON, ('keyboard', (560, 470, 160, 40))], [[(9, (600, 480))]])
        work = self.computer('under_15s', ELSEWHERE, frames=[hands])
        self.assertEqual((work['pc_input'], work['independent']), ('pc_input_under_15s_with_camera_cue', ['computer_input_recency']))
        self.assertIn('detector_pose_relation', work['same_camera'])

    def test_recent_input_without_a_cue_is_named_and_never_independent(self):
        for recency, facts in (('under_15s', ELSEWHERE), ('15_to_60s', COMPUTER), ('under_60s', COMPUTER), ('under_60s', ELSEWHERE)):
            with self.subTest(recency=recency, cue=facts is COMPUTER):
                work = self.computer(recency, facts)
                self.assertEqual((work['pc_input'], work['independent'], work['independent_support']),
                                 ('pc_input_recent_but_not_observed_at_pc', [], 'none'))
        # The v1 fact alone never counts either, even with a cue.
        work = self.computer('unknown', COMPUTER, **{'switch.pc': {'state': 'on'}})
        self.assertEqual((work['pc_input'], work['independent']), ('pc_input_recent_but_not_observed_at_pc', []))
        for recency in ('1_to_5_min', 'over_5_min', 'locked'):
            self.assertEqual(self.computer(recency, COMPUTER)['pc_input'], 'pc_input_not_recent')
        self.assertEqual(self.computer('unknown', COMPUTER)['pc_input'], 'pc_input_unknown')

    def test_a_held_book_or_phone_is_a_conflict_not_support(self):
        for held in ('book', 'phone'):
            with self.subTest(held=held):
                work = self.computer('under_15s', {**COMPUTER, 'held_object': held})
                self.assertEqual((work['pc_input'], work['conflicts'], work['independent'], work['independent_support']),
                                 ('pc_input_recent_but_conflicts_with_held_object', ['qwen_held_object_' + held], [], 'none'))
        self.assertEqual(self.computer('over_5_min', {**ELSEWHERE, 'held_object': 'book'})['conflicts'], ['qwen_held_object_book'])
        # Reading keeps its own support while PC input is recent.
        reading = self.state('under_15s', FACTS)['corroboration']['activities']['reading']
        self.assertEqual(reading['same_camera'], ['qwen_scene'])

    def test_blocks_hold_no_numbers(self):
        hands = frame('7', 99.5, [PERSON, ('keyboard', (560, 470, 160, 40)), ('tv', (40, 60, 300, 180))], [[(9, (600, 480))]])
        state = self.state('under_15s', COMPUTER, frames=[hands])
        self.assertEqual(numbers(state['corroboration']), [])
        self.assertEqual(numbers(state['static_scene_objects']), [])


class StaticSceneTests(unittest.TestCase):
    def frames(self, n=3):
        # The tv box jitters by a pixel; the book moves with the reader's hands.
        return [frame(str(10+i), 97+i, [PERSON, ('tv', (40+i % 2, 60, 300, 180)), ('book', (600+25*i, 330+15*i, 60, 40))])
                for i in range(n)]

    def test_stationary_tv_is_background_and_a_moving_book_stays_a_cue(self):
        state = semantic_state(project(self.frames()), 99.5)
        for observation in state['observations']:
            self.assertEqual((observation['detected_objects'], observation['background_objects']), (['book', 'person'], ['tv']))
        self.assertEqual(state['static_scene_objects'], {'rule': 'static-scene-1', 'basis': 'this_packet_visual_observations',
                                                         'stationarity': 'compared', 'background_static': ['tv']})
        self.assertEqual(state['version'], VERSION)

    def test_a_tv_is_never_a_screen_cue(self):
        records = project([*self.frames(), ha_record('under_15s')], ha_bindings=BINDINGS)
        work = semantic_state(records + project(qwen_records(ELSEWHERE)), 99.5)['corroboration']['activities']['computer_work']
        self.assertEqual((work['pc_input'], work['independent']), ('pc_input_recent_but_not_observed_at_pc', []))

    def test_a_held_static_object_and_a_still_keyboard_stay_cues(self):
        frames = [frame(str(10+i), 97+i, [PERSON, ('keyboard', (560, 470, 160, 40)), ('tv', (40, 60, 300, 180))],
                        [[(9, (600, 480))]]) for i in range(3)]
        state = semantic_state(project(frames), 99.5)
        self.assertEqual(state['observations'][-1]['detected_objects'], ['keyboard', 'person'])
        self.assertEqual(state['observations'][-1]['background_objects'], ['tv'])

    def test_objects_seen_with_no_person_present_are_background(self):
        empty = frame('20', 99, [('tv', (40, 60, 300, 180)), ('book', (600, 330, 60, 40))])
        state = semantic_state(project([empty]), 99.5)
        self.assertEqual((state['observations'][0]['detected_objects'], state['observations'][0]['background_objects']),
                         ([], ['book', 'tv']))
        self.assertEqual(state['static_scene_objects']['stationarity'], 'too_few_frames')
        # Camera occupancy off at the frame tags everything but the person box.
        occupancy = {'id': '19', 'site_id': 'victoria', 'camera_id': 'den', 'session_id': 's', 'arm': 'a', 'generation': 1,
                     'source_kind': 'ha.observation', 'observed_at': 98.5, 'available_at': 98.6, 'valid_until': 101,
                     'evidence_roots': ['ha:occ'], 'ha': {'binary_sensor.occ': {'state': 'off'}}}
        seen = frame('21', 99, [PERSON, ('tv', (40, 60, 300, 180))])
        state = semantic_state(project([occupancy, seen], ha_bindings={'binary_sensor.occ': 'person_present'}), 99.5)
        observation = next(o for o in state['observations'] if o['evidence_id'] == '21')
        self.assertEqual((observation['detected_objects'], observation['background_objects']), (['person'], ['tv']))
        # A single frame with a person present is not compared, so nothing is tagged.
        state = semantic_state(project([self.frames(1)[0]]), 99.5)
        self.assertEqual((state['observations'][0]['background_objects'], state['static_scene_objects']['stationarity']),
                         ([], 'too_few_frames'))


class PhoneRelationTests(unittest.TestCase):
    """Held-phone geometry: the wrist sits below a small phone box, about one
    phone length away, which the person-scale 0.08 bound alone misses."""
    def facts(self, objects, joints):
        return semantic_state(project([frame('7', 99.5, [PERSON, *objects], [joints])]), 100)['observations'][0]['facts']

    def test_wrist_below_a_held_phone_is_near_it(self):
        phone = ('cell phone', (610, 330, 34, 60))
        # 50 px below the phone: over 0.08 x 494 px (~40 px), within one phone length (60 px).
        facts = self.facts([phone], [(10, (625, 440))])
        self.assertEqual([(f['fact'], f['basis'], f['physical_contact']) for f in facts],
                         [('right_wrist_near_cell_phone_in_image', 'within_phone_length', 'unknown')])
        self.assertGreater(facts[0]['normalized_distance'], .08)
        # The same gap to a book is not a relation: the margin is phone-only.
        self.assertEqual(self.facts([('book', (610, 330, 34, 60))], [(10, (625, 440))]), [])
        # A phone out of reach is not near.
        self.assertEqual(self.facts([('cell phone', (1000, 330, 34, 60))], [(10, (625, 440))]), [])
        # The person-scale rule still applies and says so.
        self.assertEqual(self.facts([phone], [(9, (625, 395))])[0]['basis'], 'within_person_scale')

    def test_live_held_phone_geometry_2026_09_29(self):
        # Session c88fe5b6 at ~121 s: phone box 29x52 px, left wrist 62 px below
        # it, person box 497x295. One phone length (52 px) missed it.
        objects = [('cell phone', (383, 109, 29, 52))]
        found = semantic_state(project([frame('8', 99.5, [('person', (141, 62, 497, 295)), *objects],
                                              [[(9, (391, 223)), (10, (346, 215))]])]), 100)['observations'][0]['facts']
        self.assertIn('left_wrist_near_cell_phone_in_image', [f['fact'] for f in found])

    def test_phone_near_the_face_and_corroboration(self):
        phone = ('cell phone', (640, 200, 30, 55))
        facts = self.facts([phone], [(0, (620, 215)), (1, (612, 205))])
        self.assertEqual([(f['fact'], f['basis']) for f in facts], [('phone_near_face_in_image', 'within_phone_length')])
        self.assertNotIn('normalized_distance', facts[0])
        state = semantic_state(project([frame('7', 99.5, [PERSON, phone], [[(0, (620, 215))]])]), 100)
        self.assertEqual(state['corroboration']['activities']['phone_use']['same_camera'], ['detector_pose_relation'])
        self.assertEqual(state['observations'][0]['detected_objects'], ['cell phone', 'person'])
        # Face joints that are not supported give no relation.
        record = frame('7', 99.5, [PERSON, phone], [[(0, (620, 215))]])
        record['pose_instances'][0]['joints'][0]['status'] = 'uncertain'
        self.assertEqual(semantic_state(project([record]), 100)['observations'][0]['facts'], [])


class PolicyTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_the_gate_recomputes_and_admits_the_v3_packet(self):
        from jev_policy import LabJevPolicy
        from reasoning import build_jev_request
        cutoff, sid = 100.0, self.session['id']
        self.store.clock = lambda: cutoff+.5
        policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'v3', 'generation': 1,
                  'expires_at': cutoff+100, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13',
                  'max_calls_per_minute': 20, 'device_context': 'den-devices-1', 'structured_sources': [QWEN_EGRESS]}
        gate = LabJevPolicy(self.store, lambda: policy, lambda: {'state': 'on', 'received_at': cutoff+.5}, ha_bindings=BINDINGS)
        self.store.db.execute('CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)')
        frames = [frame(str(10+i), 97.5+i, [PERSON, ('tv', (40, 60, 300, 180)), ('keyboard', (560, 470, 160, 40))],
                        [[(9, (600, 480))]], session=sid) for i in range(3)]
        records = project([*frames, ha_record('under_15s', session=sid)], ha_bindings=BINDINGS)
        packet, provenance = build_jev_request(records, model='jev-1.13', site_id='victoria', session_id=sid, arm='a', generation=1,
                                               cutoff=cutoff, retain_history=True, semantic=True,
                                               shared_context=project(qwen_records(COMPUTER, cutoff, sid)))
        semantic = packet['state']['semantic']
        self.assertEqual((semantic['version'], provenance['question_version']), ('den-semantics-7', 'den-activity-7-semantic'))
        self.assertEqual(semantic['corroboration']['activities']['computer_work']['pc_input'], 'pc_input_under_15s_with_camera_cue')
        self.assertEqual(semantic['static_scene_objects']['background_static'], ['tv'])
        self.assertIn('PC input activity alone does not establish', packet['questions']['activity']['instructions'])
        self.assertIn('PC input activity alone does not establish', packet['questions']['computer_work_supported']['instructions'])
        context = {'site_id': 'victoria', 'session_id': sid, 'arm': 'a', 'generation': 1}
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)', ('ok', sid, 'dispatching'))
        self.assertTrue(gate({**context, 'request_id': 'ok'}, packet, 'send')['allowed'])
        # A packet whose code-built block was edited no longer matches the recompute.
        for change in (lambda s: s['corroboration']['activities']['computer_work'].update(pc_input='pc_input_not_recent'),
                       lambda s: s['observations'][-1].update(detected_objects=['keyboard', 'person', 'tv'], background_objects=[])):
            forged = deepcopy(packet)
            change(forged['state']['semantic'])
            request = 'forged%d' % id(change)
            self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)', (request, sid, 'dispatching'))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
                gate({**context, 'request_id': request}, forged, 'send')


if __name__ == '__main__':
    unittest.main()
