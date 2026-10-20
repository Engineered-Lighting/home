"""Provisioned factory through real in-process ASGI; no sockets or hardware."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from capture_app import create_capture_app,load_provisioning
from store import Store
from app import PREFIX


class CaptureFactoryTests(unittest.TestCase):
    def test_authenticated_factory_catalog_and_shutdown_without_capture(self):
        self.exercise_factory(False)

    def test_encoded_only_authenticated_record_stop_read_without_jpeg(self):
        self.exercise_factory(True)

    def exercise_factory(self,encoded_only):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);token='test-controller-'+'a'*32
            camera_token='test-camera-'+'b'*32
            (root/'camera-token').write_text(camera_token)
            (root/'settings.json').write_text(json.dumps({
                'storage_root':str(root/'data'),'site_id':'victoria','min_free_bytes':0,
                'cameras':[{'id':'den'}],'token_grants':[{'subject':'fixture','site_id':'victoria',
                    'token_sha256':hashlib.sha256(token.encode()).hexdigest(),
                    'capabilities':['read','record','run','prepare']}]}))
            provision={'schema_version':1,'settings_file':str(root/'settings.json'),
                'camera_credential_file':str(root/'camera-token'),
                'source':{'id':'oak','camera_id':'den','url':'rtsp://127.0.0.1:8554/fixture',
                    'codec':'avc1.640028','codec_validation_id':'fixture'},
                'receiver_clock':{'validation_id':'fixture','uncertainty_seconds':.1},
                'gate_references':{'technical':'fixture','health_resource':'fixture','workload_authorization':'fixture'}}
            if encoded_only:
                provision['capture_mode']='encoded_only'
                provision.pop('camera_credential_file');provision.pop('receiver_clock')
                (root/'camera-token').unlink()
            path=root/'capture.json';path.write_text(json.dumps(provision))
            with patch.dict('os.environ',{'PERCEPTION_LAB_CAPTURE_CONFIG':str(path)}),patch('subprocess.Popen',side_effect=AssertionError('process started')),patch('http.client.HTTPConnection',side_effect=AssertionError('camera contacted')):
                application=create_capture_app()
                with TestClient(application) as client:
                    self.assertEqual(client.get(PREFIX+'/capabilities').status_code,401)
                    headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
                    response=client.get(PREFIX+'/capabilities',headers=headers)
                    self.assertEqual(response.status_code,200,response.text)
                    caps=response.json()
                    self.assertTrue(caps['recording_allowed'])
                    self.assertEqual(len(caps['experiments']),18)
                    self.assertIn({'id':'inference','state':'blocked','reason':'recording_only_commissioning'},caps['gates'])
                    self.assertNotIn(token,response.text)
                    self.assertNotIn(camera_token,response.text)
                    self.assertEqual(client.get(PREFIX+'/sessions',headers=headers).json()['items'],[])
                    wrong={**headers,'X-Home-Lab-Site':'echo'}
                    self.assertEqual(client.get(PREFIX+'/capabilities',headers=wrong).status_code,403)
                    runtime=application.state.controller.runtime
                    if encoded_only:
                        self.assertEqual(runtime.preview_sources,{})
                        self.assertEqual(runtime.bundle_sources,{})
                    self.assertEqual(runtime.runners,{})
                    self.assertEqual(runtime.preview_pumps,{})
                    self.assertIsNone(runtime.reasoning_thread)
                    # Exercise the configured API end to end with capture
                    # replaced at its process boundary, not a replacement app.
                    from preview_client import PreviewPump
                    recorder=runtime.recorders['den']
                    with patch.object(recorder,'start',return_value={'state':'recording'}),patch.object(PreviewPump,'_run',return_value=None):
                        started=client.post(PREFIX+'/sessions',headers=headers,json={
                            'camera_id':'den','experiment_id':caps['experiments'][0]['id'],
                            'name':'Factory integration fixture','request_id':'start-fixture'})
                    self.assertEqual(started.status_code,201,started.text)
                    session=started.json();sid=session['id']
                    self.assertEqual(session['state'],'recording')
                    self.assertEqual(session['reason'],'recording_only_commissioning')
                    media=application.state.store.add_media(sid,b'fixture encoded bytes','video/mp4',{'fixture':True})
                    with patch.object(recorder,'stop',return_value={'state':'stopped'}):
                        stopped=client.post(PREFIX+f'/sessions/{sid}/stop',headers=headers,json={'request_id':'stop-fixture'})
                    self.assertEqual(stopped.status_code,200,stopped.text)
                    self.assertEqual(stopped.json()['state'],'stopped')
                    read=client.get(PREFIX+f"/sessions/{sid}/media/{media['id']}?generation=0",headers=headers)
                    self.assertEqual(read.content,b'fixture encoded bytes')
                    self.assertEqual(runtime.runners,{})
                    self.assertIsNone(runtime.reasoning_thread)
                self.assertTrue(application.state.shutdown_drained)
            settings,*_=load_provisioning(path)
            replacement=Store(settings)
            replacement.close()


if __name__=='__main__':unittest.main()
