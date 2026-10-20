"""Authored external person/work witnesses through actual Journal/worker only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import importlib.util
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'stack/services/perception-lab'), str(ROOT/'stack/services/home-context'), str(ROOT/'tools/den-live-runtime')]
from journal import Journal, digest
from live_capture import _original_digest
from live_test_helpers import NOW, SCOPE, TOKEN, MANIFEST, reference, geometry, event, receipt_resolver, LiveHttp
from live_worker_test_helpers import WorkerProbe
from controlled_external_io import assemble_journal_grants
from lab_person_semantic_admission import LabPersonSemanticAdmission, TrustedPersonBridge
from lab_live_semantic_admission import SemanticRefusal
from test_lab_live_semantic_admission_v2 import semantic_v2_fixture
from reasoning import ACTIVITIES, digest as lab_digest
import jev_person_wire as person_wire
from test_live_pc_support import authored_pc_fixture
from pc_support_configuration import upgrade_configuration


class PersonAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = semantic_v2_fixture(include_worker=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='authored-person-admission-')
        self.addCleanup(self.temp.cleanup)
        self.config, base, _, self.worker = deepcopy(self.fixture)
        if getattr(self,'allow_person_refinement',False):
            # An explicit authored successor commissions this interpretation
            # method. The deployed worker/policy is never modified by this test.
            self.worker['engagement_policy']['refinement_methods']=[deepcopy(base['jev']['method_ref'])]
            pc=deepcopy(self.worker['pc_support_policy'])
            self.config.update(schema='home-live-ingress-config-9',version=9)
            self.config['manifest'].update(schema='home-live-ingress-manifest-9',version=9)
            self.config['manifest'].pop('pc_support_interface_digest',None)
            for key in ('pc_support_policy','pc_support_schema_pins'):
                self.config['manifest']['worker'].pop(key,None)
            self.config,self.worker=upgrade_configuration(self.config,self.worker,pc)
            base['pc_support_policy_digest']=digest(pc)
        base.update(schema='home-lab-semantic-admission-policy-3', version=3,
                    root_lineage='persisted-observation-record-1')
        self.policy = dict(schema='home-lab-person-semantic-admission-policy-1', version=1, base_policy=base,
            bridge=dict(source_id='synthetic-source', method_ref=deepcopy(base['jev']['association_method_ref']),
                qualification_ref=reference('authored-independent-person-work-qualification'),
                receipt_store_id='authored-person-witness', receipt_component_id='person-work-association', max_bindings=1))
        self.now = NOW+.1
        self.session = deepcopy(getattr(self,'external_session',dict(id=SCOPE['session_id'], site_id=SCOPE['site_id'], camera_id='den', generation=0, started_at=NOW-10)))
        visual_root=str(getattr(self,'external_visual_id',10))
        self.proofs, self.retained = {}, []
        self.capability = TrustedPersonBridge(self.config['manifest']['identity'], self.policy['bridge'],
            lambda store, receipt: deepcopy(self.proofs.get((store, receipt))))
        self.join = self.make_join(self.capability)
        self.worker_token = 'authored-person-worker-token'
        grants = assemble_journal_grants(self.config, self.worker, publication_token=TOKEN,
            worker_token=self.worker_token, human_token='authored-person-human')
        for grant in grants:
            grant['revisions']=list(dict.fromkeys([*grant['revisions'],'synthetic-r1',self.worker['revision']]))
        def resolve(store, receipt):
            try:
                return self.join.resolve_receipt(store, receipt)
            except KeyError:
                value = receipt_resolver(store, receipt)
                if receipt == 'synthetic-capture-1:pose':
                    value['observation_root_times'] = {visual_root: NOW}
                    value['sensor_root_bindings'] = {visual_root: ['camera:synthetic-oak']}
                return value
        self.journal = Journal(Path(self.temp.name)/'journal.sqlite', grants=grants, profile='home-live-shadow-1',
            live_config=self.config, live_receipt_resolver=resolve, clock=lambda:self.now,
            live_wall_clock=lambda:self.now, live_monotonic=lambda:self.now-NOW)
        self.addCleanup(self.journal.close)
        self.records=[];self.journal.attach_recorder(self.records.append)
        self.principal = self.journal.authenticate(TOKEN)
        body = geometry('authored-person-qualified-work')
        body['manifest_ref'] = deepcopy(self.config['manifest_ref'])
        body['observation_roots'] = [visual_root, 'synthetic-frame-1:depth']
        body['observation_root_times'] = {root:NOW for root in body['observation_roots']}
        body['sensor_root_bindings'] = {root:['camera:synthetic-oak'] for root in body['observation_roots']}
        work = body['parts']['work']
        work['uncertainty'] = deepcopy(self.config['manifest']['sources']['synthetic-source']['part_policies']['work'])
        work['uncertainty']['age']['origin_at'] = NOW
        work['valid_until'] = min(work['valid_until'], NOW+work['uncertainty']['age']['max_age_s'])
        self.journal.append_event(event(body), self.principal)
        self.parent = self.journal.snapshot(self.principal, SCOPE['site_id'], SCOPE['room_id'])['items'][0]
        self.ref = self.join._work_ref(self.parent)
        self.now = NOW+.75
        self.rows = self.chain()

    def make_join(self, capability):
        return LabPersonSemanticAdmission(self.session, live_config=self.config, revision='synthetic-r1',
            policy=self.policy, original_retainer=lambda row:self.retain(row), person_bridge=capability)

    def retain(self, row):
        self.retained.append(deepcopy(row))
        return dict(status='retained', digest=_original_digest(row))

    def row(self, eid, kind, payload):
        return dict(id=eid, kind=kind, session_id=self.session['id'], created_at=self.now, payload=payload)

    def chain(self):
        source = dict(frame_id='authored-view-frame', request_id='authored-pose-request', observed_at=NOW,
            available_at=NOW+.05, valid_until=NOW+3, width=640, height=360)
        scope = dict(site_id=self.session['site_id'],camera_id='den',session_id=self.session['id'],session_generation=0,
            arm='b',generation=1,capture_epoch='authored-camera',engine_epoch='pose-epoch',artifact_sha256='a'*64,model_version='authored-pose')
        assignments=[dict(instance_id=name,track_ref='anonymous-'+name,status='continued',reason=None) for name in ('desk','sofa')]
        association=dict(schema='home-lab-view-person-association-1',version=1,scope=scope,source=source,track_epoch='view-epoch',
            status='available',reason=None,assignments=assignments,retired=[],viewing_only=True,
            identity_authority=False,geometry_authority=False,physical_permission=False)
        visual=self.row(10,'perception.output',dict(site_id=self.session['site_id'],camera_id='den',session_id=self.session['id'],
            arm='b',generation=1,current=True,status='completed',capture_epoch='authored-camera',frame_id=source['frame_id'],
            request_id=source['request_id'],engine_epoch=scope['engine_epoch'],artifact_sha256=scope['artifact_sha256'],model_version=scope['model_version'],
            observed_at=NOW,available_at=NOW+.05,valid_until=NOW+3,media_ids=['authored-rgb'],result=dict(schema_version=1,coordinate_space='source_pixels',frame_id=source['frame_id'],objects=[dict(label='person')]),
            arm_snapshot=dict(frame=dict(id=source['frame_id'],media_id='authored-rgb',width=640,height=360),
                persons2d=dict(association=association,persons=[dict(instance_id=a['instance_id']) for a in assignments]))))
        packet=dict(schema='home-lab-view-person-semantics-1',version=1,basis={k:deepcopy(association[k]) for k in ('scope','source','track_epoch')},
            model=dict(request_id='b'*64,body_sha256='c'*64,prompt_version='lab-live-person-scene-prompt-1',engine_epoch='authored-qwen',artifact_sha256='d'*64),
            evidence=dict(media_id='authored-rgb',sha256='e'*64,width=640,height=360,frame_id=source['frame_id'],capture_epoch=scope['capture_epoch']),
            observed_at=NOW,available_at=NOW+.2,valid_until=NOW+3,scene=dict(people_visible='multiple'),
            subjects=[dict(instance_id=a['instance_id'],track_ref=a['track_ref'],status='available',reason=None,
                facts=dict(subject_posture='sitting',held_object='none',book_state='not_visible',gaze_target='screen',typing_visible='unclear',
                    screen_facing_subject='unclear',activity_hypothesis='computer_work',visible_basis=['person_visible'])) for a in assignments],
            decision=dict(status='unavailable',reason='subject_semantics_unqualified_for_decision'),viewing_only=True,
            identity_authority=False,geometry_authority=False,engagement_authority=False,physical_permission=False)
        semantic=self.row(12,'qwen.person_observation',dict(packet=packet,source_event_id=10,source_payload_digest=lab_digest(visual['payload'])))
        context=dict(schema='home-lab-person-jev-input-1',version=1,semantics_event_id=12,semantics_payload_digest=lab_digest(semantic['payload']),
            visual_event_id=10,visual_payload_digest=lab_digest(visual['payload']),packet=deepcopy(packet),
            slots=[dict(subject_slot='person_'+str(i),instance_id=a['instance_id'],track_ref=a['track_ref']) for i,a in enumerate(assignments,1)])
        body=person_wire.build_packet(context)
        request=self.row(13,'reasoning.request',dict(request_id='person-jev-request',arm='b',generation=1,status='admitted_before_transport',
            cutoff=NOW+.3,deadline=NOW+10,available_at=NOW+.4,person_context=deepcopy(context),request=body,packet_sha256=lab_digest(body),wire_version='den-person-facts-1',evidence_ids=['10','12'],timeline_ids=[],timeline_verified='full'))
        delivery=self.row(14,'reasoning.delivery',dict(request_id='person-jev-request',arm='b',generation=1,available_at=NOW+.6,state='completed',status='ready',current=True))
        output=self.row(15,'reasoning.person_output',dict(schema='home-lab-person-jev-output-1',version=1,request_id='person-jev-request',site_id=self.session['site_id'],
            session_id=self.session['id'],arm='b',generation=1,person_context=deepcopy(context),subjects=[dict(**slot,status='ready',judgment=dict(activity=activity,confidence=.8,
                probabilities={k:1. if k==activity else 0. for k in ACTIVITIES},model_version='jev-1.13.0')) for slot,activity in zip(context['slots'],('computer_work','reading'))],
            provenance=dict(request_id='person-jev-request',site_id=self.session['site_id'],session_id=self.session['id'],arm='b',generation=1,
                cutoff=NOW+.3,deadline=NOW+10,wire_version='den-person-facts-1',question_version=person_wire.QUESTION_VERSION,response_version='jev-1.13.0',input_digest=lab_digest(body),evidence_digest=lab_digest(context),evidence_ids=['10','12'],timeline_ids=[]),
            diagnostics=dict(request=deepcopy(body),answers={'activity_'+slot['subject_slot']:dict(type='choice',choice=activity,confidence=.8,
                probabilities={k:float(k==activity) for k in ACTIVITIES}) for slot,activity in zip(context['slots'],('computer_work','reading'))}),
            observed_at=NOW,available_at=NOW+.6,valid_until=NOW+3,status='ready',viewing_only=True,physical_permission=False))
        return [visual,semantic,request,delivery,output]

    def install_proof(self, slot=0, mutate=None):
        context=self.rows[2]['payload']['person_context'];subject=context['slots'][slot]
        query=self.join.bridge_query(context,subject)
        original=dict(schema='home-lab-person-work-bridge-1',version=1,scope=deepcopy(self.join.scope),
            **{k:deepcopy(self.policy['bridge'][k]) for k in ('source_id','method_ref','qualification_ref')},
            basis=deepcopy(context['packet']['basis']),**deepcopy(subject),visual_event_id=context['visual_event_id'],
            visual_payload_digest=context['visual_payload_digest'],semantics_event_id=context['semantics_event_id'],
            semantics_payload_digest=context['semantics_payload_digest'],work_parent_ref=deepcopy(self.ref),
            qualified_original_refs=deepcopy(self.parent['payload']['original_receipt_refs']),observed_at=NOW,available_at=NOW+.25,valid_until=NOW+3)
        if mutate:mutate(original)
        receipt='person-work-bridge:'+digest(query)
        record=dict(ref=dict(store_id=self.policy['bridge']['receipt_store_id'],receipt_id=receipt,
            component_id=self.policy['bridge']['receipt_component_id'],digest=digest(original)),original=original)
        self.proofs[(record['ref']['store_id'],receipt)]=record
        return record

    def freeze(self):
        return self.join.remember_person_request(self.rows[2],events=self.rows[:2],received_at=self.now,journal=self.journal,grant=self.principal)

    def publish(self):
        return self.join.publish_person_jev(self.rows,output_id=15,received_at=self.now,journal=self.journal,grant=self.principal)

    def facts(self):
        return [json.loads(r[0])['payload'] for r in self.journal.db.execute("SELECT body FROM publications WHERE payload_type='home-live-fact-1'")]

    def test_retained_exact_bridge_copies_actual_parent_refs_and_only_refines_one_subject(self):
        proof=self.install_proof();frozen=self.freeze();result=self.publish()
        self.assertEqual(result['status'],'admitted_attributed');self.assertFalse(result['supports_engagement'])
        fact=self.facts()[0]
        self.assertEqual(fact['kind'],'activity_refinement');self.assertEqual(fact['value'],'computer_work')
        self.assertEqual(fact['person_ref'],self.parent['payload']['person_ref']);self.assertEqual(fact['parent_refs'],[self.ref])
        self.assertEqual(len(self.facts()),1,'sofa reading has no qualified bridge or support')
        self.assertEqual(self.retained[0]['original']['request_binding'],frozen)
        self.assertEqual(frozen['bindings'][0]['bridge'],proof)

    def test_unique_work_parent_without_retained_bridge_is_explicitly_unavailable(self):
        self.assertEqual(self.freeze()['bindings'],[])
        self.assertEqual(self.publish()['status'],'unavailable');self.assertFalse(self.facts())

    def test_absent_server_capability_cannot_be_replaced_by_request_proof(self):
        self.join=self.make_join(None);self.rows[2]['payload']['association']={'forged':'caller'}
        self.assertEqual(self.freeze()['bindings'],[]);self.assertEqual(self.publish()['status'],'unavailable')

    def test_foreign_epoch_identity_visual_or_qualification_proofs_do_not_grant(self):
        mutations=[lambda p:p['basis'].update(track_epoch='other'),lambda p:p.update(track_ref='other'),
            lambda p:p.update(visual_payload_digest='0'*64),lambda p:p.update(qualification_ref=reference('wrong')),
            lambda p:p.update(qualified_original_refs=[]),lambda p:p.update(available_at=NOW+.35),
            lambda p:p.update(valid_until=NOW+.5)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.proofs.clear();self.join=self.make_join(self.capability);self.install_proof(mutate=mutate)
                self.assertEqual(self.freeze()['bindings'],[]);self.assertEqual(self.publish()['status'],'unavailable')

    def test_conflicting_two_positive_subject_bridges_never_choose_array_order(self):
        self.install_proof(0);self.install_proof(1)
        self.assertEqual(self.freeze()['bindings'],[]);self.assertEqual(self.publish()['status'],'unavailable')

    def test_two_current_qualified_work_people_never_select_the_one_named_by_a_proof(self):
        other=deepcopy(self.parent['payload']);other['id']='authored-other-qualified-person'
        other['person_ref']['pid']=2;other['person_ref']['track_epoch']='other-qualified-track'
        other['head']['key']['person_ref']=deepcopy(other['person_ref'])
        self.journal.append_event(event(other),self.principal)
        self.install_proof();self.assertEqual(self.freeze()['bindings'],[])
        self.assertEqual(self.publish()['status'],'unavailable');self.assertFalse(self.facts())

    def test_fixed_original_resolver_missing_key_is_explicitly_unavailable(self):
        def missing(*_):raise KeyError('no_retained_witness')
        cap=TrustedPersonBridge(self.join.scope,self.policy['bridge'],missing)
        self.join=self.make_join(cap)
        self.assertEqual(self.freeze()['bindings'],[]);self.assertEqual(self.publish()['status'],'unavailable')

    def test_changed_frozen_request_or_context_never_rebinds(self):
        self.install_proof();self.freeze();self.rows[2]['payload']['person_context']['packet']['basis']['track_epoch']='changed'
        with self.assertRaises(SemanticRefusal):self.freeze()
        with self.assertRaises(SemanticRefusal):self.publish()

    def test_altered_original_deadline_or_authored_packet_never_qualifies(self):
        for change in ('deadline','packet','answer','delivery'):
            with self.subTest(change=change):
                self.join=self.make_join(self.capability);self.rows=self.chain();self.proofs.clear();self.install_proof();self.freeze()
                output=self.rows[-1]['payload']
                if change=='deadline':output['provenance']['deadline']+=1
                elif change=='packet':
                    self.rows[2]['payload']['request']={'authored':'not_the_person_wire'}
                    self.rows[2]['payload']['packet_sha256']=output['provenance']['input_digest']=lab_digest(self.rows[2]['payload']['request'])
                elif change=='answer':output['subjects'][0]['judgment']['activity']='reading'
                else:self.rows[3]['payload']['status']='abstain'
                with self.assertRaises(SemanticRefusal):self.publish()
        self.assertFalse(self.facts())

    def test_lab_payload_digest_keeps_float_originals_distinct_from_journal_pins(self):
        visual=self.rows[0];visual['payload']['result']['authored_raw_score']=1.0
        context=self.rows[2]['payload']['person_context']
        self.rows[1]['payload']['source_payload_digest']=lab_digest(visual['payload'])
        context['visual_payload_digest']=lab_digest(visual['payload'])
        context['semantics_payload_digest']=lab_digest(self.rows[1]['payload'])
        self.rows[-1]['payload']['person_context']=deepcopy(context)
        self.rows[-1]['payload']['provenance']['evidence_digest']=lab_digest(context)
        self.assertNotEqual(context['visual_payload_digest'],digest(visual['payload']))
        self.install_proof();self.freeze();self.assertEqual(self.publish()['status'],'admitted_attributed')

    def test_bridge_retirement_during_retention_prevents_first_append(self):
        self.install_proof();self.freeze()
        def retired(row):
            result=self.retain(row);self.proofs.clear();return result
        self.join.original_retainer=retired
        with self.assertRaisesRegex(SemanticRefusal,'retired_before_append'):self.publish()
        self.assertFalse(self.facts());self.assertIsNone(self.join.pending)

    def test_lost_ack_retries_exact_publication_without_new_binding(self):
        self.install_proof();self.freeze();append=self.journal.append_event;lost=[False]
        def fail_once(body,grant):
            receipt=append(body,grant)
            if body['payload']['source_id']=='jev-semantic' and not lost[0]:
                lost[0]=True;raise TimeoutError('authored_lost_ack')
            return receipt
        with mock.patch.object(self.journal,'append_event',side_effect=fail_once):
            with self.assertRaises(TimeoutError):self.publish()
        frozen=deepcopy(self.join.prepared[('jev',15)]['event'])
        self.proofs.clear()
        self.now=NOW+4
        self.assertEqual(self.publish()['status'],'admitted_attributed')
        self.assertEqual(self.join.prepared[('jev',15)]['event'],frozen);self.assertEqual(len(self.facts()),1)

    def test_explicit_wrapper_composes_actual_runtime_graph_and_base_original_archive(self):
        import test_lab_live_semantic_runtime as incumbent_fixture
        import lab_live_runtime
        f=incumbent_fixture.LabLiveSemanticRuntimeTests();f.setUp();self.addCleanup(f.doCleanups)
        producer=deepcopy(f.producer);base=producer['semantic_policy']
        base.update(schema='home-lab-semantic-admission-policy-3',version=3,root_lineage='persisted-observation-record-1')
        wrapper=dict(schema='home-lab-person-semantic-admission-policy-1',version=1,base_policy=base,
            bridge=dict(source_id='synthetic-source',method_ref=deepcopy(base['jev']['association_method_ref']),
                qualification_ref=reference('authored-independent-person-work-qualification'),receipt_store_id='authored-person-witness',
                receipt_component_id='person-work-association',max_bindings=1))
        producer['semantic_policy']=wrapper
        expected=lab_live_runtime.lab_graph_configuration(producer,session=f.f.session)
        composition=f.base.compose(producer);composition.monotonic=lambda:f.f.now-NOW
        graph=f.base.create_graph(composition)
        self.assertEqual(graph.config,expected);self.assertIsInstance(graph.semantic,LabPersonSemanticAdmission)
        self.assertIsNone(graph.semantic.person_bridge,'no installed capability is fabricated by composition')
        self.assertEqual(graph.join.root_lineage,'persisted-observation-record-1')
        self.assertEqual(composition.originals.semantic_binding['policy_digest'],digest(base))

    def test_real_queued_person_provider_and_qualified_pc_reach_actual_sole_worker(self):
        spec=importlib.util.spec_from_file_location('actual_person_queue',ROOT/'tools/fixtures/den-person-jev-runtime.py')
        queued=importlib.util.module_from_spec(spec);spec.loader.exec_module(queued)
        source=queued.Fixture(viewing=True,clock_origin=NOW+.2)
        self.addCleanup(source.close)
        scope=dict(site_id='victoria',room_id='den',session_id=source.session['id'],domain_epoch='authored-person-decision')
        source.f.step([('desk',20,True),('sofa',350,True)],label='qualified_authored_cut')
        originals=source.store.list_events(source.session['id'])
        from jev_person_source import freeze_person_context
        context=freeze_person_context([e for e in originals if e['kind'] in ('perception.output','qwen.person_observation')],
            site_id='victoria',session_id=source.session['id'],arm='b',generation=source.f.runner.generation,cutoff=source.f.la.now)
        self.assertIsNotNone(context)
        visual=next(e for e in originals if e['id']==context['visual_event_id'])
        semantic=next(e for e in originals if e['id']==context['semantics_event_id'])
        with mock.patch.dict(SCOPE,scope,clear=True),mock.patch.dict(MANIFEST,deepcopy(MANIFEST),clear=True):
            h=PersonAdmissionTests();h.fixture=semantic_v2_fixture(include_worker=True)
            h.external_session=source.store.get_session(source.session['id']);h.external_visual_id=visual['id']
            h.allow_person_refinement=True
            h.setUp();self.addCleanup(h.doCleanups)
        h.now=source.f.la.now
        # The retained commissioned witness exists before JEV reservation/send.
        h.rows=[deepcopy(visual),deepcopy(semantic),dict(payload=dict(person_context=deepcopy(context)))]
        h.install_proof(mutate=lambda proof:proof.update(available_at=context['packet']['available_at'],valid_until=context['packet']['valid_until']))
        from lab_live_acquisition import LabLiveAcquisition
        graph=LabLiveAcquisition.__new__(LabLiveAcquisition)
        graph.store,graph.session,graph.semantic=source.store,source.store.get_session(source.session['id']),h.join
        graph.media_generation,graph.bundled_source=source.session['generation'],None
        graph.journal,graph.principal=h.journal,h.principal
        graph.counters=dict(refusals=0,admitted=0,expired=0)
        self.assertEqual(graph._frame(visual)['frame_id'],visual['payload']['frame_id'],'actual acquisition resolves the existing scoped RAM original')
        original_transport=source.transport;holder={}
        def transport(raw,token,timeout):
            rows=source.store.list_events(source.session['id'])
            request=next(e for e in reversed(rows) if e['kind']=='reasoning.request')
            dependencies=graph._semantic_context(request)
            frozen=h.join.remember_person_request(request,events=dependencies,received_at=h.now,journal=h.journal,grant=h.principal)
            self.assertEqual(len(frozen['bindings']),1);holder['frozen']=frozen
            return original_transport(raw,token,timeout)
        def checked_transport(raw,token,timeout):
            try:return transport(raw,token,timeout)
            except BaseException as error:
                holder['error']=repr(error)
                raise
        source.transport=checked_transport
        offered=source.service.offer_reasoning(source.session['id'],[],generation=source.f.runner.generation,cutoff=source.f.la.now,only_new=True)
        source.reasoner=source.service.reasoners.get(source.session['id']);source.wait()
        self.assertIn('frozen',holder,dict(holder=holder,offered=offered,
            deliveries=[e['payload'] for e in source.store.list_events(source.session['id']) if e['kind']=='reasoning.delivery']))
        rows=source.store.list_events(source.session['id'])
        output=next(e for e in reversed(rows) if e['kind']=='reasoning.person_output')
        context=output['payload']['person_context']
        h.rows=[e for e in rows if e['id'] in {context['visual_event_id'],context['semantics_event_id']}
                or e['kind'] in ('reasoning.request','reasoning.delivery','reasoning.person_output') and e['payload'].get('request_id')==output['payload']['request_id']]
        h.now=source.f.la.now
        graph.pending_semantic=dict(event=output,events=graph._semantic_context(output),received_at=h.now,work_parent_ref=None)
        result=graph._publish_semantic()
        self.assertEqual(result['status'],'admitted_attributed');self.assertFalse(result['supports_engagement'])
        self.assertEqual(graph.after_id,output['id']);self.assertEqual(graph.counters['admitted'],1)
        person_fact=h.facts()[0];self.assertEqual(person_fact['value'],'computer_work')
        with mock.patch.dict(SCOPE,scope,clear=True):
            ha=authored_pc_fixture(snapshot_at=NOW+.15,input_at=NOW-.1)['original']['ha_event']
        ha=h.row(max(e['id'] for e in rows)+1,'ha.observation',ha['payload'])
        pc=h.join.publish_pc([ha],received_at=h.now,journal=h.journal,grant=h.principal,work_parent_ref=h.ref)
        self.assertTrue(pc['supports_engagement'],'the incumbent qualified PC proof is the positive support')
        server=LiveHttp(h.journal);self.addCleanup(server.close)
        probe=WorkerProbe();self.addCleanup(probe.close)
        worker={**deepcopy(h.worker),'token':h.worker_token,'service_url':'http://127.0.0.1:'+str(server.server.server_address[1])}
        self.assertTrue(worker['engagement_policy']['allow_experimental'])
        self.assertIn(h.policy['base_policy']['jev']['method_ref'],worker['engagement_policy']['refinement_methods'])
        probe.command('start',config=worker,t=h.now);consumed=probe.command('tick',t=h.now)
        self.assertFalse(consumed['http_errors']);self.assertEqual(len(consumed['decisions']),1)
        checkpoint=json.loads(h.journal.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',(worker['worker_id'],)).fetchone()[0])
        relations=list(checkpoint['state']['engagement']['reducer']['state']['relations'].values())
        self.assertEqual(len(relations),1,'sofa reading never creates a qualified engagement relation')
        relation=relations[0]
        self.assertEqual(relation['person_ref'],h.parent['payload']['person_ref'])
        self.assertEqual(relation['chosen_clause']['qualification'],'qualified_pc_input')
        self.assertEqual(relation['refine_candidate'],'computer_work','actual reducer uses this qualified person refinement')
        self.assertNotIn(person_fact['id'],relation['chosen_clause']['fact_ids'],'JEV cannot substitute for the PC positive')
        self.assertIn(person_fact['id'],[f['id'] for f in checkpoint['state']['engagement']['reducer']['state']['cache'].values()])
        self.assertEqual(len(source.calls),1);self.assertEqual(len(offered['request_ids']),1)
        self.assertEqual(source.store.db.execute('SELECT COUNT(*) FROM media').fetchone()[0],0)
        source.close();self.assertEqual(source.cleanup['jev_active_rows'],0)


if __name__=='__main__':unittest.main()
