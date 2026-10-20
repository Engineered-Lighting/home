import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app
from config import Settings
from store import Store

RID = '01234567-1234-4234-8234-0123456789ab'
URL = '/api/perception-lab/v1/cameras/den/context-refresh'

class Backend:
    def __init__(self): self.calls = []; self.records = {}
    async def accept(self, namespace, rid):
        self.calls.append((namespace, rid))
        return self.records.setdefault((namespace, rid), dict(request_id=rid, accepted_at=1000., deadline=1015., state='queued', outcome=None))
    async def status(self, namespace, rid): return self.records.get((namespace, rid))

    async def image(self, namespace, rid):
        if (namespace,rid) in self.records:
            return dict(request_id=rid,frame_id='frame',observed_at=1000.,valid_until=1180.,image_base64='/9j/2Q==')

class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = 'fixture-refresh-' + 'a' * 40
        self.reader = 'fixture-reader-' + 'b' * 40
        grants = tuple(dict(subject=subject, site_id='victoria', token_sha256=hashlib.sha256(token.encode()).hexdigest(), capabilities=scopes)
            for token, subject, scopes in ((self.token, 'refresh-gateway', ['camera_refresh']), (self.reader, 'reader', ['read', 'run'])))
        self.settings = Settings(Path(self.temp.name), min_free_bytes=0, token_grants=grants, cameras=({'id': 'den'},), developer_fixture_mode=True)
        self.store = Store(self.settings)
        self.addCleanup(self.store.close)
        self.backend = Backend()
        self.client = TestClient(create_app(self.settings, store=self.store, refresh_backend=self.backend, refresh_gateway_subject='refresh-gateway'), raise_server_exceptions=False)
        self.headers = {'Authorization': 'Bearer ' + self.token, 'X-Home-Lab-Site': 'victoria', 'X-Home-Refresh-Namespace': 'a' * 64}

    def post(self, **kwargs):
        return self.client.post(URL, headers=kwargs.pop('headers', self.headers), **kwargs)

    def test_explicit_post_and_isolated_status(self):
        result = self.post(json={'version': 1, 'site_id': 'victoria', 'request_id': RID})
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(result.json()['deadline'], 1015)
        self.assertEqual(self.client.get(URL+'/'+RID, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(URL+'/'+RID, headers={**self.headers, 'X-Home-Refresh-Namespace': 'b'*64}).status_code, 404)

    def test_read_capability_cannot_refresh(self):
        result = self.post(headers={**self.headers, 'Authorization': 'Bearer '+self.reader}, json={'version': 1, 'site_id': 'victoria', 'request_id': RID})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.backend.calls, [])

    def test_missing_backend_stays_unavailable(self):
        client = TestClient(create_app(self.settings, store=self.store), raise_server_exceptions=False)
        result = client.post(URL, headers=self.headers, json={'version':1,'site_id':'victoria','request_id':RID})
        self.assertEqual(result.status_code, 503)

    def test_duplicate_json_identity_fields_rejected(self):
        result = self.post(content='{"version":1,"site_id":"victoria","request_id":"'+RID+'","request_id":"'+RID+'"}', headers={**self.headers, 'Content-Type':'application/json'})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.backend.calls, [])

    def test_poll_rate_bounded(self):
        for _ in range(30): self.client.get(URL+'/'+RID, headers=self.headers)
        self.assertEqual(self.client.get(URL+'/'+RID, headers=self.headers).status_code, 429)

    def test_arbitrary_identity_and_endpoint_fields_denied(self):
        result = self.post(json={'version': 1, 'site_id': 'victoria', 'request_id': RID, 'url': 'https://other.invalid'})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.backend.calls, [])

    def test_image_requires_same_namespace_and_camera_capability(self):
        self.post(json={'version':1,'site_id':'victoria','request_id':RID})
        result=self.client.get(URL+'/'+RID+'/image',headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['image_base64'],'/9j/2Q==')
        self.assertIn('no-store',result.headers['cache-control'])
        for patch,status in [({'X-Home-Refresh-Namespace':'b'*64},404),
                             ({'Authorization':'Bearer '+self.reader},403)]:
            self.assertEqual(self.client.get(URL+'/'+RID+'/image',headers={**self.headers,**patch}).status_code,status)

if __name__ == '__main__': unittest.main()
