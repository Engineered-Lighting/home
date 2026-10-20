"""Exact storage accounting without a full scan per write.

The Store keeps an exact running total of the files it writes and deletes
(media, recorder segment imports, deletion and erasure), measures SQLite's own
files by stat and active recorder staging directly, and reconciles the total
against a full scan at startup, session start and stop, and on a background
interval. These tests prove the total equals the full scan (scan_usage, the
oracle) after every write/delete path, that capacity checks never walk the tree,
and that drift is found, corrected, counted and fails closed.
"""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Settings
from store import Store, LabError


class StorageScanTests(unittest.TestCase):
    """The full scan itself: the reconciliation and test oracle."""

    def test_nested_files_and_external_growth_are_counted_by_the_full_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'recording').mkdir()
            (root/'recording'/'segment').write_bytes(b'x'*12)
            (root/'ledger-wal').write_bytes(b'y'*17)
            self.assertEqual(Store.scan_tree(root),29)
            (root/'recording'/'segment').write_bytes(b'x'*42)
            self.assertEqual(Store.scan_tree(root),59)

    def test_skipped_paths_are_neither_entered_nor_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'staging'/'active').mkdir(parents=True)
            (root/'staging'/'active'/'00000000.m4s').write_bytes(b'x'*5)
            (root/'lab.sqlite3').write_bytes(b'y'*7)
            (root/'kept').write_bytes(b'z'*3)
            skip={os.path.join(str(root),'lab.sqlite3'),str(root/'staging'/'active')}
            self.assertEqual(Store.scan_tree(root,skip),3)

    def test_directory_removed_while_scanning_holds_no_bytes_but_root_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'staging'/'other').mkdir(parents=True)
            (root/'staging'/'other'/'00000000.m4s').write_bytes(b'x'*5)
            (root/'ledger').write_bytes(b'y'*7)
            real=os.scandir
            def scandir(path):
                if Path(path).name=='other':
                    raise FileNotFoundError(path)  # deleted after its parent was listed
                return real(path)
            with patch('store.os.scandir',side_effect=scandir):
                self.assertEqual(Store.scan_tree(root),7)
            with self.assertRaises(FileNotFoundError):
                Store.scan_tree(root/'missing')

    def test_rejects_links_before_traversal_or_accounting(self):
        class Link:
            path='fixture/link'
            def is_symlink(self):return True
            def is_dir(self,**_):raise AssertionError('link traversed')
            def stat(self,**_):raise AssertionError('link read')
        class Entries:
            def __enter__(self):return iter([Link()])
            def __exit__(self,*_):pass
        with patch('store.os.scandir',return_value=Entries()):
            with self.assertRaises(LabError) as caught:
                Store.scan_tree(Path('fixture'))
        self.assertEqual(caught.exception.code,'unsafe_storage')


class RunningTotalTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root=Path(self.directory.name)
        self.now=100.0
        self.settings=Settings(storage_root=self.root,min_free_bytes=0,quota_bytes=64*1024**2)
        self.store=Store(self.settings,clock=lambda:self.now)
        self.sid=self.recording('request1')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def recording(self,request_id,camera='den'):
        session,_=self.store.create_session(camera,'detection','Fixture',request_id)
        self.store.transition(session['id'],'starting')
        self.store.transition(session['id'],'recording')
        return session['id']

    def assertExact(self):
        # The running total, measured without a scan, equals the full scan.
        with self.store.lock:
            self.assertEqual(self.store.storage_usage(),self.store.scan_usage())

    def scans(self):
        calls=[]
        real=Store.scan_tree
        def counted(root,skip=frozenset(),limit=None):
            calls.append(str(root))
            return real(root,skip,limit)
        return calls,patch('store.Store.scan_tree',side_effect=counted)

    def test_capacity_checks_and_writes_never_walk_the_storage_root(self):
        calls,counting=self.scans()
        with counting:
            for i in range(20):
                self.store.add_media(self.sid,b'frame'*(i+1),'image/jpeg',{'i':i})
                self.store.append_event(self.sid,'fixture',{'i':i})
                self.store.check_capacity(1024)
        self.assertEqual(calls,[],'no full or partial scan on the write path')
        self.assertExact()

    def test_every_store_write_and_delete_path_keeps_the_total_exact(self):
        sid=self.sid
        self.assertExact()
        blobs=[os.urandom(100+i) for i in range(5)]
        media=[self.store.add_media(sid,blob,'image/jpeg',{'i':i},f'key{i}') for i,blob in enumerate(blobs)]
        self.assertExact()
        # An idempotent replay writes nothing.
        self.assertEqual(self.store.add_media(sid,blobs[0],'image/jpeg',{'i':0},'key0')['id'],media[0]['id'])
        for i in range(30):
            self.store.append_event(sid,'fixture',{'padding':'p'*4000,'i':i})
        self.store.append_event(sid,'recording.fixture',{'reason':'x'},critical=True)
        job,_=self.store.create_job('analysis','job-request',{'x':1},sid)
        self.store.update_job(job['id'],'cancelled')
        self.assertExact()
        # A write that fails part-way leaves its partial file on disk, counted.
        with patch('store.os.replace',side_effect=OSError('fixture rename failure')):
            with self.assertRaises(OSError):
                self.store.add_media(sid,b'q'*333,'image/jpeg',{})
        self.assertTrue(list((self.root/'media'/sid).glob('*.partial')))
        self.assertExact()
        # Stop reconciles; explicit media deletion subtracts exactly what it unlinks.
        self.store.transition(sid,'stopped')
        self.assertExact()
        session=self.store.get_session(sid)
        self.store.delete_session(sid,session['revision'])
        self.assertEqual(len(media),5)
        self.assertExact()
        # Erasure of the records (ledger only) of another stopped session.
        other=self.recording('request2',camera='den2')
        self.store.add_media(other,b'z'*2048,'image/png',{})
        self.store.transition(other,'stopped')
        self.store.delete_session(other,self.store.get_session(other)['revision'],erase_records=True)
        self.assertExact()
        self.assertEqual(self.store.accounting['drift_corrections'],0,'no path needed a correction')

    def test_recorder_staging_is_measured_live_then_joins_the_total(self):
        sid=self.sid
        staging=self.root/'staging'/sid
        staging.mkdir(parents=True)
        self.store.track_staging(sid,staging)
        # FFmpeg writes outside the Store: counted at once, without a reconcile.
        (staging/'index.m3u8').write_bytes(b'#EXTM3U'+b'x'*50)
        (staging/'init.mp4').write_bytes(b'i'*700)
        (staging/'00000000.m4s.tmp').write_bytes(b's'*5000)
        self.assertExact()
        # The monitor's own fresh measurement is used instead of rescanning.
        measured=sum(p.stat().st_size for p in staging.iterdir())
        calls,counting=self.scans()
        with counting:
            used=self.store.storage_usage({sid:measured})
            self.store.check_capacity(1024,measured_staging={sid:measured})
        self.assertEqual(calls,[])
        self.assertEqual(used,self.store.scan_usage())
        with self.assertRaises(LabError):
            self.store.storage_usage({sid:-1})
        # Segment import: a media write plus the staging copy's unlink.
        (staging/'00000000.m4s.tmp').rename(staging/'00000000.m4s')
        data=(staging/'00000000.m4s').read_bytes()
        self.store.add_media(sid,data,'video/mp4',{'kind':'encoded_segment','format':'fmp4'},'segment:00000000')
        (staging/'00000000.m4s').unlink()
        self.assertExact()
        # After termination the remaining staging bytes move into the total.
        self.store.release_staging(sid)
        self.store.release_staging(sid)  # idempotent
        self.assertExact()
        calls,counting=self.scans()
        with counting:
            self.store.check_capacity(0)
        self.assertEqual(calls,[],'a released directory is no longer measured')
        # Deleting the stopped session's media removes its staging leftovers.
        self.store.transition(sid,'stopped')
        self.store.delete_session(sid,self.store.get_session(sid)['revision'])
        self.assertFalse(staging.exists())
        self.assertExact()
        self.assertEqual(self.store.accounting['drift_corrections'],0)

    def test_deleting_a_still_tracked_staging_directory_stays_exact(self):
        sid=self.sid
        staging=self.root/'staging'/sid
        staging.mkdir(parents=True)
        self.store.track_staging(sid,staging)
        (staging/'init.mp4').write_bytes(b'i'*90)
        self.store.transition(sid,'stopped')
        self.store.delete_session(sid,self.store.get_session(sid)['revision'])
        self.assertNotIn(sid,self.store._active_staging)
        self.assertExact()

    def test_tracking_an_existing_directory_moves_its_bytes_out_of_the_total(self):
        staging=self.root/'staging'/self.sid
        staging.mkdir(parents=True)
        (staging/'init.mp4').write_bytes(b'i'*90)
        self.store.reconcile_storage()
        self.store.track_staging(self.sid,staging)
        self.assertExact()
        (staging/'index.m3u8').write_bytes(b'x'*9)
        self.assertExact()

    def test_drift_is_detected_corrected_counted_and_logged_without_content(self):
        self.store.add_media(self.sid,b'x'*1000,'image/jpeg',{})
        # A write inside a tree only the Store writes, but not by the Store.
        secret=self.root/'media'/self.sid/'private-name.bin'
        secret.write_bytes(b'{"secret":"content"}')
        with self.store.lock:
            self.assertNotEqual(self.store.storage_usage(),self.store.scan_usage(),'an external write is drift')
        with self.assertLogs('perception_lab.storage','WARNING') as logged:
            outcome=self.store.reconcile_storage('interval')
        self.assertEqual(outcome['drift_bytes'],secret.stat().st_size)
        self.assertTrue(outcome['within_quota'])
        self.assertEqual(self.store.accounting['drift_corrections'],1)
        self.assertEqual(self.store.accounting['last_drift_bytes'],secret.stat().st_size)
        text=' '.join(logged.output)
        self.assertNotIn('private-name',text)
        self.assertNotIn('secret',text)
        self.assertExact()
        # Drift in the other direction (a file removed behind the Store).
        secret.unlink()
        outcome=self.store.reconcile_storage('interval')
        self.assertLess(outcome['drift_bytes'],0)
        self.assertEqual(self.store.accounting['drift_corrections'],2)
        self.assertExact()
        # A clean pass corrects nothing.
        self.assertEqual(self.store.reconcile_storage('interval')['drift_bytes'],0)
        self.assertEqual(self.store.accounting['drift_corrections'],2)

    def test_drift_over_quota_fails_closed_until_usage_is_back_within_quota(self):
        big=self.root/'media'/'external.bin'
        with big.open('wb') as handle:
            handle.truncate(self.settings.quota_bytes)
        outcome=self.store.reconcile_storage('interval')
        self.assertFalse(outcome['within_quota'])
        with self.assertRaises(LabError) as caught:
            self.store.add_media(self.sid,b'x','image/jpeg',{})
        self.assertEqual(caught.exception.code,'quota_exceeded')
        with self.assertRaises(LabError):
            self.store.append_event(self.sid,'fixture',{})
        # Bounded stop/failure receipts still land; quota never blocks a stop.
        self.store.append_event(self.sid,'recording.fixture',{'reason':'quota_exceeded'},critical=True)
        with self.assertRaises(LabError):
            self.store.create_session('den2','detection','Other','request3')
        big.unlink()
        self.store.reconcile_storage('interval')
        self.store.add_media(self.sid,b'x','image/jpeg',{})
        self.assertExact()

    def test_quota_semantics_are_unchanged_at_the_boundary(self):
        with self.store.lock:
            used=self.store.storage_usage()
        remaining=self.settings.quota_bytes-used
        self.store.check_capacity(remaining)
        with self.assertRaises(LabError):
            self.store.check_capacity(remaining+1)

    def test_out_of_band_bytes_under_the_root_count_at_once(self):
        # Reviewer probe: 25 MB into derived/ (tools/extract-lab-detector-inputs.py
        # writes there) must refuse a 20 MB admission under a 40 MB quota at once,
        # with no reconciliation in between.
        self.store.close()
        self.settings=Settings(storage_root=self.root,min_free_bytes=0,quota_bytes=40*1024**2)
        self.store=Store(self.settings,clock=lambda:self.now)
        self.store.check_capacity(20*1024**2)
        derived=self.root/'derived'/self.sid/'detector-inputs-v1'
        derived.mkdir(parents=True)
        with (derived/'frame-000001.jpg').open('wb') as handle:
            handle.truncate(25*1024**2)
        before=dict(self.store.accounting)
        with self.assertRaises(LabError) as caught:
            self.store.check_capacity(20*1024**2)
        self.assertEqual(caught.exception.code,'quota_exceeded')
        self.assertEqual(self.store.accounting['reconciliations'],before['reconciliations'],'no reconciliation was needed')
        self.assertEqual(self.store.accounting['unowned_entries'],3)
        self.assertExact()
        # Growth of an out-of-band file, a new top-level file, and removal all
        # count at the next check; none of it is drift.
        with (derived/'frame-000001.jpg').open('ab') as handle:
            handle.write(b'g'*1000)
        (self.root/'operator-notes.txt').write_bytes(b'n'*77)
        self.assertExact()
        (derived/'frame-000001.jpg').unlink()
        self.store.check_capacity(20*1024**2)
        self.assertExact()
        self.store.reconcile_storage('interval')
        self.assertEqual(self.store.accounting['drift_corrections'],0)

    def test_out_of_band_walk_is_bounded_and_fails_closed_beyond_it(self):
        derived=self.root/'derived'
        derived.mkdir()
        for i in range(6):
            (derived/f'{i}.bin').write_bytes(b'd')
        with patch('store.MAX_UNOWNED_ENTRIES',6):
            self.store.check_capacity(0)
            self.assertEqual(self.store.accounting['unowned_entries'],6)
            (derived/'6.bin').write_bytes(b'd')
            with self.assertRaises(LabError) as caught:
                self.store.check_capacity(0)
            self.assertEqual(caught.exception.code,'storage_unbounded')
        self.store.check_capacity(0)

    def test_startup_session_start_and_stop_reconcile_against_a_full_scan(self):
        before=self.store.accounting['reconciliations']
        owned=self.root/'media'
        (owned/'external-a').write_bytes(b'a'*11)
        other=self.recording('request2',camera='den2')  # session start
        self.assertEqual(self.store.accounting['reconciliations'],before+1)
        self.assertEqual(self.store.accounting['drift_corrections'],1)
        self.assertExact()
        (owned/'external-b').write_bytes(b'b'*13)
        self.store.transition(other,'stopped')  # session stop
        self.assertEqual(self.store.accounting['reconciliations'],before+2)
        self.assertEqual(self.store.accounting['drift_corrections'],2)
        self.assertExact()
        # Controller startup: a reopened Store begins from a full scan.
        self.store.close()
        (owned/'external-c').write_bytes(b'c'*17)
        self.store=Store(self.settings,clock=lambda:self.now)
        self.assertEqual(self.store.accounting['reconciliations'],1)
        self.assertEqual(self.store.accounting['last_reason'],'startup')
        self.assertExact()

    def test_a_write_during_the_unlocked_scan_is_not_mistaken_for_drift(self):
        real=Store.scan_tree
        raced=[]
        def racing(root,skip=frozenset(),limit=None):
            result=real(root,skip,limit)
            if Path(root)==self.store.root/'media' and not raced:
                raced.append(1)
                # Another thread writes after this scan passed its directory:
                # the scan misses bytes the running total already holds.
                worker=threading.Thread(target=self.store.add_media,args=(self.sid,b'r'*4096,'image/jpeg',{}))
                worker.start();worker.join()
            return result
        with patch('store.Store.scan_tree',side_effect=racing):
            outcome=self.store.reconcile_storage('interval')
        self.assertEqual(raced,[1])
        self.assertEqual(outcome['drift_bytes'],0,'the racing scan was retried, not trusted')
        self.assertEqual(self.store.accounting['drift_corrections'],0)
        self.assertExact()

    def test_scan_os_errors_fail_closed_as_storage_scan_failed(self):
        import errno
        for error in (PermissionError(errno.EACCES,'fixture denied','/private/name'),
                      OSError(errno.EIO,'fixture I/O error','/private/name')):
            with self.subTest(error=type(error).__name__):
                failures=self.store.accounting['scan_failures']
                with patch('store.Store.scan_tree',side_effect=error):
                    with self.assertLogs('perception_lab.storage','WARNING') as logged:
                        with self.assertRaises(LabError) as caught:
                            self.store.reconcile_storage('interval')
                self.assertEqual((caught.exception.status,caught.exception.code),(503,'storage_scan_failed'))
                self.assertEqual(str(caught.exception),'storage scan failed')
                self.assertEqual(self.store.accounting['scan_failures'],failures+1)
                text=' '.join(logged.output)
                self.assertIn('storage_scan_failed',text)
                self.assertIn(type(error).__name__,text)
                self.assertNotIn('/private/name',text)
                self.assertNotIn('fixture',text)
                # Admission stays refused until a scan succeeds.
                with self.assertRaises(LabError) as caught:
                    self.store.check_capacity(0)
                self.assertEqual(caught.exception.code,'storage_scan_failed')
                self.store.reconcile_storage('interval')
                self.store.check_capacity(0)
        # A live measurement that fails refuses that admission too.
        (self.root/'derived').mkdir()
        with patch('store.Store.scan_tree',side_effect=OSError(errno.EIO,'fixture')):
            with self.assertRaises(LabError) as caught:
                self.store.check_capacity(0)
        self.assertEqual(caught.exception.code,'storage_scan_failed')
        self.store.check_capacity(0)

    def test_a_failed_scan_fails_closed_until_a_scan_succeeds(self):
        with patch('store.Store.scan_tree',side_effect=LabError('unsafe storage link',503,'unsafe_storage')):
            with self.assertRaises(LabError):
                self.store.reconcile_storage('interval')
            # A stop still completes; its reconciliation failure is recorded.
            self.store.transition(self.sid,'stopped')
        self.assertEqual(self.store.get_session(self.sid)['state'],'stopped')
        self.assertEqual(self.store.accounting['scan_failures'],2)
        with self.assertRaises(LabError) as caught:
            self.store.check_capacity(0)
        self.assertEqual(caught.exception.code,'unsafe_storage')
        self.store.reconcile_storage('interval')
        self.store.check_capacity(0)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.settings=Settings(storage_root=Path(self.directory.name),min_free_bytes=0,quota_bytes=64*1024**2)
        self.store=Store(self.settings,clock=lambda:100.0)
        self.session,_=self.store.create_session('den','detection','Fixture','request1')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def plan(self,sql,*args):
        return ' '.join(row[-1] for row in self.store.db.execute('EXPLAIN QUERY PLAN '+sql,args))

    def test_kind_lookups_seek_the_session_kind_index_and_reopen_is_idempotent(self):
        sid=self.session['id']
        self.assertIn('events_session_kind_id',self.plan(
            "SELECT payload FROM events WHERE session_id=? AND kind='reasoning.setting' ORDER BY id DESC LIMIT 1",sid))
        self.assertIn('events_session_kind_id',self.plan(
            "SELECT created_at FROM events WHERE session_id=? AND kind='perception.offered' AND json_extract(payload,'$.arm')=? ORDER BY id DESC LIMIT 1",sid,'a'))
        self.assertIn('events_session_id',self.plan("SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT 10",sid,0))
        self.store.close()
        self.store=Store(self.settings,clock=lambda:100.0)
        indexes=[r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='events_session_kind_id'")]
        self.assertEqual(indexes,['events_session_kind_id'])
        self.assertTrue(self.store.get_session(sid)['reasoning_enabled'] is False)

    def counted(self):
        checks=[]
        original=self.store.storage_usage
        self.store.storage_usage=lambda:checks.append(1) or original()
        return checks

    def test_batch_checks_once_and_rechecks_beyond_its_allowance(self):
        sid=self.session['id']
        self.store.transition(sid,'starting');self.store.transition(sid,'recording')
        checks=self.counted()
        with self.store.capacity_batch(3*(65536+1024)):
            for i in range(3):
                self.store.append_event(sid,'fixture',{'i':i})
            with self.store.capacity_batch(10**9):  # nested: shares, never rechecks
                pass
            self.assertEqual(len(checks),1)
            self.store.append_event(sid,'fixture',{'i':3})
            self.assertEqual(len(checks),2)
        self.store.append_event(sid,'fixture',{'i':4})
        self.assertEqual(len(checks),3)

    def test_batch_counts_unconsumed_allowance_when_it_must_recheck(self):
        sid=self.session['id']
        self.store.transition(sid,'starting');self.store.transition(sid,'recording')
        with patch.object(self.store,'storage_usage',return_value=self.settings.quota_bytes-300*1024):
            with self.store.capacity_batch(200*1024):
                with self.assertRaises(LabError):
                    # 210 KiB alone fits, but not beside the 200 KiB still admitted.
                    self.store.add_media(sid,b'x'*(210*1024-65536),'image/jpeg',{})
            with self.assertRaises(LabError):
                with self.store.capacity_batch(300*1024+1):
                    self.fail('batch admitted beyond quota')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM media').fetchone()[0],0)


if __name__=='__main__':unittest.main()
