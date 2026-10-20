"""Authored independent alignment/calibration; no real qualification or model."""
from copy import deepcopy
from contextlib import ExitStack
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import test_desk_method_export as fixtures
from desk_method_assessment import digest,encoded
from desk_method_export import MEASUREMENT_KEYS,alignment_request,evaluate_resolved_method


def diagnostic_fixture(*,coverage=False):
    c,d,col,o,anchor=fixtures.fixture();q=fixtures.qualification(o)
    q.update(schema='home-desk-method-qualification-2',version=2,
        measurement_binding={k:deepcopy(o['coverage'][k]) for k in MEASUREMENT_KEYS})
    if not coverage:q['coverage']=None
    return c,d,col,o,anchor,q


def evaluate(c,d,col,o,q):
    resolver=fixtures.AuthoredResolver(q);resolver.leaves=deepcopy(o['exposure_alignment'])
    aligned=resolver.align(alignment_request(q,col))
    return evaluate_resolved_method(c,d,col,qualification=q,alignment=aligned,expected_qualification_sha256=digest(q),now=104.5)


class DiagnosticExportTests(unittest.TestCase):
    def test_actual_guide_measures_point_residual_with_unknown_coverage_without_export(self):
        c,d,col,o,anchor,q=diagnostic_fixture()
        # The sealed parent's q1 validator refuses the explicit q2 variant;
        # this is an additive diagnostic consumer, not changed q1 semantics.
        baseline=Path(__file__).resolve().parents[2]/'artifacts/den-desk-evaluated-export-20261017/reproduced-source/tools/den-assessment/desk_method_export.py'
        spec=importlib.util.spec_from_file_location('_sealed_strict_export_parent',baseline)
        parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
        with self.assertRaises(ValueError):parent.validate_qualification(q)
        now,mono=[100.],[1000000000];resolver=fixtures.AuthoredResolver(q)
        with tempfile.TemporaryDirectory(prefix='authored-diagnostic-guide-') as directory,ExitStack() as cleanup:
            base=Path(directory)
            with patch.object(fixtures.distinct.guide,'sync_reference_directory',return_value=None):
                server=fixtures.distinct.guide.Guide(base/'unused.json',seconds=20,assessment_candidate=c,
                    assessment_collection_loader=lambda:deepcopy(col),assessment_directory=base/'references',
                    assessment_clock=lambda:now[0],assessment_monotonic=lambda:mono[0],evaluate_desk_method=True,
                    evaluation_resolver=resolver,evaluation_qualification_sha256=digest(q),evaluation_boot_id=lambda:'authored-boot')
                thread=threading.Thread(target=server.run,daemon=True);thread.start()
                cleanup.callback(lambda:(server.stopped.set(),thread.join(2)))
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                try:
                    connection.request('GET','/');reply=connection.getresponse();page=reply.read().decode('utf-8')
                    self.assertEqual(reply.status,200);self.assertIn('A residual is not a qualified error bound.',page)
                finally:connection.close()
                def post(path,value):
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                    try:
                        connection.request('POST',path,encoded(value),headers={'Origin':server.origin,
                            'X-Desk-Nonce':server.nonce,'Content-Type':'application/json'})
                        reply=connection.getresponse();return reply.status,json.loads(reply.read())
                    finally:connection.close()
                protocol={k:v for k,v in d['protocol']['body'].items() if k!='id'}
                self.assertEqual(post('/evaluation/freeze',dict(protocol=protocol,requirements=d['requirements']['body']))[0],200)
                self.assertIsNone(resolver.leaves);self.assertFalse(resolver.requests)
                parts={name:dict(status='unknown') for name in fixtures.distinct.consumer.PARTS}
                parts['work']=dict(status='available',**{k:deepcopy(d['windows'][0]['request']['reference_parts']['work']['original_measurements'][k])
                    for k in ('outer_corners','thickness_m')})
                selected={k:d['windows'][0]['request']['selected_anchor'][k] for k in ('status','original_measurement')}
                status,started=post('/evaluation/start',dict(configuration_id='neutral',references=c['original_survey']['references'],
                    parts=parts,reference_uncertainty_m=0,activity='focused',selected_anchor=selected))
                self.assertEqual(status,200,started)
                now[0],mono[0]=104.,5000000000;resolver.leaves=deepcopy(o['exposure_alignment'])
                self.assertEqual(post('/evaluation/end',dict(window_id=started['window_id']))[0],200)
                now[0],mono[0]=104.5,5500000000
                status,report=post('/evaluation/report',{});self.assertEqual(status,200,report)
                self.assertEqual(report['schema'],'home-desk-method-evaluation-report-3')
                diagnostic=report['diagnostic_measurement'];self.assertAlmostEqual(diagnostic['selected_anchor_residual_m']['max'],.05)
                self.assertEqual(diagnostic['selected_anchor_residual_m']['samples'],2)
                self.assertNotIn('eligible_s',diagnostic['selected_anchor_residual_m'])
                self.assertNotIn('eligible_s',diagnostic['residual_plus_reference_margin_m'])
                for key in ('method_qualified_error_bound_m','method_availability','method_protection','availability','part_containment'):
                    self.assertIsNone(report[key],key)
                self.assertIsNone(diagnostic['qualified_method_error_bound_m'])
                self.assertEqual((report['decision'],report['export_status']),('block','unavailable'))
                self.assertEqual(post('/evaluation/export',{})[0],409)
                self.assertFalse((base/'references/commissioning.json').exists())
                self.assertNotIn('camera_xyz_m',json.dumps(report))
                self.assertEqual(post('/stop',{})[0],200);thread.join(2);self.assertFalse(thread.is_alive())

    def test_missing_independent_clock_transform_or_reference_produces_no_numeric_residual(self):
        mutations=(lambda c,d,col,o,q:q.update(clock_mapping=None),lambda c,d,col,o,q:q.update(registration=None),
            lambda c,d,col,o,q:d['windows'][0]['request'].update(selected_anchor=dict(status='unknown',original_measurement=None,point_m=None,error_axes_m=None)))
        for mutation in mutations:
            c,d,col,o,anchor,q=diagnostic_fixture();mutation(c,d,col,o,q)
            with self.subTest(mutation=mutation):
                value=evaluate(c,d,col,o,q)
                self.assertIsNone(value['report']['diagnostic_measurement']['selected_anchor_residual_m']['max'])
                self.assertIsNone(value['commissioning']);self.assertEqual(value['report']['decision'],'block')

    def test_complete_q2_export_and_q1_strict_positive_keep_existing_consumer(self):
        for version in (1,2):
            c,d,col,o,anchor,q=diagnostic_fixture(coverage=True)
            if version==1:q=fixtures.qualification(o)
            with self.subTest(version=version):
                value=evaluate(c,d,col,o,q);report=value['report'];exported=value['commissioning']
                self.assertEqual(report['version'],version+1);self.assertIsNotNone(exported,report)
                if version==1:self.assertNotIn('diagnostic_measurement',report)
                else:self.assertAlmostEqual(report['diagnostic_measurement']['selected_anchor_residual_m']['max'],.05)
                anchor.update(observed_at=105.,available_at=105.1,valid_until=107.)
                current=deepcopy(exported['binding']);current['anchor_original_ref'].update(digest=digest(anchor),observed_at=105.,available_at=105.1)
                result=fixtures.distinct.consumer.evaluate_desk_enclosure(c,binding=current,assessment=exported['assessment'],anchor=anchor,now=105.5,hazards=[])
                self.assertEqual(result['status'],'eligible_for_admission_review',result)

if __name__=='__main__':unittest.main()
