"""Desk-seat position (rule desk-seat-1) and the den-facts-7 wire: the region
config, the per-frame word, now.position, the desk_seat camera cue for
computer work, the registered schemas, facts keys and the egress policy.
Synthetic records and temp stores only; no network or model."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import jev_wire as wire
from jev_evidence import QWEN_EGRESS, project
from jev_policy import LabJevPolicy
from jev_semantics import POSITIONS, desk_seat_config, frame_position, pc_input
from test_jev_policy_v6 import BINDINGS as POLICY_BINDINGS, V6Case
from test_jev_wire import BINDINGS, BOOK, KEYBOARD, PHONE, WRIST_ON, WRIST_PHONE, frame, ha, qwen
from test_qwen_wiring import FACTS

# The den region (640x360 measurements, as fractions): right edge, within 3%
# of the width, at least 35% of the width wide, top at least 8% down.
REGION = {'edge': 'image_right', 'edge_margin_frac': 0.03, 'min_width_frac': 0.35, 'min_top_frac': 0.08}
# 1280x720 fixture frames (test_jev_wire.frame): the same shapes scaled x2 from
# the den's recorded boxes.
SEATED = (660, 150, 620, 570)       # 8824f874: x 330-640, top 75, 310 wide
STANDING = (640, 2, 640, 700)       # 0dc34636 19.6 s: at the edge, head at the frame top
PASSING = (1080, 2, 200, 650)       # 0dc34636 236 s: a narrow walking box at the edge
ARMCHAIR = (350, 270, 440, 444)     # 0dc34636 reading: box ends at x ~397 of 640
COMPUTER = {**FACTS, 'held_object': 'none', 'book_state': 'not_visible', 'gaze_target': 'screen',
            'typing_visible': 'no', 'activity_hypothesis': 'computer_work', 'visible_basis': ['person_visible']}


def seat(ident, t, box=SEATED, objects=(), joints=None, **kw):
    record = frame(ident, t, objects, joints, **kw)
    record['objects'][0]['pixel_box'] = dict(zip('xywh', box))
    return record


def local(records, *, facts=COMPUTER, recency='under_15s', cutoff=100.0):
    raw = list(records) + [ha('h', cutoff - 1, recency=recency)]
    projected = project(raw, ha_bindings=BINDINGS)
    shared = project(qwen(facts, cutoff), ha_bindings=BINDINGS) if facts else []
    return wire.sidecar(projected, shared, None, [wire.frame_facts(r) for r in projected
                                                  if r['source_kind'] == 'perception.output'], cutoff)


def states(records, **kw):
    side = local(records, **kw)
    return (wire.build_state(side, cutoff=100.0),
            wire.build_state(side, cutoff=100.0, wire_version=wire.WIRE_VERSION_7, desk_seat=REGION))


class RegionTests(unittest.TestCase):
    def test_region_config_is_exact_and_bounded(self):
        self.assertEqual(desk_seat_config(REGION), REGION)
        self.assertEqual(desk_seat_config(dict(REGION, min_top_frac=0)), dict(REGION, min_top_frac=0.0))
        for bad in (None, [], {}, dict(REGION, edge='right'), dict(REGION, extra=1), {k: v for k, v in REGION.items() if k != 'edge'},
                    dict(REGION, edge_margin_frac=.3), dict(REGION, min_width_frac=0), dict(REGION, min_top_frac=True),
                    dict(REGION, min_top_frac='0.1'), dict(REGION, min_width_frac=float('nan')), dict(REGION, min_top_frac=1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                desk_seat_config(bad)

    def test_the_word_for_each_recorded_shape(self):
        for box, word in ((SEATED, 'desk_seat'), (STANDING, 'elsewhere_in_room'), (PASSING, 'elsewhere_in_room'),
                          (ARMCHAIR, 'elsewhere_in_room')):
            with self.subTest(box=box):
                self.assertEqual(frame_position(project([seat('s', 99.0, box)])[0], REGION), word)
        mirrored = dict(REGION, edge='image_left')
        self.assertEqual(frame_position(project([seat('s', 99.0, (0, 150, 620, 570))])[0], mirrored), 'desk_seat')
        self.assertEqual(frame_position(project([seat('s', 99.0)])[0], mirrored), 'elsewhere_in_room')

    def test_only_the_rgb_box_and_frame_size_matter(self):
        record = project([seat('s', 99.0)])[0]
        spatial = dict(record, tracks=[{'track_ref': 'a' * 24, 'pos': [0.1, 0.2, 0.6], 'units': 'metres',
                                        'uncertainty_m': .05, 'method': 'stereo_surface'}],
                       quality=dict(record['quality'], metric_depth='validated_surface', calibration='changed'))
        self.assertEqual(frame_position(spatial, REGION), frame_position(record, REGION))

    def test_no_single_person_box_is_unknown(self):
        empty = project([frame('e', 99.0, person=False)])[0]
        two = project([seat('t', 99.0, objects=[('person', (10, 10, 100, 300))])])[0]
        unsized = project([seat('u', 99.0)])[0]
        unsized['quality'] = {}
        for record in (empty, two, unsized):
            self.assertEqual(frame_position(record, REGION), 'unknown')
        self.assertEqual(set(POSITIONS), {'desk_seat', 'elsewhere_in_room', 'unknown'})

    def test_desk_seat_is_a_pc_cue_but_never_through_a_held_object(self):
        devices = {'computer_input_recency': 'under_15s'}
        self.assertEqual(pc_input(devices, set(), [], {}), ('pc_input_recent_but_not_observed_at_pc', [], False))
        self.assertEqual(pc_input(devices, set(), [], {}, position='desk_seat'), ('pc_input_under_15s_with_camera_cue', [], True))
        self.assertFalse(pc_input(devices, set(), [], {}, position='elsewhere_in_room')[2])
        held = pc_input(devices, set(), [], {'held_object': 'phone'}, position='desk_seat')
        self.assertEqual(held, ('pc_input_recent_but_conflicts_with_held_object', ['qwen_held_object_phone'], False))


class WireTests(unittest.TestCase):
    def test_desk_seat_turns_the_camera_line_into_detector_and_qwen(self):
        records = [seat('s%d' % i, 97.0 + i) for i in range(3)]
        six, seven = states(records)
        # den-facts-6 is unchanged: no position, a Qwen-only camera line.
        self.assertNotIn('position', six['now'])
        self.assertEqual((six['schema'], six['guide']), (wire.WIRE_VERSION, wire.GUIDE))
        self.assertEqual(six['support']['computer_work']['camera_line'], 'qwen_only')
        self.assertEqual(six['support']['computer_work']['pc_input_role'], 'counts_camera_cue_at_pc')  # Qwen gaze screen
        wire.validate_state(six)
        self.assertEqual((seven['schema'], seven['guide'], seven['now']['position']),
                         (wire.WIRE_VERSION_7, wire.GUIDE_7, 'desk_seat'))
        work = seven['support']['computer_work']
        self.assertEqual((work['camera_line'], work['pc_input_role'], work['independent']),
                         ('detector_and_qwen', 'counts_camera_cue_at_pc', ['pc_input_counts']))
        wire.validate_state(seven)
        # Only now.position, the guide, the schema id and computer work differ.
        self.assertEqual({k: v for k, v in seven['now'].items() if k != 'position'}, six['now'])
        self.assertEqual({k: v for k, v in seven['support'].items() if k != 'computer_work'},
                         {k: v for k, v in six['support'].items() if k != 'computer_work'})

    def test_desk_seat_alone_counts_pc_input_without_any_qwen_cue(self):
        elsewhere = {**COMPUTER, 'gaze_target': 'elsewhere', 'activity_hypothesis': 'resting'}
        six, seven = states([seat('s%d' % i, 97.0 + i) for i in range(3)], facts=elsewhere)
        self.assertEqual((six['support']['computer_work']['camera_line'], six['support']['computer_work']['pc_input_role']),
                         ('none', 'recent_not_observed_at_pc'))
        self.assertEqual((seven['support']['computer_work']['camera_line'], seven['support']['computer_work']['pc_input_role']),
                         ('detector_only', 'counts_camera_cue_at_pc'))

    def test_armchair_reading_and_phone_never_read_desk_seat(self):
        for objects, joints, cue in (([BOOK], [WRIST_ON], 'book_near_hand'), ([PHONE], [WRIST_PHONE], 'phone_near_hand')):
            _, seven = states([seat('a%d' % i, 97.0 + i, ARMCHAIR, objects, joints) for i in range(3)], facts=None)
            self.assertEqual((seven['now']['position'], seven['now']['cue_state']), ('elsewhere_in_room', cue))
            work = seven['support']['computer_work']
            self.assertEqual((work['camera_line'], work['pc_input_role']), ('none', 'recent_not_observed_at_pc'))

    def test_a_book_or_phone_near_the_hand_at_the_desk_blocks_the_seat_cue(self):
        scaled = (('book', (900, 470, 120, 60)), (900, 480))
        records = [seat('b%d' % i, 97.0 + i, objects=[scaled[0]], joints=[(9, scaled[1])]) for i in range(3)]
        _, seven = states(records, facts=None)
        self.assertEqual((seven['now']['position'], seven['now']['cue_state']), ('desk_seat', 'book_near_hand'))
        work = seven['support']['computer_work']
        self.assertEqual((work['camera_line'], work['pc_input_role']), ('none', 'recent_not_observed_at_pc'))
        self.assertIn('detector_book_near_hand', work['conflicts'])

    def test_keyboard_at_the_desk_is_still_one_detector_line(self):
        records = [seat('k%d' % i, 97.0 + i, objects=[('keyboard', (900, 470, 160, 40))], joints=[(9, (950, 480))])
                   for i in range(3)]
        _, seven = states(records)
        self.assertEqual(seven['support']['computer_work']['camera_line'], 'detector_and_qwen')

    def test_expired_or_absent_camera_never_counts_the_seat(self):
        _, seven = states([seat('o%d' % i, 90.0 + i) for i in range(3)])
        self.assertEqual((seven['now']['camera_evidence'], seven['now']['position']), ('expired_historical', 'desk_seat'))
        self.assertEqual(seven['support']['computer_work']['camera_line'], 'qwen_only')
        _, empty = states([], facts=None)
        self.assertEqual(empty['now']['position'], 'unknown')
        wire.validate_state(empty)

    def test_every_budget_level_keeps_the_position_word(self):
        side = local([seat('s%d' % i, 97.0 + i) for i in range(3)])
        for level in wire.LEVELS:
            state = wire.build_state(side, cutoff=100.0, budget_level=level, wire_version=wire.WIRE_VERSION_7, desk_seat=REGION)
            wire.validate_state(state)
            self.assertEqual(state['now']['position'], 'desk_seat')

    def test_wire_parameters_are_registered(self):
        side = local([seat('s', 99.0)])
        with self.assertRaisesRegex(ValueError, 'desk_seat'):
            wire.build_state(side, cutoff=100.0, wire_version=wire.WIRE_VERSION_7)
        with self.assertRaises(ValueError):
            wire.build_state(side, cutoff=100.0, wire_version=wire.WIRE_VERSION_7, desk_seat=dict(REGION, edge='up'))
        with self.assertRaises(ValueError):
            wire.build_state(side, cutoff=100.0, wire_version='den-facts-9', desk_seat=REGION)
        # den-facts-6 ignores a configured region: rollback is only the wire id.
        self.assertEqual(wire.build_state(side, cutoff=100.0, desk_seat=REGION), wire.build_state(side, cutoff=100.0))

    def test_each_schema_accepts_only_its_own_shape(self):
        six, seven = states([seat('s%d' % i, 97.0 + i) for i in range(3)])
        for forged in (dict(six, now=dict(six['now'], position='desk_seat')),          # position on den-facts-6
                       dict(seven, now={k: v for k, v in seven['now'].items() if k != 'position'}),
                       dict(seven, now=dict(seven['now'], position='armchair')),
                       dict(seven, guide=wire.GUIDE), dict(six, guide=wire.GUIDE_7),
                       dict(seven, schema='den-facts-8'), dict(seven, schema='den-facts-9'),
                       dict(seven, now=dict(seven['now'], position=1))):
            with self.subTest(forged=forged['schema']), self.assertRaises(ValueError):
                wire.validate_state(forged)

    def test_wire_schemas_are_registered_and_digest_stable(self):
        # A changed schema needs a new wire id (and its own household grant).
        self.assertEqual(wire.WIRE_VERSIONS, ('den-facts-6', 'den-facts-7', 'den-facts-8'))
        self.assertEqual(set(wire.SCHEMAS), set(wire.WIRE_VERSIONS))
        self.assertEqual({w: wire.schema_digest(w) for w in wire.WIRE_VERSIONS}, SCHEMA_DIGESTS)
        self.assertEqual(wire.PROJECTION_VERSIONS, {'den-facts-6': 'structured-facts-9', 'den-facts-7': 'structured-facts-10',
                                                    'den-facts-8': 'structured-facts-11'})

    def test_facts_keys_carry_the_position_only_on_den_facts_7(self):
        six, seven = states([seat('s%d' % i, 97.0 + i) for i in range(3)])
        for version in wire.FACTS_KEY_VERSIONS:
            moved = deepcopy(seven)
            moved['now']['position'] = 'elsewhere_in_room'
            self.assertNotEqual(wire.facts_key(moved, version), wire.facts_key(seven, version), version)
            unknown = deepcopy(seven)
            unknown['now']['position'] = 'unknown'
            self.assertNotEqual(wire.facts_key(unknown, version), wire.facts_key(moved, version), version)
        # den-facts-6 states carry no position, so their keys are as before.
        self.assertNotIn('position', six['now'])

    def test_prepare_binds_the_wire_id(self):
        records = project([seat('s%d' % i, 97.0 + i) for i in range(3)] + [ha('h', 99.0, recency='under_15s')],
                          ha_bindings=BINDINGS)
        _, packet, binding = wire.prepare(records, [], None, site_id='victoria', session_id='s', arm='a', generation=1,
                                          cutoff=100.0, window_seconds=10, model='jev-1.13.0',
                                          wire_version=wire.WIRE_VERSION_7, desk_seat=REGION)
        self.assertEqual((binding['wire_version'], packet['state']['schema'], packet['state']['now']['position']),
                         (wire.WIRE_VERSION_7, wire.WIRE_VERSION_7, 'desk_seat'))


class WordingTests(unittest.TestCase):
    """den-activity-10-facts ('v8'): one computer-work wording change,
    registered, digest-pinned and only on den-facts-7."""

    def test_only_the_computer_work_wording_differs(self):
        v6, v8 = wire.QUESTIONS, wire.QUESTIONS_10
        self.assertEqual(wire.QUESTION_SETS[wire.QUESTION_VERSION_10], v8)
        self.assertEqual(wire.QUESTION_CHOICES['v8'], wire.QUESTION_VERSION_10)
        self.assertEqual(set(v8), set(v6))
        self.assertEqual({k: v for k, v in v8.items() if k not in ('activity', 'computer_work_supported')},
                         {k: v for k, v in v6.items() if k not in ('activity', 'computer_work_supported')})
        self.assertEqual(v8['activity']['instructions'], v6['activity']['instructions'])
        self.assertEqual({k: v for k, v in v8['activity']['criteria'].items() if k != 'computer_work'},
                         {k: v for k, v in v6['activity']['criteria'].items() if k != 'computer_work'})
        self.assertEqual(wire.question_digest(v8), 'cbff6beb5b21547264bd0b89f0c9debdff42b05f0657fae21b4d851f31f51652')
        self.assertEqual(wire.question_digest(v6), 'fc413b660c1f43eb9dc86caa977be0150a15a2f75a4627a1480b6ca6e9ca272e')

    def test_every_backtick_path_exists_in_a_den_facts_7_state(self):
        import re
        _, seven = states([seat('s%d' % i, 97.0 + i) for i in range(3)])
        texts = [q['instructions'] for q in wire.QUESTIONS_10.values()] + list(wire.QUESTIONS_10['activity']['criteria'].values())
        paths = {p for text in texts for p in re.findall(r'`([^`]+)`', text)}
        self.assertIn('now.position', paths)
        for path in paths:
            value = seven
            for part in path.split('.'):
                self.assertIn(part, value, path)
                value = value[part]

    def test_v8_questions_need_den_facts_7(self):
        self.assertTrue(wire.question_wire_ok(wire.QUESTION_VERSION_10, wire.WIRE_VERSION_7))
        self.assertFalse(wire.question_wire_ok(wire.QUESTION_VERSION_10, wire.WIRE_VERSION))
        self.assertTrue(wire.question_wire_ok(wire.QUESTION_VERSION, wire.WIRE_VERSION_7))
        records = project([seat('s', 99.0)], ha_bindings=BINDINGS)
        with self.assertRaisesRegex(ValueError, 'registered question set'):
            wire.prepare(records, [], None, site_id='victoria', session_id='s', arm='a', generation=1, cutoff=100.0,
                         window_seconds=10, model='jev-1.13.0', question_version=wire.QUESTION_VERSION_10)
        _, packet, _ = wire.prepare(records, [], None, site_id='victoria', session_id='s', arm='a', generation=1,
                                    cutoff=100.0, window_seconds=10, model='jev-1.13.0',
                                    question_version=wire.QUESTION_VERSION_10, wire_version=wire.WIRE_VERSION_7,
                                    desk_seat=REGION)
        self.assertEqual(packet['questions'], wire.QUESTIONS_10)


class PolicyTests(V6Case):
    """The gate recomputes now.position with its own desk_seat region and
    needs the den-facts-7 grant."""

    def setUp(self):
        super().setUp()
        self.policy['structured_sources'] = [QWEN_EGRESS, wire.WIRE_VERSION, wire.WIRE_VERSION_7]
        self.gate = self.gate7()

    def gate7(self, region=REGION):
        return LabJevPolicy(self.store, lambda: self.policy, lambda: {'state': 'on', 'received_at': self.now},
                            ha_bindings=POLICY_BINDINGS, desk_seat=region)

    def request7(self, rid='r7'):
        records = project(self.records(), ha_bindings=POLICY_BINDINGS)
        scene = project(qwen(cutoff=100.0, session=self.sid))
        side, packet, binding = wire.prepare(records, scene, None, site_id='victoria', session_id=self.sid, arm='a',
                                             generation=1, cutoff=100.0, window_seconds=60, model='jev-1.13.0',
                                             ring=wire.FrameRing(), wire_version=wire.WIRE_VERSION_7, desk_seat=REGION)
        context = {'request_id': rid, 'site_id': 'victoria', 'session_id': self.sid, 'arm': 'a', 'generation': 1,
                   **{k: v for k, v in binding.items() if k not in ('packet_bytes', 'estimated_tokens')}}
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', (rid, self.sid, 'dispatching'))
        return context, packet, side

    def test_den_facts_7_admits_under_its_own_grant(self):
        context, packet, side = self.request7()
        self.assertEqual(packet['state']['now']['position'], 'elsewhere_in_room')
        self.assertTrue(self.gate(context, packet, 'send', local=side)['allowed'])
        row = self.store.db.execute('SELECT wire_version FROM lab_jev_egress_facts').fetchone()
        self.assertEqual(row[0], wire.WIRE_VERSION_7)
        self.assertTrue(self.gate(context, packet, 'publish', local=side)['allowed'])

    def test_den_facts_6_grant_does_not_cover_den_facts_7(self):
        self.policy['structured_sources'] = [QWEN_EGRESS, wire.WIRE_VERSION]
        self.refused(*self.request7(), message='Packet wire not permitted')

    def test_a_gate_without_the_region_or_a_forged_word_refuses(self):
        context, packet, side = self.request7()
        self.gate = self.gate7(None)
        self.refused(context, packet, side)
        self.gate = self.gate7()
        forged = deepcopy(packet)
        forged['state']['now']['position'] = 'desk_seat'
        self.refused(context, forged, side)
        mislabelled = dict(context, wire_version=wire.WIRE_VERSION)
        self.refused(mislabelled, packet, side)

    def test_v8_questions_on_den_facts_6_are_refused(self):
        context, packet, side = self.request()
        forged = deepcopy(packet)
        forged['questions'] = wire.QUESTIONS_10
        self.refused(dict(context, question_version=wire.QUESTION_VERSION_10), forged, side)

    def test_the_gate_checks_its_region(self):
        with self.assertRaisesRegex(ValueError, 'Registered semantic source bindings required'):
            self.gate7(dict(REGION, edge='up'))


class ConfigTests(unittest.TestCase):
    CONFIG = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0',
              'ha_bindings': {'derived.computer_input_recency': 'computer_input_recency'}}

    def factory(self, **changes):
        from managed_jev import reasoning_factory
        with patch('managed_jev.private_json', side_effect=lambda p: dict(self.CONFIG, **changes)):
            return reasoning_factory('config')

    def test_den_facts_7_needs_a_valid_region_and_den_facts_6_keeps_it_inert(self):
        self.assertEqual(self.factory(wire=wire.WIRE_VERSION_7, desk_seat=REGION).desk_seat, REGION)
        self.assertEqual(self.factory(wire=wire.WIRE_VERSION, desk_seat=REGION).wire, wire.WIRE_VERSION)
        self.assertIsNone(self.factory(wire=wire.WIRE_VERSION).desk_seat)
        with self.assertRaisesRegex(ValueError, 'Exact Lab JEV provisioning required'):
            self.factory(wire=wire.WIRE_VERSION_7)
        with self.assertRaisesRegex(ValueError, 'Registered semantic source bindings required'):
            self.factory(wire=wire.WIRE_VERSION_7, desk_seat=dict(REGION, min_width_frac=2))
        with self.assertRaises(ValueError):
            self.factory(wire='den-facts-9', desk_seat=REGION)
        self.assertEqual(self.factory(wire=wire.WIRE_VERSION_7, desk_seat=REGION, questions='v8').questions, 'v8')
        with self.assertRaisesRegex(ValueError, 'Exact Lab JEV provisioning required'):
            self.factory(wire=wire.WIRE_VERSION, desk_seat=REGION, questions='v8')

    def test_provider_needs_the_region_for_den_facts_7(self):
        from jev_provider import JevProvider
        kwargs = dict(model_selector='jev-1.13.0', response_version='jev-1.13.0', authorize=lambda *a, **k: None,
                      credential=lambda: 'x')
        with self.assertRaises(ValueError):
            JevProvider(wire=wire.WIRE_VERSION_7, **kwargs)
        provider = JevProvider(wire=wire.WIRE_VERSION_7, desk_seat=REGION, **kwargs)
        self.assertEqual((provider.wire, provider.desk_seat), (wire.WIRE_VERSION_7, REGION))
        with self.assertRaises(ValueError):
            JevProvider(wire=wire.WIRE_VERSION, questions=wire.QUESTION_VERSION_10, **kwargs)
        self.assertEqual(JevProvider(wire=wire.WIRE_VERSION_7, desk_seat=REGION, questions=wire.QUESTION_VERSION_10,
                                     **kwargs).question_version, wire.QUESTION_VERSION_10)


SCHEMA_DIGESTS = {
    # Unchanged since den-facts-6 was registered (structured-facts-9).
    'den-facts-6': '6d64c6fa488ff9f85abbe78ba592810ef10a3b09ce3a92a7c44033007a75fd4b',
    'den-facts-7': '05b169514b5f9fc5fb0b0548c66607bd19526248f0c30e1fb16b1950f714a59b',
    # room-facts-1 words, room_storyline and the ROOM_SUPPORT lines (test_room_facts).
    'den-facts-8': 'c2d0c5e1bb22e07747b32c2fd10db421aa6d448d941dd17cc29983c7ad9ec482',
}
