"""Authored original-shaped desk inputs through actual Journal admission.

No household data, host, camera, model, service or physical writer is accessed.
All asserted independent assessments here are fictional test commissioning.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'home-context'))
sys.path.insert(0, str(ROOT / 'tools/den-assessment'))
from journal import Journal, JournalError, digest
from live_capture import _original_digest
from live_test_helpers import NOW, PROFILE, SCOPE, TOKEN, publication_ref
import test_lab_live_journal_join as historical
import live_capture_fixtures as capture_fixtures
from lab_live_desk_admission import LabLiveDeskAdmission, DeskAdmissionRefusal, RECORD_KEYS

spec = importlib.util.spec_from_file_location('authored_distinct_desk_fixtures',
    ROOT / 'tools/den-assessment/test_desk_distinct_parts.py')
distinct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distinct)


class LabLiveDeskAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.base = historical.LabLiveJournalJoinTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.journal.close()
        self.now = NOW + .5
        self.config = self.base.config
        capture_source = self.config['manifest']['sources']['synthetic-source']
        _, cp, _, _, _ = capture_fixtures.setup()
        registration, projection = deepcopy(cp['registration']), deepcopy(cp['projection'])
        registration['camera_id'] = projection['camera_id'] = 'den'
        capture_source['registration'] = {
            **deepcopy(capture_source['registration']), 'registration_ref': registration['id'],
            'registration_digest': digest(registration), 'geometry_revision': registration['geometry_revision'],
            'geometry_digest': registration['geometry_digest'], 'mount_digest': digest(cp['mount_signature']),
            'calibration_digest': projection['id'], 'coverage_refs': registration['coverage_refs'],
            'transform_chain_refs': [registration['transform_ref']]}
        self.definition = {'schema': 'home-desk-protection-definition-1', 'version': 1,
            'id': 'desk-protection', 'required_body_part_ids': ['torso'], 'protected_parts': ['head'],
            'body_primitive_id': 'torso', 'protection_primitive_id': 'head-protection'}
        protection_ref = {'id': self.definition['id'], 'digest': digest(self.definition)}
        candidate, assessment, anchor, binding = distinct.inputs()
        candidate['original_survey']['protection_policy']['policy_ref'] = deepcopy(protection_ref)
        candidate['survey_sha256'] = distinct.consumer.digest(candidate['original_survey'])
        candidate['protection_policy_ref'] = deepcopy(protection_ref)
        candidate['digest'] = distinct.consumer.digest({k: v for k, v in candidate.items() if k != 'digest'})
        person = {**SCOPE, 'pid': 1, 'track_epoch': 'independently-assessed-track'}
        for value in (assessment, anchor, binding):
            value['scope'], value['person_ref'] = deepcopy(SCOPE), deepcopy(person)
        clock = capture_source['capture_clock']
        registration_ref = {'id': registration['id'], 'digest': digest(registration)}
        clock_ref = {'id': clock['mapping_id'], 'digest': clock['mapping_digest']}
        assessment.update(candidate_digest=candidate['digest'], protection_policy_ref=protection_ref,
                          observed_at=NOW-10, available_at=NOW-9, valid_until=NOW+100)
        assessment['registration'].update(ref=registration_ref, error_bound_m=.02, valid_until=NOW+100)
        assessment['clock_mapping'].update(ref=clock_ref, valid_until=NOW+100)
        anchor.update(observed_at=NOW, available_at=NOW+.25, valid_until=NOW+2)
        anchor['source_position'] = {'capture_epoch': capture_source['capture_epoch'], 'capture_seq': 7, 'revision': 0}
        anchor['clock'].update(mapping_id=clock['mapping_id'], mapping_digest=clock['mapping_digest'],
            source_epoch=capture_source['capture_epoch'], original_value=str(NOW*1000000000), uncertainty_s=.0011)
        binding.update(candidate_digest=candidate['digest'], registration_ref=registration_ref,
            clock_mapping_ref=clock_ref, source_position=deepcopy(anchor['source_position']))
        binding['anchor_original_ref'].update(digest=distinct.consumer.digest(anchor), observed_at=NOW,
            available_at=NOW+.25, clock_mapping_id=clock['mapping_id'])
        binding['assessment_ref']['digest'] = distinct.consumer.digest(assessment)
        self.policy = {'schema': 'home-lab-live-desk-admission-policy-1', 'version': 1,
            'source_id': 'surveyed-desk', 'capture_source_id': 'synthetic-source',
            'method_ref': binding['method_ref'], 'association_method_ref': binding['association_method_ref'],
            'candidate_digest': candidate['digest'], 'assessment_ref': binding['assessment_ref'],
            'projection_ref': {'id': projection['id'], 'digest': digest(projection)},
            'protection_policy_ref': protection_ref, 'receipt_store_id': 'desk-originals',
            'receipt_component_id': 'desk-assessment-original', 'body_primitive_id': 'torso',
            'protection_primitive_id': 'head-protection'}
        desk = deepcopy(capture_source)
        desk.update(method_refs=[binding['method_ref']], association_method_refs=[binding['association_method_ref']],
            model_refs=[], receipt_store_ids=['desk-originals'], receipt_component_ids=['desk-assessment-original'],
            primitive_ids={'body': ['torso'], 'protection': ['head-protection']},
            protection_policy_refs=[protection_ref])
        for name in ('body', 'head', 'hand_left', 'hand_right', 'protection'):
            desk['part_policies'][name] = deepcopy(desk['part_policies']['work'])
        self.config['manifest']['sources']['surveyed-desk'] = desk
        self.base.repin()
        self.grant = deepcopy(self.base.grant)
        self.grant['source_ids'].append('surveyed-desk')
        self.grant['live_bindings'].append({'manifest_ref': deepcopy(self.config['manifest_ref']),
            'source_id': 'surveyed-desk', 'capabilities': ['capture', 'control', 'read']})
        self.lab = self.base.make_join()
        self.values = {'candidate': candidate, 'assessment': assessment, 'anchor': anchor, 'binding': binding,
            'capture_ref': None, 'capture_original': None, 'capture_record': None,
            'projection': projection, 'registration': registration,
            'clock_mapping': deepcopy(clock), 'protection_policy': deepcopy(self.definition)}
        self.anchor_ref = deepcopy(binding['anchor_original_ref'])
        self.retained, self.retain_hook = [], None
        self.producer = self.make_producer()
        self.journal = Journal(Path(self.base.work.name) / 'desk-journal.sqlite', grants=[self.grant],
            profile=PROFILE, live_config=self.config, live_receipt_resolver=self.resolve,
            clock=lambda: self.now)
        self.addCleanup(self.journal.close)
        self.principal = self.journal.authenticate(TOKEN)
        self.publish_capture()
        self.hazards = [{'id': 'prior-epoch-person', 'status': 'unresolved'}]

    def make_producer(self):
        return LabLiveDeskAdmission(self.config, policy=self.policy, revision='synthetic-r1',
            resolve_inputs=lambda ref: deepcopy(self.values) if ref == self.anchor_ref else None,
            original_retainer=self.retain, clock=lambda: self.now)

    def retain(self, original):
        # Earlier observations may already be committed. This exact observation
        # must still be absent until its own original has been durably retained.
        rows = [json.loads(row[0]) for row in self.journal.db.execute('SELECT body FROM publications')]
        self.assertFalse(any(row['payload'].get('source_id') == 'surveyed-desk'
            and row['payload']['head']['position'] == original['inputs']['anchor']['source_position'] for row in rows))
        self.retained.append(deepcopy(original))
        if self.retain_hook:
            return self.retain_hook(original)
        return {'status': 'retained', 'digest': _original_digest(original, limit=512*1024)}

    def resolve(self, store, receipt):
        return (self.producer if store == 'desk-originals' else self.lab).resolve_receipt(store, receipt)

    def publish_capture(self, *, seq=7, event_id=12, observed=NOW):
        row, frame = self.base.inputs(event_id, observed=observed, seq=seq)
        saved = self.lab.publish(row, frame_metadata=frame, received_at=NOW+.2,
            journal=self.journal, grant=self.principal)
        ref = publication_ref(saved['receipts'][0])
        # Get the exact original ref from the just-committed event, not a fake DB row.
        original_id = next(key for key, value in self.lab.originals.items() if value['event']['id'] == row['id'])
        self.values['capture_ref'] = ref
        self.values['capture_original'] = self.lab.original_receipt('synthetic-receipts', original_id)
        self.values['capture_record'] = self.lab.resolve_receipt('synthetic-receipts', original_id)
        self.values['binding']['parent_publication_refs'] = [deepcopy(ref)]
        return saved

    def consume(self, **overrides):
        return self.producer.consume(self.anchor_ref, received_at=self.now,
            journal=overrides.get('journal', self.journal), grant=overrides.get('grant', self.principal),
            hazards=self.hazards)

    def unavailable(self, reason=None):
        result = self.consume()
        self.assertEqual(result['status'], 'unavailable', result)
        if reason:
            self.assertEqual(result['reason'], reason)
        self.assertIsNone(result['receipt'])
        self.assertEqual(result['inherited_hazards'], self.hazards)
        self.assertIsNone(result['hazard_clearance'])

    def repin_anchor(self):
        self.values['binding']['anchor_original_ref']['digest'] = distinct.consumer.digest(self.values['anchor'])
        self.anchor_ref = deepcopy(self.values['binding']['anchor_original_ref'])

    def advance(self, *, seq, event_id, observed):
        self.publish_capture(seq=seq, event_id=event_id, observed=observed)
        anchor = self.values['anchor']
        anchor.update(id='anchor-' + str(seq), observed_at=observed, valid_until=observed+2)
        anchor['source_position']['capture_seq'] = seq
        anchor['clock']['original_value'] = str(self.values['capture_original']['frame_metadata']['source_timing_receipt']['observed_at_unix_ns'])
        self.values['binding']['source_position'] = deepcopy(anchor['source_position'])
        self.values['binding']['anchor_original_ref'].update(receipt_id=anchor['id'], observed_at=observed)
        self.repin_anchor()

    def test_actual_authenticated_append_distinct_geometry_and_original_durability(self):
        result = self.consume()
        self.assertEqual(result['status'], 'admitted', result)
        self.assertEqual(result['receipt']['cursor'], 3)
        body = result['event']['payload']
        self.assertEqual(body['parent_refs'], [])
        self.assertEqual(body['status'], 'supplied')
        self.assertEqual(body['parts']['body']['shape']['items'][0]['part_id'], 'torso')
        self.assertNotEqual(body['parts']['head']['shape'], body['parts']['hand_left']['shape'])
        self.assertEqual(body['parts']['body_forward']['status'], 'unobservable')
        self.assertEqual(body['parts']['head_forward']['status'], 'unobservable')
        self.assertEqual(body['parts']['protection']['shape']['policy_ref'], self.policy['protection_policy_ref'])
        self.assertEqual(body['observed_at'], NOW)
        self.assertEqual(body['available_at'], NOW+.5)
        self.assertLess(body['valid_until'], NOW+2)
        ref = body['original_receipt_refs'][0]
        record = self.producer.resolve_receipt(ref['store_id'], ref['receipt_id'])
        self.assertEqual(set(record), RECORD_KEYS)
        self.assertEqual(self.producer.original_receipt(ref['store_id'], ref['receipt_id']), self.retained[0])
        self.assertEqual(result['inherited_hazards'], self.hazards)
        self.assertIsNone(result['hazard_clearance'])

    def test_exact_retry_after_expiry_never_refreshes(self):
        first = self.consume()
        self.assertEqual(first['status'], 'admitted', first)
        self.now += 20
        self.assertEqual(self.consume(), first)
        self.assertEqual(len(self.retained), 1)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 3)

    def test_historical_retry_does_not_rewind_head_before_next_actual_append(self):
        ref7 = deepcopy(self.anchor_ref)
        first = self.consume()
        self.assertEqual(first['status'], 'admitted', first)
        self.advance(seq=8, event_id=13, observed=NOW+.05)
        second = self.consume()
        self.assertEqual(second['status'], 'admitted', second)
        ref8 = deepcopy(self.anchor_ref)
        self.anchor_ref = ref7
        historical_retry = self.consume()
        self.assertEqual(historical_retry, first)
        self.anchor_ref = ref8
        self.advance(seq=9, event_id=14, observed=NOW+.1)
        third = self.consume()
        self.assertEqual(third['status'], 'admitted', third)
        self.assertEqual(third['event']['payload']['head']['supersedes'], publication_ref(second['receipt']))
        self.assertEqual(third['receipt']['cursor'], 9)

    def test_retry_preserves_current_additional_hazards_without_mutating_event(self):
        first = self.consume()
        self.assertEqual(first['status'], 'admitted', first)
        self.hazards.append({'id': 'another-unresolved-person', 'status': 'unresolved'})
        retried = self.consume()
        self.assertEqual(retried['event'], first['event'])
        self.assertEqual(retried['receipt'], first['receipt'])
        self.assertEqual(retried['inherited_hazards'], self.hazards)
        self.assertIsNone(retried['hazard_clearance'])
        self.assertEqual(len(first['inherited_hazards']), 1)

    def test_slow_resolver_stamps_actual_production_after_resolution(self):
        original_resolve = self.producer.resolve_inputs
        def slow(ref):
            self.now = max(self.now, NOW+1)
            return original_resolve(ref)
        self.producer.resolve_inputs = slow
        result = self.consume()
        self.assertEqual(result['status'], 'admitted', result)
        self.assertEqual(result['event']['available_at'], NOW+1)
        self.assertEqual(result['event']['observed_at'], NOW)
        self.assertEqual(self.retained[0]['produced_at'], NOW+1)
        self.assertEqual(self.retained[0]['first_consumer_received_at'], NOW+.5)

    def test_resolver_consuming_lease_refuses_before_retention(self):
        original_resolve = self.producer.resolve_inputs
        def slow(ref):
            # The anchor lease ends <NOW+2; the capture still lasts nearly 3s.
            # Isolate consumed geometry lease from independent capture expiry.
            self.now += 1.6
            return original_resolve(ref)
        self.producer.resolve_inputs = slow
        self.unavailable('desk_support_intersection_expired')
        self.assertFalse(self.retained)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_post_retainer_resolver_consuming_lease_never_attempts_first_append(self):
        original_resolve = self.producer.resolve_inputs
        calls, appends = [], []
        def slow_second(ref):
            calls.append(deepcopy(ref))
            if len(calls) == 2:
                self.now += 1.6
            return original_resolve(ref)
        def append(event, grant):
            appends.append(deepcopy(event))
            self.fail('expired original must refuse before first append attempt')
        self.producer.resolve_inputs = slow_second
        self.journal.append_event = append
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'anchor_not_current'):
            self.consume()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.retained), 1)
        self.assertFalse(appends)
        frozen = next(iter(self.producer.prepared.values()))
        self.assertEqual(frozen['event']['available_at'], NOW+.5)
        self.assertEqual(frozen['original']['first_consumer_received_at'], NOW+.5)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_post_retainer_resolver_quiesce_never_attempts_first_append(self):
        original_resolve = self.producer.resolve_inputs
        calls, appends = [], []
        def stop_second(ref):
            calls.append(deepcopy(ref))
            if len(calls) == 2:
                self.producer.quiesce()
            return original_resolve(ref)
        def append(event, grant):
            appends.append(deepcopy(event))
            self.fail('quiesce during final lookup must prevent first append')
        self.producer.resolve_inputs = stop_second
        self.journal.append_event = append
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'desk_quiesced_before_admission'):
            self.consume()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.retained), 1)
        self.assertFalse(appends)
        self.assertEqual(self.producer.status()['status'], 'unconfirmed')
        self.assertIsNotNone(self.producer.status()['pending'])
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_lost_ack_settles_same_committed_event_after_quiesce(self):
        real = self.journal.append_event
        def lost(event, grant):
            real(event, grant)
            raise TimeoutError('authored lost ACK')
        self.journal.append_event = lost
        with self.assertRaises(TimeoutError):
            self.consume()
        self.assertEqual(self.producer.quiesce()['status'], 'unconfirmed')
        frozen = deepcopy(next(iter(self.producer.prepared.values()))['event'])
        self.journal.append_event = real
        self.now += 20
        result = self.consume()
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['event'], frozen)
        self.assertEqual(self.producer.status()['status'], 'quiescent')

    def test_no_ack_means_no_original_and_no_publication(self):
        self.retain_hook = lambda _: {'status': 'failed'}
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'durable_desk_original_required'):
            self.consume()
        self.assertFalse(self.producer.records)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_quiesce_during_retainer_prevents_first_append(self):
        def stop(original):
            self.producer.quiesce()
            return {'status': 'retained', 'digest': _original_digest(original, limit=512*1024)}
        self.retain_hook = stop
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'desk_quiesced_before_admission'):
            self.consume()
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_expiry_during_retention_prevents_first_append(self):
        def expire(original):
            self.now += 3
            return {'status': 'retained', 'digest': _original_digest(original, limit=512*1024)}
        self.retain_hook = expire
        with self.assertRaises(DeskAdmissionRefusal):
            self.consume()
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_missing_assessment_or_unqualified_null_registration_error_refuses(self):
        self.values['assessment'] = None
        self.unavailable('independent_assessment_unavailable')

    def test_candidate_projection_with_unknown_error_is_not_registration(self):
        self.values['projection']['error_bound_m'] = None
        self.unavailable('qualified_projection_required')

    def test_incomplete_policy_protection_cannot_be_declared_complete(self):
        self.values['protection_policy']['protected_parts'].append('hand_left')
        self.unavailable('complete_protection_definition_required')

    def test_anchor_face_reset_two_people_or_stale_original_refuses_without_clearance(self):
        for state in ('ambiguous', 'reset', 'departed', 'source_lost'):
            self.values['anchor']['state'] = state
            self.repin_anchor()
            self.unavailable('anchor_applicability_unavailable')
        self.values['anchor']['state'] = 'seated_unique'
        self.values['anchor']['occupant_count'] = 2
        self.repin_anchor()
        self.unavailable('unique_person_applicability_unavailable')

    def test_original_clock_double_rounding_or_new_epoch_refuses(self):
        self.values['anchor']['clock']['original_value'] = str(NOW*1000000000+123456789)
        self.repin_anchor()
        self.unavailable('desk_originals_unavailable')

    def test_quiesce_during_resolver_reports_inflight_and_cannot_admit(self):
        called = []
        def resolve(ref):
            called.append(self.producer.quiesce())
            return deepcopy(self.values)
        self.producer.resolve_inputs = resolve
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'desk_quiesced_before_admission'):
            self.consume()
        self.assertTrue(called[0]['in_flight'])
        self.assertEqual(called[0]['status'], 'unconfirmed')
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_new_capture_head_makes_earlier_original_unavailable(self):
        old = deepcopy(self.values)
        self.publish_capture(seq=8, event_id=13, observed=NOW+.05)
        self.values = old
        self.unavailable('capture_head_not_current')

    def test_receipt_resolution_rechecks_original_and_never_extends_metadata(self):
        result = self.consume()
        self.assertEqual(result['status'], 'admitted', result)
        ref = result['event']['payload']['original_receipt_refs'][0]
        self.values['anchor']['point_m'][0] += .01
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'underlying_desk_original_changed'):
            self.producer.resolve_receipt(ref['store_id'], ref['receipt_id'])

    def test_foreign_journal_never_returns_cached_ack(self):
        self.assertEqual(self.consume()['status'], 'admitted')
        other = Journal(Path(self.base.work.name) / 'other.sqlite', grants=[self.grant], profile=PROFILE,
            live_config=self.config, live_receipt_resolver=self.resolve, clock=lambda: self.now)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(DeskAdmissionRefusal, 'desk_journal_authority_changed'):
            self.consume(journal=other)

    def test_actual_journal_authentication_still_required(self):
        bad = deepcopy(self.grant)
        bad['operations'] = ['read']
        from journal import Grant
        principal = Grant.from_dict(bad, profile=PROFILE)
        with self.assertRaises(JournalError):
            self.consume(grant=principal)


if __name__ == '__main__':
    unittest.main()
