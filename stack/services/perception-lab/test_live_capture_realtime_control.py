"""Real progressing wall clock, authored control metadata and fake publisher."""
import time
import unittest

import live_capture_fixtures as fixtures
from journal import digest
from live_capture import LiveCaptureAdapter


class RealClockControlTests(unittest.TestCase):
    def adapter(self, *, future=False):
        config, policy, method, _, _ = fixtures.setup()
        control_policy = config['manifest']['sources'][fixtures.SOURCE]['control_clock']
        self.events = []
        def control():
            time.sleep(.002)
            ns = time.time_ns() + (60_000_000_000 if future else 0)
            return {'observed_at': ns / 1_000_000_000, 'clock': {'status': 'commissioned',
                **{key: control_policy[key] for key in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
                'original_value': str(ns), 'uncertainty_s': .001}}
        def publish(event):
            self.events.append(event)
            return {'status': 'accepted', 'id': event['id'], 'payload_digest': digest(event['payload']), 'cursor': 1}
        return LiveCaptureAdapter(config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
            policy=policy, method_ref=method, clock=time.time, publish=publish, control_clock=control)

    def test_real_control_clock_is_sampled_before_its_result_availability(self):
        result = self.adapter().withdraw(retain_originals=True)
        self.assertEqual(result['withdrawal'], 'recorded', result)
        payload = self.events[0]['payload']
        self.assertGreaterEqual(payload['available_at'], payload['observed_at'])
        self.assertLessEqual(payload['available_at'], time.time())

    def test_a_future_control_assertion_is_still_refused_without_clamping(self):
        result = self.adapter(future=True).withdraw(retain_originals=True)
        self.assertEqual(result['withdrawal'], 'not_recorded')
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
