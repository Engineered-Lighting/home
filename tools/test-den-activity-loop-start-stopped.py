"""Offline startup-order regression; imports no production app or model."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def factory():
    path = Path(__file__).with_name('den-activity-loop-start-stopped.py')
    spec = importlib.util.spec_from_file_location('den_start_stopped_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_app


class StartStopped(unittest.TestCase):
    def test_stopped_before_returning_app(self):
        calls = []
        loop = types.SimpleNamespace(stop=lambda reason: calls.append(reason))
        app = types.SimpleNamespace(state=types.SimpleNamespace(activity_loop=loop))
        producer = types.ModuleType('managed_detector_app')
        producer.create_managed_refresh_app = lambda: app
        with patch.dict(sys.modules, managed_detector_app=producer):
            self.assertIs(factory()(), app)
        self.assertEqual(calls, ['operator_stop'])

    def test_missing_loop_refuses_startup(self):
        producer = types.ModuleType('managed_detector_app')
        producer.create_managed_refresh_app = lambda: types.SimpleNamespace(state=types.SimpleNamespace())
        with patch.dict(sys.modules, managed_detector_app=producer):
            with self.assertRaisesRegex(RuntimeError, 'required_activity_loop_missing'):
                factory()()


if __name__ == '__main__':
    unittest.main()
