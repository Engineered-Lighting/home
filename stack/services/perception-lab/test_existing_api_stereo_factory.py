"""Actual config3 factory/tap -> existing-API stereo media selection.

Only camera/model transport and Windows directory fsync are authored. This
first tracer selects original media, not geometry/person qualification. The
coordinator's joined gate owns actual Journal/worker publication assertions.
No paired-preview source or paired-original descriptor is used by this input.
"""
from copy import deepcopy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE.parent / 'home-context') not in sys.path:
    sys.path.append(str(HERE.parent / 'home-context'))

from assessment_existing_api import START_KIND, TERMINAL_KIND, encoded, sha
from depth_clock import DepthClock
from preview_client import LocalBundleClient
from test_existing_api_assessment_factory import ExistingApiAssessmentFactoryTests
import live_capture_fixtures as capture
from lab_existing_api_stereo import ExistingApiStereoSource, ExistingApiStereoRefusal, validate_existing_api_originals


def original_factory(case):
    """Real factory/Journal; independently authored static/person qualifications."""
    from test_lab_live_desk_anchor import GeneratedStereoFixture
    from lab_live_desk_anchor import LabLiveDeskAnchor, body_ref
    f = GeneratedStereoFixture(case, original_family='existing-api-assessment-1')
    descriptor = f.admit()
    expected = dict(scope=deepcopy(f.scope), lab_binding_digest=f.lab_binding_digest,
        assessment_policy_sha256=f.runtime.existing_api_assessment.policy_sha256)
    source = ExistingApiStereoSource(f.store, expected_assessment_binding=expected,
        assessment_tap=f.runtime.existing_api_assessment,
        depth_clock=DepthClock(f.store, site_id='victoria', camera_id='den', clock=f.depth_clock_policy,
            calibration=f.projection), current=lambda _: True, clock=lambda:f.now)
    binding = dict(scope=f.scope, lab_binding_digest=f.lab_binding_digest,
        media_generation=f.session['generation'], camera_id='den', original_family='existing-api-assessment-1',
        assessment_policy_sha256=expected['assessment_policy_sha256'])
    def retain(refs):
        ack = f.retain_media(refs)
        ack['binding_digest'] = sha(encoded(binding))
        return ack
    anchor = LabLiveDeskAnchor(f.store, scope=f.scope, lab_binding_digest=f.lab_binding_digest,
        stereo_policy_ref=body_ref(f.qualification(descriptor)['stereo_policy']),
        resolve_qualification=f.qualification, retain_media=retain, resolve_media=f.resolve_media,
        original_retainer=f.retain_original, clock=lambda:f.now, existing_api_source=source)
    return f, descriptor, source, anchor


class ExistingApiStereoFactoryTests(unittest.TestCase):
    def test_runner_stop_during_retainer_prevents_selected_anchor_and_raw_original(self):
        f, descriptor, source, anchor = original_factory(self)
        retain = anchor.retain_media
        def close_after_retention(refs):
            ack = retain(refs)
            f.runtime.runners[f.session['id']].close()
            return ack
        anchor.retain_media = close_after_retention
        result = anchor.select(descriptor, f.now)
        self.assertEqual(result['status'], 'unavailable', {k:result.get(k) for k in ('status','reason')})
        self.assertTrue(f.runtime.runners[f.session['id']].closed)
        self.assertEqual(f.retained, [])
        self.assertTrue(all('selected' not in entry for entry in anchor.prepared.values()))

    def test_reclosed_future_terminal_envelope_cannot_backdate_support_receipt(self):
        f, descriptor, source, anchor = original_factory(self)
        result = anchor.select(descriptor, f.now)
        self.assertEqual(result['status'], 'selected', {k:result.get(k) for k in ('status','reason')})
        changed = deepcopy(f.retained[0])
        changed['support']['assessment_originals']['terminal']['created_at'] = f.now + .1
        # Reclose the complete changed original. Refusal must be temporal, not
        # an old outer hash mismatch or a failure to load the selected bytes.
        changed_bytes = encoded(changed)
        self.assertNotEqual(sha(changed_bytes), sha(encoded(f.retained[0])))
        with self.assertRaisesRegex(ExistingApiStereoRefusal, 'existing_api_support_precedes_originals'):
            validate_existing_api_originals(changed['support'], expected_assessment_binding=source.binding(),
                resolve_media=f.resolve_media)

    def test_actual_factory_offer_then_pending_terminal_then_original_depth_selection(self):
        f = ExistingApiAssessmentFactoryTests()
        self.addCleanup(f.doCleanups)
        f.setUp()
        runtime = f.build()
        scope = dict(site_id='victoria', room_id='den', session_id=f.session['id'], domain_epoch='authored-domain')
        expected = dict(scope=scope, lab_binding_digest='b' * 64,
                        assessment_policy_sha256=sha(encoded(f.config['existing_api_assessment'])))
        _, policy, _, _, _ = capture.setup()
        projection = deepcopy(policy['projection'])
        projection.update(site_id='victoria', camera_id='den', mount_signature=deepcopy(policy['mount_signature']))
        # Independent fictional commissioning; production DepthClock still
        # validates actual Store originals and performs its real mapping.
        depth_clock = DepthClock(f.store, site_id='victoria', camera_id='den',
                                 clock=policy['depth_clock'], calibration=projection)
        def current(descriptor):
            session = f.store.get_session(f.session['id'])
            return (not runtime.closed and not runtime.preview_pumps[f.session['id']].stopped.is_set()
                    and not runtime.runners[f.session['id']].closed
                    and session['state'] == 'recording'
                    and session['generation'] == descriptor['media_generation']
                    and runtime.runners[f.session['id']].generation == descriptor['model_generation']
                    and runtime.existing_api_assessment.policy_sha256 == expected['assessment_policy_sha256'])
        source = ExistingApiStereoSource(f.store, expected_assessment_binding=expected,
            assessment_tap=runtime.existing_api_assessment,
            depth_clock=depth_clock, current=current, clock=lambda: f.now)
        holder = {}
        def read(client, path):
            if path == '/calibration':
                # Actual model execution can finish before the tap terminal.
                # Keep the exact original source clock while observing this.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    outputs = [row for row in f.store.list_events(f.session['id'])
                               if row['kind'] == 'perception.output' and row['payload']['arm'] == 'b']
                    if outputs: break
                    time.sleep(.005)
                self.assertTrue(outputs, 'bounded authored model output required')
                event = outputs[-1]
                rgb = f.store.get_media(f.session['id'], event['payload']['media_ids'][0])
                frame = deepcopy(rgb['metadata'])
                descriptor = dict(schema='home-lab-admitted-capture-1', version=1,
                    scope=deepcopy(scope), lab_binding_digest=expected['lab_binding_digest'],
                    original=dict(schema='home-original-lab-observation-1', version=1,
                        event=deepcopy(event), frame_metadata=deepcopy(frame), first_consumer_received_at=f.now),
                    frame_metadata=frame, source_generation=1,
                    source_position=dict(capture_epoch=frame['capture_epoch'], capture_seq=frame['sequence'], revision=0),
                    model_generation=event['payload']['generation'], media_generation=f.session['generation'])
                holder['descriptor'] = descriptor
                events = f.store.list_events(f.session['id'])
                self.assertEqual(sum(row['kind'] == START_KIND for row in events), 1)
                self.assertFalse(any(row['kind'] == TERMINAL_KIND for row in events))
                self.assertEqual(source.select(descriptor, received_at=f.now),
                    dict(status='pending', reason='assessment_capture_terminal_pending'))
                f.now += .05  # Actual late original receipt, not backdated to model.
            return f.read(client, path)
        with patch.object(LocalBundleClient, '_read', autospec=True, side_effect=read):
            runtime.preview_pumps[f.session['id']].consume(f.jpeg, f.header)
        descriptor = holder['descriptor']
        selected = source.select(descriptor, received_at=descriptor['original']['first_consumer_received_at'])
        self.assertEqual(selected['status'], 'selected', selected)
        self.assertEqual(set(selected), {'status', 'rows', 'assessment_start', 'assessment_terminal'})
        self.assertEqual(set(selected['rows']),
                         {'rgb', 'bundle', 'calibration_original', 'assessment_association', 'mapped_depth'})
        self.assertEqual(selected['rows']['calibration_original'][1], f.raw_calibration)
        self.assertEqual(selected['assessment_terminal']['payload']['status'], 'retained')
        self.assertGreater(selected['assessment_terminal']['payload']['processing_completed_at'],
                           descriptor['original']['event']['payload']['available_at'])
        media = f.store.list_media(f.session['id'])['items']
        self.assertFalse(any(row['metadata'].get('stream') == 'paired_preview_original' for row in media))
        association = selected['rows']['assessment_association'][0]
        self.assertEqual(association['metadata']['stream'], 'assessment_existing_api_association')
        mapped = selected['rows']['mapped_depth'][0]
        self.assertEqual(mapped['metadata']['clock_mapping_status'], 'verified')
        # Selection retry must reuse the exact original derived clock/receipt.
        f.now += .01
        self.assertEqual(source.select(descriptor, received_at=descriptor['original']['first_consumer_received_at']), selected)
        self.assertEqual(len([row for row in f.store.list_media(f.session['id'])['items']
                              if row['metadata'].get('derivation') == mapped['metadata']['derivation']]), 1)


if __name__ == '__main__': unittest.main()
