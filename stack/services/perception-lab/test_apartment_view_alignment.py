"""Authored same-NPZ evidence and approximate presentation contract tests.

No household media, real camera, network, model trial or private alignment.
"""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(value):return sha(encoded(value))

class ViewAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_class=importlib.import_module('test_apartment_viewing_metric').ViewingMetricTests
        cls.fixture_class.setUpClass();cls.addClassCleanup(cls.fixture_class.doClassCleanups)

    def setUp(self):
        self.module=importlib.import_module('apartment_view_alignment')
        self.fixture=self.fixture_class('runTest');self.fixture.setUp()
        self.lease=self.fixture.prepare();self.event,self.frame=self.fixture.model_inputs(self.lease)
        self.scope=dict(siteId='victoria',contentRevision='authored-revision',frameDigest='1'*64,
            registrationVersion=1,cameraId='den',sessionId=self.lease.binding['session']['id'],generation=self.lease.binding['media_generation'])
        self.pack=dict(content_revision=self.scope['contentRevision'],registration_version=1,
            frame_sha256='1'*64,collision_sha256='2'*64)
        self.matrix=[[1,0,0,.4],[0,1,0,.2],[0,0,1,.1],[0,0,0,1]]
        self.camera=dict(camera_id='den',source_frame='rectified_left',capture_epoch=self.lease.descriptor['capture_epoch'],
            calibration_content_id=self.lease.descriptor['calibration_content_id'])
        self.candidate=dict(schema='home-apartment-camera-alignment-candidate-1',version=1,status='refused',
            qualification='diagnostic_alignment',scope=dict(site_id='victoria',map_id='victoria-den',room_id='den'),
            pack=self.pack,camera=self.camera,matrix=self.matrix,matrix_sha256=digest(self.matrix),
            inputs=dict(rgb=dict(sha256='3'*64),original_bundle=dict(sha256='4'*64),calibration=dict(sha256='5'*64),original=dict(sha256='6'*64)),
            fit=dict(best=dict(reasons=['authored_refusal'])),publication_or_state_write=False)
        self.signature=dict(grid=[8,8],shape=[400,640],cells=[[1234.]*8 for _ in range(8)])
        self.evidence=dict(original_rgb_sha256='3'*64,original_npz_sha256='4'*64,original_calibration_sha256='5'*64,
            original_metadata_sha256='6'*64,input_manifest_sha256='7'*64,projection_review_sha256='8'*64,
            render_comparison_sha256='9'*64,landmark_overlay_sha256='a'*64)
        self.doc=dict(schema='home-apartment-view-alignment-1',version=1,status='approximate_visual_alignment',
            scope=self.candidate['scope'],pack=self.pack,camera=self.camera,matrix=self.matrix,matrix_sha256=digest(self.matrix),
            source_candidate=dict(sha256=sha(encoded(self.candidate)),status='refused',refusal_reasons=['authored_refusal']),
            mount=dict(revision='authored-static-mount',signature=self.signature,signature_sha256=digest(self.signature)),
            evidence=self.evidence,label='Approximate room placement',metric_error_bound_m=None,
            owner_or_physical_calibration=False,**self.module.NO_AUTHORITY)
        self.review=dict(schema='home-apartment-view-alignment-review-1',version=1,status='reviewed_for_approximate_visualization',
            alignment_sha256=sha(encoded(self.doc)),source_candidate_sha256=sha(encoded(self.candidate)),matrix_sha256=digest(self.matrix),
            reviewed_by='Authored test review',actual_retained_inputs_verified=True,metric_error_unknown=True,
            owner_or_physical_calibration=False,evidence_sha256s=list(self.evidence.values()),**self.module.NO_AUTHORITY)

    def binding(self,doc=None,review=None,candidate=None):
        return self.module.bind_view_alignment(alignment_original_bytes=encoded(self.doc if doc is None else doc),
            review_original_bytes=encoded(self.review if review is None else review),
            source_candidate_original_bytes=encoded(self.candidate if candidate is None else candidate))

    def project(self,binding=None,**updates):
        args=dict(event=self.event,image_record=self.frame,now_s=1800000000.65,alignment=binding or self.binding(),
            scope=self.scope,active_collision_sha256='2'*64,current_mount_revision='authored-static-mount',
            session_state='recording',received_monotonic_s=10.,now_monotonic_s=10.1)
        args.update(updates);return self.module.project_approximate_ram_room_pose(self.lease,**args)

    def test_actual_supported_joint_only_and_distinct_honest_approximation(self):
        out=self.project();self.assertTrue(out['renderable'],out);self.assertEqual(out['schema'],'home-apartment-approximate-room-pose-1')
        p=out['approximate_pose'];self.assertEqual(p['qualification'],'approximate_visual_alignment');self.assertEqual(out['label'],'Approximate room placement')
        self.assertIsNone(out['room_pose']);self.assertIsNone(p['error_bound_m']);self.assertEqual([j['id'] for j in p['joints']],[0])
        self.assertEqual(p['edges'],[]);self.assertGreater(len(p['omitted_joint_ids']),0)
        joint=p['joints'][0];self.assertAlmostEqual(joint['position_m'][2],joint['camera_xyz_m'][2]+.1)
        self.assertIsNone(joint['error_bound_m']);self.assertEqual(p['registration']['source_fit_status'],'refused')
        self.assertTrue(all(out[k] is False for k in self.module.NO_AUTHORITY));self.assertEqual(self.candidate['status'],'refused')

    def test_original_doc_review_matrix_and_source_refusal_are_pinned(self):
        for which in ('doc','review','candidate'):
            d,r,c=deepcopy(self.doc),deepcopy(self.review),deepcopy(self.candidate)
            if which=='doc':d['matrix'][0][3]+=.1
            elif which=='review':r['alignment_sha256']='f'*64
            else:c['status']='review_required'
            with self.subTest(which=which),self.assertRaises(ValueError):self.binding(d,r,c)
        with self.assertRaises(ValueError):
            self.module.bind_view_alignment(alignment_original_bytes=b'{"schema":1,"schema":2}',review_original_bytes=encoded(self.review),source_candidate_original_bytes=encoded(self.candidate))

    def test_scene_epoch_calibration_mount_and_session_mismatch_withdraw(self):
        for update in (dict(scope={**self.scope,'siteId':'echo'}),dict(scope={**self.scope,'frameDigest':'f'*64}),
            dict(active_collision_sha256='f'*64),dict(current_mount_revision='moved'),dict(session_state='stopped'),
            dict(now_monotonic_s=12.)):
            with self.subTest(update=update):self.assertFalse(self.project(**update)['renderable'])
        for key in ('capture_epoch','calibration_content_id'):
            d=deepcopy(self.doc);d['camera'][key]='f'*64;r=deepcopy(self.review);r['alignment_sha256']=sha(encoded(d))
            with self.subTest(key=key),self.assertRaises(ValueError):self.binding(d,r)

    def test_changed_or_unknown_actual_depth_signature_withdraws(self):
        for value in (5000.,None):
            d=deepcopy(self.doc);d['mount']['signature']['cells']=[[value]*8 for _ in range(8)];d['mount']['signature_sha256']=digest(d['mount']['signature'])
            r=deepcopy(self.review);r['alignment_sha256']=sha(encoded(d))
            with self.subTest(value=value):self.assertFalse(self.project(self.binding(d,r))['renderable'])

    def test_repeated_poll_cannot_renew_receipt_and_stop_is_terminal(self):
        clock=[10.];tracker=self.module.ApproximatePoseReceiptTracker(alignment=self.binding(),scope=self.scope,
            active_collision_sha256='2'*64,current_mount_revision='authored-static-mount',monotonic=lambda:clock[0])
        args=dict(event=self.event,image_record=self.frame,now_s=1800000000.65,session_state='recording')
        self.assertTrue(tracker.ingest(self.lease,**args)['renderable']);clock[0]=11.9
        self.assertTrue(tracker.ingest(self.lease,**args)['renderable']);clock[0]=12.
        self.assertFalse(tracker.snapshot()['renderable']);self.assertFalse(tracker.ingest(self.lease,**args)['renderable'])
        tracker.stop();clock[0]=12.1;self.assertFalse(tracker.ingest(self.lease,**args)['renderable'])

    def test_changed_media_model_generation_or_actual_proof_refuses(self):
        for patch in (dict(image_record={**self.frame,'sha256':'f'*64}),dict(now_s=1800000003.),
            dict(scope={**self.scope,'generation':self.scope['generation']+1}),
            dict(event={**self.event,'session_id':'another-session'})):
            with self.subTest(patch=patch):self.assertFalse(self.project(**patch)['renderable'])

    def test_missing_configuration_authority_escalation_and_large_documents_refuse(self):
        for key in ('camera','mount','evidence'):
            doc=deepcopy(self.doc);del doc[key]
            with self.subTest(key=key),self.assertRaises(ValueError):self.binding(doc)
        for key in self.module.NO_AUTHORITY:
            doc=deepcopy(self.doc);doc[key]=True
            with self.subTest(authority=key),self.assertRaises(ValueError):self.binding(doc)
        with self.assertRaises(ValueError):self.module.bind_view_alignment(alignment_original_bytes=b' '*131073,
            review_original_bytes=encoded(self.review),source_candidate_original_bytes=encoded(self.candidate))

    def test_snapshot_copies_and_changed_original_do_not_retain_old_geometry(self):
        clock=[10.];tracker=self.module.ApproximatePoseReceiptTracker(alignment=self.binding(),scope=self.scope,
            active_collision_sha256='2'*64,current_mount_revision='authored-static-mount',monotonic=lambda:clock[0])
        args=dict(event=self.event,image_record=self.frame,now_s=1800000000.65,session_state='recording')
        first=tracker.ingest(self.lease,**args);first['approximate_pose']['joints'][0]['position_m'][0]=999
        self.assertNotEqual(tracker.snapshot()['approximate_pose']['joints'][0]['position_m'][0],999)
        altered=deepcopy(self.event);altered['payload']['result']['frame_id']='changed'
        self.assertFalse(tracker.ingest(self.lease,**{**args,'event':altered})['renderable']);self.assertFalse(tracker.snapshot()['renderable'])

    def test_slow_projection_cannot_return_after_first_receipt_expiry(self):
        clock=[10.];tracker=self.module.ApproximatePoseReceiptTracker(alignment=self.binding(),scope=self.scope,
            active_collision_sha256='2'*64,current_mount_revision='authored-static-mount',monotonic=lambda:clock[0])
        original=self.module.project_approximate_ram_room_pose
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);clock[0]=12.1;return result
        with patch.object(self.module,'project_approximate_ram_room_pose',side_effect=delayed):
            out=tracker.ingest(self.lease,event=self.event,image_record=self.frame,now_s=1800000000.65,session_state='recording')
        self.assertFalse(out['renderable']);self.assertEqual(out['reason'],'approximate_receipt_expired')

    def test_owner_can_count_original_buffer_aliases_without_hidden_media_copy(self):
        bound=self.binding();a=bound.retained_byte_allocations;b=bound.retained_byte_allocations
        self.assertEqual(len(a),3);self.assertTrue(all(type(v) is bytes for v in a));self.assertTrue(all(x is y for x,y in zip(a,b)))
        tracker=self.module.ApproximatePoseReceiptTracker(alignment=bound,scope=self.scope,active_collision_sha256='2'*64,
            current_mount_revision='authored-static-mount',monotonic=lambda:10.)
        self.assertTrue(all(x is y for x,y in zip(a,tracker.retained_byte_allocations)))

RETIRED_EPOCH='authored-reviewed-epoch-92008392'
TOOL=Path(__file__).resolve().parents[3]/'tools/generate-apartment-view-alignment-successor.py'

class ViewAlignmentSuccessorTests(unittest.TestCase):
    """The reviewed fit pinned to a retired epoch, plus a successor verified for the current one."""
    @classmethod
    def setUpClass(cls):ViewAlignmentTests.setUpClass.__func__(cls)
    binding=ViewAlignmentTests.binding
    project=ViewAlignmentTests.project

    def setUp(self):
        ViewAlignmentTests.setUp(self)
        self.epoch=self.camera['capture_epoch'];self.camera['capture_epoch']=RETIRED_EPOCH
        originals=self.fixture.originals
        self.verification=dict(verified_epoch=self.epoch,calibration_sha256=sha(originals['calibration']),
            calibration_content_id=self.camera['calibration_content_id'],npz_sha256=sha(originals['npz']),
            check_mount=dict(status='unchanged',compared_cells=64,changed_cells=0),
            rgb_sha256='b'*64,landmark_overlay_sha256='c'*64,verified_at='2026-10-19T12:00:00Z')
        self.successor=dict(schema='home-apartment-view-alignment-successor-1',version=1,status='verified_camera_session_successor',
            label='Approximate room placement',scope=deepcopy(self.doc['scope']),predecessor=None,matrix_sha256=None,mount=None,
            camera=dict(camera_id='den',source_frame='rectified_left',calibration_content_id=self.camera['calibration_content_id']),
            verification=self.verification,metric_error_bound_m=None,owner_or_physical_calibration=False,**self.module.NO_AUTHORITY)
        self.successor_review=dict(schema='home-apartment-view-alignment-successor-review-1',version=1,
            status='reviewed_for_verified_camera_session',successor_sha256=None,predecessor_alignment_sha256=None,
            verified_epoch=self.epoch,verification_sha256s=[self.verification[k] for k in
                ('calibration_sha256','npz_sha256','rgb_sha256','landmark_overlay_sha256')],
            landmark_overlay_matches=True,metric_error_unknown=True,owner_or_physical_calibration=False,
            reviewed_by='Authored successor review',**self.module.NO_AUTHORITY)
        self.rechain()

    def rechain(self):
        """Re-pin every derived digest after an authored change to the reviewed originals."""
        self.doc['source_candidate']['sha256']=sha(encoded(self.candidate))
        self.doc['mount']['signature_sha256']=digest(self.doc['mount']['signature'])
        self.review.update(alignment_sha256=sha(encoded(self.doc)),source_candidate_sha256=sha(encoded(self.candidate)))
        self.successor.update(predecessor=dict(alignment_sha256=sha(encoded(self.doc)),review_sha256=sha(encoded(self.review)),
            source_candidate_sha256=sha(encoded(self.candidate)),source_fit_status='refused',
            reviewed_capture_epoch=self.camera['capture_epoch']),matrix_sha256=self.doc['matrix_sha256'],
            mount=dict(revision=self.doc['mount']['revision'],signature_sha256=self.doc['mount']['signature_sha256']))
        self.successor_review['predecessor_alignment_sha256']=sha(encoded(self.doc))

    def originals(self,successor=None,review=None,*,pin=True):
        s=deepcopy(self.successor if successor is None else successor);r=deepcopy(self.successor_review if review is None else review)
        if pin:r['successor_sha256']=sha(encoded(s))
        return dict(alignment=encoded(self.doc),review=encoded(self.review),source_candidate=encoded(self.candidate),
            successor=encoded(s),successor_review=encoded(r))

    def bound(self,successor=None,review=None,*,pin=True,raw=None):
        o={**self.originals(successor,review,pin=pin),**(raw or {})}
        return self.module.bind_view_alignment_successor(alignment_original_bytes=o['alignment'],review_original_bytes=o['review'],
            source_candidate_original_bytes=o['source_candidate'],successor_original_bytes=o['successor'],
            successor_review_original_bytes=o['successor_review'])

    def refusal(self,successor=None,review=None,**kwargs):
        with self.assertRaises(self.module.ViewAlignmentRefusal) as caught:self.bound(successor,review,**kwargs)
        return str(caught.exception)

    def changed(self,which,path,value):
        out=deepcopy(self.successor if which=='successor' else self.successor_review);target=out
        for key in path[:-1]:target=target[key]
        target[path[-1]]=value;return out

    def test_matching_verified_epoch_renders_and_charges_five_allocations(self):
        old=self.project(self.binding());self.assertFalse(old['renderable'])
        self.assertEqual(old['reason'],'approximate_camera_epoch_changed')
        bound=self.bound();self.assertIs(type(bound),self.module.VerifiedSessionAlignment)
        self.assertEqual(bound.verified_epoch,self.lease.descriptor['capture_epoch'])
        out=self.project(bound);self.assertTrue(out['renderable'],out)
        r=out['approximate_pose']['registration']
        self.assertEqual(r['camera_session'],dict(status='verified_session_successor',verified_epoch=self.epoch,
            reviewed_capture_epoch=RETIRED_EPOCH,successor_digest=bound.successor_sha256,successor_review_digest=bound.successor_review_sha256))
        self.assertEqual(r['alignment_digest'],sha(encoded(self.doc)));self.assertEqual(r['source_fit_status'],'refused')
        self.assertEqual(r['camera_to_room_digest'],digest(self.matrix));self.assertIsNone(r['metric_error_bound_m'])
        self.assertEqual(out['frame']['source_epoch'],self.epoch);self.assertEqual(self.candidate['status'],'refused')
        self.assertTrue(all(out[k] is False for k in self.module.NO_AUTHORITY))
        a=bound.retained_byte_allocations;self.assertEqual(len(a),5);self.assertTrue(all(type(v) is bytes for v in a))
        self.assertTrue(all(x is y for x,y in zip(a,bound.retained_byte_allocations)))
        tracker=self.module.ApproximatePoseReceiptTracker(alignment=bound,scope=self.scope,active_collision_sha256='2'*64,
            current_mount_revision='authored-static-mount',monotonic=lambda:10.)
        self.assertTrue(all(x is y for x,y in zip(a,tracker.retained_byte_allocations)))
        self.assertTrue(tracker.ingest(self.lease,event=self.event,image_record=self.frame,now_s=1800000000.65,session_state='recording')['renderable'])
        self.assertEqual(len(self.binding().retained_byte_allocations),3,'the v1 path keeps its own type and charge')

    def test_each_refusal_has_its_own_reason(self):
        reasons={'v1_alone_new_epoch':self.project(self.binding())['reason']}
        other=self.changed('successor',('verification','verified_epoch'),'authored-other-camera-epoch')
        review=deepcopy(self.successor_review);review['verified_epoch']='authored-other-camera-epoch'
        reasons['successor_for_other_epoch']=self.project(self.bound(other,review))['reason']
        calibration=[self.refusal(self.changed('successor',('camera','calibration_content_id'),'f'*64)),
            self.refusal(self.changed('successor',('verification','calibration_content_id'),'f'*64))]
        self.assertEqual(len(set(calibration)),1);reasons['successor_calibration_mismatch']=calibration[0]
        # The recorded depth check is advisory: changed/unknown still bind.
        for status,counts in (('changed',(64,40)),('unknown',(8,0))):
            mount=dict(status=status,compared_cells=counts[0],changed_cells=counts[1])
            with self.subTest(advisory_mount=status):
                self.assertTrue(self.bound(self.changed('successor',('verification','check_mount'),mount)))
        reasons['successor_mount_counts']=self.refusal(self.changed('successor',('verification','check_mount'),
            dict(status='unchanged',compared_cells=64,changed_cells=40)))
        authority=[self.refusal(self.changed('successor',(key,),True)) for key in self.module.NO_AUTHORITY]
        authority+=[self.refusal(self.changed('successor',('metric_error_bound_m',),.05)),
            self.refusal(self.changed('successor',('owner_or_physical_calibration',),True))]
        authority+=[self.refusal(review=self.changed('review',(key,),True)) for key in (*self.module.NO_AUTHORITY,'owner_or_physical_calibration')]
        self.assertEqual(len(set(authority)),1);reasons['authority_flag_true']=authority[0]
        predecessor=[self.refusal(self.changed('successor',('predecessor',key),value)) for key,value in
            (('alignment_sha256','f'*64),('review_sha256','f'*64),('source_candidate_sha256','f'*64),('reviewed_capture_epoch',self.epoch))]
        rereviewed=deepcopy(self.review);rereviewed['reviewed_by']='A later v1 review'
        predecessor.append(self.refusal(raw=dict(review=encoded(rereviewed))))
        self.assertEqual(len(set(predecessor)),1);reasons['predecessor_sha_changed']=predecessor[0]
        reasons['matrix_sha_changed']=self.refusal(self.changed('successor',('matrix_sha256',),'f'*64))
        mount=[self.refusal(self.changed('successor',('mount','signature_sha256'),'f'*64)),
            self.refusal(self.changed('successor',('mount','revision'),'moved-mount'))]
        self.assertEqual(len(set(mount)),1);reasons['mount_sha_changed']=mount[0]
        unpinned=[self.refusal(review=self.changed('review',('successor_sha256',),'f'*64),pin=False),
            self.refusal(self.changed('successor',('verification','rgb_sha256'),'d'*64)),
            self.refusal(review=self.changed('review',('verified_epoch',),'authored-other-camera-epoch')),
            self.refusal(review=self.changed('review',('predecessor_alignment_sha256',),'f'*64)),
            self.refusal(review=self.changed('review',('landmark_overlay_matches',),False))]
        self.assertEqual(len(set(unpinned)),1);reasons['unpinned_review']=unpinned[0]
        reasons['duplicate_key']=self.refusal(raw=dict(successor=b'{"schema":1,"schema":2}'))
        reasons['oversize']=self.refusal(raw=dict(successor=b' '*16385))
        reasons['verification_incomplete']=self.refusal(self.changed('successor',('verification','verified_at'),'yesterday'))
        reasons['scope_changed']=self.refusal(self.changed('successor',('scope','room_id'),'kitchen'))
        reasons['not_a_successor']=self.refusal(self.changed('successor',('status',),'reviewed_for_approximate_visualization'))
        expected={'v1_alone_new_epoch':'approximate_camera_epoch_changed',
            'successor_for_other_epoch':'approximate_camera_epoch_unverified',
            'successor_calibration_mismatch':'approximate_successor_calibration_changed',
            'successor_mount_counts':'approximate_successor_mount_check_required',
            'authority_flag_true':'approximate_successor_approximate_only_required',
            'predecessor_sha_changed':'approximate_successor_predecessor_changed',
            'matrix_sha_changed':'approximate_successor_matrix_changed','mount_sha_changed':'approximate_successor_mount_identity_changed',
            'unpinned_review':'approximate_successor_review_required','duplicate_key':'approximate_duplicate_document_key',
            'oversize':'approximate_original_document_bytes_required','verification_incomplete':'approximate_successor_verification_required',
            'scope_changed':'approximate_successor_scope_changed','not_a_successor':'approximate_successor_document_required'}
        self.assertEqual(reasons,expected);self.assertEqual(len(set(reasons.values())),len(reasons))

    def test_frame_calibration_mount_and_session_guards_still_apply_with_a_successor(self):
        self.camera['calibration_content_id']='e'*64;self.verification['calibration_content_id']='e'*64
        self.successor['camera']['calibration_content_id']='e'*64;self.rechain()
        self.assertEqual(self.project(self.bound())['reason'],'approximate_camera_calibration_changed')
        for value,status in ((5000.,'changed'),(None,'unknown')):
            self.setUp();self.doc['mount']['signature']['cells']=[[value]*8 for _ in range(8)];self.rechain()
            with self.subTest(status=status):self.assertEqual(self.project(self.bound())['reason'],'approximate_mount_signature_'+status)
        self.setUp();bound=self.bound()
        for update,reason in ((dict(scope={**self.scope,'generation':self.scope['generation']+1}),'approximate_camera_identity_changed'),
                (dict(scope={**self.scope,'sessionId':'another-session'}),'approximate_camera_identity_changed'),
                (dict(current_mount_revision='moved'),'approximate_scene_or_mount_identity_changed'),
                (dict(now_monotonic_s=12.),'approximate_source_or_receipt_expired'),
                (dict(alignment={'verified_epoch':self.epoch}),'trusted_approximate_alignment_required')):
            with self.subTest(update=update):self.assertEqual(self.project(bound,**update)['reason'],reason)

    def test_development_preview_skips_the_per_frame_mount_check_but_strict_still_runs_it(self):
        self.doc['mount']['signature']['cells']=[[5000.]*8 for _ in range(8)];self.rechain();bound=self.bound()
        self.assertEqual(self.project(bound)['reason'],'approximate_mount_signature_changed')
        import spatial_validation
        with patch.object(spatial_validation,'check_mount',side_effect=AssertionError('development must not run check_mount')):
            out=self.project(bound,development=True)
        self.assertTrue(out['renderable'],out)
        self.assertEqual(out['approximate_pose']['registration']['mount_movement_detection'],dict(status='mount_check_skipped_development',
            result=None,compared_cells=None,changed_cells=None,physical_movement_error_bound_m=None))
        for update,reason in ((dict(scope={**self.scope,'sessionId':'another-session'}),'approximate_camera_identity_changed'),
                (dict(current_mount_revision='moved'),'approximate_scene_or_mount_identity_changed')):
            with self.subTest(reason=reason):self.assertEqual(self.project(bound,development=True,**update)['reason'],reason)

    def test_compatibility_accepts_only_the_verified_session_and_calibration(self):
        bound=self.bound();calibration=self.fixture.originals['calibration']
        ok=self.module.camera_session_compatibility(health_session=self.epoch,calibration_original_bytes=calibration,successor=bound)
        self.assertEqual(ok['status'],'compatible');self.assertEqual(ok['reasons'],[])
        self.assertEqual(ok['calibration_content_id'],self.camera['calibration_content_id'])
        self.assertEqual(ok['view_alignment_successor_sha256'],bound.successor_sha256)
        def changed(**updates):return encoded({**json.loads(calibration),**updates})
        for health,raw,reasons in ((RETIRED_EPOCH,calibration,['camera_health_session_changed']),
                (None,calibration,['camera_health_session_changed']),
                (self.epoch,changed(session='authored-newer-camera-epoch'),['camera_calibration_session_changed']),
                (self.epoch,changed(eeprom={'authored':'different-board'}),['camera_calibration_content_changed']),
                (self.epoch,b'{"session":"a","session":"b"}',['camera_calibration_invalid']),
                (self.epoch,b' '*24577,['camera_calibration_invalid']),
                ('authored-newer-camera-epoch',changed(session='authored-newer-camera-epoch'),
                    ['camera_health_session_changed','camera_calibration_session_changed'])):
            out=self.module.camera_session_compatibility(health_session=health,calibration_original_bytes=raw,successor=bound)
            with self.subTest(reasons=reasons):
                self.assertEqual(out['status'],'camera_session_incompatible');self.assertEqual(out['reasons'],reasons)
        with self.assertRaises(self.module.ViewAlignmentRefusal):
            self.module.camera_session_compatibility(health_session=self.epoch,calibration_original_bytes=calibration,successor=self.binding())

    def test_generator_recomputes_verification_and_only_writes_what_the_binder_accepts(self):
        spec=importlib.util.spec_from_file_location('generate_view_alignment_successor',TOOL)
        tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
        o=self.fixture.originals;inputs=dict(alignment=encoded(self.doc),review=encoded(self.review),
            source_candidate=encoded(self.candidate),calibration=o['calibration'],npz=o['npz'],rgb=b'authored-rgb',
            landmark_overlay=b'authored-overlay',verified_epoch=self.epoch,verified_at='2026-10-19T12:00:00Z',
            reviewed_by='Authored successor review',landmark_overlay_matches=True)
        successor,review,receipt=tool.build(**inputs)
        verification=json.loads(successor)['verification']
        self.assertEqual(verification['check_mount'],dict(status='unchanged',compared_cells=64,changed_cells=0))
        self.assertEqual(verification['calibration_content_id'],self.camera['calibration_content_id'])
        self.assertEqual(verification['npz_sha256'],sha(o['npz']))
        self.assertEqual(receipt['view_alignment_successor_sha256'],sha(successor));self.assertEqual(receipt['camera_operations'],0)
        out=self.project(self.bound(raw=dict(successor=successor,successor_review=review)));self.assertTrue(out['renderable'],out)
        for update,reason in ((dict(verified_epoch=RETIRED_EPOCH),'verification_read_session_mismatch'),
                (dict(landmark_overlay_matches=False),'landmark_overlay_review_required'),
                (dict(verified_at='not-a-time'),'approximate_successor_verification_required')):
            with self.subTest(reason=reason),self.assertRaisesRegex(ValueError,reason):tool.build(**{**inputs,**update})
        with tempfile.TemporaryDirectory() as folder:
            paths={}
            for key in ('alignment','review','source_candidate','calibration','npz','rgb','landmark_overlay'):
                paths[key]=Path(folder)/(key+'.bin');paths[key].write_bytes(inputs[key])
            argv=[a for key,path in paths.items() for a in ('--'+key.replace('_','-'),str(path))]
            argv+=['--verified-epoch',self.epoch,'--verified-at','2026-10-19T12:00:00Z','--reviewed-by','Authored successor review',
                '--landmark-overlay-matches','--out-dir',str(Path(folder)/'out')]
            with patch('sys.stdout',new=io.StringIO()):self.assertEqual(tool.main(argv),receipt)
            self.assertEqual((Path(folder)/'out'/tool.SUCCESSOR_NAME).read_bytes(),successor)
            self.assertEqual((Path(folder)/'out'/tool.REVIEW_NAME).read_bytes(),review)
            with self.assertRaises(FileExistsError),patch('sys.stdout',new=io.StringIO()):tool.main(argv)

if __name__=='__main__':unittest.main()
