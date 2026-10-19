import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from recorder import StreamSource
from rtsp_preview import RTSPPreview
from store import LabError

class RTSPPreviewTests(unittest.TestCase):
    def test_demand_driven_shared_reader_and_receipt_time_only(self):
        popen=Mock(side_effect=AssertionError('No real process'))
        preview=RTSPPreview(StreamSource('oak','den','rtsp://127.0.0.1:8554/test'),'victoria',popen=popen)
        popen.assert_not_called()
        with patch.object(preview,'_run',side_effect=lambda:preview.stop.wait(2)) as worker:
            with self.assertRaises(LabError):preview.read()
            with preview.lock:preview.frame=(time.monotonic(),b'\xff\xd8fixture\xff\xd9',1)
            for _ in range(3):
                result=preview.read()
                self.assertIsNone(result['source_age_seconds'])
                self.assertEqual(result['timestamp_semantics'],'decode_receipt_only')
                self.assertNotIn('observed_at',result)
            self.assertTrue(preview.close()['drained'])
            worker.assert_called_once()
        with self.assertRaises(LabError):preview.read()

    def test_stale_decode_not_served_and_command_has_no_recording(self):
        preview=RTSPPreview(StreamSource('oak','den','rtsp://127.0.0.1:8554/test'),'victoria')
        with patch.object(preview,'_run',side_effect=lambda:preview.stop.wait(2)):
            with self.assertRaises(LabError):preview.read()
            with preview.lock:preview.frame=(time.monotonic()-4,b'\xff\xd8\xff\xd9',1)
            with self.assertRaises(LabError) as error:preview.read()
            self.assertEqual(error.exception.code,'preview_stale')
            self.assertTrue(preview.close()['drained'])
        command=preview.command()
        self.assertEqual(command[-1],'pipe:1')
        self.assertIn('fps=1,scale=640:360',command)
        self.assertNotIn('hls',command)
