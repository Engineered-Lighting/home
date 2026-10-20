"""Authored independent inputs and clock bounds; no real qualification."""
from copy import deepcopy
import math
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import ExitStack

import test_desk_distinct_parts as distinct
from test_desk_method_assessment import reference_parts, verified_collection, requirements
from desk_method_assessment import digest, encoded, EvaluatedDeskAssessment, DeskAssessmentRefusal
from journal import digest as wire_digest


def fixture():
    raw = distinct.survey()
    raw['parts']['head']['outer_corners'] = distinct.measured_box([1, 2, 3], [.2, .2, .2])
    raw['parts']['protection']['outer_corners'] = distinct.measured_box([1, 2, 3], [.3, .3, .3])
    candidate, old_assessment, anchor, binding = distinct.inputs(raw)
    # A pre-Freeze context can contain only already-existing originals. The
    # incumbent live resolver supplies a fresh anchor at later consumption.
    anchor.update(point_m=[1, 2, 3], observed_at=99., available_at=99.1, valid_until=101.)
    collection = verified_collection(); inner = collection['binding']['binding']
    inner['session'].update(site_id='victoria', camera_id='den', id='authored')
    collection['binding']['binding_digest'] = digest(inner)
    attempts = []
    for index, observed in enumerate((100.01, 102.01), 1):
        a = deepcopy(collection['attempts'][0])
        event = dict(id=index, session_id='authored', kind='perception.output', payload=dict(
            frame_id='rgb-'+str(index), capture_epoch='capture-1', generation=1,
            observed_at=observed,available_at=observed+.10,valid_until=observed+2.19))
        a.update(id='attempt-'+str(index), source_event_id=index, binding_digest=digest(inner),
            original_event=event, original_event_sha256=digest(event),
            source_times=dict(observed_at=observed, available_at=observed+.10, valid_until=observed+2.19))
        a['instances'][0]['joints'][0]['camera_xyz_m'] = [0, 0, 2]
        attempts.append(a)
    collection.update(attempts=attempts)
    refs = reference_parts(candidate)
    selected = distinct.legacy.fixture.measurement(1.03, 2.04, 3, .001)
    point, axes = distinct.guide.room_point(selected, raw['references'])
    window = dict(id='held-1', request=dict(id='held-1', configuration_id='neutral', reference_parts=refs,
        reference_uncertainty_m=0, activity='focused', selected_anchor=dict(status='available',
            original_measurement=selected, point_m=point, error_axes_m=axes)),
        started=dict(utc_s=100.,monotonic_ns='1000000000',boot_id='authored-boot'),
        ended=dict(utc_s=104.,monotonic_ns='5000000000',boot_id='authored-boot'),
        end_receipt=dict(duration_s=4.))
    req = requirements(); req.update(min_containment_fraction=.95,min_complete_protection_fraction=.95,
        max_work_center_error_m=.1,max_capture_to_available_s=.2)
    doc = dict(windows=[window], requirements=dict(body=req,sha256=digest(req)),
        protocol=dict(body=dict(id='protocol',reference_method='tape',supported_configuration_ids=['neutral'],max_windows=1),
            sha256=digest('protocol')),candidate_ref=dict(id='candidate',digest=candidate['digest']),
        reported=dict(utc_s=104.5,monotonic_ns='5500000000',boot_id='authored-boot'))
    context = {k:deepcopy(binding[k]) for k in ('scope','person_ref','subject_ref','device_id','workstation_id',
        'method_ref','association_method_ref','source_generation','source_position','anchor_original_ref','parent_publication_refs')}
    context['anchor_original_ref'].update(digest=digest(anchor), observed_at=99.,available_at=99.1)
    registration = dict(schema='home-capture-registration-1',version=1,id='registration',
        site_id='victoria',room_id='den',camera_id='den',mount_revision='mount-1',
        frame='z_up_metric_floor0',unit='m',geometry_revision='authored-geometry',geometry_digest='d'*64,
        source_frame='rectified_left',coverage_refs=['authored-coverage'],validation_ref=distinct.legacy.ref('authored-registration'),
        room_from_rectified_left=[[1,0,0,1],[0,1,0,2],[0,0,1,1],[0,0,0,1]],error_bound_m=.01)
    registration['transform_ref']=dict(id='authored-transform',digest=digest(registration['room_from_rectified_left']))
    projection = dict(id='projection',status='validated',site_id='victoria',camera_id='den',mount_revision='mount-1',
        error_bound_m=.01,validation_receipt='authored-independent-projection',frame='rectified_left',width=640,height=400,
        intrinsics=dict(fx=450,fy=450,cx=320,cy=200),reference_intrinsics=dict(rectified_left=[[450,0,320],[0,450,200],[0,0,1]]),
        rgb_distortion_coefficients=[0]*14,reprojection=dict(receipt='authored-reprojection',p95_px=1.))
    clock = dict(mapping_id='clock',mapping_digest=binding['clock_mapping_ref']['digest'],source_epoch='capture-1',
        original_unit='unix_ns',epoch_origin_s=0,max_uncertainty_s=.001)
    protection = dict(schema='home-desk-protection-definition-1',version=1,id='delivery-protection',
        required_body_part_ids=['torso'],protected_parts=['head'],body_primitive_id='torso',protection_primitive_id='head-protection')
    # Source fixture policy pin is an authored original, not a Guide checkbox.
    candidate['original_survey']['protection_policy']['policy_ref'] = dict(id=protection['id'],digest=digest(protection))
    candidate['protection_policy_ref'] = deepcopy(candidate['original_survey']['protection_policy']['policy_ref'])
    candidate['survey_sha256'] = digest(candidate['original_survey'])
    candidate['digest'] = digest({k:v for k,v in candidate.items() if k!='digest'})
    doc['candidate_ref']['digest'] = candidate['digest']
    coverage = dict(schema='home-desk-method-coverage-original-1',version=1,id='authored-coverage',status='qualified',
        candidate_digest=candidate['digest'],configuration_ids=['neutral'],work=deepcopy(candidate['work_patch']),
        parts=deepcopy(candidate['parts']),anchor_region=dict(kind='aabb',center_m=[1,2,3],half_extents_m=[.3,.3,.3]),
        anchor_part='head',part_coverage=list(distinct.consumer.PARTS),
        hidden_configuration_basis='independent_all_supported_hidden_configurations',
        max_anchor_age_s=2.2,max_anchor_error_m=.1,motion_bound=dict(max_speed_m_s=.01,max_accel_m_s2=.001),
        observed_at=90.,available_at=91.,valid_until=200.,
        registration_valid_until=190.,clock_valid_until=180.,
        qualification_ref=distinct.legacy.ref('independent-coverage'),applicability_ref=distinct.legacy.ref('independent-applicability'),
        participant_ref=distinct.legacy.ref('independent-participant'),motion_ref=distinct.legacy.ref('independent-motion'))
    coverage['reference_frame_digest']=digest(candidate['original_survey']['references'])
    coverage['collection_binding_digest']=collection['binding']['binding_digest']
    coverage['capture_registration']=dict(registration_ref=registration['id'],registration_digest=wire_digest(registration),
        geometry_revision=registration['geometry_revision'],geometry_digest=registration['geometry_digest'],
        mount_digest='e'*64,calibration_digest=projection['id'],coverage_refs=registration['coverage_refs'],
        frame=registration['frame'],unit=registration['unit'],transform_chain_refs=[registration['transform_ref']])
    original = dict(schema='home-desk-method-evaluation-originals-1',version=1,id='authored-originals',
        context=context,projection=projection,registration=registration,clock_mapping=clock,protection_policy=protection,
        coverage=coverage,reference_alignment=dict(schema='home-desk-reference-alignment-1',version=1,
            status='qualified',boot_id='authored-boot',monotonic_origin_ns='1000000000',utc_origin_s=100.,
            valid_until_monotonic_ns='200000000000',uncertainty_s=0.,qualification_ref=distinct.legacy.ref('authored-clock')),
        exposure_alignment=[dict(source_event_id=a['source_event_id'],original_event_sha256=a['original_event_sha256'],
            frame_id=a['original_event']['payload']['frame_id'],capture_epoch='capture-1',model_generation=1,
            rgb=dict(observed_at=a['source_times']['observed_at'],uncertainty_s=0.),
            depth=dict(observed_at=a['source_times']['observed_at'],uncertainty_s=0.),
            qualification_ref=distinct.legacy.ref('authored-exposure-'+str(a['source_event_id']))) for a in attempts])
    original['coverage'].update(context_digest=digest(context),projection_ref=dict(id=projection['id'],digest=wire_digest(projection)),
        registration_ref=dict(id=registration['id'],digest=wire_digest(registration)))
    doc['protocol']['sha256']=digest(doc['protocol']['body'])
    return candidate,doc,collection,original,anchor


def qualification(original):
    q={k:deepcopy(v) for k,v in original.items() if k!='exposure_alignment'}
    q.update(schema='home-desk-method-qualification-1',version=1,
        exposure_alignment_method_ref=distinct.legacy.ref('authored-exposure-method'),
        resolver_ref=distinct.legacy.ref('authored-fixed-original-resolver'))
    return q


class AuthoredResolver:
    """Explicit authored trusted port; no real timestamp/measurement truth."""
    def __init__(self,q):self.q=deepcopy(q);self.leaves=None;self.requests=[]
    def qualification(self):return deepcopy(self.q)
    def align(self,request):
        self.requests.append(deepcopy(request))
        if self.leaves is None:return None
        rows=deepcopy(self.leaves)
        for row in rows:
            row['qualification_ref']=deepcopy(self.q['exposure_alignment_method_ref'])
            source=next(r for r in request['sources'] if r['source_event_id']==row['source_event_id'])
            row['available_at']=source['source_times']['available_at']
        return dict(schema='home-desk-exposure-alignment-originals-1',version=1,
            **{k:deepcopy(request[k]) for k in ('qualification_ref','resolver_ref','method_ref','collection_sha256')},
            request_sha256=digest(request),exposures=rows)


class EvaluatedExportTests(unittest.TestCase):
    def test_independent_point_error_availability_and_existing_consumer_export(self):
        from desk_method_export import evaluate_method
        c,d,col,o,anchor = fixture()
        value = evaluate_method(c,d,col,originals=o,expected_originals_sha256=digest(o),now=104.5)
        report, exported = value['report'],value['commissioning']
        self.assertAlmostEqual(report['selected_anchor_error_m']['max'],.05)
        self.assertAlmostEqual(report['availability']['complete_fraction'],.9725)
        self.assertAlmostEqual(report['availability']['longest_gap_s'],.11)
        self.assertIsNotNone(exported,report)
        self.assertEqual(set(exported),{'candidate','assessment','binding','projection','registration','clock_mapping','protection_policy'})
        # Authored current-resolver port uses the same incumbent replacement
        # fields as LabStereoDeskCapability; assessment bytes never change.
        anchor.update(observed_at=105.,available_at=105.1,valid_until=107.)
        current=deepcopy(exported['binding'])
        current['anchor_original_ref'].update(digest=digest(anchor),observed_at=105.,available_at=105.1)
        result = distinct.consumer.evaluate_desk_enclosure(exported['candidate'],binding=current,
            assessment=exported['assessment'],anchor=anchor,now=105.5,hazards=[])
        self.assertEqual(result['status'],'eligible_for_admission_review',result)
        self.assertIsNone(result['operational_geometry'])

    def evaluate(self, mutate=None, *, trusted=True):
        from desk_method_export import evaluate_method
        c,d,col,o,_=fixture()
        if mutate:mutate(c,d,col,o)
        return evaluate_method(c,d,col,originals=o,expected_originals_sha256=digest(o) if trusted else None,now=104.5)

    def test_original_short_lease_preserves_missing_denominator(self):
        value=self.evaluate(lambda c,d,col,o:o['coverage'].update(max_anchor_age_s=2))
        self.assertAlmostEqual(value['report']['availability']['complete_fraction'],.9475)
        self.assertIn('complete_method_availability',value['report']['failed_requirements'])
        self.assertIsNone(value['commissioning'])

    def test_missing_pose_is_a_missing_prediction_not_removed_reference_time(self):
        def missing(c,d,col,o):col['attempts'][1].update(status='refused',instances=[])
        value=self.evaluate(missing);report=value['report']
        self.assertEqual(report['missing_selected_anchor_attempts'],1)
        self.assertEqual(report['part_containment']['head']['eligible_reference_s'],4)
        self.assertAlmostEqual(report['availability']['complete_fraction'],.5)
        self.assertIsNone(value['commissioning'])

    def test_independent_reference_unavailable_shift_and_undercoverage_block(self):
        def missing(c,d,col,o):
            d['windows'][0]['request']['reference_parts']['protection']=dict(status='unavailable',shape=None,original_measurements=None)
        def shifted(c,d,col,o):
            item=d['windows'][0]['request']['selected_anchor'];item['original_measurement']['wall_distances_m'][0]+=1
            item['point_m'],item['error_axes_m']=distinct.guide.room_point(item['original_measurement'],c['original_survey']['references'])
        def under(c,d,col,o):o['coverage']['work']['half_extents_m']=[.01,.01,.01]
        for mutation,reason in ((missing,'protection_independent_reference_unavailable'),
                (shifted,'selected_anchor_error_bound'),(under,'work_containment')):
            with self.subTest(reason=reason):
                value=self.evaluate(mutation)
                self.assertIsNone(value['commissioning'])
                self.assertIn(reason,value['report']['missing_inputs']+value['report']['failed_requirements'])

    def test_unknown_clock_full_lease_and_untrusted_originals_cannot_export(self):
        changes=(lambda c,d,col,o:o['reference_alignment'].update(status='unknown'),
                 lambda c,d,col,o:o['coverage']['motion_bound'].update(max_speed_m_s=2),
                 lambda c,d,col,o:o['coverage'].update(clock_valid_until=104.5))
        for mutation in changes:
            with self.subTest(mutation=mutation):
                value=self.evaluate(mutation);self.assertIsNone(value['commissioning']);self.assertEqual(value['report']['decision'],'block')
        value=self.evaluate(trusted=False)
        self.assertIsNone(value['commissioning'])
        self.assertIn('trusted_pinned_original_resolver_unavailable',value['report']['missing_inputs'])

    def test_new_bundled_variant_is_evaluated_but_has_no_old_consumer_export(self):
        def successor(c,d,col,o):
            b=col['binding'];b.update(schema='home-lab-assessment-binding-3',version=3)
            b['binding']['collection_mode']='existing-api-bundled-rgb-1';b['binding_digest']=digest(b['binding'])
            o['coverage']['collection_binding_digest']=b['binding_digest']
            for a in col['attempts']:a.update(schema='home-lab-assessment-attempt-3',version=3,binding_digest=b['binding_digest'])
        value=self.evaluate(successor)
        self.assertAlmostEqual(value['report']['selected_anchor_error_m']['max'],.05)
        self.assertIn('new_family_operational_consumer_unavailable',value['report']['missing_inputs'])
        self.assertIsNone(value['commissioning'])

    def test_rehashed_foreign_exposure_and_edited_raw_point_refuse(self):
        for mutation,reason in ((lambda c,d,col,o:o['exposure_alignment'][0].update(frame_id='foreign'),'exposure_original_identity_changed'),
                (lambda c,d,col,o:d['windows'][0]['request']['selected_anchor']['point_m'].__setitem__(0,5),'selected_anchor_original_measurement_changed')):
            with self.subTest(reason=reason),self.assertRaisesRegex(ValueError,reason):self.evaluate(mutation)

    def test_rehashed_foreign_reference_frame_and_incomplete_calibration_block(self):
        def foreign_profile(c,d,col,o):
            col['binding']['binding']['lab_profile']['artifact_sha256']='f'*64
            col['binding']['binding_digest']=digest(col['binding']['binding'])
            for a in col['attempts']:a['binding_digest']=col['binding']['binding_digest']
        for mutation in (lambda c,d,col,o:o['coverage'].update(reference_frame_digest='f'*64),
                lambda c,d,col,o:o['projection'].pop('reprojection'),foreign_profile):
            value=self.evaluate(mutation)
            self.assertIsNone(value['commissioning']);self.assertEqual(value['report']['decision'],'block')

    def test_nonzero_uncertainty_uses_existing_consumer_exclusive_lease(self):
        def uncertain(c,d,col,o):
            o['clock_mapping']['max_uncertainty_s']=.02
            for e in o['exposure_alignment']:
                for k in ('rgb','depth'):e[k]['uncertainty_s']=.01
            col['attempts'][0]['source_times']['valid_until']=102.
            col['attempts'][1]['source_times']['valid_until']=103.5
        value=self.evaluate(uncertain)
        self.assertAlmostEqual(value['report']['availability']['complete_s'],3.26)
        c,a,anchor,b=distinct.inputs()
        a['max_anchor_age_s']=2.2;a['clock_mapping']['uncertainty_s']=.02
        anchor.update(observed_at=102.01,available_at=102.11,valid_until=103.5)
        anchor['clock']['uncertainty_s']=.01
        b['assessment_ref']['digest']=digest(a)
        b['anchor_original_ref'].update(digest=digest(anchor),observed_at=102.01,available_at=102.11)
        result=distinct.consumer.evaluate_desk_enclosure(c,binding=b,assessment=a,anchor=anchor,now=103.48,hazards=[])
        self.assertEqual(result['status'],'eligible_for_admission_review',result)
        self.assertAlmostEqual(result['geometry_proposal']['valid_until'],103.49)
        refused=distinct.consumer.evaluate_desk_enclosure(c,binding=b,assessment=a,anchor=anchor,now=103.49,hazards=[])
        self.assertEqual(refused['status'],'unavailable')
        def exceeded(c,d,col,o):o['exposure_alignment'][0]['depth']['uncertainty_s']=.002
        with self.assertRaisesRegex(ValueError,'exposure_clock_bound_exceeded'):self.evaluate(exceeded)

    def make_session(self, fail=None):
        c,d,col,o,_=fixture();now,mono,records,loads=[100.],[1000000000],[],[]
        def record(action):
            records.append(deepcopy(action))
            if action['kind']==fail:return dict(status='unconfirmed')
            return dict(status='retained',sha256=digest(action))
        def load():loads.append(True);return deepcopy(col)
        s=EvaluatedDeskAssessment(c,clock=lambda:now[0],monotonic=lambda:mono[0],verified_collection_loader=load,
            record=record,qualification_resolver=lambda:deepcopy(o),expected_originals_sha256=digest(o),reference_boot_id=lambda:'authored-boot')
        return s,d,now,mono,records,loads

    def begin(self,s,d):
        s.freeze(d['protocol']['body'],d['requirements']['body'])
        request=d['windows'][0]['request']
        selected={k:deepcopy(request['selected_anchor'][k]) for k in ('status','original_measurement')}
        return s.start('held-1','neutral',request['reference_parts'],0,'focused',selected)

    def test_durable_freeze_start_end_report_and_private_export(self):
        s,d,now,mono,records,loads=self.make_session()
        self.begin(s,d);self.assertFalse(loads)
        now[0],mono[0]=104.,5000000000;s.end('held-1');now[0],mono[0]=104.5,5500000000
        report=s.report();self.assertEqual(report['export_status'],'available_for_private_save',report)
        self.assertEqual(len(loads),1)
        self.assertEqual(records[0]['schema'],'home-desk-held-action-2')
        self.assertIn('independent_originals',records[0]['body'])
        self.assertEqual(records[-1]['body']['commissioning']['assessment']['assessment_report_ref']['digest'],digest(report))
        writes=[]
        def writer(value):writes.append(value);return dict(status='retained',sha256=digest(value))
        saved=s.export(writer);self.assertEqual(saved['status'],'export_saved');self.assertEqual(s.export(writer),saved)
        self.assertEqual(len(writes),1);self.assertFalse(any(saved['authority'].values()))

    def test_unknown_ACK_poison_prevents_retries_and_export(self):
        for fail in ('freeze','start','end','report','export'):
            with self.subTest(fail=fail):
                s,d,now,mono,records,loads=self.make_session(fail)
                def end():now[0],mono[0]=104.,5000000000;return s.end('held-1')
                def report():now[0],mono[0]=104.5,5500000000;return s.report()
                def start():
                    r=d['windows'][0]['request'];return s.start('held-1','neutral',r['reference_parts'],0,'focused',
                        {k:r['selected_anchor'][k] for k in ('status','original_measurement')})
                ops=(('freeze',lambda:s.freeze(d['protocol']['body'],d['requirements']['body'])),('start',start),
                    ('end',end),('report',report),('export',lambda:s.export(lambda v:dict(status='retained',sha256=digest(v)))))
                for kind,op in ops:
                    if kind==fail:
                        with self.assertRaises(ValueError):op()
                        before=(len(records),len(loads))
                        with self.assertRaisesRegex(ValueError,'reference_recording_incomplete|qualified_compatible_export_unavailable'):s.report()
                        self.assertEqual((len(records),len(loads)),before);break
                    op()

    def test_public_guide_raw_anchor_to_report_private_export_and_stop(self):
        c,d,col,o,_=fixture();now,mono=[100.],[1000000000]
        q=qualification(o);resolver=AuthoredResolver(q)
        with tempfile.TemporaryDirectory(prefix='authored-evaluated-guide-') as directory, ExitStack() as cleanup:
            base=Path(directory)
            # Only Windows' external directory-fsync port is authored. Actual
            # HTTP, core, raw derivation, exclusive files and file fsync remain.
            with patch.object(distinct.guide,'sync_reference_directory',return_value=None):
                server=distinct.guide.Guide(base/'unused.json',seconds=20,assessment_candidate=c,
                    assessment_collection_loader=lambda:deepcopy(col),assessment_directory=base/'references',
                    assessment_clock=lambda:now[0],assessment_monotonic=lambda:mono[0],evaluate_desk_method=True,
                    evaluation_resolver=resolver,evaluation_qualification_sha256=digest(q),
                    evaluation_boot_id=lambda:'authored-boot')
                thread=threading.Thread(target=server.run,daemon=True);thread.start()
                cleanup.callback(lambda:(server.stopped.set(),thread.join(2)))
                def post(path,value):
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                    try:
                        connection.request('POST',path,encoded(value),headers={'Origin':server.origin,
                            'X-Desk-Nonce':server.nonce,'Content-Type':'application/json'})
                        reply=connection.getresponse();return reply.status,json.loads(reply.read())
                    finally:connection.close()
                protocol={k:v for k,v in d['protocol']['body'].items() if k!='id'}
                self.assertEqual(post('/assessment/freeze',dict(protocol=protocol,requirements=d['requirements']['body']))[0],409)
                self.assertEqual(post('/evaluation/freeze',dict(protocol=protocol,requirements=d['requirements']['body']))[0],200)
                parts={}
                for name,item in d['windows'][0]['request']['reference_parts'].items():
                    raw=item['original_measurements'];parts[name]=dict(status='available',outer_corners=raw['outer_corners'],thickness_m=raw['thickness_m'])
                request=dict(configuration_id='neutral',references=c['original_survey']['references'],parts=parts,
                    reference_uncertainty_m=0,activity='focused',selected_anchor=dict(status='available',
                        original_measurement=d['windows'][0]['request']['selected_anchor']['original_measurement']))
                forged=deepcopy(request);forged['selected_anchor']['point_m']=[1,2,3]
                self.assertEqual(post('/evaluation/start',forged)[0],409)
                status,started=post('/evaluation/start',request);self.assertEqual(status,200,started)
                now[0],mono[0]=104.,5000000000
                resolver.leaves=deepcopy(o['exposure_alignment'])
                self.assertEqual(post('/evaluation/end',dict(window_id=started['window_id']))[0],200)
                now[0],mono[0]=104.5,5500000000
                status,report=post('/evaluation/report',{});self.assertEqual(status,200,report)
                self.assertEqual(report['export_status'],'available_for_private_save',report)
                self.assertNotIn('camera_xyz_m',json.dumps(report));self.assertNotIn('original_measurement',json.dumps(report))
                status,saved=post('/evaluation/export',{});self.assertEqual(status,200,saved)
                exported=json.loads((base/'references/commissioning.json').read_bytes())
                self.assertEqual(digest(exported),saved['sha256']);self.assertEqual(len(exported),7)
                self.assertEqual(post('/evaluation/export',{}),(200,saved))
                self.assertEqual(post('/stop',{})[0],200);thread.join(2);self.assertFalse(thread.is_alive())
                actions=[json.loads(row) for row in (base/'references/actions.jsonl').read_bytes().splitlines()]
                self.assertEqual([r.get('kind') for r in actions],['freeze','start','end','alignment','report','export','guide_closed'])
                self.assertEqual(actions[1]['body']['selected_anchor']['original_measurement'],request['selected_anchor']['original_measurement'])


if __name__=='__main__': unittest.main()
