"""Authored projection contracts only; no camera, model, host or authority IO."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location('apartment_room_pose_diagnostic',HERE/'apartment_room_pose_diagnostic.py')
MODULE=None
if SPEC and SPEC.origin and Path(SPEC.origin).exists():
    MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def inputs():
    scope=dict(siteId='victoria',contentRevision='authored-den-pack',frameDigest='a'*64,registrationVersion=1,
        cameraId='den',sessionId='authored-session',generation=2)
    binding=dict(session=dict(id=scope['sessionId'],site_id='victoria',camera_id='den'),arm='b',model_generation=4,media_generation=2)
    event=dict(id=7,kind='perception.output',session_id=scope['sessionId'],payload=dict(site_id='victoria',camera_id='den',
        frame_id='rgb-7',capture_epoch='capture-epoch',generation=4,observed_at=100.,available_at=100.2,valid_until=101.))
    frame=dict(id='media-7',session_id=scope['sessionId'],generation=2,sha256='b'*64,mime_type='image/png',metadata=dict(
        site_id='victoria',camera_id='den',frame_id='rgb-7',capture_epoch='capture-epoch',width=640,height=360,
        observed_at=100.,available_at=100.1,valid_until=100.9))
    candidate=dict(schema='home-lab-assessment-attempt-3',version=3,status='candidate',candidate_only=True,owner_local_only=True,
        binding_digest=digest(binding),original_event=event,original_event_sha256=digest(event),source_event_id=7,
        coordinate_frame='rectified_left',unit='m',source_times={k:event['payload'][k] for k in ('observed_at','available_at','valid_until')},
        original_frame_record=frame,media_records={'bundle':{'metadata':{'calibration_content_id':'c'*64}}},
        depth_clock={'status':'unassessed','mapping':None,'exposure_error_bound_s':None},error_bound_m=None,
        person_ref=None,subject_ref=None,candidate_produced_at=None,operational_geometry=None,
        geometry_authority=False,identity_authority=False,engagement_authority=False,absence_authority=False,protection_authority=False,beam_authority=False,
        instances=[{'instance_ref':'object-0','joints':[{'joint_id':5,'status':'candidate','camera_xyz_m':[1.,2.,3.],'pixel':[100.,110.],
            'depth_support':8,'depth_spread_m':.02,'error_bound_m':None},
            {'joint_id':7,'status':'candidate','camera_xyz_m':[2.,3.,4.],'pixel':[120.,130.],'depth_support':6,'depth_spread_m':.03,'error_bound_m':None},
            {'joint_id':9,'status':'unavailable','camera_xyz_m':None,'pixel':None,'error_bound_m':None}]}])
    collection=dict(binding={'binding':binding,'binding_digest':digest(binding)},attempts=[candidate],collection_sha256='d'*64,
        summary={'ordinary_lighting_replay':False,'qualified_error_bound_m':None})
    matrix=[[0.,-1.,0.,10.],[1.,0.,0.,20.],[0.,0.,1.,0.],[0.,0.,0.,1.]]
    alignment=dict(schema='home-apartment-camera-alignment-candidate-1',version=1,status='review_required',qualification='diagnostic_alignment',
        viewing_only=True,operational_geometry=False,physical_permission=False,
        scope={'site_id':'victoria','map_id':'victoria-den','room_id':'den'},pack={'content_revision':scope['contentRevision'],
            'registration_version':1,'frame_sha256':scope['frameDigest'],'collision_sha256':'e'*64},
        camera={'camera_id':'den','source_frame':'rectified_left','capture_epoch':'capture-epoch','calibration_content_id':'c'*64},
        matrix=matrix,matrix_sha256=digest(matrix))
    alignment_bytes=(json.dumps(alignment,sort_keys=True,indent=2)+'\n').encode()
    review=dict(schema='home-apartment-diagnostic-alignment-review-1',status='reviewed_for_diagnostic_viewing',
        alignment_sha256=hashlib.sha256(alignment_bytes).hexdigest(),matrix_sha256=digest(matrix),actual_retained_inputs_verified=True,
        input_evidence_sha256=['f'*64],reviewed_by='authored-reviewer')
    return dict(collection=collection,attempt_id=7,instance_index=0,alignment=alignment,review=review,scope=scope,
        session_state='recording',received_monotonic_s=20.,now_monotonic_s=20.1,max_hold_s=2.,replay=False,active_collision_sha256='e'*64,
        alignment_original_bytes=alignment_bytes)

class DiagnosticRoomPoseTests(unittest.TestCase):
    def call(self,**changes):
        self.assertIsNotNone(MODULE,'registered diagnostic viewing needs its own pure producer adapter')
        values=inputs();values.update(changes);return MODULE.project_diagnostic_room_pose(**values)
    def test_real_xyz_transform_is_renderable_without_claiming_source_clock_or_operational_admission(self):
        values=inputs();before=deepcopy(values);out=self.call()
        self.assertTrue(out['renderable']);self.assertEqual(out['status'],'timing_unverified')
        self.assertIsNone(out['room_pose']);self.assertFalse(out['follow_allowed']);self.assertFalse(out['physical_permission'])
        self.assertEqual(out['diagnostic_pose']['joints'][0]['position_m'],[8.,21.,3.])
        self.assertEqual(out['diagnostic_pose']['joints'][0]['camera_xyz_m'],[1.,2.,3.])
        self.assertEqual(out['diagnostic_pose']['edges'],[[5,7]])
        self.assertEqual(out['diagnostic_pose']['registration']['canonical_collision_digest'],'e'*64)
        self.assertEqual(out['diagnostic_pose']['source_times'],values['collection']['attempts'][0]['source_times'])
        self.assertIsNone(out['diagnostic_pose']['error_bound_m']);self.assertIsNone(out['timing']['exposure_error_bound_s'])
        self.assertEqual(values,before)
    def test_review_required_is_not_itself_a_review_and_authored_or_changed_receipts_refuse(self):
        for mutate in [lambda v:v.update(review=None),lambda v:v['review'].update(actual_retained_inputs_verified=False),
            lambda v:v['review'].update(alignment_sha256='0'*64),lambda v:v['alignment']['matrix'][0].__setitem__(3,11.)]:
            values=inputs();mutate(values);self.assertFalse(self.call(**values)['renderable'])
    def test_exact_alignment_file_bytes_not_json_numeric_reserialization_pin_the_review(self):
        values=inputs();values['alignment_original_bytes']=json.dumps(values['alignment'],separators=(',',':')).encode()
        self.assertFalse(self.call(**values)['renderable'],'different file bytes need their own reviewed receipt even if parsed values match')
        values['review']['alignment_sha256']=hashlib.sha256(values['alignment_original_bytes']).hexdigest()
        self.assertTrue(self.call(**values)['renderable'])
        values['alignment_original_bytes']=b'{"matrix":[],"matrix":[]}'
        self.assertFalse(self.call(**values)['renderable'])
    def test_missing_anatomy_and_instance_identity_are_not_completed_or_reassociated(self):
        out=self.call();pose=out['diagnostic_pose']
        self.assertEqual([j['id'] for j in pose['joints']],[5,7]);self.assertIsNone(pose['anchor_joint_id'])
        self.assertEqual(pose['instance_ref'],'object-0');self.assertIsNone(pose['person_ref']);self.assertIsNone(pose['subject_ref'])
        self.assertFalse(self.call(instance_index=1)['renderable'])
    def test_scope_epoch_calibration_original_event_and_nonrigid_matrix_refuse(self):
        mutations=[lambda v:v['scope'].update(frameDigest='0'*64),lambda v:v['scope'].update(generation=3),
            lambda v:v['alignment']['camera'].update(capture_epoch='other'),lambda v:v['alignment']['camera'].update(calibration_content_id='0'*64),
            lambda v:v['collection']['attempts'][0]['original_event']['payload'].update(frame_id='other'),
            lambda v:v['alignment']['matrix'][0].__setitem__(0,2.),
            lambda v:v['collection']['attempts'][0]['instances'][0]['joints'][0].update(camera_xyz_m=[float('nan'),1.,2.])]
        for mutate in mutations:
            values=inputs();mutate(values);self.assertFalse(self.call(**values)['renderable'])
    def test_source_stop_and_receipt_retirement_withdraw_live_diagnostics_but_explicit_replay_is_recorded(self):
        self.assertFalse(self.call(now_monotonic_s=22.)['renderable'])
        self.assertFalse(self.call(session_state='stopped')['renderable'])
        replay=self.call(session_state='stopped',replay=True,now_monotonic_s=50.)
        self.assertTrue(replay['renderable']);self.assertEqual(replay['status'],'recorded');self.assertFalse(replay['follow_allowed'])
    def test_nonrigid_or_reflected_transform_cannot_pass_even_with_matching_review_digests(self):
        for row,column,value in [(0,1,-2.),(2,2,-1.)]:
            values=inputs();a=values['alignment'];a['matrix'][row][column]=value;a['matrix_sha256']=digest(a['matrix'])
            values['alignment_original_bytes']=(json.dumps(a,sort_keys=True,indent=2)+'\n').encode()
            values['review'].update(matrix_sha256=a['matrix_sha256'],alignment_sha256=hashlib.sha256(values['alignment_original_bytes']).hexdigest())
            self.assertFalse(self.call(**values)['renderable'])
    def test_latest_only_source_never_renews_repeated_event_receipts_or_accepts_callbacks_after_stop(self):
        v=inputs();now=[20.]
        source=MODULE.DiagnosticPoseReceiptTracker(scope=v['scope'],alignment=v['alignment'],review=v['review'],
            active_collision_sha256=v['active_collision_sha256'],monotonic=lambda:now[0],alignment_original_bytes=v['alignment_original_bytes'])
        self.assertTrue(source.ingest(v['collection'],attempt_id=7,session_state='recording')['renderable'])
        now[0]=21.;self.assertTrue(source.ingest(v['collection'],attempt_id=7,session_state='recording')['renderable'])
        now[0]=22.;self.assertFalse(source.ingest(v['collection'],attempt_id=7,session_state='recording')['renderable'])
        self.assertEqual(source.snapshot()['reason'],'diagnostic_receipt_expired')
        source.stop();now[0]=22.1
        self.assertFalse(source.ingest(v['collection'],attempt_id=7,session_state='recording')['renderable'])
    def test_latest_only_source_rejects_changed_originals_and_keeps_caller_mutations_out(self):
        v=inputs();source=MODULE.DiagnosticPoseReceiptTracker(scope=v['scope'],alignment=v['alignment'],review=v['review'],
            active_collision_sha256=v['active_collision_sha256'],monotonic=lambda:20.,alignment_original_bytes=v['alignment_original_bytes'])
        source.ingest(v['collection'],attempt_id=7,session_state='recording')
        v['collection']['attempts'][0]['instances'][0]['joints'][0]['camera_xyz_m'][0]=2.
        self.assertEqual(source.snapshot()['diagnostic_pose']['joints'][0]['camera_xyz_m'],[1.,2.,3.])
        result=source.ingest(v['collection'],attempt_id=7,session_state='recording')
        self.assertFalse(result['renderable']);self.assertEqual(result['reason'],'diagnostic_original_event_changed')
    def test_existing_public_viewer_cannot_admit_diagnostic_packet_as_live_follow_or_geometry(self):
        import subprocess
        v=inputs();out=self.call()
        envelope=dict(schema='home-apartment-perception-1',scope=v['scope'],session_state='recording',
            source_epoch='capture-epoch',frame=out['frame'],pose2d=None,models=[],room_pose=out['room_pose'],diagnostic_room_pose=out)
        script="""const fs=require('node:fs'),assert=require('node:assert/strict');
const View=require(process.argv[1]),input=JSON.parse(fs.readFileSync(0,'utf8'));
const view=View.project({envelope:input,scope:input.scope,clock:null});
assert.equal(view.roomPose.admitted,false);assert.equal(view.follow.allowed,false);
assert.equal(view.roomPose.joints.length,0);process.stdout.write('diagnostic_is_not_admission');"""
        result=subprocess.run(['node','-e',script,str(HERE.parents[2]/'app/src/home-apartment-perception-view.js')],
            input=json.dumps(envelope),capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,'diagnostic_is_not_admission')
    def test_actual_verified_rgbd_factory_output_flows_through_the_public_projection(self):
        # The incumbent real Store/ASGI/original-byte verifier is exercised;
        # camera, model, calibration and room transform are authored boundaries.
        import sys
        root=HERE.parents[2]
        for folder in (root/'tools/den-live-runtime',root/'tools/shared-home',root/'stack/services/home-context'):
            if str(folder) not in sys.path:sys.path.append(str(folder))
        from test_bundle_rgb_collect import BundledRgbCollectionTests
        from lab_assessment_collect import read_verified_attempts
        fixture=BundledRgbCollectionTests('runTest')
        try:
            fixture.make(pose=True);self.assertTrue(fixture.collect()['complete'])
            collection=read_verified_attempts(fixture.output);c=collection['attempts'][0];binding=collection['binding']['binding']
            values=inputs();values.update(collection=collection,attempt_id=c['source_event_id'])
            values['scope'].update(sessionId=binding['session']['id'],generation=binding['media_generation'])
            a=values['alignment'];a['camera'].update(capture_epoch=c['original_event']['payload']['capture_epoch'],
                calibration_content_id=c['media_records']['bundle']['metadata']['calibration_content_id'])
            values['alignment_original_bytes']=(json.dumps(a,sort_keys=True,indent=2)+'\n').encode()
            values['review'].update(alignment_sha256=hashlib.sha256(values['alignment_original_bytes']).hexdigest())
            out=self.call(**values)
            self.assertTrue(out['renderable'],out['reason']);self.assertEqual(out['status'],'timing_unverified')
            self.assertEqual(len(out['diagnostic_pose']['joints']),1)
            self.assertAlmostEqual(out['diagnostic_pose']['joints'][0]['camera_xyz_m'][2],1.234)
            self.assertIsNone(out['diagnostic_pose']['joints'][0]['error_bound_m'])
            self.assertEqual(out['diagnostic_pose']['provenance']['frame_media_sha256'],c['original_frame_record']['sha256'])
        finally:fixture.doCleanups()

if __name__=='__main__':unittest.main()
