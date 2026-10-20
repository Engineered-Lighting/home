"""Live viewing must work with no recording or model runtime."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app, PREFIX
from config import Settings
from live_preview import LivePreview
from store import LabError


class PreviewTests(unittest.TestCase):
    def source(self):
        self.now=100
        meta={'schema':2,'width':640,'height':360,'observed_at_unix_ns':99_500_000_000,
            'uncertainty_seconds':.2,'session':'capture1','sequence':7,'timing_generation':1}
        self.fetch=Mock(return_value=(b'\xff\xd8fixture\xff\xd9',base64.urlsafe_b64encode(json.dumps(meta).encode()).decode()))
        return LivePreview(self.fetch,site_id='victoria',camera_id='den_oak',clock=lambda:self.now,monotonic=lambda:self.now)

    def test_no_session_runtime_or_persistence_required(self):
        source=self.source()
        token='x'*40
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(storage_root=Path(tmp),min_free_bytes=0,cameras=({'id':'den_oak'},),
                token_grants=({'subject':'viewer','site_id':'victoria','token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
            headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
            with patch('subprocess.Popen',side_effect=AssertionError('Process started')):
                with TestClient(create_app(settings,previews={'den_oak':source})) as client:
                    endpoint=PREFIX+'/cameras/den_oak/preview'
                    self.assertEqual(client.get(endpoint).status_code,401)
                    self.fetch.assert_not_called()
                    caps=client.get(PREFIX+'/capabilities',headers=headers).json()
                    self.assertFalse(caps['recording_allowed'])
                    self.assertIn('den_oak',caps['live_previews'])
                    result=client.get(endpoint,headers=headers)
                    self.assertEqual(result.status_code,200,result.text)
                    self.assertFalse(result.json()['recorded'])
                    self.assertEqual(result.headers['cache-control'],'no-store')
                    self.assertEqual(client.get(PREFIX+'/sessions',headers=headers).json()['items'],[])
                    self.assertEqual(client.get(PREFIX+'/cameras/other/preview',headers=headers).status_code,404)
                    self.assertEqual(client.get(endpoint,headers={**headers,'X-Home-Lab-Site':'echo'}).status_code,403)
                    self.assertEqual(client.post(PREFIX+'/sessions',headers=headers,json={}).status_code,403)
                    self.fetch.assert_called_once()
                    context=PREFIX+'/cameras/den_oak/context'
                    self.assertEqual(client.get(context).status_code,401)
                    self.assertEqual(client.get(context,headers=headers).json()['status'],'missing')
                    self.assertEqual(client.get(context,headers={**headers,'X-Home-Lab-Site':'echo'}).status_code,403)
                    self.assertEqual(client.get(PREFIX+'/cameras/other/context',headers=headers).status_code,404)

    def test_cache_stale_and_failure_never_refresh_old_image(self):
        source=self.source()
        self.assertEqual(source.read()['sequence'],7)
        self.now=100.5
        self.assertEqual(source.read()['source_age_seconds'],1)
        self.fetch.assert_called_once()
        self.now=104
        with self.assertRaises(LabError) as raised:source.read()
        self.assertEqual(raised.exception.code,'preview_stale')
        self.now=106
        self.fetch.side_effect=RuntimeError('private upstream detail')
        with self.assertRaises(LabError) as raised:source.read()
        self.assertNotIn('private',str(raised.exception))
        self.assertIsNone(source.cached)

    def test_bad_image_or_metadata_rejected(self):
        for payload in ((b'not jpeg','e30='),(b'\xff\xd8\xff\xd9','e30='),(b'\xff\xd8\xff\xd9','bad!')):
            source=self.source();self.fetch.return_value=payload
            with self.assertRaises(LabError):source.read()

    def test_default_app_does_not_advertise_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            token='x'*40
            settings=Settings(storage_root=Path(tmp),cameras=({'id':'den_oak'},),token_grants=(
                {'subject':'viewer','site_id':'victoria','token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
            with TestClient(create_app(settings)) as client:
                headers={'Authorization':'Bearer '+token}
                self.assertEqual(client.get(PREFIX+'/capabilities',headers=headers).json()['live_previews'],{})
                self.assertEqual(client.get(PREFIX+'/cameras/den_oak/preview',headers=headers).status_code,503)
