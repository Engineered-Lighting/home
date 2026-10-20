"""Actual config4 Store/C image originals under an explicit private family."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
import test_bundle_rgb_input as authored
import live_capture_fixtures as capture
from preview_client import LocalBundleClient
from bundle_rgb_input import support_refs
import lab_stereo_media as media


def actual_bundled_originals(case):
    """Reuse the actual factory; double only its external camera/clock/model I/O."""
    fixture = authored.BundledRgbFactoryTests()
    case.addCleanup(fixture.doCleanups)
    fixture.setUp()
    runtime = fixture.build_bundled()
    fixture.release.set()
    def read(_client, path):
        fixture.reads.append(path)
        if path == '/frame.npz':
            fixture.now = capture.NOW + .59
            return fixture.bundle, ''
        case.assertEqual(path, '/calibration')
        return fixture.raw_calibration, ''
    with patch('bundle_rgb_input.clock_context', side_effect=fixture.clock_context), \
         patch('bundle_rgb_input.sync_directory'), \
         patch.object(LocalBundleClient, '_read', autospec=True, side_effect=read):
        fixture.consume(runtime, fixture.anchor_a)
        fixture.now = capture.NOW + .55
        fixture.consume(runtime, fixture.anchor_b)
        case.assertTrue(fixture.entered.wait(2), 'actual RegisteredImage/model boundary did not receive C')
    store, sid = fixture.store, fixture.session['id']
    support = next(row for row in store.list_media(sid)['items']
        if row['metadata'].get('stream') == 'bundled_rgb_input_original' and row['metadata'].get('status') == 'retained')
    body = json.loads(store.media_path(sid, support['id']).read_bytes())
    rows = {'support': support, **{role: store.get_media(sid, ref['id']) for role, ref in support_refs(body).items()}}
    case.assertEqual(set(rows), {'support', 'anchor_a', 'anchor_b', 'npz', 'calibration_original', 'bundle', 'rgb'})
    scope = dict(site_id='victoria', room_id='den', session_id=sid, domain_epoch='authored-bundled-domain')
    return fixture, runtime, scope, rows, body


class BundledStereoMediaTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'nt':
            # Only the incumbent unsupported Windows directory fsync port.
            replaced = patch('lab_stereo_media.sync_directory')
            replaced.start(); self.addCleanup(replaced.stop)

    def test_actual_seven_originals_roundtrip_under_only_the_bundled_binding(self):
        fixture, runtime, scope, rows, body = actual_bundled_originals(self)
        refs = [media.media_ref(row) for row in rows.values()]
        options = dict(store=fixture.store, scope=scope, lab_binding_digest='a' * 64,
            media_generation=fixture.session['generation'], current=lambda: True)
        spool = media.LabStereoMedia(Path(fixture.directory.name) / 'bundled-seven', **options,
            original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256=body['binding']['policy_sha256'])
        ack = spool.retain_media(refs)
        self.assertEqual(ack, dict(status='retained', binding_digest=media.digest(spool.binding), media_refs=refs))
        final = spool.finalize(expected_refs=refs)
        self.assertEqual(final['status'], 'finalized', final)
        reader = media.SealedStereoMedia(spool.directory, expected_binding=spool.binding,
            expected_refs=refs, manifest_digest=final['manifest_digest'])
        with patch.object(fixture.store, 'get_media', side_effect=AssertionError('sealed reader must not use Store')):
            self.assertEqual(reader(media.media_ref(rows['rgb']))['data'], fixture.store.media_path(scope['session_id'], rows['rgb']['id']).read_bytes())
            self.assertEqual(reader(media.media_ref(rows['support']))['record']['metadata']['stream'], 'bundled_rgb_input_original')
        for name, extra in (('legacy', {}), ('source5', dict(original_family='existing-api-assessment-1', assessment_policy_sha256='b' * 64))):
            old = media.LabStereoMedia(Path(fixture.directory.name) / name, **options, **extra)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'family_or_scope_refused'):
                old.retain_media([media.media_ref(rows['rgb'])])
        with self.assertRaisesRegex(ValueError, 'mixed_original_families'):
            media.LabStereoMedia(Path(fixture.directory.name) / 'mixed', **options,
                original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256='b' * 64,
                assessment_policy_sha256='c' * 64)

    def test_new_raw_calibration_family_preserves_existing_endpoint_bound(self):
        fixture, runtime, scope, rows, body = actual_bundled_originals(self)
        data = fixture.raw_calibration + b' ' * (24 * 1024 + 1 - len(fixture.raw_calibration))
        changed = fixture.store.add_media(scope['session_id'], data, 'application/json',
            deepcopy(rows['calibration_original']['metadata']), 'authored-bundled-oversized-calibration')
        spool = media.LabStereoMedia(Path(fixture.directory.name) / 'bounded-bundled-calibration',
            store=fixture.store, scope=scope, lab_binding_digest='a' * 64,
            media_generation=fixture.session['generation'], current=lambda: True,
            original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256=body['binding']['policy_sha256'])
        with self.assertRaisesRegex(ValueError, 'family_or_scope_refused'):
            spool.retain_media([media.media_ref(changed)])
        self.assertEqual(spool.media_refs(), [])


if __name__ == '__main__': unittest.main()
