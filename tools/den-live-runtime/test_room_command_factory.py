"""Pure fixed-declaration tests; no native, host, camera or qualification."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from room_command_factory import (FACTORY_REF, SIMULATION_PROFILE, SIMULATION_PROFILE_REF,
    SOURCE_INVENTORY, RoomCommandFactory, validate_command_declaration)
from room_canonical_provider import WINDOWS_PROVIDER, LINUX_ARM64_PROVIDER


def authored_declaration():
    """Syntax data only: these fake source hashes cannot install a factory."""
    directory = Path(__file__).resolve().parent
    return dict(schema='home-den-canonical-command-declaration-1', version=1,
        factory_ref=deepcopy(FACTORY_REF), simulation_profile_ref=deepcopy(SIMULATION_PROFILE_REF),
        provider_pins=deepcopy(WINDOWS_PROVIDER.provider_pins), source_bundle_digest='a' * 64,
        source_pins={p: 'b' * 64 for p in SOURCE_INVENTORY},
        recording=dict(native_file=str(directory / 'authored-native.jsonl'),
            canonical_file=str(directory / 'authored-canonical.jsonl'), provenance_file=str(directory / 'authored-provenance.json'),
            max_records=8192, max_bytes=32 * 1024**2, min_free_bytes=1),
        limits=dict(max_runtime_s=120, interval_s=.02, max_commits=256))


class RoomCommandFactoryDeclarationTests(unittest.TestCase):
    def test_declaration_is_data_only_and_does_not_select_platform(self):
        original = authored_declaration()
        result = validate_command_declaration(original, source_bundle_digest='a' * 64)
        self.assertEqual(result, original)
        result['limits']['max_commits'] = 1
        self.assertEqual(original['limits']['max_commits'], 256)
        original['provider_pins'] = deepcopy(LINUX_ARM64_PROVIDER.provider_pins)
        self.assertEqual(validate_command_declaration(original, source_bundle_digest='a' * 64)['provider_pins'],
                         LINUX_ARM64_PROVIDER.provider_pins)
        with self.assertRaises(ValueError):
            validate_command_declaration(original, source_bundle_digest='a' * 64,
                expected_provider_pins=WINDOWS_PROVIDER.provider_pins)

    def test_no_json_callback_or_arbitrary_import_executable_admission(self):
        for key in ('command_factory', 'module', 'executable', 'transport', 'clock'):
            value = authored_declaration(); value[key] = 'owner-supplied'
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_command_declaration(value, source_bundle_digest='a' * 64)

    def test_full_source_bundle_and_profile_are_mandatory(self):
        changes = (
            lambda v: v['source_pins'].pop(SOURCE_INVENTORY[0]),
            lambda v: v['source_pins'].update({'other.py': 'b' * 64}),
            lambda v: v.update(source_bundle_digest='c' * 64),
            lambda v: v['factory_ref'].update(digest='c' * 64),
            lambda v: v['simulation_profile_ref'].update(digest='c' * 64),
            lambda v: v['provider_pins'].update(executable_sha256='c' * 64),
        )
        for change in changes:
            value = authored_declaration(); change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_command_declaration(value, source_bundle_digest='a' * 64)

    def test_recording_and_runtime_caps_are_not_widened(self):
        changes = (
            lambda v: v['recording'].pop('provenance_file'),
            lambda v: v['recording'].update(provenance_file=v['recording']['native_file']),
            lambda v: v['recording'].update(native_file='relative.jsonl'),
            lambda v: v['recording'].update(max_records=8193),
            lambda v: v['recording'].update(max_bytes=32 * 1024**2 + 1),
            lambda v: v['limits'].update(max_runtime_s=121),
            lambda v: v['limits'].update(max_runtime_s=float('nan')),
            lambda v: v['limits'].update(interval_s=float('inf')),
            lambda v: v['limits'].update(max_commits=257),
        )
        for change in changes:
            value = authored_declaration(); change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_command_declaration(value, source_bundle_digest='a' * 64)

    def test_mapping_does_not_claim_real_calibration_or_feedback(self):
        self.assertEqual(SIMULATION_PROFILE['wire_pins'], dict(zip(
            ('fixture', 'firmware', 'protocol', 'profile', 'calibration', 'contract'),
            [format(i, '02x') * 32 for i in range(1, 7)])))
        self.assertFalse(SIMULATION_PROFILE['physical_permission'])
        self.assertFalse(SIMULATION_PROFILE['physical_truth'])
        self.assertEqual(SIMULATION_PROFILE['evidence_source'], 'simulated')

    def test_json_namespace_is_not_a_runtime_composition(self):
        with self.assertRaisesRegex(ValueError, 'actual_source5'):
            RoomCommandFactory(authored_declaration(), composition=SimpleNamespace(producer={'version': 5}))


if __name__ == '__main__':
    unittest.main()
