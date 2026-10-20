"""Actual config3/source5 originals -> inert explicit package successor.

Camera/model transports and independent qualification are authored. Preparing
this package does not install an association authority or qualify real geometry.
"""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT/'stack/services/home-context', ROOT/'stack/services/perception-lab'):
    if str(directory) not in sys.path: sys.path.append(str(directory))

import lab_stereo_session_package as package
from journal import digest
from paired_preview_client import encoded, sha
from lab_live_runtime import _assessment_binding, _join_and_configuration
import test_lab_existing_api_desk_runtime as existing_fixture
import test_lab_stereo_session_package as package_fixture


class LabExistingApiSessionPackageTests(unittest.TestCase):
    def inputs(self):
        source = existing_fixture.ExistingApiDeskRuntimeTests.make_source()
        self.addCleanup(source.doCleanups)
        source.setUp()
        value = package_fixture.LabStereoSessionPackageTests.inputs.__func__(SimpleNamespace(source=source))
        value.update(schema='home-den-lab-session-inputs-3', version=3)
        value['stereo'].update(schema='home-den-stereo-package-inputs-2', version=2)
        return source, value

    def test_actual_config3_source5_prepares_exact_inert_mount4_without_paired_alias(self):
        source, value = self.inputs()
        before = deepcopy(value)
        fail = lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError('prepare_attempted_effect'))
        with patch('store.Store', side_effect=fail), patch('subprocess.run', side_effect=fail), \
                patch('subprocess.Popen', side_effect=fail), patch('threading.Thread.start', side_effect=fail), \
                patch('socket.socket', side_effect=fail), patch('lab_live_desk_anchor.LabLiveDeskAnchor', side_effect=fail):
            result = package.prepare_stereo_session_package(value, now=source.f.now)
        self.assertEqual(result['status'], 'prepared', result['missing_dependencies'])
        self.assertEqual((result['schema'], result['version']), ('home-den-lab-session-package-3', 3))
        self.assertEqual(value, before)
        self.assertFalse(result['activation_ready'])
        self.assertFalse(result['capability_constructed'])
        mount = result['files']['mount.json']
        self.assertEqual((mount['schema'], mount['version']), ('home-managed-den-mount-4', 4))
        self.assertEqual((mount['producer']['version'], mount['producer']['desk_policy']['version']), (5, 3))
        self.assertEqual(result['files']['bootstrap.json']['schema'], 'home-den-lab-bootstrap-3')
        selected = mount['stereo_source']
        self.assertEqual((selected['schema'], selected['version']), ('home-lab-stereo-source-config-2', 2))
        self.assertEqual(selected['original_family'], 'existing-api-assessment-1')
        self.assertEqual(selected['assessment_policy_sha256'], source.f.runtime.existing_api_assessment.policy_sha256)
        join, graph = _join_and_configuration(value['producer'], value['session'])
        expected = _assessment_binding(join.scope, value['producer']['desk_policy'], join.binding_digest)
        media_binding = result['bindings']['media_binding']
        self.assertEqual(media_binding, dict(scope=expected['scope'], lab_binding_digest=expected['lab_binding_digest'],
            media_generation=value['producer']['lab_binding']['media_generation'], camera_id='den',
            original_family='existing-api-assessment-1', assessment_policy_sha256=expected['assessment_policy_sha256']))
        self.assertEqual(result['bindings']['media_binding_digest'], sha(encoded(media_binding)))
        self.assertEqual(result['bindings']['graph_configuration_digest'], digest(graph))
        self.assertEqual(result['files']['lab-session.json'], value['session'])
        self.assertEqual(result['files']['stereo-commissioning.json'], value['stereo']['commissioning'])
        self.assertEqual(result['files']['stereo-qualification.json']['authority_binding'], value['stereo']['authority_binding'])
        self.assertIn('concrete_exact_capture_association_authority_not_installed', result['activation_blockers'])
        self.assertIn('target_compatible_command_owner_not_installed', result['activation_blockers'])
        self.assertIsNone(value['command_capture'])
        rows = source.f.store.list_media(source.scope['session_id'])['items']
        self.assertFalse(any(row['metadata'].get('stream') == 'paired_preview_original' for row in rows))

    def test_crossed_profiles_family_policy_shape_and_caller_command_owner_refuse(self):
        source, original = self.inputs()
        attacks = (
            ('producer', lambda v: v['producer'].update(schema='home-incumbent-lab-producer-4', version=4)),
            ('outer', lambda v: v.update(schema='home-den-lab-session-inputs-2', version=2)),
            ('stereo', lambda v: v['stereo'].update(schema='home-den-stereo-package-inputs-1', version=1)),
            ('family', lambda v: v['producer']['desk_policy'].update(original_family='paired-preview-1')),
            ('digest_shape', lambda v: v['producer']['desk_policy'].update(assessment_policy_sha256='f'*63)),
            ('caller_command', lambda v: v.update(command_capture={'status':'qualified'})),
        )
        for name, mutate in attacks:
            with self.subTest(attack=name):
                value = deepcopy(original)
                mutate(value)
                result = package.prepare_stereo_session_package(value, now=source.f.now)
                self.assertEqual(result['status'], 'refused', result['missing_dependencies'])
                self.assertFalse(result['activation_ready'])
                self.assertFalse(result['capability_constructed'])
                self.assertEqual(result['files'], {})
                self.assertTrue(result['missing_dependencies'])


if __name__ == '__main__': unittest.main()
