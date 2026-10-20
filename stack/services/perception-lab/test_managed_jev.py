import unittest
import json
from unittest.mock import patch
import test_paired_runtime as fixtures
from managed_jev import reasoning_factory,session_policy


class ManagedJevTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def test_standing_policy_is_anchored_to_explicit_recording_not_current_time(self):
        policy={'schema_version':2,'enabled':True,'scope':'household_structured','revision':'sessions1',
            'generation':2,'site_id':'victoria','camera_ids':['den'],'model_selector':'jev-1.13.0',
            'session_seconds':300,'max_session_calls':600,'max_calls_per_minute':120}
        session={'state':'recording','reasoning_enabled':True,'site_id':'victoria','camera_id':'den','started_at':100}
        self.assertEqual(session_policy(policy,session,now=101)['expires_at'],400)
        self.assertEqual(session_policy(policy,session,now=399)['expires_at'],400)
        for changed,now in ((session,400),({**session,'reasoning_enabled':False},101),
                            ({**session,'state':'stopped'},101),({**session,'site_id':'echo'},101)):
            with self.assertRaises(ValueError):session_policy(policy,changed,now=now)
        with self.assertRaises(ValueError):session_policy({**policy,'session_seconds':301},now=101)
        # Caption egress is an explicit, versioned grant carried into the session lease.
        granted=session_policy({**policy,'historical_caption':'caption-activity-1'},session,now=101)
        self.assertEqual(granted['historical_caption'],'caption-activity-1')
        self.assertNotIn('historical_caption',session_policy(policy,session,now=101))
        with self.assertRaises(ValueError):session_policy({**policy,'historical_caption':True},now=101)
        from managed_jev import reasoning_factory
        config={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0'}
        for value,expected in ((policy,'not_granted'),({**policy,'historical_caption':'caption-activity-1'},'granted'),
                               ({**policy,'historical_caption':'caption-activity-9'},'not_granted')):
            with patch('managed_jev.private_json',side_effect=lambda p:dict(config if p=='config' else value)):
                self.assertEqual(reasoning_factory('config').readiness()['historical_caption'],expected)

    def test_real_provider_policy_and_scheduler_compose(self):
        from jev_provider import JevProvider
        from reasoning import ACTIVITIES
        from runtime import PairedRunner
        config={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0'}
        policy={'schema_version':1,'enabled':True,'scope':'household_structured','revision':'lab1',
            'generation':1,'expires_at':200,'site_id':'victoria','camera_ids':['den'],
            'model_selector':'jev-1.13.0','max_calls_per_minute':120}
        self.store.append_event(self.session['id'],'reasoning.setting',{'enabled':True,'revision':1})
        runner=PairedRunner(self.store,self.session['id'],self.profiles,lambda _:None,clock=self.store.clock)
        self.runners.append(runner)
        self.store.db.execute('CREATE TABLE lab_experiment_state(session_id TEXT,generation INTEGER)')
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?)',(self.session['id'],runner.generation))
        calls=[]
        def transport(body,*_):
            packet=json.loads(body);calls.append(packet)
            answers={'activity':{'type':'choice',
                'choice':'insufficient_evidence','confidence':.5,
                'probabilities':{k:float(k=='insufficient_evidence') for k in ACTIVITIES}}}
            answers.update({k:{'type':'noul','noul':.2} for k,q in packet['questions'].items() if q['type']=='noul'})
            return json.dumps({'model':'jev-1.13.0','answers':answers,'usage':{'input_tokens':100,'output_tokens':20}}).encode()
        def provider(**kw):return JevProvider(**{**kw,'credential':lambda:'fixture','transport':transport})
        with patch('managed_jev.private_json',side_effect=lambda p:dict(config if p=='config' else policy)),patch('managed_jev.JevProvider',side_effect=provider):
            reasoner=reasoning_factory('config')(self.store,self.session['id'])
            try:
                records=[{'id':arm,'site_id':'victoria','camera_id':'den','session_id':self.session['id'],
                    'arm':arm,'generation':runner.generation,'observed_at':99,'available_at':99.5,
                    'valid_until':103,'objects':[{'label':'person'}]} for arm in ('a','b')]
                reasoner.offer(records,generation=runner.generation,cutoff=100)
                fixtures.PairedRuntimeTests.settle(self,lambda:not reasoner.futures)
                self.assertEqual(len(calls),2)
                outcomes=self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.output'").fetchall()
                self.assertEqual(len(outcomes),2)
                for outcome in outcomes:
                    details=json.loads(outcome[0])['judgment']['diagnostics']
                    self.assertEqual(len(details['answers']),4)
                    self.assertEqual(details['usage']['input_tokens'],100)
            finally:reasoner.close();reasoner.pool.shutdown(wait=True)

    def test_camera_derived_ha_reaches_both_arm_packets_as_one_correlated_snapshot(self):
        from dataclasses import replace
        from ha_ingest import HAIngestor
        from live_inputs import live_records
        from jev_provider import JevProvider
        from reasoning import ACTIVITIES
        entity='binary_sensor.den_oak_person_occupancy'
        self.store.settings=replace(self.store.settings,cameras=({'id':'den'},))
        config={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0',
            'ha_bindings':{entity:'person_present'}}
        policy={'schema_version':1,'enabled':True,'scope':'household_structured','revision':'lab1',
            'generation':1,'expires_at':200,'site_id':'victoria','camera_ids':['den'],
            'model_selector':'jev-1.13.0','max_calls_per_minute':120}
        sid=self.session['id']
        self.store.append_event(sid,'reasoning.setting',{'enabled':True,'revision':1})
        self.store.db.execute('CREATE TABLE lab_experiment_state(session_id TEXT,generation INTEGER)')
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?)',(sid,1))
        for arm in ('a','b'):
            self.store.append_event(sid,'perception.output',{'site_id':'victoria','camera_id':'den','arm':arm,
                'generation':1,'status':'completed','observed_at':99.5,'available_at':99.6,
                'valid_until':102,'result':{'objects':[{'label':'person'}]}})
        HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='frigate-ha',entities=[entity],
            camera_derived_entities=[entity]).ingest(sid,snapshot_id='occupancy1',observed_at=99,states={entity:'on'})
        records=live_records(self.store,self.session,1,100,retain_history=True)
        def transport(body,*_):
            packet=json.loads(body)
            answers={'activity':{'type':'choice','choice':'insufficient_evidence','confidence':.5,
                'probabilities':{k:float(k=='insufficient_evidence') for k in ACTIVITIES}}}
            answers.update({k:{'type':'noul','noul':.2} for k,q in packet['questions'].items() if q['type']=='noul'})
            return json.dumps({'model':'jev-1.13.0','answers':answers}).encode()
        def provider(**kw):return JevProvider(**{**kw,'credential':lambda:'fixture','transport':transport})
        with patch('managed_jev.private_json',side_effect=lambda p:dict(config if p=='config' else policy)),patch('managed_jev.JevProvider',side_effect=provider):
            reasoner=reasoning_factory('config')(self.store,sid)
            try:
                reasoner.offer(records,generation=1,cutoff=100)
                fixtures.PairedRuntimeTests.settle(self,lambda:not reasoner.futures and len(self.store.db.execute(
                    "SELECT 1 FROM events WHERE kind='reasoning.output'").fetchall())==2)
            finally:reasoner.close();reasoner.pool.shutdown(wait=True)
        requests={json.loads(r[0])['arm']:json.loads(r[0])['request'] for r in self.store.db.execute(
            "SELECT payload FROM events WHERE kind='reasoning.request'")}
        self.assertEqual(set(requests),{'a','b'})
        shared=[]
        for arm,packet in requests.items():
            ha=[r for r in packet['state']['evidence'] if r['source_kind']=='ha.observation']
            visual=[r for r in packet['state']['evidence'] if r['source_kind']=='perception.output']
            self.assertEqual((len(ha),[r['arm'] for r in visual]),(1,[arm]))
            self.assertEqual(ha[0]['ha'],{'person_present':'on'})
            self.assertEqual(ha[0]['quality']['camera_derived_ha_facts'],['person_present'])
            self.assertEqual(ha[0]['quality']['correlated_with'],{'relation':'same_camera_visual','camera_id':'den'})
            self.assertEqual(ha[0]['camera_id'],visual[0]['camera_id'])
            semantic={o['evidence_id']:o for o in packet['state']['semantic']['observations']}
            self.assertEqual(semantic[ha[0]['id']]['correlated_with'],ha[0]['quality']['correlated_with'])
            self.assertIsNone(semantic[visual[0]['id']]['correlated_with'])
            self.assertEqual(packet['state']['semantic']['latest_visual_evidence_id'],visual[0]['id'])
            self.assertNotIn(entity,json.dumps(packet))
            shared.append((ha[0]['id'],ha[0]['evidence_roots']))
        self.assertEqual(shared[0],shared[1])
        # The operator's HA source id never leaves: the root is an opaque digest
        # (the local ledger keeps 'ha:frigate-ha:occupancy1').
        from jev_evidence import opaque
        self.assertEqual(shared[0][1],[opaque('root','ha:frigate-ha:occupancy1')])
        self.assertNotIn('frigate-ha',json.dumps(requests))
        for row in self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.output'"):
            self.assertEqual(json.loads(row[0])['judgment']['diagnostics']['projection_version'],'structured-semantic-8')

    def test_derived_computer_active_reaches_packets_without_raw_inputs(self):
        from dataclasses import replace
        from ha_ingest import HAIngestor
        from live_inputs import live_records
        from jev_provider import JevProvider
        from reasoning import ACTIVITIES
        session_entity,last_entity='sensor.formd_t1_sessionstate','sensor.formd_t1_lastactive'
        stamp='1970-01-01T00:01:20+00:00'  # 19 s before the snapshot
        self.store.settings=replace(self.store.settings,cameras=({'id':'den'},))
        config={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0',
            'ha_bindings':{'derived.computer_active':'computer_active'}}
        policy={'schema_version':1,'enabled':True,'scope':'household_structured','revision':'lab1',
            'generation':1,'expires_at':200,'site_id':'victoria','camera_ids':['den'],
            'model_selector':'jev-1.13.0','max_calls_per_minute':120}
        sid=self.session['id']
        self.store.append_event(sid,'reasoning.setting',{'enabled':True,'revision':1})
        self.store.db.execute('CREATE TABLE lab_experiment_state(session_id TEXT,generation INTEGER)')
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?)',(sid,1))
        for arm in ('a','b'):
            self.store.append_event(sid,'perception.output',{'site_id':'victoria','camera_id':'den','arm':arm,
                'generation':1,'status':'completed','observed_at':99.5,'available_at':99.6,
                'valid_until':102,'result':{'objects':[{'label':'person'}]}})
        HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='pc-ha',entities=[session_entity,last_entity],
            derived={'computer_active':{'session_entity':session_entity,'unlocked_values':['Unlocked'],
                                        'last_active_entity':last_entity,'max_idle_seconds':300}}
            ).ingest(sid,snapshot_id='pc1',observed_at=99,states={session_entity:'Unlocked',last_entity:stamp})
        records=live_records(self.store,self.session,1,100,retain_history=True)
        def transport(body,*_):
            packet=json.loads(body)
            answers={'activity':{'type':'choice','choice':'insufficient_evidence','confidence':.5,
                'probabilities':{k:float(k=='insufficient_evidence') for k in ACTIVITIES}}}
            answers.update({k:{'type':'noul','noul':.2} for k,q in packet['questions'].items() if q['type']=='noul'})
            return json.dumps({'model':'jev-1.13.0','answers':answers}).encode()
        def provider(**kw):return JevProvider(**{**kw,'credential':lambda:'fixture','transport':transport})
        with patch('managed_jev.private_json',side_effect=lambda p:dict(config if p=='config' else policy)),patch('managed_jev.JevProvider',side_effect=provider):
            reasoner=reasoning_factory('config')(self.store,sid)
            try:
                reasoner.offer(records,generation=1,cutoff=100)
                fixtures.PairedRuntimeTests.settle(self,lambda:not reasoner.futures and len(self.store.db.execute(
                    "SELECT 1 FROM events WHERE kind='reasoning.output'").fetchall())==2)
            finally:reasoner.close();reasoner.pool.shutdown(wait=True)
        requests={json.loads(r[0])['arm']:json.loads(r[0])['request'] for r in self.store.db.execute(
            "SELECT payload FROM events WHERE kind='reasoning.request'")}
        self.assertEqual(set(requests),{'a','b'})
        for arm,packet in requests.items():
            ha=[r for r in packet['state']['evidence'] if r['source_kind']=='ha.observation']
            self.assertEqual(len(ha),1)
            self.assertEqual(ha[0]['ha'],{'computer_active':'on'})
            self.assertNotIn('camera_derived_ha_facts',ha[0]['quality'])
            self.assertNotIn('correlated_with',ha[0]['quality'])
            text=json.dumps(packet)
            for private in (session_entity,last_entity,'formd','derived.computer_active',stamp,'1970-',
                            'Unlocked','computer_active_v1','max_idle_seconds'):
                self.assertNotIn(private,text)

    def test_derived_bindings_must_name_their_own_fact(self):
        base={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0'}
        with patch('managed_jev.private_json',return_value={**base,'ha_bindings':{'derived.computer_active':'computer_active'}}):
            self.assertEqual(reasoning_factory('config').ha_bindings,{'derived.computer_active':'computer_active'})
        for bindings in ({'derived.computer_active':'screen_playback'},{'derived.computer_active':'person_present'},
                         {'derived.screen_active':'computer_active'}):
            with self.subTest(bindings=bindings),patch('managed_jev.private_json',return_value={**base,'ha_bindings':bindings}):
                with self.assertRaisesRegex(ValueError,'Registered semantic source bindings'):
                    reasoning_factory('config')

    def test_session_setting_revokes_publication_and_construction_has_no_network(self):
        config={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0'}
        policy={'revision':'household-lab-1','enabled':True,'model_selector':'jev-1.13'}
        self.store.append_event(self.session['id'],'reasoning.setting',{'enabled':True,'revision':1})
        # Fixture session already represents a recording; no hardware is started.
        with patch('managed_jev.private_json',side_effect=lambda p:dict(config if p=='config' else policy)), \
                patch('managed_jev.JevProvider') as provider, patch('managed_jev.LiveReasoning'):
            factory=reasoning_factory('config')
            provider.assert_not_called()
            factory(self.store,self.session['id'])
            gate=provider.call_args.kwargs['authorize']
            self.assertTrue(gate.read()['revision'].endswith(':1'))
            self.store.append_event(self.session['id'],'reasoning.setting',{'enabled':False,'revision':2})
            with self.assertRaisesRegex(ValueError,'revoked'):gate.read()
            self.store.append_event(self.session['id'],'reasoning.setting',{'enabled':True,'revision':3})
            with self.assertRaisesRegex(ValueError,'revoked'):gate.read()

    def test_expired_policy_is_visible_without_provider_call(self):
        config={'schema_version':1,'policy_file':'policy','credential_file':'key','response_version':'jev-1.13.0'}
        policy={'enabled':True,'expires_at':90}
        with patch('managed_jev.private_json',side_effect=lambda p:dict(config if p=='config' else policy)),patch('managed_jev.time.time',return_value=100),patch('managed_jev.JevProvider') as provider:
            factory=reasoning_factory('config')
            self.assertEqual(factory.readiness()['reason'],'jev_policy_expired')
            policy['expires_at']=200
            self.assertTrue(factory.readiness()['available'])
            # Schema 1 reports the same explicit caption egress grant.
            self.assertEqual(factory.readiness()['historical_caption'],'not_granted')
            policy['historical_caption']='caption-activity-1'
            self.assertEqual(factory.readiness()['historical_caption'],'granted')
            provider.assert_not_called()
