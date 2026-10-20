"""Existing endpoint originals retain their own private media family."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
import assessment_existing_api as assessment
import test_assessment_existing_api as existing
from depth_clock import DepthClock
import live_capture_fixtures as capture
import lab_stereo_media as media


class ExistingApiStereoMediaTests(unittest.TestCase):
    def test_actual_tap_originals_roundtrip_only_under_the_explicit_assessment_family(self):
        fixture = existing.ExistingApiTapTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        terminal = fixture.tap()(fixture.jpeg, fixture.header)
        self.assertEqual(terminal['status'], 'retained', terminal)
        store, sid = fixture.store, fixture.session['id']
        association = store.get_media(sid, terminal['association_media_ref']['id'])
        original = json.loads(store.media_path(sid, association['id']).read_bytes())
        rgb = store.get_media(sid, original['capture']['rgb_media_ref']['id'])
        bundle = store.get_media(sid, original['bundle_media_ref']['id'])
        calibration = store.get_media(sid, original['calibration_original_media_ref']['id'])
        _, commissioned, _, _, _ = capture.setup()
        mapped = DepthClock(store, site_id='victoria', camera_id='den',
            clock=commissioned['depth_clock']).derive(sid, bundle['id'], rgb['id'])
        refs = [media.media_ref(row) for row in (rgb, bundle, calibration, association, mapped)]
        scope = dict(site_id='victoria', room_id='den', session_id=sid, domain_epoch='authored-epoch')
        if os.name == 'nt':
            # Only the unsupported directory-fsync OS operation is doubled.
            stub = patch('lab_stereo_media.sync_directory')
            stub.start(); self.addCleanup(stub.stop)
        options = dict(store=store, scope=scope, lab_binding_digest='a' * 64,
            media_generation=fixture.session['generation'], current=lambda: True)
        spool = media.LabStereoMedia(Path(fixture.directory.name) / 'assessment-spool', **options,
            original_family='existing-api-assessment-1', assessment_policy_sha256=assessment.sha(assessment.encoded(fixture.policy)))
        ack = spool.retain_media(refs)
        self.assertEqual(ack['status'], 'retained')
        sealed = spool.finalize(expected_refs=refs)
        self.assertEqual(sealed['status'], 'finalized', sealed)
        reader = media.SealedStereoMedia(spool.directory, expected_binding=deepcopy(spool.binding),
            expected_refs=refs, manifest_digest=sealed['manifest_digest'])
        with patch.object(store, 'get_media', side_effect=AssertionError('sealed replay has no Store fallback')):
            self.assertEqual(reader(media.media_ref(calibration))['data'], fixture.calibration)
            self.assertEqual(reader(media.media_ref(association))['data'], assessment.encoded(original))
        legacy = media.LabStereoMedia(Path(fixture.directory.name) / 'legacy-spool', **options)
        with self.assertRaisesRegex(ValueError, 'family_or_scope_refused'):
            legacy.retain_media([media.media_ref(association)])

    def test_calibration_family_keeps_the_existing_endpoint_byte_bound(self):
        fixture = existing.ExistingApiTapTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        terminal = fixture.tap()(fixture.jpeg, fixture.header)
        store, sid = fixture.store, fixture.session['id']
        association = store.get_media(sid, terminal['association_media_ref']['id'])
        original = json.loads(store.media_path(sid, association['id']).read_bytes())
        calibration = store.get_media(sid, original['calibration_original_media_ref']['id'])
        oversized = fixture.calibration + b' ' * (24 * 1024 + 1 - len(fixture.calibration))
        row = store.add_media(sid, oversized, 'application/json', deepcopy(calibration['metadata']),
            'authored-oversized-raw-calibration')
        if os.name == 'nt':
            stub = patch('lab_stereo_media.sync_directory')
            stub.start(); self.addCleanup(stub.stop)
        spool = media.LabStereoMedia(Path(fixture.directory.name) / 'bounded-assessment-spool',
            store=store, scope=dict(site_id='victoria', room_id='den', session_id=sid, domain_epoch='authored-epoch'),
            lab_binding_digest='a' * 64, media_generation=fixture.session['generation'], current=lambda: True,
            original_family='existing-api-assessment-1', assessment_policy_sha256=assessment.sha(assessment.encoded(fixture.policy)))
        with self.assertRaisesRegex(ValueError, 'family_or_scope_refused'):
            spool.retain_media([media.media_ref(row)])
        self.assertEqual(spool.media_refs(), [])


if __name__ == '__main__':
    unittest.main()
