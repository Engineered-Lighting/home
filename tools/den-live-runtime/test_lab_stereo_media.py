"""Real private Store/filesystem retention with generated camera originals."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
from config import Settings
from store import Store
from preview_ingest import PreviewIngestor
from bundle_ingest import BundleIngestor
from depth_clock import DepthClock
import live_capture_fixtures as capture
import lab_stereo_media as media


class LabStereoMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='authored-stereo-media-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.now = capture.NOW - 1
        self.store = Store(Settings(storage_root=self.path / 'store', min_free_bytes=0,
                                   cameras=({'id': 'den'},)), clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.session, _ = self.store.create_session('den', 'detection', 'Authored stereo originals', 'authored-stereo-request')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.now = capture.NOW + .2
        jpeg, header, bundle, calibration = capture.inputs(observed_ns=capture.NOW * 10**9)
        preview = PreviewIngestor(self.store, site_id='victoria', camera_id='den',
                                 clock_validation_id='authored-clock', clock_uncertainty_s=.001)
        frame = preview.ingest(self.session['id'], jpeg, header)
        raw = BundleIngestor(self.store, site_id='victoria', camera_id='den').ingest(self.session['id'], bundle, calibration)
        _, cp, _, _, _ = capture.setup()
        mapped = DepthClock(self.store, site_id='victoria', camera_id='den', clock=cp['depth_clock']).derive(
            self.session['id'], raw['id'], frame['media_ids'][0])
        original = {'schema': 'home-lab-paired-preview-original-1', 'version': 1,
                    'session_id': self.session['id'], 'capture_epoch': 'synthetic-camera-epoch',
                    'rgb_media_id': frame['media_ids'][0], 'bundle_media_id': raw['id'],
                    'calibration': calibration, 'source_metadata': raw['metadata']['source_metadata'],
                    'first_pair_received_at': self.now,
                    'native_clock': 1792173344000000120}
        pair = self.store.add_media(self.session['id'], media.encoded(original), 'application/json',
            {'stream': 'paired_preview_original', 'format': 'home-lab-paired-preview-original-1',
             'site_id': 'victoria', 'camera_id': 'den'}, 'authored-exact-pair')
        self.rows = [self.store.get_media(self.session['id'], frame['media_ids'][0]), raw, pair, mapped]
        self.refs = [media.media_ref(row) for row in self.rows]
        self.scope = {'site_id': 'victoria', 'room_id': 'den', 'session_id': self.session['id'], 'domain_epoch': 'authored-epoch'}
        self.current = True
        # Production requires Linux directory durability. Only that existing
        # platform operation is substituted by the Windows workstation runner.
        if os.name == 'nt':
            self.sync = patch('lab_stereo_media.sync_directory')
            self.sync.start()
            self.addCleanup(self.sync.stop)
        self.spool = self.make_spool()

    def make_spool(self, name='binary-originals', **kwargs):
        return media.LabStereoMedia(self.path / name, store=self.store, scope=self.scope,
            lab_binding_digest='a' * 64, media_generation=self.session['generation'],
            current=lambda: self.current, **kwargs)

    def reader(self, receipt=None, **kwargs):
        receipt = receipt or self.spool.finalize(expected_refs=self.refs)
        self.assertEqual(receipt['status'], 'finalized', receipt)
        return media.SealedStereoMedia(self.spool.directory, expected_binding=kwargs.get('binding', self.spool.binding),
            expected_refs=kwargs.get('refs', self.refs), manifest_digest=kwargs.get('manifest_digest', receipt['manifest_digest']))

    def test_full_exact_store_originals_and_integer_clocks_roundtrip_without_live_fallback(self):
        self.assertFalse(self.spool.directory.exists(), 'constructor must not create a spool')
        with patch('lab_stereo_media.os.fsync', wraps=os.fsync) as sync:
            ack = self.spool.retain_media(self.refs)
        self.assertEqual(ack, {'status': 'retained', 'binding_digest': self.spool.binding_digest, 'media_refs': self.refs})
        self.assertGreaterEqual(sync.call_count, 8)
        for ref, row in zip(self.refs, self.rows):
            live = self.spool.resolve_retained(ref)
            self.assertEqual(live['record'], {key: row[key] for key in media.ROW_KEYS})
            self.assertEqual(hashlib.sha256(live['data']).hexdigest(), ref['sha256'])
        receipt = self.spool.finalize(expected_refs=self.refs)
        reader = self.reader(receipt)
        with patch.object(self.store, 'get_media', side_effect=AssertionError('no live Store fallback')):
            restored = reader(self.refs[2])
            self.assertEqual(json.loads(restored['data'])['native_clock'], 1792173344000000120)
            self.assertEqual(reader(self.refs[1])['record']['metadata']['calibration'], self.rows[1]['metadata']['calibration'])
        self.assertEqual(self.spool.finalize(expected_refs=list(reversed(self.refs))), receipt)

    def test_exact_retry_preserves_files_and_changed_metadata_or_source_bytes_refuse(self):
        first = self.spool.retain_media(self.refs)
        paths = {path: path.stat().st_mtime_ns for path in self.spool.directory.rglob('*') if path.is_file()}
        self.assertEqual(self.spool.retain_media(deepcopy(self.refs)), first)
        self.assertEqual(paths, {path: path.stat().st_mtime_ns for path in paths})
        changed = deepcopy(self.rows[0]['metadata']); changed['available_at'] += .01
        with self.store.lock:
            self.store.db.execute('UPDATE media SET metadata=? WHERE id=?', (json.dumps(changed), self.refs[0]['id']))
        with self.assertRaisesRegex(ValueError, 'identity_conflict'):
            self.spool.retain_media([self.refs[0]])
        source_spool = self.make_spool('source-bytes-change')
        source_spool.retain_media([self.refs[1]])
        original_path = self.store.media_path(self.session['id'], self.refs[1]['id'])
        original_path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source_digest_mismatch'):
            source_spool.retain_media([self.refs[1]])

    def test_unsealed_missing_requested_and_truncated_bytes_are_incomplete(self):
        self.spool.retain_media(self.refs)
        with self.assertRaises(ValueError):
            media.SealedStereoMedia(self.spool.directory, expected_binding=self.spool.binding,
                expected_refs=self.refs, manifest_digest='0' * 64)
        missing = [*self.refs, {'id': 'not-retained', 'sha256': 'b' * 64, 'size': 1}]
        self.assertEqual(self.spool.finalize(expected_refs=missing)['status'], 'incomplete')
        receipt = self.spool.finalize(expected_refs=self.refs)
        self.assertEqual(receipt['status'], 'finalized', receipt)
        binary = self.spool.directory / 'bytes' / (self.refs[1]['sha256'] + '.bin')
        binary.write_bytes(binary.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, 'binary_digest_mismatch'):
            self.reader(receipt)

    def test_wrong_binding_generation_ref_and_manifest_tampering_refuse(self):
        self.spool.retain_media(self.refs)
        receipt = self.spool.finalize(expected_refs=self.refs)
        changed = deepcopy(self.spool.binding); changed['scope']['domain_epoch'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'sealed_manifest_required'):
            self.reader(receipt, binding=changed)
        with self.assertRaisesRegex(ValueError, 'missing_expected_original'):
            self.reader(receipt, refs=self.refs[:-1])
        reader = self.reader(receipt)
        wrong = {**self.refs[0], 'sha256': 'b' * 64}
        with self.assertRaisesRegex(ValueError, 'replay_ref_mismatch'):
            reader(wrong)
        path = self.spool.directory / 'manifest.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest_digest_mismatch'):
            self.reader(receipt)

    def test_failure_or_uncertain_fsync_never_acknowledges_or_seals(self):
        with patch('lab_stereo_media.os.fsync', side_effect=OSError('authored fsync failure')):
            with self.assertRaises(OSError):
                self.spool.retain_media(self.refs)
        self.assertEqual(self.spool.finalize(expected_refs=self.refs)['status'], 'incomplete')
        self.assertFalse((self.spool.directory / 'manifest.json').exists())

    def test_quota_and_free_space_bounds_fail_before_retained_ack(self):
        small = self.make_spool('small', max_bytes=1024)
        with self.assertRaisesRegex(ValueError, 'byte_limit'):
            small.retain_media([self.refs[0]])
        limited = self.make_spool('object-limit', max_objects=1)
        with self.assertRaisesRegex(ValueError, 'object_limit'):
            limited.retain_media(self.refs)
        free = self.make_spool('free-limit')
        with patch('lab_stereo_media.shutil.disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'disk_headroom'):
                free.retain_media(self.refs)

    def test_stop_or_generation_change_after_durability_refuses_selection_but_preserves_originals(self):
        write = self.spool._write
        def stop_after_write(path, raw):
            result = write(path, raw)
            if path.parent.name == 'records':self.spool.quiesce()
            return result
        with patch.object(self.spool, '_write', side_effect=stop_after_write):
            with self.assertRaisesRegex(ValueError, 'admission_not_current'):
                self.spool.retain_media(self.refs)
        self.assertEqual(len(self.spool.records), 4)
        with self.assertRaisesRegex(ValueError, 'admission_not_current'):
            self.spool.resolve_retained(self.refs[0])
        self.reader()  # Final historical sealing never rereads a live Store.
        other = self.make_spool('generation-change')
        write = other._write
        def retire(path, raw):
            result = write(path, raw)
            if path.parent.name == 'records':
                with self.store.lock:
                    self.store.db.execute('UPDATE sessions SET generation=generation+1 WHERE id=?', (self.session['id'],))
            return result
        with patch.object(other, '_write', side_effect=retire):
            with self.assertRaises(Exception):other.retain_media(self.refs)

    def test_stop_is_nonblocking_during_a_retention_write(self):
        entered, release = threading.Event(), threading.Event()
        write = self.spool._write
        errors = []
        def block(path, raw):
            if not entered.is_set():
                entered.set()
                if not release.wait(2):raise AssertionError('authored retention release missing')
            return write(path, raw)
        def retain():
            try:self.spool.retain_media(self.refs)
            except Exception as error:errors.append(error)
        with patch.object(self.spool, '_write', side_effect=block):
            thread = threading.Thread(target=retain)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.spool.quiesce(), {'status': 'unconfirmed', 'in_flight': True})
            finally:
                release.set(); thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIn('admission_not_current', str(errors[0]))

    def test_closed_refs_foreign_family_scope_and_unretained_lookup_refuse(self):
        for refs in ([], [{**self.refs[0], 'path': '/arbitrary'}], [self.refs[0], self.refs[0]],
                     [{**self.refs[0], 'id': '../escape'}]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.spool.retain_media(refs)
        with self.assertRaisesRegex(ValueError, 'retained_ref_mismatch'):
            self.spool.resolve_retained(self.refs[0])
        item = self.store.add_media(self.session['id'], b'outside allowed family', 'application/json',
                                    {'stream': 'unrelated', 'format': 'private-other'}, 'unrelated-original')
        with self.assertRaisesRegex(ValueError, 'family_or_scope'):
            self.spool.retain_media([media.media_ref(item)])
        self.current = False
        with self.assertRaisesRegex(ValueError, 'admission_not_current'):
            self.spool.retain_media(self.refs)

    def test_stop_inside_current_guard_cannot_return_retained(self):
        def current():
            self.spool.quiesce()
            return True
        self.spool.current = current
        with self.assertRaisesRegex(ValueError, 'session_or_generation_changed'):
            self.spool.retain_media(self.refs)
        self.assertFalse(self.spool.directory.exists())

    def test_partial_second_object_failure_cannot_seal_successful_subset(self):
        for name, limit, changed in (('object-budget', 1, False), ('original-conflict', 96, True)):
            with self.subTest(failure=name):
                spool = self.make_spool(name, max_objects=limit)
                refs = deepcopy(self.refs[:2])
                if changed:refs[1]['sha256'] = 'b' * 64
                with self.assertRaises(ValueError):spool.retain_media(refs)
                self.assertEqual(spool.media_refs(), self.refs[:1])
                result = spool.finalize(expected_refs=spool.media_refs())
                self.assertEqual(result['status'], 'incomplete', result)
                self.assertEqual(result['reason'], 'stereo_media_durability_unconfirmed')
                self.assertFalse((spool.directory / 'manifest.json').exists())

    def test_all_retained_refs_are_copied_and_empty_finalization_is_explicitly_incomplete(self):
        empty = self.make_spool('empty')
        self.assertEqual(empty.media_refs(), [])
        result = empty.finalize(expected_refs=empty.media_refs())
        self.assertEqual(result, {'status': 'incomplete', 'reason': 'stereo_media_no_retained_originals'})
        self.assertFalse(empty.directory.exists())
        # Malformed callback requests rejected before I/O do not poison an
        # otherwise unused spool or remove later retained candidate originals.
        with self.assertRaises(ValueError):self.spool.retain_media([{**self.refs[0], 'path': 'untrusted'}])
        self.spool.retain_media(self.refs)
        refs = self.spool.media_refs()
        self.assertEqual(refs, sorted(self.refs, key=lambda ref: ref['id']))
        refs[0]['sha256'] = 'b' * 64
        self.assertNotEqual(refs, self.spool.media_refs())
        self.assertEqual(self.spool.finalize(expected_refs=self.spool.media_refs())['status'], 'finalized')


if __name__ == '__main__':
    unittest.main()
