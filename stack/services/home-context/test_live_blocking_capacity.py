"""Bounded authored retention and SQLite-contention public boundary cases."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from journal import Journal, Limits, PREFIX, canonical
from live_conservative_test_helpers import conservative_fixture
from live_delivery_test_helpers import delivery_geometry
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, event, fact, publication_ref
from live_worker_test_helpers import WORKER_ID, WORKER_TOKEN, WorkerProbe, worker_receipt_resolver


class BlockingCapacityTests(unittest.TestCase):
    def system(self, **limits):
        work=tempfile.TemporaryDirectory(prefix='authored-blocking-capacity-',dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        service,grants,self.config=conservative_fixture()
        self.now,self.mono=NOW+.2,10.
        self.heads={}
        self.path=Path(work.name)/'authored.sqlite'
        self.journal=Journal(self.path,grants=grants,profile=PROFILE,limits=Limits(**limits),
            live_config=service,live_receipt_resolver=worker_receipt_resolver,clock=lambda:self.now,
            live_wall_clock=lambda:self.now,live_monotonic=lambda:self.mono)
        self.addCleanup(self.journal.close)
        self.http=LiveHttp(self.journal);self.addCleanup(self.http.close)
        self.config['service_url']=f'http://127.0.0.1:{self.http.server.server_port}'
        self.scope='?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']

    def cut(self):
        return self.http.request('GET',PREFIX+'/live-input-cut'+self.scope+'&consumer_id='+WORKER_ID,token=WORKER_TOKEN)

    def publish(self,payload):
        key=canonical(payload['head']['key'])
        if key in self.heads:payload['head']['supersedes']=publication_ref(self.heads[key])
        status,result=self.http.request('POST',PREFIX+'/events',event(payload))
        self.assertEqual(status,200,result)
        self.heads[key]=result
        return result

    def test_current_head_remains_in_complete_cut_after_publication_history_pruning(self):
        self.system(max_publications=1)
        negative=fact('authored-still-current-negative')
        negative.update(kind='negative_engagement',value='observed_use_ended',reason='fresh_covered_use_ending')
        negative['head']['key']['predicate']='negative_engagement'
        self.publish(negative)
        self.now+=1;self.mono+=1
        positive=fact('authored-later-positive',seq=2)
        self.publish(positive)
        status,snapshot=self.http.request('GET',PREFIX+'/snapshot'+self.scope)
        self.assertEqual(status,200,snapshot)
        snapshot_ids={row['id'] for row in snapshot['items']}
        self.assertEqual(snapshot_ids,{negative['id'],positive['id']})
        status,cut=self.cut()
        self.assertEqual(status,200,cut)
        self.assertEqual({row['id'] for row in cut['items']},snapshot_ids,
                         'history retention must not silently omit an independently current head')

    def test_current_head_without_any_immutable_revision_refuses_instead_of_disappearing(self):
        self.system(max_publications=1)
        saved=self.publish(fact('authored-missing-revision'))
        # Deliberate storage-loss injection, through the same public read seam.
        self.journal.db.execute('DELETE FROM publications WHERE cursor=?',(saved['cursor'],))
        self.journal.db.execute('DELETE FROM receipts WHERE event_id=?',(saved['id'],))
        status,result=self.cut()
        self.assertEqual(status,409,result)
        self.assertEqual(result['error']['code'],'reference_unavailable',result)

    def test_expired_delivery_copies_do_not_outlive_all_retained_source_bodies(self):
        self.system(retention_seconds=1)
        saved=self.publish(fact('authored-delivered-body'))
        status,cut=self.cut();self.assertEqual(status,200,cut)
        self.assertEqual(cut['items'][0]['cursor'],saved['cursor'])
        self.now+=11;self.mono+=11
        status,snapshot=self.http.request('GET',PREFIX+'/snapshot'+self.scope)
        self.assertEqual(status,200,snapshot);self.assertEqual(snapshot['items'],[])
        status,cut=self.cut();self.assertEqual(status,200,cut);self.assertEqual(cut['items'],[])
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0],0)
        bodies=[row[0] for row in self.journal.db.execute('SELECT publication FROM live_worker_deliveries')]
        retained=[body for body in bodies if body and json.loads(body).get('payload') is not None]
        self.assertEqual(len(retained),0, 'expired private delivery copies must obey retained-body bounds')

    def test_delivery_copy_aggregate_byte_budget_is_explicit_and_transactional(self):
        self.system()
        self.publish(fact('authored-byte-budget'))
        self.journal.limits=replace(self.journal.limits,max_publication_bytes=1)
        status,result=self.cut()
        self.assertEqual(status,507,result)
        self.assertEqual(result['error']['code'],'journal_full',result)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM live_worker_deliveries').fetchone()[0],0)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM live_worker_cuts').fetchone()[0],0)

    def test_24_cycle_profile_prunes_obsolete_bodies_and_preserves_exact_checkpoint_and_retry(self):
        self.system(retention_seconds=1)
        probe=WorkerProbe();self.addCleanup(probe.close)
        probe.command('start',config=self.config,t=self.now)
        peak_rows=peak_bytes=peak_commit=0
        for seq in range(1,25):
            if seq>1:self.now+=1;self.mono+=1
            self.publish(fact('authored-profile-fact-'+str(seq),seq=seq));self.publish(delivery_geometry(seq))
            result=probe.command('tick',t=self.now)
            self.assertEqual(len(result['decisions']),1,{k:result.get(k) for k in ('statuses','http_errors')})
            rows,size=self.journal.db.execute('SELECT count(*),coalesce(sum(length(CAST(publication AS BLOB))),0) FROM live_worker_deliveries').fetchone()
            peak_rows=max(peak_rows,rows);peak_bytes=max(peak_bytes,size)
            peak_commit=max(peak_commit,len(result['commit_posts'][-1].encode()))
        self.assertEqual(self.journal.db.execute('SELECT context_seq FROM workers').fetchone()[0],24)
        self.assertLess(peak_rows,48,'obsolete publication copies must be pruned during repeated cuts')
        self.assertLess(peak_commit,262144)
        durable=self.journal.db.execute('SELECT checkpoint FROM workers').fetchone()[0]
        expected={r['cursor'] for r in json.loads(durable)['state']['engagement']['input_receipts']}
        final_commit=json.loads(result['commit_posts'][-1])
        self.now+=11;self.mono+=11
        status,cut=self.cut();self.assertEqual(status,200,cut);self.assertEqual(cut['items'],[])
        retained={row[0] for row in self.journal.db.execute('SELECT cursor FROM live_worker_deliveries')}
        self.assertEqual(retained,expected,'expired restoration copies must be exactly checkpoint-scoped')
        self.assertEqual(self.journal.db.execute('SELECT checkpoint FROM workers').fetchone()[0],durable)
        status,retry=self.http.request('POST',PREFIX+'/worker-commits',final_commit,token=WORKER_TOKEN)
        self.assertEqual(status,200,retry);self.assertEqual(retry['context_seq'],24)
        print('AUTHORED_24_CYCLE_CAPACITY '+json.dumps(dict(peak_delivery_rows=peak_rows,peak_delivery_bytes=peak_bytes,
              peak_commit_bytes=peak_commit,retained_checkpoint_rows=len(retained),commit_ceiling_bytes=262144)))

    def test_configured_commit_ceiling_has_typed_refusal_before_unbounded_state_admission(self):
        self.system()
        probe=WorkerProbe();self.addCleanup(probe.close)
        probe.command('start',config=self.config,t=self.now)
        self.publish(fact('authored-sized-commit'))
        probe.command('fault',mode='before_commit')
        prepared=json.loads(probe.command('tick',t=self.now)['commit_posts'][-1])
        self.assertLess(len(canonical(prepared).encode()),262144)
        # This exact valid commit is larger than an intentionally narrowed
        # per-operation ceiling; no invalid giant fixture masks admission.
        self.journal.limits=replace(self.journal.limits,max_worker_commit_bytes=1)
        status,result=self.http.request('POST',PREFIX+'/worker-commits',prepared,token=WORKER_TOKEN)
        self.assertEqual(status,413,result)
        self.assertEqual(result['error']['code'],'payload_too_large',result)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM workers').fetchone()[0],0)

    def test_begin_immediate_lock_failure_returns_retryable_typed_503_and_recovers(self):
        self.system()
        self.journal.db.execute('PRAGMA busy_timeout=20')
        external=sqlite3.connect(self.path,isolation_level=None,timeout=.1)
        self.addCleanup(external.close)
        external.execute('BEGIN IMMEDIATE')
        original=event(fact('authored-during-external-lock'))
        try:
            try:
                response=self.http.request('POST',PREFIX+'/events',original)
            except Exception as error:
                response=(None,{'unexpected_transport_error':type(error).__name__})
            self.assertEqual(response[0],503,response)
            self.assertEqual(response[1]['schema'],'home-context-error-1',response)
            self.assertEqual(response[1]['error']['code'],'journal_busy',response)
        finally:
            external.execute('ROLLBACK')
        status,result=self.http.request('POST',PREFIX+'/events',original)
        self.assertEqual(status,200,result)
        self.assertEqual(self.http.request('POST',PREFIX+'/events',original),(200,result))


if __name__=='__main__':unittest.main()
