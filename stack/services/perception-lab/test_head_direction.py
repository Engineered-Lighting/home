"""Head direction (head-direction-1) as a code-computed camera fact, and
corroboration-3 (head toward the site's monitor side lets recent PC input
count for computer work). Owner session 0dc34636 (2026-09-30): at the PC the
monitor and hands are out of view and the head is turned toward image-right.
Synthetic and recorded geometry, temp stores; no network or model."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import jev_wire as wire
from jev_evidence import QWEN_EGRESS, head_words, project
from jev_semantics import (EAR_MERGED, PITCH_DOWN, YAW_TURNED, frame_head, head_direction, head_measures, head_toward,
                           relation_facts, semantic_state)
from persisted_evidence import observation_record
from test_jev_policy_v6 import V6Case
from test_pc_evidence_v3 import BINDINGS, ELSEWHERE, ha_record, numbers, qwen_records
import test_paired_runtime as fixtures

# Supported COCO17 face and shoulder joints (0 nose, 1-2 eyes, 3-4 ears,
# 5-6 shoulders) stored for session 0dc34636, arm a, 640x360 frames.
REAL = {
    # 39.7 s reading: book raised to the chest, face toward the camera.
    'reading': [(0, (279.3, 203.8)), (1, (286.7, 189.6)), (2, (265.0, 189.6)), (3, (294.2, 191.6)), (4, (236.5, 189.6)),
                (5, (309.1, 238.4)), (6, (206.7, 272.4))],
    # 74.5 s phone: head bowed toward the phone.
    'phone': [(0, (254.8, 209.2)), (1, (260.3, 193.6)), (2, (236.8, 196.7)), (3, (266.5, 191.2)), (4, (204.8, 197.5)),
              (5, (295.4, 229.5)), (6, (173.5, 286.6))],
    # 102.9 s at the PC, face briefly toward the camera.
    'pc_frontal': [(0, (305.4, 166.9)), (1, (313.8, 148.3)), (2, (284.0, 153.0)), (3, (325.8, 150.2)), (4, (256.1, 161.3)),
                   (5, (372.3, 215.2)), (6, (219.9, 251.5))],
    # 130.6 s at the PC, head turned toward image-right: the far ear is
    # predicted 3 px from the near one.
    'pc_turned': [(0, (581.1, 183.9)), (1, (583.1, 155.7)), (2, (549.8, 159.7)), (3, (471.1, 151.6)), (4, (474.1, 158.7)),
                  (5, (583.1, 252.5)), (6, (381.3, 278.8))],
}


def pose(joints, status='supported'):
    return {'layout': 'coco17', 'instance_id': 'person-0',
            'joints': [{'joint_id': j, 'pixel': list(p) if p is not None else None, 'score': .9, 'status': status}
                       for j, p in joints]}


def synthetic(nose, ears=((-40, 0), (40, 0)), eyes=((-15, -10), (15, -10)), shoulders=100, neck=50):
    """A head at (300, 200) over shoulders 2*shoulders wide, `neck` px below
    the nose line. Offsets are relative to the head centre."""
    cx, cy = 300, 200
    joints = [(0, (cx+nose[0], cy+nose[1]))]
    joints += [(1+i, (cx+x, cy+y)) for i, (x, y) in enumerate(eyes or ())]
    joints += [(3+i, (cx+x, cy+y)) for i, (x, y) in enumerate(ears or ())]
    joints += [(5, (cx+shoulders, cy+nose[1]+neck)), (6, (cx-shoulders, cy+nose[1]+neck))]
    return pose(joints)


def posed(state):
    """The head block of the newest observation that carries a pose."""
    return [o['head'] for o in state['observations'] if 'head' in o][-1]


def frame(ident, t, joints, objects=(), session='s', person=('person', (150, 100, 450, 260))):
    labelled = [person, *objects]
    return {'id': ident, 'site_id': 'victoria', 'camera_id': 'den', 'session_id': session, 'arm': 'a', 'generation': 1,
            'source_kind': 'perception.output', 'observed_at': t, 'available_at': t+.1, 'valid_until': t+2.5,
            'evidence_roots': [ident], 'quality': {'image_width': 640, 'image_height': 360},
            'objects': [{'label': label, 'instance_id': '%s-%d' % (label, i), 'pixel_box': dict(zip('xywh', box))}
                        for i, (label, box) in enumerate(labelled)],
            'pose_instances': [pose(joints)] if joints is not None else []}


class HeadBucketTests(unittest.TestCase):
    def test_buckets_from_synthetic_geometry(self):
        cases = [
            # Frontal: nose between both ears, well above the shoulders.
            (synthetic((0, 0), neck=100), ('forward', 'level', 'both_ears')),
            # Turned toward image-right: far ear hidden, near ear 0.5 shoulder widths behind the nose.
            (synthetic((100, 0), ears=((-2, 0), (2, 0)), neck=100), ('toward_image_right', 'level', 'one_ear')),
            (synthetic((100, 0), ears=((0, 0),), neck=100), ('toward_image_right', 'level', 'one_ear')),
            (synthetic((-100, 0), ears=((0, 0),), neck=100), ('toward_image_left', 'level', 'one_ear')),
            # Bowed head: the nose sits close to the shoulder line.
            (synthetic((0, 0), neck=50), ('forward', 'down', 'both_ears')),
            # Eyes without ears: pitch is measured, yaw is not.
            (synthetic((0, 0), ears=None, neck=50), ('unknown', 'down', 'eyes_only')),
        ]
        for value, expected in cases:
            with self.subTest(expected=expected):
                head = head_direction(value)
                self.assertEqual((head['yaw'], head['pitch'], head['support']), expected)

    def test_thresholds_are_the_calibrated_round_values(self):
        self.assertEqual((EAR_MERGED, YAW_TURNED, PITCH_DOWN), (.1, .4, .4))
        # Just inside and outside the yaw threshold (shoulder width 200 px).
        self.assertEqual(head_direction(synthetic((79, 0), ears=((0, 0),)))['yaw'], 'forward')
        self.assertEqual(head_direction(synthetic((81, 0), ears=((0, 0),)))['yaw'], 'toward_image_right')
        support, yaw, pitch = head_measures(synthetic((81, 0), ears=((0, 0),), neck=50))
        self.assertEqual(support, 'one_ear')
        self.assertAlmostEqual(yaw, .405)
        self.assertAlmostEqual(pitch, .25)

    def test_missing_or_uncertain_joints_are_unknown(self):
        insufficient = {'yaw': 'unknown', 'pitch': 'unknown', 'support': 'insufficient'}
        full = synthetic((0, 0))
        for drop in (0, 5, 6):
            with self.subTest(drop=drop):
                value = deepcopy(full)
                value['joints'] = [j for j in value['joints'] if j['joint_id'] != drop]
                self.assertEqual(head_direction(value), insufficient)
        for change in (lambda j: j.update(status='uncertain'), lambda j: j.update(pixel=None),
                       lambda j: j.update(pixel=[float('nan'), 1.0]), lambda j: j.update(pixel=['1', 2])):
            value = deepcopy(full)
            change(value['joints'][0])
            with self.subTest(change=change):
                self.assertEqual(head_direction(value), insufficient)
        # Nose and shoulders but no ear or eye pair.
        self.assertEqual(head_direction(synthetic((0, 0), ears=None, eyes=((0, -10),))), insufficient)
        # Coincident shoulders give no scale.
        value = synthetic((0, 0), shoulders=0)
        self.assertEqual(head_direction(value), insufficient)
        for value in (None, {}, {'joints': None}, {'joints': ['x', None, 3]}):
            self.assertEqual(head_direction(value), insufficient)

    def test_one_frame_head_needs_exactly_one_pose_and_person(self):
        record = frame('1', 99.5, REAL['pc_turned'])
        self.assertEqual(frame_head(record)['yaw'], 'toward_image_right')
        two = deepcopy(record)
        two['pose_instances'].append(deepcopy(two['pose_instances'][0]))
        self.assertEqual(frame_head(two)['support'], 'insufficient')
        crowd = frame('1', 99.5, REAL['pc_turned'], objects=[('person', (10, 10, 100, 200))])
        self.assertEqual(frame_head(crowd)['support'], 'insufficient')
        self.assertEqual(frame_head(frame('1', 99.5, None))['support'], 'insufficient')

    def test_head_toward_needs_a_monitor_side(self):
        turned = head_direction(pose(REAL['pc_turned']))
        self.assertIsNone(head_toward(turned, None))
        self.assertEqual(head_toward(turned, 'image_right'), 'monitor_side')
        self.assertEqual(head_toward(turned, 'image_left'), 'elsewhere')
        self.assertEqual(head_toward({'yaw': 'toward_image_left', 'pitch': 'down'}, 'image_left'), 'monitor_side')
        self.assertEqual(head_toward({'yaw': 'forward', 'pitch': 'down'}, 'image_right'), 'lap_or_hands')
        self.assertEqual(head_toward({'yaw': 'unknown', 'pitch': 'down'}, 'image_right'), 'lap_or_hands')
        self.assertEqual(head_toward({'yaw': 'unknown', 'pitch': 'level'}, 'image_right'), 'unknown')
        self.assertIsNone(head_toward(turned, 'left'))
        self.assertNotIn('head_toward', frame_head(frame('1', 99.5, REAL['pc_turned'])))

    def test_recorded_session_0dc34636_geometry(self):
        expected = {'reading': ('forward', 'level', 'both_ears', 'elsewhere'),
                    'phone': ('forward', 'down', 'both_ears', 'lap_or_hands'),
                    'pc_frontal': ('forward', 'level', 'both_ears', 'elsewhere'),
                    'pc_turned': ('toward_image_right', 'level', 'one_ear', 'monitor_side')}
        for name, joints in REAL.items():
            with self.subTest(frame=name):
                head = frame_head(frame('1', 99.5, joints), 'image_right')
                self.assertEqual((head['yaw'], head['pitch'], head['support'], head['head_toward']), expected[name])

    def test_head_words_allowlist(self):
        head = {'yaw': 'forward', 'pitch': 'level', 'support': 'both_ears'}
        self.assertTrue(head_words(head))
        self.assertFalse(head_words(head, 'image_right'))
        self.assertTrue(head_words({**head, 'head_toward': 'monitor_side'}, 'image_right'))
        self.assertFalse(head_words({**head, 'head_toward': 'monitor_side'}))
        for change in ({'yaw': 'left'}, {'pitch': 0.3}, {'support': None}, {'extra': 'x'}):
            self.assertFalse(head_words({**head, **change}))


class CorroborationThreeTests(unittest.TestCase):
    """v5 semantic block: corroboration-3 outcomes at a PC input under 15 s."""

    def computer(self, joints, qwen_facts=ELSEWHERE, recency='under_15s', monitor_side='image_right'):
        records = project([frame('7', 99.5, joints), ha_record(recency)], ha_bindings=BINDINGS)
        state = semantic_state(records + project(qwen_records(qwen_facts)), 100, monitor_side=monitor_side)
        return state, state['corroboration']['activities']['computer_work']

    def test_head_toward_the_monitor_side_counts_pc_input(self):
        state, work = self.computer(REAL['pc_turned'])
        self.assertEqual(state['corroboration']['rule'], 'corroboration-3')
        self.assertEqual(posed(state), {'yaw': 'toward_image_right', 'pitch': 'level',
                                                              'support': 'one_ear', 'head_toward': 'monitor_side'})
        self.assertEqual((work['pc_input'], work['independent'], work['conflicts']),
                         ('pc_input_under_15s_with_camera_cue', ['computer_input_recency'], []))
        # Without the site mapping the raw buckets stay and PC input does not count.
        state, work = self.computer(REAL['pc_turned'], monitor_side=None)
        self.assertNotIn('head_toward', posed(state))
        self.assertEqual((work['pc_input'], work['independent']), ('pc_input_recent_but_not_observed_at_pc', []))
        # Older input never counts, head or not.
        self.assertEqual(self.computer(REAL['pc_turned'], recency='15_to_60s')[1]['pc_input'],
                         'pc_input_recent_but_not_observed_at_pc')
        # The other side of the image is not the monitor.
        self.assertEqual(self.computer(REAL['pc_turned'], monitor_side='image_left')[1]['pc_input'],
                         'pc_input_recent_but_not_observed_at_pc')

    def test_held_book_or_phone_stays_a_conflict(self):
        for held in ('book', 'phone'):
            for joints in (REAL['pc_turned'], REAL['phone']):
                with self.subTest(held=held, joints=joints is REAL['phone']):
                    _, work = self.computer(joints, {**ELSEWHERE, 'held_object': held})
                    self.assertEqual((work['pc_input'], work['conflicts'], work['independent']),
                                     ('pc_input_recent_but_conflicts_with_held_object', ['qwen_held_object_' + held], []))

    def test_lap_or_hands_is_not_a_pc_cue(self):
        state, work = self.computer(REAL['phone'])
        self.assertEqual(posed(state)['head_toward'], 'lap_or_hands')
        self.assertEqual((work['pc_input'], work['independent']), ('pc_input_recent_but_not_observed_at_pc', []))

    def test_an_expired_frame_is_no_cue(self):
        records = project([frame('7', 96.0, REAL['pc_turned']), ha_record('under_15s')], ha_bindings=BINDINGS)
        work = semantic_state(records, 100, monitor_side='image_right')['corroboration']['activities']['computer_work']
        self.assertEqual(work['pc_input'], 'pc_input_recent_but_not_observed_at_pc')

    def test_head_blocks_hold_no_numbers(self):
        state, _ = self.computer(REAL['pc_turned'])
        self.assertEqual(numbers([o.get('head') for o in state['observations']]), [])
        self.assertEqual(numbers(state['corroboration']), [])
        with self.assertRaises(ValueError):
            semantic_state([], 100, monitor_side='right')


def turned_session(session='s', start=40.0, count=60):
    """A reader (book, head level) then a PC user whose head turns toward image-right."""
    records = [frame('r%d' % i, start+i, REAL['reading'], session=session) for i in range(20)]
    records += [frame('c%d' % i, start+20+i, REAL['pc_turned'], session=session) for i in range(count-20)]
    return records


class WireHeadTests(unittest.TestCase):
    def build(self, monitor_side='image_right', recency='under_15s', level=0, records=None):
        raw = (records or turned_session()) + [ha_record(recency)]
        projected = project(raw, ha_bindings=BINDINGS)
        timeline = [wire.frame_facts(r) for r in projected if r.get('source_kind') == 'perception.output']
        local = wire.sidecar(projected, project(qwen_records(ELSEWHERE)), None, timeline, 100.0, 60)
        return wire.build_state(local, cutoff=100.0, budget_level=level, monitor_side=monitor_side)

    def test_now_head_storyline_and_support(self):
        state = self.build()
        wire.validate_state(state)
        self.assertEqual(state['now']['head'], {'yaw': 'toward_image_right', 'pitch': 'level', 'support': 'one_ear'})
        self.assertEqual(state['now']['head_toward'], 'monitor_side')
        self.assertEqual(state['support']['computer_work']['pc_input_role'], 'counts_camera_cue_at_pc')
        self.assertIn('pc_input_counts', state['support']['computer_work']['independent'])
        # One person_no_hand_cue segment: 20 reading frames, then 40 at the PC.
        self.assertEqual([s['head_toward'] for s in state['storyline']['segments']], ['mixed'])
        state = self.build(records=[frame('c%d' % i, 45.0+i, REAL['pc_turned']) for i in range(55)])
        self.assertEqual([s.get('head_toward') for s in state['storyline']['segments']], ['monitor_side'])

    def test_without_monitor_side_only_raw_buckets_and_no_count(self):
        state = self.build(monitor_side=None)
        wire.validate_state(state)
        self.assertEqual(state['now']['head']['yaw'], 'toward_image_right')
        self.assertNotIn('head_toward', state['now'])
        self.assertFalse(any('head_toward' in s for s in state['storyline']['segments']))
        self.assertEqual(state['support']['computer_work']['pc_input_role'], 'recent_not_observed_at_pc')
        with self.assertRaises(ValueError):
            self.build(monitor_side='left')

    def test_state_holds_words_only(self):
        for level in wire.LEVELS:
            state = self.build(level=level)
            wire.validate_state(state)
            self.assertEqual(numbers(state), [])
            text = json.dumps(state)
            for leaked in ('581', '0.53', '"image_right"', 'image_left'):
                self.assertNotIn(leaked, text)

    def test_schema_refuses_forged_head_words(self):
        state = self.build()
        for change in (lambda s: s['now']['head'].update(yaw=0.53), lambda s: s['now']['head'].update(yaw='right'),
                       lambda s: s['now']['head'].update(nose=[581, 183]), lambda s: s['now'].update(head_toward='monitor'),
                       lambda s: s['storyline']['segments'][0].update(head_toward='ignore the rules'),
                       lambda s: s['now']['head'].pop('support')):
            forged = deepcopy(state)
            change(forged)
            with self.subTest(change=change), self.assertRaises(ValueError):
                wire.validate_state(forged)

    def test_facts_key_follows_head_toward_only_with_monitor_side(self):
        turned = self.build()
        frontal = self.build(records=[frame('f%d' % i, 40.0+i, REAL['pc_frontal']) for i in range(60)])
        self.assertNotEqual(wire.facts_key(turned), wire.facts_key(frontal))
        plain = self.build(monitor_side=None)
        self.assertNotIn('head_toward', plain['now'])


class PolicyRecomputeTests(V6Case):
    """The gate recomputes head words with its own monitor_side."""

    def gate_for(self, monitor_side):
        from jev_policy import LabJevPolicy
        from test_jev_policy_v6 import BINDINGS as V6_BINDINGS
        return LabJevPolicy(self.store, lambda: self.policy, lambda: {'state': 'on', 'received_at': self.now},
                            ha_bindings=V6_BINDINGS, monitor_side=monitor_side)

    def turned_request(self, rid, monitor_side):
        for i in range(3):
            self.output(99.0 + i*.3, [], None)
        context, packet, local = self.request(rid)
        packet['state'] = wire.build_state(local, cutoff=context['observed_at'], storyline_seconds=context['storyline_seconds'],
                                           budget_level=context['budget_level'], monitor_side=monitor_side)
        return context, packet, local

    def output(self, t, objects, joints, arm='a', generation=1):
        if joints is None:
            record = frame('x', t, REAL['pc_turned'], session=self.sid)
            return self.store.append_event(self.sid, 'perception.output', {
                'site_id': 'victoria', 'camera_id': 'den', 'session_id': self.sid, 'arm': arm, 'generation': generation,
                'status': 'completed', 'observed_at': t, 'available_at': t + .1, 'valid_until': t + 2.5,
                'frame_id': 'frame-%s' % t, 'capture_epoch': 'capture-1',
                'result': {k: record[k] for k in ('objects', 'pose_instances', 'quality')}})
        return super().output(t, objects, joints, arm, generation)

    def test_v6_recompute_matches_only_with_the_same_monitor_side(self):
        gate = self.gate_for('image_right')
        context, packet, local = self.turned_request('h1', 'image_right')
        self.assertEqual(packet['state']['now']['head_toward'], 'monitor_side')
        self.assertTrue(gate(context, packet, 'send', local=local)['allowed'])
        # A packet built without the mapping, or with the other side, is not this gate's recompute.
        for side in (None, 'image_left'):
            context, packet, local = self.turned_request('h-%s' % side, side)
            with self.subTest(side=side), self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
                gate(context, packet, 'send', local=local)
        # A forged head word is refused by the schema walk before the recompute.
        context, packet, local = self.turned_request('h-forged', 'image_right')
        packet['state']['now']['head']['pitch'] = 'toward_screen'
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            gate(context, packet, 'send', local=local)

    def test_gate_refuses_an_unregistered_monitor_side(self):
        with self.assertRaisesRegex(ValueError, 'Registered semantic source bindings required'):
            self.gate_for('left')


class V5PolicyTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_v5_gate_recomputes_head_words(self):
        from jev_policy import LabJevPolicy
        from reasoning import build_jev_request
        cutoff, sid = 100.0, self.session['id']
        self.store.clock = lambda: cutoff+.5
        policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'h', 'generation': 1,
                  'expires_at': cutoff+100, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13',
                  'max_calls_per_minute': 20, 'device_context': 'den-devices-1', 'structured_sources': [QWEN_EGRESS]}
        self.store.db.execute('CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)')
        records = project([frame(str(10+i), 97.5+i, REAL['pc_turned'], session=sid) for i in range(3)]
                          + [ha_record('under_15s', session=sid)], ha_bindings=BINDINGS)
        packet, _ = build_jev_request(records, model='jev-1.13', site_id='victoria', session_id=sid, arm='a', generation=1,
                                      cutoff=cutoff, retain_history=True, semantic=True, monitor_side='image_right',
                                      shared_context=project(qwen_records(ELSEWHERE, cutoff, sid)))
        semantic = packet['state']['semantic']
        self.assertEqual(semantic['corroboration']['activities']['computer_work']['pc_input'], 'pc_input_under_15s_with_camera_cue')
        context = {'site_id': 'victoria', 'session_id': sid, 'arm': 'a', 'generation': 1}

        def send(gate, request, value):
            self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)', (request, sid, 'dispatching'))
            return gate({**context, 'request_id': request}, value, 'send')
        gate = LabJevPolicy(self.store, lambda: policy, lambda: {'state': 'on', 'received_at': cutoff+.5},
                            ha_bindings=BINDINGS, monitor_side='image_right')
        self.assertTrue(send(gate, 'ok', packet)['allowed'])
        plain = LabJevPolicy(self.store, lambda: policy, lambda: {'state': 'on', 'received_at': cutoff+.5}, ha_bindings=BINDINGS)
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            send(plain, 'no-side', packet)
        for index, change in enumerate((lambda s: posed(s).update(head_toward='elsewhere'),
                                        lambda s: posed(s).update(yaw=0.53))):
            forged = deepcopy(packet)
            change(forged['state']['semantic'])
            with self.subTest(change=index), self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
                send(gate, 'forged%d' % index, forged)


class ProvisioningTests(unittest.TestCase):
    def test_monitor_side_is_an_exact_optional_jev_config_key(self):
        from managed_jev import reasoning_factory
        config = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0'}
        for value, expected in ((None, None), ('image_right', 'image_right'), ('image_left', 'image_left')):
            item = dict(config, **({} if value is None else {'monitor_side': value}))
            with patch('managed_jev.private_json', side_effect=lambda p, item=item: dict(item)):
                self.assertEqual(reasoning_factory('config').monitor_side, expected)
        for value in ('right', 'IMAGE_RIGHT', 1, ['image_right']):
            item = dict(config, monitor_side=value)
            with patch('managed_jev.private_json', side_effect=lambda p, item=item: dict(item)), self.assertRaises(ValueError):
                reasoning_factory('config')


class RealPhoneRelationTests(unittest.TestCase):
    """Session 0dc34636 at 74.5 s, as stored: the worker's objects carry no
    pixel_box (boxes ride in result.boxes, paired by instance_id), the pose
    instance_id is the person's, and the left wrist sits 31 px below a
    34x31 px phone box. A probe that reads result.objects[].pixel_box directly
    sees no box and no relation; the projection pairs it and the relation
    fires (within_phone_length)."""

    PAYLOAD = {
        'site_id': 'victoria', 'camera_id': 'den', 'arm': 'a', 'generation': 1, 'status': 'completed',
        'observed_at': 1790737065.2864685, 'available_at': 1790737065.9, 'valid_until': 1790737067.8,
        'frame_id': 'ac3b94aa-0-44447', 'capture_epoch': 'ac3b94aa',
        'arm_snapshot': {'frame': {'id': 'ac3b94aa-0-44447', 'width': 640, 'height': 360}, 'validation': []},
        'result': {'schema_version': 1, 'coordinate_space': 'source_pixels', 'frame_id': 'ac3b94aa-0-44447',
                   'quality': {'coordinate_space': 'source_pixels', 'pose_score_semantics': 'SimCC_response_not_probability'},
                   'objects': [{'confidence': 0.9489, 'instance_id': '9ac2d3e6', 'label': 'person'},
                               {'confidence': 0.6393, 'instance_id': 'ac3b94aa-0-44447:1', 'label': 'cell phone'}],
                   'boxes': [{'confidence': 0.9489, 'instance_id': '9ac2d3e6', 'label': 'person',
                              'x': 148.0756, 'y': 137.4573, 'w': 240.0301, 'h': 218.4886},
                             {'confidence': 0.6393, 'instance_id': 'ac3b94aa-0-44447:1', 'label': 'cell phone',
                              'x': 317.9316, 'y': 237.9186, 'w': 33.5446, 'h': 30.7816}],
                   'pose_instances': [{'instance_id': '9ac2d3e6', 'layout': 'coco17',
                                       'score_semantics': 'SimCC_response_not_probability', 'joints': [
                       {'joint_id': 0, 'name': 'nose', 'pixel': [254.8077, 209.1969], 'score': 0.914, 'status': 'supported'},
                       {'joint_id': 1, 'name': 'left_eye', 'pixel': [260.2772, 193.5699], 'score': 0.9735, 'status': 'supported'},
                       {'joint_id': 2, 'name': 'right_eye', 'pixel': [236.8367, 196.6953], 'score': 0.8982, 'status': 'supported'},
                       {'joint_id': 9, 'name': 'left_wrist', 'pixel': [350.9135, 299.8332], 'score': 0.7921, 'status': 'supported'},
                       {'joint_id': 10, 'name': 'right_wrist', 'pixel': [279.0295, 322.4923], 'score': 0.8211,
                        'status': 'supported'}]}]}}

    def test_stored_held_phone_geometry_fires_through_the_projection(self):
        raw = self.PAYLOAD['result']
        # The raw worker objects have no box: a direct probe finds nothing.
        self.assertEqual(relation_facts(raw['objects'], raw['pose_instances']), ([], set()))
        session = {'id': 's', 'site_id': 'victoria', 'camera_id': 'den'}
        projected = project([observation_record('74', 'perception.output', self.PAYLOAD, session)])[0]
        facts, related = relation_facts(projected['objects'], projected['pose_instances'])
        self.assertEqual([(f['fact'], f['basis']) for f in facts],
                         [('left_wrist_near_cell_phone_in_image', 'within_phone_length')])
        self.assertGreater(facts[0]['normalized_distance'], .08)
        self.assertEqual(wire.frame_cues(projected)[1], 'phone_near_hand')


if __name__ == '__main__':
    unittest.main()
