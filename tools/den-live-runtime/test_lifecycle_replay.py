"""Causal config6 replay, with actual journal commands and fictional inputs."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
for folder in (ROOT/'stack/services/home-context',ROOT/'tools/den-live-runtime'):
    sys.path.insert(0,str(folder))
import test_runtime as runtime
from journal import PREFIX
from live_lifecycle_test_helpers import lifecycle_fixture,clearance,brightness,lifecycle
from live_scene_test_helpers import HUMAN_TOKEN
from live_test_helpers import NOW,barrier,event,publication_ref


class LifecycleCausalReplayTests(unittest.TestCase):
    fixture_factory=staticmethod(lifecycle_fixture)
    setUp=runtime.ActualOrdinaryReplayTests.setUp
    replay=runtime.ActualOrdinaryReplayTests.replay
    rewrite=runtime.ActualOrdinaryReplayTests.rewrite
    publish=runtime.ActualOrdinaryReplayTests.publish

    def decisions(self):
        rows=[json.loads(line)['record'] for line in self.archive.read_text(encoding='utf8').splitlines()]
        return [r['data']['prepared']['decision'] for r in rows if r['kind']=='worker_prepared']

    def command(self,body):
        status,result=self.http.request('POST',PREFIX+'/intent-events',body,token=HUMAN_TOKEN)
        self.assertEqual(status,200,result)

    def test_brightness_hazards_source_withdrawal_and_assisted_recovery_reproduce_exactly(self):
        self.driver.cycle(self.now)
        bootstrap=self.decisions()[-1]
        self.now=NOW+.3
        self.command(clearance('archive-bootstrap',bootstrap['person_hazards']['items'],self.config['lifecycle_policy'],self.now))
        self.now=NOW+1.2;self.driver.cycle(self.now)
        for seq in range(3,12):self.publish(seq);self.driver.cycle(self.now)
        before=self.decisions()[-1];self.assertEqual(before['execution']['status'],'virtual',before)
        self.now+=.05;self.command(brightness('archive-brightness',before['scene'],self.now,.25))
        self.publish(12);self.driver.cycle(self.now)
        corrected=self.decisions()[-1]
        self.assertEqual(corrected['goal']['nominal']['brightness_fraction'],.25)
        self.assertEqual(corrected['scene']['scene_id'],before['scene']['scene_id'])
        self.now=NOW+12.2
        payload=barrier([self.head,self.geometry_head],event_id='archive-withdrawal',at=NOW+12)
        status,withdrawn=self.http.request('POST',PREFIX+'/events',event(payload));self.assertEqual(status,200,withdrawn)
        self.driver.cycle(self.now)
        stopped=self.decisions()[-1]
        self.assertEqual(stopped['execution']['status'],'unavailable')
        self.assertTrue(stopped['person_hazards']['items'])
        self.now+=.1
        self.command(clearance('archive-known-person-clearance',stopped['person_hazards']['items'],self.config['lifecycle_policy'],self.now))
        self.now=NOW+13.2;self.driver.cycle(self.now)
        self.assertEqual(self.decisions()[-1]['person_hazards']['items'],[])
        drained=self.driver.drain(deadline_ms=1000,through_cursor=withdrawn['cursor']);self.assertEqual(drained['status'],'settled',drained)
        self.recorder.drain({'status':'complete','reason':'authored_211ab','terminal_cursor':drained['input_cursor']})
        status,result=self.replay();self.assertEqual(status,0,result);self.assertEqual(result['status'],'reproduced')
        self.assertEqual(result['commits'],len(self.decisions()))
        def forge(records):
            row=next(r for r in records if r['record']['kind']=='worker_prepared' and r['record']['data']['prepared']['decision']['goal']['status']=='requested')
            row['record']['data']['prepared']['decision']['goal']['nominal']['brightness_fraction']=.99
        status,result=self.replay(self.rewrite(forge));self.assertEqual(status,2);self.assertEqual(result['reason'],'ordinary_replay_output_mismatch')


if __name__=='__main__':unittest.main()
