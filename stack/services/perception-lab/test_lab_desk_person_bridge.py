"""Actual generated stereo desk admission -> fixed person witness, offline only.

Camera/model I/O and independent commissioning are authored. The real Store,
stereo verifier, desk admission and Journal create the work parent; no completed
geometry/witness is fabricated. No household qualification is claimed.
"""
from copy import deepcopy
import unittest

import test_lab_stereo_desk_runtime as stereo
from test_lab_live_desk_anchor import GeneratedStereoFixture
from lab_person_semantic_admission import LabPersonSemanticAdmission
from lab_desk_person_bridge import DeskPersonBridgeSource
from jev_person_source import freeze_person_context
from journal import digest
from live_capture import _original_digest
from reasoning import digest as lab_digest
from reasoning import ACTIVITIES
import jev_person_wire as person_wire


class CompletePoseFixture(GeneratedStereoFixture):
    def execute(self, request):
        terminal = super().execute(request)
        pose = terminal['result']['pose_instances'][0]
        pose.update(crop_xyxy=[260., 80., 380., 340.], score_semantics='raw_model_response')
        pose['joints'] = [dict(joint_id=i, name='nose' if i == 0 else 'joint_'+str(i),
            pixel=[320., 150.] if i == 0 else None, score=.9 if i == 0 else None,
            status='supported' if i == 0 else 'uncertain') for i in range(17)]
        # The independently associated person is deliberately not object zero.
        terminal['result']['objects'].insert(0,dict(label='chair',instance_id='generated-chair'))
        return terminal

    def qualification(self, descriptor):
        result=super().qualification(descriptor)
        result['association']['instance_ref']='object-1'
        return result


class DeskPersonBridgeTests(unittest.TestCase):
    def setUp(self):
        class Fixture(stereo.LabStereoDeskRuntimeTests):
            def make_camera_fixture(self):
                return CompletePoseFixture(self)
        self.f = Fixture()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.graph = self.f.create()
        self.result = self.graph.step()
        self.assertEqual(self.result['status'], 'admitted_desk', self.result)
        self.visual = deepcopy(self.f.f.row)
        self.assertIn('association', self.visual['payload']['arm_snapshot']['persons2d'])
        ref = self.result['desk']['event']['payload']['original_receipt_refs'][0]
        original = self.graph.desk.original_receipt(ref['store_id'], ref['receipt_id'])
        association = original['inputs']['stereo_qualification']['association']
        self.bridge_policy = dict(source_id=self.f.policy['source_id'],
            method_ref=deepcopy(association['method_ref']), qualification_ref=deepcopy(association['assessment_ref']),
            receipt_store_id='authored-person-witness', receipt_component_id='person-work-association', max_bindings=1)
        self.retained = []
        self.owner = DeskPersonBridgeSource(self.f.scope, self.bridge_policy,
            desk_admission=self.graph.desk, original_retainer=self.retain, clock=lambda: self.f.f.now)
        policy = dict(schema='home-lab-person-semantic-admission-policy-1', version=1,
            base_policy=deepcopy(self.f.producer['semantic_policy']), bridge=deepcopy(self.bridge_policy))
        self.join = LabPersonSemanticAdmission(self.f.f.session, live_config=self.f.config,
            revision='synthetic-r1', policy=policy, original_retainer=self.retain, person_bridge=self.owner.capability)
        # The new semantic owner must resolve its own retained originals when
        # the same real Journal validates the resulting publication.
        old_resolver=self.graph.journal.live_receipt_resolver
        def resolve(store, receipt):
            try:return self.join.resolve_receipt(store,receipt)
            except KeyError:return old_resolver(store,receipt)
        self.graph.journal.live_receipt_resolver=resolve
        self.owner.bind(store=self.f.f.store, journal=self.graph.journal,
                        grant=self.graph.principal, semantic_admission=self.join)
        self.f.f.now += .05
        a = self.visual['payload']['arm_snapshot']['persons2d']['association']
        slot = a['assignments'][0]
        self.packet = dict(schema='home-lab-view-person-semantics-1', version=1,
            basis={k:deepcopy(a[k]) for k in ('scope','source','track_epoch')},
            model=dict(request_id='b'*64,body_sha256='c'*64,prompt_version='lab-live-person-scene-prompt-1',
                       engine_epoch='authored-qwen',artifact_sha256='d'*64),
            evidence=dict(media_id=self.visual['payload']['arm_snapshot']['frame']['media_id'],sha256='e'*64,
                          width=640,height=360,frame_id=a['source']['frame_id'],capture_epoch=a['scope']['capture_epoch']),
            observed_at=a['source']['observed_at'], available_at=self.f.f.now, valid_until=a['source']['valid_until'],
            scene=dict(people_visible='one'), subjects=[dict(instance_id=slot['instance_id'],track_ref=slot['track_ref'],
                status='available',reason=None,facts=dict(subject_posture='sitting',held_object='none',book_state='not_visible',
                gaze_target='screen',typing_visible='unclear',screen_facing_subject='unclear',activity_hypothesis='computer_work',
                visible_basis=['person_visible']))],
            decision=dict(status='unavailable',reason='subject_semantics_unqualified_for_decision'),viewing_only=True,
            identity_authority=False,geometry_authority=False,engagement_authority=False,physical_permission=False)
        sid = self.f.f.store.append_event(self.f.f.session['id'],'qwen.person_observation',dict(packet=self.packet,
            source_event_id=self.visual['id'],source_payload_digest=lab_digest(self.visual['payload'])))
        self.semantic = next(e for e in self.f.f.store.list_events(self.f.f.session['id']) if e['id']==sid)
        self.context = self.context_now()
        self.assertIsNotNone(self.context)

    def retain(self, record):
        self.retained.append(deepcopy(record))
        return dict(status='retained',digest=_original_digest(record))

    def context_now(self):
        return freeze_person_context(self.f.f.store.list_events(self.f.f.session['id']),site_id=self.f.scope['site_id'],
            session_id=self.f.scope['session_id'],arm='b',generation=self.f.model_generation,cutoff=self.f.f.now)

    def prepare(self):
        return self.owner.prepare_context(self.context)

    def test_actual_desk_original_retained_witness_is_accepted_before_request_cutoff(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'available', result)
        record = result['record']
        self.assertEqual(record, self.retained[-1])
        self.assertEqual(record['original']['instance_id'], 'generated-person')
        self.assertEqual(record['original']['qualified_original_refs'], self.result['desk']['event']['payload']['original_receipt_refs'])
        self.assertEqual(record['ref']['digest'], digest(record['original']))
        self.f.f.now += .01
        request = dict(id=self.semantic['id']+1,session_id=self.f.scope['session_id'],kind='reasoning.request',
            created_at=self.f.f.now,payload=dict(request_id='desk-person-jev',arm='b',generation=self.f.model_generation,
            status='admitted_before_transport',available_at=self.f.f.now,cutoff=self.f.f.now,person_context=self.context))
        binding = self.join.remember_person_request(request,events=[self.visual,self.semantic],received_at=self.f.f.now,
            journal=self.graph.journal,grant=self.graph.principal)
        self.assertEqual(len(binding['bindings']),1,binding)
        self.assertEqual(binding['bindings'][0]['bridge'],record)
        body=person_wire.build_packet(self.context)
        cutoff=self.f.f.now
        request['payload'].update(request=body,packet_sha256=lab_digest(body),wire_version=person_wire.SCHEMA,
            deadline=cutoff+1,evidence_ids=[str(self.visual['id']),str(self.semantic['id'])],timeline_ids=[],timeline_verified='full')
        # Freeze the exact fully populated request, as the actual queue does.
        self.join.person_requests.clear()
        self.join.remember_person_request(request,events=[self.visual,self.semantic],received_at=self.f.f.now,
            journal=self.graph.journal,grant=self.graph.principal)
        self.f.f.now+=.01
        delivery=dict(id=request['id']+1,session_id=self.f.scope['session_id'],kind='reasoning.delivery',created_at=self.f.f.now,
            payload=dict(request_id='desk-person-jev',arm='b',generation=self.f.model_generation,
                available_at=self.f.f.now,state='completed',status='ready',current=True))
        answer=dict(type='choice',choice='computer_work',confidence=.8,
                    probabilities={k:float(k=='computer_work') for k in ACTIVITIES})
        output=dict(id=request['id']+2,session_id=self.f.scope['session_id'],kind='reasoning.person_output',created_at=self.f.f.now,
            payload=dict(schema='home-lab-person-jev-output-1',version=1,request_id='desk-person-jev',site_id=self.f.scope['site_id'],
            session_id=self.f.scope['session_id'],arm='b',generation=self.f.model_generation,person_context=deepcopy(self.context),
            subjects=[dict(**self.context['slots'][0],status='ready',judgment=dict(activity='computer_work',confidence=.8,
                probabilities=deepcopy(answer['probabilities']),model_version=person_wire.MODEL))],
            provenance=dict(request_id='desk-person-jev',site_id=self.f.scope['site_id'],session_id=self.f.scope['session_id'],
                arm='b',generation=self.f.model_generation,cutoff=cutoff,deadline=cutoff+1,wire_version=person_wire.SCHEMA,
                question_version=person_wire.QUESTION_VERSION,response_version=person_wire.MODEL,input_digest=lab_digest(body),
                evidence_digest=lab_digest(self.context),evidence_ids=request['payload']['evidence_ids'],timeline_ids=[]),
            diagnostics=dict(request=body,answers={'activity_person_1':answer}),observed_at=self.context['packet']['observed_at'],
            available_at=self.f.f.now,valid_until=self.context['packet']['valid_until'],status='ready',viewing_only=True,physical_permission=False))
        published=self.join.publish_person_jev([self.visual,self.semantic,request,delivery,output],output_id=output['id'],
            received_at=self.f.f.now,journal=self.graph.journal,grant=self.graph.principal)
        self.assertEqual(published['status'],'admitted_attributed',published)
        facts=[p['payload'] for p in self.graph.journal.snapshot(self.graph.principal,self.f.scope['site_id'],self.f.scope['room_id'])['items']
               if p.get('payload_type')=='home-live-fact-1' and p['payload']['source_id']==self.join.policy['jev']['source_id']]
        self.assertEqual([(p['kind'],p['value']) for p in facts],[('activity_refinement','computer_work')])
        self.assertEqual(facts[0]['person_ref'],self.result['desk']['event']['payload']['person_ref'])

    def test_duplicate_never_renews_or_retains_twice(self):
        first = self.prepare()
        self.f.f.now += .01
        self.assertEqual(self.prepare(), first)
        self.assertEqual(len(self.retained),1)

    def test_unacknowledged_retention_is_not_visible_and_quiesce_unconfirmed(self):
        def lost(record):
            self.retained.append(deepcopy(record))
            raise TimeoutError('authored lost fsync acknowledgement')
        self.owner.retain = lost
        result = self.prepare()
        self.assertEqual(result['reason'],'person_bridge_retention_unconfirmed')
        query = self.join.bridge_query(self.context,self.context['slots'][0])
        self.assertIsNone(self.owner.capability.resolve(query))
        self.assertEqual(self.owner.quiesce()['status'],'unconfirmed')

    def test_wrong_assessment_pin_and_multi_person_context_refuse(self):
        self.owner.policy['qualification_ref']['digest']='0'*64
        self.assertEqual(self.prepare()['status'],'unavailable')
        self.assertFalse(self.retained)
        self.owner.policy=deepcopy(self.bridge_policy)
        context=deepcopy(self.context);context['slots'].append(dict(subject_slot='person_2',instance_id='other',track_ref='other'))
        result=self.owner.prepare_context(context)
        self.assertEqual(result['reason'],'one_qualified_desk_person_required')
        self.assertFalse(self.retained)

    def test_expiry_stop_and_quiesce_withhold_previously_completed_witness(self):
        self.assertEqual(self.prepare()['status'],'available')
        query = self.join.bridge_query(self.context,self.context['slots'][0])
        self.owner.quiesce()
        self.assertIsNone(self.owner.capability.resolve(query))
        self.assertEqual(self.prepare()['status'],'unavailable')

    def test_newer_visual_cannot_borrow_old_desk_or_frozen_context(self):
        self.assertEqual(self.prepare()['status'],'available')
        changed=deepcopy(self.visual['payload']);changed['observed_at']+=.01
        self.f.f.store.append_event(self.f.f.session['id'],'perception.output',changed)
        self.assertEqual(self.prepare()['status'],'unavailable')

    def test_confirmed_slow_retention_past_expiry_cannot_publish_or_leave_unknown_ack(self):
        def slow(record):
            ack=self.retain(record)
            self.f.f.now=self.context['packet']['valid_until']+.01
            return ack
        self.owner.retain=slow
        self.assertEqual(self.prepare()['status'],'unavailable')
        self.assertFalse(self.owner.records)
        self.assertEqual(self.owner.quiesce()['status'],'quiescent')


if __name__ == '__main__':
    unittest.main()
