"""Exact capture-only classification; no person/protection authority is granted."""
from copy import deepcopy
import unittest

from live_delivery import _capture_metadata


class LabCaptureMetadataTests(unittest.TestCase):
    def fixture(self, reason='missing_measurement_bound'):
        scope = dict(site_id='authored', room_id='room', session_id='session', domain_epoch='epoch')
        method = dict(id='capture', digest='a' * 64)
        registration = dict(registration_ref='authored-registration')
        payload = dict(schema='home-live-geometry-1', version=1, **scope, person_ref=None,
            subject_ref=None, status='unobservable', reason=reason, source_id='camera',
            method_ref=deepcopy(method), registration=deepcopy(registration), observed_at=10, valid_until=12,
            association=dict(person_status='not_applicable', subject_status='not_applicable',
                candidate_person_refs=[], candidate_subject_refs=[], method_ref=deepcopy(method)),
            head=dict(key=dict(source_id='camera', payload_type='home-live-geometry-1',
                person_ref=None, subject_ref=None, predicate='geometry_bundle', method_ref=deepcopy(method))),
            parts={name: dict(status='unobservable', reason=reason, support_refs=[], observed_at=10, valid_until=12)
                   for name in ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection', 'body_forward', 'head_forward')})
        return payload, scope, registration

    def test_both_closed_producers_are_metadata_without_mutating_original(self):
        for reason in ('pose_unavailable', 'missing_measurement_bound'):
            p, scope, registration = self.fixture(reason)
            original = deepcopy(p)
            self.assertTrue(_capture_metadata(p, identity=scope, registration=registration))
            self.assertEqual(p, original)

    def test_ambiguous_people_and_mixed_parts_are_never_metadata(self):
        mutations = [
            lambda p: p.update(person_ref={'pid': 1}),
            lambda p: p.update(subject_ref={'subject_id': 'work'}),
            lambda p: p['association'].update(candidate_person_refs=[{'pid': 1}]),
            lambda p: p['association'].update(person_status='unresolved'),
            lambda p: p['association'].update(subject_status='unresolved'),
            lambda p: p['parts']['head'].update(reason='pose_unavailable'),
            lambda p: p['parts']['head'].update(status='supplied'),
            lambda p: p['parts'].pop('head'),
            lambda p: p['parts'].update(extra=deepcopy(p['parts']['head'])),
            lambda p: p['head']['key'].update(source_id='other'),
            lambda p: p['association']['method_ref'].update(id='other'),
            lambda p: p.update(reason='source_lost'),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                p, scope, registration = self.fixture()
                mutate(p)
                self.assertFalse(_capture_metadata(p, identity=scope, registration=registration))

    def test_foreign_scope_and_registration_remain_required_geometry(self):
        for key in ('site_id', 'room_id', 'session_id', 'domain_epoch', 'registration'):
            with self.subTest(binding=key):
                p, scope, registration = self.fixture()
                p[key] = {'registration_ref': 'other'} if key == 'registration' else 'other'
                self.assertFalse(_capture_metadata(p, identity=scope, registration=registration))


if __name__ == '__main__':
    unittest.main()
