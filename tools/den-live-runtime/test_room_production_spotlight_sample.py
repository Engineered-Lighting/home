"""Actual accepted Home SAMPLE -> fixed production translator; no native I/O.

Execute the actual factory/Journal/Node-worker case only through the coordinator's
bounded loopback guard. External capture/model/HA inputs remain authored. This
does not establish manual authorization, a production command or physical truth.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'stack/services/home-context'),str(ROOT/'tools/den-live-runtime')]
from room_production_spotlight_sample import (TrustedCommittedSpotlightSample,
    CommittedSpotlightTranslation)
from room_committed_source import RoomCommitBinding,CommittedRoomSource
import test_room_worker as existing
from test_room_fixture_command_join import PartialPrefixRecorder


class CommittedSpotlightSampleTests(unittest.TestCase):
    def test_actual_accepted_samples_preserve_bytes_zero_and_unmet_without_authority(self):
        evidence=Path(os.environ['DEN_PRODUCTION_SAMPLE_EVIDENCE_DIR'])
        allowed=ROOT/'artifacts/den-c12-production-consumer-20261019'
        self.assertTrue(evidence.is_absolute() and evidence.is_dir() and evidence.resolve().is_relative_to(allowed.resolve()))
        self.assertFalse(any(p.is_symlink() for p in (evidence,*evidence.parents)))
        self.assertFalse(any(evidence.iterdir()),'fresh controlled SAMPLE evidence directory required')
        negative_checks=[]
        prefix=PartialPrefixRecorder(evidence/'partial-accepted-source-originals.jsonl')
        self.addCleanup(prefix.close)
        records=prefix.records
        home=existing.RoomWorkerTests('test_rehashed_forged_ambient_sample_is_refused_without_advancing_public_checkpoint')
        self.addCleanup(home.doCleanups)
        actual_create=existing.create_production_acquisition
        def create_before_inputs(*args,**kwargs):
            graph=actual_create(*args,**kwargs)
            try:
                with graph.journal.lock:
                    self.assertIsNone(graph.journal._recorder)
                    self.assertEqual(int(graph.journal._meta('cursor')),0)
                    graph.journal.attach_recorder(prefix.record)
            except BaseException:
                graph.close();raise
            return graph
        with patch.object(existing,'create_production_acquisition',side_effect=create_before_inputs):home.setUp()
        journal=home.graph.journal
        worker=journal.dynamic.worker
        binding=RoomCommitBinding(deepcopy(home.identity),worker.policy['worker_id'],worker.policy['producer_id'],
            worker.policy['config_digest'],journal.dynamic.config['manifest_ref']['digest'],worker.worker_version,
            tuple(f['fixture_id'] for f in worker.policy['room_policy']['fixtures']))
        live={'current':True}
        source=CommittedRoomSource(journal,binding=binding,current=lambda:live['current'])
        consumer=TrustedCommittedSpotlightSample(source)
        zero=positive=None
        for _ in range(14):
            home.now+=1
            home.bundle['external_io'].set_scenario('stationary_work')
            home.wait_occupancy()
            self.assertEqual(home.graph.step()['interaction'],'completed')
            response=home.probe.command('tick',t=home.now)
            self.assertTrue(response['commit_posts'],response)
            commit=json.loads(response['commit_posts'][-1])
            accepted=source.install_commit(commit)
            output=accepted['decision']['room_output']
            domain=output['fixtures'][0]['domains'][7]
            if domain['sample'] is None:continue
            result=consumer.translate('fixture-1',request_id=str(uuid.uuid4()))
            self.assertIs(type(result),CommittedSpotlightTranslation)
            self.assertEqual(result.room_commit_ref,accepted['room_commit_ref'])
            self.assertEqual(result.publication_sha256,hashlib.sha256(result.publication_bytes).hexdigest())
            self.assertEqual(result.sample_pointer,'/payload/room_output/fixtures/0/domains/7/sample')
            publication=json.loads(result.publication_bytes)
            self.assertEqual(publication,accepted['publication'])
            self.assertEqual(result.translation['original_sample'],domain['sample'])
            self.assertFalse(result.translation['authority_created'])
            self.assertFalse(result.translation['physical_permission'])
            self.assertFalse(result.translation['physical_truth'])
            with self.assertRaises(FrozenInstanceError):result.fixture_id='foreign'
            if domain['sample']['brightness_fraction']==0:
                zero=result
                self.assertEqual(result.translation['status'],'supported_zero_without_color_claim')
                self.assertEqual(result.translation['production_request']['levels'],[0,0,0])
                self.assertEqual(result.translation['production_request']['transition_us'],0)
            else:
                positive=result
                self.assertEqual(result.translation['status'],'unmet')
                self.assertEqual(result.translation['reason'],'spotlight_emitter_mapping_and_color_model_unavailable')
                self.assertIsNone(result.translation['production_request'])
            if zero is not None and positive is not None:break
        self.assertIsNotNone(zero,'actual selected TASK ramp must supply its original zero SAMPLE')
        self.assertIsNotNone(positive,'actual later TASK must supply its original nonzero SAMPLE')
        self.assertEqual(consumer.require_current(positive).translation,positive.translation)
        tampered=deepcopy(positive)
        tampered.translation['production_request']={'levels':[1,2,3]}
        with self.assertRaisesRegex(ValueError,'translation_changed'):consumer.require_current(tampered)
        negative_checks.append('nested_translation_mutation_refused')
        with self.assertRaises(ValueError):consumer.require_current(zero)
        negative_checks.append('superseded_zero_translation_refused')
        with self.assertRaisesRegex(ValueError,'foreign_room_fixture'):
            consumer.translate('foreign',request_id=str(uuid.uuid4()))
        negative_checks.append('foreign_fixture_refused')
        with self.assertRaisesRegex(ValueError,'unavailable'):
            consumer.translate('fixture-2',request_id=str(uuid.uuid4()))
        negative_checks.append('unavailable_fixture_refused_without_zero_fallback')
        with self.assertRaises(TypeError):
            consumer.translate('fixture-1',request_id=str(uuid.uuid4()),kind='raw_logical_duties')
        negative_checks.append('RoomSource_raw_duty_injection_refused')
        altered=deepcopy(commit);altered['checkpoint']['input_cursor']+=1
        with self.assertRaises(ValueError):source.install_commit(altered)
        negative_checks.append('altered_commit_refused')
        # Corruption is an adversarial fault, never an alternate accepted source.
        with journal.lock:
            row=journal.db.execute('SELECT key,body FROM live_worker_current WHERE payload_type=?',
                (worker.decision_type,)).fetchone()
            original=row['body'];bad=json.loads(original)
            bad['payload']['room_output']['fixtures'][0]['domains'][7]['sample']['brightness_fraction']=0
            journal.db.execute('UPDATE live_worker_current SET body=? WHERE key=?',(json.dumps(bad),row['key']))
        try:
            with self.assertRaisesRegex(ValueError,'publication_mismatch'):
                consumer.translate('fixture-1',request_id=str(uuid.uuid4()))
            negative_checks.append('tampered_current_publication_refused')
        finally:
            with journal.lock:journal.db.execute('UPDATE live_worker_current SET body=? WHERE key=?',(original,row['key']))
        home.now=positive.valid_until
        with self.assertRaisesRegex(ValueError,'expired'):
            consumer.translate('fixture-1',request_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError,'expired'):consumer.require_current(positive)
        negative_checks.append('exclusive_original_sample_expiry_refused')
        live['current']=False
        with self.assertRaisesRegex(ValueError,'not_current'):
            consumer.translate('fixture-1',request_id=str(uuid.uuid4()))
        negative_checks.append('retired_source_refused')
        self.assertFalse(any(r['kind'].startswith('fixture_canonical_') for r in records))
        saved=[]
        for name,result in [('zero',zero),('nonzero',positive)]:
            filename=name+'-original-publication.json'
            (evidence/filename).write_bytes(result.publication_bytes)
            saved.append(dict(case=name,fixture_id=result.fixture_id,request_id=result.request_id,
                publication_file=filename,publication_sha256=result.publication_sha256,
                sample_pointer=result.sample_pointer,sample_sha256=result.sample_sha256,
                room_commit_ref=result.room_commit_ref,observed_at=result.observed_at,
                checked_at=result.checked_at,valid_until=result.valid_until,translation=result.translation))
        receipt=dict(schema='home-production-committed-sample-validation-1',version=1,status='passed',
            actual_path='incumbent factory/acquisition -> actual Journal/sole Node worker accepted commit -> CommittedRoomSource -> fixed production translator',
            evidence_source='authored_external_inputs',samples=saved,negative_checks=negative_checks,
            source_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
                'tools/den-live-runtime/room_production_spotlight_sample.py',
                'tools/den-live-runtime/test_room_production_spotlight_sample.py')},
            manual_authorization=False,command_effects=0,native_launches=0,physical_permission=False,physical_truth=False)
        receipt.update(capture_prefix_incomplete=True,full_worker_replay=False,
            source_originals_file='partial-accepted-source-originals.jsonl',
            source_originals_prefix_bytes=(evidence/'partial-accepted-source-originals.jsonl').stat().st_size,
            source_originals_prefix_sha256=hashlib.sha256((evidence/'partial-accepted-source-originals.jsonl').read_bytes()).hexdigest())
        (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')


if __name__=='__main__':unittest.main(verbosity=2)
