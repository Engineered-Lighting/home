"""Inert command declaration on actual authored source5 package originals.

The fixture uses external camera/model/qualification doubles. No preparation
test starts a command factory, native client, worker or operational authority.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT/'stack/services/home-context',ROOT/'stack/services/perception-lab'):
    if str(directory) not in sys.path: sys.path.append(str(directory))

import lab_stereo_session_package as package
import test_lab_existing_api_desk_runtime as source_fixture
import test_lab_stereo_session_package as package_fixture
from room_command_factory import FACTORY_REF,SIMULATION_PROFILE_REF,SOURCE_INVENTORY
from room_canonical_provider import WINDOWS_PROVIDER
from paired_preview_client import encoded,sha


class LabCommandSessionPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=source_fixture.ExistingApiDeskRuntimeTests.make_source()
        cls.addClassCleanup(cls.source.doCleanups)
        cls.source.setUp()
        cls.template=package_fixture.LabStereoSessionPackageTests.inputs.__func__(SimpleNamespace(source=cls.source))
        cls.template.update(schema='home-den-lab-session-inputs-3',version=3)
        cls.template['stereo'].update(schema='home-den-stereo-package-inputs-2',version=2)

    def inputs(self):
        value=deepcopy(self.template); value.update(schema='home-den-lab-session-inputs-4',version=4)
        base=self.source.path
        value['command_capture']=dict(schema='home-den-canonical-command-declaration-1',version=1,
            factory_ref=deepcopy(FACTORY_REF),simulation_profile_ref=deepcopy(SIMULATION_PROFILE_REF),
            provider_pins=WINDOWS_PROVIDER.provider_pins,source_bundle_digest=value['runtime']['source_bundle_digest'],
            source_pins={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_INVENTORY},
            recording=dict(native_file=str(base/'command-native.jsonl'),canonical_file=str(base/'command-canonical.jsonl'),
                provenance_file=str(base/'command-provenance.json'),max_records=8192,max_bytes=32*1024**2,min_free_bytes=1),
            limits=dict(max_runtime_s=5,interval_s=.02,max_commits=8))
        return value

    def test_actual_source5_prepares_inert_package4_mount5_and_pinned_bootstrap4(self):
        value=self.inputs(); original=deepcopy(value)
        def forbidden(*_a,**_kw): raise AssertionError('package_preparation_attempted_command_effect')
        with patch('subprocess.Popen',side_effect=forbidden),patch('subprocess.run',side_effect=forbidden), \
                patch('threading.Thread.start',side_effect=forbidden),patch('socket.socket',side_effect=forbidden), \
                patch('store.Store',side_effect=forbidden),patch('room_command_factory.RoomCommandFactory',side_effect=forbidden), \
                patch('room_canonical_adapter.load_canonical_modules',side_effect=forbidden):
            result=package.prepare_stereo_session_package(value,now=self.source.f.now)
        self.assertEqual(result['status'],'prepared',result['missing_dependencies'])
        self.assertEqual((result['schema'],result['version']),('home-den-lab-session-package-4',4))
        self.assertEqual(value,original)
        mount=result['files']['mount.json']; bootstrap=result['files']['bootstrap.json']
        self.assertEqual((mount['schema'],mount['version']),('home-managed-den-mount-5',5))
        self.assertEqual((bootstrap['schema'],bootstrap['version']),('home-den-lab-bootstrap-4',4))
        self.assertEqual(mount['command_capture'],original['command_capture'])
        self.assertEqual(bootstrap['command_capture_digest'],sha(encoded(original['command_capture'])))
        self.assertEqual(result['bindings']['command_capture_digest'],bootstrap['command_capture_digest'])
        self.assertEqual(mount['producer'],original['producer'])
        self.assertEqual(mount['stereo_source']['version'],2)
        self.assertEqual(mount['runtime']['schema'],'home-live-runtime-config-1')
        self.assertEqual(result['files']['lab-session.json'],original['session'])
        self.assertIn('fixed_command_owner_runtime_ready_required',result['activation_blockers'])
        self.assertIn('concrete_exact_capture_association_authority_not_installed',result['activation_blockers'])
        self.assertFalse(result['activation_ready'])
        self.assertFalse(result['capability_constructed'])
        self.assertFalse(result['physical_authority'])

    def test_changed_factory_path_alias_and_parent_budget_refuse(self):
        attacks=(
            ('factory',lambda v:v['command_capture']['factory_ref'].update(digest='0'*64),
                'fixed_canonical_command_factory_profile_required'),
            # Different literal paths resolve to the same private native file.
            ('path_alias',lambda v:v['command_capture']['recording'].update(
                canonical_file=str(self.source.path/'nested'/'..'/'command-native.jsonl')),
                'distinct_private_state_and_credential_paths_required'),
            ('parent_runtime',lambda v:v['command_capture']['limits'].update(
                max_runtime_s=v['runtime']['max_runtime_s']+1),
                'command_lifecycle_exceeds_parent_runtime'),
        )
        for name,mutate,reason in attacks:
            with self.subTest(attack=name):
                value=self.inputs();mutate(value)
                result=package.prepare_stereo_session_package(value,now=self.source.f.now)
                self.assertEqual(result['status'],'refused',result['missing_dependencies'])
                self.assertEqual(result['missing_dependencies'],[reason])
                self.assertEqual(result['files'],{})
                self.assertFalse(result['activation_ready'])
                self.assertFalse(result['capability_constructed'])

    def test_none_retains_package3_mount4_bootstrap3_without_command_digest(self):
        value=deepcopy(self.template);before=deepcopy(value)
        result=package.prepare_stereo_session_package(value,now=self.source.f.now)
        self.assertEqual(result['status'],'prepared',result['missing_dependencies'])
        self.assertEqual((result['schema'],result['version']),('home-den-lab-session-package-3',3))
        self.assertEqual(result['files']['mount.json']['schema'],'home-managed-den-mount-4')
        self.assertEqual(result['files']['bootstrap.json']['schema'],'home-den-lab-bootstrap-3')
        self.assertNotIn('command_capture',result['files']['mount.json'])
        self.assertNotIn('command_capture_digest',result['files']['bootstrap.json'])
        self.assertNotIn('command_capture_digest',result['bindings'])
        self.assertIn('target_compatible_command_owner_not_installed',result['activation_blockers'])
        self.assertNotIn('fixed_command_owner_runtime_ready_required',result['activation_blockers'])
        self.assertEqual(value,before)
        self.assertEqual(encoded(result),encoded(package.prepare_stereo_session_package(value,now=self.source.f.now)))


if __name__=='__main__': unittest.main()
