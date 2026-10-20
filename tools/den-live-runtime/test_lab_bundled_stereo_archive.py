"""Source6 raw capture ACK/replay require the actual complete seven originals."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_lab_bundled_stereo_media as actual
import lab_stereo_media as media
import lab_original_archive as archive
import lab_stereo_replay as replay


class BundledStereoArchiveTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'nt':
            for name in ('lab_stereo_media.sync_directory', 'lab_original_archive.sync_parent'):
                port = patch(name); port.start(); self.addCleanup(port.stop)
        self.fixture, self.runtime, self.scope, self.rows, self.input_support = actual.actual_bundled_originals(self)
        sid = self.scope['session_id']
        end = time.monotonic() + 2
        self.events = []
        while time.monotonic() < end:
            self.events = self.fixture.store.list_events(sid)
            if any(e['kind'] == 'perception.output' and e['payload'].get('arm') == 'b' for e in self.events): break
            time.sleep(.005)
        self.event = next(e for e in self.events if e['kind'] == 'perception.output' and e['payload'].get('arm') == 'b')
        self.start = next(e for e in self.events if e['kind'] == 'assessment.bundle_rgb.started'
            and e['id'] == self.input_support['started_event_id'])
        self.terminal = next(e for e in self.events if e['kind'] == 'assessment.bundle_rgb.terminal'
            and e['payload']['started_event_id'] == self.start['id'])
        self.policy = dict(mapping_id='authored-derived-c-clock', mapping_digest='1' * 64,
            source_epoch=self.rows['rgb']['metadata']['capture_epoch'], original_unit='unix_s',
            epoch_origin_s=0, max_uncertainty_s=1)
        self.expected_bundle = dict(scope=self.scope, lab_binding_digest='a' * 64,
            bundle_input_policy_sha256=self.input_support['binding']['policy_sha256'])
        self.spool = media.LabStereoMedia(Path(self.fixture.directory.name) / 'capture-binaries',
            store=self.fixture.store, scope=self.scope, lab_binding_digest='a' * 64,
            media_generation=self.fixture.session['generation'], current=lambda: True,
            original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256=self.expected_bundle['bundle_input_policy_sha256'])
        self.refs = {role: media.media_ref(row) for role, row in self.rows.items()}
        records = {role: ({key: deepcopy(row[key]) for key in media.ROW_KEYS},
            self.fixture.store.media_path(sid, row['id']).read_bytes()) for role, row in self.rows.items()}
        from lab_bundled_rgb_stereo import build_bundled_clock_original
        self.clock_original = build_bundled_clock_original(records, expected_bundle_binding=self.expected_bundle,
            capture_clock_policy=self.policy, started=self.start, terminal=self.terminal,
            received_at=self.fixture.now, event=self.event, frame=self.rows['rgb']['metadata'])
        # The original full-read receipt stays fixed while the actual authored
        # consumer clock advances to the later post-retention capture receipt.
        self.fixture.now += .01
        self.method = dict(id='authored-bundled-capture-method', digest='2' * 64)
        self.model = dict(id='authored-existing-composite', digest=self.fixture.profiles['b']['artifact_sha256'])
        self.binding = dict(session_id=sid, method_ref=self.method, model_ref=self.model)
        self.desk = dict(schema='home-lab-desk-archive-binding-4', version=4,
            scope=deepcopy(self.scope), policy_digest='3' * 64, source_id='authored-bundled-desk',
            method_ref=dict(id='authored-desk-method', digest='4' * 64),
            receipt_store_id='authored-desk-originals', receipt_component_id='authored-desk-component',
            stereo_policy_ref=dict(id='authored-stereo-policy', digest='5' * 64),
            lab_binding_digest='a' * 64, original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256=self.expected_bundle['bundle_input_policy_sha256'])
        originals = dict(binding=self.expected_bundle, start=self.start, terminal=self.terminal,
            clock_original=self.clock_original)
        self.original = dict(schema='home-original-lab-observation-2', version=2,
            event=deepcopy(self.event), frame_metadata=deepcopy(self.rows['rgb']['metadata']),
            first_consumer_received_at=self.fixture.now, method_ref=self.method, model_ref=self.model,
            bundled_originals=originals, clock_original=deepcopy(self.clock_original))

    def writer(self, name, **kwargs):
        result = archive.LabOriginalArchive(Path(self.fixture.directory.name) / name,
            binding=self.binding, desk_binding=self.desk, min_free_bytes=1,
            stereo_media_resolver=self.spool.resolve_retained,
            capture_clock_policy=self.policy, **kwargs)
        self.addCleanup(result.finalize)
        return result

    def test_actual_capture_ack_waits_for_media_and_roundtrips_with_exact_policy_and_footer(self):
        writer = self.writer('bundled-capture.jsonl')
        with self.assertRaisesRegex(ValueError, 'lab_bundled_capture_original_binding_refused'):
            writer(deepcopy(self.original))
        self.assertEqual(writer.records, {})
        self.spool.retain_media(list(self.refs.values()))
        ack = writer(deepcopy(self.original))
        self.assertEqual(ack, dict(status='retained', digest=archive.original_digest(self.original)))
        self.assertEqual(writer(deepcopy(self.original)), ack)
        self.assertEqual(writer.finalize()['status'], 'finalized')
        final = self.spool.finalize(expected_refs=list(self.refs.values()))
        self.assertEqual(final['status'], 'finalized', final)
        sealed = media.SealedStereoMedia(self.spool.directory, expected_binding=self.spool.binding,
            expected_refs=list(self.refs.values()), manifest_digest=final['manifest_digest'])
        with patch.object(self.fixture.store, 'get_media', side_effect=AssertionError('no Store replay fallback')):
            recovered = archive.read_lab_originals(writer.path, expected_digests=[ack['digest']],
                expected_desk_binding=self.desk, stereo_media_resolver=sealed.resolve_media,
                capture_clock_policy=self.policy)
        self.assertEqual(recovered, {ack['digest']: self.original})
        with self.assertRaisesRegex(ValueError, 'clock_policy'):
            archive.read_lab_originals(writer.path, expected_digests=[ack['digest']],
                expected_desk_binding=self.desk, stereo_media_resolver=sealed.resolve_media)
        changed_policy = {**self.policy, 'mapping_digest': '6' * 64}
        with self.assertRaisesRegex(ValueError, 'clock_policy_changed'):
            archive.read_lab_originals(writer.path, expected_desk_binding=self.desk,
                stereo_media_resolver=sealed.resolve_media, capture_clock_policy=changed_policy)
        truncated = writer.path.with_name('missing-footer.jsonl')
        truncated.write_bytes(b'\n'.join(writer.path.read_bytes().splitlines()[:-1]) + b'\n')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            archive.read_lab_originals(truncated, expected_desk_binding=self.desk,
                stereo_media_resolver=sealed.resolve_media, capture_clock_policy=self.policy)

    def test_rehashed_terminal_and_narrowed_clock_fail_before_ack_and_fsync_failure_stays_unconfirmed(self):
        self.spool.retain_media(list(self.refs.values()))
        missing = deepcopy(self.original)
        del missing['bundled_originals']['terminal']
        changed = deepcopy(self.original)
        changed['bundled_originals']['terminal']['payload']['binding']['experiment_generation'] += 1
        narrowed = deepcopy(self.original)
        narrowed['clock_original']['clock']['uncertainty_s'] = 0
        narrowed['bundled_originals']['clock_original'] = deepcopy(narrowed['clock_original'])
        for index, record in enumerate((missing, changed, narrowed)):
            # Fresh writers and new outer digests ensure this is original-proof
            # validation, not merely a retry conflict/hash mismatch.
            self.assertNotEqual(archive.original_digest(record), archive.original_digest(self.original))
            writer = self.writer('invalid-bundled-' + str(index) + '.jsonl')
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'bundled_capture_original_binding_refused'):
                writer(record)
            self.assertEqual(writer.records, {})
        failed = self.writer('unknown-bundled-ack.jsonl')
        with patch('lab_original_archive.os.fsync', side_effect=OSError('authored fsync failure')):
            with self.assertRaises(OSError): failed(deepcopy(self.original))
        self.assertEqual(failed.records, {})
        with self.assertRaisesRegex(ValueError, 'unavailable'): failed(deepcopy(self.original))
        self.assertEqual(failed.finalize()['status'], 'incomplete')

    def test_input3_preserves_actual_c_component_and_refuses_source5_or_changed_causal_mapping(self):
        source_id = 'authored-bundled-capture'
        expected = dict(schema='home-live-stereo-replay-inputs-3', version=3,
            causal_sha256='7' * 64, originals_sha256='8' * 64, manifest_digest='9' * 64,
            config_digest='a' * 64, scope=self.scope, capture_binding=self.binding,
            capture_receipt_binding=dict(source_id=source_id, receipt_store_id='authored-capture-originals',
                receipt_component_id='authored-capture-component', root_lineage='persisted-observation-record-1'),
            semantic_binding=dict(scope=self.scope), desk_binding=self.desk,
            original_digests=[archive.original_digest(self.original)], media_binding=self.spool.binding,
            media_refs=list(self.refs.values()), media_manifest_digest='b' * 64,
            replay_source_sha256='c' * 64, capture_clock_policy=self.policy)
        self.assertEqual(replay._expectations(expected), expected)
        source = dict(method_refs=[self.method], association_method_refs=[self.method], model_refs=[self.model],
            receipt_store_ids=['authored-capture-originals'], receipt_component_ids=['authored-capture-component'],
            sensor_roots=['authored-one-camera'], capture_clock=self.policy)
        manifest = dict(worker_config=dict(live_manifest=dict(sources={source_id: source})))
        captured = replay._capture_metadata(self.original, manifest, expected)
        self.assertEqual(captured['capture_seq'], self.clock_original['component']['sequence'])
        self.assertEqual(captured['capture_seq'], self.rows['rgb']['metadata']['sequence'])
        self.assertNotEqual(captured['capture_seq'], self.rows['anchor_b']['metadata']['sequence'])
        self.assertEqual(captured['ref']['available_at'], self.original['first_consumer_received_at'])
        self.assertGreater(captured['ref']['available_at'], self.event['payload']['available_at'])
        older = {**deepcopy(expected), 'schema': 'home-live-stereo-replay-inputs-2', 'version': 2}
        with self.assertRaisesRegex(ValueError, 'expected_pins_required'): replay._expectations(older)
        altered = deepcopy(manifest)
        altered['worker_config']['live_manifest']['sources'][source_id]['capture_clock']['mapping_digest'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'clock_policy_mismatch'):
            replay._capture_metadata(self.original, altered, expected)
        with self.assertRaisesRegex(ValueError, 'clock_policy_required'):
            archive.LabOriginalArchive(Path(self.fixture.directory.name) / 'old-cannot-use-new-clock.jsonl',
                binding=self.binding, capture_clock_policy=self.policy)


if __name__ == '__main__': unittest.main()
