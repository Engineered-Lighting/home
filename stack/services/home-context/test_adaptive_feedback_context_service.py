"""Fresh native E journals and literal loopback admission; no scenarios/devices."""
import copy
import hashlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode,quote
from urllib.request import ProxyHandler,Request,build_opener

from journal import Journal,JournalError,FEEDBACK_PROFILE,PREFIX,digest,canonical,Limits
from schema_registry import SchemaRegistry,FEEDBACK_SCHEMAS
from adaptive_feedback_validation import validate_feedback,preference_unchanged_projection,original_family_clocks
from server import LocalServer

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def example(name):
    return json.loads((ROOT/'contracts/home-adaptive-feedback/examples'/(name+'.json')).read_text())

def fixture():
    script="const f=require('./tools/fixtures/home-adaptive-experience-synthetic.cjs').makeFixture({variant_id:'P-A-D-adequate-protected-yes',duration_s:10});process.stdout.write(JSON.stringify({identity:f.identity,clock_profile:f.clock_profile,materials:f.materials,registered_surfaces:f.registered_surfaces,config:f.config,rows:f.inputsAt(0)}));"
    f=json.loads(subprocess.check_output(['node','-e',script],cwd=ROOT,text=True,timeout=10))
    def scoped(name):
        v=example(name);v.update(**f['identity'],clock_profile_digest=f['clock_profile']['digest']);return v
    binding=scoped('home-experience-actor-binding-1')
    geom=next(x for x in f['rows'] if x['schema']=='home-geometry-3')
    binding.update(principal_id='owner',feedback_origin={'producer_id':'owner','producer_epoch':'owner-1'},actor_ref='synthetic-owner',person_ref=geom['objects'][0]['person_ref'])
    binding['allowed_bindings']=[{'owner_id':'synthetic-owner','subject':{'kind':'session','id':f['identity']['session_id']},'preference_set_id':'reading:person1:book-1','context_set_id':'context:book-1','beneficiary':{'kind':'person','person_ref':geom['objects'][0]['person_ref']},'target':{'kind':'object','object_ref':geom['objects'][0]['object_ref'],'registration_ref':None},'association_generation':0}]
    binding['binding_digest']=digest({k:v for k,v in binding.items() if k!='binding_digest'})
    policy=scoped('home-experience-classifier-policy-1');policy.update(max_feedback_age_s=3,max_exposure_age_s=3,max_exposure_span_s=1)
    policy['illumination_lux_bounds']={'min':0,'max':1000}
    preference=next(x for x in f['rows'] if x['schema']=='home-preference-snapshot-1')
    authority=scoped('home-preference-write-authority-1')
    authority.update(owner_id=preference['owner_id'],subject=preference['subject'],preference_set_id=preference['preference_set_id'],context_set_id='context:book-1',actor_ref='synthetic-owner',person_ref=geom['objects'][0]['person_ref'],target=preference['target'],writer_principal_id='owner',writer_origin={'producer_id':'owner','producer_epoch':'owner-1'},feedback_origin={'producer_id':'owner','producer_epoch':'owner-1'},allowed_operations=['learn','default','recall','human_undo'])
    n=authority['authority_component']['normalized'];authority['authority_component'].update(**f['identity'],clock_profile_digest=f['clock_profile']['digest'])
    n.update(scope=f['identity'],owner_id=authority['owner_id'],chain_id=authority['preference_set_id'],preference_subject=authority['subject'],set_id=authority['preference_set_id'],actor_ref=authority['actor_ref'],person_ref=authority['person_ref'],target=authority['target'],writer_origin=authority['writer_origin'],feedback_origin=authority['feedback_origin'],allowed_operations=authority['allowed_operations'])
    n['allowed_operations']=['owner_undo' if x=='human_undo' else x for x in n['allowed_operations']]
    config=copy.deepcopy(f['config']);config.update(profile_mode=FEEDBACK_PROFILE,interface_digest=SchemaRegistry((),profile=FEEDBACK_PROFILE).interface_digest)
    producer_epochs=[{'principal_id':p,'producer_id':p,'producer_epoch':p+'-1'} for p in ('issuer','owner','worker')]
    feedback={'producer_epochs':producer_epochs,'actor_bindings':[binding],'classifier_policy':policy,'write_authorities':[authority]}
    config['feedback_config_digest']=digest(feedback)
    f.update(**feedback,config=config,config_digest=digest(config))
    return f

def grants(f):
    r=SchemaRegistry((),profile=FEEDBACK_PROFILE)
    names=sorted(r.publication_families)
    regs={x['registration_ref']:{'source_id':x['source_id'],'geometry_revision':'synthetic-geometry','registry_digest':x['registry_digest'],'coverage_refs':['synthetic-pages']} for x in f['materials']+f['registered_surfaces']}
    result=[]
    for principal in ('issuer','owner','worker'):
        permitted=[n for n in names if principal!='owner' or n not in {'home-preference-write-application-1','home-evidence-reply-3','home-acquisition-status-2'}]
        result.append({'principal_id':principal,'producer_id':principal,'producer_epoch':principal+'-1','expected_consumer_id':principal,
                       'token_sha256':hashlib.sha256(('test-'+principal).encode()).hexdigest(),'scopes':[{k:f['identity'][k] for k in ('site_id','room_id')}],
                       'operations':['read','worker_commit'] if principal=='worker' else ['read','event','intent','feedback','evidence_request','evidence_reply','revoke'],
                       'payload_types':permitted,'schema_pins':{n:hashlib.sha256((FEEDBACK_SCHEMAS/(n+'.schema.json')).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in permitted},
                       'revisions':['1.0','2.0','3.0','4.0','5.0'],'source_ids':['scripted-camera','scripted-daylight'],'registration_bindings':regs,
                       'expected_actor_ref':'synthetic-owner','expected_worker_id':'worker' if principal=='worker' else None,
                       'adaptive_subjects':[{'owner_id':'synthetic-owner','scope':f['identity'],'subject_rights':[{'subject':{'kind':'session','id':f['identity']['session_id']},'chain_ids':['reading:person1:book-1','context:book-1','protect:synthetic-protected-1'],'rights':['read','write']}]}]})
    reader=copy.deepcopy(next(g for g in result if g['principal_id']=='owner'))
    reader.update(operations=['read'],payload_types=names,schema_pins={n:hashlib.sha256((FEEDBACK_SCHEMAS/(n+'.schema.json')).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in names},token_sha256=hashlib.sha256(b'test-owner-read').hexdigest())
    return result+[reader]

class FeedbackServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.f=fixture()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=HERE);self.now=0
        config={k:self.f[k] for k in ('identity','clock_profile','materials','registered_surfaces','config','config_digest','producer_epochs','actor_bindings','classifier_policy','write_authorities')}
        self.j=Journal(Path(self.tmp.name)/'fresh.sqlite',profile=FEEDBACK_PROFILE,adaptive_feedback_config=config,grants=grants(self.f),clock=lambda:self.now)
        self.owner,self.worker,self.issuer=[next(g for g in self.j.grants if g.principal_id==p) for p in ('owner','worker','issuer')]
        self.owner_read=next(g for g in self.j.grants if g.principal_id=='owner' and g.operations==frozenset({'read'}))
        self.identity=self.f['identity']
    def tearDown(self):self.j.close();self.tmp.cleanup()
    def use_fixture(self,f,*,extra_chains=(),worker_goals=False):
        """Replace only this test's empty journal with a fresh configured one."""
        self.j.close();self.f=f;self.identity=f['identity'];self.now=0
        f['config']['materials_digest']=digest(f['materials'])
        f['config']['feedback_config_digest']=digest({k:f[k] for k in ('producer_epochs','actor_bindings','classifier_policy','write_authorities')})
        f['config_digest']=digest(f['config'])
        configured=grants(f)
        if worker_goals:
            worker=next(g for g in configured if g['principal_id']=='worker')
            worker['adaptive_subjects'].append({'owner_id':'worker','scope':f['identity'],'subject_rights':[{
                'subject':{'kind':'session','id':f['identity']['session_id']},'chain_ids':[],
                'derived_goal_namespace':'goal:worker:','rights':['read','derive']}]})
        for g in configured:
            for auth in g['adaptive_subjects']:
                for rule in auth['subject_rights']:rule['chain_ids'].extend(extra_chains)
        config={k:f[k] for k in ('identity','clock_profile','materials','registered_surfaces','config','config_digest','producer_epochs','actor_bindings','classifier_policy','write_authorities')}
        self.fixture_sequence=getattr(self,'fixture_sequence',0)+1
        self.j=Journal(Path(self.tmp.name)/('identity-'+str(self.fixture_sequence)+'.sqlite'),profile=FEEDBACK_PROFILE,adaptive_feedback_config=config,grants=configured,clock=lambda:self.now)
        self.owner,self.worker,self.issuer=[next(g for g in self.j.grants if g.principal_id==p) for p in ('owner','worker','issuer')]
        self.owner_read=next(g for g in self.j.grants if g.principal_id=='owner' and g.operations==frozenset({'read'}))
    def scoped(self,name):
        v=example(name);v.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest']);return v
    def preference(self):
        p=copy.deepcopy(next(x for x in self.f['rows'] if x['schema']=='home-preference-snapshot-1'))
        p.update(schema='home-preference-snapshot-2',version=2,condition_tags=None,context_set_id=None,rule_valid_until=120,write_origin=None)
        return p
    def bootstrap(self):
        """Explicit service contract fixture, not an integrated native worker."""
        fence=self.j.dynamic.input_cut(self.worker,self.identity['site_id'],self.identity['room_id'],consumer_id='worker',after=0,t=0)
        c=self.scoped('home-context-worker-commit-7');c.update(id='bootstrap',journal_epoch=self.j.stream_epoch,expected_authority=fence['authority'],outputs=[])
        cut=c['input_cut'];cut.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'],journal_epoch=self.j.stream_epoch,cut_id=fence['cut_id'],input_cursor=fence['cut_cursor'],authority=fence['authority'])
        cut['digest']=digest({k:v for k,v in cut.items() if k!='digest'})
        cp=c['checkpoint'];cp.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'],journal_epoch=self.j.stream_epoch,config_digest=self.f['config_digest'],interface_digest=self.j.schemas.interface_digest,commit_seq=1,authority=fence['authority'])
        cp['revisions']['identity']=self.identity;cp['revisions']['clock_profile_digest']=self.f['clock_profile']['digest']
        return c
    def semantic_genesis(self):
        """Closed dark numeric fixture, without optical or worker acceptance."""
        c=self.bootstrap();self.j.dynamic.worker_commit(c,self.worker)
        fence=self.j.dynamic.input_cut(self.worker,self.identity['site_id'],self.identity['room_id'],consumer_id='worker',after=0,t=0)
        c=copy.deepcopy(c);c.update(id='semantic-zero',kind='semantic',expected_commit_seq=1)
        c['input_cut']['cut_id']=fence['cut_id'];c['input_cut']['digest']=digest({k:v for k,v in c['input_cut'].items() if k!='digest'})
        cp=c['checkpoint'];cp.update(commit_seq=2,semantic_seq=0,next_semantic_t=1,next_control_t=.2,boundary_phase='ready',last_installed_t=0)
        cp['reducer'].update(last_t=0,semantic_seq=0)
        eng=self.scoped('home-engagement-state-4');eng.update(id='engagement-zero',semantic_seq=0)
        pol=json.loads((ROOT/'contracts/home-adaptive-experience/examples/home-lighting-policy-decision-3.json').read_text())
        pol.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'],id='policy-zero',requests=[],dispositions=[],intent_results=[],consumed_refs=[])
        installed=self.scoped('home-lighting-installed-boundary-1');installed.update(id='installed-zero',input_cut_digest=c['input_cut']['digest'])
        installed['installed_state'].update(registry_digest=self.f['config']['registry']['digest'],clock_profile_digest=self.f['clock_profile']['digest'])
        installed['installed_state']['digest']=digest({k:v for k,v in installed['installed_state'].items() if k!='digest'})
        cp['pipeline']={'t':0,'commands':installed['installed_state']['commands'],'policy':pol,'leases':[],'frozen_heads':[],'intent_ids':[],'intents':[],'intent_outcomes':[],'fixtures':installed['installed_state']['fixtures'],'config_digest':self.f['config_digest'],'exposure_context':None,'reservations':[],
                        'feedback_exposure':{'held_context':None,'acknowledged_boundaries':[],'completed_intervals':[]}}
        report=self.scoped('home-lighting-settled-exposure-1');report.update(id='report-zero',t=0,semantic_seq=0)
        c['outputs']=[eng,pol,installed,report]
        cp['pending_installed']={'commit_id':c['id'],'control_seq':0,'payload_type':installed['schema'],'payload_digest':digest(installed)}
        return c
    def test_worker_output_proof_joins_exact_wire_and_separate_receipts(self):
        c=self.semantic_genesis();receipt=self.j.dynamic.worker_commit(c,self.worker)
        cursor=next(x['cursor'] for x in receipt['receipts'] if x['id']=='installed-zero')
        args={'cursor':cursor,'consumer_id':'owner'};site,room=self.identity['site_id'],self.identity['room_id']
        proof=self.j.dynamic.lookup_output_proof(self.owner,site,room,**args)
        self.assertEqual(proof['commit'],c);self.assertEqual(proof['request_canonical_json'],canonical(c))
        self.assertIsNone(proof['consumer_receipt']);self.assertIsNone(proof['worker_receipt'])
        self.now=.2;self.j.page(self.owner,site,room,consumer_id='owner')
        delivered=self.j.dynamic.lookup_output_proof(self.owner,site,room,**args)
        self.assertEqual(delivered['consumer_receipt']['received_at'],.2);self.assertIsNone(delivered['worker_receipt'])
        self.now=.4
        self.assertEqual(delivered['consumer_receipt'],self.j.dynamic.lookup_output_proof(self.owner,site,room,**args)['consumer_receipt'])
        self.j.db.execute('UPDATE feedback_worker_commit_originals SET request_digest=? WHERE id=?',('0'*64,c['id']))
        with self.assertRaises(JournalError):self.j.dynamic.lookup_output_proof(self.owner,site,room,**args)
    def test_typed_worker_proof_rejects_internal_join_and_receipt_mutants(self):
        c=self.semantic_genesis();r=self.j.dynamic.worker_commit(c,self.worker)
        cursor=next(x['cursor'] for x in r['receipts'] if x['id']=='installed-zero')
        proof=self.j.dynamic.lookup_output_proof(self.owner,self.identity['site_id'],self.identity['room_id'],cursor=cursor,consumer_id='owner')
        self.assertTrue(validate_feedback(proof,self.j.schemas)['ok'])
        for path,value in ((('receipt','control_seq'),1),(('publication','context_seq'),99),(('publication','cursor'),99),
                           (('receipt','first_received_at'),1),(('commit','expected_input_cursor'),1),(('commit','input_cut','complete'),False)):
            mutant=copy.deepcopy(proof);at=mutant
            for key in path[:-1]:at=at[key]
            at[path[-1]]=value
            if path[0]=='commit':mutant['request_canonical_json']=canonical(mutant['commit']);mutant['receipt']['raw_request_digest']=digest(mutant['commit'])
            self.assertFalse(validate_feedback(mutant,self.j.schemas)['ok'],path)
        mutant=copy.deepcopy(proof);mutant['consumer_receipt']={'consumer_id':'owner','received_at':0,'eligible_at':0,'digest':'0'*64}
        self.assertFalse(validate_feedback(mutant,self.j.schemas)['ok'])
    def write_fixture(self,*,deliver=True,policy_owner='selector',operation='default'):
        """Authored service CAS fixture; it makes no learner-truth claim."""
        p=self.preference();pr=self.j.dynamic.declaration(p,self.owner,family=p['schema'])
        def pub(r):return json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(r['cursor'],)).fetchone()[0])
        preference=pub(pr);prefref={'preference_set_id':p['preference_set_id'],'revision':p['revision'],'digest':digest(p),'source_ref':self.j.dynamic._source_ref(preference)}
        ctx=self.scoped('home-experience-context-declaration-1')
        ctx.update(owner_id=p['owner_id'],subject=p['subject'],preference_set_id=p['preference_set_id'],context_set_id='context:book-1',actor_ref='synthetic-owner',beneficiary=p['beneficiary'],target=p['target'],tags=['reading'])
        for child in ctx['context_components'].values():
            child.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'])
            child['normalized'].update(scope=self.identity,owner_id=p['owner_id'],chain_id=ctx['context_set_id'],preference_subject=p['subject'],set_id=p['preference_set_id'],tags=['reading'])
        ctx['context_components']['model']['normalized'].update(actor_ref='synthetic-owner',person_ref=p['beneficiary']['person_ref'],target=p['target'])
        context=pub(self.j.dynamic.declaration(ctx,self.owner,family=ctx['schema']))
        contextref={'context_set_id':ctx['context_set_id'],'revision':ctx['revision'],'digest':digest(ctx),'source_ref':self.j.dynamic._source_ref(context)}
        authority=pub(self.j.append_event(self.j.dynamic._event_for(self.f['write_authorities'][0],self.issuer),self.issuer))
        authorityref=self.j.dynamic._source_ref(authority)
        pol=self.scoped('home-preference-learning-policy-1')
        for child in pol['policy_components'].values():
            child.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest']);child['normalized']['scope']=self.identity
        pol['policy_components']['table']['normalized'].update(max_delta_lux=25,step_lux=25,proposal_ttl_s=10)
        pol['policy_components']['model']['normalized'].update(max_delta_lux=25,step_lux=25,proposal_ttl_s=10,belief_duration_s=100,reservations=[{'id':'model-'+operation+'-'+str(i),'operation':operation,'tags':['reading']} for i in range(5)])
        pol['policy_components']['selector']['normalized'].update(max_delta_lux=25,proposal_ttl_s=10,belief_duration_s=100,reservations=[{'id':operation+'-'+str(i),'operation':operation,'tags':['reading']} for i in range(5)])
        pol['policy_components']['selector']['normalized']['bootstrap_preference_basis'].update(source=self.j.dynamic._source_ref(preference),body_digest=digest(p),clock_profile_digest=self.f['clock_profile']['digest'])
        policy=pub(self.j.append_event(self.j.dynamic._event_for(pol,self.issuer),self.issuer))
        frontier=self.scoped('home-preference-write-frontier-1')
        frontier.update(owner_id=p['owner_id'],subject=p['subject'],preference_set_id=p['preference_set_id'],context_set_id=ctx['context_set_id'],head_ref=prefref,head_projection_digest=digest(preference_unchanged_projection(p)),context_ref=contextref,authority_ref=authorityref)
        child=frontier['frontier_component'];child.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest']);child['normalized'].update(scope=self.identity,head_ref={'set_id':p['preference_set_id'],'revision':p['revision'],'digest':digest(p)})
        child['normalized']['authority_ref'].update(source=authorityref,body_digest=digest(authority['payload']['authority_component']['normalized']),clock_profile_digest=self.f['clock_profile']['digest'])
        frontierpub=pub(self.j.append_event(self.j.dynamic._event_for(frontier,self.issuer),self.issuer))
        w=self.scoped('home-preference-write-1')
        child=pol['policy_components'][policy_owner];selector='acknowledged_model_policy' if policy_owner=='model' else 'selector_memory_policy'
        policyref=self.j.dynamic._source_ref(policy,{'selector':selector,'id':child['id'],'schema':child['schema'],'version':child['version'],'digest':digest(child)})
        w.update(id='write-default',producer_epoch='owner-1',actor_ref='synthetic-owner',owner_id=p['owner_id'],subject=p['subject'],person_ref=p['beneficiary']['person_ref'],target=p['target'],preference_set_id=p['preference_set_id'],operation=operation,reservation_id=child['normalized']['reservations'][0]['id'],candidate_ref={'id':'authenticated-candidate','digest':'0'*64},undo_effect_ref=None,learning_basis=None,
                 expected_predecessor=prefref,context_ref=contextref,authority_ref=authorityref,policy_ref=policyref,frontier_ref=self.j.dynamic._source_ref(frontierpub),desired_lux=p['qualities']['illuminance_band']['desired_lux']-25,deadline=10,unchanged_projection_digest=digest(preference_unchanged_projection(p)))
        if deliver:self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        return p,w
    def test_write_exact_projection_successor_origin_and_lost_ack(self):
        p,w=self.write_fixture();result=self.j.dynamic.preference_write(w,self.owner)
        self.assertEqual(result['application']['payload']['status'],'applied')
        successor=result['successor']['payload']
        self.assertEqual(preference_unchanged_projection(p),preference_unchanged_projection(successor))
        self.assertEqual(successor['qualities']['illuminance_band']['desired_lux'],w['desired_lux'])
        first=result['first_receipt'];self.now=11
        retry=self.j.dynamic.preference_write(w,self.owner)
        self.assertEqual(first,retry['first_receipt']);self.assertEqual(result['successor'],retry['successor']);self.assertEqual(result['application'],retry['application'])
        bad=copy.deepcopy(w);bad['desired_lux']+=1
        with self.assertRaises(JournalError):self.j.dynamic.preference_write(bad,self.owner)
        with self.assertRaises(JournalError) as absent:self.j.dynamic.lookup_origin(self.owner,self.identity['site_id'],self.identity['room_id'],event_id='absent')
        self.assertEqual(absent.exception.code,'not_found')

    def test_selected_policy_eligibility_is_genuine_parent_and_first_delivery(self):
        p,w=self.write_fixture(deliver=False)
        result=self.j.dynamic.preference_write(w,self.owner)
        self.assertEqual(result['application']['payload']['status'],'rejected');self.assertEqual(result['application']['payload']['reason'],'learning_basis_unavailable')
        self.assertIsNone(result['successor'])
        # Once this exact parent is genuinely read, a new command may qualify;
        # the first terminal outcome for the earlier ID remains unchanged.
        self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        self.assertEqual(result,self.j.dynamic.preference_write(w,self.owner))
        new=copy.deepcopy(w);new.update(id='write-after-actual-policy-delivery',reservation_id='default-1')
        admitted=self.j.dynamic.preference_write(new,self.owner)
        self.assertEqual(admitted['application']['payload']['status'],'applied')
        parent,policy=self.j.dynamic._source_component(w['policy_ref'],self.owner)
        table=parent['payload']['policy_components']['table'];context=self.j.dynamic._source_component(w['context_ref']['source_ref'],self.owner)[1]
        authority=self.j.dynamic._source_component(w['authority_ref'],self.owner)[1]
        self.assertIsNone(self.j.dynamic._write_learning_reason(w,self.owner,p,context,table,authority,0,policy_parent=parent))
        with self.assertRaises(JournalError):original_family_clocks(table,parent['committed_at'])
    def test_administrative_model_and_selector_use_only_own_reservation(self):
        for owner,operation in (('model','default'),('model','recall'),('selector','default'),('selector','recall')):
            with self.subTest(owner=owner,operation=operation):
                self.use_fixture(copy.deepcopy(self.f))
                p,w=self.write_fixture(policy_owner=owner,operation=operation)
                forged=copy.deepcopy(w);forged.update(id='cross-arm-reservation',reservation_id=(operation+'-1' if owner=='model' else 'model-'+operation+'-1'))
                rejected=self.j.dynamic.preference_write(forged,self.owner)
                self.assertEqual(rejected['application']['payload']['reason'],'reservation_conflict');self.assertIsNone(rejected['successor'])
                for component in (None,{**w['policy_ref']['component'],'selector':'model_policy'},
                                  {**w['policy_ref']['component'],'digest':w['policy_ref']['publication_ref']['payload_digest']}):
                    malformed=copy.deepcopy(w);malformed.update(id='invalid-owned-policy');malformed['policy_ref']['component']=component
                    before=int(self.j._meta('cursor'))
                    with self.assertRaises(JournalError):self.j.dynamic.preference_write(malformed,self.owner)
                    self.assertEqual(int(self.j._meta('cursor')),before)
                admitted=self.j.dynamic.preference_write(w,self.owner)
                self.assertEqual(admitted['application']['payload']['status'],'applied');self.assertEqual(admitted['successor']['payload']['qualities']['illuminance_band']['desired_lux'],w['desired_lux'])

    def test_public_loopback_model_administrative_policy_ownership(self):
        p,w=self.write_fixture(policy_owner='model',operation='default')
        server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=build_opener(ProxyHandler({}));url='http://127.0.0.1:'+str(server.server_port)+PREFIX+'/preference-writes'
            def post(body):
                with opener.open(Request(url,data=canonical(body).encode(),headers={'Authorization':'Bearer test-owner','Content-Type':'application/json'}),timeout=3) as response:return json.load(response)
            wrong=copy.deepcopy(w);wrong.update(id='wrong-selector-reservation',reservation_id='default-1')
            rejected=post(wrong);self.assertEqual(rejected['application']['payload']['reason'],'reservation_conflict')
            parent_only=copy.deepcopy(w);parent_only.update(id='parent-only');parent_only['policy_ref']['component']=None
            before=int(self.j._meta('cursor'))
            with self.assertRaises(HTTPError) as error:post(parent_only)
            self.assertEqual(error.exception.code,409);error.exception.close();self.assertEqual(int(self.j._meta('cursor')),before)
            # Valid reference metadata does not establish original authority:
            # each altered field must fail at the actual authenticated join.
            component=w['policy_ref']['component']
            for key,value in (('selector','table_policy'),('id','other-policy-child'),
                              ('schema','home-lighting-request-4'),('version',component['version']+1),('digest','0'*64)):
                changed=copy.deepcopy(w);changed.update(id='changed-component-'+key);changed['policy_ref']['component'][key]=value
                self.assertTrue(validate_feedback(changed,self.j.schemas)['ok'],key)
                with self.assertRaises(HTTPError) as error:post(changed)
                self.assertEqual(error.exception.code,409);error.exception.close();self.assertEqual(int(self.j._meta('cursor')),before)
            result=post(w);self.assertEqual(result['application']['payload']['status'],'applied');self.assertEqual(result,post(w))
        finally:server.shutdown();server.server_close();thread.join(3)

    def test_write_projection_rejection_is_first_terminal_without_cas(self):
        p,w=self.write_fixture();w['unchanged_projection_digest']='0'*64
        result=self.j.dynamic.preference_write(w,self.owner)
        self.assertEqual(result['application']['payload']['reason'],'projection_mismatch');self.assertIsNone(result['successor'])
        self.assertEqual(self.j.dynamic._revision_store().materialize(0)[-1]['row']['publication']['payload']['revision'],0)

    def test_write_rejects_parent_wrong_policy_role_and_submission_renewal(self):
        p,w=self.write_fixture();parent=copy.deepcopy(w);parent['policy_ref']['component']=None
        cursor=int(self.j._meta('cursor'))
        with self.assertRaises(JournalError):self.j.dynamic.preference_write(parent,self.owner)
        self.assertEqual(int(self.j._meta('cursor')),cursor)
        policy,_=self.j.dynamic._source_component(w['policy_ref'],self.owner)
        table=policy['payload']['policy_components']['table'];wrong=copy.deepcopy(w)
        wrong['policy_ref']=self.j.dynamic._source_ref(policy,{'selector':'table_policy','id':table['id'],'schema':table['schema'],'version':table['version'],'digest':digest(table)})
        with self.assertRaises(JournalError):self.j.dynamic.preference_write(wrong,self.owner)
        self.assertEqual(int(self.j._meta('cursor')),cursor)
        w['deadline']=10.01
        with self.assertRaises(JournalError):self.j.dynamic.preference_write(w,self.owner)
        self.assertEqual(int(self.j._meta('cursor')),cursor)

    def test_static_worker_bindings_cannot_change_after_bootstrap(self):
        c=self.semantic_genesis();c['checkpoint']['static_bindings_digest']='1'*64
        cursor=int(self.j._meta('cursor'))
        with self.assertRaises(JournalError) as reset:self.j.dynamic.worker_commit(c,self.worker)
        self.assertEqual(reset.exception.code,'reset_required');self.assertEqual(int(self.j._meta('cursor')),cursor)

    def test_preference_origin_proof_preserves_exact_wire_and_lost_ack(self):
        p,w=self.write_fixture();before=int(self.j._meta('cursor'))
        with self.assertRaises(JournalError):self.j.dynamic.preference_write(w,self.owner,request_canonical_json=json.dumps(w))
        self.assertEqual(int(self.j._meta('cursor')),before)
        result=self.j.dynamic.preference_write(w,self.owner,request_canonical_json=canonical(w))
        args=dict(event_id=w['id']);site,room=self.identity['site_id'],self.identity['room_id']
        proof=self.j.dynamic.lookup_write_origin_proof(self.owner,site,room,**args)
        self.assertTrue(validate_feedback(proof,self.j.schemas)['ok'])
        self.assertEqual(proof['request_canonical_json'],canonical(w));self.assertEqual(proof['origin_lookup']['command']['payload'],w)
        self.assertEqual(proof['origin_lookup']['application'],result['application'])
        self.now=11;later=self.j.dynamic.lookup_write_origin_proof(self.owner,site,room,**args)
        self.assertEqual(proof['origin_lookup']['first_receipt'],later['origin_lookup']['first_receipt'])
        self.assertEqual(proof['request_canonical_json'],later['request_canonical_json'])
        for path,value in ((('request_canonical_json',),json.dumps(w)),(('origin_lookup','request_digest'),'0'*64),
                           (('origin_lookup','first_receipt','committed_at'),1),(('origin_lookup','application','payload','raw_request_digest'),'0'*64)):
            mutant=copy.deepcopy(later);at=mutant
            for key in path[:-1]:at=at[key]
            at[path[-1]]=value;self.assertFalse(validate_feedback(mutant,self.j.schemas)['ok'],path)
        with self.assertRaises(JournalError):self.j.dynamic.lookup_write_origin_proof(self.issuer,site,room,**args)
        import zlib
        self.j.db.execute('UPDATE feedback_write_originals SET request_blob=?',(zlib.compress(b' ' * 65537),))
        with self.assertRaises(JournalError) as corrupt:self.j.dynamic.lookup_write_origin_proof(self.owner,site,room,**args)
        self.assertEqual(corrupt.exception.code,'corrupt_original')

    def test_closed_execution_feedback_selector_uses_exact_control_child(self):
        # Selector unit fixture only: it is deliberately not a public original.
        child=self.scoped('home-lighting-interval-exposure-component-1')
        p={'payload':{'schema':'home-lighting-control-decision-3','interval_exposures':[child]}}
        selector={'selector':'execution_feedback','id':child['id'],'schema':child['schema'],'version':child['version'],'digest':digest(child)}
        self.assertEqual(self.j.dynamic._raw_component(p,selector),child)
        with self.assertRaises(JournalError):self.j.dynamic._raw_component(p,{**selector,'digest':'0'*64})

    def test_selected_request_lease_resolve_exact_original_children(self):
        # Pure original-component resolution fixture, not a fabricated journal
        # publication, installation, consumer delivery or optical qualification.
        request=json.loads((ROOT/'contracts/home-adaptive-experience/examples/home-lighting-request-4.json').read_text())
        parent={'payload':{'schema':'home-lighting-policy-decision-3','requests':[request]}}
        for selector,body in (('policy_request',request),('policy_request_lease',request['lease'])):
            component={'selector':selector,'id':body['id'],'schema':body['schema'],'version':body['version'],'digest':digest(body)}
            self.assertEqual(self.j.dynamic._raw_component(parent,component),body)
            for key,value in (('selector','policy_request_lease' if selector=='policy_request' else 'policy_request'),
                              ('id','different-id'),('schema','home-preference-snapshot-2'),('version',body['version']+1),('digest','0'*64)):
                with self.subTest(selector=selector,key=key):
                    with self.assertRaises(JournalError) as error:self.j.dynamic._raw_component(parent,{**component,key:value})
                    self.assertEqual(error.exception.code,'invalid_reference')

    def test_learning_projection_binds_normalized_exposure_and_distinct_settings(self):
        # Pure projection fixture, not admitted or optically qualified evidence.
        from unittest.mock import patch
        pref=self.preference();exposure=self.scoped('home-lighting-settled-exposure-component-1')
        exposure.update(person_ref=pref['beneficiary']['person_ref'],target=pref['target'],status='qualified',from_t=1,until=2,available_at=2,
                        goal_ref={'goal_id':'goal-fixture','revision':0,'digest':'0'*64,'source_ref':None},
                        preference_ref={'preference_set_id':pref['preference_set_id'],'revision':0,'digest':digest(pref),'source_ref':None})
        parent={'schema':'home-context-publication-2','id':'control-fixture','producer_id':'worker','producer_epoch':'worker-1','journal_epoch':self.j.stream_epoch,
                'cursor':1,'payload_type':'home-lighting-control-decision-3','payload_version':'3.0','payload_digest':'0'*64,
                'payload':{'interval_exposures':[]}}
        child=self.scoped('home-lighting-interval-exposure-component-1');child.update(person_ref=exposure['person_ref'],target=exposure['target'])
        parent['payload']['interval_exposures']=[child];ref=self.j.dynamic._source_ref(parent);exposure['control_refs']=[ref]
        prefsource={**ref,'publication_id':pref['id'],'payload_type':pref['schema'],'payload_version':'2.0'}
        with patch.object(self.j.dynamic,'_source_component',return_value=(parent,parent['payload'])):
            actual=self.j.dynamic._learning_normalized_basis(exposure,pref,prefsource,self.owner)
        normalized_pref={'kind':'preference_snapshot','scope':self.identity,'id':pref['id'],'set_id':pref['preference_set_id'],'revision':pref['revision'],
                         'preference_subject':pref['subject'],**{k:pref[k] for k in ('effective_at','available_at','valid_until')}}
        band=pref['qualities']['illuminance_band']
        common={'dimension':'illumination_lux','min':band['min_lux'],'desired':band['desired_lux'],'max':band['max_lux'],'owner_id':pref['owner_id'],
                'original_source':prefsource,'normalized_row_body_digest':digest(normalized_pref)}
        model={**common,'person_ref':pref['beneficiary']['person_ref'],'target':pref['target'],'immutable_projection_digest':digest(preference_unchanged_projection(pref)),
               'condition_tags':pref['condition_tags'],'rule_valid_until':pref['rule_valid_until']}
        self.assertEqual(actual['table_setting_digest'],digest(common));self.assertEqual(actual['model_setting_digest'],digest(model))
        self.assertNotEqual(actual['model_setting_digest'],actual['table_setting_digest']);self.assertNotEqual(actual['exposure_digest'],digest(exposure))
        self.assertEqual(actual['control_refs'][0]['component']['digest'],digest(child))
    def test_canonical_bootstrap_exact_retry_and_bounded_original_recovery(self):
        import zlib
        c=self.bootstrap();wire=canonical(c);r=self.j.dynamic.worker_commit(c,self.worker,request_canonical_json=wire)
        self.assertEqual(r['raw_request_digest'],hashlib.sha256(wire.encode()).hexdigest())
        self.assertEqual(r['commit_seq'],1)
        self.now=2;self.assertEqual(r,self.j.dynamic.worker_commit(c,self.worker,request_canonical_json=wire))
        saved,text,sha=self.j.dynamic._commit_original('worker','worker-1','bootstrap')
        self.assertEqual((saved,text,sha),(c,wire,r['raw_request_digest']))
        with self.assertRaises(JournalError):self.j.dynamic.worker_commit(c,self.worker,request_canonical_json=json.dumps(c))
        for corrupt in (zlib.compress(b' '*262145),zlib.compress(wire.encode())+b'junk',b'not-zlib'):
            self.j.db.execute('UPDATE feedback_worker_commit_originals SET request_blob=?',(corrupt,))
            with self.assertRaises(JournalError) as raised:self.j.dynamic._commit_original('worker','worker-1','bootstrap')
            self.assertEqual(raised.exception.code,'corrupt_original')
    def test_native_preference_cas_raw_epoch_and_lost_ack(self):
        p=self.preference();r=self.j.dynamic.declaration(p,self.owner,family=p['schema'])
        pub=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(r['cursor'],)).fetchone()[0])
        self.assertEqual(pub['producer_epoch'],'owner-1');self.assertEqual(len(pub),13)
        self.now=3;self.assertEqual(r,self.j.dynamic.declaration(p,self.owner,family=p['schema']))
        changed=copy.deepcopy(p);changed['qualities']['illuminance_band']['desired_lux']+=1
        with self.assertRaises(JournalError):self.j.dynamic.declaration(changed,self.owner,family=changed['schema'])
        p1=copy.deepcopy(p);p1.update(id='new-preference',revision=1,expected_predecessor={'revision':0,'digest':digest(p)},issued_at=3,effective_at=3,available_at=3)
        self.j.dynamic.declaration(p1,self.owner,family=p1['schema'])
        self.assertEqual(self.j.dynamic._revision_store().materialize(3)[0]['row']['publication']['payload']['id'],'new-preference')
    def test_immutable_issuer_binding(self):
        b=self.f['actor_bindings'][0];event=self.j.dynamic._event_for(b,self.issuer)
        r=self.j.append_event(event,self.issuer)
        bad=copy.deepcopy(b);bad.update(id='forged-binding',actor_ref='someone-else');bad['binding_digest']=digest({k:v for k,v in bad.items() if k!='binding_digest'})
        with self.assertRaises(JournalError):self.j.append_event(self.j.dynamic._event_for(bad,self.issuer),self.issuer)
        self.assertEqual(int(self.j._meta('cursor')),r['cursor'])

    def test_configuration_write_authority_joins_configured_feedback_actor_origin(self):
        from adaptive_feedback_validation import validate_configuration
        keys=('identity','clock_profile','materials','registered_surfaces','config','config_digest','producer_epochs','actor_bindings','classifier_policy','write_authorities')
        baseline={k:copy.deepcopy(self.f[k]) for k in keys};self.assertIs(validate_configuration(baseline,self.j.schemas),baseline)
        for origin,writer,normalized_only in (({'producer_id':'issuer','producer_epoch':'issuer-1'},None,False),
                                             ({'producer_id':'foreign','producer_epoch':'foreign-1'},None,False),
                                             (None,{'producer_id':'issuer','producer_epoch':'issuer-1'},False),
                                             ({'producer_id':'issuer','producer_epoch':'issuer-1'},None,True)):
            value=copy.deepcopy(baseline);authority=value['write_authorities'][0]
            if origin is not None:
                if not normalized_only:authority['feedback_origin']=origin
                authority['authority_component']['normalized']['feedback_origin']=origin
            if writer is not None:
                authority['writer_origin']=writer;authority['authority_component']['normalized']['writer_origin']=writer
            value['config']['feedback_config_digest']=digest({k:value[k] for k in ('producer_epochs','actor_bindings','classifier_policy','write_authorities')});value['config_digest']=digest(value['config'])
            with self.assertRaises(JournalError) as error:validate_configuration(value,self.j.schemas)
            self.assertEqual(error.exception.code,'feedback_authority_configuration')

    def test_learning_origin_joins_actual_binding_and_exact_application_origin(self):
        binding=self.f['actor_bindings'][0];receipt=self.j.append_event(self.j.dynamic._event_for(binding,self.issuer),self.issuer)
        original=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(receipt['cursor'],)).fetchone()[0])
        rule=binding['allowed_bindings'][0];authority=self.f['write_authorities'][0]
        feedback={'id':'origin-label','actor_ref':binding['actor_ref'],'person_ref':binding['person_ref'],'reported_at':0,
                  **{k:rule[k] for k in ('subject','beneficiary','target','association_generation')},'actor_binding_ref':self.j.dynamic._source_ref(original)}
        # Exact authenticated-label join helper fixture, not an admitted
        # feedback/learner body or a fabricated end-to-end application.
        publication={'producer_id':'owner','producer_epoch':'owner-1'};app={'origin':{**publication,'event_id':feedback['id']}}
        check=self.j.dynamic._feedback_learning_origin_supported
        self.assertTrue(check(publication,feedback,app,authority,self.owner))
        for changed in ({**publication,'producer_id':'issuer','producer_epoch':'issuer-1'}, {**publication,'producer_epoch':'owner-2'}):
            self.assertFalse(check(changed,feedback,{'origin':{**changed,'event_id':feedback['id']}},authority,self.owner))
        self.assertFalse(check(publication,feedback,{'origin':{**publication,'event_id':'other-label'}},authority,self.owner))
        self.assertFalse(check(publication,{**feedback,'association_generation':1},app,authority,self.owner))

    def test_global_qualification_context_selects_current_chain_before_disposition(self):
        preference,write=self.write_fixture();original,context=self.j.dynamic._source_component(write['context_ref']['source_ref'],self.owner)
        target={k:context[k] for k in ('beneficiary','target','association_generation')};row={'publication':original,'received_at':0}
        select=self.j.dynamic._qualification_context_original
        self.assertIsNone(preference['condition_tags']);self.assertEqual(select([row],preference,target,0),original)
        conditional={**preference,'condition_tags':context['tags'],'context_set_id':context['context_set_id']}
        self.assertEqual(select([row],conditional,target,0),original)
        self.assertIsNone(select([row],{**conditional,'condition_tags':['another-context']},target,0))
        duplicate=copy.deepcopy(original);duplicate['cursor']+=100
        for ordered in ([row,{'publication':duplicate,'received_at':0}],[{'publication':duplicate,'received_at':0},row]):
            self.assertEqual(select(ordered,preference,target,0),original,'identical original bodies select the lowest cursor independently of row order')
        newer=copy.deepcopy(context);newer.update(id='context-ended',revision=1,expected_predecessor={'revision':0,'digest':digest(context)},status='ended',issued_at=.2,effective_at=.2,available_at=.2)
        for child in newer['context_components'].values():
            child['id']+=':next';child['normalized'].update(id=child['id'],effective_at=.2,available_at=.2)
        self.now=.2;receipt=self.j.dynamic.declaration(newer,self.owner,family=newer['schema'])
        closed=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(receipt['cursor'],)).fetchone()[0]);rows=[row,{'publication':closed,'received_at':.2}]
        self.assertEqual(select(rows,preference,target,.1),original,'future delivery cannot retire eligible context')
        self.assertIsNone(select(rows,preference,target,.2),'new ended head cannot revive older active context')
        # Pure selector adversaries supplement the real admitted ended head;
        # they are not forged journal admissions or complete worker commits.
        for field,value in (('status','active'),('preference_set_id','foreign-set'),('valid_until',.1)):
            changed=copy.deepcopy(closed)
            if field=='status':changed['payload']['target']={'kind':'registered_region','object_ref':None,'registration_ref':'other'}
            changed['payload'][field]=value;changed['payload_digest']=digest(changed['payload'])
            self.assertIsNone(select([row,{'publication':changed,'received_at':.2}],preference,target,.2))
        conflict=copy.deepcopy(original);conflict['payload']['tags']=['different'];conflict['payload_digest']=digest(conflict['payload'])
        self.assertIsNone(select([row,{'publication':conflict,'received_at':0}],preference,target,0))
        another=copy.deepcopy(original);another['payload'].update(context_set_id='another-chain');another['payload_digest']=digest(another['payload'])
        self.assertIsNone(select([row,{'publication':another,'received_at':0}],preference,target,0),'two qualifying declaration chains are ambiguous')
        # Direct proof-guard fixture only: no invented publication/ACK or
        # optical qualification. Future support reaches original resolution;
        # half-open expired support must refuse before that resolver is called.
        class OriginalResolutionRequested(RuntimeError):pass
        t=.2;receiver={**target,'id':'guard-benefit','role':'benefit','request_id':'guard-request','fences':{}}
        q={**target,'target_id':receiver['id'],'authority_fences':{},'goal_ref':{}}
        staged={'basis':{},'digest':digest({}),'target_id':receiver['id']}
        held={'installed_at':t,'targets':[receiver],'requests':[{'id':'guard-request','lease':{},'preference_refs':[{}]}],
              'support_bounds':[{'target_id':receiver['id'],'valid_until':t}],
              'qualification_rows':[{'target_id':receiver['id'],'basis':q,'digest':digest(q)}],'installation_rows':[staged]}
        proof={**self.identity,'input_cut':{'as_of_t':t,'input_cursor':int(self.j._meta('cursor')),'receipts':[]},
               'checkpoint':{'last_t':t,'acknowledged_installed':None,'pipeline':{'exposure_context':{},
                   'feedback_exposure':{'held_context':held,'acknowledged_boundaries':[],'completed_intervals':[]}}},
               'outputs':[{'schema':'home-lighting-control-decision-3','installed_state':{'t':t},'interval_proof':{'assessments':[]}}]}
        def set_bound(value):
            held['support_bounds'][0]['valid_until']=value
            proof['checkpoint']['pipeline']['exposure_context']={k:v for k,v in held.items() if k not in {'qualification_rows','installation_rows'}}
        called=[];actual_resolver=self.j.dynamic._received_original
        def resolver(*args,**kwargs):called.append(True);raise OriginalResolutionRequested('original authentication remains required')
        self.j.dynamic._received_original=resolver
        try:
            for until in (t-.01,t):
                set_bound(until)
                with self.assertRaisesRegex(JournalError,'support'):self.j.dynamic._feedback_context_proofs(proof,self.worker,None)
                self.assertEqual(called,[],'expired staged Q must not enter original resolution')
            set_bound(t+.01)
            with self.assertRaises(OriginalResolutionRequested):self.j.dynamic._feedback_context_proofs(proof,self.worker,None)
            self.assertEqual(len(called),1,'future support still requires the authentic original resolver')
            held['qualification_rows']=[];held['installation_rows']=[];set_bound(t)
            self.j.dynamic._feedback_context_proofs(proof,self.worker,None)
            self.assertEqual(len(called),1,'expired receiver context without new Q remains representable')
        finally:self.j.dynamic._received_original=actual_resolver

    def test_qualification_context_cannot_omit_newer_received_original(self):
        preference,write=self.write_fixture();original,context=self.j.dynamic._source_component(write['context_ref']['source_ref'],self.owner)
        target={k:context[k] for k in ('beneficiary','target','association_generation')}
        # Real service admission of an explicit bootstrap contract fixture.
        # Its empty revision subset cannot redefine the authentic receipt head.
        bootstrap=self.bootstrap();cut=self.j.dynamic.input_cut(self.worker,self.identity['site_id'],self.identity['room_id'],consumer_id='worker',after=0,t=0)
        def claims(fence,t):
            return [{'ref':self.j.dynamic._source_ref(p)['publication_ref'],'received_at':t,'eligible_at':self.j.dynamic._eligible(p,t)} for p in fence['publications']]
        bootstrap['input_cut'].update(cut_id=cut['cut_id'],input_cursor=cut['cut_cursor'],receipts=claims(cut,0),eligible_refs=[self.j.dynamic._source_ref(p)['publication_ref'] for p in cut['publications']])
        bootstrap['checkpoint']['input_cursor']=cut['cut_cursor'];bootstrap['input_cut']['digest']=digest({k:v for k,v in bootstrap['input_cut'].items() if k!='digest'})
        self.j.dynamic.worker_commit(bootstrap,self.worker)
        self.assertEqual(bootstrap['checkpoint']['revisions']['rows'],[])
        rows=self.j.dynamic._qualification_context_rows(bootstrap,self.worker)
        self.assertEqual(self.j.dynamic._qualification_context_original(rows,preference,target,0),original)
        newer=copy.deepcopy(context);newer.update(id='context-ended-received',revision=1,expected_predecessor={'revision':0,'digest':digest(context)},status='ended',issued_at=.2,effective_at=.2,available_at=.2)
        for child in newer['context_components'].values():
            child['id']+=':received';child['normalized'].update(id=child['id'],effective_at=.2,available_at=.2)
        self.now=.2;self.j.dynamic.declaration(newer,self.owner,family=newer['schema'])
        cut=self.j.dynamic.input_cut(self.worker,self.identity['site_id'],self.identity['room_id'],consumer_id='worker',after=bootstrap['checkpoint']['input_cursor'],t=.2)
        claim={'site_id':self.identity['site_id'],'room_id':self.identity['room_id'],'input_cut':{'input_cursor':cut['cut_cursor'],'receipts':claims(cut,.2)},'checkpoint':{'revisions':{'rows':[]}}}
        rows=self.j.dynamic._qualification_context_rows(claim,self.worker)
        self.assertEqual(len(rows),2,'complete context originals survive an omitted checkpoint subset')
        self.assertEqual(self.j.dynamic._qualification_context_original(rows,preference,target,.1),original)
        self.assertIsNone(self.j.dynamic._qualification_context_original(rows,preference,target,.2))

    def test_current_target_heads_use_genuine_revision_cas_before_disposition(self):
        self._exercise_current_target_heads(public=False)
        for status in ('active','suspended','ended','expired'):
            with self.subTest(goal_head=status):self._exercise_current_target_heads(public=False,goal_status=status)
        self._exercise_current_target_heads(public=False,conditional=True)

    def test_public_loopback_current_target_heads_use_genuine_revision_cas(self):
        self._exercise_current_target_heads(public=True)
        self._exercise_current_target_heads(public=True,conditional=True)

    def _exercise_current_target_heads(self,public,goal_status=None,conditional=False):
        """Real declarations/cuts and semantic goal admission, then the target
        admission seam. This is not a full positive optical/control run."""
        self.use_fixture(copy.deepcopy(self.f),worker_goals=True)
        server=None;thread=None
        if public:
            server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            opener=build_opener(ProxyHandler({}));base='http://127.0.0.1:'+str(server.server_port)+PREFIX
        def post(path,body,token):
            if not public:
                if path=='worker-commits':return self.j.dynamic.worker_commit(body,self.worker)
                return self.j.dynamic.declaration(body,self.owner,family=body['schema'])
            request=Request(base+'/'+path,data=canonical(body).encode(),headers={'Authorization':'Bearer test-'+token,'Content-Type':'application/json'})
            with opener.open(request,timeout=3) as response:return json.load(response)
        def fence():
            if not public:return self.j.dynamic.input_cut(self.worker,self.identity['site_id'],self.identity['room_id'],consumer_id='worker',after=0,t=self.now)
            query=urlencode({**{k:self.identity[k] for k in ('site_id','room_id')},'consumer_id':'worker','after':0,'t':self.now})
            with opener.open(Request(base+'/feedback-input-cut?'+query,headers={'Authorization':'Bearer test-worker'}),timeout=3) as response:return json.load(response)
        def original(receipt):return json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(receipt['cursor'],)).fetchone()[0])
        def ref(p):return {k:p[k] for k in ('journal_epoch','cursor','payload_digest')}
        def component(p,*,staged=False):return {'component_id':p['id'],'component_digest':p['payload_digest'],'publication_ref':None if staged else ref(p)}
        observed_first={}
        def first(p):
            saved=self.j.db.execute('SELECT received_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?',('worker','worker',p['cursor'])).fetchone()
            return saved[0] if saved else observed_first.setdefault(p['cursor'],self.now)
        def claims(cut):return [{'ref':ref(p),'received_at':first(p),'eligible_at':self.j.dynamic._eligible(p,first(p))} for p in cut['publications']]
        try:
            semantic=self.semantic_genesis()
            preference=self.preference()
            if conditional:preference.update(condition_tags=['reading'],context_set_id='context:book-1')
            pref=original(post('preference-snapshots',preference,'owner'))
            context=None;context_pub=None
            if conditional:
                context=self.scoped('home-experience-context-declaration-1')
                context.update(owner_id=preference['owner_id'],subject=preference['subject'],preference_set_id=preference['preference_set_id'],context_set_id='context:book-1',actor_ref='synthetic-owner',beneficiary=preference['beneficiary'],target=preference['target'],tags=['reading'],valid_until=2)
                for child in context['context_components'].values():
                    child.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'])
                    child['normalized'].update(scope=self.identity,owner_id=preference['owner_id'],chain_id=context['context_set_id'],preference_subject=preference['subject'],set_id=preference['preference_set_id'],tags=['reading'])
                context['context_components']['model']['normalized'].update(actor_ref='synthetic-owner',person_ref=preference['beneficiary']['person_ref'],target=preference['target'])
                context_pub=original(post('experience-contexts',context,'owner'))
            geometry=self.geometry();gp=original(self.j.append_event(self.j.dynamic._event_for(geometry,self.issuer),self.issuer))
            capability=copy.deepcopy(next(p for p in self.f['rows'] if p['schema']=='home-lighting-capability-1'))
            cap=original(self.j.append_event(self.j.dynamic._event_for(capability,self.issuer),self.issuer))
            obj=geometry['objects'][0];material=self.f['materials'][0];authority=self.j.dynamic.authority(self.identity['site_id'],self.identity['room_id'])
            goal=json.loads((ROOT/'contracts/home-adaptive-experience/examples/home-experience-goal-1.json').read_text())
            goal.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'],id='current-goal:0',owner_id='worker',subject=preference['subject'],goal_id='goal:worker:current',beneficiary=preference['beneficiary'],target=preference['target'],association_generation=0,preference_refs=[component(pref)])
            request=json.loads((ROOT/'contracts/home-adaptive-experience/examples/home-lighting-request-4.json').read_text())
            request.update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'],id='current-request',goal_ref={'component_id':goal['id'],'component_digest':digest(goal),'publication_ref':None},preference_refs=[component(pref)],capability_refs=[component(cap)],beneficiary=goal['beneficiary'],target=goal['target'],association_generation=0,valid_until=2)
            fences=self.j.dynamic._fences(authority,obj['person_ref'],obj['object_ref'],0,material['source_id'],material['registration_ref'])
            request['lease'].update(**self.identity,clock_profile_digest=self.f['clock_profile']['digest'],person_ref=obj['person_ref'],object_ref=obj['object_ref'],association_generation=0,fences=fences,valid_until=2)
            raw=fence();cut=semantic['input_cut'];cut.update(cut_id=raw['cut_id'],input_cursor=raw['cut_cursor'],authority=raw['authority'],receipts=claims(raw),eligible_refs=[ref(p) for p in raw['publications']]);cut['digest']=digest({k:v for k,v in cut.items() if k!='digest'})
            cp=semantic['checkpoint'];cp.update(input_cursor=raw['cut_cursor'],authority=authority,goal_heads=[goal],goal_revision_count=1)
            cp['revisions']['rows']=[{'publication':p,'received_at':0} for p in ([pref,context_pub] if conditional else [pref])]
            semantic['expected_authority']=authority
            policy=next(p for p in semantic['outputs'] if p['schema']=='home-lighting-policy-decision-3');policy['requests']=[request]
            if conditional:policy['consumed_refs']=[component(context_pub)]
            cp['pipeline']['policy']=policy;cp['pipeline']['leases']=[request['lease']]
            installed=next(p for p in semantic['outputs'] if p['schema']=='home-lighting-installed-boundary-1');installed['input_cut_digest']=cut['digest']
            cp['pending_installed']['payload_digest']=digest(installed);semantic['outputs'].append(goal)
            if goal_status:
                successor=copy.deepcopy(goal);successor.update(id='current-goal:1',revision=1,expected_predecessor={'revision':0,'digest':digest(goal)},status='active' if goal_status=='expired' else goal_status)
                if goal_status=='expired':successor['valid_until']=.1
                semantic['outputs'].append(successor);cp['goal_heads']=[successor];cp['goal_revision_count']=2
            post('worker-commits',semantic,'worker')
            gp_goal=json.loads(self.j.db.execute("SELECT body FROM publications WHERE payload_type='home-experience-goal-1' ORDER BY cursor LIMIT 1").fetchone()[0])
            self.assertEqual(gp_goal['producer_id'],'worker');self.assertEqual(gp_goal['payload'],goal)
            midpoint=[sum(p['centre_m'][i] for p in obj['patches'])/2 for i in range(3)]
            target={**{k:request[k] for k in ('goal_ref','beneficiary','target','association_generation','role','objectives')},'request_id':request['id'],
                    'geometry_ref':{**ref(gp),'component_id':obj['object_ref']['oid']},'patches':obj['patches'],'uncertainty':obj['uncertainty'],
                    'source_id':material['source_id'],'registration_ref':material['registration_ref'],'uncertainty_correlation_ref':material['uncertainty_correlation_ref'],
                    'transform_origin_m':midpoint,'aim_anchor_m':midpoint,'fences':fences,'status':'resolved','environment_refs':[]}
            output={'geometry_snapshot':{'object_heads':[{'object_ref':obj['object_ref'],'estimate':obj,'status':'fresh','estimate_ref':target['geometry_ref']}]}}
            proof={**self.identity,'input_cut':{},'checkpoint':{'pipeline':{'policy':policy},'authority':authority,'revisions':{'rows':[]},'dependency_receipts':[],'committed_output_inputs':[]},'outputs':[]}
            def refresh():
                raw=fence();proof['input_cut'].update(journal_epoch=raw['journal_epoch'],input_cursor=raw['cut_cursor'],as_of_t=self.now,receipts=claims(raw),eligible_refs=[ref(p) for p in raw['publications'] if self.j.dynamic._eligible(p,self.now)<=self.now])
                proof['checkpoint']['revisions']['rows']=[{'publication':p,'received_at':first(p)} for p in raw['publications'] if p['payload_type'] in {'home-experience-goal-1','home-preference-snapshot-2'}]
            self.now=.2;refresh()
            if goal_status:
                with self.assertRaisesRegex(JournalError,'current.*head'):self.j.dynamic._target(target,output,proof,self.worker)
                self.assertEqual(self.j.dynamic._component(request['goal_ref'],proof,self.worker,historical=True),goal)
                return
            self.j.dynamic._target(target,output,proof,self.worker)
            pending=copy.deepcopy(context if conditional else preference)
            pending.update(id='current-context:1' if conditional else 'current-preference:1',revision=1,expected_predecessor={'revision':0,'digest':digest(context if conditional else preference)},issued_at=.2,effective_at=.4,available_at=.4)
            if conditional:
                pending['valid_until']=.5
                for child in pending['context_components'].values():
                    child['id']+=':next';child['normalized'].update(id=child['id'],effective_at=.4,available_at=.4,valid_until=.5)
            else:pending['qualities']['illuminance_band']['desired_lux']-=1
            current=original(post('experience-contexts' if conditional else 'preference-snapshots',pending,'owner'))
            refresh();self.j.dynamic._target(target,output,proof,self.worker)
            self.now=.4;refresh()
            before=[self.j.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('publications','receipts','feedback_worker_first_receipts')]
            with self.assertRaisesRegex(JournalError,'current.*head'):self.j.dynamic._target(target,output,proof,self.worker)
            self.assertEqual(before,[self.j.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('publications','receipts','feedback_worker_first_receipts')])
            # Historical exact-original resolution remains usable; it cannot
            # confer current support or rewrite the sealed semantic policy.
            self.assertEqual(self.j.dynamic._component(component(pref),proof,self.worker,historical=True),preference)
            self.assertEqual(policy['requests'][0]['preference_refs'],[component(pref)])
            if conditional:
                # Even a compatible successor with a shorter TTL cannot replace
                # the context original sealed into the old policy's read-set.
                policy['consumed_refs']=[component(current)]
                self.j.dynamic._current_request_heads(request,proof,self.worker)
                for field,value in (('association_generation',1),('beneficiary',{**pending['beneficiary'],'person_ref':{**pending['beneficiary']['person_ref'],'track_epoch':'replacement-person'}})):
                    prior=pending;pending=copy.deepcopy(prior)
                    pending.update(id='context-incompatible:'+field,revision=prior['revision']+1,expected_predecessor={'revision':prior['revision'],'digest':digest(prior)},valid_until=.8)
                    pending[field]=value
                    for child in pending['context_components'].values():
                        child['id']+=':incompatible';child['normalized'].update(id=child['id'],valid_until=.8)
                    pending['context_components']['model']['normalized']['person_ref']=pending['beneficiary']['person_ref']
                    current=original(post('experience-contexts',pending,'owner'));refresh()
                    policy['consumed_refs']=[component(current)]
                    with self.assertRaisesRegex(JournalError,'current.*head'):self.j.dynamic._current_request_heads(request,proof,self.worker)
            else:
                for status in ('suspended','ended','expired'):
                    prior=pending;pending=copy.deepcopy(prior)
                    pending.update(id='current-preference:'+status,revision=prior['revision']+1,expected_predecessor={'revision':prior['revision'],'digest':digest(prior)},status='active' if status=='expired' else status)
                    if status=='expired':pending['valid_until']=.5
                    current=original(post('preference-snapshots',pending,'owner'));refresh()
                    live=copy.deepcopy(request);live['preference_refs']=[component(current)]
                    if status=='expired':self.j.dynamic._current_request_heads(live,proof,self.worker);self.now=.5;refresh()
                    with self.assertRaisesRegex(JournalError,'current.*head'):self.j.dynamic._current_request_heads(live,proof,self.worker)
        finally:
            if server:server.shutdown();server.server_close();thread.join(3)

    def test_public_loopback_feedback_wrong_and_unconfigured_origin_cannot_admit(self):
        binding=self.f['actor_bindings'][0];receipt=self.j.append_event(self.j.dynamic._event_for(binding,self.issuer),self.issuer)
        original=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(receipt['cursor'],)).fetchone()[0])
        body=self.scoped('home-experience-feedback-1');rule=binding['allowed_bindings'][0]
        body.update(actor_ref=binding['actor_ref'],person_ref=binding['person_ref'],actor_binding_ref=self.j.dynamic._source_ref(original),**{k:rule[k] for k in ('subject','beneficiary','target','association_generation')})
        body['preference_refs'][0]['preference_set_id']=rule['preference_set_id']
        self.assertTrue(validate_feedback(body,self.j.schemas)['ok'])
        server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=build_opener(ProxyHandler({}));url='http://127.0.0.1:'+str(server.server_port)+PREFIX+'/experience-feedback';before=int(self.j._meta('cursor'))
            for token in ('test-issuer','not-configured-origin'):
                with self.assertRaises(HTTPError) as error:
                    opener.open(Request(url,data=canonical(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}),timeout=3)
                self.assertEqual(error.exception.code,403 if token=='test-issuer' else 401)
                reason=json.loads(error.exception.read());error.exception.close()
                if token=='test-issuer':self.assertIn('binding',reason['error']['message'])
                self.assertEqual(int(self.j._meta('cursor')),before)
        finally:server.shutdown();server.server_close();thread.join(3)
    def test_consumer_lookup_does_not_create_or_refresh_receipt(self):
        p=self.preference();r=self.j.dynamic.declaration(p,self.owner,family=p['schema'])
        site,room=self.identity['site_id'],self.identity['room_id']
        before=self.j.dynamic.lookup_original(self.owner,site,room,cursor=r['cursor'],consumer_id='owner')
        self.assertIsNone(before['receipt'])
        self.j.page(self.owner,site,room,consumer_id='owner')
        first=self.j.dynamic.lookup_original(self.owner,site,room,cursor=r['cursor'],consumer_id='owner')['receipt']
        self.now=2;self.j.page(self.owner,site,room,consumer_id='owner')
        self.assertEqual(first,self.j.dynamic.lookup_original(self.owner,site,room,cursor=r['cursor'],consumer_id='owner')['receipt'])
        with self.assertRaises(JournalError):self.j.dynamic.lookup_original(self.owner,site,room,cursor=r['cursor'],consumer_id='worker')

    def test_read_cut_read_only_actual_receipts_and_immutable_history(self):
        p=self.preference();r=self.j.dynamic.declaration(p,self.owner,family=p['schema'])
        site,room=self.identity['site_id'],self.identity['room_id']
        captured=self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertEqual(captured['snapshot']['cursor'],r['cursor'])
        self.assertEqual(captured['as_of_t'],0);self.assertTrue(validate_feedback(captured)['ok'])
        self.assertEqual([row['publication_ref']['cursor'] for row in captured['receipts']],[r['cursor']])
        first=captured['receipts'][0]['consumer_receipt']
        self.assertEqual(first['received_at'],0);self.assertEqual(first['eligible_at'],0)
        self.assertEqual(captured,self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner'))
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM feedback_read_cuts').fetchone()[0],1)
        self.now=1
        later=self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertEqual(later['receipts'],captured['receipts']);self.assertNotEqual(later['cut_id'],captured['cut_id'])
        self.assertEqual(captured,self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner',cut_id=captured['cut_id']))
        with self.assertRaises(JournalError) as other:
            self.j.dynamic.read_cut(self.worker,site,room,consumer_id='worker',cut_id=captured['cut_id'])
        self.assertEqual(other.exception.code,'unknown_cut')
        with self.assertRaises(JournalError):self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='worker')
        # A read-lane cut cannot satisfy the authenticated worker installation fence.
        commit=self.bootstrap();commit['input_cut']['cut_id']=captured['cut_id']
        commit['input_cut']['digest']=digest({k:v for k,v in commit['input_cut'].items() if k!='digest'})
        with self.assertRaises(JournalError) as rejected:self.j.dynamic._validate_cut(commit,self.worker,0)
        self.assertEqual(rejected.exception.code,'incomplete_cut')
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM workers').fetchone()[0],0)

    def test_read_cut_pending_revision_originals_recover_without_early_heads(self):
        p,w=self.write_fixture();site,room=self.identity['site_id'],self.identity['room_id']
        def publish(body,grant):
            receipt=self.j.dynamic.declaration(body,grant,family=body['schema'])
            return json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(receipt['cursor'],)).fetchone()[0])
        next_preference=copy.deepcopy(p);next_preference.update(id='preference-future',revision=1,expected_predecessor={'revision':0,'digest':digest(p)},issued_at=0,effective_at=40,available_at=0,valid_until=120)
        pref=publish(next_preference,self.owner)
        old_context,_=self.j.dynamic._source_component(w['context_ref']['source_ref'],self.owner)
        context=copy.deepcopy(old_context['payload']);context.update(id='context-future',revision=1,expected_predecessor={'revision':0,'digest':digest(old_context['payload'])},issued_at=0,effective_at=40,available_at=0)
        for child in context['context_components'].values():child['normalized']['effective_at']=40
        ctx=publish(context,self.owner)
        captured=self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertIn(pref,captured['snapshot']['items']);self.assertIn(ctx,captured['snapshot']['items'])
        self.assertNotIn(pref['cursor'],[h['publication_ref']['cursor'] for h in captured['snapshot']['revision_heads']])
        self.assertEqual(captured['snapshot']['applicable_preference_refs'],[w['expected_predecessor']['source_ref']])
        pending={r['publication_ref']['cursor']:r['consumer_receipt'] for r in captured['receipts']}
        self.assertEqual(pending[pref['cursor']]['received_at'],0);self.assertEqual(pending[pref['cursor']]['eligible_at'],40)
        from adaptive_feedback_validation import RevisionStore
        recovered=RevisionStore(identity=self.identity,clock_profile=self.f['clock_profile'])
        for original in captured['snapshot']['items']:
            if original['payload_type'] in {'home-preference-snapshot-2','home-experience-context-declaration-1','home-experience-goal-1','home-goal-declaration-1'}:
                recovered.admit({'publication':original},self.j.dynamic._original_authorization({'publication':original}))
        self.assertEqual(recovered.materialize(40),self.j.dynamic._revision_store().materialize(40))
        self.now=40
        later=self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertIn(pref['cursor'],[h['publication_ref']['cursor'] for h in later['snapshot']['revision_heads']])
        later_receipts={r['publication_ref']['cursor']:r['consumer_receipt'] for r in later['receipts']}
        self.assertEqual(later_receipts[pref['cursor']],pending[pref['cursor']])

    def test_read_cut_pending_and_response_overflow_are_atomic(self):
        site,room=self.identity['site_id'],self.identity['room_id']
        p=self.preference();self.j.dynamic.declaration(p,self.owner,family=p['schema'])
        g=self.geometry(0,available=.4)
        self.j.append_event(self.j.dynamic._event_for(g,self.issuer),self.issuer)
        tables=('dynamic_snapshot_masks','dynamic_deliveries','feedback_consumer_receipts','feedback_read_cuts')
        before={name:self.j.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in tables}
        with self.assertRaises(JournalError) as pending:self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertEqual(pending.exception.code,'snapshot_pending_inputs')
        self.assertEqual(before,{name:self.j.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in tables})
        self.now=.4;saved=self.j.limits
        self.j.limits=Limits(**{**saved.__dict__,'max_response_bytes':1024})
        with self.assertRaises(JournalError):self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertEqual(before,{name:self.j.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in tables})
        self.j.limits=saved
        captured=self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertEqual(captured['as_of_t'],.4)
        self.assertEqual([h['observed_at'] for h in captured['snapshot']['component_heads']],[0])

    def test_read_cut_mirror_rejects_meta_order_clock_and_digest_mutants(self):
        self.j.dynamic.declaration(self.preference(),self.owner,family='home-preference-snapshot-2')
        g=self.geometry();self.j.append_event(self.j.dynamic._event_for(g,self.issuer),self.issuer)
        cut=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        mutants=[]
        for key,value in (('journal_epoch','other'),('input_cursor',cut['input_cursor']+1),('as_of_t',.2),('consumer_id','other')):
            m=copy.deepcopy(cut);m[key]=value;mutants.append(m)
        m=copy.deepcopy(cut);m['receipts'].reverse();mutants.append(m)
        m=copy.deepcopy(cut);m['receipts'][0]['consumer_receipt']['received_at']=.1;mutants.append(m)
        m=copy.deepcopy(cut);m['receipts'][0]['publication_ref']['payload_digest']='1'*64;mutants.append(m)
        for m in mutants:
            m['digest']=digest({k:v for k,v in m.items() if k!='digest'})
            self.assertFalse(validate_feedback(m)['ok'])
        cut['digest']='1'*64;self.assertFalse(validate_feedback(cut)['ok'])

    def test_read_cut_and_worker_cuts_share_closed_reservation(self):
        site,room=self.identity['site_id'],self.identity['room_id']
        # Private fence-budget fixture, not purported publication or installation proof.
        for n in range(1024):
            self.j.db.execute('INSERT INTO dynamic_cuts VALUES(?,?,?,?,?,?,?,?,?,?)',
                              ('budget:'+str(n),'fixture','fixture',site,room,0,0,0,'{}',0))
        with self.assertRaises(JournalError) as read:self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        self.assertEqual(read.exception.code,'cut_budget')
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM feedback_read_cuts').fetchone()[0],0)
        self.j.db.execute("DELETE FROM dynamic_cuts WHERE cut_id='budget:0'")
        self.j.dynamic.read_cut(self.owner_read,site,room,consumer_id='owner')
        with self.assertRaises(JournalError) as worker:
            self.j.dynamic.input_cut(self.worker,site,room,consumer_id='worker',after=0,t=0)
        self.assertEqual(worker.exception.code,'cut_budget')

    def test_public_loopback_read_cut_route_is_separate_and_fixed_consumer(self):
        self.j.dynamic.declaration(self.preference(),self.owner,family='home-preference-snapshot-2')
        server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=build_opener(ProxyHandler({}));base='http://127.0.0.1:'+str(server.server_port)+PREFIX
            def get(path,params):
                with opener.open(Request(base+path+'?'+urlencode(params),headers={'Authorization':'Bearer test-owner-read'}),timeout=3) as response:return json.load(response)
            query={k:self.identity[k] for k in ('site_id','room_id')};query['consumer_id']='owner'
            cut=get('/feedback-read-cut',query);self.assertTrue(validate_feedback(cut)['ok'])
            initial=self.preference();future=copy.deepcopy(initial)
            future.update(id='public-future-preference',revision=1,expected_predecessor={'revision':0,'digest':digest(initial)},effective_at=.4)
            with opener.open(Request(base+'/preference-snapshots',data=canonical(future).encode(),
                                     headers={'Authorization':'Bearer test-owner','Content-Type':'application/json'}),timeout=3) as response:
                admitted=json.load(response)
            pending=get('/feedback-read-cut',query)
            self.assertEqual(pending['snapshot']['revision_heads'][0]['revision'],0)
            future_receipt=next(r['consumer_receipt'] for r in pending['receipts'] if r['publication_ref']['cursor']==admitted['cursor'])
            self.assertEqual(future_receipt['received_at'],0);self.assertEqual(future_receipt['eligible_at'],.4)
            self.now=.4;eligible=get('/feedback-read-cut',query)
            self.assertEqual(eligible['snapshot']['revision_heads'][0]['revision'],1)
            self.assertEqual(future_receipt,next(r['consumer_receipt'] for r in eligible['receipts'] if r['publication_ref']['cursor']==admitted['cursor']))
            self.now=2
            self.assertEqual(cut,get('/feedback-read-cuts/'+quote(cut['cut_id'],safe=''),query))
            for params in ({**query,'t':'0'},{**query,'after':'0'},{**query,'consumer_id':'worker'}):
                with self.assertRaises(HTTPError) as invalid:get('/feedback-read-cut',params)
                self.assertEqual(invalid.exception.code,403 if params.get('consumer_id')=='worker' else 400)
                invalid.exception.close()
            self.assertEqual(self.j.db.execute('SELECT count(*) FROM dynamic_cuts').fetchone()[0],0)
        finally:server.shutdown();server.server_close();thread.join(3)
    def test_public_loopback_native_preference(self):
        server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=build_opener(ProxyHandler({}));url='http://127.0.0.1:'+str(server.server_port)+PREFIX+'/preference-snapshots'
            request=Request(url,data=json.dumps(self.preference()).encode(),headers={'Authorization':'Bearer test-owner','Content-Type':'application/json'})
            with opener.open(request,timeout=3) as response:r=json.load(response)
            self.assertEqual(r['schema'],'home-context-receipt-2')
        finally:server.shutdown();server.server_close();thread.join(3)

    def geometry(self,t=0,state='open',available=None):
        """Author native geometry4 from the same trusted numeric recipe."""
        g=copy.deepcopy(next(x for x in self.f['rows'] if x['schema']=='home-geometry-3'))
        g.update(schema='home-geometry-4',version=4,clock_profile_digest=self.f['clock_profile']['digest'],id='geometry:'+str(t)+':'+state,
                 observed_at=t,available_at=t if available is None else available,valid_until=min(120,t+1))
        g['capture'].update(capture_seq=int(t*100)+1,observed_at=t)
        for obj in g['objects']:obj['state']=state
        return g
    def evidence(self,g,event_id='evidence:1'):
        r=self.scoped('home-evidence-request-3');o=g['objects'][0]
        r.update(id=event_id,person_ref=o['person_ref'],object_ref=o['object_ref'],available_at=self.now,deadline=self.now+1,
                 precision_m=.1,orientation_rad=.2)
        return r

    def test_full_object_identity_heads_preserve_epochs_without_cached_fallback(self):
        f=copy.deepcopy(self.f);material=copy.deepcopy(f['materials'][0]);material['object_ref']['object_epoch']='object-epoch-2'
        material['uncertainty_correlation_ref']='object:'+digest(material['object_ref']);f['materials'].append(material)
        self.use_fixture(f)
        first=self.geometry();old=self.j.append_event(self.j.dynamic._event_for(first,self.issuer),self.issuer)
        batch=copy.deepcopy(next(x for x in f['rows'] if x['schema']=='home-fact-batch-2'))
        batch.update(schema='home-fact-batch-3',version=3,clock_profile_digest=f['clock_profile']['digest'],id='facts-old')
        self.j.append_event(self.j.dynamic._event_for(batch,self.issuer),self.issuer)
        self.now=.2;second=self.geometry(.2);second['objects'][0]['object_ref']=material['object_ref']
        new=self.j.append_event(self.j.dynamic._event_for(second,self.issuer),self.issuer)
        batch2=copy.deepcopy(batch);batch2.update(id='facts-new',observed_at=.2,available_at=.2,valid_until=1.2)
        for fact in batch2['facts']:
            fact.update(id=fact['id']+':epoch-2',object_ref=material['object_ref'],observed_at=.2,available_at=.2,valid_until=1.2)
            fact['capture'].update(observed_at=.2,capture_seq=21)
        self.j.append_event(self.j.dynamic._event_for(batch2,self.issuer),self.issuer)
        cut=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        heads=cut['snapshot']['component_heads']
        geometry=[h for h in heads if h['payload_type']=='home-geometry-4']
        self.assertEqual({canonical(h['object_ref']) for h in geometry},{canonical(first['objects'][0]['object_ref']),canonical(material['object_ref'])})
        self.assertEqual({h['publication_ref']['cursor'] for h in geometry},{old['cursor'],new['cursor']})
        self.assertEqual(len([h for h in heads if h['payload_type']=='home-fact-batch-3']),2*len(batch['facts']))
        for ref in (first['objects'][0]['object_ref'],material['object_ref']):
            self.assertTrue(any(h['object_ref']==ref for h in cut['snapshot']['authority']['follows']))
        request=self.evidence(first)
        self.assertIsNone(self.j.dynamic._cached_estimate(request),'retained old epoch cannot override current raw-oid support')
        current=self.evidence(second,'evidence-current-epoch')
        self.assertEqual(self.j.dynamic._cached_estimate(current)['cursor'],new['cursor'])

    def _concurrent_snapshot_fixture(self):
        f=copy.deepcopy(self.f);binding=copy.deepcopy(f['actor_bindings'][0]);binding.update(id='binding-second')
        binding['person_ref']['track_epoch']='person-track-2'
        binding['allowed_bindings'][0]['beneficiary']['person_ref']=binding['person_ref']
        binding['allowed_bindings'][0]['preference_set_id']='reading:person1:book-2'
        binding['binding_digest']=digest({k:v for k,v in binding.items() if k!='binding_digest'});f['actor_bindings'].append(binding)
        authority=copy.deepcopy(f['write_authorities'][0]);authority.update(id='authority-second',preference_set_id='reading:person1:book-2',person_ref=binding['person_ref'])
        authority['authority_component']['id']='authority-second:model'
        authority['authority_component']['normalized'].update(id='authority-second:model',set_id=authority['preference_set_id'],chain_id=authority['preference_set_id'],person_ref=binding['person_ref'])
        f['write_authorities'].append(authority);self.use_fixture(f,extra_chains=['reading:person1:book-2'])
        bodies=f['actor_bindings']+f['write_authorities']
        env=copy.deepcopy(next(x for x in f['rows'] if x['schema']=='home-lighting-environment-1'))
        env2=copy.deepcopy(env);env2.update(id='environment-second-source',source_id='scripted-camera')
        bodies.extend([env,env2])
        for i in range(2):
            row=self.scoped('home-experience-classifier-disposition-1');row['id']='classifier:'+str(i)
            row['normalized'].update(id=row['id'],scope=self.identity,clock_profile_digest=f['clock_profile']['digest'])
            row['normalized']['feedback_identity']['id']='feedback:'+str(i)
            bodies.append(row)
        return bodies

    def test_public_loopback_snapshot_preserves_concurrent_actor_source_authority_and_decisions(self):
        """Actual public service admission/read; authored diagnostic, no optics claim."""
        bodies=self._concurrent_snapshot_fixture()
        server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=build_opener(ProxyHandler({}));base='http://127.0.0.1:'+str(server.server_port)+PREFIX
            for body in bodies:
                event=self.j.dynamic._event_for(body,self.issuer)
                request=Request(base+'/events',data=canonical(event).encode(),headers={'Authorization':'Bearer test-issuer','Content-Type':'application/json'})
                with opener.open(request,timeout=3) as response:self.assertEqual(json.load(response)['schema'],'home-context-receipt-2')
            query=urlencode({**{k:self.identity[k] for k in ('site_id','room_id')},'consumer_id':'owner'})
            with opener.open(Request(base+'/feedback-read-cut?'+query,headers={'Authorization':'Bearer test-owner-read'}),timeout=3) as response:cut=json.load(response)
            self.assertTrue(validate_feedback(cut,self.j.schemas)['ok'])
            for family in ('home-experience-actor-binding-1','home-preference-write-authority-1','home-lighting-environment-1','home-experience-classifier-disposition-1'):
                self.assertEqual(len([p for p in cut['snapshot']['items'] if p['payload_type']==family]),2,family)
            self.assertEqual(len([h for h in cut['snapshot']['family_heads'] if h['payload_type']=='home-lighting-environment-1']),2)
            self.assertEqual(len(cut['snapshot']['write_heads']),2)
            self.assertTrue(all(h['disposition']=='active' and h['source_ref']['component'] is None for h in cut['snapshot']['write_heads']))
            self.assertTrue(all(r['consumer_receipt']['received_at']==0 for r in cut['receipts']))
        finally:server.shutdown();server.server_close();thread.join(3)

    def test_authority_head_rank_preserves_closed_expired_current_original(self):
        f=copy.deepcopy(self.f);new=copy.deepcopy(f['write_authorities'][0]);new.update(id='authority-new',status='revoked',issued_at=.2,effective_at=.2,available_at=.2,valid_until=.3)
        new['authority_component']['id']='authority-new:model';new['authority_component']['normalized'].update(id='authority-new:model',status='revoked',effective_at=.2,available_at=.2,valid_until=.3)
        f['write_authorities'].append(new);self.use_fixture(f)
        old=self.j.append_event(self.j.dynamic._event_for(f['write_authorities'][0],self.issuer),self.issuer)
        self.now=.2;current=self.j.append_event(self.j.dynamic._event_for(new,self.issuer),self.issuer)
        self.now=.4
        cut=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        selected=[p for p in cut['snapshot']['items'] if p['payload_type']=='home-preference-write-authority-1']
        self.assertEqual([p['cursor'] for p in selected],[current['cursor']]);self.assertNotEqual(current['cursor'],old['cursor'])
        self.assertEqual(selected[0]['payload']['status'],'revoked');self.assertLess(selected[0]['payload']['valid_until'],cut['as_of_t'])
        self.assertEqual(self.j.dynamic._write_family_head(new['schema'],new,.4),selected[0])
        head=cut['snapshot']['write_heads'][0]
        self.assertEqual(head['source_ref'],self.j.dynamic._source_ref(selected[0]));self.assertEqual(head['eligible_at'],.2);self.assertEqual(head['disposition'],'revoked')
        revoke=self.scoped('home-experience-producer-revocation-1')
        revoke.update(id='revoke-authority-publisher',target_producer_id='issuer',target_producer_epoch='issuer-1',revoked=True,issued_at=.4,effective_at=.4,available_at=.4,actor_ref='synthetic-owner')
        self.j.dynamic.producer_revoke(revoke,self.issuer)
        unavailable=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        self.assertEqual(unavailable['snapshot']['write_heads'][0]['disposition'],'unavailable')
        self.assertEqual(unavailable['snapshot']['write_heads'][0]['source_ref'],head['source_ref'])
        self.assertEqual(cut,self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner',cut_id=cut['cut_id']))

    def test_versioned_snapshot_write_heads_join_and_reject_older_transports(self):
        p,w=self.write_fixture();cut=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        snapshot=cut['snapshot'];self.assertEqual(snapshot['schema'],'home-context-snapshot-5');self.assertEqual(cut['schema'],'home-context-feedback-read-cut-2')
        self.assertEqual([h['payload_type'] for h in snapshot['write_heads']],['home-preference-write-authority-1','home-preference-write-frontier-1'])
        self.assertTrue(validate_feedback(snapshot,self.j.schemas)['ok'])
        for name,version in (('home-context-snapshot-4',4),('home-context-feedback-read-cut-1',1)):
            old=copy.deepcopy(snapshot if 'snapshot' in name else cut);old.update(schema=name,version=version)
            if 'snapshot' in name:old.pop('write_heads')
            else:old['digest']=digest({k:v for k,v in old.items() if k!='digest'})
            self.assertFalse(validate_feedback(old,self.j.schemas)['ok'])
        mutations=[]
        for field,value in (('eligible_at',.1),('disposition','expired'),('preference_set_id','foreign-chain'),('valid_until',119)):
            mutant=copy.deepcopy(snapshot);mutant['write_heads'][0][field]=value;mutations.append(mutant)
        missing=copy.deepcopy(snapshot);missing['items']=[x for x in missing['items'] if x['cursor']!=missing['write_heads'][0]['source_ref']['publication_ref']['cursor']];mutations.append(missing)
        duplicate=copy.deepcopy(snapshot);duplicate['write_heads'].append(copy.deepcopy(duplicate['write_heads'][0]));mutations.append(duplicate)
        wrong_order=copy.deepcopy(snapshot);wrong_order['write_heads'].reverse();mutations.append(wrong_order)
        for mutant in mutations:self.assertFalse(validate_feedback(mutant,self.j.schemas)['ok'])
        from dataclasses import replace
        foreign_auth=[json.loads(x) for x in self.owner_read.adaptive_subjects]
        for auth in foreign_auth:
            for rule in auth['subject_rights']:rule['chain_ids']=['foreign-chain']
        foreign=replace(self.owner_read,adaptive_subjects=tuple(canonical(x) for x in foreign_auth))
        self.now=.1
        private=self.j.dynamic.read_cut(foreign,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        self.assertEqual(private['snapshot']['write_heads'],[])
        self.assertFalse(any(x['payload_type'] in {'home-preference-write-authority-1','home-preference-write-frontier-1'} for x in private['snapshot']['items']))

    def test_snapshot_write_heads_cover_exact_current_before_expiry_or_publisher_status(self):
        f=copy.deepcopy(self.f);new=copy.deepcopy(f['write_authorities'][0])
        new.update(id='authority-expiring',issued_at=.2,effective_at=.2,available_at=.2,valid_until=.3)
        new['authority_component']['id']='authority-expiring:model'
        new['authority_component']['normalized'].update(id='authority-expiring:model',effective_at=.2,available_at=.2,valid_until=.3)
        f['write_authorities'].append(new);self.use_fixture(f)
        old_receipt=self.j.append_event(self.j.dynamic._event_for(f['write_authorities'][0],self.issuer),self.issuer)
        old=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(old_receipt['cursor'],)).fetchone()[0])
        self.now=.2;current=self.j.append_event(self.j.dynamic._event_for(new,self.issuer),self.issuer)
        self.now=.4
        cut=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        snapshot=copy.deepcopy(cut['snapshot']);snapshot['items'].append(old);snapshot['items'].sort(key=lambda p:p['cursor'])
        head=snapshot['write_heads'][0]
        self.assertEqual(head['source_ref']['publication_ref']['cursor'],current['cursor']);self.assertEqual(head['disposition'],'expired')
        self.assertTrue(validate_feedback(snapshot,self.j.schemas)['ok'],'old reference-only parent must not displace current expired head')
        missing=copy.deepcopy(snapshot);missing['write_heads']=[]
        self.assertIn('write_head_coverage',validate_feedback(missing,self.j.schemas)['errors'])
        fallback=copy.deepcopy(snapshot);fallback['write_heads'][0].update(source_ref=self.j.dynamic._source_ref(old),eligible_at=0,valid_until=old['payload']['valid_until'],disposition='active')
        self.assertIn('write_head_original_binding',validate_feedback(fallback,self.j.schemas)['errors'])
        unavailable=copy.deepcopy(snapshot)
        next(p for p in unavailable['authority']['producers'] if p['producer_id']=='issuer' and p['producer_epoch']=='issuer-1')['revoked']=True
        unavailable['write_heads'][0]['disposition']='unavailable'
        self.assertTrue(validate_feedback(unavailable,self.j.schemas)['ok'])
        invalid=copy.deepcopy(snapshot);selected=next(p for p in invalid['items'] if p['cursor']==current['cursor']);selected['id']='not-original-id'
        self.assertFalse(validate_feedback(invalid,self.j.schemas)['ok'])

    def test_read_cut_header_normalized_clocks_and_availability_only_families(self):
        self.now=.2
        classifier=self.scoped('home-experience-classifier-disposition-1');classifier['normalized'].update(scope=self.identity,clock_profile_digest=self.f['clock_profile']['digest'],as_of=.1,available_at=.2)
        selector=self.scoped('home-preference-selector-memory-policy-1');selector['normalized'].update(scope=self.identity,effective_at=.1,available_at=.2)
        selector['normalized']['bootstrap_preference_basis']['clock_profile_digest']=self.f['clock_profile']['digest']
        receipts=[]
        for body in (classifier,selector):receipts.append(self.j.append_event(self.j.dynamic._event_for(body,self.issuer),self.issuer))
        self.j.evidence_router='synthetic_stub';request=self.evidence(self.geometry());self.j.request_evidence(request,self.owner)
        cut=self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        self.assertTrue(validate_feedback(cut,self.j.schemas)['ok'])
        for p in cut['snapshot']['items']:
            first=next(x['consumer_receipt'] for x in cut['receipts'] if x['publication_ref']['cursor']==p['cursor'])
            self.assertEqual(first['eligible_at'],max(.2,*original_family_clocks(p['payload'],p['committed_at'])))
        for family in ('home-evidence-request-3','home-acquisition-status-2'):
            self.assertTrue(any(p['payload_type']==family for p in cut['snapshot']['items']))
        with self.assertRaises(JournalError):original_family_clocks({'schema':'no-authored-clock'},0)

    def test_classifier_policy_basis_uses_exact_normalization_and_first_delivery(self):
        policy=self.f['classifier_policy'];receipt=self.j.append_event(self.j.dynamic._event_for(policy,self.issuer),self.issuer)
        original=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(receipt['cursor'],)).fetchone()[0])
        self.j.dynamic.read_cut(self.owner_read,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        normalized={'kind':'classifier_policy','scope':self.identity,'id':policy['id'],'version':policy['policy_version'],**{k:policy[k] for k in ('allowed_dimensions','allowed_context_keys','illumination_lux_bounds','max_feedback_age_s','max_exposure_age_s','max_exposure_span_s','allow_modeled_candidates','effective_at','available_at','valid_until')}}
        basis={'source':self.j.dynamic._source_ref(original),'body_digest':digest(normalized),'clock_profile_digest':self.f['clock_profile']['digest'],'committed_at':0,'received_at':0}
        row={'policy_digest':digest(policy),'policy_normalized_digest':digest(normalized),'basis_refs':[basis]}
        self.assertTrue(self.j.dynamic._classifier_policy_basis_supported(row,self.owner,0))
        for changed in ({**row,'policy_normalized_digest':digest(policy)}, {**row,'basis_refs':[]}, {**row,'basis_refs':[basis,basis]}, {**row,'basis_refs':[{**basis,'received_at':.1}]}, {**row,'basis_refs':[{**basis,'body_digest':digest(policy)}]}):
            self.assertFalse(self.j.dynamic._classifier_policy_basis_supported(changed,self.owner,.2))
        self.assertFalse(self.j.dynamic._classifier_policy_basis_supported(row,self.worker,.2),'another consumer cannot inherit owner delivery')

    def test_classifier_limits_are_original_decision_age_span_and_modeled_policy(self):
        f=copy.deepcopy(self.f);f['classifier_policy']['allow_modeled_candidates']=True;self.use_fixture(f)
        feedback={'dimension':'illumination_lux','reported_at':2,'available_at':2.5}
        exposure={'from_t':1,'until':2,'assessment_kind':'modeled'}
        check=self.j.dynamic._classifier_limits_supported
        self.assertTrue(check(feedback,exposure,5.5),'processing delay uses original availability, not reported or current lookup time')
        self.assertFalse(check(feedback,exposure,5.50001))
        self.assertFalse(check(feedback,{**exposure,'from_t':.99},3))
        self.assertFalse(check({**feedback,'reported_at':5.01,'available_at':5.01},exposure,5.01))
        f=copy.deepcopy(self.f);f['classifier_policy']['allow_modeled_candidates']=False;self.use_fixture(f)
        self.assertFalse(self.j.dynamic._classifier_limits_supported(feedback,exposure,3))
    def test_native_evidence_current_head_and_exact_retry(self):
        g=self.geometry();gr=self.j.append_event(self.j.dynamic._event_for(g,self.issuer),self.issuer)
        req=self.evidence(g);receipt=self.j.request_evidence(req,self.owner)
        reply=self.j.lookup_request(req['id'],self.owner_read,self.identity['site_id'],self.identity['room_id'])['terminal_reply']
        self.assertEqual(reply['schema'],'home-evidence-reply-3');self.assertEqual(reply['status'],'supported')
        self.assertEqual(reply['estimate_ref']['cursor'],gr['cursor']);self.assertEqual(reply['actual_model_calls'],0)
        self.now=2;self.assertEqual(receipt,self.j.request_evidence(req,self.owner))
        self.assertEqual(reply,self.j.lookup_request(req['id'],self.owner_read,self.identity['site_id'],self.identity['room_id'])['terminal_reply'])
        self.now=.2;closed=self.geometry(.2,'closed');self.j.append_event(self.j.dynamic._event_for(closed,self.issuer),self.issuer)
        other=self.evidence(g,'evidence:closed');self.j.request_evidence(other,self.owner)
        self.assertEqual(self.j.lookup_request(other['id'],self.owner_read,self.identity['site_id'],self.identity['room_id'])['terminal_reply']['status'],'unavailable')
    def test_stub_pending_deadline_terminal(self):
        self.j.evidence_router='synthetic_stub';req=self.evidence(self.geometry())
        self.j.request_evidence(req,self.owner)
        kinds=[x[0] for x in self.j.db.execute('SELECT payload_type FROM publications')]
        self.assertEqual(kinds,['home-evidence-request-3','home-acquisition-status-2'])
        self.now=1
        first=self.j.lookup_request(req['id'],self.owner_read,self.identity['site_id'],self.identity['room_id'])
        self.assertEqual(first['terminal_reply']['status'],'expired')
        self.now=2;self.assertEqual(first,self.j.lookup_request(req['id'],self.owner_read,self.identity['site_id'],self.identity['room_id']))
    def test_ambiguous_service_issuer_refuses_before_admitting_request(self):
        from dataclasses import replace
        self.j.grants=(*self.j.grants,replace(self.owner,payload_types=self.issuer.payload_types,schema_pins=self.issuer.schema_pins))
        req=self.evidence(self.geometry())
        with self.assertRaises(JournalError) as ambiguous:self.j.request_evidence(req,self.owner)
        self.assertEqual(ambiguous.exception.code,'issuer_unavailable')
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM requests').fetchone()[0],0)
        self.assertEqual(int(self.j._meta('cursor')),0)
    def test_future_geometry_snapshot_and_cache_do_not_advance_early(self):
        g=self.geometry();r=self.j.append_event(self.j.dynamic._event_for(g,self.issuer),self.issuer)
        self.now=.2;future=self.geometry(.2,available=.4);self.j.append_event(self.j.dynamic._event_for(future,self.issuer),self.issuer)
        req=self.evidence(g);self.assertEqual(self.j.dynamic._cached_estimate(req)['cursor'],r['cursor'])
        with self.assertRaises(JournalError) as pending:self.j.snapshot(self.owner,self.identity['site_id'],self.identity['room_id'],consumer_id='owner')
        self.assertEqual(pending.exception.code,'snapshot_pending_inputs')
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM feedback_consumer_receipts').fetchone()[0],0)
        self.now=.4;self.assertEqual(self.j.dynamic._cached_estimate(req)['cursor'],r['cursor']+1)

    def test_producer_revoke_preserves_historical_retry_but_fences_new_commands(self):
        p=self.preference();receipt=self.j.dynamic.declaration(p,self.owner,family=p['schema'])
        revoke=self.scoped('home-experience-producer-revocation-1')
        revoke.update(id='revoke-owner',target_producer_id='owner',target_producer_epoch='owner-1',revoked=True,actor_ref='synthetic-owner')
        rr=self.j.dynamic.producer_revoke(revoke,self.issuer)
        self.assertEqual(receipt,self.j.dynamic.declaration(p,self.owner,family=p['schema']))
        p1=copy.deepcopy(p);p1.update(id='new-pref',revision=1,expected_predecessor={'revision':0,'digest':digest(p)})
        with self.assertRaises(JournalError) as revoked:self.j.dynamic.declaration(p1,self.owner,family=p1['schema'])
        self.assertEqual(revoked.exception.code,'producer_revoked');self.assertEqual(int(self.j._meta('cursor')),rr['cursor'])
        self.now=2;self.assertEqual(rr,self.j.dynamic.producer_revoke(revoke,self.issuer))
    def test_independent_source_revocation_fences_cached_geometry(self):
        g=self.geometry();self.j.append_event(self.j.dynamic._event_for(g,self.issuer),self.issuer)
        req=self.evidence(g);self.assertIsNotNone(self.j.dynamic._cached_estimate(req))
        revoke=self.scoped('home-feedback-authority-revocation-1')
        revoke.update(id='revoke-camera',kind='source',source_id='scripted-camera',closed=True,actor_ref='synthetic-owner')
        self.j.dynamic.authority_revoke(revoke,self.issuer)
        self.assertIsNone(self.j.dynamic._cached_estimate(req))
        self.assertTrue(self.j.dynamic.authority(self.identity['site_id'],self.identity['room_id'])['room']['motion_allowed'])
    def test_atomic_capacity_refusal_does_not_leave_origin_cursor_or_receipt(self):
        p=self.preference();before=int(self.j._meta('cursor'))
        for field,value in (('max_publication_bytes',1),('max_db_bytes',65536)):
            saved=self.j.limits;self.j.limits=Limits(**{**saved.__dict__,field:value})
            with self.assertRaises(JournalError) as exhausted:self.j.dynamic.declaration(p,self.owner,family=p['schema'])
            self.assertEqual(exhausted.exception.code,'journal_full')
            self.assertEqual(int(self.j._meta('cursor')),before)
            self.assertEqual(self.j.db.execute('SELECT count(*) FROM publications').fetchone()[0],0)
            self.assertIsNone(self.j.dynamic.origins.lookup(self.j.dynamic._auth(self.owner),p['id']))
            self.j.limits=saved
        self.assertEqual(self.j.dynamic.declaration(p,self.owner,family=p['schema'])['cursor'],before+1)
    def test_later_eligible_frontier_fences_old_without_future_displacement(self):
        p,w=self.write_fixture();original,_=self.j.dynamic._source_component(w['frontier_ref'],self.owner)
        new=copy.deepcopy(original['payload']);new.update(id='frontier-next',sampled_at=.2,issued_at=0,effective_at=.4,available_at=.4)
        self.j.append_event(self.j.dynamic._event_for(new,self.issuer),self.issuer)
        self.now=.2;self.assertEqual(self.j.dynamic._write_family_head(new['schema'],w,self.now),original)
        self.now=.4
        result=self.j.dynamic.preference_write(w,self.owner)
        self.assertEqual(result['application']['payload']['reason'],'predecessor_conflict');self.assertIsNone(result['successor'])

    def test_actual_native_worker_bootstrap_semantic_control_and_restore(self):
        """Actual Node worker + actual journal ACK; no fabricated receipt."""
        self._exercise_native_worker()

    def test_public_loopback_actual_native_worker_canonical_proof_and_restart(self):
        """Actual Node worker through bounded literal-loopback HTTP admission."""
        self._exercise_native_worker(public=True)

    def test_reference_checkpoint_originals_and_history_are_atomic(self):
        self._exercise_reference_rejections()

    def test_actual_two_phase_worker_handoff_recovery_and_atomic_rejections(self):
        self._exercise_phase_handoff(public=False)

    def test_public_loopback_two_phase_worker_handoff_recovery_and_atomic_rejections(self):
        self._exercise_phase_handoff(public=True)

    def _exercise_phase_handoff(self,public):
        checked=[]
        def inspect(commit,index,request):
            if index not in (6,7):return
            def reject(body,label,structural=True):
                if structural:self.j.schemas.validate(body)
                else:self.assertFalse(validate_feedback(body,self.j.schemas)['ok'])
                before={name:self.j.db.execute('SELECT count(*) FROM '+name).fetchone()[0]
                        for name in ('publications','receipts','feedback_worker_commit_originals','feedback_worker_first_receipts')}
                stored=self.j.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',('worker',)).fetchone()[0]
                if public:
                    with self.assertRaises(HTTPError) as failure:request('/worker-commits',body)
                    self.assertIn(failure.exception.code,(400,409));failure.exception.close()
                else:
                    with self.assertRaises(JournalError):self.j.dynamic.worker_commit(body,self.worker)
                self.assertEqual(before,{name:self.j.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in before},label)
                self.assertEqual(stored,self.j.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',('worker',)).fetchone()[0],label)
                checked.append(label)
            if index==6:
                cp=commit['checkpoint'];self.assertEqual(commit['kind'],'semantic');self.assertEqual(cp['boundary_phase'],'control_pending')
                old=json.loads(self.j.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',('worker',)).fetchone()[0])
                for key in ('t','commands','fixtures','exposure_context','reservations','frozen_heads','intent_ids'):
                    self.assertEqual(cp['pipeline'][key],old['pipeline'][key])
                self.assertEqual(cp['pipeline']['feedback_exposure']['held_context'],old['pipeline']['feedback_exposure']['held_context'])
                self.assertGreaterEqual(len(cp['pipeline']['feedback_exposure']['completed_intervals']),len(old['pipeline']['feedback_exposure']['completed_intervals']))
                bad=copy.deepcopy(commit);bad['kind']='combined';reject(bad,'combined cannot install through semantic publication',structural=False)
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['fixtures'][0]['level']=.1;reject(bad,'semantic cannot advance fixture mechanics')
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['commands'][0]['level']=.1;reject(bad,'semantic cannot replace installed command')
                bad=copy.deepcopy(commit);bad['checkpoint']['runtime_duration_s']=3;reject(bad,'duration immutable after bootstrap')
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['feedback_exposure']['acknowledged_boundaries'][0]['first_received_at']=1
                reject(bad,'history append cannot refresh authentic FIRST')
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['feedback_exposure']['acknowledged_boundaries']=[]
                reject(bad,'semantic cannot silently discard authentic history')
            else:
                self.assertEqual(commit['kind'],'control');self.assertEqual(commit['checkpoint']['boundary_phase'],'ready')
                held=commit['checkpoint']['pipeline']['feedback_exposure']['held_context']
                self.assertEqual(held['qualification_rows'] if held else [],[])
                bad=copy.deepcopy(commit);cp=bad['checkpoint'];wanted=digest(cp['pipeline']['policy'])
                removed=[r for r in cp['referenced_outputs'] if r['source_ref']['payload_type']=='home-lighting-policy-decision-3' and r['source_ref']['publication_ref']['payload_digest']==wanted]
                self.assertEqual(len(removed),1)
                cursor=removed[0]['source_ref']['publication_ref']['cursor']
                cp['referenced_outputs']=[r for r in cp['referenced_outputs'] if r['source_ref']['publication_ref']['cursor']!=cursor]
                cp['dependency_receipts']=[r for r in cp['dependency_receipts'] if r['source_ref']['publication_ref']['cursor']!=cursor]
                reject(bad,'empty qualification still requires delivered current policy')
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['policy']['id']='rebound-policy';reject(bad,'control cannot substitute a policy')
                bad=copy.deepcopy(commit);bad['checkpoint']['last_installed_t']=.8;reject(bad,'control must install its due boundary once')
        self._exercise_native_worker(public=public,inspect_commit=inspect,handoff=True)
        self.assertEqual(len(checked),9)

    def test_public_loopback_reference_checkpoint_originals_and_history_are_atomic(self):
        self._exercise_reference_rejections(public=True)

    def test_prior_reference_recovery_dialects_are_not_current_admission(self):
        for current,prior,version in (
                ('home-context-worker-checkpoint-7','home-context-worker-checkpoint-6',6),
                ('home-context-worker-commit-7','home-context-worker-commit-6',6),
                ('home-context-replay-8','home-context-replay-7',7),
                ('home-context-worker-output-proof-3','home-context-worker-output-proof-2',2)):
            value=example(current);value.update(schema=prior,version=version)
            self.assertFalse(validate_feedback(value,self.j.schemas)['ok'],prior)

    def _exercise_reference_rejections(self,public=False):
        checked=[]
        def state():
            return (self.j._meta('cursor'),[tuple(r) for r in self.j.db.execute('SELECT * FROM workers')],
                    [tuple(r) for r in self.j.db.execute('SELECT * FROM feedback_worker_first_receipts ORDER BY cursor')],
                    self.j.db.execute('SELECT count(*) FROM receipts').fetchone()[0],
                    self.j.db.execute('SELECT count(*) FROM feedback_worker_commit_originals').fetchone()[0])
        def reject(body,label,request,*,structural=True):
            if structural:self.assertTrue(validate_feedback(body,self.j.schemas)['ok'],label)
            before=state()
            if request:
                with self.assertRaises(HTTPError,msg=label) as failure:request('/worker-commits',body)
                self.assertIn(failure.exception.code,(400,409));failure.exception.close()
            else:
                with self.assertRaises(JournalError,msg=label):self.j.dynamic.worker_commit(body,self.worker)
            self.assertEqual(state(),before,label);checked.append(label)
        def changed_descriptor(commit,change):
            body=copy.deepcopy(commit);cp=body['checkpoint'];d=cp['referenced_outputs'][0]
            cursor=d['source_ref']['publication_ref']['cursor'];change(d)
            d['digest']=digest({k:v for k,v in d.items() if k!='digest'})
            cp['dependency_receipts']=[copy.deepcopy(d) if r['source_ref']['publication_ref']['cursor']==cursor else r for r in cp['dependency_receipts']]
            for r in cp['receipts']:
                if r['ref']['cursor']==cursor:r.update(ref=copy.deepcopy(d['source_ref']['publication_ref']),received_at=d['received_at'],eligible_at=d['eligible_at'])
            return body
        def inspect(commit,index,request):
            if index==2:
                self.assertGreater(len(commit['checkpoint']['referenced_outputs']),0)
                for field,value in (('publication_id','not-the-admitted-output'),('producer_id','issuer'),('producer_epoch','issuer-1')):
                    reject(changed_descriptor(commit,lambda d,k=field,v=value:d['source_ref'].__setitem__(k,v)),field,request)
                reject(changed_descriptor(commit,lambda d:d['source_ref']['publication_ref'].__setitem__('payload_digest','0'*64)),'parent digest',request)
                missing=copy.deepcopy(commit);missing['checkpoint']['referenced_outputs'].pop()
                reject(missing,'missing recovery parent',request,structural=False)
                cursor=commit['checkpoint']['referenced_outputs'][0]['source_ref']['publication_ref']['cursor']
                parent=json.loads(self.j.db.execute('SELECT body FROM publications WHERE cursor=?',(cursor,)).fetchone()[0])
                admitted=self.j.db.execute("SELECT * FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id LIKE 'worker-commit/%' AND json_extract(receipt,'$.commit_seq')=?",
                                           ('worker','worker-1',parent['context_seq'])).fetchone()
                self.assertIsNotNone(admitted)
                self.j.db.execute('DELETE FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?',('worker','worker-1',admitted['event_id']))
                try:reject(commit,'publisher alone lacks atomic admission',request)
                finally:self.j.db.execute('INSERT INTO receipts VALUES('+','.join('?' for _ in admitted)+')',tuple(admitted))
                admission_id=json.loads(admitted['receipt'])['id']
                wire=self.j.db.execute('SELECT * FROM feedback_worker_commit_originals WHERE producer_id=? AND producer_epoch=? AND id=?',('worker','worker-1',admission_id)).fetchone()
                self.assertIsNotNone(wire)
                self.j.db.execute('DELETE FROM feedback_worker_commit_originals WHERE producer_id=? AND producer_epoch=? AND id=?',('worker','worker-1',admission_id))
                try:reject(commit,'receipt alone lacks its exact saved commit',request)
                finally:self.j.db.execute('INSERT INTO feedback_worker_commit_originals VALUES(?,?,?,?,?)',tuple(wire))
                delivered=self.j.db.execute('SELECT * FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?',('worker','worker',cursor)).fetchone()
                self.assertIsNotNone(delivered)
                self.j.db.execute('DELETE FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?',('worker','worker',cursor))
                try:reject(commit,'descriptor alone has no genuine first delivery',request)
                finally:self.j.db.execute('INSERT INTO feedback_consumer_receipts VALUES(?,?,?,?,?,?)',tuple(delivered))
            if index==3:
                reject(changed_descriptor(commit,lambda d:d.update(received_at=commit['checkpoint']['last_t'],eligible_at=commit['checkpoint']['last_t'])),'historical FIRST refresh',request)
                feedback=commit['checkpoint']['pipeline']['feedback_exposure']
                self.assertGreater(len(feedback['acknowledged_boundaries']),0)
                self.assertGreater(len(feedback['completed_intervals']),0)
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['feedback_exposure']['acknowledged_boundaries'][0]['installed_state_digest']='0'*64
                reject(bad,'acknowledged installed state is not the original',request)
                bad=copy.deepcopy(commit);bad['checkpoint']['pipeline']['feedback_exposure']['completed_intervals'][0]['interval']['execution_sample_digest']='0'*64
                reject(bad,'completed interval is not the original',request)
        self._exercise_native_worker(public=public,inspect_commit=inspect)
        self.assertEqual(len(checked),11)

    def _exercise_native_worker(self,public=False,trace=None,inspect_commit=None,handoff=False):
        options={k:copy.deepcopy(self.f[k]) for k in ('identity','clock_profile','materials','registered_surfaces','config','config_digest','producer_epochs','actor_bindings','classifier_policy','write_authorities')}
        options.update(registry=self.f['config']['registry'],profile=self.f['config']['profile'],worker_producer_id='worker',worker_producer_epoch='worker-1',runtime_epoch='test-native-runtime',duration_s=2,subjectBindings=[],source_origins={})
        options['revision_authorizations']=[{**json.loads(binding),'producer_id':g.producer_id,'producer_epoch':g.producer_epoch} for g in self.j.grants for binding in g.adaptive_subjects]
        script="const readline=require('node:readline'),W=require('./tools/home-adaptive-feedback-shadow-worker.cjs');let w;readline.createInterface({input:process.stdin}).on('line',line=>{try{const m=JSON.parse(line);if(m.method==='create'){w=W.createWorker(m.options);process.stdout.write(JSON.stringify({ok:true})+'\\n');}else{const value=w[m.method](...(m.args||[]));process.stdout.write(JSON.stringify({ok:true,value:value??null})+'\\n');}}catch(e){process.stdout.write(JSON.stringify({ok:false,error:String(e.stack||e)})+'\\n');}});"
        child=subprocess.Popen(['node','-e',script],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        server=None;thread=None
        if public:
            server=LocalServer(('127.0.0.1',0),self.j);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            opener=build_opener(ProxyHandler({}));base='http://127.0.0.1:'+str(server.server_port)+PREFIX
            def request(path,body=None):
                req=Request(base+path,data=None if body is None else canonical(body).encode('utf-8'),headers={'Authorization':'Bearer test-worker','Content-Type':'application/json'})
                started=time.perf_counter()
                with opener.open(req,timeout=3) as response:value=json.load(response)
                if trace is not None:trace.setdefault('http_requests',[]).append({'path':path,'request_bytes':0 if body is None else len(canonical(body).encode()),'duration_s':time.perf_counter()-started})
                return value
        def call(method,*args,**extra):
            child.stdin.write(json.dumps({'method':method,'args':args,**extra})+'\n');child.stdin.flush()
            reply=json.loads(child.stdout.readline());self.assertTrue(reply['ok'],reply.get('error'));return reply.get('value')
        def restore_checkpoint(cp):
            originals=[]
            for descriptor in cp['referenced_outputs']:
                cursor=descriptor['source_ref']['publication_ref']['cursor']
                looked=(request('/feedback-originals?'+urlencode({**{k:self.identity[k] for k in ('site_id','room_id')},'cursor':cursor,'consumer_id':'worker'})) if public
                        else self.j.dynamic.lookup_original(self.worker,self.identity['site_id'],self.identity['room_id'],cursor=cursor,consumer_id='worker'))
                self.assertEqual(self.j.dynamic._source_ref(looked['publication']),descriptor['source_ref'])
                self.assertIsNotNone(looked['receipt']);self.assertLessEqual(looked['receipt']['received_at'],descriptor['received_at'])
                originals.append({'publication':looked['publication'],'first_received_at':descriptor['received_at']})
            call('restore',cp,{'original_rows':originals})
        try:
            call('create',options=options);last=0;controls=[];history=[]
            times=(0,0,.2,.4,.6,.8,1,1,1.2,1.4,1.6,1.8,2) if handoff else (0,0,.2,.4)
            for index,t in enumerate(times):
                self.now=t
                cut=(request('/feedback-input-cut?'+urlencode({**{k:self.identity[k] for k in ('site_id','room_id')},'consumer_id':'worker','after':last,'t':t})) if public
                     else self.j.dynamic.input_cut(self.worker,self.identity['site_id'],self.identity['room_id'],consumer_id='worker',after=last,t=t))
                for p in cut['publications']:call('ingest',p,t)
                call('ingestCommittedOutputs',cut.get('committed_outputs',[]),t)
                commit=call('prepareBootstrap' if index==0 else 'prepareBoundary',{'t':t,'cut':cut})
                if inspect_commit:inspect_commit(commit,index,request if public else None)
                if public and index==0:
                    malformed=Request(base+'/worker-commits',data=json.dumps(commit).encode('utf-8'),headers={'Authorization':'Bearer test-worker','Content-Type':'application/json'})
                    with self.assertRaises(HTTPError) as rejected:opener.open(malformed,timeout=3)
                    self.assertEqual(rejected.exception.code,400);rejected.exception.close()
                receipt=request('/worker-commits',commit) if public else self.j.dynamic.worker_commit(commit,self.worker)
                if public:self.assertEqual(receipt,request('/worker-commits',commit))
                call('acknowledge',commit,receipt)
                if handoff and index==6:
                    self.assertEqual(receipt,request('/worker-commits',commit) if public else self.j.dynamic.worker_commit(commit,self.worker))
                    restore_checkpoint(call('snapshot'))
                # ACK has no original delivery. This exact GET does, followed
                # by the worker's distinct actual first processing clock.
                lookup=(request('/feedback-commits/'+quote(commit['id'],safe='')+'?'+urlencode({k:self.identity[k] for k in ('site_id','room_id')})) if public
                        else self.j.dynamic.lookup_commit(commit['id'],self.worker,self.identity['site_id'],self.identity['room_id']))
                self.assertEqual(lookup['receipt'],receipt);call('ingestCommittedOutputs',lookup['publications'],t)
                controls.append(commit);last=commit['checkpoint']['input_cursor']
                history.append(copy.deepcopy({'cut':cut,'t':t,'commit':commit,'receipt':receipt,'outputs':lookup['publications'],'restore_before_delivery':handoff and index==6,'restore_after':index==2 or handoff and index==6}))
                if index==2 or handoff and index==6:
                    cp=call('snapshot');restore_checkpoint(cp)
            cp=call('snapshot');restore_checkpoint(cp)
            self.assertEqual(cp['control_seq'],10 if handoff else 2);self.assertEqual(cp['commit_seq'],13 if handoff else 4)
            self.assertEqual(cp['boundary_phase'],'ready');self.assertEqual(cp['last_installed_t'],cp['last_t'])
            self.assertEqual([c['kind'] for c in controls],['bootstrap','semantic',*(['control']*4),'semantic',*(['control']*6)] if handoff else ['bootstrap','semantic','control','control'])
            if handoff:
                self.assertEqual(cp['semantic_seq'],1);self.assertEqual(cp['last_t'],2)
                with self.assertRaises(JournalError):self.j.dynamic._phase_transition(controls[-1],cp)
            self.assertTrue(all(x['publication']['payload_type'] not in {'home-engagement-state-4','home-lighting-policy-decision-3','home-lighting-settled-exposure-1'}
                                for k in ('retained_inputs','geometry_inputs','pending_inputs') for x in cp[k]))
            genesis=next(p for p in controls[1]['outputs'] if p['schema']=='home-lighting-installed-boundary-1')
            row=self.j.db.execute('SELECT cursor FROM publications WHERE payload_type=? AND json_extract(body,\'$.id\')=?',(genesis['schema'],genesis['id'])).fetchone()
            proof=(request('/feedback-worker-output-proofs?'+urlencode({**{k:self.identity[k] for k in ('site_id','room_id')},'cursor':row[0],'consumer_id':'worker'})) if public
                   else self.j.dynamic.lookup_output_proof(self.worker,self.identity['site_id'],self.identity['room_id'],cursor=row[0],consumer_id='worker'))
            self.assertEqual(proof['consumer_receipt']['received_at'],0);self.assertEqual(proof['worker_receipt']['received_at'],0)
            self.assertTrue(validate_feedback(proof,self.j.schemas)['ok'])
            stored=self.j.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',('worker',)).fetchone()[0]
            self.assertEqual(json.loads(stored),cp)
            self.assertGreater(len(cp['referenced_outputs']),0)
            final_control=next(p for p in history[-1]['outputs'] if p['payload_type']=='home-lighting-control-decision-3')
            compact_proof=(request('/feedback-worker-output-proofs?'+urlencode({**{k:self.identity[k] for k in ('site_id','room_id')},'cursor':final_control['cursor'],'consumer_id':'worker'})) if public
                           else self.j.dynamic.lookup_output_proof(self.worker,self.identity['site_id'],self.identity['room_id'],cursor=final_control['cursor'],consumer_id='worker'))
            self.assertEqual(compact_proof['schema'],'home-context-worker-output-proof-3')
            self.assertEqual(compact_proof['request_canonical_json'],canonical(controls[-1]))
            self.assertEqual(compact_proof['commit'],controls[-1])
            self.assertEqual(compact_proof['publication'],final_control)
            self.assertTrue(validate_feedback(compact_proof,self.j.schemas)['ok'])
            # A fresh actual worker reproduces the exact committed requests
            # from unchanged cuts and genuine original journal receipts.
            # This is deterministic two-control replay, not another service run.
            replay_started=time.perf_counter();call('create',options=options)
            for index,item in enumerate(history):
                for original in item['cut']['publications']:call('ingest',original,item['t'])
                call('ingestCommittedOutputs',item['cut'].get('committed_outputs',[]),item['t'])
                replayed=call('prepareBootstrap' if index==0 else 'prepareBoundary',{'t':item['t'],'cut':item['cut']})
                self.assertEqual(replayed,item['commit']);call('acknowledge',replayed,item['receipt'])
                if item['restore_before_delivery']:restore_checkpoint(call('snapshot'))
                call('ingestCommittedOutputs',item['outputs'],item['t'])
                if item['restore_after']:restore_checkpoint(call('snapshot'))
            self.assertEqual(call('snapshot'),cp)
            if trace is not None:trace.update(replayed_commits=len(history),replayed_controls=cp['control_seq'],replay_s=time.perf_counter()-replay_started,
                                             max_commit_bytes=max(len(canonical(c).encode()) for c in controls),
                                             max_control_bytes=max(len(canonical(o).encode()) for c in controls for o in c['outputs'] if o['schema']=='home-lighting-control-decision-3'))
        finally:
            if child.stdin:child.stdin.close()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
            if child.stdout:child.stdout.close()
            if child.stderr:child.stderr.close()
            if server:server.shutdown();server.server_close();thread.join(3)

if __name__=='__main__':unittest.main()
