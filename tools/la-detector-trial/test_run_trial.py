import unittest
from run_trial import check_limits, check_manifest, INPUT_HASHES, SESSION


class TrialGuardTests(unittest.TestCase):
    def test_missing_or_unbounded_limits_fail(self):
        values={'memory.max':str(4*1024**3),'memory.swap.max':'0','pids.max':'64','cpu.max':'100000 100000'}
        check_limits(values)
        for key,value in [('memory.max','max'),('memory.swap.max','1024'),('pids.max','max'),('cpu.max','200000 100000')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                check_limits({**values,key:value})

    def test_only_exact_registered_three_frames(self):
        frames=[dict(frame_id=str(i),sha256=digest,width=1920,height=1080) for i,digest in enumerate(INPUT_HASHES)]
        manifest=dict(site_id='victoria',camera_id='den',session_id=SESSION,time_basis='encoded_video',frames=frames)
        self.assertEqual(check_manifest(manifest),frames)
        for changes in ({'site_id':'echo'},{'frames':frames[:2]},{'frames':list(reversed(frames))},{'time_basis':'capture'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                check_manifest({**manifest,**changes})


if __name__ == '__main__':
    unittest.main()
