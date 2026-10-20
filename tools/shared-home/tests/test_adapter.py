import unittest

from home_shared.adapter import CurrentContext, Frame, VictoriaAdapter, validate_frame, finite
from test_gateway import header_fixture


class AdapterTests(unittest.TestCase):
    def test_finite_rejects_huge_metadata_integers_without_overflow(self):
        self.assertFalse(finite(10**400))
        self.assertFalse(finite(True))
        self.assertTrue(finite(1.25))

    def setUp(self):
        self.adapter = VictoriaAdapter()
        self.adapter.connect(clean_session=True)
        self.adapter.subscription_ack(self.adapter.subscriptions, success=True)

    def reconcile(self, t, people=1, payload=None):
        a = self.adapter
        a.health(camera_session="capture", frigate_instance="frigate-process", healthy=True,
                 checked_mono=t, now_mono=t)
        a.count_hint()
        a.request_reconciliation(t)
        accepted = a.snapshot(payload if payload is not None else
                              {"den_oak": {"objects": [{"label": "person", "stationary": True}] * people}}, t)
        a.tick(t, 1000 + t)
        return accepted

    def occupied(self):
        self.reconcile(0)
        self.reconcile(5)
        return self.adapter.take_work(5)

    def test_explicit_empty_room_refresh_and_unknown_evidence(self):
        with self.assertRaises(ValueError):
            self.adapter.request_refresh(now_mono=0, now_unix=1000)
        self.reconcile(1, people=0)
        work = self.adapter.request_refresh(now_mono=1, now_unix=1001)
        self.assertEqual(work.kind, "interactive")
        self.assertEqual(work.request_deadline, 1016)
        self.assertEqual(self.adapter.take_work(1), work)
        self.adapter.bind_frame(Frame("capture", "frame", 1001, 1, .1, "clock", header_fixture()),
                                now_mono=1, now_unix=1001, cross_host_uncertainty=.1)
        self.adapter.invalidate()
        with self.assertRaises(ValueError):
            self.adapter.bind_frame(Frame("capture", "frame", 1001, 1, .1, "clock", header_fixture()),
                                    now_mono=1, now_unix=1001, cross_host_uncertainty=.1)

    def test_interactive_replaces_only_unsent_background_and_keeps_deadline(self):
        self.reconcile(0)
        self.reconcile(5)
        background = self.adapter.pending
        work = self.adapter.request_refresh(now_mono=5, now_unix=1005)
        self.assertNotEqual(work.request_id, background.request_id)
        with self.assertRaises(ValueError):
            self.adapter.request_refresh(now_mono=6, now_unix=1006)
        self.assertEqual(self.adapter.pending, work)
        self.reconcile(6)
        self.assertEqual(self.adapter.take_work(6), work)
        queued = self.adapter.request_refresh(now_mono=6, now_unix=1006)
        self.assertEqual(self.adapter.active, work)
        self.assertIsNone(self.adapter.take_work(6))
        self.adapter.complete(work.request_id, None, now_mono=7, now_unix=1007)
        self.assertIsNone(self.adapter.take_work(7))  # same dispatch cooldown
        self.reconcile(21)
        self.assertIsNone(self.adapter.pending)  # expiry cannot extend while queued
        self.assertEqual(queued.request_deadline, 1021)

    def test_startup_requires_ack_and_missing_is_not_empty(self):
        a = VictoriaAdapter()
        a.connect(clean_session=True)
        self.assertIsNone(a.request_reconciliation(0))
        self.assertFalse(self.reconcile(0, payload={"den_oak": {"config": {}}}))
        self.assertEqual(self.adapter.state, "unknown")
        self.assertTrue(self.reconcile(1, people=0))
        self.assertEqual(self.adapter.state, "vacancy_pending")

    def test_stationary_refresh_and_restart_no_replay(self):
        work = self.occupied()
        self.assertEqual(work.kind, "arrival")
        self.assertEqual(work.request_deadline, 1020)
        self.adapter.complete(work.request_id, None, now_mono=6, now_unix=1006)
        for t in range(10, 130, 5):
            self.reconcile(t)
        refresh = self.adapter.take_work(125)
        self.assertEqual(refresh.kind, "refresh")
        self.assertEqual(refresh.scheduled_at, 1125)
        old_generation = self.adapter.generation
        self.adapter.disconnect()
        self.adapter.connect(clean_session=True)
        self.assertGreater(self.adapter.generation, old_generation)
        self.assertIsNone(self.adapter.pending)
        self.assertIsNotNone(self.adapter.active)  # outstanding work is still charged

    def test_count_retained_and_duplicates_are_not_state(self):
        self.adapter.count_hint(retained=True)
        self.assertIsNone(self.adapter.people)
        self.adapter.health(camera_session="capture", frigate_instance="f", healthy=True,
                            checked_mono=0, now_mono=0)
        self.adapter.request_reconciliation(0)
        payload = {"den_oak": {"objects": []}}
        self.assertFalse(self.adapter.snapshot(payload, 0, retained=True))
        self.assertFalse(self.adapter.snapshot(payload, 0, duplicate=True))
        self.assertTrue(self.adapter.snapshot(payload, 0))
        self.assertFalse(self.adapter.snapshot(payload, 0))

    def test_vacancy_interrupt_and_camera_restart_invalidate(self):
        work = self.occupied()
        for t in range(10, 45, 5):
            self.reconcile(t, people=0)
        self.reconcile(45, people=1)
        self.assertEqual(self.adapter.generation, work.occupancy_generation)
        for t in range(50, 115, 5):
            self.reconcile(t, people=0)
        self.assertGreater(self.adapter.generation, work.occupancy_generation)
        self.assertEqual(self.adapter.state, "vacant")
        self.assertIsNone(self.adapter.pending)
        self.adapter.health(camera_session="new", frigate_instance="frigate-process", healthy=True,
                            checked_mono=115, now_mono=115)
        self.assertEqual(self.adapter.state, "unknown")

    def test_unobserved_gap_cannot_confirm_vacancy_or_arrival(self):
        self.reconcile(0)
        self.assertTrue(self.reconcile(20))
        self.assertIsNone(self.adapter.take_work(20))
        self.reconcile(21)
        self.assertIsNone(self.adapter.take_work(21))
        self.reconcile(26)
        self.assertIsNotNone(self.adapter.take_work(26))

    def test_frame_selection_and_result_bound_to_source(self):
        work = self.occupied()
        frame = Frame("capture", "42", 1005, 5, .1, "commissioned-revision", header_fixture())
        self.adapter.bind_frame(frame, now_mono=5.2, now_unix=1005.2, cross_host_uncertainty=.1)
        result = dict(request_id=work.request_id, site_id="victoria", camera_id="den_oak", room_id="den",
                      adapter_session=work.adapter_session, sequence=work.sequence,
                      occupancy_generation=work.occupancy_generation, capture_session="capture", frame_id="42",
                      observed_at=1005, valid_until=1185, uncertainty_seconds=.2, description="A person is seated.", status="ok")
        self.assertEqual(self.adapter.complete(work.request_id, result, now_mono=6, now_unix=1006), result)
        for changes in ({"observed_at": 999}, {"uncertainty_seconds": 2}, {"clock_mapping_revision": ""}):
            invalid = Frame(**{**frame.__dict__, **changes})
            with self.assertRaises(ValueError):
                validate_frame(invalid, work, now_mono=6, now_unix=1006, cross_host_uncertainty=.1)
        for jpeg in (b"\xff\xd8\xff\xd9", header_fixture(65535, 65535)):
            with self.assertRaises(ValueError):
                validate_frame(Frame(**{**frame.__dict__, "jpeg": jpeg}), work,
                               now_mono=6, now_unix=1006, cross_host_uncertainty=.1)

    def test_pet_only_does_not_schedule(self):
        for t in (0, 5, 10):
            self.reconcile(t, payload={"den_oak": {"objects": [{"label": "cat"}]}})
        self.assertEqual(self.adapter.pets, 1)
        self.assertIsNone(self.adapter.take_work(10))

    def test_dispatch_checks_health_without_tick(self):
        self.reconcile(0)
        self.reconcile(5)
        self.assertIsNone(self.adapter.take_work(16))
        self.assertEqual(self.adapter.state, "unknown")


class ConsumerTests(unittest.TestCase):
    def test_lwt_bound_before_health_generation_still_invalidates(self):
        c = CurrentContext("victoria", "den_oak")
        c.availability("s", 2, True, 0)
        record = dict(site_id="victoria", camera_id="den_oak", adapter_session="s", occupancy_generation=2,
                      sequence=1, observed_at=1000, valid_until=1180, uncertainty_seconds=.1, description="A person.")
        c.accept(record, now_unix=1000, now_mono=0)
        self.assertTrue(c.availability("s", 1, False, 1))
        self.assertEqual(c.generation, 2)
        self.assertIsNone(c.current(now_unix=1001, now_mono=1))

    def test_clock_rollback_never_revives_expired_context(self):
        c = CurrentContext("victoria", "den_oak")
        record = dict(site_id="victoria", camera_id="den_oak", adapter_session="s", occupancy_generation=1,
                      sequence=1, observed_at=1000, valid_until=1180, uncertainty_seconds=.1, description="A person.")
        c.availability("s", 1, True, 0)
        self.assertTrue(c.accept(record, now_unix=1170, now_mono=0))
        c.availability("s", 1, True, 11)
        self.assertIsNone(c.current(now_unix=1170, now_mono=11))
        self.assertFalse(c.accept(record, now_unix=1170, now_mono=11))

    def test_absolute_expiry_restart_site_and_generation(self):
        c = CurrentContext("victoria", "den_oak")
        record = dict(site_id="victoria", camera_id="den_oak", adapter_session="session", occupancy_generation=1,
                      sequence=1, observed_at=1000, valid_until=1180, uncertainty_seconds=.1, description="A person.")
        self.assertFalse(c.availability("session", 1, True, 0, retained=True))
        self.assertFalse(c.accept(record, now_unix=1170, now_mono=0))
        c.availability("session", 1, True, 0)
        self.assertTrue(c.accept(record, now_unix=1170, now_mono=0))
        self.assertFalse(c.accept(record, now_unix=1175, now_mono=5))
        self.assertIsNone(c.current(now_unix=1180, now_mono=10))
        self.assertIsNone(c.current(now_unix=1175, now_mono=11))
        c.availability("new-session", 2, True, 12)
        self.assertFalse(c.availability("session", 1, True, 13))
        self.assertFalse(c.accept(record, now_unix=1175, now_mono=13))
        self.assertIsNone(CurrentContext("victoria", "den_oak").current(now_unix=1175, now_mono=13))


if __name__ == "__main__":
    unittest.main()
