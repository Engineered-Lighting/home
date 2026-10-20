import json
from pathlib import Path
import tempfile
import unittest
from home_shared.caption_publication import CaptionPublication


class CaptionPublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'shared-caption.json'
        self.writer = CaptionPublication(self.path, model_revision='pinned-model')
        self.value = dict(site_id='victoria', camera_id='den_oak', room_id='den', model_revision='pinned-model',
                          observed_at=1000, valid_until=1180, request_deadline=1015, uncertainty_seconds=.4,
                          description='An empty room.', request_id='request', frame_id='frame')

    def test_preserves_request_and_source_times_in_existing_reader_format(self):
        self.writer(self.value, generated_at=1002)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['camera_id'], 'den')
        self.assertEqual(saved['request_id'], 'request')
        self.assertEqual(saved['observed_at'], 1000)
        self.assertEqual(saved['valid_until'], 1180)
        self.assertEqual(saved['text'], 'An empty room.')
        self.assertEqual(list(self.path.parent.glob('.caption-*')), [])

    def test_wrong_site_model_or_late_result_cannot_replace_caption(self):
        self.writer(self.value, generated_at=1002)
        original = self.path.read_bytes()
        for patch in ({'site_id':'echo'}, {'model_revision':'other'}, {'valid_until':999}, {'request_deadline':1002}):
            with self.assertRaises(ValueError):
                self.writer({**self.value, **patch}, generated_at=1002)
            self.assertEqual(self.path.read_bytes(), original)
