"""Fixed export selection/pure SDK tests; no native or network execution.

The Linux filesystem/build conformance gate remains the coordinator's separate
target operation. Platform doubles here confer no runtime qualification.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'stack/services/home-context'))

from fixture_execution_validation import FixtureAuthorityDenied
import room_canonical_provider as provider
import room_canonical_adapter as adapter
import room_command_projection as projection
import test_room_canonical_adapter as adapter_fixture
import test_room_command_projection as projection_fixture


class RoomCanonicalProviderTests(unittest.TestCase):
    def platform(self, system, machine):
        system_patch = patch.object(provider.platform,'system',return_value=system)
        machine_patch = patch.object(provider.platform,'machine',return_value=machine)
        system_patch.start(); machine_patch.start()
        self.addCleanup(system_patch.stop); self.addCleanup(machine_patch.stop)

    def test_fixed_platform_selection_has_no_caller_profile_or_path(self):
        for system,machine,expected in (
            ('Windows','AMD64',provider.WINDOWS_PROVIDER),
            ('Windows','x86_64',provider.WINDOWS_PROVIDER),
            ('Linux','aarch64',provider.LINUX_ARM64_PROVIDER)):
            with self.subTest(system=system,machine=machine), \
                    patch.object(provider.platform,'system',return_value=system), \
                    patch.object(provider.platform,'machine',return_value=machine):
                self.assertIs(provider.selected_provider(),expected)
        with self.assertRaises(TypeError): provider.selected_provider(provider.WINDOWS_PROVIDER)
        with self.assertRaises(FrozenInstanceError): provider.LINUX_ARM64_PROVIDER.name = 'other'

    def test_unsupported_platform_refuses_before_reading_any_export(self):
        for system,machine in (('Linux','x86_64'),('Linux','arm64'),('Darwin','arm64'),('Windows','ARM64')):
            with self.subTest(system=system,machine=machine), \
                    patch.object(provider.platform,'system',return_value=system), \
                    patch.object(provider.platform,'machine',return_value=machine), \
                    patch.object(provider,'_read',side_effect=AssertionError('unexpected original read')):
                with self.assertRaisesRegex(FixtureAuthorityDenied,'unsupported fixed canonical provider platform'):
                    adapter.load_canonical_modules()

    def test_windows_default_reads_exact_originals_and_preserves_pure_types(self):
        self.platform('Windows','AMD64')
        profile,manifest,originals = provider.verified_provider_export()
        self.assertIs(profile,provider.WINDOWS_PROVIDER)
        self.assertEqual(adapter.BUILD,profile.build)
        self.assertEqual(adapter.MANIFEST_SHA256,profile.manifest_sha256)
        self.assertEqual(set(originals),set(provider.MODULE_NAMES))
        for name,(_,raw) in originals.items():
            self.assertEqual(hashlib.sha256(raw).hexdigest(),dict(provider.SOURCE_PINS)[name])
        modules = adapter.load_canonical_modules()
        self.assertIs(adapter.load_canonical_modules(),modules)
        self.assertEqual(modules.provider_pins,profile.provider_pins)
        self.assertEqual(modules.source_pins,dict(provider.SOURCE_PINS))
        self.assertEqual(manifest['config']['file_sha256'],provider.CONFIG_FILE_SHA256)
        self.assertNotEqual(manifest['config']['file_sha256'],profile.provider_pins['configuration_sha256'])

    def test_linux_projection_records_selected_tuple_and_preserves_unknown_spotlight(self):
        self.platform('Linux','aarch64')
        fixed = provider.LINUX_ARM64_PROVIDER
        port = projection.CanonicalProjectionProvider(**fixed.provider_pins,
            mixer_source_sha256=provider.MIXER_SOURCE_SHA256,ambient_mix=projection_fixture.literal_mixer)
        snapshot,policy = projection_fixture.authored_projection_case(task=True)
        result = projection.project_committed_snapshot(snapshot,room_policy=policy,provider=port)
        self.assertEqual(result['provider_pins'],fixed.provider_pins)
        self.assertNotEqual(result['provider_pins'],projection.PROVIDER_PINS)
        self.assertEqual(result['fixtures'][0]['domains'][7]['reason'],'spotlight_semantic_CCT_mapping_unavailable')
        self.assertFalse(result['physical_permission'])
        self.assertFalse(result['native_achieved_truth'])
        for changed in (
            dict(manifest_sha256=provider.WINDOWS_PROVIDER.manifest_sha256),
            dict(executable_sha256=provider.WINDOWS_PROVIDER.executable_sha256),
            dict(source_sha256='f'*64),dict(configuration_sha256='f'*64)):
            with self.subTest(changed=changed),self.assertRaisesRegex(
                    projection.RoomProjectionRefusal,'canonical_projection_provider_changed'):
                projection.project_committed_snapshot(snapshot,room_policy=policy,provider=replace(port,**changed))

    def test_profile_pin_views_are_detached_and_unknown_or_mixed_tuple_refuses(self):
        fixed = provider.LINUX_ARM64_PROVIDER
        pins = fixed.provider_pins
        pins['manifest_sha256'] = provider.WINDOWS_PROVIDER.manifest_sha256
        self.assertFalse(fixed.matches(pins))
        self.assertTrue(fixed.matches(fixed.provider_pins))
        self.assertFalse(fixed.matches(dict(fixed.provider_pins,extra=True)))
        self.assertFalse(fixed.matches({k:'f'*64 for k in provider.PIN_KEYS}))

    def test_cached_loader_revalidates_changed_original_bytes_before_returning_types(self):
        self.platform('Windows','AMD64')
        modules = adapter.load_canonical_modules()
        read = provider._read
        targets = [provider.WINDOWS_PROVIDER.build/provider.WINDOWS_PROVIDER.executable_name,
            provider.WINDOWS_PROVIDER.build/'source'/provider.CONFIG,
            provider.WINDOWS_PROVIDER.build/'source'/provider.HEADER]
        targets += [provider.WINDOWS_PROVIDER.build/'source'/'tools'/(name+'.py')
                    for name in provider.MODULE_NAMES]
        for target in targets:
            def changed(filename,maximum):
                raw = read(filename,maximum)
                return raw[:-1] + bytes((raw[-1]^1,)) if filename == target else raw
            with self.subTest(target=target.name),patch.object(provider,'_read',side_effect=changed):
                with self.assertRaises(FixtureAuthorityDenied): adapter.load_canonical_modules()
        self.assertIs(adapter.load_canonical_modules(),modules)
        mixed = deepcopy(modules.provider_pins)
        mixed['manifest_sha256'] = provider.LINUX_ARM64_PROVIDER.manifest_sha256
        with patch.object(adapter,'_MODULES',replace(modules,provider_pins=mixed)):
            with self.assertRaisesRegex(FixtureAuthorityDenied,'cached canonical provider differs'):
                adapter.load_canonical_modules()

    def test_adapter_rejects_each_mixed_recorder_header_pin_before_exchange(self):
        self.platform('Windows','AMD64')
        fixture = adapter_fixture.AdapterFixture(self)
        for name in ('manifest_sha256','source_sha256','config_sha256','executable_sha256'):
            with self.subTest(name=name),patch.dict(fixture.recorder._header['producer'],{name:'f'*64}):
                with self.assertRaisesRegex(FixtureAuthorityDenied,'actual active room member/provider binding differs'):
                    fixture.connect()
            self.assertEqual(fixture.calls,[])
        fixture.connect()
        self.assertEqual(fixture.calls,[])


if __name__ == '__main__': unittest.main()
