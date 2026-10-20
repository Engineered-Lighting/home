import io
import json
import tarfile
import unittest
import test_paired_runtime as fixtures
from portable_export import archive_chunks,CHUNK
from store import LabError


class PortableTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def stop(self):
        self.store.transition(self.session['id'],'stopping')
        self.store.transition(self.session['id'],'stopped')

    def test_stream_round_trip_preserves_media_and_reviewed_labels(self):
        sid=self.session['id']
        payload=b'synthetic video segment'*20000
        self.store.add_media(sid,payload,'video/mp4',{'fixture':True,'clock_mapping_status':'unverified'})
        self.store.save_annotation(sid,'label',{'at_ms':0,'end_ms':1000,'label':'reading',
            'origin':'human','reviewed':True},0,'fixture')
        self.stop()
        chunks=list(archive_chunks(self.store,sid,authorize=lambda:True))
        self.assertLessEqual(max(map(len,chunks)),CHUNK)
        with tarfile.open(fileobj=io.BytesIO(b''.join(chunks))) as archive:
            complete=json.load(archive.extractfile('COMPLETE.json'))
            self.assertEqual(complete['media_count'],2)
            self.assertFalse(complete['training_ready'])
            records=[]
            for entry in archive.getnames():
                self.assertFalse(entry.startswith('/') or '..' in entry)
                if entry.startswith('metadata/'):
                    records.extend(json.load(archive.extractfile(entry))['records'])
            annotation=next(r['value'] for r in records if r['kind']=='annotation')
            self.assertEqual(annotation['label_status'],'reviewed_human')
            video=next(r['value'] for r in records if r['kind']=='media' and r['value']['mime_type']=='video/mp4')
            self.assertEqual(archive.extractfile(video['archive_path']).read(),payload)
            self.assertEqual(video['metadata']['clock_mapping_status'],'unverified')

    def test_revocation_and_deletion_stop_stream(self):
        self.stop(); sid=self.session['id']; allowed=[True]
        stream=archive_chunks(self.store,sid,authorize=lambda:allowed[0]); next(stream)
        allowed[0]=False
        with self.assertRaisesRegex(LabError,'authorized'): next(stream)
        stream=archive_chunks(self.store,sid,authorize=lambda:True); next(stream)
        self.store.delete_session(sid,self.store.get_session(sid)['revision'])
        with self.assertRaisesRegex(LabError,'deleted'): next(stream)

    def test_corrupt_media_and_budget_never_produce_completion(self):
        self.stop(); sid=self.session['id']
        self.store.media_path(sid,self.media['id']).write_bytes(b'corrupt')
        collected=[]
        with self.assertRaisesRegex(LabError,'truncated'):
            for chunk in archive_chunks(self.store,sid,authorize=lambda:True): collected.append(chunk)
        self.assertFalse(b'COMPLETE.json\0' in b''.join(collected))
        with self.assertRaisesRegex(LabError,'budget'):
            list(archive_chunks(self.store,sid,authorize=lambda:True,maximum_bytes=1024))

    def test_annotation_edit_mid_stream_prevents_finalization(self):
        self.stop(); sid=self.session['id']; edited=False; collected=[]
        with self.assertRaisesRegex(LabError,'changed'):
            for chunk in archive_chunks(self.store,sid,authorize=lambda:True):
                collected.append(chunk)
                if not edited:
                    edited=True
                    self.store.save_annotation(sid,'late',{'at_ms':0,'label':'working','origin':'human'},0,'fixture')
        self.assertNotIn(b'COMPLETE.json\0',b''.join(collected))


if __name__=='__main__': unittest.main()
