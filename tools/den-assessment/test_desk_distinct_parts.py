"""Authored v2 surveys and real allocator checks; no measurement qualification.

Only generated inputs and a bounded loopback guide are used. No images, private
assets, camera, model, installed service or physical writer is opened.
"""
from copy import deepcopy
import http.client
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import desk_enclosure as consumer


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


legacy = module('desk_distinct_legacy_fixtures', HERE / 'test_desk_enclosure.py')
guide = legacy.fixture.guide
feasibility = module('desk_distinct_allocator_harness', ROOT / 'tools/den-live-runtime/test_desk_enclosure_feasibility.py')


def measured_box(center, half):
    return [legacy.fixture.measurement(*(center[n] + sign * half[n] for n in range(3)), uncertainty=.001)
            for sign in (-1, 1)]


def survey():
    value = legacy.fixture.survey()
    value.update(schema='home-desk-geometry-survey-2', version=2)
    del value['seated_envelope']
    value['references']['reference_uncertainty_m'] = .001
    value['work_patch'] = {'outer_corners': [legacy.fixture.measurement(1.75, 1.8, 1.08, .001),
                                            legacy.fixture.measurement(2.25, 2.2, 1.08, .001)],
                           'thickness_m': .16}
    value['parts'] = {}
    for name, center, half in (
        ('body', [3.5, 2, 1.1], [.18, .15, .5]),
        ('head', [3.5, 2, 1.8], [.12, .12, .15]),
        ('hand_left', [1.55, 2, 1], [.025, .025, .025]),
        ('hand_right', [2.45, 2, 1], [.025, .025, .025]),
        ('protection', [3.5, 2, 1.8], [.14, .14, .17])):
        value['parts'][name] = {'outer_corners': measured_box(center, half),
            'coverage_declaration': 'independently_measured_' + name + '_supported_configurations'}
    value['protection_policy'] = {'policy_ref': {'id': 'delivery-protection', 'digest': 'a' * 64},
                                  'source_file_sha256': 'b' * 64}
    return value


def inputs(value=None):
    candidate = guide.prepare_candidate(survey() if value is None else value, now=lambda: 90)
    _, assessment, anchor, binding = legacy.inputs()
    method = legacy.ref(consumer.DISTINCT_METHOD)
    assessment.update(schema='home-desk-enclosure-assessment-2', version=2,
        candidate_digest=candidate['digest'], method_ref=method,
        parts=deepcopy(candidate['parts']), work=deepcopy(candidate['work_patch']),
        protection_policy_ref=deepcopy(candidate['protection_policy_ref']), protected_parts=['head'],
        anchor_part='body', anchor_region={'kind': 'aabb', 'center_m': [3.5, 2, 1.1], 'half_extents_m': [1, 1, 1]})
    del assessment['enclosure']
    anchor.update(schema='home-desk-seated-anchor-2', version=2, point_m=[3.5, 2, 1.1])
    anchor['clock']['original_unit'] = 'unix_ns'
    anchor['source_position']['revision'] = 1
    binding.update(schema='home-desk-enclosure-binding-2', version=2,
        candidate_digest=candidate['digest'], method_ref=method,
        source_position=deepcopy(anchor['source_position']))
    binding['anchor_original_ref']['digest'] = consumer.digest(anchor)
    binding['assessment_ref']['digest'] = consumer.digest(assessment)
    return tuple(deepcopy(item) for item in (candidate, assessment, anchor, binding))


class DistinctPartsTests(unittest.TestCase):
    def setUp(self):
        self.candidate, self.assessment, self.anchor, self.binding = inputs()
        self.hazards = [{'id': 'prior-person', 'status': 'unresolved'}]

    def evaluate(self, now=100.5):
        return consumer.evaluate_desk_enclosure(self.candidate, binding=self.binding,
            assessment=self.assessment, anchor=self.anchor, now=now, hazards=self.hazards)

    def repin(self):
        self.binding['assessment_ref']['digest'] = consumer.digest(self.assessment)
        self.binding['anchor_original_ref']['digest'] = consumer.digest(self.anchor)

    def refused(self, reason):
        result = self.evaluate()
        self.assertEqual((result['status'], result['reason']), ('unavailable', reason))
        self.assertIsNone(result['geometry_proposal'])
        self.assertIsNone(result['operational_geometry'])
        self.assertEqual(result['inherited_hazards'], self.hazards)
        self.assertIsNone(result['hazard_clearance'])

    def test_v2_guide_retains_separate_measured_bounds_originals_and_zero_authority(self):
        original = survey()
        candidate = guide.prepare_candidate(original, now=lambda: 90)
        self.assertEqual(candidate['original_survey'], original)
        self.assertEqual(candidate['schema'], 'home-desk-geometry-candidate-2')
        self.assertEqual(set(candidate['parts']), set(consumer.PARTS))
        self.assertEqual(candidate['parts']['head']['center_m'], [3.5, 2, 1.8])
        self.assertNotEqual(candidate['parts']['head'], candidate['parts']['body'])
        self.assertNotEqual(candidate['parts']['hand_left'], candidate['parts']['hand_right'])
        self.assertEqual(candidate['protection_policy_ref'], original['protection_policy']['policy_ref'])
        self.assertEqual(candidate['survey_sha256'], guide.digest(original))
        self.assertFalse(candidate['qualified'])
        self.assertFalse(any(candidate['authority'].values()))
        self.assertIsNone(candidate['operational_geometry'])

    def test_missing_unmeasured_or_degenerate_part_never_borrows_another_shape(self):
        for mutate in (
            lambda v: v['parts'].pop('hand_left'),
            lambda v: v['parts']['body'].pop('coverage_declaration'),
            lambda v: v['parts']['head']['outer_corners'][1].update(height_above_floor_m=1.65),
            lambda v: v['parts']['hand_right']['outer_corners'][0].update(uncertainty_m=None),
            lambda v: v['protection_policy']['policy_ref'].update(id='policy/forged'),
            lambda v: v.update(operational_geometry=True)):
            value = survey(); mutate(value)
            with self.subTest(value=value), self.assertRaises(guide.SurveyRefusal):
                guide.prepare_candidate(value)

    def test_v2_consumer_keeps_distinct_assessed_parts_and_conservative_lease(self):
        before = deepcopy((self.candidate, self.assessment, self.anchor, self.binding))
        result = self.evaluate()
        self.assertEqual((result['schema'], result['version']), ('home-desk-enclosure-result-2', 2))
        self.assertEqual(result['status'], 'eligible_for_admission_review')
        proposal = result['geometry_proposal']
        self.assertEqual(proposal['version'], 2)
        self.assertEqual(proposal['protection_policy_ref'], self.candidate['protection_policy_ref'])
        self.assertEqual(proposal['protected_parts'], ['head'])
        self.assertEqual(proposal['anchor_original_ref'], self.binding['anchor_original_ref'])
        self.assertIsNone(proposal['body_forward']); self.assertIsNone(proposal['head_forward'])
        self.assertNotEqual(proposal['parts']['body'], proposal['parts']['head'])
        self.assertEqual(proposal['valid_until'], 101.999)
        self.assertFalse(any(result[k] for k in ('journal_authority', 'engagement_authority', 'beam_authority', 'physical_authority')))
        self.assertIsNone(result['operational_geometry'])
        self.assertEqual(before, (self.candidate, self.assessment, self.anchor, self.binding))

    def test_assessment_cannot_squeeze_any_measured_part_or_work_to_obtain_clearance(self):
        for name in consumer.PARTS:
            self.setUp()
            self.assessment['parts'][name]['half_extents_m'][0] /= 2
            self.repin(); self.refused('survey_' + name + '_undercovered')
        self.setUp(); self.assessment['work']['half_extents_m'][0] /= 2
        self.repin(); self.refused('survey_work_undercovered')

    def test_protection_policy_and_explicit_protected_part_coverage_are_required(self):
        self.assessment['protection_policy_ref']['digest'] = 'f' * 64
        self.repin(); self.refused('protection_policy_mismatch')
        self.setUp(); self.assessment['protected_parts'] = []
        self.repin(); self.refused('explicit_policy_protected_parts_required')
        self.setUp(); self.assessment['protected_parts'] = ['head', 'body']
        self.repin(); self.refused('policy_protection_undercovered')

    def test_originals_scope_qualification_reset_and_ambiguity_cannot_be_bypassed(self):
        self.assessment['parts']['head']['center_m'][0] += .1
        self.refused('assessment_original_mismatch')
        self.setUp(); self.anchor['point_m'][0] += .01
        self.refused('anchor_original_mismatch')
        for state in ('ambiguous', 'source_lost', 'reset', 'outside', 'departed'):
            self.setUp(); self.anchor['state'] = state; self.repin()
            self.refused('anchor_applicability_unavailable')
        self.setUp(); self.anchor['occupant_count'] = 2; self.repin()
        self.refused('unique_person_applicability_unavailable')
        self.setUp(); self.assessment['person_ref']['track_epoch'] = 'new-epoch'
        self.repin(); self.refused('assessment_scope_or_epoch_mismatch')
        self.setUp(); self.assessment['registration']['ref']['digest'] = 'e' * 64
        self.repin(); self.refused('registration_qualification_unavailable')

    def test_v2_uses_explicit_clock_units_and_integer_revisions(self):
        self.anchor['clock']['original_unit'] = 'ns'; self.repin()
        self.refused('clock_qualification_unavailable')
        self.setUp(); self.binding['source_position']['revision'] = 'r1'
        self.refused('integer_source_revision_required')
        self.setUp(); self.anchor['clock']['original_unit'] = 'device_ns'; self.repin()
        self.assertEqual(self.evaluate()['status'], 'eligible_for_admission_review')

    def test_full_future_anchor_lease_is_checked_against_its_assessed_part(self):
        self.anchor['point_m'][0] += .15; self.repin()
        # Now: .15 + .026265005 < .182. Lease end: .15 + .06 > .182.
        self.assertLess(.15 + .026265005, self.assessment['parts']['body']['half_extents_m'][0])
        self.assertGreater(.15 + .06, self.assessment['parts']['body']['half_extents_m'][0])
        self.refused('anchor_lease_not_enclosed')
        self.setUp(); self.assessment['valid_until'] = 100.5; self.repin()
        self.refused('assessment_not_current')

    def allocate(self, result):
        self.assertEqual(result['status'], 'eligible_for_admission_review')
        self.assertIsNone(result['operational_geometry'])
        node = shutil.which('node')
        if node is None:
            self.fail('existing Node runtime required; no install or false pass')
        source = feasibility.ALLOCATE.replace('const geometry=projectedGeometry();',
            "if(L.digest(proposal.protection_policy_ref)!==L.digest(policy.protection.policy_ref))throw Error('policy pin mismatch');\nconst geometry=projectedGeometry();")
        run = subprocess.run([node, '--max-old-space-size=128', '-e', source], cwd=ROOT,
            input=json.dumps({'proposal': result['geometry_proposal'], 'independent_distinct_parts': False}),
            text=True, capture_output=True, timeout=8)
        self.assertEqual(run.returncode, 0, run.stderr[:2000])
        self.assertLess(len(run.stdout), 32768)
        return json.loads(run.stdout)

    def test_actual_v2_proposal_distinct_parts_permit_authored_positive_allocator_service(self):
        checked = self.allocate(self.evaluate())
        self.assertFalse(checked['operational_admission'])
        self.assertEqual(checked['start']['allocation']['status'], 'allocated')
        self.assertEqual(checked['later']['execution']['status'], 'virtual')
        self.assertGreater(checked['later']['execution']['readback'][0]['brightness_fraction'], 0)

    def test_actual_v2_proposal_with_conservative_blanket_parts_still_refuses(self):
        value = survey()
        for name in consumer.PARTS:
            value['parts'][name]['outer_corners'] = measured_box([2.5, 2, 1.1], [1.5, 1, 1.1])
        self.candidate, self.assessment, self.anchor, self.binding = inputs(value)
        self.anchor['point_m'] = [2.5, 2, 1.1]
        self.assessment['anchor_region']['center_m'] = [2.5, 2, 1.1]
        self.repin()
        checked = self.allocate(self.evaluate())
        self.assertEqual(checked['start']['allocation']['reason'], 'protected_volume_intersection')
        self.assertEqual(checked['later']['execution']['status'], 'unavailable')

    def test_actual_loopback_v2_form_contract_saves_private_candidate_and_stops(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            path = Path(temporary) / 'candidate.json'
            server = guide.Guide(path, seconds=10)
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('GET', '/')
                response = connection.getresponse(); page = response.read().decode()
                self.assertEqual(response.status, 200)
                for name in ('surveyVersion', 'distinctParts', 'protectionPolicy', 'partForms'):
                    self.assertIn('id="' + name + '"', page)
                connection.close()
                headers = {'Origin': server.origin, 'X-Desk-Nonce': server.nonce, 'Content-Type': 'application/json'}
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('POST', '/prepare', guide.encoded(survey()), headers)
                response = connection.getresponse(); reply = json.loads(response.read())
                self.assertEqual(response.status, 200, reply)
                self.assertEqual(reply['status'], 'candidate_saved')
                candidate = json.loads(path.read_bytes())
                self.assertEqual(candidate['schema'], 'home-desk-geometry-candidate-2')
                self.assertFalse(candidate['qualified'])
                self.assertEqual(set(candidate['parts']), set(consumer.PARTS))
                connection.close()
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                connection.request('POST', '/stop', b'{}', headers)
                response = connection.getresponse(); self.assertEqual(json.loads(response.read()), {'status': 'stopped'})
                connection.close()
            finally:
                server.stopped.set(); thread.join(2)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
