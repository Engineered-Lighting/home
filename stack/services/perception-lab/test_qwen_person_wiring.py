"""New protocol is an explicit opt-in; old grants and defaults remain separate."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from live_detector_app import check_qwen_live, qwen_live_factory
from managed_jev import reasoning_factory
from qwen_person_live import QwenPersonPump
from test_qwen_live import StoreFixture, TRANSPORT_CONFIG

BLOCK = dict(transport_file='/fixture/private-transport.json', artifact_sha256='a'*64,
             runtime_revision='authored-runtime', enabled=True)


class Transport:
    def __init__(self, config):
        self.config = config
        self.closed = False

    def close(self):
        self.closed = True


class PersonWiringTests(StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()

    def tearDown(self):
        self.close_store()

    def factory(self, block, grants):
        with patch('live_detector_app.private_json', return_value=dict(TRANSPORT_CONFIG)):
            return qwen_live_factory({'qwen_live': block}, reasoning_factory=grants, transport_factory=Transport)

    def test_old_default_and_old_only_grant_do_not_enable_person_protocol(self):
        grants = SimpleNamespace(qwen_granted=lambda: True)
        old = self.factory(BLOCK, grants)
        new = self.factory(dict(BLOCK, protocol='lab-live-person-scene-1'), grants)
        self.assertTrue(old.kill_switch())
        self.assertFalse(new.kill_switch())
        self.assertEqual(check_qwen_live(BLOCK), check_qwen_live(dict(BLOCK, protocol='lab-live-person-scene-1')))

    def test_explicit_new_protocol_builds_only_the_fixed_bounded_pump(self):
        factory = self.factory(dict(BLOCK, protocol='lab-live-person-scene-1', arm='b'),
                               SimpleNamespace(qwen_granted=lambda: False, qwen_person_granted=lambda: True))
        self.assertTrue(factory.kill_switch())
        pump = factory(self.store, self.session, active=lambda: True, enabled=lambda: True,
                       occupied=lambda: dict(state='occupied', reason='authored'))
        try:
            self.assertIsInstance(pump, QwenPersonPump)
            self.assertEqual(pump.base_profile['adapter_revision'], 'lab-live-person-scene-prompt-1')
            self.assertEqual(pump.config['max_per_minute'], 15)
            self.assertFalse(pump.config['send_prior'])
            self.assertFalse(pump.thread.is_alive())
        finally:
            pump.stop(timeout=1)
        self.assertTrue(pump.transport.closed)
        with self.assertRaises(ValueError):
            factory(self.store, dict(self.session, camera_id='other'), active=lambda: True, enabled=lambda: True,
                    occupied=lambda: dict(state='occupied', reason='authored'))

    def test_unknown_protocol_and_legacy_arm_selector_refuse(self):
        for addition in ({'protocol': 'arbitrary.module'}, {'arm': 'b'},
                         {'protocol': 'lab-live-person-scene-1', 'arm': 'any'}):
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                check_qwen_live(dict(BLOCK, **addition))

    def test_new_policy_grant_is_separate_revocable_and_camera_scoped(self):
        config = dict(schema_version=1, policy_file='policy', credential_file='key', response_version='jev-1.13.0')
        policy = dict(enabled=True, scope='household_structured', site_id='victoria', camera_ids=['den'],
                      structured_sources=['lab-live-person-scene-1'])
        with patch('managed_jev.private_json', side_effect=lambda path: dict(config if path == 'config' else policy)):
            factory = reasoning_factory('config')
            self.assertTrue(factory.qwen_person_granted())
            self.assertFalse(factory.qwen_granted())
            for key, value in (('enabled', False), ('site_id', 'other'), ('camera_ids', ['other']),
                               ('structured_sources', ['lab-live-scene-1'])):
                old = policy[key]
                policy[key] = value
                self.assertFalse(factory.qwen_person_granted())
                policy[key] = old


if __name__ == '__main__':
    unittest.main()
