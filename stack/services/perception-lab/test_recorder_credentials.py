import shlex
import subprocess
import unittest
from unittest.mock import Mock,patch
import test_paired_runtime as fixtures
from recorder import StreamCopySupervisor,StreamSource,ffconcat_input

URL='rtsp://reader:fixture-only-secret@127.0.0.1:8554/den/rgb'


def entry(listing):
    header,line=listing.splitlines()
    assert header=='ffconcat version 1.0'
    # ffconcat quoting (av_get_token) matches POSIX single quotes and backslash.
    keyword,value=shlex.split(line)
    assert keyword=='file'
    return value


class RecorderCredentialTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def supervisor(self,process):
        source=StreamSource('source','den',URL,enabled=True,admitted=True,
            codec='avc1.640028',codec_validation_id='fixture')
        return StreamCopySupervisor(self.store,source,popen=Mock(return_value=process))

    def test_reader_url_reaches_ffmpeg_only_through_stdin(self):
        process=Mock();process.poll.return_value=None
        supervisor=self.supervisor(process)
        with patch('recorder.threading.Thread'):
            self.assertEqual(supervisor.start(self.session)['state'],'recording')
        (argv,),kwargs=supervisor.popen.call_args
        self.assertFalse([arg for arg in argv if 'fixture-only-secret' in arg or 'reader' in arg])
        self.assertEqual(argv[argv.index('-i')+1],'pipe:0')
        self.assertEqual(argv[argv.index('-f')+1],'concat')
        self.assertNotIn('-rtsp_transport',argv)
        self.assertEqual(kwargs['stdin'],subprocess.PIPE)
        (written,),_=process.stdin.write.call_args
        self.assertEqual(entry(written.decode('utf-8')),URL+'?tcp')
        process.stdin.close.assert_called_once()

    def test_stream_copy_output_is_unchanged(self):
        argv=self.supervisor(Mock()).command('staging')
        tail=argv[argv.index('-map'):]
        self.assertEqual(tail[:tail.index('-hls_segment_filename')],['-map','0:v:0','-an','-c:v','copy','-f','hls',
            '-hls_time','10','-hls_playlist_type','event','-hls_list_size','0','-hls_segment_type','fmp4',
            '-hls_flags','temp_file','-hls_fmp4_init_filename','init.mp4'])

    def test_ffmpeg_that_exits_before_reading_is_reported(self):
        process=Mock();process.poll.return_value=1
        process.stdin.write.side_effect=BrokenPipeError()
        supervisor=self.supervisor(process)
        with patch('recorder.threading.Thread') as thread:
            outcome=supervisor.start(self.session)
        self.assertEqual(outcome,{'state':'blocked','reason':'encoded_source_process_exited'})
        process.stdin.close.assert_called_once()
        thread.assert_not_called()

    def test_list_entry_quotes_the_url_and_requests_tcp(self):
        for url,expected in (
                ("rtsp://reader:it's\\x@127.0.0.1:8554/den/rgb","rtsp://reader:it's\\x@127.0.0.1:8554/den/rgb?tcp"),
                ('rtsp://127.0.0.1:8554/den/rgb?a=b','rtsp://127.0.0.1:8554/den/rgb?a=b&tcp')):
            with self.subTest(url=url):
                self.assertEqual(entry(ffconcat_input(url)),expected)


if __name__=='__main__': unittest.main()
