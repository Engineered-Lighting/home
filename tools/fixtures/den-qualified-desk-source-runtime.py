"""Authored one-desk source8 -> person JEV -> actual config13 worker.

No camera/model/host or physical operation. Commissioning and the P1 model
receipt are authored external inputs. Store, bundled lifting, desk admission,
bridge retention, real managed JEV queue/provider/policy, Journal and worker
are actual. The caller attaches its sole Recorder before any admission.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import http.client
import io
import json
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'stack/services/perception-lab'),str(ROOT/'stack/services/home-context'),str(ROOT/'tools/den-live-runtime')]
import test_lab_assessed_command_runtime as assessed
import test_lab_live_desk_anchor as generated
import test_lab_live_semantic_admission_v2 as semantic_fixture
import test_room_subset_worker as subset_fixture
import test_lab_stereo_worker_runtime as stereo_worker
from controlled_external_io import assemble_journal_grants
from pc_support_configuration import upgrade_configuration as pc_upgrade
from journal import PREFIX,digest
from live_test_helpers import NOW,TOKEN
from reasoning import ACTIVITIES,digest as lab_digest
from jev_person_provider import JevPersonProvider
from managed_jev import reasoning_factory
from test_live_pc_support import authored_pc_fixture
import jev_person_wire as wire
import test_lab_bundled_rgb_stereo as bundled_fixture
import test_bundle_rgb_input as input_fixture


def fresh_pc_fixture(sequence, received_at):
    """Authored 100ms-old snapshot, delivered after its original 50ms read.

    All timestamps/digests are generated together. No completed original is
    rewritten, and the source's original three-second lease is preserved.
    """
    return authored_pc_fixture(seq=sequence,snapshot_at=received_at-.1,input_at=received_at-.2)


def positive_frame_schedule(first_received_at):
    """Initial warmup plus seven data Cs;16inputs and10.5s data span."""
    return [dict(received_at=first_received_at+1.75*i,observed_at=NOW+.45+1.75*i,sequence=13+8*i) for i in range(8)]


def eligible_worker_cut(received_at,previous_cut):
    """Wait for the incumbent one-second worker cadence, without backdating."""
    return received_at if previous_cut is None else max(received_at,previous_cut+1.)


def author_desk_front_fixture(fixture):
    """Independent fictional placement already used by the desk worker recipe.

    The surveyed person/work shapes are untouched. These retained data bodies
    pin the new static pose/optics declarations rather than relabelling the old
    generic placement or changing a completed original.
    """
    task=fixture['task_policy']['fixture']
    pose=dict(schema='authored-desk-front-fixture-pose-1',version=1,fixture_id=fixture['fixture_id'],
        mount_id=fixture['mount_id'],origin_m=[2.,0.,3.],local_to_room=deepcopy(task['local_to_room']),
        initial=dict(pan_deg=90.,tilt_deg=45.),
        provenance='test_lab_live_desk_runtime.py:199-206; independently authored workcenter[2,2,1]',
        physical_truth=False)
    pose_ref=dict(id='authored-desk-front-fixture-pose',digest=digest(pose))
    optical=dict(schema='authored-desk-front-optical-assessment-1',version=1,
        fixture_pose_ref=deepcopy(pose_ref),optics=deepcopy(task['optics']),physical_truth=False)
    fixture['pose_ref']=deepcopy(pose_ref)
    task.update(origin_m=deepcopy(pose['origin_m']),initial=deepcopy(pose['initial']),fixture_pose_ref=deepcopy(pose_ref),
        assessment_ref=dict(id='authored-desk-front-optical-assessment',digest=digest(optical)))
    domain=fixture['ambient'][0]
    domain['origin_m']=deepcopy(pose['origin_m'])
    ambient=dict(schema='authored-desk-front-ambient-assessment-1',version=1,
        fixture_pose_ref=deepcopy(pose_ref),domain=domain['domain'],
        assessment={k:deepcopy(v) for k,v in domain.items() if k not in ('domain','assessment_ref')},physical_truth=False)
    domain['assessment_ref']=dict(id='authored-desk-front-ambient-assessment',digest=digest(ambient))
    return dict(pose=pose,optical=optical,ambient=ambient)


def campaign_assessed_subset(policy,convert):
    """Declare four static fictional assessments before config/manifest pins.

    This is one finite20-second test campaign, not renewal of an observation,
    original clock, protection region or model result. Other members/domains
    remain explicitly unassessed in the existing subset conversion.
    """
    declared=deepcopy(policy)
    declared['valid_until']=declared['exposure']['valid_until']=NOW+20
    declared['fixtures'][0]['valid_until']=NOW+20
    declared['fixtures'][0]['ambient'][0]['valid_until']=NOW+20
    author_desk_front_fixture(declared['fixtures'][0])
    value=convert(declared)
    value['fixtures'][0]['assessment']['ambient'][0]['assessment']={
        k:deepcopy(v) for k,v in declared['fixtures'][0]['ambient'][0].items() if k!='domain'}
    return value


def install_campaign_inputs(stack):
    """Declare the explicit policy/recipe before the actual factory exists.

    New A inputs advance the real prior anchor through a retained wide-clock
    candidate. The real1Hz sampler drops those interstitial model requests;
    the close B then produces the next qualified-uncertainty C. No prior,
    sampling state, completed output or original is reset or rewritten.
    """
    stack.enter_context(patch.object(input_fixture,'POLICY',dict(schema='home-lab-bundled-rgb-input-policy-2',
        version=2,max_captures=17,max_retained_bytes=40*1024**2)))
    # Sixteen actual inputs plus one declared unused slot: consuming the
    # final admitted input must not itself Stop the shared preview owner.
    stack.enter_context(patch.object(input_fixture.BundledRgbFactoryTests,'max_captures',17,create=True))
    catalog_path=ROOT/'stack/services/perception-lab/experiments.json'
    original_read=Path.read_text
    catalog=json.loads(original_read(catalog_path,encoding='utf8'))
    recipe=next(r for r in catalog['experiments'] if r['id']=='placement-same')
    for arm in ('a','b'):recipe[arm]['sampling_hz']=1
    recipe['revision']+='; authored finite campaign: both arms1Hz'
    catalog_raw=json.dumps(catalog)
    def read(path,*args,**kwargs):
        return catalog_raw if path.resolve()==catalog_path.resolve() else original_read(path,*args,**kwargs)
    stack.enter_context(patch.object(Path,'read_text',read))
    original_clock=bundled_fixture.AuthoredBundledClock
    class CampaignClock(original_clock):
        def header(self,jpeg,sequence,observed_at):
            midpoint=int((Decimal(10)+Decimal(str(observed_at))-Decimal(NOW))*10**9)
            checked=midpoint-20_000_000
            self.evidence.update(validation_id='authored-frame-clock-'+str(midpoint),checked_monotonic_ns=checked,
                checked_unix_ns=self.utc_ns(checked),valid_until_monotonic_ns=checked+1_000_000_000)
            self.receiver_before=self.point(midpoint+120_000_000)
            # A completes before the next B receiver bracket begins. The
            # original A/B exposure separation remains20ms; no body-read
            # intervals or processing availability overlap across inputs.
            self.receiver_after=self.point(midpoint+135_000_000)
            self.completed_at=float(Decimal(self.utc_ns(self.receiver_after.after_ns)+1_000_000)/10**9)
            return super().header(jpeg,sequence,observed_at)
    stack.enter_context(patch.object(bundled_fixture,'AuthoredBundledClock',CampaignClock))
    def append_pair(owner,*,sequence=13,observed_at=NOW+.45,depth_delta_s=-.01,expect_output=True,**_):
        import numpy as np
        from preview_client import LocalBundleClient
        source=owner.runtime.bundled_rgb_sessions[owner.session['id']]
        nv12=np.full((540,640),128,np.uint8);nv12[:360]=235
        clock=CampaignClock(nv12.tobytes(),shift=Decimal(str(observed_at))-Decimal(NOW)-Decimal('.45'))
        def component(identity,at):
            owner.f.time_shift=Decimal(str(at))-Decimal(NOW)-Decimal('.45')
            device=float(Decimal(50)+Decimal(str(at))-Decimal(NOW))
            meta=bundled_fixture.capture.frame_metadata(sequence=identity,depth_sequence=41+identity-13,
                rgb_device_s=device,depth_device_s=device+depth_delta_s)
            out=io.BytesIO();np.savez_compressed(out,depth_mm=np.full((400,640),1234,np.uint16),
                left=np.zeros((400,640),np.uint8),right=np.zeros((400,640),np.uint8),rgb_nv12=nv12,
                metadata_json=np.frombuffer(json.dumps(meta).encode(),np.uint8))
            owner.f.bundle=out.getvalue();owner.f.raw_calibration=json.dumps(owner.f.calibration).encode()
        def camera_read(_client,path):
            if path=='/frame.npz':owner.now=max(owner.now,clock.completed_at);return owner.f.bundle,''
            if path=='/calibration':return owner.f.raw_calibration,''
            raise AssertionError('unknown authored camera route')
        a_at,b_at=observed_at-.01,observed_at+.01
        previous=source.previous
        warm_at=None if previous is None else (owner.store.get_media(owner.session['id'],
            previous['media_ref']['id'])['metadata']['observed_at']+a_at)/2
        with patch('bundle_rgb_input.clock_context',side_effect=clock.context),patch('bundle_rgb_input.sync_directory'), \
                patch.object(LocalBundleClient,'_read',autospec=True,side_effect=camera_read):
            component(sequence if warm_at is None else sequence-4,observed_at if warm_at is None else warm_at)
            owner.now=max(owner.now,a_at+.02)
            owner.f.consume(owner.runtime,clock.header(owner.f.jpeg,sequence-3,a_at))
            component(sequence,observed_at)
            owner.now=max(owner.now,b_at+.02)
            owner.f.consume(owner.runtime,clock.header(owner.f.jpeg,sequence+3,b_at))
        frame_id='synthetic-camera-epoch-bundle-rgb-'+str(sequence)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            rows=[e for e in owner.store.list_events(owner.session['id']) if e['kind']=='perception.output'
                and e['payload'].get('arm')=='b' and e['payload'].get('frame_id')==frame_id]
            if rows:break
            time.sleep(.005)
        owner.case.assertTrue(rows,source.status())
        owner.row=rows[-1]
        owner.rgb=owner.store.get_media(owner.session['id'],owner.row['payload']['media_ids'][0])
        owner.frame=deepcopy(owner.rgb['metadata'])
        return owner.row,deepcopy(owner.frame)
    stack.enter_context(patch.object(bundled_fixture.GeneratedBundledFixture,'append_pair',append_pair))


class Fixture:
    def __init__(self,case,*,on_graph):
        self.case,self.stack,self.closed=case,ExitStack(),False
        self.driver=self.rpc=self.reasoner=self.graph=None
        self.last_worker_tick_at=None
        case.addCleanup(self.close)
        install_campaign_inputs(self.stack)
        original_execute=generated.GeneratedStereoFixture.execute
        def complete_model(owner,request):
            reply=original_execute(owner,request)
            pose=reply['result']['pose_instances'][0]
            pose.update(crop_xyxy=[260.,80.,380.,340.],score_semantics='raw_model_response')
            pose['joints']=[dict(joint_id=i,name='nose' if i==0 else 'joint_'+str(i),
                pixel=[320.,150.] if i==0 else None,score=.9 if i==0 else None,
                status='supported' if i==0 else 'uncertain') for i in range(17)]
            return reply
        self.stack.enter_context(patch.object(generated.GeneratedStereoFixture,'execute',complete_model))
        original_subset=subset_fixture.subset_policy
        def one_ambient(policy):
            return campaign_assessed_subset(policy,original_subset)
        self.stack.enter_context(patch.object(subset_fixture,'subset_policy',one_ambient))
        original_semantic=semantic_fixture.semantic_v2_fixture
        def refine_policy(*,include_worker=False):
            config,semantic,grant,worker=original_semantic(include_worker=True)
            # Independently commissioned authored interpretation method, fixed
            # before the room/PC/subset manifest and actual Journal exist.
            worker['engagement_policy']['refinement_methods']=[deepcopy(semantic['jev']['method_ref'])]
            pc=deepcopy(worker['pc_support_policy'])
            config.update(schema='home-live-ingress-config-9',version=9)
            config['manifest'].update(schema='home-live-ingress-manifest-9',version=9)
            config['manifest'].pop('pc_support_interface_digest',None)
            for key in ('pc_support_policy','pc_support_schema_pins'):
                config['manifest']['worker'].pop(key,None)
            config,worker=pc_upgrade(config,worker,pc)
            semantic['pc_support_policy_digest']=digest(pc)
            return (config,semantic,grant,worker) if include_worker else (config,semantic,grant)
        self.stack.enter_context(patch.object(semantic_fixture,'semantic_v2_fixture',refine_policy))
        self.recipe=assessed.assessed_bundled_fixture(case)
        self.source=self.recipe.source
        self.f,self.path=self.source.f,self.source.path
        self.store,self.session,self.service=self.f.store,self.f.session,self.f.runtime
        self.worker=deepcopy(self.recipe.inputs['worker_config'])
        producer=self.source.producer
        base=deepcopy(producer['semantic_policy'])
        q=self.f.qualification(dict(original=dict(event=self.f.row),source_generation=1,
            source_position=dict(capture_epoch='synthetic-camera-epoch',capture_seq=13,revision=0),publication_ref=None))
        producer['semantic_policy']=dict(schema='home-lab-person-semantic-admission-policy-1',version=1,
            base_policy=base,bridge=dict(source_id=producer['desk_policy']['source_id'],
            method_ref=deepcopy(q['association']['method_ref']),qualification_ref=deepcopy(q['association']['assessment_ref']),
            receipt_store_id='authored-desk-person-witness',receipt_component_id='person-work-association',max_bindings=1))
        # Finite test input capacities, declared before opening any owned files.
        producer['limits'].update(max_poll_attempts=128,max_capture_originals=16,max_runtime_s=20)
        producer['original_archive'].update(max_records=96,max_bytes=8*1024**2)
        self.recipe.config['media'].update(max_objects=128,max_bytes=32*1024**2)
        self.recipe.inputs['runtime'].update(max_poll_attempts=128,max_capture_originals=16,max_runtime_s=20)
        self.worker_token,self.owner_token='authored-stereo-worker-token','authored-stereo-owner'
        producer['journal_grants']=assemble_journal_grants(producer['live_config'],self.worker,
            publication_token=TOKEN,worker_token=self.worker_token,human_token=self.owner_token)
        for grant in producer['journal_grants']:
            grant['revisions']=list(dict.fromkeys([*grant['revisions'],'synthetic-r1',self.worker['revision']]))
        self.config=deepcopy(producer['live_config'])
        # One external model-lifecycle port receipt for this finite authored
        # campaign. It is declared before composition and never renewed later.
        self.store.append_event(self.session['id'],'workers.ready',dict(receipts={'b':dict(
            state='ready',session_id=self.session['id'],engine_epoch=self.f.profile['engine_epoch'],
            profile=deepcopy(self.f.profile),expires_at=NOW+30)},fixture_evidence='authored_external_finite_campaign_ready'))
        self.composition=self.recipe.compose()
        self.composition.monotonic=lambda:self.f.now-NOW
        self.source.composition=self.composition
        self.graph=self.composition.create()
        self.source.graph=self.graph
        self.recorder=on_graph(self,self.graph,self.worker)
        if self.recorder is None:raise AssertionError('sole_recorder_before_admission_required')
        from archive import RecorderRPC
        from supervisor import NodeWorkerDriver
        self.rpc=RecorderRPC(self.recorder)
        self.driver=NodeWorkerDriver(node_runtime=shutil.which('node'),config={**deepcopy(self.worker),
            'token':self.worker_token,'service_url':'http://127.0.0.1:'+str(self.graph.address[1])},
            recorder=self.rpc.start(),controlled_time=self.f.now)
        case.assertEqual(self.driver.readiness()['status'],'ready')
        self.store.append_event(self.session['id'],'reasoning.setting',dict(enabled=True,revision=2))
        self.calls=[]
        self.jev_policy=dict(schema_version=1,enabled=True,scope='household_structured',revision='authored-desk-person-jev',
            generation=1,expires_at=NOW+30,site_id=self.session['site_id'],camera_ids=['den'],model_selector=wire.MODEL,
            max_calls_per_minute=30,max_session_calls=24,structured_sources=[wire.SCHEMA,'lab-live-person-scene-1'])
        config=dict(schema_version=1,policy_file='authored-person-policy',credential_file='authored-key',response_version=wire.MODEL,wire=wire.SCHEMA)
        self.stack.enter_context(patch('managed_jev.private_json',side_effect=lambda name:deepcopy(
            config if name=='authored-person-config' else self.jev_policy)))
        self.stack.enter_context(patch('jev_person_provider.JevPersonProvider',side_effect=lambda **kwargs:JevPersonProvider(
            **{**kwargs,'credential':lambda:'authored-not-a-secret','transport':self.transport})))
        self.service.reasoning_factory=reasoning_factory('authored-person-config')
        self.service.reasoning_min_offer_interval_s=1.

    def transport(self,raw,token,timeout):
        packet=json.loads(raw);self.calls.append(deepcopy(packet))
        answers={key:dict(type='choice',choice='computer_work',confidence=.8,
            probabilities={k:float(k=='computer_work') for k in ACTIVITIES}) for key in packet['questions']}
        return json.dumps(dict(model=wire.MODEL,answers=answers,usage=dict(input_tokens=200,output_tokens=60))).encode()

    def records(self):
        return [json.loads(line)['record'] for line in self.recorder.path.read_text(encoding='utf8').splitlines()]

    def tick(self):
        self.f.now=eligible_worker_cut(self.f.now,self.last_worker_tick_at)
        before=len(self.records());self.driver.cycle(self.f.now)
        fresh=self.records()[before:]
        accepted={r['data']['receipt']['id'] for r in fresh if r['kind']=='worker_commit_result' and r['data']['receipt']['status']=='accepted'}
        prepared=[r['data'] for r in fresh if r['kind']=='worker_prepared' and r['data']['prepared']['commit_id'] in accepted]
        self.case.assertEqual(len(prepared),1,dict(fresh=fresh,driver=self.driver.status()))
        value=prepared[0]
        self.last_worker_tick_at=self.f.now
        return dict(commit=value['commit'],decision=value['prepared']['decision'],checkpoint=value['commit']['checkpoint'])

    def clear_authored_bootstrap(self,first_decision=None):
        bootstrap=self.tick() if first_decision is None else None
        first=bootstrap['decision'] if bootstrap is not None else first_decision
        self.f.now+=1 if first_decision is None else .05
        value={k:deepcopy(v) for k,v in self.worker['lifecycle_policy']['operator_clearance'].items() if k!='max_age_s'}
        history=[item for item in first['person_hazards']['items'] if item['kind']=='unreconciled_session_history']
        self.case.assertEqual(len(history),1,first['person_hazards'])
        value.update(hazard_refs=[item['hazard_ref'] for item in history],inspection_at=self.f.now)
        intent=dict(schema='home-live-intent-event-2',version=2,id='authored-desk-bridge-inspection',
            **self.source.scope,manifest_ref=deepcopy(self.worker['manifest_ref']),actor_ref='controlled-owner',
            origin='human_declaration',truth_role='runtime_intent',kind='clear_person_hazards',target=None,
            set_at=self.f.now,available_at=self.f.now,valid_until=self.f.now+5,reply_to=None,value=value)
        connection=http.client.HTTPConnection('127.0.0.1',self.graph.address[1],timeout=4)
        try:
            connection.request('POST',PREFIX+'/intent-events',json.dumps(intent),
                {'Authorization':'Bearer '+self.owner_token,'Content-Type':'application/json'})
            response=connection.getresponse();receipt=json.loads(response.read())
            self.case.assertEqual(response.status,200,receipt)
        finally:connection.close()
        # Process session history on the empty Journal before any person or
        # geometry admission. Present-person hazards remain under the ordinary
        # qualified protection/lifecycle contract and are never cleared here.
        cleared=self.tick()
        self.case.assertEqual(cleared['decision']['intent_results'][0]['status'],'applied')
        return dict(intent_receipt=receipt,worker=cleared,applied_at=self.f.now,bootstrap_worker=bootstrap)

    def _p1(self):
        visual=deepcopy(self.f.row);p=visual['payload'];association=p['arm_snapshot']['persons2d']['association']
        self.case.assertEqual(association['status'],'available',association)
        self.case.assertEqual(len(association['assignments']),1)
        slot=association['assignments'][0]
        media=self.store.get_media(self.session['id'],p['arm_snapshot']['frame']['media_id'],generation=self.session['generation'])
        packet=dict(schema='home-lab-view-person-semantics-1',version=1,
            basis={k:deepcopy(association[k]) for k in ('scope','source','track_epoch')},
            model=dict(request_id=lab_digest(dict(fixture='authored-person-request',visual=visual['id'],payload=lab_digest(p))),
                body_sha256=lab_digest(dict(fixture='authored-person-body',frame=p['frame_id'],sha256=media['sha256'])),
                prompt_version='lab-live-person-scene-prompt-1',engine_epoch='authored-qwen',artifact_sha256='d'*64),
            evidence=dict(media_id=media['id'],sha256=media['sha256'],width=640,height=360,frame_id=p['frame_id'],capture_epoch=p['capture_epoch']),
            observed_at=p['observed_at'],available_at=self.f.now,valid_until=association['source']['valid_until'],scene=dict(people_visible='one'),
            subjects=[dict(instance_id=slot['instance_id'],track_ref=slot['track_ref'],status='available',reason=None,
                facts=dict(subject_posture='sitting',held_object='none',book_state='not_visible',gaze_target='screen',typing_visible='yes',
                    screen_facing_subject='unclear',activity_hypothesis='computer_work',visible_basis=['hands_on_keyboard']))],
            decision=dict(status='unavailable',reason='subject_semantics_unqualified_for_decision'),viewing_only=True,
            identity_authority=False,geometry_authority=False,engagement_authority=False,physical_permission=False)
        self.store.append_event(self.session['id'],'qwen.person_observation',dict(packet=packet,
            source_event_id=visual['id'],source_payload_digest=lab_digest(p),fixture_evidence='authored_external_P1_model_receipt'))

    def _drain_reasoning(self):
        deadline=time.monotonic()+4
        while True:
            with self.store.lock:
                count=self.store.db.execute("SELECT count(*) FROM lab_reasoning_requests WHERE session_id=? AND state IN ('pending','dispatching')",
                                            (self.session['id'],)).fetchone()[0]
            if not count:return
            if time.monotonic()>deadline:raise AssertionError('authored_person_queue_did_not_drain')
            time.sleep(.001)

    def run_positive(self):
        steps=[];last=None
        origin=self.f.now
        # The initial Lab C is a retained input warmup. No camera/PC/P1/JEV
        # publication enters this Journal until history clearance is applied.
        bootstrap_clearance=self.clear_authored_bootstrap()
        for index,frame in enumerate(positive_frame_schedule(origin)):
            if index==0:continue
            if index:self.f.append_pair(sequence=frame['sequence'],observed_at=frame['observed_at'])
            # Actual header/full-body ports advance their own causal clock.
            # The decision cut follows those completed reads.
            self.f.now=max(self.f.now,frame['received_at'])
            # Preserve the original initial C/cursor. Consume any older cut
            # through the actual producer before using the new current desk.
            capture_results=[]
            for _ in range(3):
                camera=self.graph.step();capture_results.append(camera)
                if self.graph.after_id>=self.f.row['id']:break
            self.case.assertGreaterEqual(self.graph.after_id,self.f.row['id'],capture_results)
            self.case.assertEqual(camera['status'],'admitted_desk',camera)
            ha=fresh_pc_fixture(index+1,self.f.now)['original']['ha_event']
            self.store.append_event(self.session['id'],'ha.observation',ha['payload'])
            pc=self.graph.step()
            self.case.assertEqual(pc['status'],'admitted_attributed',pc)
            self._p1()
            offered=self.service.offer_reasoning(self.session['id'],[],generation=self.source.model_generation,cutoff=self.f.now,only_new=True)
            self.reasoner=self.service.reasoners.get(self.session['id'])
            self._drain_reasoning()
            semantic=[]
            for _ in range(8):
                item=self.graph.step();semantic.append(item)
                if item['status']=='idle':break
            self.case.assertTrue(any(v['status']=='admitted_attributed' for v in semantic),dict(offered=offered,semantic=semantic,
                events=[e for e in self.store.list_events(self.session['id']) if e['kind'].startswith('reasoning.')][-5:]))
            last=self.tick()
            decision_at=self.f.now
            clearance=bootstrap_clearance if index==1 else None
            steps.append(dict(index=index,observed_at=self.f.row['payload']['observed_at'],now=decision_at,clearance=clearance,
                camera=camera,capture_results=capture_results,pc=pc,offered=offered,semantic=semantic,
                decision=last['decision'],checkpoint=last['checkpoint']))
        events=self.store.list_events(self.session['id'])
        return {**last,'steps':steps,'bootstrap_clearance':bootstrap_clearance,
                'bridge_records':[e['payload'] for e in events if e['kind']=='qualification.person_work_bridge'],
                'jev_outputs':[e for e in events if e['kind']=='reasoning.person_output'],'pc_roots':[s['pc'] for s in steps],
                'fake_cloud_calls':len(self.calls),'classification':'authored_software_integration_only',
                'physical_authority':False,'household_qualification':False}

    def close(self):
        if self.closed:return getattr(self,'cleanup',None)
        self.closed=True
        if self.reasoner is not None:
            self.reasoner.close();self.reasoner.pool.shutdown(wait=True)
        result=dict(status='closed',physical_authority=False)
        withdrawal=None;worker_status='unconfirmed';worker_receipt=None
        if self.graph is not None and not self.graph.finished:
            withdrawal=self.graph.withdraw()
            if self.driver is not None:
                try:
                    # The controlled clock must advance beyond its last commit
                    # so the actual final drain can commit the withdrawal cut.
                    self.f.now+=.01
                    self.driver.cycle(self.f.now)
                    worker_receipt=self.driver.drain(deadline_ms=3000,
                        through_cursor=withdrawal['input_cursor'] or 0)
                    worker_status='settled' if worker_receipt.get('status')=='settled' else 'unconfirmed'
                except Exception:
                    worker_receipt=dict(status='unconfirmed',reason='actual_worker_drain_failed')
        if self.driver is not None:self.driver.close()
        if self.rpc is not None:self.rpc.close()
        if withdrawal is not None:
            result=self.graph.finalize(drain_receipt=dict(schema='home-acquisition-drain-1',version=1,
                session_ref=deepcopy(self.source.scope),configuration_digest=self.graph.configuration_digest,
                withdrawal_receipt=deepcopy(withdrawal['barrier_receipt']),recorder_status='incomplete',worker_status=worker_status,
                terminal_cursor=withdrawal['input_cursor'] or 0))
            result['actual_worker_drain']=worker_receipt
        self.stack.close()
        self.cleanup=result
        return result
