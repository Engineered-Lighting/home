"""Independent synthetic challenges at the paired adapter and journal boundary."""
import copy
import tempfile
import unittest
from pathlib import Path

import live_capture_fixtures as fixtures
from journal import Journal, PREFIX, digest
from live_capture import LiveCaptureAdapter
from live_test_helpers import LiveHttp


class LiveCaptureAdversarialTests(unittest.TestCase):
    def system(self, *, max_age_s=3, now=None):
        work = tempfile.TemporaryDirectory(prefix='synthetic-capture-adversarial-')
        self.addCleanup(work.cleanup)
        config, policy, method, calibration, grant = fixtures.setup()
        source = config['manifest']['sources'][fixtures.SOURCE]
        source['max_age_s'] = max_age_s
        config['manifest_ref']['digest'] = digest(config['manifest'])
        grant['live_bindings'][0]['manifest_ref'] = copy.deepcopy(config['manifest_ref'])
        current = fixtures.NOW + .2 if now is None else now
        transport = {}

        def publish(event):
            status, response = transport['server'].request('POST', PREFIX + '/events', event, token=fixtures.TOKEN)
            if status != 200:
                raise AssertionError({'http_status': status, 'response': response})
            return response

        adapter = LiveCaptureAdapter(config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                                     policy=policy, method_ref=method, clock=lambda: current, publish=publish)
        journal = Journal(Path(work.name) / 'synthetic.sqlite', grants=[grant], profile=fixtures.PROFILE,
                          live_config=config, clock=lambda: current,
                          live_receipt_resolver=adapter.resolve_receipt)
        self.addCleanup(journal.close)
        transport['server'] = LiveHttp(journal)
        self.addCleanup(transport['server'].close)
        return adapter, calibration, transport['server']

    def assert_refused_without_current_capture(self, adapter, calibration, server, metadata):
        jpeg = fixtures.jpeg()
        result = adapter.offer(jpeg, fixtures.header(jpeg), fixtures.bundle(metadata), calibration,
                               received_at=fixtures.NOW + .1)
        self.assertEqual(result['status'], 'refused', {key: result.get(key) for key in ('status', 'reason')})
        status, snapshot = server.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room',
                                          token=fixtures.TOKEN)
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(snapshot['items'], [], snapshot)
        return result

    def test_depth_metadata_dimensions_must_match_the_numeric_plane(self):
        adapter, calibration, server = self.system()
        metadata = fixtures.frame_metadata()
        metadata['frames']['depth']['width'] = 320
        metadata['frames']['depth']['height'] = 200
        self.assert_refused_without_current_capture(adapter, calibration, server, metadata)

    def test_rgb_metadata_dimensions_must_match_the_original_jpeg(self):
        adapter, calibration, server = self.system()
        metadata = fixtures.frame_metadata()
        metadata['frames']['rgb']['width'] = 320
        metadata['frames']['rgb']['height'] = 180
        self.assert_refused_without_current_capture(adapter, calibration, server, metadata)

    def test_current_distortion_cannot_replace_the_commissioned_projection(self):
        adapter, calibration, server = self.system()
        metadata = fixtures.frame_metadata()
        metadata['frames']['rgb']['source_distortion_coefficients'][0] = .1
        self.assert_refused_without_current_capture(adapter, calibration, server, metadata)

    def test_metric_deprojection_and_association_must_use_the_same_intrinsics(self):
        config, policy, method, _, _ = fixtures.setup()
        policy['projection']['intrinsics']['fx'] = 900
        method['digest'] = digest(policy)
        source = config['manifest']['sources'][fixtures.SOURCE]
        source['method_refs'] = [copy.deepcopy(method)]
        source['association_method_refs'] = [copy.deepcopy(method)]
        config['manifest_ref']['digest'] = digest(config['manifest'])
        with self.assertRaises(ValueError):
            LiveCaptureAdapter(config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                               policy=policy, method_ref=method, clock=lambda: fixtures.NOW + .2,
                               publish=lambda event: self.fail('invalid policy must refuse before publication'))

    def test_policy_requires_scoped_registration_rigid_axes_and_assessed_projection(self):
        def nonrigid(policy):
            policy['registration']['room_from_rectified_left'][0][0] = 2

        cases = (
            ('wrong_room', lambda policy: policy['registration'].update(room_id='another-synthetic-room')),
            ('nonrigid_room_transform', nonrigid),
            ('missing_semantics_bound', lambda policy: policy['depth_clock'].pop('semantics_bound_s')),
            ('missing_reprojection_receipt', lambda policy: policy['projection']['reprojection'].pop('receipt')),
            ('negative_reprojection_error', lambda policy: policy['projection']['reprojection'].update(p95_px=-1)),
            ('unvalidated_projection', lambda policy: policy['projection'].update(status='fitted')),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                config, policy, method, _, _ = fixtures.setup()
                mutate(policy)
                # Keep content pins internally consistent, so refusals cannot
                # be explained merely by an outdated method/manifest digest.
                registration = policy['registration']
                registration['transform_ref']['digest'] = digest(registration['room_from_rectified_left'])
                source = config['manifest']['sources'][fixtures.SOURCE]
                source['registration']['registration_digest'] = digest(registration)
                source['registration']['transform_chain_refs'] = [copy.deepcopy(registration['transform_ref'])]
                method['digest'] = digest(policy)
                source['method_refs'] = [copy.deepcopy(method)]
                source['association_method_refs'] = [copy.deepcopy(method)]
                config['manifest_ref']['digest'] = digest(config['manifest'])
                with self.assertRaises(ValueError):
                    LiveCaptureAdapter(config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                                       policy=policy, method_ref=method, clock=lambda: fixtures.NOW + .2,
                                       publish=lambda event: self.fail('invalid policy must refuse before publication'))

    def test_depth_component_obeys_source_age_limit_as_well_as_rgb(self):
        # RGB remains inside its 240 ms horizon after clock uncertainty;
        # the older depth component is already outside the same horizon.
        adapter, calibration, server = self.system(max_age_s=.24, now=fixtures.NOW + .21)
        result = self.assert_refused_without_current_capture(adapter, calibration, server, fixtures.frame_metadata())
        self.assertEqual(result['reason'], 'capture_expired_or_uncertain')

    def test_depth_skew_includes_the_commissioned_relative_clock_error(self):
        # Nominal skew is 29 ms against a 30 ms cap. Drift, quantization and
        # the assessed RGB/stereo semantics contribute more than 2 ms; the
        # shared UTC anchor error is irrelevant to this relative interval.
        adapter, _, server = self.system()
        jpeg, receipt, bundle, calibration = fixtures.inputs(depth_delta_s=-.029)
        result = adapter.offer(jpeg, receipt, bundle, calibration, received_at=fixtures.NOW + .1)
        self.assertEqual(result['status'], 'refused', {key: result.get(key) for key in ('status', 'reason')})
        status, snapshot = server.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room',
                                          token=fixtures.TOKEN)
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(snapshot['items'], [], snapshot)

    def test_reusing_one_native_depth_frame_keeps_its_original_receipt_and_root(self):
        adapter, calibration, _ = self.system()
        jpeg = fixtures.jpeg()
        first = adapter.offer(jpeg, fixtures.header(jpeg), fixtures.bundle(), calibration,
                              received_at=fixtures.NOW + .1)
        self.assertEqual(first['status'], 'qualified', first['reason'])
        first_refs = {ref['component_id']: ref for ref in first['publication']['payload']['original_receipt_refs']}
        original_depth = adapter.original_receipt(first_refs['depth']['store_id'], first_refs['depth']['receipt_id'])
        resolved_depth = adapter.resolve_receipt(first_refs['depth']['store_id'], first_refs['depth']['receipt_id'])

        # A second exact RGB frame can share the same slower depth frame.
        metadata = fixtures.frame_metadata(sequence=8, rgb_device_s=50.21, depth_sequence=41, depth_device_s=50.19)
        second = adapter.offer(jpeg, fixtures.header(jpeg, sequence=8, observed_ns=1800000000010000000,
                                                    frame_device_ns=50_210_000_000),
                               fixtures.bundle(metadata), calibration, received_at=fixtures.NOW + .12)
        self.assertEqual(second['status'], 'qualified', second['reason'])
        second_refs = {ref['component_id']: ref for ref in second['publication']['payload']['original_receipt_refs']}
        self.assertNotEqual(second_refs['rgb']['receipt_id'], first_refs['rgb']['receipt_id'])
        self.assertEqual(second_refs['depth'], first_refs['depth'])
        self.assertEqual(adapter.original_receipt(second_refs['depth']['store_id'], second_refs['depth']['receipt_id']), original_depth)
        self.assertEqual(adapter.resolve_receipt(second_refs['depth']['store_id'], second_refs['depth']['receipt_id']), resolved_depth)
        first_payload = first['publication']['payload']
        second_payload = second['publication']['payload']
        self.assertTrue(set(resolved_depth['observation_root_times']) <= set(second_payload['observation_roots']))
        for root, observed in resolved_depth['observation_root_times'].items():
            self.assertEqual(first_payload['observation_root_times'][root], observed)
            self.assertEqual(second_payload['observation_root_times'][root], observed)

    def test_same_native_depth_identity_with_changed_pixels_cannot_be_reintroduced(self):
        adapter, calibration, server = self.system()
        jpeg = fixtures.jpeg()
        first = adapter.offer(jpeg, fixtures.header(jpeg), fixtures.bundle(), calibration,
                              received_at=fixtures.NOW + .1)
        self.assertEqual(first['status'], 'qualified', first['reason'])
        metadata = fixtures.frame_metadata(sequence=8, rgb_device_s=50.21, depth_sequence=41, depth_device_s=50.19)
        changed = adapter.offer(jpeg, fixtures.header(jpeg, sequence=8, observed_ns=1800000000010000000,
                                                     frame_device_ns=50_210_000_000),
                                fixtures.bundle(metadata, value=1240), calibration, received_at=fixtures.NOW + .12)
        self.assertEqual(changed['status'], 'refused', {key: changed.get(key) for key in ('status', 'reason')})
        status, snapshot = server.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room',
                                          token=fixtures.TOKEN)
        self.assertEqual(status, 200, snapshot)
        self.assertEqual([item['cursor'] for item in snapshot['items']], [first['receipt']['cursor']])


if __name__ == '__main__':
    unittest.main()
