import json
import os
import tempfile
import unittest
from pathlib import Path
from shared_caption import read_caption


class CaptionTests(unittest.TestCase):
    def test_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'caption.json'
            self.assertEqual(read_caption(path, 'victoria', 'den')['status'], 'missing')
            value = dict(schema_version=1, site_id='victoria', camera_id='den', processing_site_id='echo',
                         model='qwen3-vl-30b', request_id='one', frame_id='frame', text='A room.',
                         observed_at=1000, generated_at=1001, valid_until=1180,
                         timestamp_semantics='Validated source observation time', uncertainty_seconds=0.5)
            def write():
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            write()
            self.assertEqual(read_caption(path, 'victoria', 'den', 1002)['status'], 'current')
            self.assertEqual(read_caption(path, 'victoria', 'den', 1180)['status'], 'stale')
            self.assertEqual(read_caption(path, 'echo', 'den', 1002)['status'], 'invalid')
            self.assertEqual(read_caption(path, 'victoria', 'den', 999)['status'], 'invalid')
            value['valid_until'] = 1181
            write()
            self.assertEqual(read_caption(path, 'victoria', 'den', 1002)['status'], 'invalid')
            path.write_bytes(b'x' * 16385)
            self.assertEqual(read_caption(path, 'victoria', 'den')['status'], 'invalid')
            if os.name == 'posix':
                write()
                path.chmod(0o644)
                self.assertEqual(read_caption(path, 'victoria', 'den')['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
