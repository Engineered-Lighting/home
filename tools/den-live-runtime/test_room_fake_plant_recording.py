"""Public file/callback seam: original bytes and durability precede effects."""
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from room_fake_plant_recording import RoomNativeRecorder, RecordingIncomplete


def header():
    return dict(session_id='authored-room-1', producer=dict(abi='c12-fake-plant-bridge-1',
        build_id='c12-fake-plant-test', manifest_sha256='ab'*32, source_sha256='bc'*32,
        config_sha256='cd'*32, executable_sha256='de'*32),
        fixtures=[dict(fixture_index=i, fixture_id='fixture-'+str(i),
            device_id=bytes(n+16*i for n in range(16)).hex(), boot=str(i+1),
            launch_id='launch-'+str(i)) for i in range(4)],
        evidence_source='simulated', physical_permission=False)


def event(n, kind='stdout_bytes', **fields):
    return dict(schema='c12-fake-plant-io-1', version=1, ordinal=str(n), kind=kind,
        captured_monotonic_ns=str(time.monotonic_ns()), captured_utc_ns=str(time.time_ns()), **fields)


def members(complete=True):
    return [dict(fixture_index=i, launch_id='launch-'+str(i), close_acknowledged=complete,
        recording_status=dict(complete=complete, cleanup_unconfirmed=False,
            observer_failed=False, terminal=not complete, active_callbacks=0)) for i in range(4)]


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'originals.jsonl'

    def make(self, **kwargs):
        rec = RoomNativeRecorder(self.path, header=header(), **kwargs)
        self.addCleanup(rec.abort, 'test_cleanup', finalize=True)
        return rec

    def clean_eofs(self,rec):
        for i in range(4):
            cb=rec.observer(i)
            cb(event(1,'stdout_eof'));cb(event(2,'stderr_eof'));cb(event(3,'process_exit',exit_code=0))

    def test_ready_requires_durable_header_and_fresh_file(self):
        calls=[]
        original=os.fsync
        with patch('room_fake_plant_recording.os.fsync', side_effect=lambda fd:(calls.append(fd), original(fd))[1]):
            rec=self.make()
        self.assertTrue(rec.ready)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(self.path.read_bytes())['payload'], header())
        with self.assertRaises((FileExistsError, RecordingIncomplete)):
            RoomNativeRecorder(self.path, header=header())

    def test_header_fsync_failure_never_ready(self):
        with patch('room_fake_plant_recording.os.fsync', side_effect=OSError('authored disk failure')):
            with self.assertRaises(RecordingIncomplete):
                RoomNativeRecorder(self.path, header=header())

    def test_raw_malformed_bytes_and_original_clocks_retained_before_parse(self):
        rec=self.make()
        raw=b'\xff\r\n{"broken":'
        original=event(1, raw=raw)
        rec.observer(0)(original)
        row=json.loads(self.path.read_bytes().splitlines()[-1])
        import base64, hashlib
        self.assertEqual(base64.b64decode(row['payload']['original']['base64']), raw)
        self.assertEqual(row['payload']['original']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(row['payload']['event']['captured_utc_ns'], original['captured_utc_ns'])
        self.assertNotIn('raw', row['payload']['event'])
        self.assertEqual(row['payload']['fixture_index'],0)
        self.assertEqual(row['payload']['launch_id'],'launch-0')

    def test_write_result_is_unknown_and_stdout_may_precede_it(self):
        rec=self.make(); cb=rec.observer(0)
        cb(event(1,'stdin_attempt',request_id='request-1',raw=b'{}\n'))
        cb(event(2,raw=b'{"ok":true}\n'))
        cb(event(3,'stdin_write_result', request_id='request-1', reported_bytes=3,
            flush_completed=False,error='TimeoutError',effect='unknown',
            flush_captured_monotonic_ns=None,flush_captured_utc_ns=None))
        value=rec.finalize(members(False))
        self.assertEqual(value['status'],'incomplete')
        self.assertEqual(json.loads(self.path.read_bytes().splitlines()[-2])['payload']['event']['effect'],'unknown')

    def test_failure_is_sticky_and_later_callbacks_cannot_dispatch(self):
        rec=self.make(); cb=rec.observer(0)
        with patch('room_fake_plant_recording.os.fsync',side_effect=OSError('authored disk failure')):
            with self.assertRaises(RecordingIncomplete):
                cb(event(1,'stdin_attempt',request_id='request-1',raw=b'{}\n'))
        with self.assertRaises(RecordingIncomplete):
            cb(event(2,raw=b'next'))
        self.assertFalse(rec.ready)
        self.assertEqual(rec.finalize(members(False))['status'],'incomplete')

    def test_missing_member_eof_exit_cannot_be_complete(self):
        rec=self.make()
        self.assertEqual(rec.finalize(members())['status'],'incomplete')

    def test_unavailable_producer_status_is_null_not_invented(self):
        rec=self.make(); rows=members(False)
        rows[0]['recording_status']=None
        receipt=rec.finalize(rows)
        self.assertEqual(receipt['status'],'incomplete')
        self.assertIsNone(receipt['members'][0]['recording_status'])

    def test_invalid_reported_write_count_is_retained_as_original_failure(self):
        rec=self.make(); cb=rec.observer(0)
        cb(event(1,'stdin_attempt',request_id='request-1',raw=b'{}\n'))
        cb(event(2,'stdin_write_result',request_id='request-1',reported_bytes=-1,
            flush_completed=False,error='OSError',effect='unknown',
            flush_captured_monotonic_ns=None,flush_captured_utc_ns=None))
        cb(event(3,'process_exit',exit_code=3221225477))
        receipt=rec.finalize(members(False))
        self.assertEqual(receipt['status'],'incomplete')
        rows=[json.loads(r) for r in self.path.read_bytes().splitlines()]
        self.assertEqual(rows[2]['payload']['event']['reported_bytes'],-1)
        self.assertEqual(rows[3]['payload']['event']['exit_code'],3221225477)

    def test_clean_all_four_finalization_is_idempotent_and_closes(self):
        rec=self.make()
        for i in range(4):
            cb=rec.observer(i)
            cb(event(1,'stdout_eof'));cb(event(2,'stderr_eof'));cb(event(3,'process_exit',exit_code=0))
        value=rec.finalize(members())
        self.assertEqual(value['status'],'complete')
        self.assertEqual(rec.finalize(members()),value)
        with self.assertRaises(RecordingIncomplete):
            rec.finalize(members(False))
        with self.assertRaises(RecordingIncomplete):
            rec.observer(0)(event(4,raw=b'late'))
        self.assertFalse(rec.ready)

    def test_wrong_ordinals_unsafe_ns_future_clock_and_unknown_fields_refuse(self):
        for change in [dict(ordinal='2'),dict(captured_utc_ns=1792132500000000001),
                       dict(captured_monotonic_ns=str(2**64-1)),dict(extra=True)]:
            with self.subTest(change=change):
                path=Path(self.tmp.name)/('bad-'+str(len(list(Path(self.tmp.name).iterdir())))+'.jsonl')
                rec=RoomNativeRecorder(path,header=header())
                row=event(1,raw=b'original');row.update(change)
                with self.assertRaises(RecordingIncomplete):rec.observer(0)(row)
                self.assertEqual(rec.finalize(members(False))['status'],'incomplete')

    def test_per_direction_end_and_exit_are_terminal(self):
        rec=self.make();cb=rec.observer(0);cb(event(1,'stdout_eof'))
        with self.assertRaises(RecordingIncomplete):cb(event(2,raw=b'after eof'))

    def test_invalid_header_and_callback_identity_refuse(self):
        h=header();h['fixtures'][1]['device_id']=h['fixtures'][0]['device_id']
        with self.assertRaises(RecordingIncomplete):RoomNativeRecorder(self.path,header=h)
        self.assertFalse(self.path.exists())
        rec=self.make()
        with self.assertRaises(RecordingIncomplete):rec.observer(True)

    def test_bounded_collection_preserves_incomplete_footer(self):
        rec=self.make(max_records=4);cb=rec.observer(0)
        cb(event(1,raw=b'one'));cb(event(2,raw=b'two'))
        with self.assertRaises(RecordingIncomplete):cb(event(3,raw=b'three'))
        self.assertEqual(rec.finalize(members(False))['status'],'incomplete')
        self.assertEqual(len(self.path.read_bytes().splitlines()),4)

    def test_blocked_callback_does_not_turn_finalize_into_unbounded_wait(self):
        rec=self.make();entered=threading.Event();release=threading.Event();errors=[]
        def blocked(_):
            entered.set()
            if not release.wait(3):raise OSError('authored blocked callback bound')
        def call():
            try:rec.observer(0)(event(1,raw=b'original'))
            except RecordingIncomplete as exc:errors.append(str(exc))
        thread=threading.Thread(target=call)
        with patch('room_fake_plant_recording.os.fsync',side_effect=blocked):
            thread.start();self.assertTrue(entered.wait(1))
            started=time.monotonic()
            rec.abort('callback_unconfirmed')
            value=rec.finalize(members(False))
            elapsed=time.monotonic()-started
            release.set();thread.join(1)
        self.assertLess(elapsed,1.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(value['status'],'incomplete')
        self.assertTrue(value['cleanup_unconfirmed'])
        self.assertFalse(value['footer_durable'])
        self.assertTrue(errors)
        final=rec.finalize(members(False))
        self.assertEqual(final['status'],'incomplete')
        self.assertEqual(final,value)
        self.assertTrue(rec._final_done.wait(1))

    def test_abort_racing_final_fsync_cannot_return_complete(self):
        rec=self.make();self.clean_eofs(rec)
        entered=threading.Event();release=threading.Event();results=[]
        def blocking(_):entered.set();release.wait(3)
        with patch('room_fake_plant_recording.os.fsync',side_effect=blocking):
            thread=threading.Thread(target=lambda:results.append(rec.finalize(members())))
            thread.start();self.assertTrue(entered.wait(1))
            rec.abort('cleanup_unconfirmed');release.set();thread.join(1.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]['status'],'incomplete')
        self.assertEqual(results[0]['failure_reason'],'cleanup_unconfirmed')

    def test_final_fsync_failure_readable_footer_is_not_a_durable_ack(self):
        rec=self.make();self.clean_eofs(rec)
        with patch('room_fake_plant_recording.os.fsync',side_effect=OSError('authored terminal disk fault')):
            result=rec.finalize(members())
        self.assertEqual(result['status'],'incomplete')
        self.assertFalse(result['footer_durable'])
        # A filesystem read can see unacknowledged bytes. Independent replay
        # requires the original complete/durable close receipt as well as bytes.
        self.assertTrue(self.path.read_bytes().endswith(b'\n'))

    def test_final_storage_stall_returns_unconfirmed_without_claiming_cancellation(self):
        rec=self.make();self.clean_eofs(rec)
        entered=threading.Event();release=threading.Event()
        def blocking(_):entered.set();release.wait(3)
        with patch('room_fake_plant_recording.os.fsync',side_effect=blocking):
            started=time.monotonic();value=rec.finalize(members());elapsed=time.monotonic()-started
            self.assertTrue(entered.is_set());release.set()
            if hasattr(rec,'_final_done'):self.assertTrue(rec._final_done.wait(1))
        self.assertLess(elapsed,1.5)
        self.assertEqual(value['status'],'incomplete')
        self.assertFalse(value['footer_durable'])
        self.assertTrue(value['cleanup_unconfirmed'])
        self.assertEqual(rec.finalize(members())['status'],'incomplete')

    def test_file_close_error_yields_an_exact_retryable_incomplete_receipt(self):
        rec=self.make();self.clean_eofs(rec)
        inner=rec._file
        class BadClose:
            def __getattr__(self,key):return getattr(inner,key)
            def close(self):inner.close();raise OSError('authored close uncertainty')
        rec._file=BadClose()
        result=rec.finalize(members())
        self.assertEqual(result['status'],'incomplete')
        self.assertTrue(result['cleanup_unconfirmed'])
        self.assertEqual(rec.finalize(members()),result)


def emit_node_fixture(path):
    rec=RoomNativeRecorder(path,header=header())
    raw=b'{"op":"diagnostic"}\n'
    for index in range(4):
        cb=rec.observer(index)
        rows=[('stdin_attempt',dict(request_id='request-1',raw=raw)),
              ('stdout_bytes',dict(raw=bytes([0,255,13,10,123,34]))),
              ('stdin_write_result',dict(request_id='request-1',reported_bytes=len(raw),flush_completed=True,
                  error=None,effect='unknown',flush_captured_monotonic_ns='3',
                  flush_captured_utc_ns='1792132500000000003')),
              ('stderr_bytes',dict(raw=b'diagnostic\r\n')),('stdout_eof',{}),('stderr_eof',{}),
              ('process_exit',dict(exit_code=0))]
        for ordinal,(kind,fields) in enumerate(rows,1):
            row=event(ordinal,kind,**fields)
            row['captured_monotonic_ns']='2' if kind=='stdin_write_result' else '1'
            row['captured_utc_ns']='1792132500000000002' if kind=='stdin_write_result' else '1792132500000000001'
            cb(row)
    print(json.dumps(rec.finalize(members()),sort_keys=True))


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--emit-node-fixture':
        emit_node_fixture(Path(sys.argv[2]))
    else:
        unittest.main()
