import unittest
from unittest.mock import patch
from pathlib import Path
import test_paired_runtime as fixtures
from store import Store,LabError


class SessionErasureTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    def stop(self):
        sid=self.session['id']
        self.store.save_annotation(sid,'label',{'at_ms':0,'label':'reading','origin':'human'},0,'fixture')
        self.store.transition(sid,'stopping')
        return self.store.transition(sid,'stopped')
    def test_erases_local_records_and_refuses_reads_writes_and_replay(self):
        stopped=self.stop();sid=stopped['id']
        result=self.store.delete_session(sid,stopped['revision'],erase_records=True)
        self.assertTrue(result['deleted'])
        self.assertEqual(self.store.list_sessions(),[])
        for table in ('media','annotations','events','commands','jobs'):
            self.assertEqual(self.store.db.execute(f'SELECT COUNT(*) FROM {table} WHERE session_id=?',(sid,)).fetchone()[0],0)
        for action in (lambda:self.store.get_session(sid),lambda:self.store.list_events(sid),
                lambda:self.store.command_result(sid,'late',{},{}),
                lambda:self.store.save_annotation(sid,'late',{'at_ms':0,'label':'working','origin':'human'},0,'fixture'),
                lambda:self.store.create_session('den','detection','Fixture','request1')):
            with self.assertRaises(LabError):action()
        self.assertEqual(self.store.db.execute('SELECT state FROM session_erasures WHERE session_id=?',(sid,)).fetchone()[0],'completed')
    def test_interrupted_erasure_is_inaccessible_then_recovers(self):
        stopped=self.stop();sid=stopped['id']
        with patch.object(Path,'unlink',side_effect=OSError('fixture failure')),self.assertRaises(OSError):
            self.store.delete_session(sid,stopped['revision'],erase_records=True)
        with self.assertRaises(LabError):self.store.get_session(sid)
        settings=self.store.settings;self.store.close();self.store=Store(settings,clock=lambda:101)
        self.assertEqual(self.store.list_sessions(),[])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM annotations').fetchone()[0],0)
    def test_external_copies_block_before_local_deletion(self):
        stopped=self.stop();sid=stopped['id']
        from store import encode
        with self.store.transaction() as db:
            db.execute('CREATE TABLE remote_specialist_dispatch(request_id TEXT,reference TEXT)')
            db.execute('INSERT INTO remote_specialist_dispatch VALUES(?,?)',('remote',encode({'session_id':sid})))
        with self.assertRaises(LabError) as error:self.store.delete_session(sid,stopped['revision'],erase_records=True)
        self.assertEqual(error.exception.code,'external_erasure_required')
        self.assertTrue(self.store.media_path(sid,self.media['id']).exists())
        self.assertEqual(len(self.store.list_annotations(sid)),1)

    def test_registered_staging_copies_are_removed_but_unknown_files_block(self):
        stopped=self.stop();sid=stopped['id']
        staging=self.store.root/'staging'/sid;staging.mkdir(parents=True)
        (staging/'init.mp4').write_bytes(b'fixture')
        (staging/'00000001.m4s.tmp').write_bytes(b'partial fixture')
        self.store.delete_session(sid,stopped['revision'],erase_records=True)
        self.assertFalse(staging.exists())

    def test_runtime_refuses_live_local_user_then_releases_only_erased_session(self):
        from service_runtime import ServiceRuntime
        from controller import Controller
        from concurrent.futures import Future
        from types import SimpleNamespace
        import threading
        stopped=self.stop();sid=stopped['id']
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None)
        pending=Future()
        worker=SimpleNamespace(closed=True,lock=threading.RLock(),futures={pending})
        runtime.runners[sid]=worker
        runtime.reasoning_errors['another-session']='preserve'
        controller=Controller(self.store,runtime)
        with self.assertRaises(LabError):controller.delete(sid,stopped['revision'],erase_records=True)
        self.assertTrue(self.store.media_path(sid,self.media['id']).exists())
        pending.set_result(None)
        self.assertTrue(controller.delete(sid,stopped['revision'],erase_records=True)['deleted'])
        self.assertNotIn(sid,runtime.runners)
        self.assertEqual(runtime.reasoning_errors['another-session'],'preserve')
        self.assertEqual(runtime.reap_finished_sessions()['closed_sessions'],[])
        self.assertTrue(runtime.close()['drained'])


if __name__=='__main__':unittest.main()
