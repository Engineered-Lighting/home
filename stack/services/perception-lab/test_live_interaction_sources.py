"""Existing LA receipt reuse: generated words-only inputs and invocation-owned files."""
from copy import deepcopy
import json
import tempfile
import unittest

import la_fact_log
import qwen_scene as scene
from journal import digest as wire_digest
from live_interaction_sources import ExistingReceiptSources


NOW = 1800000000
MODEL = {'id': 'synthetic-qwen-model', 'digest': 'a' * 64}
SESSION = '5e' * 16
FACTS = {'people_visible': 'one', 'subject_posture': 'sitting', 'held_object': 'book',
         'book_state': 'open', 'gaze_target': 'book', 'typing_visible': 'no',
         'screen_facing_subject': 'no', 'activity_hypothesis': 'reading',
         'visible_basis': ['book_in_hands', 'book_pages_open'], 'change_vs_prior': 'no_prior'}


def context(sequence):
    observed = NOW + sequence
    frame = {'session': 'camera-epoch', 'timing_generation': 3, 'sequence': sequence,
             'observed_at_unix_ns': observed * 10**9, 'jpeg_sha256': format(sequence, '064x')}
    roots = ['rgb:' + str(sequence), 'depth:' + str(sequence)]
    payload = {'site_id': 'site', 'room_id': 'room', 'session_id': 'local-session', 'domain_epoch': 'domain',
               'source_id': 'camera-source', 'source_generation': 1,
               'head': {'position': {'capture_epoch': 'camera-epoch', 'capture_seq': sequence, 'revision': 0}},
               'observed_at': observed, 'available_at': observed + .1, 'valid_until': observed + 3,
               'sensor_roots': ['camera:physical'], 'observation_roots': roots,
               'observation_root_times': {root: observed for root in roots},
               'sensor_root_bindings': {root: ['camera:physical'] for root in roots},
               'original_receipt_refs': [{'store_id': 'capture-store', 'receipt_id': root, 'component_id': name}
                                         for root, name in zip(roots, ['rgb', 'depth'])]}
    original = {'component_id': 'rgb', 'capture_epoch': 'camera-epoch', 'capture_seq': sequence,
                'observed_at': observed, 'available_at': observed + .1,
                'sensor_roots': ['camera:physical'], 'rgb_receipt': frame}
    cap = {'id': 'capture:' + str(sequence), 'observed_at': observed, 'available_at': observed + .1,
           'effective_valid_until': observed + 3, 'source_generation': 1}
    return {'capture': cap, 'capture_payload': payload, 'original_receipts': [original],
            'registration': {'camera_id': 'camera'}}


def accepted(contexts, available, *, index=1, site_id='site', camera_id='camera',
             session_id=SESSION, artifact_sha256=MODEL['digest'], engine_epoch='la-engine', facts=None):
    frames = []
    for value in contexts:
        original = value['original_receipts'][0]
        rgb = original['rgb_receipt']
        frames.append({'media_id': 'ring-' + rgb['jpeg_sha256'],
                       'frame_id': f"{rgb['session']}-{rgb['timing_generation']}-{rgb['sequence']}",
                       'capture_epoch': rgb['session'], 'observed_at': original['observed_at']})
    reference = {'request_id': scene.digest({'synthetic-request': index}), 'session_id': session_id,
                 'site_id': site_id, 'body_sha256': 'b' * 64, 'prompt_version': scene.PROMPT_VERSION}
    req = {'reference': reference, 'frames': frames, 'sent_at': frames[-1]['observed_at'] + .2,
           'profile': {'engine_epoch': engine_epoch}}
    draft = scene.build_observation(site_id=site_id, camera_id=camera_id, session_id=session_id,
        request_id=reference['request_id'], frames=frames, generated_at=available - .1,
        prompt_version=scene.PROMPT_VERSION, context={}, body_sha256='b' * 64,
        engine_epoch=engine_epoch, artifact_sha256=artifact_sha256, content=json.dumps(FACTS if facts is None else facts),
        finish_reason='stop', prior_supplied=False)
    return req, scene.first_receipt(draft, available)


def spatial_context(value, *, track='track-1', pid=1):
    value = deepcopy(value)
    payload = deepcopy(value['capture_payload'])
    person = {**{key: payload[key] for key in ('site_id', 'room_id', 'session_id', 'domain_epoch')},
              'track_epoch': track, 'pid': pid}
    payload.update(schema='home-live-geometry-1', status='supplied', person_ref=person,
                   association={'person_status': 'qualified', 'candidate_person_refs': []})
    event = {'id': 'spatial:' + value['capture']['id'], 'producer_id': 'producer', 'payload': payload}
    ack = {'status': 'accepted', 'id': event['id'], 'producer_id': 'producer',
           'payload_digest': wire_digest(payload), 'committed_at': payload['available_at']}
    value['spatial'] = {'status': 'supplied', 'publication': event, 'receipt': ack}
    return value


class ExistingSourcesTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW + 2.5
        self.enabled = True
        self.engine = {'engine_epoch': 'la-engine', 'artifact_sha256': MODEL['digest'], 'session_id': SESSION}
        self.values = [context(1), context(2)]
        self.native = {row['capture']['id']: deepcopy(row) for row in self.values}
        self.sources = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=MODEL,
            artifact_sha256=MODEL['digest'],
            granted=lambda: self.enabled, identity=lambda: deepcopy(self.engine), clock=lambda: self.now)
        self.sources.bind_capture(lambda key: deepcopy(self.native[key]), self.receipt_record)
        self.addCleanup(self.sources.clear)

    def receipt_record(self, store, receipt):
        for native in self.native.values():
            payload = native['capture_payload']
            refs = [ref for ref in payload['original_receipt_refs'] if ref['store_id'] == store and ref['receipt_id'] == receipt]
            if refs:
                root = receipt
                return {'ref': deepcopy(refs[0]), 'capture_epoch': payload['head']['position']['capture_epoch'],
                        'capture_seq': payload['head']['position']['capture_seq'],
                        'observation_root_times': {root: payload['observation_root_times'][root]},
                        'sensor_root_bindings': {root: payload['sensor_root_bindings'][root]}}
        raise KeyError(receipt)

    def seed(self):
        self.sources.read(self.values[0])
        self.sources.read(self.values[1])
        req, receipt = accepted(self.values, self.now)
        self.assertEqual(self.sources.observe_la(req, receipt), 'accepted')
        return req, receipt

    def test_fresh_receipt_maps_all_media_roots_to_original_camera_without_claiming_use(self):
        _, receipt = self.seed()
        value = self.sources.read(self.values[-1])
        self.assertEqual(value['la']['status'], 'fresh_proposal')
        self.assertEqual(value['la']['receipt'], receipt)
        self.assertEqual(value['la']['model_ref'], MODEL)
        self.assertEqual(value['la']['probability'], None)
        self.assertEqual(value['la']['sensor_roots'], ['camera:physical'])
        self.assertEqual(set(value['la']['observation_roots']), {'rgb:1', 'rgb:2'})
        self.assertEqual(value['la']['available_at'], receipt['first_received_at'])
        self.assertEqual(value['pc']['reason'], 'qualified_person_device_attribution_unavailable')
        self.assertNotIn('object_interaction', value['la'])

    def test_retry_keeps_first_availability_and_changed_identity_refuses(self):
        req, receipt = self.seed()
        retry = deepcopy(receipt)
        retry['available_at'] += .1
        retry['first_received_at'] += .1
        self.assertEqual(self.sources.observe_la(req, retry), 'duplicate')
        self.assertEqual(self.sources.read(self.values[-1])['la']['available_at'], receipt['available_at'])
        changed = deepcopy(receipt)
        changed['capture_epoch'] = 'different-camera-epoch'
        self.assertEqual(self.sources.observe_la(req, changed), 'refused')

    def test_no_bare_media_root_mapping_or_guessed_frame_alias(self):
        req, receipt = accepted(self.values, self.now)
        self.sources.observe_la(req, receipt)
        value = self.sources.read(self.values[-1])
        self.assertEqual(value['la']['status'], 'unavailable')
        self.assertEqual(value['la']['reason'], 'la_frame_unmapped')
        forged = deepcopy(self.values[0])
        forged['original_receipts'][0]['rgb_receipt']['jpeg_sha256'] = 'f' * 64
        self.assertEqual(self.sources.read(forged)['la']['reason'], 'capture_binding_unavailable')

    def test_expiry_is_half_open_and_old_receipt_cannot_refresh(self):
        _, receipt = self.seed()
        self.now = receipt['valid_until']
        self.assertEqual(self.sources.read(self.values[-1])['la']['status'], 'unavailable')

    def test_la_grant_identity_backoff_and_factlog_unavailable_do_not_raise_or_block_local_binding(self):
        self.seed()
        for identity in (None, {}, {'reason': 'backoff'}, {'reason': 'fact_log_unwritable'},
                         {**self.engine, 'engine_epoch': 'new-engine'}):
            self.engine = identity
            value = self.sources.read(self.values[-1])
            self.assertEqual(value['la']['status'], 'unavailable')
        self.enabled = False
        self.assertEqual(self.sources.read(self.values[-1])['la']['reason'], 'la_grant_unavailable')

    def test_bare_label_receipts_are_proposals_never_calibrated_probabilities(self):
        _, receipt = self.seed()
        self.assertEqual(receipt['facts']['activity_hypothesis'], 'reading')
        value = self.sources.read(self.values[-1])['la']
        self.assertEqual(value['qualification'], 'uncalibrated_model_proposal')
        self.assertEqual(value['truth_role'], 'runtime_model_proposal')
        self.assertEqual(value['person_association'], 'unresolved')

    def test_explicit_owner_binding_is_required_and_cannot_be_replaced(self):
        with self.assertRaises(ValueError):
            self.sources.bind_capture(lambda key: {}, self.receipt_record)
        unbound = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=MODEL,
            artifact_sha256=MODEL['digest'],
            granted=lambda: True, identity=lambda: self.engine, clock=lambda: self.now)
        self.addCleanup(unbound.clear)
        self.assertEqual(unbound.read(self.values[-1])['la']['reason'], 'capture_binding_unavailable')

    def test_mismatched_artifact_prompt_session_camera_and_original_times_refuse(self):
        req, receipt = accepted(self.values, self.now)
        for key, value in [('artifact_sha256', 'c' * 64), ('prompt_version', 'other-prompt'),
                           ('session_id', 'bad-session'), ('camera_id', 'other-camera'),
                           ('observed_at', NOW + 3), ('fingerprint', 'd' * 64)]:
            with self.subTest(key=key):
                changed = deepcopy(receipt)
                changed[key] = value
                self.assertEqual(self.sources.observe_la(req, changed), 'refused')

    def test_frame_capacity_refuses_instead_of_eviction_and_clear_is_terminal(self):
        source = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=MODEL,
            artifact_sha256=MODEL['digest'],
            granted=lambda: True, identity=lambda: self.engine, clock=lambda: self.now, max_receipts=2)
        source.bind_capture(lambda key: deepcopy(self.native[key]), self.receipt_record)
        for row in self.values:
            source.read(row)
        third = context(3)
        self.native[third['capture']['id']] = third
        self.now = NOW + 3.5
        self.assertEqual(source.read(third)['la']['reason'], 'supplemental_capacity_exhausted')
        self.assertTrue(source.clear())
        self.assertEqual(source.read(self.values[-1])['la']['reason'], 'optional_sources_closed')

    def test_configuration_digest_and_artifact_pin_remain_distinct(self):
        other_model = {'id': MODEL['id'], 'digest': 'f' * 64}
        source = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=other_model,
            artifact_sha256=MODEL['digest'], granted=lambda: True,
            identity=lambda: self.engine, clock=lambda: self.now)
        source.bind_capture(lambda key: deepcopy(self.native[key]), self.receipt_record)
        self.addCleanup(source.clear)
        for row in self.values:
            source.read(row)
        req, receipt = accepted(self.values, self.now)
        self.assertEqual(source.observe_la(req, receipt), 'accepted')
        self.assertEqual(source.read(self.values[-1])['la']['model_ref'], other_model)

    def test_supplemental_request_cannot_carry_images_or_unbounded_opaque_fields(self):
        req, receipt = accepted(self.values, self.now)
        req['envelope'] = {'body': {'image_base64': 'not-an-image'}}
        self.assertEqual(self.sources.observe_la(req, receipt), 'refused')
        del req['envelope']
        req['frames'][0]['data'] = b'not-an-image'
        self.assertEqual(self.sources.observe_la(req, receipt), 'refused')

    def test_native_receipt_root_mismatch_never_synthesizes_a_root_mapping(self):
        self.sources.clear()
        source = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=MODEL,
            artifact_sha256=MODEL['digest'], granted=lambda: True, identity=lambda: self.engine,
            clock=lambda: self.now)
        source.bind_capture(lambda key: deepcopy(self.native[key]), lambda store, receipt:
                            {**self.receipt_record(store, receipt), 'sensor_root_bindings': {'invented': ['other-camera']}})
        self.addCleanup(source.clear)
        self.assertEqual(source.read(self.values[-1])['la']['reason'], 'capture_binding_unavailable')

    def test_la_revocation_discards_old_receipts_without_revoking_capture_authority(self):
        self.seed()
        self.enabled = False
        self.sources.read(self.values[-1])
        self.enabled = True
        value = self.sources.read(self.values[-1])
        self.assertEqual(value['la']['reason'], 'la_receipt_unavailable')
        self.assertEqual(self.native[self.values[-1]['capture']['id']]['capture'], self.values[-1]['capture'])

    def test_runtime_authority_reader_exception_is_only_optional_absence(self):
        self.seed()
        self.sources.identity = lambda: (_ for _ in ()).throw(OSError('synthetic LA unavailable'))
        self.assertEqual(self.sources.read(self.values[-1])['la']['reason'], 'la_identity_unavailable')

    def test_person_association_requires_every_exact_spatial_frame_to_have_same_full_identity(self):
        values = [spatial_context(row) for row in self.values]
        for row in values:
            self.sources.read(row)
        req, receipt = accepted(values, self.now)
        self.sources.observe_la(req, receipt)
        result = self.sources.read(values[-1])['la']
        self.assertEqual(result['person_association'], 'qualified')
        self.assertEqual(result['person_ref'], values[-1]['spatial']['publication']['payload']['person_ref'])

    def test_changed_person_track_or_bad_spatial_receipt_keeps_qwen_proposal_unresolved(self):
        for mode in ('changed_track', 'bad_ack'):
            with self.subTest(mode=mode):
                source = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=MODEL,
                    artifact_sha256=MODEL['digest'], granted=lambda: True,
                    identity=lambda: self.engine, clock=lambda: self.now)
                self.addCleanup(source.clear)
                source.bind_capture(lambda key: deepcopy(self.native[key]), self.receipt_record)
                values = [spatial_context(self.values[0]), spatial_context(self.values[1],
                            track='track-2' if mode == 'changed_track' else 'track-1')]
                if mode == 'bad_ack':
                    values[0]['spatial']['receipt']['payload_digest'] = 'f' * 64
                for row in values:
                    source.read(row)
                req, receipt = accepted(values, self.now)
                source.observe_la(req, receipt)
                self.assertEqual(source.read(values[-1])['la']['person_association'], 'unresolved')

    def test_same_capture_competing_la_interpretations_remain_conflicting_receipts(self):
        self.seed()
        facts = deepcopy(FACTS)
        facts.update(held_object='phone', book_state='not_visible', activity_hypothesis='phone_use',
                     gaze_target='phone', visible_basis=['phone_in_hands', 'phone_screen_toward_face'])
        request, receipt = accepted(self.values, self.now, index=2, facts=facts)
        self.assertEqual(self.sources.observe_la(request, receipt), 'accepted')
        result = self.sources.read(self.values[-1])['la']
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['reason'], 'conflicting_receipts')
        self.assertEqual({row['facts']['activity_hypothesis'] for row in result['alternatives']}, {'reading', 'phone_use'})
        self.assertEqual(result['alternative_count'], 2)
        self.assertTrue(result['alternatives_complete'])


class FactLogObserverTests(unittest.TestCase):
    def test_successful_existing_terminal_receipt_reaches_subscriber_once_and_exceptions_are_isolated(self):
        values = [context(1), context(2)]
        req, receipt = accepted(values, NOW + 2.5)
        with tempfile.TemporaryDirectory() as directory:
            log = la_fact_log.FactLog(directory, private=False, clock=lambda: NOW + 2.5)
            seen = []
            log.subscribe_receipts(lambda request, value: seen.append((deepcopy(request), deepcopy(value))))
            log.subscribe_receipts(lambda request, value: (_ for _ in ()).throw(ValueError('observer failure')))
            draft = {k: v for k, v in receipt.items() if k not in {'available_at', 'first_received_at', 'fingerprint'}}
            try:
                self.assertEqual(log.terminal(req, 'completed', draft)['state'], 'available')
                self.assertEqual(log.terminal(req, 'completed', draft)['state'], 'duplicate')
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0][1], receipt)
                self.assertEqual(set(seen[0][0]), {'reference', 'frames', 'sent_at', 'profile'})
                self.assertNotIn('envelope', seen[0][0])
            finally:
                log.close()

    def test_attach_and_clear_remove_only_own_callback(self):
        values = [context(1), context(2)]
        req, receipt = accepted(values, NOW + 2.5)
        with tempfile.TemporaryDirectory() as directory:
            log = la_fact_log.FactLog(directory, private=False, clock=lambda: NOW + 2.5)
            source = ExistingReceiptSources(site_id='site', camera_id='camera', model_ref=MODEL,
                artifact_sha256=MODEL['digest'],
                granted=lambda: True, identity=lambda: {'engine_epoch': 'la-engine',
                'artifact_sha256': MODEL['digest'], 'session_id': SESSION}, clock=lambda: NOW + 2.5)
            source.attach(log)
            source.clear()
            draft = {k: v for k, v in receipt.items() if k not in {'available_at', 'first_received_at', 'fingerprint'}}
            try:
                self.assertEqual(log.terminal(req, 'completed', draft)['state'], 'available')
                self.assertEqual(source.read(values[-1])['la']['reason'], 'optional_sources_closed')
            finally:
                log.close()


if __name__ == '__main__':
    unittest.main()
