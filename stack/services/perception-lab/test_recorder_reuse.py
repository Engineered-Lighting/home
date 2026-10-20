import unittest
from unittest.mock import Mock,patch
import test_paired_runtime as fixtures
from recorder import StreamCopySupervisor,StreamSource
from store import LabError


class RecorderReuseTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def supervisor(self):
        process=Mock();process.poll.return_value=None
        source=StreamSource('source','den','rtsp://127.0.0.1:8554/fixture',enabled=True,admitted=True,
            codec='avc1.640028',codec_validation_id='fixture')
        supervisor=StreamCopySupervisor(self.store,source,popen=Mock(return_value=process))
        supervisor.process=Mock();supervisor.process.poll.return_value=0
        supervisor.thread=Mock();supervisor.thread.is_alive.return_value=False
        supervisor.stop_event.set();supervisor.imported={'00000000.m4s'};supervisor.initialization_id='old-init'
        return supervisor

    def test_new_session_resets_completed_recorder_without_restarting_old_process(self):
        supervisor=self.supervisor();old=supervisor.process
        with patch('recorder.threading.Thread') as thread:
            outcome=supervisor.start(self.session)
        self.assertEqual(outcome['state'],'recording')
        self.assertFalse(supervisor.stop_event.is_set())
        self.assertEqual(supervisor.imported,set())
        self.assertIsNone(supervisor.initialization_id)
        self.assertEqual(supervisor.sid,self.session['id'])
        supervisor.popen.assert_called_once();thread.return_value.start.assert_called_once()
        old.terminate.assert_not_called();old.kill.assert_not_called()

    def test_active_process_or_monitor_cannot_be_replaced(self):
        for active_process,active_monitor in ((True,False),(False,True)):
            supervisor=self.supervisor()
            supervisor.process.poll.return_value=None if active_process else 0
            supervisor.thread.is_alive.return_value=active_monitor
            with self.assertRaises(LabError):supervisor.start(self.session)
            supervisor.popen.assert_not_called()
            self.assertEqual(supervisor.initialization_id,'old-init')

    def test_dead_monitor_does_not_prove_capture_process_stopped(self):
        supervisor=self.supervisor();supervisor.sid=self.session['id']
        supervisor.process.poll.return_value=None
        self.assertEqual(supervisor.stop(self.session),{
            'state':'indeterminate','reason':'recorder_termination_unconfirmed'})

    def test_failed_termination_marks_session_indeterminate(self):
        supervisor=self.supervisor();supervisor.sid=self.session['id']
        supervisor._terminate=Mock(side_effect=TimeoutError('fixture only'))
        supervisor._monitor()
        self.assertEqual(self.store.get_session(self.session['id'])['state'],'indeterminate')
        self.assertEqual(self.store.list_events(self.session['id'])[-1]['payload']['reason'],'recorder_termination_unconfirmed')

    def test_finalization_keeps_collection_failure_distinct_from_process_stop(self):
        from controller import Controller
        supervisor=self.supervisor();supervisor.sid=self.session['id']
        supervisor._collect=Mock(side_effect=ValueError('incomplete fixture segment'))
        supervisor._monitor()
        detail=Controller(self.store).detail(self.session['id'])
        self.assertEqual(detail['session']['state'],'stopped')
        self.assertEqual(detail['capture_status']['final_collection'],'failed')
        self.assertTrue(detail['capture_status']['process_terminal'])
        self.assertEqual(detail['capture_status']['capture_completeness'],'partial_or_unverified')
        self.assertEqual(detail['capture_status']['synchronization'],'unverified')

    def test_final_fragment_drain_is_bounded_and_does_not_reopen_capture(self):
        sid=self.session['id'];start=self.store.get_session(sid)['started_at']
        self.store.clock=lambda:start+self.store.settings.max_session_seconds+1
        metadata={'kind':'encoded_init','format':'fmp4'}
        with self.assertRaises(LabError):self.store.add_media(sid,b'fixture','video/mp4',metadata,'normal')
        self.store.transition(sid,'stopping')
        item=self.store.add_media(sid,b'fixture','video/mp4',metadata,'final',finalized_fragment=True)
        self.assertEqual(item['size'],7)
        with self.assertRaises(LabError):
            self.store.add_media(sid,b'fixture','image/jpeg',{},'wrong',finalized_fragment=True)
        self.store.clock=lambda:start+self.store.settings.max_session_seconds+30
        with self.assertRaises(LabError):
            self.store.add_media(sid,b'fixture','video/mp4',metadata,'late',finalized_fragment=True)

    def test_final_collection_requires_terminal_process(self):
        supervisor=self.supervisor();supervisor.process.poll.return_value=None
        with self.assertRaisesRegex(LabError,'termination'):
            supervisor._collect(final=True)


PLAYLIST='#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2.000,\n00000000.m4s\n#EXTINF:2.000,\n00000001.m4s\n'


class RecorderRaceTests(unittest.TestCase):
    """FFmpeg renames its .tmp segment and playlist while the monitor lists
    them. Under CPU pressure that window is visible: it must delay a segment,
    never end the recording as recorder_failure."""
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def supervisor(self):
        source=StreamSource('source','den','rtsp://127.0.0.1:8554/fixture',enabled=True,admitted=True,
            codec='avc1.640028',codec_validation_id='fixture')
        supervisor=StreamCopySupervisor(self.store,source)
        supervisor.sid=self.session['id']
        supervisor.directory=self.store.root/'staging'/self.session['id']
        supervisor.directory.mkdir(parents=True)
        (supervisor.directory/'index.m3u8').write_text(PLAYLIST,encoding='utf-8')
        (supervisor.directory/'init.mp4').write_bytes(b'fixture-init')
        (supervisor.directory/'00000000.m4s').write_bytes(b'fixture-a')
        # Listed, but its rename from 00000001.m4s.tmp has not landed yet.
        (supervisor.directory/'00000001.m4s.tmp').write_bytes(b'fixture-b')
        return supervisor

    def committed(self):
        return sorted(x['metadata'].get('filename') or 'init' for x in self.store.list_media(self.session['id'])['items'] if x['mime_type']=='video/mp4')

    def test_listed_segment_not_yet_renamed_is_retried_in_order(self):
        supervisor=self.supervisor()
        supervisor._collect()
        self.assertEqual(self.committed(),['00000000.m4s','init'])
        (supervisor.directory/'00000001.m4s.tmp').rename(supervisor.directory/'00000001.m4s')
        supervisor._collect()
        self.assertEqual(self.committed(),['00000000.m4s','00000001.m4s','init'])

    def test_a_missing_segment_is_retried_for_a_bounded_time_only(self):
        supervisor=self.supervisor()
        clock=[100.0];supervisor.monotonic=lambda:clock[0]
        supervisor._collect()
        clock[0]=104.9
        supervisor._collect()  # still within the grace: retried, not failed
        self.assertEqual(self.committed(),['00000000.m4s','init'])
        clock[0]=105.0
        with self.assertRaises(LabError) as caught:
            supervisor._collect()
        self.assertEqual(caught.exception.code,'segment_missing')

    def test_a_later_segment_on_disk_proves_the_listed_one_missing_at_once(self):
        supervisor=self.supervisor()
        clock=[100.0];supervisor.monotonic=lambda:clock[0]
        # FFmpeg renames in order: 2 landed, so 1 will not.
        (supervisor.directory/'00000002.m4s').write_bytes(b'fixture-c')
        with self.assertRaises(LabError) as caught:
            supervisor._collect()
        self.assertEqual(caught.exception.code,'segment_missing')

    def test_a_segment_that_lands_clears_its_miss_record(self):
        supervisor=self.supervisor()
        clock=[100.0];supervisor.monotonic=lambda:clock[0]
        supervisor._collect()
        self.assertIn('00000001.m4s',supervisor.missing)
        (supervisor.directory/'00000001.m4s.tmp').rename(supervisor.directory/'00000001.m4s')
        clock[0]=200.0
        supervisor._collect()
        self.assertEqual(supervisor.missing,{})
        self.assertEqual(self.committed(),['00000000.m4s','00000001.m4s','init'])

    def test_monitor_ends_a_stalled_segment_as_segment_missing_not_success(self):
        supervisor=self.supervisor()
        clock=[100.0];supervisor.monotonic=lambda:clock[0]
        process=Mock();process.poll.return_value=None
        process.terminate.side_effect=lambda:setattr(process.poll,'return_value',0)
        supervisor.process=process
        collect=supervisor._collect
        def later(final=False):
            clock[0]+=3
            collect(final=final)
        supervisor._collect=later
        supervisor._monitor()
        finalized=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='recording.finalized'][-1]
        self.assertEqual(finalized['reason'],'segment_missing')
        self.assertEqual(finalized['capture_completeness'],'partial_or_unverified')
        self.assertEqual(finalized['final_collection'],'failed')

    def test_a_listed_segment_still_missing_after_termination_fails_final_collection(self):
        supervisor=self.supervisor()
        supervisor.process=Mock();supervisor.process.poll.return_value=0
        with self.assertRaises(LabError) as caught:
            supervisor._collect(final=True)
        self.assertEqual(caught.exception.code,'segment_missing')

    def test_staging_bytes_tolerate_an_entry_renamed_while_listing(self):
        supervisor=self.supervisor()
        import os
        real=os.scandir
        class Vanishing:
            def __init__(inner,entry):inner.entry=entry
            def is_file(inner,**kw):return True
            def stat(inner,**kw):raise FileNotFoundError(inner.entry.path)
        def scandir(path):
            listing=list(real(path))
            class Listing:
                def __enter__(inner):return iter([Vanishing(e) if e.name.endswith('.tmp') else e for e in listing])
                def __exit__(inner,*_):pass
            return Listing()
        with patch('recorder.os.scandir',side_effect=scandir):
            expected=sum((supervisor.directory/name).stat().st_size for name in ('index.m3u8','init.mp4','00000000.m4s'))
            self.assertEqual(supervisor._staging_bytes(),expected)

    def test_capture_continues_through_the_race_and_stops_as_user_stop(self):
        supervisor=self.supervisor()
        process=Mock();process.poll.return_value=None
        def terminate():
            # FFmpeg finishes the pending rename as it exits.
            (supervisor.directory/'00000001.m4s.tmp').rename(supervisor.directory/'00000001.m4s')
            process.poll.return_value=0
        process.terminate.side_effect=terminate
        supervisor.process=process
        collect=supervisor._collect
        polls=[]
        def counted(final=False):
            collect(final=final)
            if not final:
                polls.append(1)
                if len(polls)==2:supervisor.stop_event.set()
        supervisor._collect=counted
        supervisor._monitor()
        finalized=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='recording.finalized'][-1]
        self.assertEqual(finalized['reason'],'user_stop')
        self.assertEqual(finalized['final_collection'],'committed_listed_segments')
        self.assertEqual(finalized['committed_segments'],2)
        self.assertNotIn('failure_class',finalized)
        self.assertEqual(self.store.get_session(self.session['id'])['state'],'stopped')

    def test_unexpected_failure_is_reported_with_its_class_never_as_success(self):
        supervisor=self.supervisor()
        process=Mock();process.poll.return_value=None
        process.terminate.side_effect=lambda:setattr(process.poll,'return_value',0)
        supervisor.process=process
        supervisor._collect=Mock(side_effect=[PermissionError('fixture'),None])
        supervisor._monitor()
        finalized=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='recording.finalized'][-1]
        self.assertEqual(finalized['reason'],'recorder_failure')
        self.assertEqual(finalized['failure_class'],'PermissionError')
        self.assertEqual(finalized['capture_completeness'],'partial_or_unverified')


class RequestedStopTests(unittest.TestCase):
    """A runtime whose stop returns before the recorder drains (the managed
    pair) lets Controller.stop move the session to 'stopping' before the
    recorder's own stop signal. That is the user's Stop: it must finalize as
    user_stop with complete capture, never as recording_state_changed
    (production session cf6a7dc6, 2026-09-28)."""
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def supervisor(self):
        supervisor=RecorderRaceTests.supervisor(self)
        (supervisor.directory/'00000001.m4s.tmp').rename(supervisor.directory/'00000001.m4s')
        self.store.reconcile_storage()  # the fixture wrote staging outside the Store
        self.store.track_staging(self.session['id'],supervisor.directory)
        process=Mock();process.poll.return_value=None
        process.terminate.side_effect=lambda:setattr(process.poll,'return_value',0)
        supervisor.process=process
        return supervisor

    def finalized(self):
        return [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='recording.finalized'][-1]

    def request_stop(self,requested=True):
        sid=self.session['id']
        if requested:
            self.store.append_event(sid,'recording.stop_requested',{'reason':'user_stop'},critical=True)
        self.store.transition(sid,'stopping','worker_cleanup_pending')

    def test_controller_stop_seen_as_stopping_finalizes_as_user_stop(self):
        supervisor=self.supervisor()
        collect=supervisor._collect
        def poll(final=False):
            collect(final=final)
            if not final and self.store.get_session(self.session['id'])['state']=='recording':
                self.request_stop()
        supervisor._collect=poll
        supervisor._monitor()
        finalized=self.finalized()
        self.assertEqual(finalized['reason'],'user_stop')
        self.assertEqual(finalized['final_collection'],'committed_listed_segments')
        self.assertEqual(finalized['capture_completeness'],'unverified')
        self.assertEqual(finalized['committed_segments'],2)
        session=self.store.get_session(self.session['id'])
        self.assertEqual((session['state'],session['reason']),('stopped','user_stop'))
        # FFmpeg terminated: its staging leftovers joined the exact total.
        self.assertNotIn(self.session['id'],self.store._active_staging)
        with self.store.lock:
            self.assertEqual(self.store.storage_usage(),self.store.scan_usage())
        self.assertEqual(self.store.accounting['drift_corrections'],1,'only the fixture reconcile')

    def test_stop_landing_between_the_state_check_and_an_import_is_still_user_stop(self):
        supervisor=self.supervisor()
        add=self.store.add_media
        def racing(*args,**kwargs):
            if not kwargs.get('finalized_fragment') and self.store.get_session(self.session['id'])['state']=='recording':
                self.request_stop()  # the import below is refused: 'conflict'
            return add(*args,**kwargs)
        with patch.object(self.store,'add_media',side_effect=racing):
            supervisor._monitor()
        finalized=self.finalized()
        self.assertEqual(finalized['reason'],'user_stop')
        self.assertEqual(finalized['final_collection'],'committed_listed_segments')
        self.assertEqual(finalized['capture_completeness'],'unverified')
        self.assertEqual(finalized['committed_segments'],2)

    def test_an_unrequested_state_change_is_still_reported_as_such(self):
        supervisor=self.supervisor()
        self.request_stop(requested=False)
        supervisor._monitor()
        finalized=self.finalized()
        self.assertEqual(finalized['reason'],'recording_state_changed')
        self.assertEqual(finalized['capture_completeness'],'partial_or_unverified')

    def test_in_process_stop_reports_the_recorded_reason_truthfully(self):
        expected={"controller_shutdown":"partial_or_unverified","quota_exceeded":"partial_or_unverified",
                  "duration_limit":"unverified","user_stop":"unverified"}
        for reason,completeness in expected.items():
            with self.subTest(reason=reason):
                self.tearDown();self.setUp()
                supervisor=self.supervisor()
                sid=self.session['id']
                collect=supervisor._collect
                def poll(final=False):
                    collect(final=final)
                    if not final and not supervisor.stop_event.is_set():
                        # Controller.stop records the reason, then the runtime
                        # signals the recorder in-process (session still recording).
                        self.store.append_event(sid,'recording.stop_requested',{'reason':reason},critical=True)
                        supervisor.stop_event.set()
                supervisor._collect=poll
                supervisor._monitor()
                finalized=self.finalized()
                self.assertEqual(finalized['reason'],reason)
                self.assertEqual(finalized['final_collection'],'committed_listed_segments')
                self.assertEqual(finalized['capture_completeness'],completeness)
                session=self.store.get_session(sid)
                self.assertEqual((session['state'],session['reason']),('stopped',reason))

    def test_in_process_stop_without_a_recorded_request_is_the_users(self):
        supervisor=self.supervisor()
        collect=supervisor._collect
        def poll(final=False):
            collect(final=final)
            supervisor.stop_event.set()
        supervisor._collect=poll
        supervisor._monitor()
        self.assertEqual(self.finalized()['reason'],'user_stop')

    def test_controller_shutdown_with_a_synchronous_runtime_end_to_end(self):
        import threading
        from controller import Controller
        supervisor=self.supervisor()
        class Synchronous:
            """ServiceRuntime-like: its stop signals and drains the recorder."""
            def stop_session(inner,session):
                return supervisor.stop(session)
        supervisor.thread=threading.Thread(target=supervisor._monitor,daemon=True)
        supervisor.thread.start()
        session=Controller(self.store,Synchronous()).stop(self.session['id'],'controller_shutdown')
        self.assertFalse(supervisor.thread.is_alive())
        self.assertEqual((session['state'],session['reason']),('stopped','controller_shutdown'))
        self.assertEqual(self.finalized()['reason'],'controller_shutdown')
        self.assertEqual(self.finalized()['capture_completeness'],'partial_or_unverified')

    def test_controller_stop_with_an_asynchronous_runtime_end_to_end(self):
        import threading
        from controller import Controller
        supervisor=self.supervisor()
        class Deferred:
            """Returns 'stopping' at once and signals the recorder later."""
            def stop_session(inner,session):
                return {'state':'stopping','reason':'worker_cleanup_pending'}
        controller=Controller(self.store,Deferred())
        supervisor.thread=threading.Thread(target=supervisor._monitor,daemon=True)
        supervisor.thread.start()
        controller.stop(self.session['id'])
        supervisor.thread.join(5)
        self.assertFalse(supervisor.thread.is_alive())
        kinds=[e['kind'] for e in self.store.list_events(self.session['id'])]
        self.assertLess(kinds.index('recording.stop_requested'),kinds.index('runtime.stop_result'))
        self.assertEqual(self.finalized()['reason'],'user_stop')
        self.assertEqual(self.finalized()['capture_completeness'],'unverified')
        session=self.store.get_session(self.session['id'])
        self.assertEqual((session['state'],session['reason']),('stopped','user_stop'))
        self.assertEqual(self.store.requested_stop(self.session['id']),'user_stop')


if __name__=='__main__':unittest.main()
