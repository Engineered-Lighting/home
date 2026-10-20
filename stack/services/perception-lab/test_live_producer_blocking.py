"""Adversarial producer regressions; authored bytes and loopback journal only."""
import base64
from copy import deepcopy
from decimal import Decimal
import json
import unittest

import live_capture_fixtures as capture
from frame_ring import preview_frame
from live_interaction_sources import ExistingReceiptSources
import test_live_capture as capture_tests
import test_live_interaction_sources as source_tests
import test_live_spatial as spatial_tests
import test_live_person_lifecycle_factory as lifecycle_tests


class CaptureResolutionTests(unittest.TestCase):
    setUp = capture_tests.LiveCaptureTests.setUp
    tearDown = capture_tests.LiveCaptureTests.tearDown
    publish = capture_tests.LiveCaptureTests.publish
    snapshot = capture_tests.LiveCaptureTests.snapshot
    control_clock = capture_tests.LiveCaptureTests.control_clock
    controlled_adapter = capture_tests.LiveCaptureTests.controlled_adapter
    history = capture_tests.LiveCaptureTests.history

    def test_real_resolution_stamp_has_one_correct_rounding_and_next_frame_progresses(self):
        stamp = 1800000000002000000
        inputs = capture.inputs(observed_ns=stamp)
        expected = float(Decimal(stamp) / Decimal(1_000_000_000))
        self.assertEqual(preview_frame(inputs[0], inputs[1], self.now)['observed_at'], expected)
        result = self.adapter.offer(*inputs, received_at=capture.NOW + .1)
        self.assertEqual(result['status'], 'qualified', result)
        self.assertEqual(result['publication']['payload']['observed_at'], expected)
        self.now += 1
        later = capture.inputs(sequence=8, observed_ns=stamp + 1_000_000_000)
        result = self.adapter.offer(*later, received_at=capture.NOW + 1.1)
        self.assertEqual(result['status'], 'qualified', result)

    def test_resolution_sweep_matches_exact_integer_mapping(self):
        jpeg = capture.jpeg()
        for offset in range(0, 1_000_000, 997):
            stamp = 1800000000000000000 + offset
            value = preview_frame(jpeg, capture.header(jpeg, observed_ns=stamp), self.now)
            self.assertEqual(value['observed_at'], float(Decimal(stamp) / Decimal(1_000_000_000)))

    def test_definitive_refusal_is_not_pending_and_can_withdraw(self):
        from live_publication import PublicationRefusal
        def publisher(event):
            if event['payload']['status'] != 'withdrawn':
                raise PublicationRefusal('journal_full', http_status=507)
            return self.publish(event)
        self.controlled_adapter(publisher)
        refused = self.adapter.offer(*capture.inputs(), received_at=capture.NOW + .1)
        self.assertEqual(refused['publication_outcome'], 'terminal_refusal')
        self.assertEqual(refused['reason'], 'journal_full')
        self.assertIsNone(self.adapter.pending)
        self.assertEqual(self.adapter.source_gap()['withdrawal'], 'recorded')
        self.assertEqual(self.adapter.offer(*capture.inputs(), received_at=capture.NOW + .1), refused)

    def test_definitive_barrier_cas_refusal_is_visible_and_not_pending(self):
        from live_publication import PublicationRefusal
        def publisher(event):
            raise PublicationRefusal('source_generation_conflict', http_status=409)
        self.controlled_adapter(publisher)
        result = self.adapter.source_gap()
        self.assertEqual(result['withdrawal'], 'not_recorded')
        self.assertEqual(result['publication_outcome'], 'terminal_refusal')
        self.assertEqual(result['withdrawal_reason'], 'source_generation_conflict')
        self.assertIsNone(self.adapter.pending_barrier)

    def test_new_barrier_reads_room_authority_but_lost_ack_keeps_exact_retry(self):
        generation = {'value': 2}
        sent = []
        def publisher(event):
            sent.append(deepcopy(event))
            raise TimeoutError('authored ACK lost')
        self.controlled_adapter(publisher, authority_generation=lambda: generation['value'])
        self.adapter.source_gap()
        self.assertEqual(sent[-1]['payload']['control']['expected_authority_generation'], 2)
        generation['value'] = 3
        self.now += .1
        self.adapter.source_gap()
        self.assertEqual(sent[-1], sent[0])


class SupplementalCapacityTests(unittest.TestCase):
    setUp = source_tests.ExistingSourcesTests.setUp
    receipt_record = source_tests.ExistingSourcesTests.receipt_record

    def test_expired_receipts_release_live_capacity_without_retry_refresh(self):
        self.sources.capacity = 2
        request1, receipt1 = source_tests.accepted(self.values, self.now, index=1)
        request2, receipt2 = source_tests.accepted(self.values, self.now, index=2)
        self.assertEqual(self.sources.observe_la(request1, receipt1), 'accepted')
        self.assertEqual(self.sources.observe_la(request2, receipt2), 'accepted')
        self.now = receipt1['valid_until']
        self.assertEqual(self.sources.observe_la(request1, receipt1), 'duplicate')
        values = [source_tests.context(15), source_tests.context(16)]
        self.now = source_tests.NOW + 16.5
        request3, receipt3 = source_tests.accepted(values, self.now, index=3)
        self.assertEqual(self.sources.observe_la(request3, receipt3), 'accepted')
        self.assertEqual(len(self.sources.receipts), 1)
        self.assertNotIn(receipt1['request_id'], self.sources.receipts)
        self.assertEqual(self.sources.observe_la(request1, receipt1), 'refused')

    def test_already_expired_initial_receipt_is_never_cached(self):
        request, receipt = source_tests.accepted(self.values, self.now)
        self.now = receipt['valid_until']
        self.assertEqual(self.sources.observe_la(request, receipt), 'refused')
        self.assertEqual(self.sources.receipts, {})


class PublicationFactoryTests(unittest.TestCase):
    setUp = spatial_tests.acquisition_tests.ProductionAcquisitionTests.setUp
    camera_pair = spatial_tests.acquisition_tests.ProductionAcquisitionTests.camera_pair
    control_clock = spatial_tests.acquisition_tests.ProductionAcquisitionTests.control_clock
    construct = spatial_tests.acquisition_tests.ProductionAcquisitionTests.construct
    snapshot = spatial_tests.acquisition_tests.ProductionAcquisitionTests.snapshot

    def test_actual_journal_budget_refusal_is_terminal_and_source_withdraws(self):
        from journal import digest
        self.live_config['manifest']['limits']['max_original_roots'] = 2
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'observed')
        self.now += 1
        self.mono += 1
        self.sequence += 1
        result = graph.step()
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['reason'], 'journal_full')
        self.assertIsNone(graph.capture.pending)
        self.assertIsNone(graph.capture.pending_barrier)
        self.assertEqual(graph.capture.barrier['publication']['payload']['status'], 'withdrawn')
        self.assertEqual(self.snapshot()['items'], [])


class SpatialPairingTests(unittest.TestCase):
    setUp = spatial_tests.LiveSpatialTests.setUp
    control_clock = spatial_tests.LiveSpatialTests.control_clock
    snapshot = spatial_tests.LiveSpatialTests.snapshot
    prepare = spatial_tests.LiveSpatialTests.prepare
    construct = spatial_tests.LiveSpatialTests.construct
    current_spatial = spatial_tests.LiveSpatialTests.current_spatial
    repin_spatial_policy = spatial_tests.LiveSpatialTests.repin_spatial_policy

    def camera_pair(self, token, *, timeout):
        pair = json.loads(spatial_tests.LiveSpatialTests.camera_pair(self, token, timeout=timeout))
        if getattr(self, 'skew', None) is not None:
            metadata = capture.frame_metadata(sequence=self.sequence, depth_sequence=41,
                depth_device_s=50.2-self.skew, rgb_device_s=50.2)
            pair['bundle_b64'] = base64.b64encode(capture.bundle(metadata)).decode()
        return json.dumps(pair).encode()

    def test_earlier_depth_bounds_each_part_horizon(self):
        self.prepare()
        result = self.construct().step()
        self.assertEqual(result['status'], 'observed', result)
        payload = self.current_spatial()[0]
        for name, part in payload['parts'].items():
            if part['status'] == 'supplied':
                self.assertLessEqual(part['valid_until'], capture.NOW - .01 + 1, name)

    def test_insufficient_pairing_motion_budget_withholds_operational_geometry(self):
        self.prepare()
        self.skew = .0275
        source = self.live_config['manifest']['sources'][capture.SOURCE]
        for name, policy in source['part_policies'].items():
            if policy is not None and not name.endswith('_forward'):
                policy['components_m']['capture_association'] = .001
                policy['bound_at_observation_m'] = sum(policy['components_m'].values())
        from journal import digest
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
        result = self.construct().step()
        self.assertNotEqual(result['spatial'], 'not_run', result)
        payloads = self.current_spatial()
        self.assertFalse(any(part['status'] == 'supplied' for payload in payloads for part in payload['parts'].values()))

    def test_raw_second_person_below_pose_threshold_cannot_disappear_from_hazards(self):
        self.prepare()
        self.boxes.append([20, 30, 200, 350])
        from test_live_acquisition_models import Tensor
        predict = self.detector.model.predict
        def lower_second(**kwargs):
            values = predict(**kwargs)
            values[0].boxes.conf = Tensor([.9, .4])
            return values
        self.detector.model.predict = lower_second
        result = self.construct().step()
        self.assertNotEqual(result.get('spatial'), 'not_run', result)
        self.assertFalse(any(payload['association']['person_status'] == 'qualified'
                             for payload in self.current_spatial()))


class LifecyclePairingTests(unittest.TestCase):
    setUp = lifecycle_tests.PersonLifecycleFactoryTests.setUp
    control_clock = lifecycle_tests.PersonLifecycleFactoryTests.control_clock
    snapshot = lifecycle_tests.PersonLifecycleFactoryTests.snapshot
    prepare = lifecycle_tests.PersonLifecycleFactoryTests.prepare
    construct = lifecycle_tests.PersonLifecycleFactoryTests.construct
    advance = lifecycle_tests.PersonLifecycleFactoryTests.advance
    geometries = lifecycle_tests.PersonLifecycleFactoryTests.geometries
    lifecycle_rows = lifecycle_tests.PersonLifecycleFactoryTests.lifecycle_rows

    def camera_pair(self, token, *, timeout):
        pair = json.loads(lifecycle_tests.PersonLifecycleFactoryTests.camera_pair(self, token, timeout=timeout))
        if getattr(self, 'skew', None) is not None:
            offset = self.sequence - 7
            metadata = capture.frame_metadata(sequence=self.sequence, depth_sequence=41+offset,
                depth_device_s=50.2+offset-self.skew, rgb_device_s=50.2+offset)
            pair['bundle_b64'] = base64.b64encode(capture.bundle(metadata)).decode()
        return json.dumps(pair).encode()

    def test_qualified_continuity_cannot_bypass_insufficient_pairing_radius(self):
        self.prepare(qualified_methods=True)
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.advance()
        self.skew = .0278
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        sequence = self.sequence
        current = [row for row in self.lifecycle_rows() if row['head']['position']['capture_seq'] == sequence]
        self.assertTrue(current)
        self.assertFalse(any(row['disposition'] == 'continuity_qualified' for row in current))
        self.assertTrue(any(row['disposition'] == 'observation_gap' for row in current))

    def test_filtered_extra_occupant_creates_separate_unattributed_lifecycle_hazard(self):
        self.prepare(qualified_methods=True)
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        previous = deepcopy(self.geometries()[0]['person_ref'])
        self.boxes.append([20, 30, 200, 350])
        self.materials.clear()
        from test_live_acquisition_models import Tensor
        predict = self.detector.model.predict
        def lower_second(**kwargs):
            values = predict(**kwargs)
            values[0].boxes.conf = Tensor([.9, .4])
            return values
        self.detector.model.predict = lower_second
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        resets = [row for row in self.lifecycle_rows() if row['disposition'] == 'association_reset']
        self.assertTrue(any(row['target_person_ref'] == previous for row in resets))
        self.assertTrue(any(row['target_person_ref'] is None for row in resets))


if __name__ == '__main__':
    unittest.main()
