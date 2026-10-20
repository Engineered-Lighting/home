"""Controlled startup composition fixtures, not installed vLLM acceptance."""
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.admission import ResourceManifest
from home_shared.engine_receipts import EngineReceiptStore, ReceiptUnavailable
from home_shared import vllm_startup as startup
from test_admission import manifest_fixture

REAL_CLAIM = startup._claim_launch


class StartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'engine.db'
        self.manifest = ResourceManifest.from_dict(manifest_fixture())
        self.config = startup.StartupConfig(self.manifest, 'epoch', str(self.path), b'k' * 32)
        with_store = EngineReceiptStore(self.path, manifest_digest=self.manifest.digest,
            engine_id=self.manifest.engine_id, engine_epoch='epoch',
            model_revision=self.manifest.model_revision, cleanup_authority=object(), create=True)
        with_store.close()
        self.events = []
        events = self.events
        class Core:
            def run_busy_loop(core):
                events.append('loop')
                return 'loop-result'
        self.core_class = Core
        async def serve(app, *args, **kwargs):
            events.append(('serve', args, kwargs))
            return 'server-result'
        self.api = SimpleNamespace(serve_http=serve)
        self.claimed = False
        self.store = None
        def claim(path, epoch):
            if self.claimed:
                raise FileExistsError('fixture launch already used')
            self.claimed = True
            self.events.append('claim')
        def install(core, store, **kwargs):
            self.store = store
            self.events.append('engine-hook')
        self.patch('_targets', return_value=(self.core_class, self.api))
        self.protection = self.patch('protected_file', return_value=self.path)
        self.patch('_claim_launch', side_effect=claim)
        self.install = self.patch('install_engine_hook', side_effect=install)
        self.api_hook = self.patch('install_api_hook', side_effect=lambda *a: self.events.append('api-hook') or 'hook')
        self.route = self.patch('install_input_route', side_effect=lambda *a: self.events.append('route'))
        async def utility(method):
            self.assertEqual(method, 'shared_home_runtime')
            return dict(version=1, manifest_digest=self.manifest.digest,
                engine_id=self.manifest.engine_id, engine_epoch='epoch',
                model_revision=self.manifest.model_revision)
        self.utility = utility

    def serving(self):
        return SimpleNamespace(engine_client=SimpleNamespace(
            engine_core=SimpleNamespace(call_utility_async=self.utility)))

    def patch(self, name, **kwargs):
        manager = patch.object(startup, name, **kwargs)
        result = manager.start()
        self.addCleanup(manager.stop)
        return result

    def test_engine_order_existing_store_and_closed_after_exit(self):
        startup.register_startup_hooks(self.config)
        self.assertEqual(self.core_class().run_busy_loop(), 'loop-result')
        self.assertEqual(self.events, ['claim', 'engine-hook', 'loop'])
        self.assertIsNone(self.store._db)
        self.protection.assert_called_with(self.path, secret=True, enforce_linux=True)
        with self.assertRaises(FileExistsError):
            self.core_class().run_busy_loop()

    def test_failed_hook_burns_launch_and_does_not_run_engine(self):
        startup.register_startup_hooks(self.config)
        original = self.install.side_effect
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ReceiptUnavailable('fixture hook failure')
        self.install.side_effect = fail
        with self.assertRaises(ReceiptUnavailable):
            self.core_class().run_busy_loop()
        self.assertNotIn('loop', self.events)
        self.assertIsNone(self.store._db)
        with self.assertRaises(FileExistsError):
            self.core_class().run_busy_loop()

    def test_api_install_before_serve_preserves_arguments(self):
        startup.register_startup_hooks(self.config)
        serving = self.serving()
        app = SimpleNamespace(state=SimpleNamespace(openai_serving_chat=serving))
        self.assertEqual(asyncio.run(self.api.serve_http(app, 'socket', ssl_ca_certs='ca')), 'server-result')
        self.assertEqual(self.events, ['api-hook', 'route', ('serve', ('socket',), {'ssl_ca_certs': 'ca'})])
        self.assertIs(self.api_hook.call_args.args[0], serving)
        self.route.assert_called_once_with(app, 'hook')
        with self.assertRaises(ReceiptUnavailable):
            asyncio.run(self.api.serve_http(app))

    def test_missing_chat_state_fails_before_serve(self):
        startup.register_startup_hooks(self.config)
        with self.assertRaises(ReceiptUnavailable):
            asyncio.run(self.api.serve_http(SimpleNamespace(state=SimpleNamespace())))
        self.assertEqual(self.events, [])

    def test_route_failure_does_not_start_http(self):
        startup.register_startup_hooks(self.config)
        self.route.side_effect = ReceiptUnavailable('fixture route failure')
        with self.assertRaises(ReceiptUnavailable):
            asyncio.run(self.api.serve_http(SimpleNamespace(state=SimpleNamespace(openai_serving_chat=self.serving()))))
        self.assertEqual(self.events, ['api-hook'])

    def test_missing_engine_hook_prevents_listener(self):
        startup.register_startup_hooks(self.config)
        async def missing(method):
            raise RuntimeError('utility not installed')
        self.utility = missing
        with self.assertRaisesRegex(ReceiptUnavailable, 'unavailable'):
            asyncio.run(self.api.serve_http(SimpleNamespace(state=SimpleNamespace(openai_serving_chat=self.serving()))))
        self.assertEqual(self.events, [])

    def test_mismatched_or_malformed_runtime_prevents_listener(self):
        original = self.utility
        original_loop, original_serve = self.core_class.run_busy_loop, self.api.serve_http
        # Fresh targets for each case; no registration reset in production.
        for key, value in [('engine_epoch', 'old'), ('version', True),
                           ('version', 1.0), ('extra', 'field')]:
            with self.subTest(key=key, value=value):
                async def mismatch(method):
                    result = await original(method)
                    result[key] = value
                    return result
                self.utility = mismatch
                self.core_class.run_busy_loop = original_loop
                self.api.serve_http = original_serve
                if hasattr(self.core_class, '_shared_home_startup_registered'):
                    del self.core_class._shared_home_startup_registered
                startup.register_startup_hooks(self.config)
                with self.assertRaisesRegex(ReceiptUnavailable, 'mismatch'):
                    asyncio.run(self.api.serve_http(SimpleNamespace(state=SimpleNamespace(openai_serving_chat=self.serving()))))
                self.assertEqual(self.events, [])

    def test_runtime_timeout_prevents_listener(self):
        config = startup.StartupConfig(self.manifest, 'epoch', str(self.path), b'k' * 32,
                                       rpc_timeout_seconds=.01)
        startup.register_startup_hooks(config)
        async def stalled(method):
            await asyncio.Event().wait()
        self.utility = stalled
        with self.assertRaisesRegex(ReceiptUnavailable, 'unavailable'):
            asyncio.run(self.api.serve_http(SimpleNamespace(state=SimpleNamespace(openai_serving_chat=self.serving()))))
        self.assertEqual(self.events, [])

    def test_duplicate_registration_rejected(self):
        startup.register_startup_hooks(self.config)
        with self.assertRaises(ReceiptUnavailable):
            startup.register_startup_hooks(self.config)

    def test_protection_failure_leaves_targets_unchanged(self):
        original = self.core_class.run_busy_loop
        self.protection.side_effect = ValueError('fixture unprotected file')
        with self.assertRaises(ValueError):
            startup.register_startup_hooks(self.config)
        self.assertIs(self.core_class.run_busy_loop, original)

    def test_missing_ledger_never_created(self):
        self.path.unlink()
        startup.register_startup_hooks(self.config)
        with self.assertRaises(ReceiptUnavailable):
            self.core_class().run_busy_loop()
        self.assertFalse(self.path.exists())

    def test_key_excluded_from_repr_and_bad_limits_rejected(self):
        self.assertNotIn('kkkk', repr(self.config))
        for timeout in (0, 16, float('nan'), True):
            with self.assertRaises(ValueError):
                startup.StartupConfig(self.manifest, 'epoch', str(self.path), b'k' * 32,
                                      rpc_timeout_seconds=timeout)

    def test_exclusive_marker_survives_failed_or_repeated_launch(self):
        # Real marker file/exclusive create/fsync; substitute only the Linux
        # directory handle on this Windows workstation. Linux durability still
        # requires commissioning on the deployment filesystem.
        original_open = os.open
        def open_path(path, flags, *args):
            if Path(path) == self.path.parent:
                return original_open(self.path, os.O_RDWR)
            return original_open(path, flags, *args)
        with patch.object(startup.os, 'O_DIRECTORY', 0, create=True), \
                patch.object(startup.os, 'open', side_effect=open_path):
            REAL_CLAIM(self.path, 'epoch')
            with self.assertRaises(FileExistsError):
                REAL_CLAIM(self.path, 'different-epoch')
        self.assertEqual(self.path.with_name('engine.db.engine-started').read_bytes(), b'epoch')


if __name__ == '__main__':
    unittest.main()
