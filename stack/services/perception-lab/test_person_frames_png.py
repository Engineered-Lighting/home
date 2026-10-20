"""New person protocol reads exact retained PNG/JPEG without changing legacy."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from config import Settings
from store import Store
from qwen_live import RegisteredPreviewFrames, FramesUnavailable
from qwen_person_live import PersonFrames


class PersonPngFramesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='person-png-original-')
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0), clock=lambda: 100.)
        self.addCleanup(self.store.close)
        session,_ = self.store.create_session('den','detection','Authored PNG','authored-png')
        self.store.transition(session['id'],'starting');self.store.transition(session['id'],'recording')
        self.session = self.store.get_session(session['id'])

    def add(self, fmt='PNG', mime='image/png'):
        output = io.BytesIO();Image.new('RGB',(640,360),(30,50,70)).save(output,format=fmt)
        raw = output.getvalue()
        item = self.store.add_media(self.session['id'],raw,mime,dict(site_id='victoria',camera_id='den',
            frame_id='authored-png',capture_epoch='authored-capture',stream='rgb',width=640,height=360,
            observed_at=99.8,available_at=99.9,valid_until=102.3,clock_mapping_status='verified',
            clock_mapping_id='authored-clock',uncertainty_seconds=.01))
        return item,raw

    def test_durable_png_is_selected_and_read_byte_for_byte_legacy_remains_jpeg_only(self):
        _,raw = self.add()
        source = PersonFrames(self.store)
        frames = source.candidates(self.session,100.)
        self.assertEqual(len(frames),1)
        self.assertEqual(frames[0]['mime_type'],'image/png')
        self.assertEqual(source.read(self.session,frames[0]),raw)
        self.assertEqual(frames[0]['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(RegisteredPreviewFrames(self.store).candidates(self.session,100.),[])

    def test_declared_png_with_jpeg_bytes_is_refused(self):
        self.add(fmt='JPEG')
        source = PersonFrames(self.store)
        with self.assertRaises(FramesUnavailable):source.read(self.session,source.candidates(self.session,100.)[0])

    def test_changed_retained_original_cannot_be_read(self):
        item,raw = self.add()
        source = PersonFrames(self.store);frame = source.candidates(self.session,100.)[0]
        self.store.media_path(self.session['id'],item['id']).write_bytes(raw+b'changed')
        with self.assertRaises(FramesUnavailable):source.read(self.session,frame)

    def test_future_available_original_is_not_a_candidate(self):
        self.add()
        self.assertEqual(PersonFrames(self.store).candidates(self.session,99.85),[])


if __name__ == '__main__':unittest.main()
