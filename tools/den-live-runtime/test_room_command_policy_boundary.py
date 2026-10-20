"""Focused profile refusal tests with authored currentness/graph ports only.

No Store/Journal initialization, worker/native process, host, camera or model.
These tests do not establish the future config13/worker12 pipeline or commands.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import room_command_factory as factory_module
import room_command_projection as projection
import test_room_command_factory as factory_fixture
import test_room_command_projection as projection_fixture
from lab_live_runtime import LabRuntimeComposition
from room_committed_source import CommittedRoomSource


def authored_profile(producer_version):
    declaration = factory_fixture.authored_declaration()
    if producer_version == 6:
        declaration.update(schema='home-den-canonical-command-declaration-2', version=2,
            factory_ref=deepcopy(factory_module.BUNDLED_FACTORY_REF),
            source_pins={p: 'b' * 64 for p in factory_module.BUNDLED_SOURCE_INVENTORY},
            original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256='c' * 64)
    policy = dict(schema='home-live-room-policy-1', version=1)
    config = dict(schema='home-live-ingress-config-12', version=12,
        manifest=dict(schema='home-live-ingress-manifest-12', version=12,
                      worker=dict(room_policy=deepcopy(policy))))
    # Actual constructor type, without invoking operational initialization.
    # The doubles below describe only the trusted ports this boundary reads.
    composition = LabRuntimeComposition.__new__(LabRuntimeComposition)
    composition.producer = dict(version=producer_version, live_config=config,
        limits=dict(max_runtime_s=120), desk_policy=dict(original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256='c' * 64))
    composition.scope = dict(site_id='authored', room_id='den', session_id='session', domain_epoch='epoch')
    composition.configuration_digest = 'd' * 64
    composition.join = SimpleNamespace(scope=composition.scope,
        bundled_binding=lambda: dict(bundle_input_policy_sha256='c' * 64))
    composition.bundled_source = object()
    worker = SimpleNamespace(worker_version='home-live-context-worker-11.0.0',
                             policy=dict(room_policy=deepcopy(policy)))
    graph = SimpleNamespace(join=composition.join, configuration_digest=composition.configuration_digest,
        config=dict(version=producer_version), bundled_source=composition.bundled_source,
        journal=SimpleNamespace(dynamic=SimpleNamespace(identity=composition.scope,
            config=deepcopy(config), worker=worker)))
    composition.graph = graph
    composition.command_current = lambda offered: offered is graph
    return composition, graph, declaration


def future_config(config):
    config.update(schema='home-live-ingress-config-13', version=13)
    config['manifest'].update(schema='home-live-ingress-manifest-13', version=13)
    config['manifest']['worker']['room_policy'] = dict(schema='home-live-room-policy-2', version=2)


class RoomCommandPolicyBoundaryTests(unittest.TestCase):
    def test_current12_keeps_source5_and_source6_acquisition_versions(self):
        for version in (5, 6):
            with self.subTest(producer=version):
                composition, graph, declaration = authored_profile(version)
                original = deepcopy(declaration)
                with patch('os.open', side_effect=AssertionError('no_recording_open')) as opened, \
                     patch('subprocess.Popen', side_effect=AssertionError('no_native_launch')) as spawned:
                    owner = factory_module.RoomCommandFactory(declaration, composition=composition)
                    self.assertIs(owner._graph(graph), graph.journal.dynamic.worker)
                self.assertEqual(owner.declaration, original)
                self.assertEqual(graph.config['version'], version)
                self.assertFalse(owner.used)
                opened.assert_not_called(); spawned.assert_not_called()

    def test_constructor_and_actual_graph_refuse_future_profile_before_effects(self):
        for version in (5, 6):
            composition, graph, declaration = authored_profile(version)
            future_config(composition.producer['live_config'])
            with self.subTest(producer=version, stage='constructor'), \
                 patch('os.open', side_effect=AssertionError('no_recording_open')) as opened, \
                 patch('subprocess.Popen', side_effect=AssertionError('no_native_launch')) as spawned:
                with self.assertRaisesRegex(ValueError, '^config12_policy1_command_profile_required$'):
                    factory_module.RoomCommandFactory(declaration, composition=composition)
                opened.assert_not_called(); spawned.assert_not_called()
            for changed in ('composition_config', 'ingress_config', 'worker_version', 'worker_room_policy'):
                composition, graph, declaration = authored_profile(version)
                owner = factory_module.RoomCommandFactory(declaration, composition=composition)
                if changed == 'composition_config':
                    future_config(composition.producer['live_config'])
                elif changed == 'ingress_config':
                    future_config(graph.journal.dynamic.config)
                elif changed == 'worker_version':
                    graph.journal.dynamic.worker.worker_version = 'home-live-context-worker-12.0.0'
                else:
                    graph.journal.dynamic.worker.policy['room_policy'] = dict(schema='home-live-room-policy-2', version=2)
                with self.subTest(producer=version, stage=changed), \
                     patch('os.open', side_effect=AssertionError('no_recording_open')) as opened, \
                     patch('subprocess.Popen', side_effect=AssertionError('no_native_launch')) as spawned:
                    # Public factory call must refuse before Recorder validation,
                    # provider/client loading, provenance writes or native effects.
                    with self.assertRaisesRegex(ValueError, '^config12_stage11_command_graph_required$'):
                        owner(graph, object())
                    self.assertFalse(owner.used)
                    opened.assert_not_called(); spawned.assert_not_called()

    def test_policy2_projection_has_typed_refusal_before_provider_or_flat_fields(self):
        policy = dict(schema='home-live-room-policy-2', version=2,
            fixtures=[dict(fixture_id='fixture-' + str(i), mount_id='mount-' + str(i),
                           profile_ref=dict(id='inventory', digest='a' * 64), assessment=None) for i in range(4)])
        with patch.object(projection, '_provider', side_effect=AssertionError('no_provider_access')) as provider:
            with self.assertRaisesRegex(projection.RoomProjectionRefusal,
                                        '^room_policy2_command_projection_unavailable$'):
                projection.project_committed_snapshot({}, room_policy=policy, provider=object())
            source = CommittedRoomSource.__new__(CommittedRoomSource)
            with self.assertRaisesRegex(projection.RoomProjectionRefusal,
                                        '^room_policy2_command_projection_unavailable$'):
                projection.RoomCommandProjector(source, room_policy=policy, provider=object())
            provider.assert_not_called()

    def test_historical_policy1_authored_projection_literal_stays_unchanged(self):
        snapshot, policy = projection_fixture.authored_projection_case()
        result = projection.project_committed_snapshot(snapshot, room_policy=policy,
            provider=projection_fixture.projection_provider())
        self.assertEqual(result['room_commit_ref'], snapshot['room_commit_ref'])
        self.assertEqual(len(result['fixtures']), 4)
        for fixture in result['fixtures']:
            self.assertEqual(fixture['ambient']['logical_channels'], [4095, 0, 0] * 7)
            self.assertEqual(fixture['ambient']['changes'], [dict(zone=i, level=254, mired=556) for i in range(7)])
            self.assertFalse(fixture['task']['command_ready'])


if __name__ == '__main__':
    unittest.main()
