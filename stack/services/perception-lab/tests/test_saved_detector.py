import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from config import Settings
from store import Store, LabError
from controller import Controller
from test_controller_store import Runtime
from saved_detector import import_receipt


class SavedDetectorTest(unittest.TestCase):
    def test_saved_result_replay_integrity_idempotency_and_closed_session_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Settings(Path(tmp),min_free_bytes=0,quota_bytes=64*1024**2,
                cameras=({'id':'den'},),developer_fixture_mode=True),clock=lambda:1000)
            try:
                c=Controller(store,Runtime())
                experiment=c.capabilities()['experiments'][0]['id']
                sid=c.start(dict(camera_id='den',experiment_id=experiment,name='fixture',request_id='test'))['id']
                init=store.add_media(sid,b'fixture-init','video/mp4',{'kind':'encoded_init'})
                c.stop(sid)
                root=Path(tmp)/'derived'/sid;root.mkdir(parents=True)
                image=Image.new('RGB',(4,4)); encoded=io.BytesIO();image.save(encoded,format='PNG')
                data=encoded.getvalue();(root/'frame.png').write_bytes(data)
                source=dict(frame_id='f1',filename='frame.png',sha256=hashlib.sha256(data).hexdigest(),
                    rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest(),width=4,height=4,encoded_video_pts_seconds=1)
                (root/'manifest.json').write_text(json.dumps(dict(session_id=sid,site_id='victoria',camera_id='den',
                    initialization_id=init['id'],frames=[source])))
                profile=dict(profile_id='pair',model_version='yolo26s',checkpoint='yolo26s',precision='fp32',
                    preprocessing={},device_id='victoria-den-jetson',engine_epoch='test',sha256='a'*64)
                processing={**profile,'artifact_sha256':'a'*64}
                result=dict(schema_version=1,frame_id='f1',coordinate_space='source_pixels',objects=[],boxes=[],processing=processing)
                receipt=dict(session_id=sid,site_id='victoria',camera_id='den',mode='saved_session',
                    time_basis='encoded_video',exposure_mapping='unverified',profile=profile,
                    results=[dict(source=source,result=result,worker_wall_ms=12)])
                path=root/'result.json';path.write_text(json.dumps(receipt))
                self.assertEqual(import_receipt(store,path,'A'),{'imported':1})
                self.assertEqual(import_receipt(store,path,'A'),{'already_imported':True})
                self.assertEqual(store.outputs_at(sid,999),[])
                output=store.outputs_at(sid,1000)[0]['payload']
                self.assertEqual(output['run_id'],'detectorA')
                self.assertIsNone(output['arm_snapshot']['frame']['captured_at'])
                self.assertEqual(output['arm_snapshot']['frame']['session_time_s'],1)
                self.assertTrue(output['arm_snapshot']['layers']['boxes']['available'])
                self.assertIsNone(output['arm_snapshot']['timing']['live_latency_ms'])
                # A second column is a real placement comparison, not the same
                # Jetson receipt relabeled as LA or a changed-precision model.
                with self.assertRaises(ValueError):import_receipt(store,path,'B')
                profile['device_id']='echo-la-rtx-pro-6000'
                processing['device_id']='echo-la-rtx-pro-6000'
                profile['precision']='fp16'
                path.write_text(json.dumps(receipt))
                with self.assertRaises(ValueError):import_receipt(store,path,'B')
                profile['precision']='fp32'
                path.write_text(json.dumps(receipt))
                self.assertEqual(import_receipt(store,path,'B'),{'imported':1})
                self.assertEqual({r['payload']['run_id'] for r in store.outputs_at(sid,1000)},{'detectorA','detectorB'})
                receipt['results'][0]['source']['sha256']='b'*64
                path.write_text(json.dumps(receipt))
                with self.assertRaises(ValueError):import_receipt(store,path,'B')
                with self.assertRaises(LabError):store.add_media(sid,b'x','image/png',{})
            finally:store.close()


if __name__=='__main__':unittest.main()
