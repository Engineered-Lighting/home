import unittest
import test_paired_runtime as fixtures


class ReplayGenerationTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def test_switch_marks_prior_live_outputs_stale_even_before_expiry(self):
        sid=self.session['id']
        for generation,at in ((1,100),(2,102)):
            self.store.append_event(sid,'experiment.activated',{'generation':generation,'effective_at':at})
        self.store.append_event(sid,'reasoning.output',{'run_id':'jevA','arm':'a','mode':'live',
            'generation':1,'source_end_s':0,'available_at_s':1,'valid_until':120,'status':'ready',
            'arm_snapshot':{'status':'ready'}})
        before=self.store.outputs_at(sid,1500)[0]['payload']
        self.assertEqual(before['status'],'ready')
        after=self.store.outputs_at(sid,2500)[0]['payload']
        self.assertEqual(after['status'],'stale')
        self.assertEqual(after['stale_reason'],'experiment_generation_changed')
        self.assertEqual(after['arm_snapshot']['status'],'stale')
        self.assertEqual(self.store.outputs_at(sid,1500)[0]['payload']['status'],'ready')


if __name__=='__main__':unittest.main()
