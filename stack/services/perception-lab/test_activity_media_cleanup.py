"""Automatic cleanup uses the real Store deletion guards, never a filesystem glob."""
import tempfile
import unittest
from pathlib import Path
from config import Settings
from store import Store
from activity_loop_runtime import ActivityMediaCleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.now = [1800000000.]
        self.store = Store(Settings(storage_root=Path(temp.name), min_free_bytes=0), clock=lambda: self.now[0])
        self.addCleanup(self.store.close)
        store = self.store
        class Controller:
            def __init__(self): self.store = store
            def delete(self, sid, revision): return store.delete_session(sid, revision)
        self.controller = Controller()
        self.config = dict(root=str(self.store.root), created_after=self.now[0], age_s=3600)

    def session(self, request, *, stopped=True):
        s, _ = self.store.create_session('den', 'apartment-viewing', 'test', request)
        self.store.transition(s['id'], 'starting'); self.store.transition(s['id'], 'recording')
        mid = self.store.add_media(s['id'], b'private original', 'image/jpeg', {'kind': 'frame'})['id']
        self.store.append_event(s['id'], 'measurement', {'value': 42})
        if stopped: self.store.transition(s['id'], 'stopped', 'activity_loop_roll')
        return s['id'], mid

    def test_only_new_owned_terminal_media_is_deleted_and_measurements_survive(self):
        old = self.session('activity-loop-old')
        self.config['created_after'] += 1; self.now[0] += 2
        ours = self.session('activity-loop-new')
        unrelated = self.session('manual-recording')
        cleanup = ActivityMediaCleanup(self.controller, self.config)
        cleanup.tick(); self.assertTrue(self.store.media_path(*ours).exists())
        self.now[0] += 3601; cleanup.tick()
        self.assertFalse(self.store.media_path(*ours).exists())
        for row in (old, unrelated): self.assertTrue(self.store.media_path(*row).exists())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE session_id=? AND kind='measurement'", (ours[0],)).fetchone()[0], 1)
        self.assertTrue(cleanup.snapshot()['ok'])
        cleanup.tick(); self.assertEqual(cleanup.snapshot()['deleted'], 1)

    def test_expired_active_session_blocks_instead_of_silently_outliving_retention(self):
        active = self.session('activity-loop-active', stopped=False)
        cleanup = ActivityMediaCleanup(self.controller, self.config)
        self.now[0] += 3601; cleanup.tick()
        self.assertFalse(cleanup.snapshot()['ok'])
        self.assertTrue(self.store.media_path(*active).exists(), 'never unlink from a live worker')

    def test_cleanup_failure_stops_loop_through_existing_monitor_reason(self):
        from activity_loop import ActivityLoop
        from test_activity_loop import FakeDeep, Brain
        from brain_publisher import BrainPublisher
        deep = FakeDeep(); deep.maintenance_ok = lambda: False
        loop = ActivityLoop(deep=deep, occupancy=lambda: 'on', clock=lambda: self.now[0],
            publisher=BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                     post=Brain()))
        result = loop.tick()
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(result['stopped_reason'], 'monitor_backoff')

    def test_wrong_root_and_unbounded_retention_are_refused(self):
        for patch in ({'root': str(self.store.root / 'other')}, {'age_s': 172800}, {'created_after': True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                ActivityMediaCleanup(self.controller, {**self.config, **patch})

    def test_workstation_can_validate_absolute_linux_deployment_path(self):
        ActivityMediaCleanup.check_config({**self.config, 'root': '/home/service/continuous-data/attempt'})
        with self.assertRaises(ValueError):
            ActivityMediaCleanup.check_config({**self.config, 'root': 'relative/store'})

    def test_deletion_failure_is_visible_and_retried_without_touching_other_media(self):
        ours = self.session('activity-loop-new'); self.now[0] += 3601
        cleanup = ActivityMediaCleanup(self.controller, self.config)
        real = self.controller.delete
        self.controller.delete = lambda *a: (_ for _ in ()).throw(OSError('private detail'))
        cleanup.tick(); self.assertFalse(cleanup.snapshot()['ok'])
        self.assertEqual(cleanup.snapshot()['error'], 'OSError')
        self.assertTrue(self.store.media_path(*ours).exists())
        self.controller.delete = real; self.now[0] += 30; cleanup.tick()
        self.assertTrue(cleanup.snapshot()['ok']); self.assertFalse(self.store.media_path(*ours).exists())


if __name__ == '__main__': unittest.main()
