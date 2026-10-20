"""Fixed managed composition is inert; all provisioning and I/O are authored."""
from copy import deepcopy
import asyncio
import hashlib
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from app import create_app
from config import Settings
from journal import digest
from live_context_edge import binding_digest
import live_den_mount as mount
from store import Store
import test_live_supervised_composition as authored
from ultralytics_worker import UltralyticsWorker
from rtmpose_worker import RTMPoseWorker


class ManagedDenMountTests(unittest.TestCase):
    def setUp(self):
        self.authored = authored.ManagedProducerCompositionTests()
        self.authored.setUp()
        self.addCleanup(self.authored.doCleanups)
        composition = self.authored.composition()
        producer = deepcopy(composition.provision)
        self.tokens = {'context': 'authored-den-context-' + 'c' * 40,
                       'operator': 'authored-den-operator-' + 'o' * 40,
                       'lab': 'authored-den-lab-' + 'l' * 40}
        self.hashes = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in self.tokens.items()}
        producer['journal_grants'].append({'token_sha256': self.hashes['context'], 'operations': ['read', 'intent'],
            'scopes': [{k: self.authored.config['session_ref'][k] for k in ('site_id', 'room_id')}],
            'live_bindings': [{'capabilities': ['read'], 'source_id': source_id,
                               'manifest_ref': deepcopy(producer['live_config']['manifest_ref'])}
                              for source_id in sorted(producer['live_config']['manifest']['sources'])]})
        root = Path(self.authored.work.name)
        session = {'schema': 'home-live-session-config-1', 'version': 1,
            'session_ref': deepcopy(self.authored.config['session_ref']), 'configuration_digest': digest(self.authored.config),
            'mode': 'development', 'max_runtime_s': 10, 'max_cycles': 2, 'interval_s': .5, 'drain_timeout_s': 1}
        self.provision = {'schema': 'home-managed-den-mount-1', 'version': 1, 'producer': producer,
            'runtime': {'schema': 'home-live-runtime-config-1', 'version': 1, 'session': session,
                'worker_config': None, 'node_runtime': shutil.which('node'), 'archive_file': str(root / 'authored-session.jsonl'),
                'archive_limits': {'max_records': 512, 'max_bytes': 1024**2, 'min_free_bytes': 1},
                'source_bundle_digest': 'a' * 64},
            'preflight_file': str(root / 'authored-preflight.json'), 'context_token_sha256': self.hashes['context'],
            'operator_token_sha256': self.hashes['operator']}
        self.lab_grants = ({'subject': 'authored-owner', 'site_id': 'victoria',
                            'token_sha256': self.hashes['lab'], 'capabilities': ['read']},)
        settings = Settings(root / 'authored-lab', min_free_bytes=0, token_grants=self.lab_grants,
                            cameras=({'id': 'den'},), developer_fixture_mode=True)
        self.store = Store(settings)
        self.addCleanup(self.store.close)
        self.runtime_closes = []
        self.runtime = SimpleNamespace(capture_scope=SimpleNamespace(require_turns=lambda: self.authored.turns),
                                      close=lambda: self.runtime_closes.append('closed') or {'drained': True})
        self.app = create_app(settings, runtime=self.runtime, store=self.store)
        self.forbidden = []
        def forbidden(*args, **kwargs):
            self.forbidden.append('external_io')
            raise AssertionError('mount_must_remain_inert')
        for target in ('subprocess.Popen', 'http.client.HTTPConnection.connect', 'http.client.HTTPSConnection.connect'):
            p = patch(target, forbidden)
            p.start()
            self.addCleanup(p.stop)
        for cls in (UltralyticsWorker, RTMPoseWorker):
            p = patch.object(cls, 'load', forbidden)
            p.start()
            self.addCleanup(p.stop)

    def installed(self, provision=None, **kwargs):
        value = self.provision if provision is None else provision
        with patch.object(mount, 'private_json', return_value=deepcopy(value)), \
                patch.object(mount, 'private_token', return_value=authored.capture.TOKEN):
            return mount.mount_den_session(self.app, str(Path(self.authored.work.name) / 'authored-mount.json'),
                incumbent=kwargs.get('incumbent', self.runtime), occupancy=kwargs.get('occupancy', self.authored.occupancy),
                lab_grants=kwargs.get('lab_grants', self.lab_grants))

    def request(self, app, method, path, *, token=None, body=None):
        async def run():
            headers = {'Authorization': 'Bearer ' + token} if token else {}
            binding = {'scope': self.provision['runtime']['session']['session_ref'],
                       'manifest_ref': self.provision['producer']['live_config']['manifest_ref']}
            headers['X-Home-Context-Binding'] = binding_digest(binding)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://authored-controller.invalid') as client:
                return await client.request(method, path, headers=headers, json=body)
        return asyncio.run(run())

    def test_actual_builder_and_runtime_install_without_models_subprocess_or_camera(self):
        # A generic top-level archive module cannot capture the managed package import.
        with patch.dict(sys.modules, {'archive': SimpleNamespace()}):
            mounted = self.installed()
            self.assertEqual(sys.modules['home_den_runtime.supervisor'].RecorderRPC.__module__, 'home_den_runtime.archive')
        supervisor = self.app.state.den_session_supervisor
        self.assertIsNone(supervisor.graph)
        self.assertIsNone(supervisor.worker)
        self.assertEqual(supervisor.status()['status'], 'idle')
        self.assertEqual(self.forbidden, [])
        self.assertFalse(Path(self.provision['runtime']['archive_file']).exists())
        self.assertIs(mounted.state, self.app.state)
        self.assertTrue(self.app.state.den_shutdown_guard())

    def test_closed_scope_credentials_and_narrow_grants_refuse_inertly(self):
        mutations = [
            lambda p: p.update(extra=True),
            lambda p: p['runtime']['session']['session_ref'].update(session_id='unbound-session'),
            lambda p: p.update(operator_token_sha256=p['context_token_sha256']),
            lambda p: p['producer']['journal_grants'][-1].update(operations=['read', 'intent', 'publish']),
            lambda p: p['producer']['journal_grants'][-1]['live_bindings'][0].update(capabilities=['read', 'commit']),
            lambda p: p['producer']['journal_grants'][-1].update(live_bindings=[]),
            lambda p: p['producer']['journal_grants'][-1]['live_bindings'][0]['manifest_ref'].update(digest='f' * 64),
            lambda p: p['producer']['journal_grants'][-1].update(scopes=[{'site_id': 'other', 'room_id': 'den'}]),
            lambda p: p.update(context_token_sha256=self.hashes['lab']),
        ]
        for mutate in mutations:
            value = deepcopy(self.provision)
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.installed(value)
        with self.assertRaises(ValueError):
            self.installed(incumbent=SimpleNamespace())
        self.assertEqual(self.forbidden, [])

    def test_acquisition5_requires_private_assessment_export_before_mount(self):
        value = deepcopy(self.provision)
        config = value['producer']['acquisition_config']
        config.update(schema='home-production-acquisition-5', version=5)
        value['runtime']['session']['configuration_digest'] = digest(config)
        with self.assertRaisesRegex(ValueError, 'den_incumbent_scope_or_assessment_required'):
            self.installed(value)
        self.assertEqual(self.forbidden, [])

    def test_context_operator_and_unchanged_real_lab_auth_are_disjoint(self):
        mounted = self.installed()
        lab = '/api/perception-lab/v1/capabilities'
        for token in (None, self.tokens['context'], self.tokens['operator']):
            self.assertEqual(self.request(mounted, 'GET', lab, token=token).status_code, 401)
        self.assertEqual(self.request(mounted, 'GET', lab, token=self.tokens['lab']).status_code, 200)
        scope = self.provision['runtime']['session']['session_ref']
        context = '/api/home-context/v1/snapshot?site_id=' + scope['site_id'] + '&room_id=' + scope['room_id']
        self.assertEqual(self.request(mounted, 'GET', context, token=self.tokens['lab']).status_code, 401)
        self.assertEqual(self.request(mounted, 'GET', context, token=self.tokens['context']).status_code, 503)
        command = {'schema': 'home-live-session-command-1', 'version': 1, 'id': 'authored-status', 'operation': 'status',
                   'session_ref': self.provision['runtime']['session']['session_ref'],
                   'configuration_digest': self.provision['runtime']['session']['configuration_digest']}
        route = '/api/home-live-session/v1/status'
        for token in (self.tokens['context'], self.tokens['lab']):
            self.assertEqual(self.request(mounted, 'POST', route, token=token, body=command).status_code, 403)
        result = self.request(mounted, 'POST', route, token=self.tokens['operator'], body=command)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'idle')
        self.assertEqual(self.forbidden, [])

    def test_unconfirmed_shutdown_retains_incumbent_and_preserves_lifespan_exception(self):
        self.installed()
        self.app.state.den_shutdown_guard = lambda: False
        async def run():
            async with self.app.router.lifespan_context(self.app):
                raise RuntimeError('authored-lifespan-failure')
        with self.assertRaisesRegex(RuntimeError, 'authored-lifespan-failure'):
            asyncio.run(run())
        self.assertFalse(self.app.state.shutdown_drained)
        self.assertEqual(self.runtime_closes, [])
        self.assertEqual(self.store.list_sessions(), [])

    def test_confirmed_shutdown_closes_incumbent_after_guard(self):
        self.installed()
        async def run():
            async with self.app.router.lifespan_context(self.app):
                pass
        asyncio.run(run())
        self.assertTrue(self.app.state.shutdown_drained)
        self.assertEqual(self.runtime_closes, ['closed'])


class PollBudgetManagedMountTests(unittest.TestCase):
    """Reject crossed tuple or command authority before source/file effects."""
    def provision(self):
        return {'schema': 'home-managed-den-mount-7', 'version': 7,
            'producer': {'schema': 'home-incumbent-lab-producer-7', 'version': 7},
            'runtime': {'schema': 'home-live-runtime-config-2', 'version': 2,
                        'session': {'schema': 'home-live-session-config-2', 'version': 2}},
            'stereo_source': {'schema': 'home-lab-stereo-source-config-4', 'version': 4},
            'preflight_file': str(Path(__file__).resolve().parent / 'authored-poll-preflight.json'),
            'context_token_sha256': 'a' * 64, 'operator_token_sha256': 'b' * 64,
            'command_capture': None}

    def test_mount7_rejects_commands_and_crossed_versions_before_effects(self):
        import lab_live_runtime
        mutations = [
            lambda p: p.update(command_capture={}),
            lambda p: p.pop('command_capture'),
            lambda p: p['producer'].update(schema='home-incumbent-lab-producer-6', version=6),
            lambda p: p['runtime'].update(schema='home-live-runtime-config-1', version=1),
            lambda p: p['runtime']['session'].update(schema='home-live-session-config-1', version=1),
            lambda p: p['stereo_source'].update(schema='home-lab-stereo-source-config-3', version=3),
            lambda p: p.update(schema='home-managed-den-mount-6', version=6),
        ]
        for index, mutate in enumerate(mutations):
            value = self.provision(); mutate(value)
            with self.subTest(index=index), \
                    patch.object(mount, 'LocalDenPreflight') as preflight, \
                    patch.object(mount, 'private_token') as token, \
                    patch.object(lab_live_runtime, 'lab_graph_configuration') as configuration:
                with self.assertRaises(ValueError):
                    mount._mount_provision(SimpleNamespace(state=SimpleNamespace()), value,
                        incumbent=object(), occupancy=object(), lab_grants=[])
                preflight.assert_not_called(); token.assert_not_called(); configuration.assert_not_called()
        legacy = self.provision(); legacy.update(schema='home-managed-den-mount-6', version=6)
        legacy['runtime'].update(schema='home-live-runtime-config-1', version=1)
        legacy['runtime']['session'].update(schema='home-live-session-config-1', version=1)
        with patch.object(mount, 'LocalDenPreflight') as preflight, self.assertRaisesRegex(
                ValueError, 'fixed_mount7_source7_required'):
            mount._mount_provision(SimpleNamespace(state=SimpleNamespace()), legacy,
                incumbent=object(), occupancy=object(), lab_grants=[])
        preflight.assert_not_called()
        value = self.provision()
        with patch.object(mount, 'LocalDenPreflight') as preflight, self.assertRaisesRegex(
                ValueError, 'poll_budget_commands_unavailable'):
            mount._mount_provision(SimpleNamespace(state=SimpleNamespace()), value,
                incumbent=object(), occupancy=object(), lab_grants=[], command_factory=lambda _: None)
        preflight.assert_not_called()

    def test_bootstrap6_refuses_injected_factory_before_installing_supervisor(self):
        from journal import digest
        provision = {'schema': 'home-den-lab-bootstrap-6', 'version': 6,
            'mount_file': str(Path(__file__).resolve().parent / 'authored-poll-mount.json'),
            'scope': {'site_id': 'victoria', 'room_id': 'den'},
            'operator_token_sha256': 'a' * 64, 'command_capture_digest': digest(None)}
        app = SimpleNamespace(state=SimpleNamespace())
        with patch.object(mount, 'private_json', return_value=provision), \
                self.assertRaisesRegex(ValueError, 'poll_budget_commands_unavailable'):
            mount.mount_den_session(app, provision['mount_file'], incumbent=object(), occupancy=object(),
                lab_grants=[], command_factory=lambda _: None)
        self.assertFalse(hasattr(app.state, 'den_session_supervisor'))


if __name__ == '__main__':
    unittest.main()
