import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from config import Settings
from store import Store


class OffLoopHandlerTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import create_app
        self.directory=tempfile.TemporaryDirectory()
        token='t'*32
        settings=Settings(storage_root=Path(self.directory.name),min_free_bytes=0,cameras=({'id':'den'},),
            token_grants=({'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'site_id':'victoria',
                           'subject':'fixture','capabilities':['read']},))
        self.store=Store(settings,clock=lambda:100.0)
        self.session,_=self.store.create_session('den','detection','Fixture','request1')
        self.store.transition(self.session['id'],'starting');self.store.transition(self.session['id'],'recording')
        self.media=self.store.add_media(self.session['id'],b'fixture-bytes','image/jpeg',{'fixture':True})
        self.client=TestClient(create_app(settings,store=self.store))
        self.headers={'Authorization':'Bearer '+token}
        self.calls=[]
        original=self.store.get_session
        def recorded(sid):
            try:
                asyncio.get_running_loop();self.calls.append('event_loop')
            except RuntimeError:
                self.calls.append('worker_thread')
            return original(sid)
        self.store.get_session=recorded

    def tearDown(self):
        self.client.close()
        self.store.close()
        self.directory.cleanup()

    def test_detail_list_findings_and_media_reads_run_off_the_event_loop(self):
        sid,mid=self.session['id'],self.media['id']
        base='/api/perception-lab/v1/sessions'
        for path in (base,base+'/'+sid,base+'/'+sid+'/findings',base+'/'+sid+'/events',
                     base+'/'+sid+'/media',base+'/'+sid+'/media/'+mid+'?generation=0'):
            self.calls.clear()
            response=self.client.get(path,headers=self.headers)
            self.assertEqual(response.status_code,200,path)
            self.assertTrue(self.calls,path)
            self.assertEqual(set(self.calls),{'worker_thread'},path)
        self.assertEqual(response.content,b'fixture-bytes')

    def test_media_integrity_failure_is_still_reported(self):
        self.store.media_path(self.session['id'],self.media['id']).write_bytes(b'tampered-bytes')
        response=self.client.get('/api/perception-lab/v1/sessions/'+self.session['id']+'/media/'+self.media['id']+'?generation=0',
                                 headers=self.headers)
        self.assertEqual((response.status_code,response.json()['error']['code']),(409,'media_integrity'))


if __name__=='__main__':unittest.main()
