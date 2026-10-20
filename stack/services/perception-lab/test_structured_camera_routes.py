"""Authenticated read path with authored originals; no camera/model I/O."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient
from app import create_app
from config import Settings
from store import Store
from structured_camera_routes import PREFIX, install_structured_camera_context


class StructuredCameraRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        grants = tuple({'subject': capability, 'site_id': 'victoria',
                        'token_sha256': hashlib.sha256((capability * 32).encode()).hexdigest(),
                        'capabilities': [capability]} for capability in ('read', 'run'))
        settings = Settings(Path(self.temp.name), min_free_bytes=0, cameras=({'id': 'den'},), token_grants=grants)
        self.store = Store(settings)
        self.app = create_app(settings, store=self.store)
        self.value = {'schema': 'home-camera-structured-context-1', 'version': 1,
                      'site_id': 'victoria', 'camera_id': 'den', 'qualification': None,
                      'physical_commands': False, 'freshness': 'missing'}
        def snapshot():
            self.calls.append('snapshot')
            return self.value
        self.source = SimpleNamespace(structured_context=snapshot)
        install_structured_camera_context(self.app, source=self.source)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.temp.cleanup()

    def headers(self, capability='read'):
        return {'Authorization': 'Bearer ' + capability * 32, 'X-Home-Lab-Site': 'victoria'}

    def test_only_authenticated_site_read_can_observe_and_no_query_or_post(self):
        self.assertEqual(self.client.get(PREFIX).status_code, 401)
        self.assertEqual(self.client.get(PREFIX, headers=self.headers('run')).status_code, 403)
        foreign = {**self.headers(), 'X-Home-Lab-Site': 'echo'}
        self.assertEqual(self.client.get(PREFIX, headers=foreign).status_code, 403)
        self.assertEqual(self.client.get(PREFIX + '?url=http://elsewhere', headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.post(PREFIX, headers=self.headers()).status_code, 405)
        self.assertEqual(self.calls, [])
        response = self.client.get(PREFIX, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(response.json(), self.value)
        self.assertEqual(self.calls, ['snapshot'])

    def test_unsupported_authority_scope_or_oversize_output_refuses(self):
        for patch in ({'site_id': 'echo'}, {'camera_id': 'other'}, {'physical_commands': True},
                      {'qualification': 'accepted'}, {'unexpected': 'x' * 32768}):
            self.value.update(patch)
            response = self.client.get(PREFIX, headers=self.headers())
            self.assertEqual(response.status_code, 503)
            self.value = {k: v for k, v in self.value.items() if k not in patch}
            self.value.update(site_id='victoria', camera_id='den', physical_commands=False, qualification=None)

    def test_installing_twice_cannot_create_another_reader(self):
        with self.assertRaisesRegex(ValueError, 'one_structured_context_reader_required'):
            install_structured_camera_context(self.app, source=self.source)
        self.assertEqual(self.calls, [])

    def test_unprovisioned_provider_returns_explicit_missing_without_start(self):
        self.source.structured_context = None
        response = self.client.get(PREFIX, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value['freshness'], 'missing')
        self.assertIsNone(value['latest'])
        self.assertEqual(value['provider']['last_reason'], 'not_provisioned')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
