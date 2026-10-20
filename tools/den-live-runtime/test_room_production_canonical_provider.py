"""Production successor contracts; Popen is always replaced, never executed."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/home-context'))
import room_production_canonical_provider as provider
import room_production_canonical_client as client
import room_canonical_provider as historical


def pipes():
    return dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=False, bufsize=0)


class ProductionCanonicalTests(unittest.TestCase):
    def setUp(self):
        self.spawn = patch.object(subprocess, 'Popen', side_effect=AssertionError('native forbidden'))
        self.spawn.start()
        self.addCleanup(self.spawn.stop)

    def test_fixed_explicit_family_and_old_selection_unchanged(self):
        selected = provider.selected_production_provider()
        self.assertEqual(selected.selection, 'production-canonical-1')
        self.assertNotEqual(selected.provider_pins, historical.selected_provider().provider_pins)
        self.assertEqual(historical.WINDOWS_PROVIDER.manifest_sha256,
                         '789533e2af58b4bf1a7f62be75db3ef118c96d8c4336b57cbd3bdd963d2bacf2')
        with self.assertRaises(TypeError): provider.selected_production_provider('caller')
        with self.assertRaises(FrozenInstanceError): selected.selection = 'caller'
        for system, machine in [('Linux', 'aarch64'), ('Windows', 'ARM64'), ('Darwin', 'arm64')]:
            with self.subTest(system=system), patch.object(provider.platform, 'system', return_value=system), \
                    patch.object(provider.platform, 'machine', return_value=machine):
                with self.assertRaisesRegex(ValueError, 'unsupported production canonical platform'):
                    provider.selected_production_provider()

    def test_actual_manifest_full_closures_and_false_flags(self):
        profile, manifest, originals = provider.verified_production_provider_export()
        self.assertEqual(len(manifest['source']['files']), 38)
        self.assertEqual(len(manifest['compiled_runtime_source']), 27)
        self.assertEqual(set(originals), set(provider.MODULE_NAMES))
        self.assertEqual(len(provider.RAW_SOURCE_PINS), 28)
        self.assertTrue(profile.matches(profile.provider_pins))
        self.assertFalse(profile.matches(dict(profile.provider_pins, extra=True)))
        for flag in ['physical_permission', 'physical_truth', 'home_qualified']:
            self.assertIs(manifest[flag], False)
        self.assertEqual(manifest['hardware_operations'], 0)

    def test_every_native_raw_source_and_identity_pin_rechecked_even_cached(self):
        provider.load_production_canonical_modules()
        read = provider._read
        profile, manifest, _ = provider.verified_production_provider_export()
        targets = [profile.build/'manifest.json', profile.build/profile.executable_name,
                   profile.build/'source'/provider.HEADER]
        targets += [profile.build/'source'/name for name in manifest['source']['files']]
        targets += [provider.SOURCE_ROOT/name for name in provider.RAW_SOURCE_PINS]
        for target in targets:
            def changed(path, maximum):
                raw = read(path, maximum)
                return raw[:-1] + bytes([raw[-1] ^ 1]) if path == target else raw
            with self.subTest(target=target), patch.object(provider, '_read', side_effect=changed):
                with self.assertRaises(ValueError): provider.load_production_canonical_modules()

    def test_pure_sdk_exact_isolated_types_and_cache(self):
        loaded = provider.load_production_canonical_modules()
        self.assertIs(provider.load_production_canonical_modules(), loaded)
        self.assertEqual(loaded.source_pins, dict(provider.SOURCE_PINS))
        self.assertEqual(loaded.adapter.authority, loaded.authority)
        self.assertEqual(loaded.session.usb, loaded.usb)
        self.assertTrue(loaded.plan.__name__.startswith('_home_production_sdk_'))
        self.assertNotIn('c12_runtime_authority', sys.modules)
        with patch.object(provider, '_MODULES', replace(loaded, provider_pins={'mixed': True})):
            with self.assertRaisesRegex(ValueError, 'cached production SDK differs'):
                provider.load_production_canonical_modules()

    def test_client_constructor_is_inert_and_has_exact_five_module_closure(self):
        factory = client.ProductionCanonicalClientFactory()
        p = provider.selected_production_provider()
        obj = factory(p.build/'manifest.json', p.manifest_sha256, raw_observer=lambda row: None)
        self.assertIs(type(obj), client.load_production_canonical_client())
        self.assertIsNone(obj._process)
        self.assertFalse(obj.recording_status['complete'])
        self.assertEqual(len(factory.binding()['client_source_pins']), 5)
        self.assertEqual(factory.binding()['selection'], provider.FAMILY)
        self.assertEqual(factory.binding()['launch_policy']['maximum_launches'], 4)
        self.assertNotIn('c12_production_canonical', sys.modules)

    def test_cached_client_revalidates_all_five_originals_before_launch_reservation(self):
        loaded = client.load_production_canonical_client()
        factory = client.ProductionCanonicalClientFactory(); profile = provider.selected_production_provider()
        read = provider._read
        for relative, _ in client.CLIENT_SOURCE_PINS:
            target = provider.SOURCE_ROOT/relative
            def changed(path, maximum):
                raw = read(path, maximum)
                return raw[:-1] + bytes([raw[-1] ^ 1]) if path == target else raw
            with self.subTest(target=target), patch.object(provider, '_read', side_effect=changed):
                with self.assertRaises(ValueError):
                    factory.process_factory([str(profile.build/profile.executable_name)], **pipes())
            self.assertEqual(factory._launches, 0)
        self.assertIs(client.load_production_canonical_client(), loaded)

    def test_failed_isolated_import_cleans_all_private_module_origins(self):
        namespace = '_authored_production_import_failure_'
        originals = {'c12_authored_first': (Path('first.py'), b'value=1\n'),
                     'c12_authored_second': (Path('second.py'), b'import c12_foreign\n')}
        with self.assertRaisesRegex(ValueError, 'dependency outside fixed closure'):
            provider._isolated_modules(originals, tuple(originals), namespace)
        for name in originals: self.assertNotIn(namespace+name, sys.modules)

    def test_constructor_rejects_foreign_path_pin_or_observer(self):
        factory = client.ProductionCanonicalClientFactory(); p = provider.selected_production_provider()
        for path, sha, observer in [(Path('foreign.json'), p.manifest_sha256, lambda x: None),
                (p.build/'manifest.json', 'f'*64, lambda x: None),
                (p.build/'manifest.json', p.manifest_sha256, None)]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                factory(path, sha, raw_observer=observer)

    def test_four_exact_launch_attempts_and_detached_binding(self):
        factory = client.ProductionCanonicalClientFactory(); p = provider.selected_production_provider()
        binding = factory.binding(); binding['provider_pins']['manifest_sha256'] = 'f'*64
        self.assertEqual(factory.binding()['provider_pins'], p.provider_pins)
        argv = [str(p.build/p.executable_name)]
        with patch.object(subprocess, 'Popen', return_value=object()) as spawn:
            for _ in range(4): factory.process_factory(argv, **pipes())
            with self.assertRaisesRegex(ValueError, 'production canonical launch budget exhausted'):
                factory.process_factory(argv, **pipes())
            self.assertEqual(spawn.call_count, 4)
            self.assertEqual(spawn.call_args.args, (argv,))
            self.assertEqual(spawn.call_args.kwargs, pipes())

    def test_failed_spawn_consumes_attempt_no_hidden_retry(self):
        factory = client.ProductionCanonicalClientFactory(); p = provider.selected_production_provider()
        with patch.object(subprocess, 'Popen', side_effect=OSError('authored launch failure')) as spawn:
            for _ in range(4):
                with self.assertRaises(OSError): factory.process_factory([str(p.build/p.executable_name)], **pipes())
            with self.assertRaisesRegex(ValueError, 'launch budget exhausted'):
                factory.process_factory([str(p.build/p.executable_name)], **pipes())
            self.assertEqual(spawn.call_count, 4)

    def test_no_extra_command_options_or_missing_pipe_allowed(self):
        factory = client.ProductionCanonicalClientFactory(); p = provider.selected_production_provider()
        argv = [str(p.build/p.executable_name)]
        for a, opts in [(argv+['--foreign'], pipes()), (argv, dict(pipes(), shell=True)),
                (argv, dict(pipes(), text=True)), (argv, dict(pipes(), env={})),
                (argv, dict(pipes(), stdin=subprocess.DEVNULL)), (argv, dict(pipes(), bufsize=False))]:
            with self.subTest(opts=opts), self.assertRaises(ValueError): factory.process_factory(a, **opts)

    def test_translation_module_exact_pure_and_no_authority(self):
        module = provider.load_production_spotlight_translator()
        self.assertIs(provider.load_production_spotlight_translator(), module)
        self.assertTrue(callable(module.extract_sample)); self.assertTrue(callable(module.translate))
        self.assertNotIn('c12_spotlight_sample', sys.modules)


    def protocol_case(self):
        profile, manifest, _ = provider.verified_production_provider_export()
        config = provider._strict(provider._read(profile.build/'source'/provider.CONFIG, 65536))
        header = dict(session_id='authored-protocol-data-only', producer=dict(
            abi=provider.ABI, build_id=manifest['build_id'], manifest_sha256=profile.manifest_sha256,
            source_sha256=provider.SOURCE_SHA256, config_sha256=provider.CONFIG_SHA256,
            executable_sha256=profile.executable_sha256), fixtures=[dict(fixture_index=f['index'],
            fixture_id='data-fixture-'+str(f['index']), device_id=f['device_id'], boot=f['boot'],
            launch_id='data-launch-'+str(f['index'])) for f in config['fixtures']],
            evidence_source='simulated', physical_permission=False, physical_truth=False)
        return manifest, header

    def test_actual_retained_production_original_stream_uses_exact_data_grammar(self):
        manifest, header = self.protocol_case()
        validator = client.production_protocol_validator(manifest, header, 0)
        path = Path(r'C:\Users\Marcelo\Documents\Engineered Light Fixture\reviews\component-followthrough-2026-10-17\coordinator\home-contract-close-20261019\final-controls-01\cases\test_existing_actual_productio0\capture-001-fixture-0.json')
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '1314fb365cc9e933fc11f1ed5b45e2c49597e939a9274c64642787d49416d47e')
        capture = json.loads(raw); count = 0
        for row in capture['original_pipe_records']:
            if row['kind'] == 'stdin_attempt': validator.request(bytes.fromhex(row['original_raw_hex']))
            elif row['kind'] == 'stdout_bytes':
                count += len(validator.stdout(bytes.fromhex(row['original_raw_hex'])))
            elif row['kind'] == 'stdout_eof': validator.finish()
        self.assertEqual(count, 257)  # 256 command responses and original EOF terminal row.
        self.assertIsNone(validator.first_failure)
        self.assertTrue(validator.closed)

    def test_changed_protocol_manifest_header_and_stream_refuse(self):
        manifest, header = self.protocol_case()
        for changed in [dict(manifest, base_revision='f'*64), dict(manifest, source_digest='f'*64)]:
            with self.subTest(changed=list(changed)), self.assertRaisesRegex(ValueError, 'exact fixed production protocol'):
                client.production_protocol_validator(changed, header, 0)
        for change in [('producer','source_sha256','f'*64), ('fixtures',0,'foreign')]:
            bad = deepcopy(header)
            if change[0] == 'producer': bad['producer'][change[1]] = change[2]
            else: bad['fixtures'][0]['device_id'] = 'f'*32
            with self.subTest(change=change), self.assertRaises(ValueError):
                client.production_protocol_validator(manifest, bad, 0)
        for index in [True, -1, 4]:
            with self.assertRaises(ValueError): client.production_protocol_validator(manifest, header, index)
        validator = client.production_protocol_validator(manifest, header, 0)
        with self.assertRaises(ValueError): validator.request(b'{"op":"lighting_timer"}\n')
        self.assertIsNotNone(validator.first_failure)
        with self.assertRaisesRegex(ValueError, 'first_failure_sticky'):
            validator.request(b'{"op":"describe","request_id":"x","schema":"c12-canonical-capture-1","version":1}\n')


if __name__ == '__main__': unittest.main()
