"""Stationary virtual delivery through public HTTP and the actual Node worker.

Every room, person, optical assumption and fixture is an authored test input.
"""
import copy
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, event, fact, publication_ref
from live_worker_test_helpers import WorkerProbe, worker_receipt_resolver
from live_delivery_test_helpers import delivery_worker_fixture, delivery_geometry


class LiveDeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-live-delivery-", dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.service, self.grants, self.config = delivery_worker_fixture()
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.service,
                               live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.heads = {}
        self.seq = 0
        self.started = False

    def publish_payload(self, payload):
        key = str(payload["head"]["key"])
        if key in self.heads:
            payload["head"]["supersedes"] = publication_ref(self.heads[key])
        status, receipt = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, receipt)
        self.heads[key] = receipt

    def advance(self, mode="supplied", *, geometry=True, presence=False):
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        cue = fact("synthetic-delivery-fact-" + str(self.seq), seq=self.seq)
        if presence:
            cue.update(kind="person_presence", value="present")
            cue["head"]["key"]["predicate"] = "person_presence"
        self.publish_payload(cue)
        if geometry:
            self.publish_payload(delivery_geometry(self.seq, mode=mode))
        if not self.started:
            self.probe.command("start", config=self.config, t=self.now)
            self.started = True
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        return result["decisions"][0]

    def establish(self):
        for _ in range(6):
            decision = self.advance()
        self.assertEqual(decision["scene"]["status"], "selected")
        return decision

    def test_presence_before_focus_keeps_background_need_separate_from_uncommissioned_delivery(self):
        decision = self.advance(presence=True)
        self.assertEqual(decision["background"]["status"], "requested")
        self.assertEqual(decision["background_delivery"], {"status": "unavailable", "reason": "background_fixture_not_commissioned"})
        self.assertEqual(decision["scene"]["status"], "unavailable")
        self.assertEqual(decision["allocation"]["status"], "unavailable")
        self.assertEqual(decision["execution"]["status"], "unavailable")
        self.assertEqual(decision["display_lease"]["status"], "withdrawn")

    def test_awaited_drain_consumes_final_public_cut_and_reports_durable_frontier(self):
        self.establish()
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        cue = fact("synthetic-final-drain-" + str(self.seq), seq=self.seq)
        self.publish_payload(cue)
        through = max(item["cursor"] for item in self.heads.values())
        drained = self.probe.command("drain", t=self.now, through_cursor=through)
        self.assertEqual(drained["status"], "settled", drained)
        self.assertGreaterEqual(drained["input_cursor"], through)
        self.assertIsNone(drained["pending_commit_id"])
        self.assertIsNotNone(drained["last_commit_id"])

    def test_exhausted_drain_deadline_returns_unconfirmed_without_dispatching_final_cut(self):
        self.establish()
        import time
        started = time.monotonic()
        drained = self.probe.command("drain", t=self.now, deadline_ms=0, through_cursor=1000000)
        self.assertEqual(drained['status'], 'unconfirmed', drained)
        self.assertLess(time.monotonic() - started, .5)

    def test_stationary_scene_moves_in_dark_then_settles_and_fades_actual_readback(self):
        first = self.establish()
        self.assertEqual(first["schema"], "home-live-shadow-decision-3")
        self.assertEqual(first["allocation"]["status"], "allocated", first)
        self.assertEqual(first["execution"]["status"], "virtual", first)
        first_sample = first["execution"]["readback"][0]
        self.assertEqual(first_sample["brightness_fraction"], 0)
        rows = [first]
        for _ in range(16):
            rows.append(self.advance())
        lit = [row for row in rows if row["execution"].get("status") == "virtual"
               and row["execution"]["readback"][0]["brightness_fraction"] > 0]
        self.assertTrue(lit, rows)
        self.assertEqual(lit[-1]["execution"]["readback"][0]["brightness_fraction"],
                         first["scene"]["nominal"]["brightness_fraction"])
        self.assertTrue(any(row["execution"]["readback"][0]["pan_deg"] != first_sample["pan_deg"]
                            or row["execution"]["readback"][0]["tilt_deg"] != first_sample["tilt_deg"]
                            for row in rows[1:]))
        for row in rows:
            self.assertEqual(row["scene"]["scene_id"], first["scene"]["scene_id"])
            self.assertEqual(row["allocation"]["geometry_cut_digest"], row["input_cut"]["cut_digest"])
            self.assertEqual(row["execution"]["sample_at"], row["t"])
            self.assertTrue(all(sample["t"] <= row["t"] for sample in row["execution"]["samples"]))
            self.assertEqual(row["display_lease"]["status"], "current")
            self.assertLessEqual(row["display_lease"]["valid_until"], row["execution"]["valid_until"])
            self.assertFalse(row["actuated"])
            self.assertFalse(row["acceptance_eligible"])

    def test_missing_protection_withdraws_delivery_then_recovers_without_new_scene(self):
        first = self.establish()
        for _ in range(5):
            lit = self.advance()
        self.assertGreater(lit["execution"]["readback"][0]["brightness_fraction"], 0)
        missing = self.advance("missing_protection")
        self.assertEqual(missing["scene"]["scene_id"], first["scene"]["scene_id"])
        self.assertEqual(missing["allocation"]["status"], "unavailable")
        self.assertEqual(missing["execution"]["status"], "unavailable")
        self.assertEqual(missing["display_lease"]["status"], "withdrawn")
        recovered = self.advance()
        self.assertEqual(recovered["scene"]["scene_id"], first["scene"]["scene_id"])
        self.assertEqual(recovered["execution"]["readback"][0]["brightness_fraction"], 0)

    def test_clear_centre_ray_cannot_override_the_full_head_protection_envelope(self):
        self.establish()
        blocked = self.advance("head_in_beam")
        self.assertEqual(blocked["scene"]["status"], "selected")
        self.assertEqual(blocked["allocation"]["status"], "unavailable")
        self.assertEqual(blocked["allocation"]["reason"], "protected_volume_intersection")
        self.assertEqual(blocked["execution"]["status"], "unavailable")
        self.assertEqual(blocked["display_lease"]["geometry_refs"], [])

    def test_worker_restart_and_lost_ack_preserve_committed_scene_and_virtual_motion(self):
        first = self.establish()
        self.probe.command("fault", mode="lost_ack")
        moved = self.advance()
        self.assertEqual(moved["scene"]["scene_id"], first["scene"]["scene_id"])
        self.probe.command("stop")
        self.started = False
        restarted = self.advance()
        self.assertEqual(restarted["scene"]["scene_id"], first["scene"]["scene_id"])
        self.assertEqual(restarted["execution"]["status"], "virtual", restarted)
        self.assertEqual(restarted["execution"]["continuity"], "continuous")
        self.assertGreater(restarted["execution"]["readback"][0]["pan_deg"],
                           moved["execution"]["readback"][0]["pan_deg"])

    def test_no_new_geometry_cannot_refresh_a_virtual_beam_when_old_parts_expire(self):
        self.establish()
        for _ in range(5):
            lit = self.advance()
        self.assertGreater(lit["execution"]["readback"][0]["brightness_fraction"], 0)
        # Interaction remains observed, but it cannot renew a spatial estimate.
        for _ in range(11):
            expired = self.advance(geometry=False)
        self.assertEqual(expired["execution"]["status"], "unavailable")
        self.assertEqual(expired["display_lease"]["status"], "withdrawn")
