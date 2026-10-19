"""Actual config4 -> authenticated collector -> same-read private originals.

All camera/model/clock truth is authored. No host/native/model process runs.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT/'stack/services/perception-lab',ROOT/'stack/services/home-context'):
    if str(directory) not in sys.path:sys.path.insert(0,str(directory))
import test_lab_bundled_rgb_stereo as generated
import test_bundle_rgb_input as factory
import test_lab_assessment_collect as legacy
import lab_assessment_collect as collector
from app import create_app


def actual_verified_bundled(case,*,no_pose=False,depth_delta_s=None):
    with ExitStack() as stack:
        stack.enter_context(patch.object(factory.BundledRgbFactoryTests,'max_captures',2,create=True))
        if depth_delta_s is not None:
            append=generated.GeneratedBundledFixture.append_pair
            def adjusted(self,**kwargs):return append(self,**dict(kwargs,depth_delta_s=depth_delta_s))
            stack.enter_context(patch.object(generated.GeneratedBundledFixture,'append_pair',adjusted))
        if no_pose:
            execute=generated.legacy.GeneratedStereoFixture.execute
            def empty(self,request):
                value=execute(self,request)
                value['result'].update(objects=[],boxes=[],pose_instances=[])
                return value
            stack.enter_context(patch.object(generated.legacy.GeneratedStereoFixture,'execute',empty))
        f=generated.GeneratedBundledFixture(case)
    settings=replace(f.store.settings,token_grants=({'subject':'authored-candidate-reader','site_id':'victoria',
        'token_sha256':hashlib.sha256(legacy.TOKEN.encode()).hexdigest(),'capabilities':['read']},))
    api=legacy.ActualReadAPI(create_app(settings=settings,runtime=f.runtime,store=f.store))
    output=Path(f.directory.name)/'authored-bundled-assessment.jsonl'
    with patch('lab_whole_stack_trial.sync_parent') if sys.platform=='win32' else ExitStack():
        result=collector.collect_existing(api,session_id=f.session['id'],output_file=output,
            clock=lambda:f.now,seconds=5,max_attempts=1,collection_mode=collector.BUNDLED_MODE)
    case.assertTrue(result['complete'],result)
    return f,output,api


class VerifiedBundledOriginalTests(unittest.TestCase):
    def test_same_read_actual_originals_and_historical_four_key_reader(self):
        f,path,api=actual_verified_bundled(self)
        ordinary=collector.read_verified_attempts(path)
        self.assertEqual(set(ordinary),{'binding','attempts','summary','collection_sha256'})
        read=Path.read_bytes;calls=[]
        def counted(selected):
            if selected==path:calls.append(selected)
            return read(selected)
        with patch.object(Path,'read_bytes',counted):
            verified=collector.read_verified_bundled_originals(path)
        self.assertEqual(len(calls),1)
        self.assertEqual(verified['collection'],ordinary)
        self.assertEqual(verified['original_family'],collector.BUNDLED_MODE)
        self.assertEqual(verified['input_binding'],ordinary['binding']['binding']['bundled_rgb_binding'])
        c=verified['captures'][0];a=ordinary['attempts'][0]
        self.assertEqual(c['event'],a['original_event'])
        self.assertEqual(c['frame_record'],a['original_frame_record'])
        self.assertEqual(set(c['records']),set(generated.BASE_ROLES))
        self.assertGreaterEqual(c['calculation_completed_at'],c['first_consumer_received_at'])
        for item in c['records'].values():
            self.assertEqual(hashlib.sha256(item['data']).hexdigest(),item['record']['sha256'])
        self.assertTrue(all(method=='GET' for method,_ in api.calls))
        self.assertIsNone(a['error_bound_m'])
        self.assertIsNone(a['operational_geometry'])
        c['records'].clear()
        self.assertEqual(len(collector.read_verified_bundled_originals(path)['captures'][0]['records']),7)

    def test_no_pose_and_missing_completion_remain_explicit_original_attempts(self):
        f,path,_=actual_verified_bundled(self,no_pose=True)
        verified=collector.read_verified_bundled_originals(path)
        self.assertEqual(len(verified['captures']),len(verified['collection']['attempts']))
        self.assertEqual(verified['collection']['attempts'][0]['reason'],'no_pose_prediction')
        rows=[json.loads(line) for line in path.read_bytes().splitlines()]
        next(row['value'] for row in rows if row['kind']=='attempt').pop('calculation_completed_at')
        changed=path.with_name('missing-completion.jsonl')
        changed.write_bytes(b''.join(collector.encoded(row)+b'\n' for row in rows))
        result=collector.read_verified_bundled_originals(changed)
        self.assertIsNone(result['captures'][0]['calculation_completed_at'])
        self.assertEqual(len(result['collection']['attempts']),1)

    def test_corrupt_binary_fails_same_read_verification(self):
        _,path,_=actual_verified_bundled(self)
        rows=[json.loads(line) for line in path.read_bytes().splitlines()]
        media=next(row['value'] for row in rows if row['kind']=='media')
        media['data_b64']='Y29ycnVwdA=='
        changed=path.with_name('corrupt.jsonl')
        changed.write_bytes(b''.join(collector.encoded(row)+b'\n' for row in rows))
        with self.assertRaisesRegex(ValueError,'retained_candidate_media_changed'):
            collector.read_verified_bundled_originals(changed)


if __name__=='__main__':unittest.main()
