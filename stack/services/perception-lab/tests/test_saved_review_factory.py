"""Existing saved sessions remain reviewable without any capture/model runtime."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app, PREFIX
from config import Settings
from store import Store


class SavedReviewFactoryTests(unittest.TestCase):
    def test_reopen_review_and_persist_without_execution_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = 'review-fixture-' + 'x' * 40
            raw = dict(storage_root=str(root/'data'), min_free_bytes=0,
                cameras=[{'id':'den'}], token_grants=[dict(subject='reviewer',
                    site_id='victoria', token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                    capabilities=['read','review'])])
            path = root/'review.json'
            path.write_text(json.dumps(raw))
            store = Store(Settings.from_file(path))
            try:
                session, _ = store.create_session('den', 'fixture', 'Saved fixture', 'seed')
                sid = session['id']
                store.transition(sid, 'starting')
                store.transition(sid, 'recording')
                media = store.add_media(sid, b'fixture bytes, not playable video', 'video/mp4', {'fixture':True})
                init = store.add_media(sid, b'init fixture', 'video/mp4', {'kind':'encoded_init','role':'init'})
                store.add_media(sid, b'segment fixture', 'video/mp4', {'kind':'encoded_segment','role':'media','initialization_id':init['id'],'end_seconds':2})
                store.transition(sid, 'stopped')
            finally:
                store.close()
            headers = {'Authorization':'Bearer '+token, 'X-Home-Lab-Site':'victoria'}
            with patch.dict('os.environ', {'PERCEPTION_LAB_CONFIG':str(path)}), \
                 patch('subprocess.Popen', side_effect=AssertionError('process started')):
                with TestClient(create_app()) as client:
                    self.assertIsNone(client.app.state.controller.runtime)
                    self.assertFalse(client.get(PREFIX+'/capabilities', headers=headers).json()['recording_allowed'])
                    self.assertEqual(client.get(PREFIX+'/sessions', headers=headers).json()['items'][0]['id'], sid)
                    result = client.get(PREFIX+f"/sessions/{sid}/media/{media['id']}?generation=0", headers=headers)
                    self.assertEqual(result.content, b'fixture bytes, not playable video')
                    payload = dict(expected_revision=0, at_ms=0, end_ms=1000, label='reading', origin='human')
                    saved = client.put(PREFIX+f'/sessions/{sid}/annotations/review', headers=headers, json=payload)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertFalse(saved.json()['reviewed'])
                    video_payload={**payload,'time_basis':'encoded_video','video_init_id':init['id'],'reviewed':True}
                    video_saved=client.put(PREFIX+f'/sessions/{sid}/annotations/video-review',headers=headers,json=video_payload)
                    self.assertEqual(video_saved.status_code,200,video_saved.text)
                    for bad in ({'video_init_id':media['id']},{'end_ms':3000},{'time_basis':'other'}):
                        rejected=client.put(PREFIX+f'/sessions/{sid}/annotations/bad',headers=headers,json={**video_payload,**bad})
                        self.assertEqual(rejected.status_code,422,rejected.text)
                    findings=client.get(PREFIX+f'/sessions/{sid}/findings',headers=headers).json()
                    self.assertEqual(findings['reviewed_intervals'],0)
                    for endpoint, body in (('/sessions', {}), ('/jobs', {'kind':'analysis','request_id':'denied'}), ('/diagnostics', {})):
                        self.assertEqual(client.post(PREFIX+endpoint, headers=headers, json=body).status_code, 403)
                with TestClient(create_app()) as reopened:
                    result = reopened.get(PREFIX+f'/sessions/{sid}', headers=headers)
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.json()['annotations'][0]['label'], 'reading')
                    self.assertEqual(result.json()['session']['state'], 'stopped')
                    label=next(a for a in result.json()['annotations'] if a['id']=='video-review')
                    self.assertEqual(label['time_basis'],'encoded_video')
                    self.assertEqual(label['video_init_id'],init['id'])


if __name__ == '__main__':
    unittest.main()
