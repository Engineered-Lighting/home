import unittest
import test_paired_runtime as fixtures


class EncodedTrackEndTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def test_final_fragment_is_global_and_only_announced_after_stop(self):
        sid=self.session['id']
        init=self.store.add_media(sid,b'init','video/mp4',{'role':'init'})
        early=self.store.add_media(sid,b'early','video/mp4',{'role':'media','start_seconds':0,'end_seconds':2})
        late=self.store.add_media(sid,b'late','video/mp4',{'role':'media','start_seconds':100,'end_seconds':102})
        self.assertFalse(self.store.list_media(sid)['encoded_track']['finalized'])
        self.store.transition(sid,'stopped')
        page=self.store.list_media(sid,kind='video',encoded_at_ms=1000,radius_ms=0)
        self.assertEqual({v['id'] for v in page['items']},{init['id'],early['id']})
        self.assertEqual(page['encoded_track'],{'finalized':True,'final_media_id':late['id'],'duration_seconds':102,'initialization_id':None})
        paged=self.store.list_media(sid,limit=1)
        self.assertIsNotNone(paged['next_cursor'])
        self.assertEqual(paged['encoded_track']['final_media_id'],late['id'])

    def test_stopped_session_without_video_does_not_invent_an_end(self):
        sid=self.session['id'];self.store.transition(sid,'stopped')
        self.assertEqual(self.store.list_media(sid)['encoded_track'],{'finalized':False,'final_media_id':None})


if __name__=='__main__':unittest.main()
