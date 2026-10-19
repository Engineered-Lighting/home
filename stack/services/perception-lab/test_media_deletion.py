"""Deletion recovery uses temporary synthetic objects, never household media."""
from pathlib import Path
from unittest.mock import patch
import unittest
import test_paired_runtime as fixtures
from store import Store,LabError
from exports import export_page
from portable_export import archive_chunks


class MediaDeletionTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def stop(self):
        sid=self.session['id']
        self.store.transition(sid,'stopping')
        return self.store.transition(sid,'stopped')

    def test_partial_unlink_is_hidden_and_resumed_after_controller_restart(self):
        sid=self.session['id']
        second=self.store.add_media(sid,b'second fixture','image/jpeg',{})
        stopped=self.stop()
        unrelated,_=self.store.create_session('den','detection','Other','request2')
        original=Path.unlink
        calls=[]
        def fail_second(path,*args,**kw):
            calls.append(path)
            if len(calls)==2:raise OSError('fixture interrupted deletion')
            return original(path,*args,**kw)
        with patch.object(Path,'unlink',fail_second),self.assertRaises(OSError):
            self.store.delete_session(sid,stopped['revision'])
        self.assertEqual(self.store.get_session(sid)['reason'],'media_deletion_pending')
        self.assertEqual(self.store.list_media(sid)['items'],[])
        with self.assertRaises(LabError) as error:self.store.get_media(sid,second['id'])
        self.assertEqual(error.exception.code,'media_deleted')
        with self.assertRaises(LabError) as error:export_page(self.store,sid)
        self.assertEqual(error.exception.code,'deletion_pending')
        with self.assertRaises(LabError):next(archive_chunks(self.store,sid,authorize=lambda:True))
        settings=self.store.settings
        self.store.close()
        self.store=Store(settings,clock=lambda:101)
        self.assertEqual(self.store.get_session(sid)['reason'],'media_deleted_by_user')
        self.assertEqual(self.store.get_session(sid)['generation'],1)
        self.assertFalse(self.store.media_path(sid,second['id']).exists())
        self.assertEqual(self.store.get_session(unrelated['id'])['name'],'Other')
        kinds=[e['kind'] for e in self.store.list_events(sid)]
        self.assertEqual(kinds.count('media.deletion_requested'),1)
        self.assertEqual(kinds.count('media.deleted'),1)
        current=self.store.get_session(sid)
        self.assertEqual(self.store.delete_session(sid,current['revision']),current)

    def test_shared_detector_uncertainty_blocks_deletion_even_without_parent(self):
        sid=self.session['id']
        with self.store.transaction() as db:
            db.execute('CREATE TABLE shared_detector_stage(id TEXT,session_id TEXT,state TEXT)')
            db.execute("INSERT INTO shared_detector_stage VALUES('detector',?,'indeterminate')",(sid,))
        stopped=self.stop()
        with self.assertRaises(LabError) as error:self.store.delete_session(sid,stopped['revision'])
        self.assertEqual(error.exception.code,'execution_pending')
        self.assertTrue(self.store.media_path(sid,self.media['id']).exists())
        self.assertEqual(self.store.get_session(sid)['generation'],0)
        self.assertIsNone(self.store.db.execute('SELECT * FROM media_deletions').fetchone())


if __name__=='__main__':unittest.main()
