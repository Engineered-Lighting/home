import sys
import types
import unittest
from unittest.mock import patch

from continuous_app import create_app


class ContinuousEntryTests(unittest.TestCase):
    def create(self, cleanup=None, missing_loop=False):
        calls = []
        deep = types.SimpleNamespace(snapshot=lambda: {'media_cleanup': cleanup})
        loop = types.SimpleNamespace(stop=calls.append, deep=deep)
        app = types.SimpleNamespace(state=types.SimpleNamespace())
        if not missing_loop:
            app.state.activity_loop = loop
        factory = types.SimpleNamespace(create_managed_refresh_app=lambda: app)
        return calls, app, patch.dict(sys.modules, {'managed_detector_app': factory})

    def test_ready_cleanup_starts_under_operator_stop(self):
        calls, app, fixture = self.create({'enabled': True, 'ok': True})
        with fixture:
            self.assertIs(create_app(), app)
        self.assertEqual(calls, ['operator_stop'])

    def test_legacy_loop_is_refused_before_lifespan(self):
        calls, _, fixture = self.create({'enabled': False})
        with fixture, self.assertRaisesRegex(RuntimeError, 'continuous_media_cleanup_required'):
            create_app()
        self.assertEqual(calls, ['operator_stop'])

    def test_failed_cleanup_is_refused(self):
        calls, _, fixture = self.create({'enabled': True, 'ok': False})
        with fixture, self.assertRaisesRegex(RuntimeError, 'continuous_media_cleanup_required'):
            create_app()
        self.assertEqual(calls, ['operator_stop'])

    def test_missing_loop_is_refused(self):
        _, _, fixture = self.create(missing_loop=True)
        with fixture, self.assertRaisesRegex(RuntimeError, 'continuous_activity_loop_required'):
            create_app()


if __name__ == '__main__':
    unittest.main()
