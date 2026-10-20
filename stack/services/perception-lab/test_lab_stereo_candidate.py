"""Generated actual factory/Store/Journal and retained media, no association.

Camera/model and independent static metric qualifications are fictional. This
does not qualify household geometry or replace unknown-estimator measurement.
"""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from journal import digest
from live_capture import _original_digest
from live_test_helpers import publication_ref
from live_den_mount import _runtime_module
from paired_preview_client import encoded
import live_capture_fixtures as capture
from test_lab_live_desk_anchor import GeneratedStereoFixture
from lab_stereo_candidate import measure_visible_nose_candidate, AUTHORITY


class LabStereoCandidateTests(unittest.TestCase):
    def setUp(self):
        self.f = GeneratedStereoFixture(self)
        self.descriptor = self.f.admit()
        f = self.f
        # This is only static metric commissioning. No association object or
        # seated/person original is constructed to unlock the measurement.
        p = dict(schema='home-desk-stereo-policy-1', version=1, id='authored-static-stereo-policy',
            status='independently_assessed', scope=deepcopy(f.scope), method_ref=capture.pin('authored-stereo-metric'),
            association_method_ref=deepcopy(f.association_method_ref),
            projection_ref={'id': f.projection['id'], 'digest': digest(f.projection)},
            registration_ref={'id': f.registration['id'], 'digest': digest(f.registration)},
            clock_mapping_ref={'id': f.clock_mapping['mapping_id'], 'digest': f.clock_mapping['mapping_digest']},
            depth_clock_ref={'id': f.depth_clock_policy['id'], 'digest': digest(f.depth_clock_policy)},
            calibration_content_id=f.projection['id'], requirements_ref=capture.pin('authored-nose-metric-requirements'),
            assessment_report_ref=capture.pin('authored-nose-metric-report'), applicability_ref=capture.pin('authored-nose-metric-applicability'),
            nose_error_bound_m=.02, max_abs_skew_s=.1, max_depth_age_s=3, max_speed_m_s=.01,
            max_accel_m_s2=.01, radius_px=4, min_support=5, max_spread_m=.08,
            valid_from=capture.NOW-10, available_at=capture.NOW-9, valid_until=capture.NOW+100)
        self.static = dict(stereo_policy=p, projection=deepcopy(f.projection), registration=deepcopy(f.registration),
            clock_mapping=deepcopy(f.clock_mapping), depth_clock_policy=deepcopy(f.depth_clock_policy))
        self.policy_ref={'id':p['id'],'digest':digest(p)}
        pairs = [row for row in f.store.list_media(f.session['id'])['items']
                 if row['metadata'].get('stream')=='paired_preview_original']
        pair = json.loads(f.store.media_path(f.session['id'],pairs[0]['id']).read_bytes())
        ids={'rgb':f.rgb['id'],'bundle':pair['bundle_media_id'],
             'paired_original':pairs[0]['id'],'mapped_depth':f.mapped['id']}
        refs=[{k:f.store.get_media(f.session['id'],mid)[k] for k in ('id','sha256','size')} for mid in ids.values()]
        _runtime_module();module=importlib.import_module('home_den_runtime.lab_stereo_media')
        if sys.platform=='win32':
            sync=patch.object(module,'sync_directory');sync.start();self.addCleanup(sync.stop)
        self.archive=module.LabStereoMedia(Path(f.directory.name)/'candidate-media',store=f.store,
            scope=f.scope,lab_binding_digest=f.lab_binding_digest,media_generation=f.session['generation'],
            current=lambda: f.store.get_session(f.session['id'])['state']=='recording',
            max_objects=4,max_bytes=8*1024**2,min_free_bytes=1)
        ack=self.archive.retain_media(refs);self.assertEqual(ack['status'],'retained')
        self.snapshots={role:self.archive.resolve_retained(next(ref for ref in refs if ref['id']==mid)) for role,mid in ids.items()}

    def measure(self,now=None):
        return measure_visible_nose_candidate(self.descriptor,static_originals=self.static,
            policy_ref=self.policy_ref,retained_media=self.snapshots,now=self.f.now if now is None else now)

    def test_actual_retained_pair_measures_before_any_person_or_association(self):
        self.assertNotIn('association',self.static)
        result=self.measure()
        self.assertEqual(result['status'],'candidate')
        self.assertAlmostEqual(result['point']['camera_xyz_m'][2],1.234)
        self.assertAlmostEqual(result['point']['camera_xyz_m'][0],.0375,delta=.004)
        self.assertAlmostEqual(result['point']['room_xyz_m'][1],3.234)
        self.assertEqual(result['landmark']['surface'],'visible_nose_surface_candidate')
        self.assertIsNone(result['person_ref']);self.assertIsNone(result['subject_ref'])
        for key,value in AUTHORITY.items():self.assertEqual(result[key],value,key)

    def test_repeated_reads_preserve_depth_first_times_and_intersection_lease(self):
        first=self.measure();second=self.measure(self.f.now+.1)
        self.assertEqual(first['depth'],second['depth'])
        self.assertEqual(first['valid_until'],second['valid_until'])
        self.assertEqual(first['observed_at'],second['observed_at'])
        self.assertEqual(first['depth']['available_at'],self.f.mapped['metadata']['available_at'])
        self.assertLess(first['depth']['observed_at'],first['observed_at'])

    def test_static_candidate_cannot_replace_the_operational_association(self):
        self.assertEqual(self.measure()['status'],'candidate')
        source=self.f.producer()
        source.resolve_qualification=lambda _:deepcopy(self.static)
        refused=source.select(self.descriptor,self.f.now)
        self.assertEqual(refused,{'status':'unavailable','reason':'complete_fixed_stereo_qualification_required'})
        self.assertFalse(self.f.retained)

    def test_missing_or_unknown_static_metric_error_is_not_filled_with_zero(self):
        for bad in (None,0):
            with self.subTest(error=bad):
                self.static['stereo_policy']['nose_error_bound_m']=bad
                self.policy_ref={'id':self.static['stereo_policy']['id'],'digest':digest(self.static['stereo_policy'])}
                with self.assertRaises(ValueError):self.measure()

    def test_changed_policy_cannot_replace_the_protected_expected_reference(self):
        self.static['stereo_policy']['nose_error_bound_m']=.00001
        self.assertNotEqual(digest(self.static['stereo_policy']),self.policy_ref['digest'])
        with self.assertRaisesRegex(ValueError,'independent_stereo_policy_required'):self.measure()

    def test_exact_capture_geometry_requires_registration_and_unrelated_facts_refuse(self):
        result=self.f.join.publish(self.f.row,frame_metadata=self.f.frame,received_at=self.f.now,
            journal=self.f.journal,grant=self.f.principal)
        pref=publication_ref(result['receipts'][1])
        geometry=next(row for row in self.f.journal.snapshot(self.f.principal,'victoria','den')['items'] if row['id']==pref['id'])
        fact=deepcopy(self.descriptor)
        self.descriptor['publication']=geometry;self.descriptor['publication_ref']=pref
        self.assertEqual(self.measure()['status'],'candidate')
        body=self.descriptor['publication']['payload'];body['registration']['registration_ref']='foreign-registration'
        self.descriptor['publication']['payload_digest']=digest(body);self.descriptor['publication_ref']['payload_digest']=digest(body)
        with self.assertRaisesRegex(ValueError,'candidate_original_camera_binding_mismatch'):self.measure()
        self.descriptor=fact;body=self.descriptor['publication']['payload']
        body['kind']='object_interaction';body['head']['key']['predicate']='object_interaction'
        self.descriptor['publication']['payload_digest']=digest(body);self.descriptor['publication_ref']['payload_digest']=digest(body)
        with self.assertRaisesRegex(ValueError,'candidate_capture_metadata_form_required'):self.measure()

    def test_changed_static_camera_pin_or_clock_refuses_before_measurement(self):
        self.static['registration']['room_from_rectified_left'][0][3]+=.1
        with self.assertRaisesRegex(ValueError,'registration'):self.measure()

    def test_exclusive_original_depth_lease_and_future_depth_refuse(self):
        first=self.measure()
        with self.assertRaisesRegex(ValueError,'expired'):self.measure(first['valid_until'])
        row=self.snapshots['mapped_depth']['record'];row['metadata']['observed_at']=self.f.frame['observed_at']+.01
        with self.assertRaises(ValueError):self.measure()

    def test_changed_raw_bytes_and_missing_family_cannot_be_used(self):
        original=self.snapshots['bundle']['data'];self.snapshots['bundle']['data']=original+b'changed'
        with self.assertRaises(ValueError):self.measure()
        self.snapshots['bundle']['data']=original;self.snapshots.pop('mapped_depth')
        with self.assertRaises(ValueError):self.measure()

    def test_duplicate_original_binary_identity_cannot_fill_two_roles(self):
        self.snapshots['mapped_depth']['record']['id']=self.snapshots['rgb']['record']['id']
        with self.assertRaisesRegex(ValueError,'distinct_stereo_binary_roles_required'):self.measure()

    def test_original_scope_generation_and_frame_remain_exact(self):
        for key,value in (('model_generation',99),('media_generation',99)):
            with self.subTest(key=key):
                original=self.descriptor[key];self.descriptor[key]=value
                with self.assertRaises(ValueError):self.measure()
                self.descriptor[key]=original

    def test_future_model_or_publication_cannot_produce_a_current_candidate(self):
        before=deepcopy(self.descriptor)
        for attack in ('model','publication'):
            with self.subTest(attack=attack):
                self.descriptor=deepcopy(before)
                if attack=='model':
                    original=self.descriptor['original'];original['event']['payload']['available_at']=self.f.now+.1
                    ref=self.descriptor['original_record']['ref']
                    ref['available_at']=original['event']['payload']['available_at']
                    ref['digest']=_original_digest(original)
                    body=self.descriptor['publication']['payload']
                    body['available_at']=ref['available_at'];body['original_receipt_refs']=[deepcopy(ref)]
                    self.descriptor['publication']['payload_digest']=digest(body)
                    self.descriptor['publication_ref']['payload_digest']=digest(body)
                else:
                    self.descriptor['publication']['committed_at']=self.f.now+.1
                with self.assertRaisesRegex(ValueError,'candidate_original_clock_or_availability_mismatch'):
                    self.measure()

    def test_pure_function_has_no_live_or_publication_capabilities_and_preserves_inputs(self):
        original=deepcopy((self.descriptor,self.static,self.snapshots))
        fail=lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('candidate attempted I/O'))
        with patch('store.Store.get_session',side_effect=fail),patch('depth_clock.DepthClock.derive',side_effect=fail),\
                patch('socket.socket',side_effect=fail),patch('subprocess.run',side_effect=fail),\
                patch('pathlib.Path.open',side_effect=fail):
            result=self.measure()
        self.assertEqual((self.descriptor,self.static,self.snapshots),original)
        self.assertEqual(result['operational_geometry'],None)
        # Private input integer clocks remain exact; no cast to browser time.
        self.assertEqual(json.loads(encoded(self.descriptor))['original']['frame_metadata']['source_timing_receipt']['observed_at_unix_ns'],
                         capture.NOW*1000000000)


if __name__=='__main__':unittest.main()
