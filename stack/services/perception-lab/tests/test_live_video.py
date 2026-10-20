import base64
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_video import FragmentParser, LiveVideo, MAX_MEDIA
from recorder import StreamSource
from store import LabError


def box(kind, body=b'fixture'):
    return (len(body)+8).to_bytes(4,'big')+kind+body


class LiveVideoTests(unittest.TestCase):
    def test_split_boxes_and_invalid_sizes(self):
        parser=FragmentParser()
        init=box(b'ftyp')+box(b'moov')
        fragment=box(b'moof')+box(b'mdat')
        results=[]
        for byte in init+fragment:
            results.extend(parser.feed(bytes([byte])))
        self.assertEqual(results,[fragment])
        self.assertEqual(parser.init,init)
        for bad in [b'\0\0\0\0mdat', (MAX_MEDIA+1).to_bytes(4,'big')+b'mdat',box(b'mdat')]:
            with self.assertRaises(ValueError):FragmentParser().feed(bad)

    def test_fake_pipe_remux_is_bounded_and_process_is_drained(self):
        process=Mock()
        process.poll.return_value=None
        process.stdout.fileno.return_value=123
        source=StreamSource('oak','den','rtsp://127.0.0.1/test',codec='avc1.4d0028')
        live=LiveVideo(source,'victoria',popen=Mock(return_value=process))
        live.last_request=time.monotonic()
        observed=[]
        process.terminate.side_effect=lambda:observed.extend(list(live.fragments))
        init=box(b'ftyp')+box(b'moov')
        fragment=box(b'moof')+box(b'mdat')
        with patch('live_video.select.select',return_value=([process.stdout],[],[])), patch('live_video.os.read',side_effect=[init]+[fragment]*5+[b'']):
            live._run()
        self.assertEqual([x[0] for x in observed],[3,4,5])
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)
        process.stdout.close.assert_called_once()
        self.assertEqual(list(live.fragments),[])

    def test_cursor_gap_shared_reader_shutdown_and_bound(self):
        popen=Mock(side_effect=AssertionError('No real process'))
        source=StreamSource('oak','den','rtsp://127.0.0.1/test',codec='avc1.4d0028')
        live=LiveVideo(source,'victoria',popen=popen)
        popen.assert_not_called()
        with patch.object(live,'_run',side_effect=lambda:live.stop.wait(2)) as worker:
            with self.assertRaises(LabError):live.read()
            with live.lock:
                live.init=box(b'ftyp')+box(b'moov')
                live.sequence=5
                for n in [3,4,5]:live.fragments.append((n,b'x'*100,time.monotonic()))
            first=live.read()
            self.assertTrue(first['reset'])
            self.assertEqual([f['sequence'] for f in first['fragments']],[5])
            self.assertEqual(base64.b64decode(first['init_base64']),live.init)
            normal=live.read(3,first['generation'])
            self.assertFalse(normal['reset'])
            self.assertEqual([f['sequence'] for f in normal['fragments']],[4,5])
            self.assertEqual(live.read(5,first['generation'])['fragments'],[])
            self.assertTrue(live.read(1,first['generation'])['reset'])
            self.assertTrue(live.read(6,first['generation'])['reset'])
            self.assertNotIn('observed_at',normal)
            with live.lock:live.fragments[-1]=(5,b'x',time.monotonic()-5)
            with self.assertRaises(LabError):live.read()
            self.assertTrue(live.close()['drained'])
            worker.assert_called_once()
        with self.assertRaises(LabError):live.read()
        cmd=live.command()
        self.assertEqual(cmd[cmd.index('-c:v')+1],'copy')
        self.assertEqual(cmd[-1],'pipe:1')
        self.assertNotIn('-vf',cmd)
        for args in [(True,None),(-1,None),(0,'bad')]:
            with self.assertRaises(LabError):live.read(*args)

if __name__=='__main__':unittest.main()
