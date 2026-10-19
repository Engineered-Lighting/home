"""Conditional authored source6 assessment; no household qualification.

Uses real config4/Store/ASGI collection and incumbent source6 proof/math. Only
the external I/O and independent measurement/commissioning truth are authored.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
for folder in (ROOT/'tools/den-live-runtime',ROOT/'stack/services/perception-lab',ROOT/'stack/services/home-context'):
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))
import test_desk_method_export as legacy
import test_desk_method_assessment as held
import test_lab_assessment_verified_bundled as actual
import lab_assessment_collect as collector
import desk_method_export as export
from desk_method_assessment import CausalEvaluatedDeskAssessment,digest
from journal import digest as wire_digest


def inputs(case,**kwargs):
    f,path,_=actual.actual_verified_bundled(case,**kwargs)
    verified=collector.read_verified_bundled_originals(path);col=verified['collection']
    candidate,doc,_,original,_=legacy.fixture()
    raw=deepcopy(candidate['original_survey'])
    raw['parts']['head']['outer_corners']=legacy.distinct.measured_box([1,2,2.2],[.9,.9,.9])
    raw['parts']['protection']['outer_corners']=legacy.distinct.measured_box([1.25,2,2.2],[1.2,1.2,1.2])
    candidate=legacy.distinct.guide.prepare_candidate(raw,now=lambda:90)
    context=original['context'];context['scope']=deepcopy(f.scope)
    context['person_ref'].update(**f.scope)
    # These independent literals describe a fictional stationary target. They
    # are not populated from a prediction or the measured output of the scorer.
    selected=legacy.distinct.legacy.fixture.measurement(1.,1.9,2.234,.001)
    point,axes=legacy.distinct.guide.room_point(selected,raw['references'])
    start,end,report=1800000000.30,1800000001.20,1800000001.30
    context['anchor_original_ref'].update(observed_at=start-1,available_at=start-.9)
    registration=deepcopy(original['registration']);projection=deepcopy(f.projection)
    registration['mount_revision']=projection['mount_revision']
    original.update(projection=projection,registration=registration,clock_mapping=deepcopy(f.clock_mapping))
    q=legacy.qualification(original)
    q.update(schema='home-desk-method-qualification-3',version=3,original_family=export.BUNDLED_FAMILY,
        bundle_binding=dict(scope=deepcopy(f.scope),lab_binding_digest=f.lab_binding_digest,
            bundle_input_policy_sha256=verified['input_binding']['policy_sha256']),
        depth_clock_policy=deepcopy(f.depth_clock_policy))
    q['measurement_method']=dict(schema='home-desk-nose-measurement-method-1',version=1,id='authored-exact-nose-method',
        name='visible_coco17_nose_depth_lift',selector=deepcopy(export.SELECTOR),
        lab_profile=deepcopy(col['binding']['binding']['lab_profile']),sampling=dict(radius_px=4,min_support=5,max_spread_m=.08),
        rgb_roi='selected_joint',projection_ref=dict(id=projection['id'],digest=wire_digest(projection)),
        max_abs_skew_s=.1,max_depth_age_s=3,
        implementation_sha256={name:hashlib.sha256((ROOT/'stack/services/perception-lab'/name).read_bytes()).hexdigest()
            for name in export.METHOD_FILES})
    coverage=q['coverage']
    coverage.update(schema='home-desk-method-coverage-original-2',version=2,
        measurement_method_ref=dict(id=q['measurement_method']['id'],digest=digest(q['measurement_method'])),
        candidate_digest=candidate['digest'],reference_frame_digest=digest(raw['references']),
        collection_binding_digest=col['binding']['binding_digest'],context_digest=digest(context),
        work=deepcopy(candidate['work_patch']),parts=deepcopy(candidate['parts']),
        anchor_region=dict(kind='aabb',center_m=[1,2,2.2],half_extents_m=[.9,.9,.9]),
        max_anchor_error_m=.5,motion_bound=dict(max_speed_m_s=0.,max_accel_m_s2=0.),
        observed_at=start-10,available_at=start-9,valid_until=report+100,
        registration_valid_until=report+90,clock_valid_until=report+80,
        projection_ref=dict(id=projection['id'],digest=wire_digest(projection)),
        registration_ref=dict(id=registration['id'],digest=wire_digest(registration)))
    coverage['capture_registration'].update(calibration_digest=projection['id'],registration_digest=wire_digest(registration))
    q['measurement_binding']={k:deepcopy(coverage[k]) for k in export.MEASUREMENT_KEYS}
    q['reference_alignment'].update(utc_origin_s=start,valid_until_monotonic_ns='100000000000',uncertainty_s=0.)
    window=doc['windows'][0]
    window['request'].update(reference_parts=held.reference_parts(candidate),selected_anchor=dict(status='available',
        original_measurement=selected,point_m=point,error_axes_m=axes))
    window['started'].update(utc_s=start,monotonic_ns='1000000000')
    window['ended'].update(utc_s=end,monotonic_ns='1900000000')
    doc['reported'].update(utc_s=report,monotonic_ns='2000000000')
    doc['candidate_ref']['digest']=candidate['digest']
    req=doc['requirements']['body'];req.update(min_containment_fraction=.3,min_complete_protection_fraction=.3,
        min_reference_s=.1,max_work_center_error_m=.1,max_capture_to_available_s=.5)
    doc['requirements']['sha256']=digest(req)
    return f,candidate,doc,verified,q


def evaluate(c,d,verified,q,*,trusted=True,alignment=None):
    request=export.alignment_request(q,verified['collection'])
    leaves=export.build_bundled_alignment(q,verified,request) if alignment is None else alignment
    return export.evaluate_resolved_method(c,d,verified['collection'],qualification=q,alignment=leaves,
        expected_qualification_sha256=digest(q) if trusted else 'f'*64,now=d['reported']['utc_s'],bundled_collection=verified)


class BundledMethodExportTests(unittest.TestCase):
    def test_actual_originals_spatial_diagnostic_positive_and_full_export_blocked(self):
        _,c,d,verified,q=inputs(self)
        originals=deepcopy(verified)
        value=evaluate(c,d,verified,q);r=value['report'];saved=value['commissioning']
        self.assertEqual(verified,originals)
        self.assertEqual(r['version'],4)
        self.assertEqual(r['diagnostic_measurement']['status'],'measured_residuals',r)
        self.assertLess(r['diagnostic_measurement']['selected_anchor_residual_m']['max'],.2)
        self.assertEqual(r['decision'],'block',r)
        self.assertIsNone(r['availability']);self.assertIsNone(r['capture_to_available_s'])
        self.assertIsNone(r['method_availability']);self.assertEqual(r['usable_service_status'],'not_measured')
        self.assertIsNotNone(r['retained_input_availability'])
        self.assertIsNone(saved)
        self.assertIn('contemporaneous_exact_method_service_timing_unavailable',r['missing_inputs'])
        self.assertIsNone(r['method_qualified_error_bound_m'])
        self.assertEqual(r['export_status'],'unavailable')
        self.assertIsNone(r['operational_geometry']);self.assertFalse(any(r['authority'].values()))
        # Nominal private camera slots are not the operational ROI sampler.
        changed=deepcopy(verified)
        for a in changed['collection']['attempts']:
            for inst in a['instances']:
                for j in inst['joints']:
                    if j['status']=='candidate':j['camera_xyz_m']=[99.,99.,99.]
        other=evaluate(c,d,changed,q)
        self.assertEqual(other['report']['diagnostic_measurement'],r['diagnostic_measurement'])

    def test_public_descriptor_alias_extra_and_missing_original_refuse(self):
        _,c,d,verified,q=inputs(self)
        for mutation in ('alias','extra','missing'):
            changed=deepcopy(verified);row=changed['captures'][0]['records']['rgb']['record']
            if mutation=='alias':row['media_id']='foreign'
            elif mutation=='extra':row['unpublished_key']=True
            else:row.pop('created_at')
            # Preserve the original descriptor relationship so refusal occurs
            # at the explicit public-to-private proof port, not a frame mock.
            changed['captures'][0]['frame_record']=deepcopy(row)
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,
                    'closed_public_bundled_descriptor_required|public_bundled_descriptor_alias_changed'):
                evaluate(c,d,changed,q)

    def test_missing_hidden_coverage_keeps_numeric_diagnostic_and_blocks_export(self):
        _,c,d,verified,q=inputs(self);q['coverage']=None
        value=evaluate(c,d,verified,q)
        self.assertEqual(value['report']['diagnostic_measurement']['status'],'measured_residuals')
        self.assertIsNone(value['report']['method_qualified_error_bound_m'])
        self.assertIsNone(value['commissioning'])
        self.assertIn('independent_complete_method_qualification_unavailable',value['report']['missing_inputs'])

    def test_future_depth_keeps_diagnostic_but_cannot_supply_method_availability(self):
        _,c,d,verified,q=inputs(self,depth_delta_s=.02)
        value=evaluate(c,d,verified,q)
        self.assertEqual(value['report']['diagnostic_measurement']['status'],'measured_residuals')
        self.assertEqual(value['report']['retained_input_availability']['complete_s'],0.)
        self.assertEqual(value['report']['missing_selected_anchor_attempts'],1)
        self.assertIsNone(value['commissioning'])

    def test_missing_proof_stays_in_denominator_and_changed_proof_refuses(self):
        _,c,d,verified,q=inputs(self)
        missing=deepcopy(verified);row=missing['captures'][0]
        row.update(records={},frame_record=None,capture_started=None,capture_terminal=None)
        value=evaluate(c,d,missing,q)
        self.assertEqual(value['report']['attempt_count'],1)
        self.assertEqual(value['report']['missing_selected_anchor_attempts'],1)
        self.assertIsNone(value['commissioning'])
        request=export.alignment_request(q,verified['collection'])
        alignment=export.build_bundled_alignment(q,verified,request)
        alignment['exposures'][0]['clock_original']['clock']['uncertainty_s']=0.
        with self.assertRaisesRegex(ValueError,'bundled_alignment_original_proof_changed'):
            evaluate(c,d,verified,q,alignment=alignment)
        changed=deepcopy(verified);changed['captures'][0]['records']['npz']['data']=b'foreign'
        with self.assertRaises(ValueError):evaluate(c,d,changed,q)

    def test_changed_sampler_profile_or_untrusted_qualification_cannot_export(self):
        _,c,d,verified,q=inputs(self)
        for key,value in (('rgb_roi','full_image'),('selector',dict(layout='coco17',joint_id=1,anchor_part='head'))):
            changed=deepcopy(q);changed['measurement_method'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):evaluate(c,d,verified,changed)
        for change in ('radius','profile'):
            changed=deepcopy(q)
            if change=='radius':changed['measurement_method']['sampling']['radius_px']=5
            else:changed['measurement_method']['lab_profile']['artifact_sha256']='f'*64
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'coverage_measurement_method_changed'):
                evaluate(c,d,verified,changed)
        self.assertIsNone(evaluate(c,d,verified,q,trusted=False)['commissioning'])

    def test_two_phase_core_retains_full_method_and_alignment_before_report(self):
        _,c,d,verified,q=inputs(self);records=[];now=[1800000000.0];mono=[700000000]
        class Resolver:
            def qualification(self):return deepcopy(q)
            def align(self,request):return export.build_bundled_alignment(q,verified,request)
        def record(action):records.append(deepcopy(action));return dict(status='retained',sha256=digest(action))
        s=CausalEvaluatedDeskAssessment(c,clock=lambda:now[0],monotonic=lambda:mono[0],
            verified_collection_loader=lambda:deepcopy(verified),record=record,qualification_resolver=Resolver(),
            expected_qualification_sha256=digest(q),reference_boot_id=lambda:'authored-boot')
        s.freeze(d['protocol']['body'],d['requirements']['body'])
        w=d['windows'][0];now[0],mono[0]=w['started']['utc_s'],int(w['started']['monotonic_ns'])
        raw={k:deepcopy(w['request']['selected_anchor'][k]) for k in ('status','original_measurement')}
        s.start('held-1','neutral',w['request']['reference_parts'],0,'focused',raw)
        now[0],mono[0]=w['ended']['utc_s'],int(w['ended']['monotonic_ns']);s.end('held-1')
        now[0],mono[0]=d['reported']['utc_s'],int(d['reported']['monotonic_ns']);r=s.report()
        self.assertEqual(r['version'],4)
        self.assertEqual([a['kind'] for a in records],['freeze','start','end','alignment','report'])
        self.assertEqual(records[0]['body']['qualification']['measurement_method'],q['measurement_method'])
        self.assertEqual(records[-2]['body']['resolved']['version'],2)
        self.assertIn('clock_original',records[-2]['body']['resolved']['exposures'][0])
        self.assertNotIn('camera_xyz_m',str(r));self.assertNotIn('device_timestamp_seconds',str(r))
        def forbidden(_):raise AssertionError('unmeasured service cannot reach an export writer')
        with self.assertRaisesRegex(ValueError,'qualified_compatible_export_unavailable'):s.export(forbidden)


if __name__=='__main__':unittest.main()
