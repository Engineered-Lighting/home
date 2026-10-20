"""Causal order with durable authored originals; no real qualification.

The verified collection loader/alignment trust are explicit authored ports. The
source event/attempt bytes are created and fsynced only after Freeze/Start.
Actual existing calibration, math, consumer and core recorder rules are used.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import test_desk_method_export as fixtures
from desk_method_assessment import CausalEvaluatedDeskAssessment,digest,encoded


class CausalExportTests(unittest.TestCase):
    def run_session(self,mutation=None,failed_alignment_ack=False):
        c,d,col,o,anchor=fixtures.fixture();q=fixtures.qualification(o);resolver=fixtures.AuthoredResolver(q)
        now,mono,records=[100.],[1000000000],[]
        with tempfile.TemporaryDirectory(prefix='authored-causal-desk-') as directory:
            path=Path(directory)/'published-attempts.jsonl'
            def record(action):
                records.append(deepcopy(action))
                if failed_alignment_ack and action['kind']=='alignment':return dict(status='unconfirmed')
                return dict(status='retained',sha256=digest(action))
            def loader():
                raw=path.read_bytes();rows=[json.loads(line) for line in raw.splitlines()]
                value=deepcopy(col);value['attempts']=rows;value['collection_sha256']=hashlib.sha256(raw).hexdigest()
                value['summary']['archive_sha256']=value['collection_sha256']
                self.assertTrue(all(r['original_event_sha256']==digest(r['original_event']) for r in rows))
                return value
            s=CausalEvaluatedDeskAssessment(c,clock=lambda:now[0],monotonic=lambda:mono[0],verified_collection_loader=loader,
                record=record,qualification_resolver=resolver,expected_qualification_sha256=digest(q),reference_boot_id=lambda:'authored-boot')
            s.freeze(d['protocol']['body'],d['requirements']['body'])
            self.assertFalse(path.exists());self.assertIsNone(resolver.leaves);self.assertFalse(resolver.requests)
            self.assertNotIn('exposure_alignment',records[0]['body']['qualification'])
            self.assertNotIn('source_event_id',json.dumps(records[0]))
            request=d['windows'][0]['request']
            s.start('held-1','neutral',request['reference_parts'],0,'focused',
                {k:request['selected_anchor'][k] for k in ('status','original_measurement')})
            with path.open('xb') as output:
                for source in col['attempts']:
                    now[0]=source['source_times']['available_at'];mono[0]=round((now[0]-99.)*1e9)
                    raw=encoded(source)+b'\n';self.assertEqual(output.write(raw),len(raw));output.flush();os.fsync(output.fileno())
            resolver.leaves=deepcopy(o['exposure_alignment'])
            now[0],mono[0]=104.,5000000000;s.end('held-1')
            now[0],mono[0]=104.5,5500000000
            if mutation:mutation(resolver)
            if failed_alignment_ack or mutation:
                with self.assertRaises(ValueError):s.report()
                before=len(records)
                with self.assertRaises(ValueError):s.report()
                self.assertEqual(len(records),before);return
            report=s.report();self.assertEqual(report['schema'],'home-desk-method-evaluation-report-2')
            self.assertEqual(report['export_status'],'available_for_private_save',report)
            self.assertAlmostEqual(report['selected_anchor_error_m']['max'],.05)
            self.assertAlmostEqual(report['availability']['complete_fraction'],.9725)
            self.assertEqual([a['kind'] for a in records],['freeze','start','end','alignment','report'])
            self.assertNotIn('instances',json.dumps(resolver.requests));self.assertNotIn('camera_xyz_m',json.dumps(resolver.requests))
            exported=records[-1]['body']['commissioning'];anchor.update(observed_at=105.,available_at=105.1,valid_until=107.)
            current=deepcopy(exported['binding']);current['anchor_original_ref'].update(digest=digest(anchor),observed_at=105.,available_at=105.1)
            result=fixtures.distinct.consumer.evaluate_desk_enclosure(c,binding=current,assessment=exported['assessment'],anchor=anchor,now=105.5,hazards=[])
            self.assertEqual(result['status'],'eligible_for_admission_review',result)

    def test_freeze_then_durable_published_originals_then_alignment_and_strict_export(self):self.run_session()

    def test_resolver_policy_foreign_or_missing_leaves_and_unknown_alignment_ACK_refuse(self):
        changes=(lambda r:r.q['coverage'].update(max_anchor_age_s=3),
            lambda r:r.leaves[0].update(original_event_sha256='f'*64),lambda r:r.leaves.pop(),
            lambda r:r.leaves.append(deepcopy(r.leaves[0])))
        for mutation in changes:
            with self.subTest(mutation=mutation):self.run_session(mutation)
        self.run_session(failed_alignment_ack=True)

if __name__=='__main__':unittest.main()
