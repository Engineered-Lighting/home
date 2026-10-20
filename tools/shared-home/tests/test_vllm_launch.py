"""Launcher fail-closed wiring; no vLLM or installed wheel claimed."""
import os
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.engine_receipts import ReceiptUnavailable
from home_shared import vllm_launch, vllm_plugin


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.entry = SimpleNamespace(name=vllm_plugin.PLUGIN_NAME, value=vllm_plugin.ENTRY_POINT)
        self.standard = ModuleType('vllm.entrypoints.cli.main')
        self.standard.main = lambda: self.events.append(('serve', list(sys.argv)))

    def run_launch(self, *, entries=None, env=None, failure=None):
        def register():
            self.events.append('register')
            self.assertEqual(os.environ['HOME_SHARED_VLLM_GUARDED'], '1')
            if failure:
                raise failure
        with patch.dict(os.environ, env or {}, clear=True), \
                patch.object(sys.modules['__main__'], '__spec__',
                             SimpleNamespace(name='home_shared.vllm_launch')), \
                patch('home_shared.vllm_launch.importlib.metadata.entry_points',
                      return_value=[self.entry] if entries is None else entries), \
                patch('home_shared.vllm_launch.register', side_effect=register), \
                patch.dict(sys.modules, {'vllm.entrypoints.cli.main': self.standard}):
            vllm_launch.main()

    def test_imported_main_without_module_bootstrap_is_rejected(self):
        with patch.object(sys.modules['__main__'], '__spec__', None):
            with self.assertRaises(ReceiptUnavailable):
                vllm_launch.main()

    def test_standard_arguments_preserved_after_explicit_registration(self):
        args = ['guarded', 'serve', 'existing-model', '--port', '8000']
        with patch.object(sys, 'argv', args):
            self.run_launch(env={'VLLM_PLUGINS': 'existing_plugin,home_shared_admission'})
        self.assertEqual(self.events, ['register', ('serve', args)])

    def test_excluded_missing_duplicate_or_wrong_entry_never_starts(self):
        with patch.object(sys, 'argv', ['guarded', 'serve', 'model']):
            for entries in ([], [self.entry, self.entry],
                            [SimpleNamespace(name=self.entry.name, value='other:register')]):
                with self.assertRaises(ReceiptUnavailable):
                    self.run_launch(entries=entries)
            with self.assertRaises(ReceiptUnavailable):
                self.run_launch(env={'VLLM_PLUGINS': 'existing_plugin'})
        self.assertFalse(self.events)

    def test_registration_failure_propagates_before_cli(self):
        with patch.object(sys, 'argv', ['guarded', 'serve', 'model']):
            with self.assertRaises(ReceiptUnavailable):
                self.run_launch(failure=ReceiptUnavailable('fixture rejected'))
        self.assertEqual(self.events, ['register'])

    def test_other_commands_rejected(self):
        with patch.object(sys, 'argv', ['guarded', 'run-batch']):
            with self.assertRaises(ReceiptUnavailable):
                self.run_launch()
        self.assertFalse(self.events)

    def test_plugin_requires_explicit_guard_and_descriptor(self):
        for env in ({}, {'HOME_SHARED_VLLM_GUARDED': '1'}):
            with patch.dict(os.environ, env, clear=True), self.assertRaises(ReceiptUnavailable):
                vllm_plugin.register()

    def test_spawn_bootstrap_registers_without_cli_or_plugin_discovery(self):
        with patch('home_shared.vllm_plugin.register') as register, \
                patch('importlib.metadata.entry_points', side_effect=AssertionError('discovery')):
            with self.assertWarns(RuntimeWarning):
                runpy.run_module('home_shared.vllm_launch', run_name='__mp_main__')
        register.assert_called_once_with()

    def test_spawn_registration_failure_is_not_swallowed(self):
        with patch('home_shared.vllm_plugin.register', side_effect=ReceiptUnavailable('failed')):
            with self.assertRaises(ReceiptUnavailable), self.assertWarns(RuntimeWarning):
                runpy.run_module('home_shared.vllm_launch', run_name='__mp_main__')

    def test_plugin_idempotence_and_changed_configuration_rejection(self):
        config = SimpleNamespace(epoch='one')
        with patch.dict(os.environ, {'HOME_SHARED_VLLM_GUARDED': '1',
                                     'HOME_SHARED_VLLM_LAUNCH': '/protected/launch.json'}, clear=True), \
                patch.object(vllm_plugin, '_registered', None), \
                patch('home_shared.vllm_launch_config.load_launch_config', return_value=config) as load, \
                patch('home_shared.vllm_startup.register_startup_hooks') as install:
            vllm_plugin.register()
            vllm_plugin.register()
            install.assert_called_once_with(config)
            load.return_value = SimpleNamespace(epoch='two')
            with self.assertRaises(ReceiptUnavailable):
                vllm_plugin.register()


if __name__ == '__main__':
    unittest.main()
