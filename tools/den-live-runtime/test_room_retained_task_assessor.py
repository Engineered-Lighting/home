"""Offline retained witness admission; no native process, network or qualification."""
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from journal import digest
from room_retained_task_assessor import (TrustedRetainedTaskAssessor, validate_path_witness)


def authored_member(resolve=None):
    """Actual compiler with existing independent decoded-original doubles."""
    from test_fixture_joint_execution import joint_fixture
    from test_fixture_retained_angle_execution import retained_request
    f=joint_fixture(); draft=retained_request(f)
    fid=f['profile']['motor_profile']['fixture_id']
    models=SimpleNamespace(provider_pins=f['provider_pins'],zha=SimpleNamespace(
        decode_snapshot_v3=f['compiler'].decode_status,decode_capabilities_v2=f['compiler'].decode_capabilities))
    captured=[]
    def retained(q):
        o=original(q)
        o.update(observed_at_home_us=900000,available_at_home_us=950000,valid_until_home_us=3900000)
        r=receipt(o);captured.append(deepcopy(r));return r
    assessor=TrustedRetainedTaskAssessor(profiles={fid:f['profile']},receipt_store_id='path-originals',
        receipt_component_id='path-assessor',method_ref=query()['method_ref'],qualification_ref=query()['qualification_ref'],
        resolve_original=retained if resolve is None else resolve,speed_fixture_deg_s=[20,20],motion_timeout_us=2000000)
    state=SimpleNamespace(current_ref=f['ref'],raw_status_v3=f['raw_status'],raw_capabilities_v2=f['raw_capabilities'],
        caps={k:str(v) for k,v in draft['original_caps'].items()},room_commit_ref=draft['retained_aim_ref']['room_commit_ref'],
        source_cut=draft['retained_aim_ref']['input_cut_digest'],protection=draft['lineage']['protection_sha256'],
        authority=draft['lineage']['authority_sha256'])
    trace=dict(retained_aim_ref=deepcopy(draft['retained_aim_ref']),original=dict(to_pan_deg=10,to_tilt_deg=20))
    snapshot=dict(home_scope=dict(site_id='Victoria',room_id='den',session_id='authored',domain_epoch='one'),
        decision=dict(input_cut={'cut_digest':state.source_cut},scene=dict(scene_id='scene-1',episode_id='episode-1',example_ref={'id':'example-1'})))
    installed=[]
    r=SimpleNamespace(task_assessor=assessor,task_compiler=f['compiler'],task_previous=None,modules=models,
        binding=f['binding'],fixture_id=fid,source=SimpleNamespace(journal=SimpleNamespace(lock=nullcontext())),
        assessment_basis=lambda:(state,trace,SimpleNamespace(now_home_us=1000000,uncertainty_us=0),snapshot,None),
        install_task_assessment=lambda value:installed.append(value))
    member=SimpleNamespace(resolver=r,dispatcher=SimpleNamespace(_meta=lambda:{'last_revision':f['ref'].revision_id}))
    return assessor,member,captured,installed


def query():
    return dict(schema='home-retained-task-path-query-1', version=1, scope={'site_id':'den'},
        room_commit_ref={'commit_id':'accepted-original'}, fixture_id='fixture-1',
        method_ref={'id':'authored-path-method','digest':'a'*64},
        qualification_ref={'id':'authored-software-qualification','digest':'b'*64},
        original_caps={'work_not_after_home_us':2000}, retained_aim_ref={'motion_trace_sha256':'c'*64})


def original(q=None):
    return dict(schema='home-retained-task-path-witness-1', version=1, query=deepcopy(q or query()),
        checks=dict(work_coverage=True, all_people_protection=True, room_collision=True, dark_motion_path=True),
        minimum_work_range_m=.8, target_uncertainty_m=.001, total_aim_error_bound_rad=.01,
        observed_at_home_us=900, available_at_home_us=950, valid_until_home_us=1900,
        evidence_source='authored-independent', physical_permission=False, physical_truth=False)


def receipt(o=None):
    o=deepcopy(o or original())
    return dict(ref=dict(store_id='path-originals',component_id='path-assessor',receipt_id='retained-1',digest=digest(o)),original=o)


class RetainedWitnessTests(unittest.TestCase):
    def admit(self, r=None, q=None, now=1000):
        return validate_path_witness(receipt() if r is None else r, query() if q is None else q,
            receipt_store_id='path-originals', receipt_component_id='path-assessor', now_home_us=now)

    def test_exact_retained_independent_witness_is_admitted_without_changing_original_lease(self):
        r=receipt(); result=self.admit(r)
        self.assertEqual(result,r)
        result['original']['checks']['dark_motion_path']=False
        self.assertTrue(r['original']['checks']['dark_motion_path'])

    def test_mutated_store_identity_hash_query_or_any_path_check_refuses(self):
        changes=(lambda r:r['ref'].update(store_id='foreign'),
            lambda r:r['ref'].update(component_id='browser'),
            lambda r:r['ref'].update(digest='0'*64),
            lambda r:r['original']['query'].update(fixture_id='sofa'),
            lambda r:r['original']['query']['room_commit_ref'].update(commit_id='later'),
            lambda r:r['original']['checks'].update(work_coverage=False),
            lambda r:r['original']['checks'].update(all_people_protection=False),
            lambda r:r['original']['checks'].update(room_collision=False),
            lambda r:r['original']['checks'].update(dark_motion_path=False))
        for change in changes:
            r=receipt();change(r)
            # Rehashed forged body still cannot change exact requested basis or checks.
            if r['ref']['digest']!='0'*64:r['ref']['digest']=digest(r['original'])
            with self.subTest(change=change), self.assertRaises(ValueError):self.admit(r)

    def test_original_availability_expiry_and_caps_are_not_renewed(self):
        for fields in ({'available_at_home_us':1001},{'observed_at_home_us':1001},
            {'valid_until_home_us':1000},{'valid_until_home_us':2001},
            {'valid_until_home_us':float('inf')},{'available_at_home_us':True}):
            r=receipt();r['original'].update(fields);r['ref']['digest']=digest(r['original']) if fields.get('valid_until_home_us')!=float('inf') else '0'*64
            with self.subTest(fields=fields),self.assertRaises(ValueError):self.admit(r)
        with self.assertRaises(ValueError):self.admit(now=1900)

    def test_unknown_or_nonfinite_geometry_and_physical_claim_refuse(self):
        for fields in ({'minimum_work_range_m':0},{'target_uncertainty_m':-.1},
            {'total_aim_error_bound_rad':None},{'physical_permission':True},
            {'evidence_source':'qualified-real'}, {'untrusted_override':True}):
            r=receipt();r['original'].update(fields);r['ref']['digest']=digest(r['original'])
            with self.subTest(fields=fields),self.assertRaises(ValueError):self.admit(r)

    def test_constructor_requires_fixed_profile_and_server_resolver(self):
        with self.assertRaises(ValueError):
            TrustedRetainedTaskAssessor(profiles={},receipt_store_id='path-originals',
                receipt_component_id='path-assessor',method_ref=query()['method_ref'],
                qualification_ref=query()['qualification_ref'],resolve_original=None,
                speed_fixture_deg_s=[20,20],motion_timeout_us=2000000)

    def test_actual_existing_compiler_accepts_bound_request_before_install(self):
        assessor,member,captured,installed=authored_member()
        result=assessor.install(member)
        self.assertEqual(result['status'],'installed')
        self.assertEqual(len(installed),1)
        r=installed[0].original()
        self.assertEqual(r['kind'],'MOVE')
        self.assertEqual(r['predecessor_id'],member.resolver.assessment_basis()[0].current_ref.revision_id)
        self.assertEqual(r['fixture_angles_deg'],[10,20])
        self.assertEqual(r['assessment']['assessment_id'],'retained-path-'+captured[0]['ref']['digest'])
        self.assertEqual(r['support_expiry_home_us'],3900000)
        self.assertEqual(r['assessment']['status'],'fictional')
        self.assertNotIn('target_room_m',r)

    def test_missing_or_wrong_witness_never_installs_assessment(self):
        for resolve in (lambda q:None,lambda q:receipt(original(query()))):
            assessor,member,_,installed=authored_member(resolve)
            with self.assertRaises(ValueError):assessor.install(member)
            self.assertEqual(installed,[])

    def test_capability_preimages_are_detached_and_internal_changes_refuse(self):
        assessor,member,_,installed=authored_member()
        public=assessor.provenance();public['profiles'].clear()
        assessor.require_current()
        assessor._speeds[0]=1
        with self.assertRaisesRegex(ValueError,'installation_changed'):assessor.install(member)
        self.assertEqual(installed,[])
        assessor,member,_,installed=authored_member()
        assessor._resolve=lambda value:None
        with self.assertRaisesRegex(ValueError,'installation_changed'):assessor.install(member)
        self.assertEqual(installed,[])

    def test_unknown_or_expired_upper_clock_bound_does_not_borrow_witness_lease(self):
        assessor,member,_,installed=authored_member()
        basis=member.resolver.assessment_basis
        member.resolver.assessment_basis=lambda:(*basis()[:2],SimpleNamespace(now_home_us=1000000,uncertainty_us=2900000),*basis()[3:])
        with self.assertRaisesRegex(ValueError,'uncertain_original_expiry'):assessor.install(member)
        self.assertEqual(installed,[])

    def test_rehashed_original_cannot_understate_aim_error(self):
        def resolve(q):
            o=original(q);o.update(observed_at_home_us=900000,available_at_home_us=950000,
                valid_until_home_us=3900000,total_aim_error_bound_rad=0)
            return receipt(o)
        assessor,member,_,installed=authored_member(resolve)
        with self.assertRaisesRegex(ValueError,'assessment_understates_uncertainty'):assessor.install(member)
        self.assertEqual(installed,[])

    def test_new_declaration_pins_optin_closure_without_changing_legacy_inventory(self):
        from test_room_command_factory import authored_declaration
        from room_command_factory import (validate_command_declaration,SOURCE_INVENTORY,TRUSTED_TASK_SOURCE_INVENTORY,
            TRUSTED_TASK_FACTORY_REF,RETAINED_TASK_PROFILE_REF)
        d=authored_declaration();legacy=deepcopy(d)
        assessor,_,_,_=authored_member()
        d.update(schema='home-den-canonical-command-declaration-4',version=4,
            factory_ref=deepcopy(TRUSTED_TASK_FACTORY_REF),original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256='c'*64,task_profile_ref=deepcopy(RETAINED_TASK_PROFILE_REF),
            task_assessor_ref=dict(id='home-retained-task-assessor-capability-1',digest=digest(assessor.descriptor())),
            source_pins={p:'b'*64 for p in TRUSTED_TASK_SOURCE_INVENTORY})
        self.assertEqual(validate_command_declaration(d,source_bundle_digest='a'*64),d)
        self.assertEqual(set(legacy['source_pins']),set(SOURCE_INVENTORY))
        self.assertEqual(validate_command_declaration(legacy,source_bundle_digest='a'*64),legacy)
        for path in set(TRUSTED_TASK_SOURCE_INVENTORY)-set(SOURCE_INVENTORY):
            cut=deepcopy(d);cut['source_pins'].pop(path)
            with self.subTest(path=path),self.assertRaises(ValueError):validate_command_declaration(cut,source_bundle_digest='a'*64)

    def test_factory_constructor_requires_explicit_exact_typed_capability(self):
        from test_room_command_factory import authored_declaration
        from room_command_factory import (RoomCommandFactory,TRUSTED_TASK_SOURCE_INVENTORY,
            TRUSTED_TASK_FACTORY_REF,RETAINED_TASK_PROFILE_REF,ASSESSED_FACTORY_REF,ASSESSED_SOURCE_INVENTORY)
        from lab_live_runtime import LabRuntimeComposition
        assessor,_,_,_=authored_member()
        d=authored_declaration()
        d.update(schema='home-den-canonical-command-declaration-4',version=4,
            factory_ref=deepcopy(TRUSTED_TASK_FACTORY_REF),original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256='c'*64,task_profile_ref=deepcopy(RETAINED_TASK_PROFILE_REF),
            task_assessor_ref=dict(id='home-retained-task-assessor-capability-1',digest=digest(assessor.descriptor())),
            source_pins={p:'b'*64 for p in TRUSTED_TASK_SOURCE_INVENTORY})
        composition=LabRuntimeComposition.__new__(LabRuntimeComposition)
        composition.producer=dict(version=8,live_config=dict(schema='home-live-ingress-config-13',version=13,
            manifest=dict(schema='home-live-ingress-manifest-13',version=13,worker=dict(room_subset_mode=True,
                room_policy=dict(schema='home-live-room-policy-2',version=2)))),desk_policy=dict(
                    original_family=d['original_family'],bundle_input_policy_sha256=d['bundle_input_policy_sha256']),
            limits=dict(max_runtime_s=120))
        self.assertIs(RoomCommandFactory(d,composition=composition,retained_task_assessor=assessor).task_assessor,assessor)
        for cap in (None,assessor.descriptor(),lambda:assessor):
            with self.subTest(cap=type(cap).__name__),self.assertRaisesRegex(ValueError,'capability_required'):
                RoomCommandFactory(d,composition=composition,retained_task_assessor=cap)
        wrong=deepcopy(d);wrong['task_assessor_ref']['digest']='0'*64
        with self.assertRaisesRegex(ValueError,'declaration_differs'):
            RoomCommandFactory(wrong,composition=composition,retained_task_assessor=assessor)
        old=deepcopy(d);old.update(schema='home-den-canonical-command-declaration-3',version=3,
            factory_ref=deepcopy(ASSESSED_FACTORY_REF),source_pins={p:'b'*64 for p in ASSESSED_SOURCE_INVENTORY})
        old.pop('task_assessor_ref')
        self.assertIsNone(RoomCommandFactory(old,composition=composition).task_assessor)
        with self.assertRaisesRegex(ValueError,'declaration4_required'):
            RoomCommandFactory(old,composition=composition,retained_task_assessor=assessor)

    def test_new_same_kind_original_retains_full_witness_and_roundtrips_recorder(self):
        import json,tempfile
        from pathlib import Path
        from archive import Recorder,validate_task_assessment_original,TASK_ORIGINAL_KIND
        assessor,member,captured,installed=authored_member()
        assessor.install(member)
        r=installed[0].original()
        data=dict(schema='home-room-task-assessment-original-2',version=2,fixture_id=member.resolver.fixture_id,
            assessment=r,assessment_sha256=digest(r),profile_sha256=digest(member.resolver.task_compiler.profile),
            room_commit_ref=r['retained_aim_ref']['room_commit_ref'],path_witness=captured[0],
            assessor_ref=dict(id='home-retained-task-assessor-capability-1',digest=digest(assessor.descriptor())))
        self.assertEqual(validate_task_assessment_original(data),data)
        manifest=dict(schema='home-live-session-manifest-2',version=2,session_ref={},configuration_digest='a'*64,
            source_bundle_digest='b'*64,worker_config={},worker_description=None,runtime_profile='home-live-runtime-config-3')
        with tempfile.TemporaryDirectory() as directory:
            archive=Recorder(str(Path(directory)/'command-originals.jsonl'),manifest,max_records=8,max_bytes=1024*1024,min_free_bytes=1)
            body=dict(kind=TASK_ORIGINAL_KIND,data=data)
            ack=archive.record(body,record_id='room-task-assessment:'+digest(data))
            saved=json.loads(archive.path.read_bytes().splitlines()[ack['seq']])
            self.assertEqual(saved['record'],body)
            self.assertEqual(saved['digest'],ack['digest'])
            self.assertEqual(validate_task_assessment_original(saved['record']['data']),data)
            archive.drain(dict(status='complete',physical_permission=False))

    def test_python_and_actual_js_replay_reject_mutated_witness_data(self):
        import json,subprocess
        from archive import validate_task_assessment_original
        assessor,member,_,installed=authored_member();assessor.install(member)
        r=installed[0].original()
        data=installed[0].record_data(fixture_id=member.resolver.fixture_id,
            profile_sha256=digest(member.resolver.task_compiler.profile),room_commit_ref=r['retained_aim_ref']['room_commit_ref'])
        stripped={k:v for k,v in data.items() if k not in ('schema','version','path_witness','assessor_ref')}
        with self.assertRaisesRegex(ValueError,'path_original_required'):validate_task_assessment_original(stripped)
        cases=[]
        def altered(change):
            v=deepcopy(data);change(v)
            # Rehash the authored body/request so semantic checks, rather than
            # merely a stale checksum, have to reject the changed witness.
            v['path_witness']['ref']['digest']=digest(v['path_witness']['original'])
            v['assessment']['assessment']['assessment_id']='retained-path-'+v['path_witness']['ref']['digest']
            v['assessment_sha256']=digest(v['assessment'])
            cases.append(v)
        altered(lambda v:v.update(version=1))
        altered(lambda v:v.update(assessor_ref=dict(id='home-retained-task-assessor-capability-1',digest='0'*64)))
        altered(lambda v:v['path_witness']['ref'].update(store_id='foreign'))
        altered(lambda v:v['path_witness']['original']['query']['scope'].update(room_id='foreign'))
        altered(lambda v:v['path_witness']['original']['query']['native'].update(reference_sha256='0'*64))
        altered(lambda v:v['path_witness']['original']['checks'].update(dark_motion_path=False))
        altered(lambda v:v['path_witness']['original'].update(valid_until_home_us=r['issued_home_us']))
        altered(lambda v:v['path_witness']['original']['query']['motion'].update(predecessor_id=99))
        altered(lambda v:v['path_witness']['original'].update(available_at_home_us=r['issued_home_us']+1))
        altered(lambda v:v['path_witness']['original']['query'].update(extra=True))
        for v in cases:
            with self.assertRaises(ValueError):validate_task_assessment_original(v)
        script="""const fs=require('node:fs'),m=require('./tools/den-live-runtime/replay.cjs');
        const v=JSON.parse(fs.readFileSync(0,'utf8'));m.validateTaskPathOriginal(v.data);
        for(const item of v.cases){let refused=false;try{m.validateTaskPathOriginal(item);}catch(e){refused=true;}
          if(!refused)throw Error('mutated_path_original_accepted');}process.stdout.write('verified\\n');"""
        run=subprocess.run(['node','-e',script],input=json.dumps(dict(data=data,cases=cases)),text=True,
            capture_output=True,timeout=10)
        self.assertEqual((run.returncode,run.stdout,run.stderr),(0,'verified\n',''))
        installed[0].path_witness['original']['checks']['dark_motion_path']=False
        with self.assertRaisesRegex(ValueError,'original_changed'):installed[0].original()


if __name__=='__main__':unittest.main()
