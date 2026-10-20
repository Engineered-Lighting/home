"""Fixed native adapters verify authored artifacts; SDK/camera I/O is fake."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

import live_capture_fixtures as capture
from journal import digest
from reasoning import digest as model_digest
from test_live_spatial import LiveSpatialTests


class ManagedProducerCompositionTests(unittest.TestCase):
    setUp = LiveSpatialTests.setUp
    prepare = LiveSpatialTests.prepare
    camera_pair = LiveSpatialTests.camera_pair
    control_clock = LiveSpatialTests.control_clock
    snapshot = LiveSpatialTests.snapshot

    def composition(self, *, allowed=True, corrupt=False, explicit_la=False, la_binding=None):
        from live_supervised_composition import ManagedProducerComposition, FileProducerAuthority
        files = {'detector': Path(self.work.name) / 'authored.pt', 'pose': Path(self.work.name) / 'authored.onnx'}
        for kind, worker in (('detector', self.detector), ('pose', self.pose_worker)):
            content = ('authored-' + kind).encode()
            files[kind].write_bytes(content)
            worker.manifest['sha256'] = hashlib.sha256(content).hexdigest()
        self.refs = {'detector': {'id': self.detector.manifest['profile_id'], 'digest': model_digest(self.detector.manifest)},
                     'pose': {'id': self.pose_worker.manifest['profile_id'], 'digest': model_digest(self.pose_worker.manifest)}}
        self.config['model_refs'] = deepcopy(self.refs)
        self.local_grant['model_refs'] = deepcopy(self.refs)
        self.prepare()
        authority_files = {key: str(Path(self.work.name) / (key + '.json'))
                           for key in ('local_grant', 'preparation_grant', 'household_enabled', 'publication_token')}
        prep = {'schema': 'home-resident-preparation-grant-1', 'version': 1,
            'session_ref': deepcopy(self.config['session_ref']), 'source_id': self.config['source_id'],
            'configuration_digest': digest(self.config), 'model_refs': deepcopy(self.refs),
            'operations': ['resident_preparation'] if allowed else [], 'not_before': capture.NOW,
            'valid_until': capture.NOW + 60, 'generation': 1}
        records = {authority_files['local_grant']: self.local_grant,
                   authority_files['preparation_grant']: prep, authority_files['household_enabled']: True}
        authority = FileProducerAuthority(authority_files, json_reader=lambda path: deepcopy(records[path]),
                                         token_reader=lambda path: capture.TOKEN)
        provision = {'schema': 'home-supervised-den-producer-1', 'version': 1,
            'acquisition_config': self.config, 'live_config': self.live_config, 'capture_policy': self.policy,
            'capture_method': self.method, 'spatial_policy': self.spatial_policy,
            'interaction_policy': deepcopy(getattr(self, 'interaction_policy', None)),
            'assessment_policy': deepcopy(self.recorder.policy) if hasattr(self, 'recorder') else None,
            'journal_path': str(Path(self.work.name) / 'supervised.sqlite'),
            'journal_grants': [self.journal_grant], 'model_manifests': {'detector': self.detector.manifest, 'pose': self.pose_worker.manifest},
            'checkpoint_paths': {kind: str(path) for kind, path in files.items()},
            'authority_files': authority_files, 'preparation_timeout_s': 2}
        if self.config['schema'] == 'home-production-acquisition-5':
            provision['lifecycle_policy'] = deepcopy(self.lifecycle_policy)
        if explicit_la:
            provision.update(schema='home-supervised-den-producer-2',version=2,la_binding=deepcopy(la_binding))
        self.loads = []
        def detector_loader(path, task):
            self.loads.append('detector')
            return self.detector.model
        def pose_loader(path, manifest):
            self.loads.append('pose')
            return self.pose_worker.model
        if corrupt:
            files['detector'].write_bytes(b'changed artifact')
        return ManagedProducerComposition(provision, controller_scope=SimpleNamespace(require_turns=lambda: self.turns),
            authority=authority, preflight=lambda: True, occupancy=self.occupancy, control_clock=self.control_clock,
            clock=lambda: self.now, monotonic=lambda: self.mono, rss=lambda: 64 * 1024**2,
            external_io={'detector_loader': detector_loader, 'pose_loader': pose_loader, 'camera_dependencies': self.camera})

    def test_inert_composition_prepares_verified_native_adapters_only_on_explicit_start(self):
        composition = self.composition()
        self.assertEqual(self.loads, [])
        self.graph = graph = composition.create()
        def cleanup():
            if graph.readiness()['state'] != 'finalized':
                withdrawal = graph.withdraw()
                if withdrawal['input_cursor'] is not None:
                    graph.finalize(drain_receipt={'schema': 'home-acquisition-drain-1', 'version': 1,
                        'session_ref': self.config['session_ref'], 'configuration_digest': graph.configuration_digest,
                        'withdrawal_receipt': withdrawal['barrier_receipt'], 'recorder_status': 'incomplete',
                        'worker_status': 'unconfirmed', 'terminal_cursor': withdrawal['input_cursor']})
        self.addCleanup(cleanup)
        self.assertEqual(self.loads, ['detector', 'pose'])
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        withdrawal = graph.withdraw()
        drain = {'schema': 'home-acquisition-drain-1', 'version': 1, 'session_ref': self.config['session_ref'],
            'configuration_digest': graph.configuration_digest, 'withdrawal_receipt': withdrawal['barrier_receipt'],
            'recorder_status': 'drained', 'worker_status': 'settled', 'terminal_cursor': withdrawal['input_cursor']}
        self.assertEqual(graph.finalize(drain_receipt=drain)['status'], 'stopped')
        self.assertEqual(composition.release()['status'], 'released')

    def test_missing_preparation_grant_or_changed_artifact_cannot_invoke_sdk_loader(self):
        from live_supervised_composition import PreparationUnavailable
        for options in ({'allowed': False}, {'corrupt': True}):
            with self.subTest(options=options):
                composition = self.composition(**options)
                with self.assertRaises(PreparationUnavailable):
                    composition.create()
                self.assertEqual(self.loads, [])

    def test_explicit_la_provision_is_closed_and_never_guesses_a_model_binding(self):
        composition=self.composition(explicit_la=True)
        self.assertEqual(composition.provision['version'],2)
        self.assertIsNone(composition.provision['la_binding'])
        self.assertIsNone(composition._existing_la_sources())
        self.assertEqual(self.loads,[])
        with self.assertRaises(ValueError):
            self.composition(explicit_la=True,la_binding={'model_ref':{'id':'unbound','digest':'a'*64}})


if __name__ == '__main__':
    unittest.main()
