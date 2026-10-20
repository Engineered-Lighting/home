"""Actual ASGI lifespan tests; no capture, provider or model execution."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app
from config import Settings
from ownership import OwnershipError
from store import Store


class LifecycleTests(unittest.TestCase):
    def test_refresh_factory_and_journal_close_share_lifespan_thread(self):
        from refresh_lifecycle import RefreshLifecycle
        calls=[]
        class Journal:
            def close(self): calls.append(('journal-close',threading.get_ident()))
        class Backend:
            journal=Journal()
            async def close(self): calls.append(('drained',threading.get_ident()))
        def factory():
            calls.append(('created',threading.get_ident()))
            return Backend()
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(Settings(Path(tmp),min_free_bytes=0),refresh_backend=RefreshLifecycle(factory))
            self.assertEqual(calls,[])
            with TestClient(app): pass
            self.assertEqual([v[0] for v in calls],['created','drained','journal-close'])
            self.assertEqual(len({v[1] for v in calls}),1)

    def test_refresh_backend_drains_before_store_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            calls=[]
            class Refresh:
                async def close(self):
                    calls.append('refresh-drained')
            app=create_app(settings,refresh_backend=Refresh())
            with TestClient(app): pass
            self.assertEqual(calls,['refresh-drained'])
            self.assertTrue(app.state.shutdown_drained)
            replacement=Store(settings);replacement.close()

    def test_runtime_factory_receives_owned_ledger_and_releases_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            seen=[]
            def factory(ledger):
                seen.append(ledger)
                class Runtime:
                    def close(self): return {"drained":True}
                return Runtime()
            application=create_app(settings,runtime_factory=factory)
            self.assertIs(seen[0],application.state.store)
            with TestClient(application): pass
            replacement=Store(settings); replacement.close()

    def test_failed_inert_runtime_factory_releases_owned_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            def factory(_): raise ValueError("configuration rejected")
            with self.assertRaises(ValueError): create_app(settings,runtime_factory=factory)
            replacement=Store(settings); replacement.close()

    def test_owned_store_released_only_after_runtime_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            calls=[]
            class Runtime:
                def close(self):
                    calls.append("drain")
                    return {"drained":True}
            app=create_app(settings,Runtime())
            with TestClient(app):
                with self.assertRaises(OwnershipError): Store(settings)
            self.assertEqual(calls,["drain"])
            self.assertTrue(app.state.shutdown_drained)
            replacement=Store(settings)
            replacement.close()

    def test_undrained_runtime_retains_sqlite_and_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            class Runtime:
                def close(self): return {"drained":False}
            app=create_app(settings,Runtime())
            try:
                with TestClient(app): pass
                self.assertFalse(app.state.shutdown_drained)
                self.assertEqual(app.state.store.list_sessions(),[])
                with self.assertRaises(OwnershipError): Store(settings)
            finally:
                # Fixture has no worker; a production owner cannot release this
                # lock until actual local database users have stopped.
                app.state.store.close()

    def test_expiry_thread_finishes_before_store_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            app=create_app(settings)
            entered,release,finished=threading.Event(),threading.Event(),threading.Event()
            def expiry():
                entered.set()
                release.wait(3)
                app.state.store.list_sessions()
                finished.set()
            app.state.controller.expire=expiry
            with TestClient(app):
                self.assertTrue(entered.wait(2))
                timer=threading.Timer(.05,release.set)
                timer.start()
            timer.join()
            self.assertTrue(finished.is_set())
            self.assertTrue(app.state.shutdown_drained)
            replacement=Store(settings)
            replacement.close()


if __name__=="__main__": unittest.main()
