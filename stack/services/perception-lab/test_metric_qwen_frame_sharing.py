"""Real metric capture/Store/paired runner with an authored Qwen frame tap."""
import unittest
import test_apartment_viewing_metric_source as fixtures
from frame_ring import FrameRing


class MetricQwenFrameSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MetricSourceTests.setUpClass()
        cls.addClassCleanup(fixtures.MetricSourceTests.doClassCleanups)

    def setUp(self):
        self.f=fixtures.MetricSourceTests('runTest');self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_accepted_metric_capture_shares_exact_original_outside_runtime_lock(self):
        f=self.f;runtime=f.build(metric_factory=f.factory());seen=[]
        ring=FrameRing(clock=lambda:f.now)
        def tap(jpeg,header):
            self.assertFalse(runtime.lock._is_owned(),'Qwen tap must not hold the runtime lock')
            seen.append((jpeg,header))
            ring.offer(jpeg,header)
        runtime.preview_taps['den']=tap
        f.now=1800000000.59;pump=runtime.preview_pumps[f.session['id']]
        first=pump.consume(f.originals['anchor_a'],f.headers['a'])
        self.assertEqual(first['state'],'awaiting_metric_frame')
        self.assertEqual(seen,[],'unaccepted metric anchor must not be shared')
        second=pump.consume(f.originals['anchor_b'],f.headers['b'])
        self.assertIn('media_ids',second)
        self.assertEqual(len(seen),1,'metric capture did not reach the always-on Qwen ring')
        self.assertTrue(seen[0]==(f.originals['anchor_b'],f.headers['b']),
            'the metric branch must share exact original bytes and metadata')
        self.assertEqual(ring.snapshot()['counts']['accepted'],1,'the real Qwen ring must accept the shared original')
        self.assertTrue(second['viewing_only']);self.assertFalse(second['physical_permission'])

    def test_qwen_tap_failure_does_not_break_metric_capture_or_paired_offer(self):
        f=self.f;runtime=f.build(metric_factory=f.factory());seen=[]
        def unavailable(jpeg,header):
            seen.append((jpeg,header));raise RuntimeError('authored Qwen ring unavailable')
        runtime.preview_taps['den']=unavailable
        first,second=f.anchors(runtime)
        self.assertEqual(first['state'],'awaiting_metric_frame')
        self.assertIn('media_ids',second)
        self.assertEqual(len(seen),1,'actual accepted metric branch must invoke the independent Qwen tap')
        self.assertTrue(second['viewing_only']);self.assertFalse(second['physical_permission'])


if __name__=='__main__':unittest.main(verbosity=2)
