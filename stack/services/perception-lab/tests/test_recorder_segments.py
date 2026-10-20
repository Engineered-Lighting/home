import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from recorder import parse_playlist,StreamSource,StreamCopySupervisor
from store import LabError
from test_controller_store import StoreTests


PLAYLIST='#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:10.000,\n00000000.m4s\n#EXTINF:9.500,\n00000001.m4s\n'


class PlaylistTests(unittest.TestCase):
    def test_fmp4_sequence_and_durations(self):
        values=parse_playlist(PLAYLIST)
        self.assertEqual(values[1]["start_seconds"],10)
        self.assertEqual(values[1]["end_seconds"],19.5)
    def test_paths_urls_reorder_and_invalid_duration_rejected(self):
        for value in (PLAYLIST.replace("00000000.m4s","../secret"),PLAYLIST.replace("00000000.m4s","https://example/clip"),PLAYLIST.replace("00000000.m4s","00000002.m4s"),PLAYLIST.replace("10.000","nan"),PLAYLIST.replace('URI="init.mp4"','URI="../init.mp4"'),PLAYLIST+'#EXTINF:10,\n'):
            with self.subTest(value=value),self.assertRaises(LabError): parse_playlist(value)


class SegmentCommitTests(StoreTests):
    # Only reuse fixture setup/helper methods; separate test loader below avoids
    # inheriting all StoreTests in discovery.
    def test_closed_fragments_keep_init_and_media_distinct(self):
        s=self.start()
        source=StreamSource("source","den","rtsp://127.0.0.1:8554/den",codec="avc1.640028",codec_validation_id="commission-1")
        supervisor=StreamCopySupervisor(self.store,source)
        supervisor.sid=s["id"]
        supervisor.directory=self.store.root/"segments-fixture"
        supervisor.directory.mkdir()
        (supervisor.directory/"index.m3u8").write_text(PLAYLIST,encoding="utf-8")
        (supervisor.directory/"init.mp4").write_bytes(b"fixture-init")
        (supervisor.directory/"00000000.m4s").write_bytes(b"fixture-fragment-a")
        (supervisor.directory/"00000001.m4s").write_bytes(b"fixture-fragment-b")
        (supervisor.directory/"00000002.m4s.tmp").write_bytes(b"unfinalized")
        supervisor._collect()
        items=self.store.list_media(s["id"])["items"]
        self.assertEqual(len(items),3)
        init=next(x for x in items if x["metadata"]["role"]=="init")
        segments=[x for x in items if x["metadata"]["role"]=="media"]
        self.assertEqual({x["metadata"]["initialization_id"] for x in segments},{init["id"]})
        self.assertTrue(all(x["mime_type"]=="video/mp4" for x in items))
        self.assertTrue(all(x["metadata"]["clock_mapping"]=="unverified" for x in items))
        supervisor._collect()
        self.assertEqual(len(self.store.list_media(s["id"])["items"]),3)
        self.assertTrue((supervisor.directory/"00000002.m4s.tmp").exists())


def load_tests(loader,tests,pattern):
    suite=loader.loadTestsFromTestCase(PlaylistTests)
    suite.addTest(SegmentCommitTests("test_closed_fragments_keep_init_and_media_distinct"))
    return suite


if __name__=="__main__": unittest.main()
