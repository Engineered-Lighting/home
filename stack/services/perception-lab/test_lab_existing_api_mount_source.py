"""Protected source5 composition over actual existing-API factory originals.

External camera/model/lifecycle and independent qualification are explicitly
authored. Only the protected-file reader is doubled here. The factory Store,
assessment tap, metric lift, Journal admission and original retention are real;
this test makes no household qualification or physical-lighting claim.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lab_stereo_mount_source as mounted_source
import test_lab_existing_api_desk_runtime as source_fixture
import test_lab_stereo_mount as mount_fixture
import test_lab_stereo_session_package as package_fixture
from paired_preview_client import encoded, sha


class ExistingApiMountedSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = source_fixture.ExistingApiDeskRuntimeTests.make_source()
        self.addCleanup(self.source.doCleanups)
        self.source.setUp()
        self.inputs = package_fixture.LabStereoSessionPackageTests.inputs.__func__(
            SimpleNamespace(source=self.source))
        original = self.inputs['stereo']
        self.qualified = {key: deepcopy(original[key]) for key in
            ('stereo_policy', 'depth_clock_policy', 'authority_binding', 'association_originals')}
        self.authority = mount_fixture.AuthoredQualificationAuthority(
            self.source, self.qualified['authority_binding'])
        paths = self.inputs['provisioning']
        policy = self.source.producer['desk_policy']
        self.config = dict(schema='home-lab-stereo-source-config-2', version=2,
            commissioning_file=paths['commissioning_file'],
            commissioning_digest=sha(encoded(self.source.commissioning)),
            qualification_file=paths['qualification_file'],
            qualification_digest=sha(encoded(self.qualified)),
            qualification_authority_ref={
                'id': self.qualified['authority_binding']['association_method_ref']['id'],
                'digest': sha(encoded(self.qualified['authority_binding']))},
            media=deepcopy(original['media']),
            original_family=policy['original_family'],
            assessment_policy_sha256=policy['assessment_policy_sha256'])
        self.records = {paths['commissioning_file']: self.source.commissioning,
                        paths['qualification_file']: self.qualified}

    def read_record(self, filename, *, maximum):
        value = self.records[str(filename)]
        self.assertLessEqual(len(encoded(value)), maximum)
        return deepcopy(value)

    def test_source5_composition_consumes_actual_existing_api_originals(self):
        source, original_runtime = self.source, self.source.f.runtime
        with patch.object(mounted_source, 'read_private_record', side_effect=self.read_record):
            composition = mounted_source.stereo_runtime_composition(source.producer, self.config,
                store=source.f.store, preflight=lambda: True, control_clock=source.control_clock,
                qualification_authority=self.authority, incumbent_runtime=original_runtime,
                clock=lambda: source.f.now)
        self.assertIsNone(composition.graph)
        self.assertIs(composition.store, source.f.store)
        capability = composition.desk_capability
        provider = capability.anchor_source.existing_api_source
        self.assertIs(provider.tap, original_runtime.existing_api_assessment)
        self.assertIs(provider.store, source.f.store)
        self.assertIs(provider.depth_clock.store, source.f.store)
        self.assertEqual(provider.depth_clock.clock, self.qualified['depth_clock_policy'])
        self.assertEqual(provider.depth_clock.calibration, source.commissioning['projection'])
        self.assertEqual(capability.media_archive.binding, {
            'scope': source.scope, 'lab_binding_digest': composition.join.binding_digest,
            'media_generation': source.f.session['generation'], 'camera_id': 'den',
            'original_family': self.config['original_family'],
            'assessment_policy_sha256': self.config['assessment_policy_sha256']})
        # Both authored external clock ports must advance together. Real
        # target composition retains its real monotonic default unchanged.
        composition.monotonic = source.composition.monotonic
        # Reuse the original fixture's retention-before-admission assertions
        # and terminal cleanup, with this ACTUAL protected composition.
        source.composition = composition
        source.capability, source.anchor = capability, capability.anchor_source
        source.media = capability.media_archive
        graph = source.create()
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.config['version'], 5)
        self.assertEqual(result['desk']['event']['payload']['status'], 'supplied')
        self.assertTrue(self.authority.resolve_calls)
        self.assertIs(source.f.runtime, original_runtime)
        self.assertFalse(any(row['metadata'].get('stream') == 'paired_preview_original'
            for row in source.f.store.list_media(source.scope['session_id'])['items']))

    def test_missing_tap_changed_policy_or_missing_authority_refuses_before_files(self):
        source, incumbent = self.source, self.source.f.runtime
        tap = incumbent.existing_api_assessment

        def compose(authority):
            with patch.object(mounted_source, 'read_private_record', side_effect=self.read_record):
                return mounted_source.stereo_runtime_composition(source.producer, self.config,
                    store=source.f.store, preflight=lambda: True, control_clock=source.control_clock,
                    qualification_authority=authority, incumbent_runtime=incumbent,
                    clock=lambda: source.f.now)

        def no_source_files():
            for filename in (source.producer['journal_path'], source.producer['original_archive']['filename'],
                             self.config['media']['directory']):
                self.assertFalse(Path(filename).exists(), filename)

        with self.subTest(refusal='typed_runtime_without_tap'):
            del incumbent.existing_api_assessment
            try:
                with self.assertRaises(ValueError):
                    compose(self.authority)
            finally:
                incumbent.existing_api_assessment = tap
            no_source_files()
        with self.subTest(refusal='changed_actual_tap_policy'):
            changed = deepcopy(tap.policy)
            changed['max_captures'] += 1
            with patch.object(tap, 'policy', changed), self.assertRaises(ValueError):
                compose(self.authority)
            no_source_files()
        with self.subTest(refusal='missing_independent_authority'):
            with self.assertRaises(ValueError):
                compose(None)
            no_source_files()


if __name__ == '__main__':
    unittest.main()
