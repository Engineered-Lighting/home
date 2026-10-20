"""Incumbent-Lab mount remains inert and preserves exact authority boundaries."""
import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import live_den_mount as mount
from live_test_helpers import TOKEN
import test_lab_live_runtime as runtime_fixture


class IncumbentLabMountTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixture.LabLiveRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        p = self.fixture.producer()
        r = self.fixture.connected_runtime(p, mode='live_shadow_unqualified')
        self.root = self.fixture.f.path
        worker = r['worker_config'].pop('token')
        r['worker_config']['token_file'] = str(self.root/'worker-token')
        self.tokens = {p['publication_token_file']: TOKEN, r['worker_config']['token_file']: worker}
        self.context_token = self.fixture.context_token
        self.operator_token = 'authored-incumbent-operator-token'
        self.provision = {'schema': 'home-managed-den-mount-2', 'version': 2,
            'producer': p, 'runtime': r, 'preflight_file': str(self.root/'preflight.json'),
            'context_token_sha256': hashlib.sha256(self.context_token.encode()).hexdigest(),
            'operator_token_sha256': hashlib.sha256(self.operator_token.encode()).hexdigest()}
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 404, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
        app.state = SimpleNamespace(store=self.fixture.f.store)
        self.app = app
        self.incumbent = SimpleNamespace(capture_scope=object())
        self.lab_grants = [{'token_sha256': 'e'*64}]

    def installed(self, provision=None):
        with patch.object(mount, 'private_json', return_value=deepcopy(provision or self.provision)), \
                patch.object(mount, 'private_token', side_effect=lambda path: self.tokens[path]), \
                patch('subprocess.Popen', side_effect=AssertionError('native_start_at_mount')), \
                patch('http.client.HTTPConnection.connect', side_effect=AssertionError('network_at_mount')):
            return mount.mount_den_session(self.app, str(self.root/'mount.json'), incumbent=self.incumbent,
                occupancy=object(), lab_grants=self.lab_grants, lab_store=self.fixture.f.store)

    def test_actual_lab_mount_is_inert_and_borrows_existing_store(self):
        result = self.installed()
        supervisor = self.app.state.den_session_supervisor
        self.assertEqual(supervisor.status()['mode'], 'live_shadow_unqualified')
        self.assertFalse(supervisor.status()['acceptance_eligible'])
        self.assertIsNone(supervisor.graph)
        self.assertFalse(Path(self.provision['producer']['journal_path']).exists())
        self.assertFalse(Path(self.provision['producer']['original_archive']['filename']).exists())
        self.assertIs(result.state, self.app.state)
        self.assertTrue(self.app.state.den_shutdown_guard())
        self.assertEqual(self.fixture.f.store.get_session(self.fixture.f.session['id'])['state'], 'recording')

    def test_bootstrap_installs_without_reading_session_config_or_loading_sources(self):
        bootstrap = {'schema': 'home-den-lab-bootstrap-1', 'version': 1,
            'mount_file': str(self.root/'not-created-yet.json'),
            'operator_token_sha256': self.provision['operator_token_sha256'],
            'scope': {k: self.provision['runtime']['session']['session_ref'][k] for k in ('site_id', 'room_id')}}
        mounted = self.installed(bootstrap)
        self.assertIsNone(mounted.supervisor.supervisor)
        self.assertTrue(self.app.state.den_shutdown_guard())

    def test_bootstrap_lab_credentials_cannot_install_operator_authority(self):
        bootstrap = {'schema': 'home-den-lab-bootstrap-1', 'version': 1,
            'mount_file': str(self.root/'not-created-yet.json'), 'operator_token_sha256': 'e'*64,
            'scope': {'site_id': 'victoria', 'room_id': 'den'}}
        with self.assertRaisesRegex(ValueError, 'credentials_must_be_disjoint'):
            self.installed(bootstrap)

    def test_lab_mount_rejects_scope_credentials_and_incompatible_intent_authority(self):
        mutations = [
            lambda p: p['runtime']['session']['session_ref'].update(session_id='other-session'),
            lambda p: p.update(operator_token_sha256=p['context_token_sha256']),
            lambda p: p.update(context_token_sha256='e'*64),
            lambda p: p['producer']['journal_grants'][-1].update(operations=['read', 'intent']),
            lambda p: p['producer']['journal_grants'][-1]['live_bindings'][0].update(capabilities=['read', 'publish']),
        ]
        for mutate in mutations:
            value = deepcopy(self.provision)
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.installed(value)


if __name__ == '__main__':
    unittest.main()
