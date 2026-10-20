"""Generated-source mechanism checks; no qualification, host, model or asset I/O."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from desk_enclosure import evaluate_desk_enclosure, digest, METHOD, PARTS
spec = importlib.util.spec_from_file_location('desk_survey_test_fixture', HERE / 'desk-geometry-tests.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def ref(name):
    return {'id': name, 'digest': digest(name)}


def inputs():
    candidate = fixture.guide.prepare_candidate(fixture.survey(), now=lambda: 90)
    scope = dict(site_id='victoria', room_id='den', session_id='authored', domain_epoch='domain-1')
    person = dict(scope, pid=1, track_epoch='track-1')
    subject = dict(subject_id='desk-work', subject_epoch='subject-1')
    registration, clock = ref('registration'), ref('clock')
    assessment = dict(schema='home-desk-enclosure-assessment-1', version=1, id='assessment',
        status='independently_assessed', scope=scope, person_ref=person, subject_ref=subject,
        device_id='authored-pc', workstation_id='authored-desk', candidate_digest=candidate['digest'],
        method_ref=ref(METHOD), association_method_ref=ref('seated-anchor-v1'),
        registration=dict(ref=registration, status='qualified', error_bound_m=.01, valid_until=200),
        clock_mapping=dict(ref=clock, status='qualified', uncertainty_s=.01, valid_until=200),
        anchor_region=dict(kind='aabb', center_m=[1.5, 1.25, 1.2], half_extents_m=[.3, .3, .3]),
        enclosure=deepcopy(candidate['seated_person_envelope']), part_coverage=list(PARTS),
        hidden_configuration_basis='independent_all_supported_hidden_configurations',
        reference_protocol_ref=ref('authored-independent-protocol'), requirements_ref=ref('authored-requirements'),
        assessment_report_ref=ref('authored-report'), observed_at=90, available_at=91, valid_until=200,
        max_anchor_age_s=2, max_anchor_error_m=.1,
        motion_bound=dict(max_speed_m_s=.01, max_accel_m_s2=.01))
    anchor = dict(schema='home-desk-seated-anchor-1', version=1, id='anchor-7', scope=scope,
        person_ref=person, method_ref=assessment['association_method_ref'], source_generation=1,
        source_position=dict(capture_epoch='capture-1', capture_seq=7, revision='r1'),
        frame='z_up_metric_floor0', unit='m', state='seated_unique', occupant_count=1,
        point_m=[1.5, 1.25, 1.2], error_bound_m=.01,
        clock=dict(status='qualified', mapping_id=clock['id'], mapping_digest=clock['digest'],
            source_epoch='capture-1', original_unit='ns', original_value='1792173344000000120',
            uncertainty_s=.001), observed_at=100, available_at=100.1, valid_until=102)
    original = dict(store_id='authored-originals', receipt_id=anchor['id'], digest=digest(anchor),
        component_id='seated-anchor', observed_at=100, available_at=100.1, clock_mapping_id=clock['id'])
    binding = dict(schema='home-desk-enclosure-binding-1', version=1, scope=scope, person_ref=person,
        subject_ref=subject, device_id=assessment['device_id'], workstation_id=assessment['workstation_id'],
        method_ref=assessment['method_ref'], association_method_ref=assessment['association_method_ref'],
        candidate_digest=candidate['digest'], assessment_ref=dict(id=assessment['id'], digest=digest(assessment)),
        registration_ref=registration, clock_mapping_ref=clock, source_generation=1,
        source_position=deepcopy(anchor['source_position']), anchor_original_ref=original,
        parent_publication_refs=[dict(stream_epoch='journal-1', cursor=7, producer_id='authored-anchor',
            id='anchor-publication', payload_digest=digest('anchor-publication'), component_id=None)])
    return candidate, assessment, anchor, binding


class DeskEnclosureTests(unittest.TestCase):
    def setUp(self):
        self.candidate, self.assessment, self.anchor, self.binding = inputs()
        self.hazards = [{'id': 'unresolved-person-previous-epoch', 'status': 'unresolved'}]

    def evaluate(self, now=100.5, **options):
        return evaluate_desk_enclosure(self.candidate, binding=self.binding,
            assessment=options.get('assessment', self.assessment), anchor=options.get('anchor', self.anchor),
            now=now, hazards=self.hazards)

    def repin_anchor(self):
        self.binding['anchor_original_ref']['digest'] = digest(self.anchor)

    def repin_assessment(self):
        self.binding['assessment_ref']['digest'] = digest(self.assessment)

    def refused(self, result, reason):
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], reason)
        self.assertIsNone(result['geometry_proposal'])
        self.assertIsNone(result['association_proposal'])
        self.assertIsNone(result['operational_geometry'])
        self.assertEqual(result['inherited_hazards'], self.hazards)
        self.assertIsNone(result['hazard_clearance'])

    def test_real_guide_shape_and_authored_independent_original_yield_local_proposals_only(self):
        before = deepcopy((self.candidate, self.assessment, self.anchor, self.binding, self.hazards))
        result = self.evaluate()
        self.assertEqual(result['status'], 'eligible_for_admission_review')
        self.assertEqual(set(result['geometry_proposal']['parts']), set(PARTS))
        self.assertEqual(result['association_proposal']['person_ref'], self.binding['person_ref'])
        self.assertEqual(result['association_proposal']['parent_publication_refs'], self.binding['parent_publication_refs'])
        self.assertIsNone(result['operational_geometry'])
        self.assertFalse(any(result[key] for key in ('journal_authority', 'engagement_authority', 'beam_authority', 'physical_authority')))
        self.assertEqual(before, (self.candidate, self.assessment, self.anchor, self.binding, self.hazards))
        self.assertEqual(result['inherited_hazards'], self.hazards)

    def test_missing_assessment_or_anchor_never_upgrades_guide_candidates(self):
        self.refused(self.evaluate(assessment=None), 'independent_assessment_unavailable')
        self.refused(self.evaluate(anchor=None), 'current_seated_anchor_unavailable')

    def test_changed_originals_cannot_reuse_digest(self):
        self.assessment['max_anchor_error_m'] = 1
        self.refused(self.evaluate(), 'assessment_original_mismatch')
        self.setUp(); self.anchor['point_m'][0] += .01
        self.refused(self.evaluate(), 'anchor_original_mismatch')
        self.setUp(); self.candidate['work_patch']['center_m'][0] += .01
        self.refused(self.evaluate(), 'candidate_digest_mismatch')

    def test_two_occupants_and_ambiguous_departed_lost_reset_states_refuse(self):
        self.anchor['occupant_count'] = 2; self.repin_anchor()
        self.refused(self.evaluate(), 'unique_person_applicability_unavailable')
        for state in ('ambiguous', 'departed', 'source_lost', 'reset', 'outside'):
            self.setUp(); self.anchor['state'] = state; self.repin_anchor()
            self.refused(self.evaluate(), 'anchor_applicability_unavailable')

    def test_reset_or_new_scope_cannot_inherit_assessment_or_association(self):
        self.anchor['person_ref'] = {**self.anchor['person_ref'], 'track_epoch': 'track-2'}
        self.repin_anchor()
        self.refused(self.evaluate(), 'anchor_scope_or_epoch_mismatch')
        self.setUp(); self.anchor['scope'] = {**self.anchor['scope'], 'session_id': 'new-session'}
        self.repin_anchor()
        self.refused(self.evaluate(), 'anchor_scope_or_epoch_mismatch')
        self.setUp(); self.binding['person_ref'] = {**self.binding['person_ref'], 'track_epoch': 'track-2'}
        self.refused(self.evaluate(), 'assessment_scope_or_epoch_mismatch')

    def test_current_source_head_capture_and_generation_are_exact(self):
        self.binding['source_position']['capture_seq'] = 8
        self.refused(self.evaluate(), 'anchor_not_current_head')
        self.setUp(); self.binding['source_generation'] = 2
        self.refused(self.evaluate(), 'anchor_not_current_head')

    def test_registration_clock_bounds_and_original_time_are_required(self):
        self.binding['registration_ref'] = ref('other-registration')
        self.refused(self.evaluate(), 'registration_qualification_unavailable')
        self.setUp(); self.assessment['registration']['status'] = 'candidate'; self.repin_assessment()
        self.refused(self.evaluate(), 'registration_qualification_unavailable')
        self.setUp(); self.anchor['clock']['status'] = 'unverified'; self.repin_anchor()
        self.refused(self.evaluate(), 'clock_qualification_unavailable')
        self.setUp(); self.anchor['clock']['uncertainty_s'] = .02; self.repin_anchor()
        self.refused(self.evaluate(), 'clock_bound_exceeded')
        self.setUp(); self.binding['anchor_original_ref']['observed_at'] = 99
        self.refused(self.evaluate(), 'original_anchor_clocks_mismatch')

    def test_expiry_is_exclusive_and_repeat_evaluation_never_refreshes(self):
        first, second = self.evaluate(), self.evaluate(now=101)
        self.assertEqual(first['association_proposal'], second['association_proposal'])
        self.assertEqual(first['association_proposal']['observed_at'], 100)
        self.assertEqual(first['association_proposal']['available_at'], 100.1)
        self.assertEqual(first['association_proposal']['valid_until'], 101.999)
        self.refused(self.evaluate(now=101.999), 'anchor_not_current')

    def test_expired_assessment_registration_or_clock_refuses(self):
        for key, expected in (('registration', 'registration_assessment_expired'),
                              ('clock_mapping', 'clock_assessment_expired')):
            self.setUp(); self.assessment[key]['valid_until'] = 100.5; self.repin_assessment()
            self.refused(self.evaluate(), expected)
        self.setUp(); self.assessment['valid_until'] = 100.5; self.repin_assessment()
        self.refused(self.evaluate(), 'assessment_not_current')

    def test_hidden_configuration_proof_cannot_be_replaced_by_face_or_pc_observation(self):
        self.assessment['hidden_configuration_basis'] = 'visible_face_only'; self.repin_assessment()
        self.refused(self.evaluate(), 'complete_hidden_configuration_bound_unavailable')
        self.setUp(); self.assessment['part_coverage'].remove('hand_left'); self.repin_assessment()
        self.refused(self.evaluate(), 'complete_hidden_configuration_bound_unavailable')
        self.setUp(); self.assessment['enclosure']['half_extents_m'][0] /= 2; self.repin_assessment()
        self.refused(self.evaluate(), 'survey_enclosure_undercovered')

    def test_lean_and_reach_within_independently_enclosed_scope_do_not_move_static_work(self):
        base = self.evaluate()['geometry_proposal']
        self.anchor['point_m'][0] += .15; self.repin_anchor()
        result = self.evaluate()
        self.assertEqual(result['status'], 'eligible_for_admission_review')
        self.assertEqual(result['geometry_proposal']['work'], base['work'])
        self.assertEqual(result['geometry_proposal']['parts'], base['parts'])
        self.assertEqual(result['moving_work'], 'unsupported_static_method')

    def test_uncertainty_and_motion_enlarge_anchor_applicability_not_only_center(self):
        self.anchor['point_m'][0] += .295; self.repin_anchor()
        self.refused(self.evaluate(), 'anchor_outside_assessed_applicability')
        self.setUp(); self.assessment['motion_bound']['max_speed_m_s'] = 1; self.repin_assessment()
        self.refused(self.evaluate(), 'anchor_motion_error_exceeded')


    def test_hazards_are_bounded_before_iteration_or_copy(self):
        consumed = []
        def unbounded():
            consumed.append(True)
            while True:
                yield {}
        for hazards in (None, 'not-hazards', unbounded(), [{}] * 65,
                        [float('nan')], [{'id': object()}]):
            result = evaluate_desk_enclosure(self.candidate, binding=self.binding,
                assessment=self.assessment, anchor=self.anchor, now=100.5, hazards=hazards)
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['inherited_hazards'])
            self.assertIsNone(result['hazard_clearance'])
            self.assertIsNone(result['geometry_proposal'])
        self.assertEqual(consumed, [])

    def test_anchor_must_be_enclosed_even_when_wrong_applicability_region_is_broad(self):
        self.assessment['anchor_region']['half_extents_m'] = [5, 5, 5]
        self.repin_assessment()
        self.anchor['point_m'][0] = 4
        self.repin_anchor()
        self.refused(self.evaluate(), 'observed_anchor_not_enclosed')


    def test_lease_cannot_outlive_assessed_anchor_containment(self):
        # Independent arithmetic: at age .501 s the total radius is
        # .01 + .01 + .01*.501 + .5*.01*.501**2 = .026265005 m.
        # The .27 m displacement therefore fits a .30 m region now.
        # By the advertised exclusive lease end, age approaches 2 s and
        # radius approaches .06 m: .27 + .06 exceeds the assessed region.
        self.assertLess(.27 + .026265005, .30)
        self.assertGreater(.27 + .06, .30)
        self.anchor['point_m'][0] += .27
        self.repin_anchor()
        self.refused(self.evaluate(), 'anchor_lease_outside_assessed_applicability')
        self.setUp()
        self.assessment['max_anchor_error_m'] = .04
        self.repin_assessment()
        self.assertLess(.026265005, .04)
        self.assertGreater(.06, .04)
        self.refused(self.evaluate(), 'anchor_lease_motion_error_exceeded')
        self.setUp()
        self.assessment['anchor_region']['half_extents_m'] = [5, 5, 5]
        self.repin_assessment()
        self.anchor['point_m'][0] += .99
        self.repin_anchor()
        self.assertLess(.99 + .026265005, 1.02)
        self.assertGreater(.99 + .06, 1.02)
        self.refused(self.evaluate(), 'anchor_lease_not_enclosed')

    def test_unknown_facing_is_no_block_and_never_a_rear_side_claim(self):
        result = self.evaluate()['geometry_proposal']
        self.assertIsNone(result['body_forward']); self.assertIsNone(result['head_forward'])

    def test_future_anchor_invalid_values_and_missing_current_parent_proof_refuse(self):
        self.refused(self.evaluate(now=100), 'anchor_not_current')
        self.setUp(); self.binding['parent_publication_refs'] = []
        self.refused(self.evaluate(), 'current_parent_publications_required')
        self.setUp(); self.anchor['point_m'][0] = float('nan')
        self.refused(self.evaluate(), 'bounded_plain_json_required')


if __name__ == '__main__':
    unittest.main()
