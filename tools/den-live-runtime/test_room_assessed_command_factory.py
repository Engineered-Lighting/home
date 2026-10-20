"""Declaration3/profile boundaries only; no native, Store, model or authority."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import room_command_factory as factory
import test_room_command_factory as historical
from lab_live_runtime import LabRuntimeComposition


def authored_declaration():
    value = historical.authored_declaration()
    value.update(schema='home-den-canonical-command-declaration-3', version=3,
        factory_ref=deepcopy(factory.ASSESSED_FACTORY_REF),
        source_pins={name: 'b' * 64 for name in factory.ASSESSED_SOURCE_INVENTORY},
        original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256='c' * 64,
        task_profile_ref=deepcopy(factory.RETAINED_TASK_PROFILE_REF))
    return value


def authored_ports():
    """Actual constructor type with explicit graph/currentness port doubles."""
    policy = dict(schema='home-live-room-policy-2', version=2)
    config = dict(schema='home-live-ingress-config-13', version=13,
        manifest=dict(schema='home-live-ingress-manifest-13', version=13,
            worker=dict(room_policy=deepcopy(policy), room_subset_mode=True)))
    composition = LabRuntimeComposition.__new__(LabRuntimeComposition)
    composition.producer = dict(version=8, live_config=config,
        limits=dict(max_poll_attempts=1, max_capture_originals=1, max_runtime_s=120, page_limit=1),
        desk_policy=dict(original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256='c' * 64))
    composition.scope = dict(site_id='authored', room_id='den', session_id='session', domain_epoch='epoch')
    composition.configuration_digest = 'd' * 64
    composition.join = SimpleNamespace(scope=composition.scope,
        bundled_binding=lambda: dict(bundle_input_policy_sha256='c' * 64))
    composition.bundled_source = object()
    worker = SimpleNamespace(worker_version='home-live-context-worker-12.0.0',
        policy=dict(room_policy=deepcopy(policy), room_subset_mode=True))
    graph = SimpleNamespace(join=composition.join, configuration_digest=composition.configuration_digest,
        config=dict(version=8), bundled_source=composition.bundled_source,
        journal=SimpleNamespace(dynamic=SimpleNamespace(identity=composition.scope,
            config=deepcopy(config), worker=worker)))
    composition.graph = graph
    composition.command_current = lambda offered: offered is graph
    return composition, graph


class AssessedCommandFactoryTests(unittest.TestCase):
    def test_declaration3_requires_exact_task_profile_and_128_source_pins(self):
        value = authored_declaration()
        self.assertEqual(factory.validate_command_declaration(value, source_bundle_digest='a' * 64), value)
        self.assertEqual(len(value['source_pins']), 128)
        self.assertIn('contracts/home-live-context/room-subset-schemas/DIGESTS.json', value['source_pins'])
        self.assertIn('stack/services/home-context/room_subset_worker.py', value['source_pins'])
        for change in (
                lambda v: v.pop('task_profile_ref'),
                lambda v: v['task_profile_ref'].update(digest='d' * 64),
                lambda v: v['source_pins'].pop('contracts/home-live-context/room-subset-schemas/DIGESTS.json'),
                lambda v: v['source_pins'].pop('stack/services/home-context/room_subset_worker.py'),
                lambda v: v.update(command_factory='caller.module'),
                lambda v: v['limits'].update(max_commits=257)):
            offered = deepcopy(value); change(offered)
            with self.subTest(change=change), self.assertRaises(ValueError):
                factory.validate_command_declaration(offered, source_bundle_digest='a' * 64)

    def test_factory3_accepts_only_source8_actual_graph_and_independent_commit_budget(self):
        composition, graph = authored_ports()
        declaration = authored_declaration()
        with patch('os.open', side_effect=AssertionError('no_file_effect')) as opened, \
                patch('subprocess.Popen', side_effect=AssertionError('no_native_effect')) as spawned:
            owner = factory.RoomCommandFactory(declaration, composition=composition)
            self.assertIs(owner._graph(graph), graph.journal.dynamic.worker)
        self.assertEqual(owner.declaration['limits']['max_commits'], 256)
        self.assertNotIn('max_cycles', composition.producer['limits'])
        self.assertFalse(owner.used)
        opened.assert_not_called(); spawned.assert_not_called()
        for version in (5, 6, 7):
            composition, _ = authored_ports(); composition.producer['version'] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                factory.RoomCommandFactory(declaration, composition=composition)

    def test_actual_graph_rechecks_profile_provider_policy_and_currentness_before_effects(self):
        for changed in ('ingress', 'worker', 'policy', 'subset_mode', 'provider', 'family', 'current'):
            composition, graph = authored_ports()
            owner = factory.RoomCommandFactory(authored_declaration(), composition=composition)
            if changed == 'ingress': graph.journal.dynamic.config['version'] = 12
            elif changed == 'worker': graph.journal.dynamic.worker.worker_version = 'home-live-context-worker-11.0.0'
            elif changed == 'policy': graph.journal.dynamic.worker.policy['room_policy']['version'] = 1
            elif changed == 'subset_mode': graph.journal.dynamic.worker.policy['room_subset_mode'] = False
            elif changed == 'provider': graph.bundled_source = object()
            elif changed == 'family': composition.producer['desk_policy']['original_family'] = 'existing-api-assessment-1'
            else: composition.command_current = lambda _: False
            with self.subTest(changed=changed), \
                    patch('os.open', side_effect=AssertionError('no_file_effect')) as opened, \
                    patch('subprocess.Popen', side_effect=AssertionError('no_native_effect')) as spawned:
                with self.assertRaises(ValueError): owner(graph, object())
                opened.assert_not_called(); spawned.assert_not_called()
                self.assertFalse(owner.used)

    def test_historical_declaration_inventories_and_source_pairings_remain_strict(self):
        import test_room_command_policy_boundary as controls
        self.assertEqual(len(factory.SOURCE_INVENTORY), 124)
        self.assertEqual(len(factory.BUNDLED_SOURCE_INVENTORY), 126)
        for producer_version in (5, 6):
            composition, graph, declaration = controls.authored_profile(producer_version)
            original = deepcopy(declaration)
            self.assertEqual(factory.validate_command_declaration(declaration, source_bundle_digest='a' * 64), original)
            owner = factory.RoomCommandFactory(declaration, composition=composition)
            self.assertIs(owner._graph(graph), graph.journal.dynamic.worker)
            composition.producer['version'] = 8
            with self.assertRaises(ValueError): factory.RoomCommandFactory(declaration, composition=composition)
            self.assertEqual(declaration, original)


if __name__ == '__main__':
    unittest.main()
