"""Actual existing-API factory originals through authenticated Home admission.

Camera/model I/O and independent qualification are explicitly authored. This
does not qualify hidden anatomy, room geometry or a real household trial.
"""
from copy import deepcopy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_lab_stereo_desk_runtime as fixture
from lab_live_runtime import _desk_archive_binding


class ExistingApiDeskRuntimeTests(unittest.TestCase):
    @staticmethod
    def make_source():
        class ExistingApiSetup(fixture.LabStereoDeskRuntimeTests):
            PRODUCER_VERSION = 5
            ORIGINAL_FAMILY = 'existing-api-assessment-1'

            def make_camera_fixture(owner):
                return fixture.generated.GeneratedStereoFixture(owner,
                    original_family=owner.ORIGINAL_FAMILY)
        return ExistingApiSetup()

    def setUp(self):
        self.source = self.make_source()
        self.addCleanup(self.source.doCleanups)
        self.source.setUp()

    def test_actual_factory_originals_publish_geometry_and_seal_without_paired_alias(self):
        source = self.source
        graph = source.create()
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.config['version'], 5)
        body = result['desk']['event']['payload']
        self.assertEqual(body['schema'], 'home-live-geometry-1')
        self.assertEqual(body['status'], 'supplied')
        self.assertEqual(len(body['observation_roots']), 2)
        self.assertIsNone(result['desk']['hazard_clearance'])
        rows = source.f.store.list_media(source.scope['session_id'])['items']
        self.assertFalse(any(row['metadata'].get('stream') == 'paired_preview_original' for row in rows))
        path = Path(source.producer['original_archive']['filename'])
        envelopes = [json.loads(line)['body'] for line in path.read_text().splitlines()]
        originals = [json.loads(row['original_json']) for row in envelopes if row['kind'] == 'original']
        self.assertEqual([row['schema'] for row in originals],
            ['home-original-lab-observation-1', 'home-original-lab-stereo-nose-2', 'home-original-lab-desk-3'])
        support = originals[1]['support']
        self.assertEqual(support['version'], 2)
        self.assertEqual(support['original_family'], 'existing-api-assessment-1')
        self.assertEqual(set(support['media_refs']),
            {'rgb', 'bundle', 'calibration_original', 'assessment_association', 'mapped_depth'})
        self.assertEqual(support['assessment_originals']['terminal']['payload']['status'], 'retained')
        expected = {source.original_module.original_digest(row) for row in originals}
        refs, binding = source.media.media_refs(), deepcopy(source.media.binding)
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        final = graph.finalize(drain_receipt=source.drain(withdrawal))
        self.assertEqual(final['status'], 'stopped', final)
        receipt = final['originals']['stereo_media']
        sealed = source.media_module.SealedStereoMedia(source.path / 'stereo-media',
            expected_binding=binding, expected_refs=refs, manifest_digest=receipt['manifest_digest'])
        values = source.original_module.read_lab_originals(path, expected_digests=expected,
            expected_desk_binding=_desk_archive_binding(source.scope, source.policy, source.composition.join.binding_digest),
            stereo_media_resolver=sealed.resolve_media)
        self.assertEqual(set(values), expected)

    def test_missing_independent_qualification_supplies_no_operational_geometry(self):
        source = self.source
        source.anchor.resolve_qualification = lambda _: None
        graph = source.create()
        result = graph.step()
        self.assertEqual(result['status'], 'desk_unavailable', result)
        self.assertFalse(result['operational_geometry'])
        self.assertFalse(any(row['payload'].get('source_id') == 'surveyed-desk'
                             and row['payload'].get('status') == 'supplied'
                             for row in source.publications()))

    def test_late_actual_terminal_keeps_capture_cursor_and_original_clocks_until_admission(self):
        from preview_client import LocalBundleClient
        import live_capture_fixtures as capture
        import hashlib
        source, seen = self.source, {}
        graph = source.create()
        self.assertEqual(graph.step()['status'], 'admitted_desk')
        observed = capture.NOW + 1
        source.f.now = observed + .2
        jpeg, header, bundle, calibration = capture.inputs(sequence=8,
            observed_ns=int(observed * 1000000000),
            preview_sha256=hashlib.sha256(bytes(540 * 640)).hexdigest())
        def read(client, path):
            if path == '/frame.npz':
                return bundle, ''
            self.assertEqual(path, '/calibration')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                outputs = [row for row in source.f.store.list_events(source.scope['session_id'])
                           if row['kind'] == 'perception.output' and row['payload']['arm'] == 'b'
                           and row['payload']['observed_at'] == observed]
                if outputs:
                    break
                time.sleep(.005)
            self.assertTrue(outputs, 'bounded external model double must settle')
            first = graph.step()
            self.assertEqual(first['status'], 'desk_pending', first)
            cursor = graph.after_id
            original = deepcopy(graph.pending_desk['descriptor'])
            self.assertFalse(first['operational_geometry'])
            second = graph.step()
            self.assertEqual(second['status'], 'desk_pending', second)
            self.assertEqual(graph.after_id, cursor)
            self.assertEqual(graph.pending_desk['descriptor'], original)
            self.assertEqual(original['original']['event'], outputs[-1])
            seen.update(original=original, cursor=cursor)
            source.f.now += .05
            return json.dumps(calibration, indent=2).encode(), ''
        with patch.object(LocalBundleClient, '_read', autospec=True, side_effect=read):
            source.f.runtime.preview_pumps[source.scope['session_id']].consume(jpeg, header)
        admitted = graph.step()
        self.assertEqual(admitted['status'], 'admitted_desk', admitted)
        self.assertEqual(graph.after_id, seen['original']['original']['event']['id'])
        self.assertEqual(admitted['desk']['event']['payload']['observed_at'], observed)
        self.assertEqual(admitted['desk']['event']['payload']['clock']['original_value'],
                         str(seen['original']['frame_metadata']['source_timing_receipt']['observed_at_unix_ns']))


if __name__ == '__main__':
    unittest.main()
