"""Authored independent references; no camera/model/host or qualification."""
from copy import deepcopy
import hashlib
import json
import unittest

from desk_method_assessment import DeskAssessmentRefusal, HeldDeskAssessment
from test_desk_distinct_parts import guide, survey


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def requirements():
    return dict(max_work_center_error_m=.05, min_containment_fraction=1,
        max_undercoverage_fraction=0, min_complete_protection_fraction=1,
        min_reference_s=1, max_capture_to_available_s=1)


def reference_parts(candidate):
    values = {}
    original = candidate['original_survey']
    # Independently generated held bounds lie strictly inside the proposed
    # candidate bounds. These are not copied model points or live assessment.
    for name in ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection'):
        source = original['work_patch'] if name == 'work' else original['parts'][name]
        raw = deepcopy(source['outer_corners'])
        for axis in range(3):
            key = 'height_above_floor_m' if axis == 2 else 'wall_distances_m'
            get = lambda row: row[key] if axis == 2 else row[key][axis]
            center = (get(raw[0]) + get(raw[1])) / 2
            for row in raw:
                value = center + (get(row) - center) * .5
                if axis == 2: row[key] = value
                else: row[key][axis] = value
        thickness = source.get('thickness_m', 0)
        references = deepcopy(original['references'])
        values[name] = dict(status='available',
            shape=guide.outer_box(raw, references, thickness_m=thickness, require_volume=name != 'work'),
            original_measurements=dict(references=references, outer_corners=raw, thickness_m=thickness))
    return values


def verified_collection():
    inner = dict(session=dict(id='authored-lab', site_id='Victoria', camera_id='authored-oak'),
        arm='b', model_generation=1, media_generation=0,
        lab_profile=dict(profile_id='la-arm-b', engine_epoch='epoch', model_version='detector-pose', artifact_sha256='a' * 64),
        sampling=dict(radius_px=4, min_support=3), collection_mode='existing-api-assessment-1',
        existing_api_binding=dict(policy_sha256='b' * 64, site_id='Victoria', camera_id='authored-oak',
            session_id='authored-lab', media_generation=0, experiment_generation=1))
    binding = dict(schema='home-lab-assessment-binding-2', version=2, binding=inner,
        binding_digest=hashlib.sha256(encoded(inner)).hexdigest(), original_session=dict(id='authored-lab'),
        original_readiness=dict(generation=1), started_at=100, limits=dict(max_attempts=24, seconds=30, max_bytes=128 * 1024**2))
    attempts = []
    for event_id, status in ((1, 'candidate'), (2, 'refused')):
        attempts.append(dict(schema='home-lab-assessment-attempt-2', version=2,
            id='attempt-' + str(event_id), source_event_id=event_id,
            binding_digest=binding['binding_digest'], candidate_only=True, error_bound_m=None,
            coordinate_frame='rectified_left', unit='m', status=status,
            reason='independent_error_unavailable' if status == 'candidate' else 'original_pair_unavailable',
            source_times=dict(observed_at=100 + event_id / 10, available_at=100 + event_id / 10 + .01, valid_until=103),
            instances=[dict(joints=[dict(joint_id=j, status='candidate' if j == 0 else 'unavailable',
                camera_xyz_m=[0, 0, 2] if j == 0 else None) for j in range(17)])] if status == 'candidate' else [],
            operational_geometry=None, geometry_authority=False, identity_authority=False,
            protection_authority=False, beam_authority=False))
    return dict(binding=binding, attempts=attempts,
        summary=dict(complete=True, attempts=2, candidates=1, refused=1, archive_sha256='c' * 64),
        collection_sha256='c' * 64)


class DeskMethodAssessmentTests(unittest.TestCase):
    def test_unconfirmed_durable_action_never_promotes_and_poison_blocks_public_retries(self):
        for failed_kind in ('freeze', 'start', 'end', 'report'):
            with self.subTest(action=failed_kind):
                candidate = guide.prepare_candidate(survey(), now=lambda: 90)
                now, mono, records, loads = [100.], [1_000_000_000], [], []
                def record(action):
                    records.append(deepcopy(action))
                    if action['kind'] == failed_kind:
                        if failed_kind == 'report':
                            raise OSError('authored_data_fsync_unconfirmed')
                        return dict(status='unconfirmed')
                    return dict(status='retained', sha256=hashlib.sha256(encoded(action)).hexdigest())
                def loader():
                    loads.append(True)
                    return verified_collection()
                session = HeldDeskAssessment(candidate, clock=lambda: now[0], monotonic=lambda: mono[0],
                                             verified_collection_loader=loader, record=record)
                def end():
                    now[0], mono[0] = 102., 3_000_000_000
                    return session.end('held-1')
                operations = (
                    ('freeze', lambda: session.freeze(dict(id='durability-protocol', reference_method='tape',
                        supported_configuration_ids=['seated-neutral'], max_windows=1), requirements())),
                    ('start', lambda: session.start('held-1', 'seated-neutral', reference_parts(candidate), 0, 'focused')),
                    ('end', end), ('report', session.report))
                failed_operation = None
                for kind, operation in operations:
                    if kind == failed_kind:
                        with self.assertRaisesRegex(DeskAssessmentRefusal, '^reference_recording_incomplete$'):
                            operation()
                        failed_operation = operation
                        break
                    operation()
                recorded_before, loads_before = deepcopy(records), len(loads)
                # Neither exact retry nor requesting results can turn an unknown
                # durable effect into a successful state or repeat the effect.
                for retry in (failed_operation, session.report):
                    with self.assertRaisesRegex(DeskAssessmentRefusal, '^reference_recording_incomplete$'):
                        retry()
                self.assertEqual(records, recorded_before)
                self.assertEqual(len(loads), loads_before)
                self.assertEqual(loads_before, 1 if failed_kind == 'report' else 0)

    def test_start_reserves_full_final_report_before_accepting_more_independent_work(self):
        measured = survey()
        # Valid long names exercise actual canonical UTF-8/JSON byte expansion,
        # rather than changing production capacity to manufacture exhaustion.
        measured['references']['floor']['id'] = 'floor-' + '\u754c' * 190
        for i, wall in enumerate(measured['references']['walls']):
            wall['id'] = 'wall-' + str(i) + '\u754c' * 190
            wall['label'] = 'wall-label-' + '\u754c' * 185
        candidate = guide.prepare_candidate(measured, now=lambda: 90)
        now, mono, records = [100.], [1_000_000_000], []
        def record(action):
            records.append(deepcopy(action))
            return dict(status='retained', sha256=hashlib.sha256(encoded(action)).hexdigest())
        session = HeldDeskAssessment(candidate, clock=lambda: now[0], monotonic=lambda: mono[0],
                                     verified_collection_loader=verified_collection, record=record)
        session.freeze(dict(id='bounded-report-protocol', reference_method='tape',
            supported_configuration_ids=['seated-neutral'], max_windows=32), requirements())
        references, accepted, refused_at = reference_parts(candidate), [], None
        for index in range(32):
            count_before = len(records)
            try:
                session.start('held-' + str(index), 'seated-neutral', references, 0, 'focused')
            except DeskAssessmentRefusal as error:
                self.assertEqual(str(error), 'reserved_reference_report_capacity')
                self.assertEqual(len(records), count_before)
                refused_at = index
                break
            accepted.append(index)
            now[0] += 1
            mono[0] += 1_000_000_000
            session.end('held-' + str(index))
        self.assertIsNotNone(refused_at, 'next Start must refuse before its final full report can exceed capacity')
        self.assertGreater(len(accepted), 0)
        self.assertLess(len(accepted), 32)
        report = session.report()
        self.assertEqual(report['proposed_bounds']['work']['eligible_reference_windows'], len(accepted))
        self.assertEqual(records[-1]['kind'], 'report')
        self.assertTrue(all(len(encoded(action)) <= 1024**2 for action in records))
        self.assertLessEqual(sum(len(encoded(action)) + 1 for action in records), 8 * 1024**2)

    def test_contained_static_work_box_with_shifted_reference_fails_frozen_center_requirement(self):
        candidate = guide.prepare_candidate(survey(), now=lambda: 90)
        now, mono = [100.], [1_000_000_000]
        def record(action):
            return dict(status='retained', sha256=hashlib.sha256(encoded(action)).hexdigest())
        session = HeldDeskAssessment(candidate, clock=lambda: now[0], monotonic=lambda: mono[0],
                                     verified_collection_loader=verified_collection, record=record)
        session.freeze(dict(id='center-error-protocol', reference_method='tape',
            supported_configuration_ids=['seated-neutral'], max_windows=1), requirements())
        references = reference_parts(candidate)
        # A 10 cm independently measured shift is still inside this authored
        # wide work patch. Containment cannot substitute for the 5 cm rule.
        measured = references['work']['original_measurements']
        for corner in measured['outer_corners']:
            corner['wall_distances_m'][0] += .1
        references['work']['shape'] = guide.outer_box(measured['outer_corners'], measured['references'],
            thickness_m=measured['thickness_m'])
        session.start('held-shift', 'seated-neutral', references, 0, 'focused')
        now[0], mono[0] = 102., 3_000_000_000
        session.end('held-shift')
        report = session.report()
        self.assertEqual(report['proposed_bounds']['work']['contained_reference_windows'], 1)
        self.assertAlmostEqual(report['proposed_bounds']['work']['max_reference_center_distance_m'], .1)
        self.assertIn('work_proposed_center_error', report['failed_requirements'])
        self.assertEqual(report['proposal_decision'], 'block')
        self.assertNotIn('max_work_center_error_m', report['unevaluated_requirements'])
        self.assertIsNone(report['candidate_observations']['qualified_error_bound_m'])

    def test_six_independent_bounds_and_actual_camera_attempts_produce_unsigned_review(self):
        candidate = guide.prepare_candidate(survey(), now=lambda: 90)
        now, mono, records, loads = [100.], [1_000_000_000], [], []
        def record(action):
            records.append(deepcopy(action))
            return dict(status='retained', sha256=hashlib.sha256(encoded(action)).hexdigest())
        def loader():
            loads.append(True)
            return verified_collection()
        session = HeldDeskAssessment(candidate, clock=lambda: now[0], monotonic=lambda: mono[0],
                                     verified_collection_loader=loader, record=record)
        frozen = session.freeze(dict(id='authored-independent-protocol', reference_method='tape',
            supported_configuration_ids=['seated-neutral'], max_windows=4), requirements())
        self.assertEqual(frozen['status'], 'frozen')
        started = session.start('held-1', 'seated-neutral', reference_parts(candidate), 0, 'focused')
        self.assertEqual((started['status'], started['window_id']), ('started', 'held-1'))
        self.assertEqual(loads, [])  # Predictions stay unread until references end.
        now[0], mono[0] = 102., 3_000_000_000
        ended = session.end('held-1')
        self.assertEqual(ended['duration_s'], 2)
        report = session.report()
        self.assertEqual(len(loads), 1)
        self.assertEqual(set(report['proposed_bounds']), {'work', 'body', 'head', 'hand_left', 'hand_right', 'protection'})
        self.assertTrue(all(row['contained_reference_windows'] == 1 for row in report['proposed_bounds'].values()))
        self.assertEqual(report['candidate_observations']['attempts'], 2)
        self.assertEqual(report['candidate_observations']['refused_attempts'], 1)
        self.assertEqual(report['candidate_observations']['joint_slots']['0']['candidate'], 1)
        self.assertEqual(report['operational_qualification']['decision'], 'block')
        self.assertEqual(report['clock']['error_bound_s'], None)
        self.assertIsNone(report['operational_geometry'])
        self.assertFalse(report['qualified'])
        self.assertEqual([r['kind'] for r in records], ['freeze', 'start', 'end', 'report'])
        self.assertNotIn('instances', encoded(report).decode())


if __name__ == '__main__': unittest.main()
