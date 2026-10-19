"""Verified camera-session successor through the actual protected bootstrap,
RAM source/Store/PairedRunner, desk preview and ASGI.

The reviewed fit is authored at a retired capture epoch; the successor names
the fixture's current one. No household originals, camera, host, model,
native fixture or physical I/O.
"""
from copy import deepcopy
import base64
import hashlib
import json
import time
import unittest
from unittest.mock import patch

import test_apartment_view_alignment as alignment_fixture
import test_apartment_viewing_metric_source as source_fixture
import test_apartment_desk_preview_source as desk_fixture
import apartment_viewing_metric_bootstrap as bootstrap

def sha(raw):return hashlib.sha256(raw).hexdigest()

def configuration(case,*,successor=True,timing='strict',verified_epoch=None):
    """Drop-in for MetricSourceTests.approximate_configuration: a v2 policy, or the bare v1 one."""
    alignment_fixture.ViewAlignmentSuccessorTests.setUpClass()
    case.addCleanup(alignment_fixture.ViewAlignmentSuccessorTests.doClassCleanups)
    fixture=alignment_fixture.ViewAlignmentSuccessorTests('runTest');fixture.setUp();case.addCleanup(fixture.doCleanups)
    if verified_epoch is not None:  # a successor reviewed for a different camera session
        fixture.successor['verification']['verified_epoch']=fixture.successor_review['verified_epoch']=verified_epoch
    originals=fixture.originals()
    if not successor:originals={role:originals[role] for role in bootstrap.ROLES}
    config={'schema':bootstrap.SCHEMA_V2 if successor else bootstrap.SCHEMA,'version':2 if successor else 1,'enabled':True,
        'pack':fixture.pack,'mount_revision':fixture.doc['mount']['revision'],
        'files':{role:{'path':bootstrap.PRIVATE_PATH+'/authored-metric-v2/'+role+'.json','sha256':sha(raw)} for role,raw in originals.items()}}
    if successor:config['timing_policy']=timing
    return fixture,bootstrap,originals,config

def successor_configuration(case):return configuration(case)

class SuccessorBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_fixture.MetricSourceTests.setUpClass()
        cls.addClassCleanup(source_fixture.MetricSourceTests.doClassCleanups)

    def setUp(self):
        self.f=source_fixture.MetricSourceTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def loaded(self,*,successor=True):
        fixture,_,originals,config=configuration(self.f,successor=successor)
        base=self.f.factory();by_path={config['files'][role]['path']:raw for role,raw in originals.items()}
        with patch.object(bootstrap.os,'lstat',side_effect=FileNotFoundError),\
                patch.object(bootstrap,'_read_private',side_effect=lambda path:by_path[path]):
            configured=bootstrap.load_factory(config,client=base.client)
        configured.clock=base.clock
        return fixture,originals,config,configured

    def run_source(self,configured):
        f=self.f;runtime=f.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[f.session['id']]
        original=runtime.executor
        def pose(request):
            value=original(request);value['result']=deepcopy(f.event['payload']['result'])
            value['result']['frame_id']=request['frame']['frame_id'];return value
        runtime.executor=runtime.runners[f.session['id']].executor=pose
        f.anchors(runtime);deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if len([e for e in f.store.list_events(f.session['id'],0,100) if e['kind']=='perception.output'])==2:break
            time.sleep(.005)
        source.metric.finalize()
        return runtime,source

    def test_v2_policy_projects_the_verified_epoch_and_publishes_the_successor(self):
        fixture,originals,_,configured=self.loaded();capability=configured.capability
        self.assertEqual(capability['view_alignment_successor_sha256'],sha(originals['successor']))
        self.assertEqual(capability['view_alignment_successor_review_sha256'],sha(originals['successor_review']))
        self.assertEqual(capability['verified_epoch'],fixture.epoch)
        self.assertEqual(capability['view_alignment_sha256'],sha(originals['alignment']))
        self.assertFalse(capability['physical_permission']);self.assertFalse(capability['operational_geometry'])
        runtime,source=self.run_source(configured)
        live=runtime.live(self.f.session);packet=live['arms']['B']['approximate_room_pose']
        self.assertTrue(packet['renderable'],packet);self.assertEqual(packet['frame']['source_epoch'],fixture.epoch)
        session=packet['approximate_pose']['registration']['camera_session']
        self.assertEqual((session['verified_epoch'],session['reviewed_capture_epoch']),(fixture.epoch,alignment_fixture.RETIRED_EPOCH))
        self.assertEqual(live['apartment_viewing']['metric']['state'],'available')
        self.assertEqual(len(source.metric.projection.retained_byte_allocations),5)
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
        runtime.stop_session(self.f.session);self.assertEqual(source.status()['resident_bytes'],0)
        self.assertIsNone(source.metric.snapshot())

    def test_reviewed_fit_alone_refuses_the_new_epoch_by_its_own_reason(self):
        _,_,_,configured=self.loaded(successor=False)
        self.assertNotIn('verified_epoch',configured.capability);self.assertNotIn('view_alignment_successor_sha256',configured.capability)
        runtime,source=self.run_source(configured)
        live=runtime.live(self.f.session);packet=live['arms']['B']['approximate_room_pose']
        self.assertFalse(packet['renderable']);self.assertEqual(packet['reason'],'approximate_camera_epoch_changed')
        self.assertEqual(live['apartment_viewing']['metric']['reason'],'approximate_camera_epoch_changed')
        self.assertEqual(len(source.metric.projection.retained_byte_allocations),3)
        runtime.stop_session(self.f.session);self.assertEqual(source.status()['resident_bytes'],0)

    def test_mid_session_epoch_change_still_retires_the_successor_source(self):
        _,_,_,configured=self.loaded();runtime,source=self.run_source(configured)
        tracker=source.metric.projection;self.assertTrue(tracker.snapshot()['renderable'])
        changed=source_fixture.viewing.capture.decoded_header(self.f.headers['b']);changed['session']='authored-reconnected-camera-epoch'
        header=base64.urlsafe_b64encode(json.dumps(changed).encode()).decode()
        with self.assertRaises(ValueError):source.ingest(self.f.session['id'],self.f.originals['anchor_b'],header)
        self.assertTrue(source.closed);self.assertTrue(source.expiry_dispatch_started)
        self.assertEqual(source.status()['resident_bytes'],0);self.assertIsNone(source.metric.projection)
        self.assertFalse(tracker.snapshot()['renderable']);self.assertEqual(tracker.snapshot()['reason'],'approximate_source_stopped')
        self.assertTrue(source.expiry_dispatch_done.wait(1));self.assertTrue(runtime.runners[self.f.session['id']].closed)

    def test_desk_preview_becomes_available_only_through_the_verified_successor(self):
        from app import create_app,PREFIX
        from fastapi.testclient import TestClient
        for successor in (True,False):
            with self.subTest(successor=successor):
                case=desk_fixture.DeskPreviewSourceTests('runTest');case.setUp();self.addCleanup(case.doCleanups)
                with patch.object(source_fixture.MetricSourceTests,'approximate_configuration',
                        lambda f,successor=successor:configuration(f,successor=successor)):
                    runtime,source,configured,token=case.build()
                self.assertEqual('verified_epoch' in configured.capability,successor)
                headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
                with TestClient(create_app(case.f.store.settings,runtime,case.f.store)) as client:
                    response=client.get(PREFIX+'/sessions/'+case.f.session['id']+'/live',headers=headers)
                self.assertEqual(response.status_code,200);arm=response.json()['arms']['B'];packet=arm['virtual_target_preview']
                if successor:
                    self.assertTrue(packet['available'],packet);self.assertEqual(packet['observation'],arm['observation'])
                    self.assertEqual(packet['target']['point_m'],case.candidate['target']['centre_m'])
                    self.assertEqual(packet['frame'],arm['approximate_room_pose']['frame'])
                    self.assertEqual(arm['approximate_room_pose']['approximate_pose']['registration']['camera_session']['verified_epoch'],
                        arm['observation']['capture_epoch'])
                else:
                    self.assertFalse(packet['available'])
                    self.assertEqual(arm['approximate_room_pose']['reason'],'approximate_camera_epoch_changed')
                runtime.stop_session(case.f.session);self.assertEqual(source.status()['resident_bytes'],0);runtime.close()

    def test_optional_loader_prefers_the_v2_file_and_never_falls_back(self):
        fixture,_,originals,config=configuration(self.f)
        v1={**{k:v for k,v in deepcopy(config).items() if k!='timing_policy'},'schema':bootstrap.SCHEMA,'version':1};v1['files']={role:v1['files'][role] for role in bootstrap.ROLES}
        disk={config['files'][role]['path']:raw for role,raw in originals.items()}
        disk[bootstrap.POLICY_PATH]=alignment_fixture.encoded(v1);base=self.f.factory()
        def load(files):
            seen=[]
            def lstat(path):
                seen.append(('lstat',path))
                if isinstance(files.get(path),Exception):raise files[path]
                if path not in files:raise FileNotFoundError(path)
                return object()
            def read(path):seen.append(('read',path));return files[path]
            with patch.object(bootstrap.os,'lstat',side_effect=lstat),patch.object(bootstrap,'_read_private',side_effect=read):
                result,status=bootstrap.load_optional_factory(client=base.client)
            return result,status,seen
        result,status,seen=load({**disk,bootstrap.SUCCESSOR_POLICY_PATH:alignment_fixture.encoded(config)})
        self.assertEqual(status,{'present':True,'provisioned':True,'reason':None})
        self.assertEqual(result.capability['verified_epoch'],fixture.epoch)
        self.assertEqual(seen[0],('lstat',bootstrap.SUCCESSOR_POLICY_PATH));self.assertNotIn(('read',bootstrap.POLICY_PATH),seen)
        result,status,seen=load(disk)
        self.assertEqual(status,{'present':True,'provisioned':True,'reason':None})
        self.assertIn(('read',bootstrap.POLICY_PATH),seen);self.assertNotIn('verified_epoch',result.capability)
        stale=deepcopy(config);stale['files']['successor']['sha256']='0'*64
        older=deepcopy(config);older['version']=1
        for name,raw in (('successor_pin_changed',alignment_fixture.encoded(stale)),('v1_schema_at_v2_path',disk[bootstrap.POLICY_PATH]),
                ('version',alignment_fixture.encoded(older)),('duplicate_key',b'{"schema":"a","schema":"b"}'),('malformed',b'{bad json')):
            result,status,_=load({**disk,bootstrap.SUCCESSOR_POLICY_PATH:raw})
            with self.subTest(name=name):
                self.assertIsNone(result);self.assertEqual(status,{'present':True,'provisioned':False,'reason':'metric_viewing_policy_invalid'})
        result,status,_=load({**disk,bootstrap.SUCCESSOR_POLICY_PATH:PermissionError()})
        self.assertIsNone(result);self.assertEqual(status['reason'],'metric_viewing_policy_unavailable')

    def test_v2_policy_shape_while_the_v1_path_and_parser_stay_unchanged(self):
        _,_,_,config=configuration(self.f)
        self.assertEqual(bootstrap.POLICY_PATH,'/home/engineeredlightingllm/perception-lab/private/apartment-viewing-metric.json')
        self.assertNotEqual(bootstrap.SUCCESSOR_POLICY_PATH,bootstrap.POLICY_PATH)
        self.assertEqual(bootstrap.policy_v2(config),config)
        with self.assertRaises(ValueError):bootstrap.policy(config)
        for edit in ('missing_successor','public_successor_path','v1_version','disabled','extra_key','pack'):
            changed=deepcopy(config)
            if edit=='missing_successor':del changed['files']['successor_review']
            if edit=='public_successor_path':changed['files']['successor']['path']='/tmp/view-alignment-successor.json'
            if edit=='v1_version':changed['version']=1
            if edit=='disabled':changed['enabled']=False
            if edit=='extra_key':changed['verified_epoch']='authored'
            if edit=='pack':changed['pack']['frame_sha256']='not-a-digest'
            with self.subTest(edit=edit),self.assertRaises(ValueError):bootstrap.policy_v2(changed)

    def test_preflight_rollback_check_requires_v1_to_name_the_same_reviewed_originals(self):
        _,_,_,config=configuration(self.f)
        v1={**{k:v for k,v in deepcopy(config).items() if k!='timing_policy'},'schema':bootstrap.SCHEMA,'version':1};v1['files']={role:v1['files'][role] for role in bootstrap.ROLES}
        self.assertEqual(bootstrap.rollback_consistency(v1,config),{'status':'consistent','mismatches':[]})
        changed=deepcopy(v1);changed['files']['alignment']['sha256']='0'*64;changed['pack']['frame_sha256']='0'*64
        changed['mount_revision']='a-later-mount'
        self.assertEqual(bootstrap.rollback_consistency(changed,config),{'status':'rollback_inconsistent',
            'mismatches':['alignment_pin_changed','pack_changed','mount_revision_changed']})
        for bad in ({**v1,'enabled':False},config):
            with self.assertRaises(ValueError):bootstrap.rollback_consistency(bad,config)

    def test_operator_preflight_reads_the_successor_and_refuses_an_authored_camera_mismatch(self):
        fixture,_,originals,config=configuration(self.f)
        disk={config['files'][role]['path']:raw for role,raw in originals.items()}
        disk[bootstrap.SUCCESSOR_POLICY_PATH]=alignment_fixture.encoded(config)
        from apartment_view_alignment import VerifiedSessionAlignment,camera_session_compatibility
        with patch.object(bootstrap,'_read_private',side_effect=lambda path:disk[path]):
            configured,bound=bootstrap.load_alignment(bootstrap.read_successor_policy())
        self.assertEqual(configured,config);self.assertIs(type(bound),VerifiedSessionAlignment)
        calibration=self.f.originals['calibration']
        ok=camera_session_compatibility(health_session=fixture.epoch,calibration_original_bytes=calibration,successor=bound)
        self.assertEqual(ok['status'],'compatible')
        for session in (alignment_fixture.RETIRED_EPOCH,'authored-reconnected-camera-epoch'):
            out=camera_session_compatibility(health_session=session,calibration_original_bytes=calibration,successor=bound)
            with self.subTest(session=session):
                self.assertEqual(out['status'],'camera_session_incompatible')
                self.assertEqual(out['reasons'],['camera_health_session_changed'])

if __name__=='__main__':unittest.main()
