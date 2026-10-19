"""Independent blocking-repair checks through the actual public journal/worker.

All source observations, grants and qualification receipts are authored. Node
and Python contact only the temporary loopback HTTP journal. No model runs.
"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from journal import Journal, PREFIX, digest
from live_conservative_test_helpers import conservative_fixture
from live_delivery_test_helpers import delivery_geometry
from live_lifecycle_test_helpers import lifecycle
from live_scene_test_helpers import HUMAN_TOKEN, intent
from live_test_helpers import LiveHttp, MANIFEST, NOW, PROFILE, SCOPE, event, fact, publication_ref, unavailable
from live_worker_test_helpers import ROOT, WORKER_ID, WORKER_TOKEN, WorkerProbe, describe, worker_receipt_resolver


class BlockingAuthorityTests(unittest.TestCase):
    def system(self, *, experimental=True, real_clock=False):
        work=tempfile.TemporaryDirectory(prefix='authored-blocking-authority-', dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.service,self.grants,self.config=conservative_fixture()
        if not experimental:
            self.config['engagement_policy']['allow_experimental']=False
            self.service['manifest']['worker']['engagement_policy']=copy.deepcopy(self.config['engagement_policy'])
            description=describe(self.config)
            self.service['manifest']['worker']['config_digest']=description['config_digest']
            self.service['manifest_ref']['digest']=digest(self.service['manifest'])
            MANIFEST.update(self.service['manifest_ref'])
            self.config.update(manifest_ref=copy.deepcopy(self.service['manifest_ref']),live_manifest=copy.deepcopy(self.service['manifest']))
            for grant in self.grants:
                for binding in grant['live_bindings']:
                    binding['manifest_ref']=copy.deepcopy(self.service['manifest_ref'])
        self.now,self.mono=NOW+.2,10.
        self.heads={}
        self.rebased_at=int(time.time())-1
        def real_receipt(store,key):
            record=worker_receipt_resolver(store,key)
            record['ref']['observed_at']=self.rebased_at
            record['ref']['available_at']=self.rebased_at+.05
            record['observation_root_times']={root:self.rebased_at for root in record['observation_root_times']}
            return record
        self.journal=Journal(Path(work.name)/'authored.sqlite',grants=self.grants,profile=PROFILE,
            live_config=self.service,live_receipt_resolver=real_receipt if real_clock else worker_receipt_resolver,
            clock=time.time if real_clock else lambda:self.now,
            live_wall_clock=time.time if real_clock else lambda:self.now,
            live_monotonic=time.monotonic if real_clock else lambda:self.mono)
        self.addCleanup(self.journal.close)
        self.http=LiveHttp(self.journal);self.addCleanup(self.http.close)
        self.config['service_url']=f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe=None

    def start(self):
        self.probe=WorkerProbe();self.addCleanup(self.probe.close)
        self.probe.command('start',config=self.config,t=self.now)

    def tick(self, *, prepare=False):
        if prepare:self.probe.command('fault',mode='before_commit')
        result=self.probe.command('tick',t=self.now)
        if prepare:
            self.assertTrue(result['commit_posts'],result)
            return json.loads(result['commit_posts'][-1])
        self.assertEqual(len(result['decisions']),1,result)
        return result['decisions'][0]

    def advance(self, seconds=1):
        self.now+=seconds;self.mono+=seconds

    def publish(self,payload):
        key=json.dumps(payload['head']['key'],sort_keys=True)
        if key in self.heads:payload['head']['supersedes']=publication_ref(self.heads[key])
        status,saved=self.http.request('POST',PREFIX+'/events',event(payload))
        self.assertEqual(status,200,saved)
        self.heads[key]=saved
        return saved

    def post_commit(self,value):
        return self.http.request('POST',PREFIX+'/worker-commits',value,token=WORKER_TOKEN)

    def proof(self,seq,basis,original,disposition='use_ended_qualified'):
        proof=lifecycle(seq,[publication_ref(basis)],disposition)
        for key in ('observation_root_times','sensor_root_bindings'):
            proof[key].update(copy.deepcopy(original[key]))
        proof['observation_roots']=sorted(proof['observation_root_times'])
        proof['sensor_roots']=sorted({s for sensors in proof['sensor_root_bindings'].values() for s in sensors})
        return proof

    @staticmethod
    def redigest_cut(commit):
        decision=next(p['payload'] for p in commit['publications'] if p['payload_type']=='home-live-shadow-decision-6')
        cut=decision['input_cut'];cut['cut_digest']=digest({k:v for k,v in cut.items() if k!='cut_digest'})
        decision['allocation']['geometry_cut_digest']=cut['cut_digest']
        for output in commit['publications']:
            if output['payload_type']=='home-live-engagement-state-1':
                output['payload']['watermark']['input_cut_digest']=cut['cut_digest']
        return decision

    def test_disabled_experimental_support_stays_unknown_but_qualified_negative_settles(self):
        self.system(experimental=False)
        positive=fact('authored-disabled-positive');saved=self.publish(positive)
        self.start();first=self.tick()
        self.assertNotEqual(first['scene']['status'],'selected')
        relations=self.journal.db.execute("SELECT body FROM live_worker_current WHERE payload_type='home-live-engagement-state-1'").fetchall()
        self.assertTrue(all(json.loads(row['body'])['payload']['state'] not in {'candidate','supported'} for row in relations))
        self.advance()
        proof=self.proof(2,saved,positive)
        self.publish(proof)
        decision=self.tick()
        self.assertNotEqual(decision['scene']['status'],'selected')
        checkpoint=json.loads(self.journal.db.execute('SELECT checkpoint FROM workers').fetchone()['checkpoint'])
        clauses=[r.get('chosen_clause') for r in checkpoint['state']['engagement']['reducer']['state']['relations'].values()]
        self.assertTrue(any(c and c['qualification']=='qualified_lifecycle' for c in clauses),clauses)

    def test_exact_current_cut_rejects_extra_or_missing_receipt_even_on_dark_output(self):
        self.system();self.publish(fact());self.publish(delivery_geometry(1));self.start()
        prepared=self.tick(prepare=True)
        changed=copy.deepcopy(prepared)
        removed=changed['input_receipts'].pop()
        changed['checkpoint']['state']['engagement']['input_receipts']=copy.deepcopy(changed['input_receipts'])
        decision=next(p['payload'] for p in changed['publications'] if p['payload_type']=='home-live-shadow-decision-6')
        decision['input_cut']['input_receipts']=copy.deepcopy(changed['input_receipts'])
        self.redigest_cut(changed)
        status,result=self.post_commit(changed)
        self.assertEqual(status,409,result);self.assertEqual(result['error']['code'],'invalid_input_receipt',result)
        self.assertGreater(removed['cursor'],0)

    def test_geometry_digest_cannot_disagree_with_its_exact_cut_on_dark_output(self):
        self.system();self.publish(delivery_geometry(1));self.start()
        prepared=self.tick(prepare=True)
        decision=next(p['payload'] for p in prepared['publications'] if p['payload_type']=='home-live-shadow-decision-6')
        decision['allocation']['geometry_cut_digest']='f'*64
        status,result=self.post_commit(prepared)
        self.assertEqual(status,409,result);self.assertEqual(result['error']['code'],'invalid_delivery_reference',result)

    def test_complete_geometry_reference_list_cannot_be_omitted_on_dark_output(self):
        self.system();self.publish(delivery_geometry(1));self.start()
        prepared=self.tick(prepare=True)
        decision=next(p['payload'] for p in prepared['publications'] if p['payload_type']=='home-live-shadow-decision-6')
        self.assertTrue(decision['input_cut']['geometry_refs'])
        decision['input_cut']['geometry_refs']=[]
        self.redigest_cut(prepared)
        status,result=self.post_commit(prepared)
        self.assertEqual(status,409,result)
        self.assertIn(result['error']['code'],{'invalid_reference','invalid_delivery_reference','invalid_commit'})

    def test_exact_retry_is_durable_but_equal_time_new_tick_is_refused(self):
        self.system();self.publish(fact());self.start()
        prepared=self.tick(prepare=True)
        status,receipt=self.post_commit(prepared);self.assertEqual(status,200,receipt)
        self.advance(.1)
        self.assertEqual(self.post_commit(prepared),(200,receipt))
        # Direct supported worker API isolates equal-time sequence behavior;
        # public admission of the original still supplies its exact checkpoint.
        script="""const W=require('./tools/home-live-context-worker.cjs');const fs=require('node:fs');
const i=JSON.parse(fs.readFileSync(0,'utf8'));const w=W.createWorker(i.config);w.restore(i.checkpoint,i.t);
try{w.applyCut(i.cut,i.t,{lower:i.t});w.tick(i.t);console.log(JSON.stringify({accepted:true}));}
catch(e){console.log(JSON.stringify({accepted:false,error:e.message}));}"""
        status,cut=self.http.request('GET',PREFIX+'/live-input-cut?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']+'&consumer_id='+WORKER_ID,token=WORKER_TOKEN)
        self.assertEqual(status,200,cut)
        run=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True,timeout=10,
            input=json.dumps(dict(config=self.config,checkpoint=prepared['checkpoint'],cut=cut,t=prepared['checkpoint']['last_committed_at'])))
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout);self.assertFalse(result['accepted']);self.assertEqual(result['error'],'live_worker_tick_order')

    def test_newer_qualified_absence_prevents_profile_binding_from_older_positive(self):
        self.system();positive=fact('authored-prior-positive');saved=self.publish(positive);self.publish(delivery_geometry(1));self.start();self.tick()
        self.advance()
        proof=self.proof(2,saved,positive,'absence_qualified')
        proof['target_subject_ref']=None;proof['head']['key']['subject_ref']=None
        self.publish(proof)
        command=intent('authored-after-absence',at=self.now)
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        result=self.probe.command('tick',t=self.now)
        self.assertEqual(len(result['decisions']),1,{k:result.get(k) for k in ('statuses','http_errors')})
        settlement=next(r for r in result['decisions'][0]['intent_results'] if r['id']==command['id'])
        self.assertEqual(settlement['status'],'rejected',settlement)
        self.assertEqual(settlement['reason'],'qualified_current_person_required')

    def test_geometry_supersession_keeps_only_original_semantic_lease_and_epoch_reset_withdraws(self):
        self.system();old=delivery_geometry(1);saved=self.publish(old)
        derived=fact('authored-derived-old-component')
        derived['available_at']=self.now
        derived['parent_refs']=[{**publication_ref(saved),'component_id':'parts.work'}]
        derived['valid_until']=old['parts']['work']['valid_until']
        self.publish(derived)
        self.advance()
        newer=delivery_geometry(2);self.publish(newer)
        status,snapshot=self.http.request('GET',PREFIX+'/snapshot?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id'])
        self.assertEqual(status,200,snapshot)
        retained=next(p['payload'] for p in snapshot['items'] if p['id']==derived['id'])
        self.assertEqual(retained['observed_at'],derived['observed_at']);self.assertEqual(retained['valid_until'],derived['valid_until'])
        self.advance()
        reset=delivery_geometry(3)
        reset['person_ref']['track_epoch']='authored-new-track'
        reset['head']['key']['person_ref']=copy.deepcopy(reset['person_ref'])
        self.publish(reset)
        status,snapshot=self.http.request('GET',PREFIX+'/snapshot?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id'])
        self.assertEqual(status,200,snapshot)
        self.assertFalse(any(p['id']==derived['id'] for p in snapshot['items']))

    def test_current_ambiguous_geometry_blocks_new_profile_binding_despite_older_positive(self):
        self.system();self.publish(fact());self.publish(delivery_geometry(1));self.start();self.tick();self.advance()
        uncertain=unavailable(delivery_geometry(2))
        prior_person=copy.deepcopy(uncertain['person_ref']);prior_subject=copy.deepcopy(uncertain['subject_ref'])
        uncertain.update(person_ref=None,subject_ref=None)
        uncertain['association'].update(person_status='unresolved',subject_status='unresolved',
            candidate_person_refs=[prior_person],candidate_subject_refs=[prior_subject])
        uncertain['head']['key'].update(person_ref=None,subject_ref=None)
        self.publish(uncertain)
        command=intent('authored-during-ambiguity',at=self.now)
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        decision=self.tick()
        settlement=next(r for r in decision['intent_results'] if r['id']==command['id'])
        self.assertEqual(settlement['status'],'rejected')
        self.assertEqual(decision['execution']['status'],'unavailable')

    def test_older_continuity_cannot_requalify_person_after_newer_exact_absence(self):
        self.system();self.start()
        for seq in range(1,9):
            if seq>1:self.advance()
            positive=fact('authored-continuity-positive-'+str(seq),seq=seq)
            saved=self.publish(positive);self.publish(delivery_geometry(seq));decision=self.tick()
        self.assertEqual(decision['scene']['status'],'selected',decision['scene'])
        self.advance()
        self.publish(delivery_geometry(9))
        continuity=self.proof(9,saved,positive,'continuity_qualified')
        continuity['window']['start_at']=positive['observed_at']
        continuity_ref=self.publish(continuity)
        self.assertEqual(self.tick()['scene']['status'],'selected')
        self.advance()
        absence=self.proof(10,continuity_ref,continuity,'absence_qualified')
        absence['target_subject_ref']=None;absence['head']['key']['subject_ref']=None
        self.publish(absence)
        command=intent('authored-unbind-after-continuity-absence',kind='unbind_profile',at=self.now)
        status,result=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,result)
        prepared=self.tick(prepare=True)
        decision=next(p['payload'] for p in prepared['publications'] if p['payload_type']=='home-live-shadow-decision-6')
        observed={r['id'] for r in prepared['input_receipts']}
        self.assertIn(continuity['id'],observed);self.assertIn(absence['id'],observed)
        actual=next(r for r in decision['intent_results'] if r['id']==command['id'])
        self.assertEqual(actual['status'],'rejected',actual)
        honest=copy.deepcopy(prepared)
        # An unbind with no existing personal binding needs no invented effect.
        # Change only its claimed settlement so independent journal authority,
        # rather than honest worker behavior, must reject stale continuity.
        for rows in (decision['intent_results'],prepared['checkpoint']['state']['intent_settlements'],
                     prepared['checkpoint']['state']['scene_and_corrections']['state']['settlements']):
            for row in rows:
                if row['id']==command['id']:
                    row.update(status='applied',reason='explicit_profile_unbound')
        status,result=self.post_commit(prepared)
        self.assertEqual(status,409,result)
        self.assertIn(result['error']['code'],{'invalid_application_result','invalid_scene_continuity'},result)
        status,result=self.post_commit(honest)
        self.assertEqual(status,200,result)

    def actual_wall_worker(self,offset=None,*,restart_host_offset=None):
        self.system(real_clock=True)
        payload=fact('authored-rebased-wall-clock-input')
        payload.update(observed_at=self.rebased_at,available_at=self.rebased_at+.1,valid_until=self.rebased_at+10)
        payload['clock']['original_value']=str(self.rebased_at*1_000_000_000)
        payload['observation_root_times']={root:self.rebased_at for root in payload['observation_root_times']}
        for ref in payload['original_receipt_refs']:
            ref.update(observed_at=self.rebased_at,available_at=self.rebased_at+.05)
        self.publish(payload)
        script="""const W=require('./tools/home-live-context-worker.cjs');const fs=require('node:fs');
(async()=>{const i=JSON.parse(fs.readFileSync(0,'utf8')),statuses=[],records=[],decisions=[];
const deps={schedule:false,onStatus:s=>statuses.push(s),onDecision:d=>decisions.push(d),record:r=>{records.push(r);return true;}};
if(i.offset!==null)deps.now=()=>Date.now()/1000+i.offset;
const r=await W.start(i.config,deps);await r.cycle();r.stop();const beforeRestart=r.worker.snapshot();
let snapshot=beforeRestart;if(i.restart_host_offset!==null){const actualDateNow=Date.now;
Date.now=()=>actualDateNow()+i.restart_host_offset*1000;try{const second=await W.start(i.config,deps);snapshot=second.worker.snapshot();second.stop();}finally{Date.now=actualDateNow;}}
console.log(JSON.stringify({statuses,records,decisions,snapshot,beforeRestart}));})().catch(e=>{console.log(JSON.stringify({error:e.message}));process.exitCode=1;});"""
        run=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True,timeout=12,
            input=json.dumps(dict(config=self.config,offset=offset,restart_host_offset=restart_host_offset)))
        self.assertEqual(run.returncode,0,run.stderr+'\n'+run.stdout)
        return json.loads(run.stdout)

    def test_actual_wall_mapped_worker_commits_bounded_first_receipt_interval(self):
        result=self.actual_wall_worker()
        self.assertEqual(len(result['decisions']),1,{k:result.get(k) for k in ('statuses','http_errors')})
        receipt=result['decisions'][0]['input_cut']['input_receipts'][0]
        self.assertLess(receipt['received_at_lower_bound'],receipt['received_at'])
        self.assertGreater(receipt['clock_uncertainty_s'],0)
        self.assertAlmostEqual((receipt['received_at']-receipt['received_at_lower_bound'])/2,receipt['clock_uncertainty_s'],places=6)
        self.assertGreaterEqual(result['decisions'][0]['t'],receipt['received_at'])
        self.assertEqual(self.journal.db.execute('SELECT context_seq FROM workers').fetchone()['context_seq'],1)

    def test_controlled_worker_clock_offsets_refuse_instead_of_creating_future_or_backdated_commits(self):
        for offset in (-.05,.05):
            with self.subTest(offset=offset):
                result=self.actual_wall_worker(offset)
                self.assertEqual(result['decisions'],[])
                self.assertTrue(any(s.get('reason')=='worker_clock_unqualified' for s in result['statuses']),result)
                self.assertIsNone(self.journal.db.execute('SELECT context_seq FROM workers').fetchone())

    def test_restart_maps_checkpoint_time_despite_constant_host_date_offset(self):
        for offset in (-100000,100000):
            with self.subTest(offset=offset):
                result=self.actual_wall_worker(restart_host_offset=offset)
                self.assertEqual(len(result['decisions']),1,result)
                restores=[row['data'] for row in result['records'] if row['kind']=='worker_restore']
                self.assertEqual(len(restores),1,result['statuses'])
                self.assertLess(abs(restores[0]['t']-time.time()),1)
                self.assertFalse(any(row['kind']=='worker_cold_start' for row in result['records']))
                durable=json.loads(self.journal.db.execute('SELECT checkpoint FROM workers').fetchone()[0])
                self.assertEqual(restores[0]['checkpoint'],durable)
                before=copy.deepcopy(result['beforeRestart']);after=copy.deepcopy(result['snapshot'])
                # The runtime reconstructs this original-resolver cache from its
                # next cut; durable checkpoint and all decision state persist.
                before['state'].pop('original_receipts');after['state'].pop('original_receipts')
                self.assertEqual(after,before)

    def test_journal_wall_step_latches_unqualified_until_new_explicit_commissioning(self):
        self.system()
        route=PREFIX+'/snapshot?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        status,_=self.http.request('GET',route);self.assertEqual(status,200)
        self.now+=2
        status,result=self.http.request('GET',route)
        self.assertGreaterEqual(status,400,result)
        self.now-=2
        status,result=self.http.request('GET',route)
        self.assertGreaterEqual(status,400,result)


if __name__=='__main__':unittest.main()
