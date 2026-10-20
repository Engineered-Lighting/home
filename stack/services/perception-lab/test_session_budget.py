import unittest
from unittest.mock import patch
from session_budget import budget, provisioned_budget, verify_process_lifetime
from test_jetson_detector_app import TrialGuards
from jetson_detector_app import validate_config


class BudgetTests(unittest.TestCase):
    def test_larger_worker_requires_explicit_stage_and_matching_process(self):
        config=TrialGuards().config()
        config.update(expires_at=1355,maximum_requests=720)
        with self.assertRaises(ValueError): validate_config(config,1000)
        config['runtime_seconds']=360
        validate_config(config,1000)
        limits=budget(300)
        with self.assertRaises(ValueError):
            verify_process_lifetime({'RuntimeMaxUSec':'2min','Restart':'no'},limits)
        verify_process_lifetime({'RuntimeMaxUSec':'6min','Restart':'no'},limits)
        for field,value in [('expires_at',1361),('maximum_requests',721),('runtime_seconds',600)]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate_config({**config,field:value},1000)

    def test_operator_stage_defaults_and_invalid_values(self):
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(provisioned_budget(),budget(60))
        for value in ('301','0','300.0','true'):
            with patch.dict('os.environ',{'PERCEPTION_LAB_SESSION_SECONDS':value}),self.assertRaises(ValueError):
                provisioned_budget()
        for stage in (60,120,300):
            limits=budget(stage)
            self.assertGreaterEqual(limits['lease_seconds']-stage,50)
            self.assertGreater(limits['runtime_seconds'],limits['lease_seconds'])
