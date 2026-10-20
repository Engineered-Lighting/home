import json
import unittest
from unittest.mock import patch

import start_live


class FailedUnitPreflightTests(unittest.TestCase):
    """Only exact, diagnosed failed units pass; anything new still refuses before later checks."""
    def preflight(self, failed):
        def run(args):
            if args[:2] == ['systemctl', 'list-units']:
                return ''.join(f'{name} loaded failed failed x\n' for name in failed)
            raise AssertionError('reached checks after the failed-unit gate')
        source = {'files': {}}
        with patch.object(start_live.socket, 'gethostname', return_value='EngineeredLightingServer1'), \
             patch.object(start_live, 'run', run), \
             patch.object(start_live.Path, 'read_text', return_value=json.dumps(source)):
            start_live.preflight()

    def test_documented_units_are_exact_names(self):
        self.assertEqual(start_live.DOCUMENTED_FAILED_UNITS, {
            'observer-health.service', 'observer-recover.service',
            'home-agent-preference-offhost-20260928.service', 'home-agent-preference-offhost-20260928-r2.service'})

    def test_documented_failures_reach_the_next_check(self):
        with self.assertRaisesRegex(AssertionError, 'after the failed-unit gate'):
            self.preflight(sorted(start_live.DOCUMENTED_FAILED_UNITS))

    def test_any_other_failed_unit_refuses(self):
        for name in ('home-agent-preference-offhost-20260929.service', 'home-agent-preference-offhost-20260928-r3.service',
                     'hav-vllm.service'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'new failed service'):
                self.preflight(['observer-health.service', name])


if __name__ == '__main__':
    unittest.main()
