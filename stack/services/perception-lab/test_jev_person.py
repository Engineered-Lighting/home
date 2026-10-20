"""New batch protocol uses real local originals, reservation and authorization."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

import jev_person_wire as wire
from jev_person_source import freeze_person_context
from jev_person_provider import JevPersonProvider
from jev_person_policy import PersonJevPolicy
from jev_provider import ProviderFailure
from reasoning import ACTIVITIES, canonical

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('person_jev_authored_fixture', ROOT/'tools/fixtures/den-person-semantics-runtime.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class PersonJevTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Fixture()
        self.f.step([('desk', 20, True), ('sofa', 350, True)])
        self.store, self.session = self.f.store, self.f.session
        self.store.append_event(self.session['id'], 'reasoning.setting', {'enabled': True, 'revision': 2})
        self.now = self.f.la.now
        self.policy = dict(schema_version=1, enabled=True, scope='household_structured', revision='new-person-only',
                           generation=1, expires_at=self.now+30, site_id='victoria', camera_ids=['den'],
                           model_selector=wire.MODEL, max_calls_per_minute=10, max_session_calls=10,
                           structured_sources=[wire.SCHEMA, 'lab-live-person-scene-1'])
        self.gate = PersonJevPolicy(self.store, lambda: deepcopy(self.policy),
                                   lambda: dict(state='on', received_at=self.f.la.now))
        self.store.db.execute('CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT,payload TEXT)')
        self.calls = []
        self.transport_hook = None
        self.provider = JevPersonProvider(model_selector=wire.MODEL, response_version=wire.MODEL, authorize=self.gate,
                                           credential=lambda: 'authored-key', transport=self.transport, clock=lambda:self.f.la.now)
        self.args = dict(request_id='person-request', site_id='victoria', session_id=self.session['id'], arm='b',
                         generation=self.f.runner.generation, cutoff=self.now, deadline=self.now+15)

    def tearDown(self):
        self.f.close()

    def records(self):
        return [row for row in self.store.list_events(self.session['id']) if row['kind'] in ('perception.output','qwen.person_observation')]

    def freeze(self):
        return freeze_person_context(self.records(), **{k:self.args[k] for k in ('site_id','session_id','arm','generation','cutoff')})

    def reserve(self, context=None):
        context = self.freeze() if context is None else context
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?,?)',
                              (self.args['request_id'],self.session['id'],'dispatching',canonical(dict(person_context=context))))
        return context

    def transport(self, raw, token, timeout):
        packet=json.loads(raw)
        self.calls.append(packet)
        if self.transport_hook:
            self.transport_hook()
        answers={key:dict(type='choice', choice=activity, confidence=.8,
                          probabilities={label:float(label==activity) for label in ACTIVITIES})
                 for key,activity in zip(packet['questions'],('computer_work','reading'))}
        return json.dumps(dict(model=wire.MODEL,answers=answers)).encode()

    def test_actual_sources_batch_two_questions_private_membership_and_durable_admission(self):
        context=self.reserve()
        result=self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual([p['judgment']['activity'] for p in result['subjects']],['computer_work','reading'])
        self.assertEqual(len(self.calls),1)
        sent=canonical(self.calls[0])
        for word in ('track_ref','instance_id','session_id','capture_epoch','frame_id','box','sha256'):
            self.assertNotIn(word,sent)
        self.assertEqual(set(self.calls[0]['questions']),{'activity_person_1','activity_person_2'})
        self.assertNotIn('judgment',result)
        event=next(e for e in self.store.list_events(self.session['id']) if e['kind']=='reasoning.request')
        self.assertEqual(event['payload']['person_context'],context)
        self.assertEqual(event['payload']['timeline_verified'],'full')
        self.assertEqual(event['payload']['cutoff'],self.args['cutoff'])
        self.assertEqual(event['payload']['deadline'],self.args['deadline'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lab_jev_egress').fetchone()[0],1)

    def test_old_grants_or_unreserved_request_cannot_send(self):
        self.reserve()
        for grants in (['lab-live-scene-1','den-facts-8'],[wire.SCHEMA],['lab-live-person-scene-1']):
            self.policy['structured_sources']=grants
            with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=self.freeze(),**self.args)
        self.assertEqual(self.calls,[])
        self.policy['structured_sources']=[wire.SCHEMA,'lab-live-person-scene-1']
        self.store.db.execute('DELETE FROM lab_reasoning_requests')
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=self.freeze(),**self.args)
        self.assertEqual(self.calls,[])

    def test_publish_rechecks_original_expiry_and_revocation_after_actual_send(self):
        context=self.reserve()
        self.transport_hook=lambda:setattr(self.f.la,'now',self.now+4)
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lab_jev_egress').fetchone()[0],1)

    def test_forged_private_source_digest_is_refused_without_egress(self):
        context=self.reserve()
        context['semantics_payload_digest']='f'*64
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(self.calls,[])

    def test_new_original_during_transport_cannot_receive_old_subject_judgment(self):
        context=self.reserve()
        self.transport_hook=lambda:self.f.step([('new-desk',25,True),('new-sofa',345,True)])
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(len(self.calls),1)

    def test_policy_revocation_and_stop_after_send_refuse_publication(self):
        context=self.reserve()
        self.transport_hook=lambda:self.policy.update(structured_sources=[])
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(len(self.calls),1)
        self.policy['structured_sources']=[wire.SCHEMA,'lab-live-person-scene-1']
        self.args['request_id']='second-request'
        self.reserve(context)
        self.transport_hook=lambda:self.store.transition(self.session['id'],'stopping')
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(len(self.calls),2)

    def test_exact_dispatch_retry_cannot_send_twice_or_renew(self):
        context=self.reserve()
        self.provider.evaluate(self.records(),person_context=context,**self.args)
        with self.assertRaises(ProviderFailure):self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lab_jev_egress').fetchone()[0],1)

    def test_unknown_person_cannot_inherit_other_or_model_positive(self):
        self.f.step([('desk-next',25,True),('sofa-next',345,True)],mode='one_unknown')
        self.now=self.f.la.now
        self.args.update(cutoff=self.now,deadline=self.now+15)
        context=self.reserve()
        result=self.provider.evaluate(self.records(),person_context=context,**self.args)
        self.assertEqual(result['subjects'][0]['judgment']['activity'],'computer_work')
        self.assertEqual(result['subjects'][1]['judgment']['activity'],'insufficient_evidence')
        self.assertEqual(result['subjects'][1]['status'],'abstain')
        self.assertEqual(result['diagnostics']['subject_gates'],{'person_2':'source_evidence_unavailable'})

    def test_extra_question_or_unreviewed_response_revision_never_returns_judgment(self):
        original=self.provider.transport
        for index in range(2):
            self.args['request_id']='bad-response-'+str(index)
            context=self.reserve()
            def changed(raw,token,timeout):
                response=json.loads(original(raw,token,timeout))
                if index==0:response['answers']['activity_foreign']=deepcopy(next(iter(response['answers'].values())))
                else:response['model']='jev-99.0.0'
                return json.dumps(response).encode()
            self.provider.transport=changed
            with self.assertRaisesRegex(ProviderFailure,'provider_response_invalid'):
                self.provider.evaluate(self.records(),person_context=context,**self.args)
            self.store.db.execute("UPDATE lab_reasoning_requests SET state='failed' WHERE id=?",(self.args['request_id'],))


if __name__=='__main__':unittest.main()
