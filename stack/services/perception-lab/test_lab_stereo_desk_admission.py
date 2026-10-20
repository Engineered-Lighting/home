"""Desk2 glue on the actual Journal; numerical stereo proof is tested separately.

The pure stereo verifier is an explicit authored external-I/O double here. These
tests establish versioning, root/time propagation, and post-validation fences;
they do not qualify a depth estimator or household geometry.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from journal import digest
from lab_live_desk_admission import LabLiveDeskAdmission, DeskAdmissionRefusal
from live_test_helpers import NOW
import test_lab_live_desk_admission as existing


class StereoDeskAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = existing.LabLiveDeskAdmissionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.policy1 = deepcopy(self.f.policy)
        self.f.policy.update(schema='home-lab-live-desk-admission-policy-2', version=2,
                             stereo_policy_ref={'id': 'authored-stereo', 'digest': 'c' * 64})
        self.f.values['stereo_support'] = {'capture_ref': deepcopy(self.f.values['capture_ref'])}
        self.f.values['stereo_qualification'] = {
            **{key: deepcopy(self.f.values[key]) for key in ('projection', 'registration', 'clock_mapping')},
            'association': {'subject_ref': deepcopy(self.f.values['binding']['subject_ref'])},
            'stereo_policy': {'association_method_ref': deepcopy(self.f.policy['association_method_ref'])}}
        self.depth = {'valid_until': NOW + 1.25,
            'observation_root_times': {'authored-separate-depth': NOW - .05},
            'sensor_root_bindings': {'authored-separate-depth': deepcopy(self.f.producer.source['sensor_roots'])}}
        self.verifier = patch('lab_live_desk_anchor.validate_stereo_support', return_value=deepcopy(self.depth)).start()
        self.addCleanup(patch.stopall)
        self.f.producer = self.producer()

    def producer(self, *, policy=None, **options):
        return LabLiveDeskAdmission(self.f.config, policy=policy or self.f.policy, revision='synthetic-r1',
            resolve_inputs=lambda _: deepcopy(self.f.values), original_retainer=self.f.retain,
            clock=lambda: self.f.now, **({'stereo_media_resolver': lambda _: None} | options))

    def test_rgb_fence_and_distinct_depth_root_reach_actual_journal_without_clock_refresh(self):
        result = self.f.consume()
        self.assertEqual(result['status'], 'admitted', result)
        body = result['event']['payload']
        original_roots = self.f.values['capture_record']['observation_root_times']
        self.assertEqual(body['observation_root_times'], {**original_roots, **self.depth['observation_root_times']})
        self.assertEqual(body['clock']['original_value'], self.f.values['anchor']['clock']['original_value'])
        self.assertEqual(body['observed_at'], NOW)
        self.assertEqual(body['valid_until'], self.depth['valid_until'])
        self.assertEqual(self.f.retained[0]['schema'], 'home-original-lab-desk-2')
        self.assertEqual(self.f.retained[0]['version'], 2)
        self.assertEqual(result['inherited_hazards'], self.f.hazards)
        self.assertIsNone(result['hazard_clearance'])
        first = deepcopy(result)
        self.f.now += 20
        self.assertEqual(self.f.consume(), first, 'exact committed retry never refreshes a depth clock or beam')

    def test_original_rgb_publication_is_still_required(self):
        self.f.values['capture_ref']['cursor'] += 999
        self.f.values['binding']['parent_publication_refs'] = [deepcopy(self.f.values['capture_ref'])]
        self.f.values['stereo_support']['capture_ref'] = deepcopy(self.f.values['capture_ref'])
        self.f.unavailable('capture_head_not_current')
        self.assertFalse(self.f.retained)

    def test_stereo_qualification_cannot_substitute_another_registration_or_subject(self):
        self.f.values['stereo_qualification']['registration']['error_bound_m'] += .01
        self.f.unavailable('stereo_desk_qualification_binding_mismatch')
        self.assertFalse(self.f.retained)

    def test_slow_binary_revalidation_cannot_make_first_append_after_expiry(self):
        calls = []
        def slow(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                self.f.now = NOW + 1.5
            return deepcopy(self.depth)
        self.verifier.side_effect = slow
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'desk_support_expired_before_admission'):
            self.f.consume()
        self.assertEqual(len(self.f.retained), 1)
        self.assertEqual(self.f.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_stereo_requires_explicit_policy_and_fixed_binary_resolver(self):
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'fixed_stereo_media_resolver_required'):
            self.producer(stereo_media_resolver=None)
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'fixed_stereo_media_resolver_required'):
            self.producer(policy=self.policy1)
        old = self.producer(policy=self.policy1, stereo_media_resolver=None)
        self.assertEqual(old.policy, self.policy1)
        self.assertEqual(old.binding_digest, digest([self.f.config, self.policy1, 'synthetic-r1']))


if __name__ == '__main__':
    unittest.main()
