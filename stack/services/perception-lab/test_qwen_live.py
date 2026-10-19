"""Victoria live perceiver: frame source, receipts ledger, transport refusals and
the recording-owned pump. Temp store, scripted transports; no network or model."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

import qwen_live as live
import qwen_scene as scene
from config import Settings
from qwen_transport import CompletionUnproven, QwenNotExecuted
from store import Store
from test_qwen_transport import Server

PROFILE = {"model_version": scene.MODEL, "served_model": scene.SERVED_MODEL, "artifact_sha256": "a" * 64,
           "engine_epoch": "boot-epoch", "runtime_revision": "fixture-runtime", "adapter_revision": scene.PROMPT_VERSION}
FACTS = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book", "book_state": "open",
         "gaze_target": "book", "typing_visible": "no", "screen_facing_subject": "not_visible",
         "activity_hypothesis": "reading", "visible_basis": ["person_visible", "book_in_hands"],
         "change_vs_prior": "no_prior"}
OCCUPIED = lambda: {"state": "occupied", "reason": "person_detected"}  # the occupancy gate as the pumps see it
TRANSPORT_CONFIG = {"hostname": "la-lab.example", "port": 9448, "ca_file": "/fixture/ca.pem",
                    "client_certificate": "/fixture/client.pem", "client_key": "/fixture/client.key",
                    "deadline_seconds": 7, "response_grace_seconds": 3, "poll_seconds": .5}


def jpeg(size=scene.IMAGE_SIZE, colour=(40, 50, 60)):
    from PIL import Image
    encoded = io.BytesIO()
    Image.new("RGB", size, colour).save(encoded, format="JPEG")
    return encoded.getvalue()


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class StoreFixture:
    def build_store(self, now=1000.0):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = Clock(now)
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0), clock=self.clock)
        self.session, _ = self.store.create_session("den", "detection", "Fixture", "request1")
        self.store.transition(self.session["id"], "starting")
        self.store.transition(self.session["id"], "recording")
        self.session = self.store.get_session(self.session["id"])
        self.sequence = 0

    def close_store(self):
        self.store.close()
        self.directory.cleanup()

    def frame(self, observed, *, epoch="cap-1", camera="den", verified=True, size=scene.IMAGE_SIZE, data=None):
        """A registered preview frame exactly as PreviewIngestor stores it."""
        self.sequence += 1
        meta = {"site_id": "victoria", "camera_id": camera, "capture_epoch": epoch, "frame_id": f"{epoch}-0-{self.sequence}",
                "sequence": self.sequence, "stream": "rgb", "width": size[0], "height": size[1], "format": "jpeg",
                "observed_at": observed, "available_at": observed + .3, "valid_until": observed + 2.5,
                "uncertainty_seconds": .5, "clock_mapping_status": "verified" if verified else "unverified",
                "clock_mapping_id": "clock-1", "session_time_s": observed - self.session["started_at"]}
        return self.store.add_media(self.session["id"], data or jpeg(size, (self.sequence % 250, 50, 60)), "image/jpeg", meta,
                                    f'["{epoch}","rgb_preview",0,{self.sequence}]')

    def frames_every_half_second(self, start, end):
        t = start
        while t <= end + 1e-9:
            self.frame(round(t, 3))
            t += .5

    def events(self, kind):
        return [e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"] == kind]


class FrameSourceTests(StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.source = live.RegisteredPreviewFrames(self.store)

    def tearDown(self):
        self.close_store()

    def test_selects_integrity_checked_frames_spanning_the_window_oldest_first(self):
        self.frames_every_half_second(990.0, 999.5)
        self.frame(999.8, camera="other")  # another camera's row is never chosen
        self.frame(999.9, verified=False)  # unverified clock mapping is never chosen
        frames = self.source.select(self.session, 1000.0, count=3, span=3.0)
        self.assertEqual([f["observed_at"] for f in frames], [996.5, 998.0, 999.5])
        for frame in frames:
            self.assertEqual(frame["data"][:2], b"\xff\xd8")
            self.assertEqual(frame["capture_epoch"], "cap-1")
        four = self.source.select(self.session, 1000.0, count=4, span=3.0)
        self.assertEqual([f["observed_at"] for f in four], [996.5, 997.5, 998.5, 999.5])

    def test_future_or_stale_frames_are_not_used(self):
        self.frames_every_half_second(990.0, 996.0)
        self.frame(1001.0)  # not yet available at 1000
        with self.assertRaises(live.FramesUnavailable) as caught:
            self.source.select(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_stale")

    def test_missing_or_insufficient_frames_are_typed(self):
        with self.assertRaises(live.FramesUnavailable) as caught:
            self.source.select(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_unavailable")
        self.frame(999.5)
        self.frame(999.0, epoch="cap-0")  # a different capture epoch never joins the window
        with self.assertRaises(live.FramesUnavailable) as caught:
            self.source.select(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_insufficient")

    def test_changed_bytes_fail_the_integrity_check(self):
        self.frames_every_half_second(996.0, 999.5)
        latest = self.source.select(self.session, 1000.0)[-1]
        path = self.store.media_path(self.session["id"], latest["media_id"])
        path.write_bytes(jpeg(colour=(1, 2, 3)))
        with self.assertRaises(live.FramesUnavailable) as caught:
            self.source.select(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frame_integrity_failed")


def draft_for(test, observed, *, request=None, content=None, generated=None):
    frames = [{"media_id": "m%d" % i, "frame_id": "f%d" % i, "capture_epoch": "cap-1", "observed_at": observed - 3 + 1.5 * i}
              for i in range(3)]
    return scene.build_observation(site_id="victoria", camera_id="den", session_id=test.session["id"],
                                   request_id=request or scene.digest(observed), frames=frames,
                                   generated_at=observed + 1 if generated is None else generated,
                                   prompt_version=scene.PROMPT_VERSION,
                                   context=scene.model_context("evening 19:40"), body_sha256="b" * 64,
                                   engine_epoch="epoch-1", artifact_sha256="a" * 64,
                                   content=content or json.dumps(FACTS), finish_reason="stop", prior_supplied=False)


class LedgerTests(StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.ledger = live.QwenSceneLedger(self.store)

    def tearDown(self):
        self.close_store()

    def test_first_receipt_is_durable_and_duplicates_do_not_refresh(self):
        sid = self.session["id"]
        first = self.ledger.record(sid, draft_for(self, 999.0))
        self.assertEqual(first["state"], "available")
        self.clock.now = 1003.0
        again = self.ledger.record(sid, draft_for(self, 999.0))
        self.assertEqual(again, first)
        [saved] = self.events("qwen.observation")
        self.assertEqual((saved["available_at"], saved["first_received_at"], saved["valid_until"]), (1000.0, 1000.0, 1009.0))
        self.clock.now = 1010.0
        self.assertEqual(self.ledger.record(sid, draft_for(self, 999.0))["state"], "expired")
        self.assertEqual(len(self.events("qwen.observation")), 1)

    def test_changed_content_for_one_request_is_a_durable_conflict_once(self):
        sid = self.session["id"]
        self.ledger.record(sid, draft_for(self, 999.0, request="c" * 64))
        changed = json.dumps({**FACTS, "held_object": "phone"})
        self.clock.now = 1001.0
        conflict = self.ledger.record(sid, draft_for(self, 999.0, request="c" * 64, content=changed))
        self.assertEqual(conflict["state"], "identity_conflict")
        self.assertEqual(self.ledger.record(sid, draft_for(self, 999.0, request="c" * 64, content=changed)), conflict)
        [event] = self.events("qwen.conflict")
        self.assertNotIn("phone", json.dumps(event))
        receipts, conflicts = self.ledger.receipts(self.session, 1000.5)
        self.assertEqual((len(receipts), conflicts), (1, []))
        receipts, conflicts = self.ledger.receipts(self.session, 1001.0)
        self.assertEqual(scene.projection(receipts, 1001.0, conflicts=conflicts)["latest"], None)

    def test_out_of_order_and_expired_receipts_are_rejected_without_facts(self):
        sid = self.session["id"]
        self.ledger.record(sid, draft_for(self, 999.0))
        self.assertEqual(self.ledger.record(sid, draft_for(self, 998.0))["state"], "out_of_order")
        self.clock.now = 1012.0
        self.assertEqual(self.ledger.record(sid, draft_for(self, 1001.0))["state"], "expired_on_receipt")
        rejected = self.events("qwen.rejected")
        self.assertEqual([r["reason"] for r in rejected], ["out_of_order", "expired_on_receipt"])
        self.assertTrue(all(r["facts"] is None for r in rejected))
        # A rejection is never selectable evidence.
        self.assertEqual(len(self.ledger.receipts(self.session, 1012.0)[0]), 1)

    def test_only_an_active_matching_recording_accepts_receipts(self):
        other = {**draft_for(self, 999.0), "camera_id": "kitchen"}
        with self.assertRaises(ValueError):
            self.ledger.record(self.session["id"], other)
        self.store.transition(self.session["id"], "stopping")
        with self.assertRaises(ValueError):
            self.ledger.record(self.session["id"], draft_for(self, 999.0))

    def test_receipts_and_context_are_causal_and_identical_for_both_arms(self):
        sid = self.session["id"]
        for t in range(960, 1000, 4):
            self.clock.now = t + 1.5
            self.ledger.record(sid, draft_for(self, float(t)))
        before = live.scene_context(self.store, self.session, 990.0, generation=2, arms={"a", "b"})
        self.clock.now = 1001.0
        self.ledger.record(sid, draft_for(self, 1000.0))
        self.assertEqual(live.scene_context(self.store, self.session, 990.0, generation=2, arms={"a", "b"}), before)
        strip = lambda value: {k: v for k, v in value.items() if k != "arm"}
        self.assertEqual(strip(before["a"][0]), strip(before["b"][0]))
        self.assertEqual(before["a"][0]["qwen_scene"]["status"], "supplied")
        self.assertEqual(live.scene_context(self.store, self.session, 990.0, generation=2, arms={"b"}).keys(), {"b"})


class TransportTests(unittest.TestCase):
    def transport(self, server):
        return live.LiveSceneTransport(dict(TRANSPORT_CONFIG), connection_factory=server.factory, clock=lambda: 1000.0,
                                       sleep=lambda seconds: None)

    def envelope(self):
        body = {"model": "qwen3-vl-30b", "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": [
            {"type": "text", "text": "{}"}, {"type": "text", "text": "{}"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/"}}]}]}
        return {"request_id": "e" * 64, "site_id": "victoria", "session_id": "f" * 32, "profile": dict(PROFILE),
                "body": body, "body_sha256": scene.digest(body), "prompt_version": scene.PROMPT_VERSION,
                "task": scene.TASK, "deadline": 1005.0}

    def test_live_paths_and_exact_pre_admission_refusals(self):
        self.assertIn((503, "thermal_shed"), live.PRE_EXECUTION)
        for status, reason in sorted(live.PRE_EXECUTION):
            server = Server((status, {"reason": reason}))
            with self.assertRaises(QwenNotExecuted) as caught:
                self.transport(server).submit(self.envelope())
            self.assertEqual(str(caught.exception), reason)
            self.assertEqual(server.requests[0][1], live.SUBMIT_PATH)
        # A whole call refused for heat: nothing to recover, so no identity lookup.
        server = Server((503, {"reason": "thermal_shed"}))
        with self.assertRaises(QwenNotExecuted):
            self.transport(server)(self.envelope())
        self.assertEqual([r[1] for r in server.requests], [live.SUBMIT_PATH])
        for reply in ((429, {"reason": "slow_down"}), (503, {"reason": "admission_unavailable"}),
                      (429, {"reason": "perceiver_busy", "detail": 1}), (500, {"reason": "consumer_error"})):
            with self.assertRaises(CompletionUnproven):
                self.transport(Server(reply)).submit(self.envelope())

    def test_lookup_uses_the_live_path_and_the_saved_proof_contract(self):
        envelope = self.envelope()
        reference = {k: v for k, v in envelope.items() if k != "body"}
        proof = {"version": 1, "request_id": "e" * 64, "body_sha256": envelope["body_sha256"], "terminal": True,
                 "state": "queue_expired", "engine_epoch": "epoch", "artifact_sha256": "a" * 64, "reason": None}
        server = Server((200, proof))
        self.assertEqual(self.transport(server).lookup(reference)["state"], "queue_expired")
        self.assertEqual(server.requests[0][1], live.LOOKUP_PATH)
        self.assertNotIn("body", server.requests[0][2])

    def test_the_reconcile_outcome_is_a_terminal_proof_without_an_answer(self):
        envelope = self.envelope()
        reference = {k: v for k, v in envelope.items() if k != "body"}
        base = {"version": 1, "request_id": "e" * 64, "body_sha256": envelope["body_sha256"], "terminal": True,
                "engine_epoch": "epoch", "artifact_sha256": "a" * 64}
        for proof in ({**base, "state": "failed", "reason": "unknown_resolved"},  # LA's shared outcome mapping
                      {**base, "state": "unknown_resolved", "reason": None}):
            with self.subTest(state=proof["state"]):
                self.assertEqual(self.transport(Server((200, proof))).lookup(reference), proof)
        for proof in ({**base, "state": "unknown_resolved", "reason": None, "terminal": False},
                      {**base, "state": "unknown_resolved", "reason": None, "response": {"choices": []}}):
            with self.assertRaises(CompletionUnproven):
                self.transport(Server((200, proof))).lookup(reference)
        # The saved-activity transport keeps its own exact states.
        from qwen_transport import QwenTransport
        with self.assertRaises(CompletionUnproven):
            QwenTransport(dict(TRANSPORT_CONFIG), connection_factory=Server((200, {**base, "state": "unknown_resolved",
                          "reason": None})).factory, clock=lambda: 1000.0).lookup(reference)


class FakeTransport:
    """Scripted admitted transport: each call pops one outcome."""
    def __init__(self, *outcomes, lookups=()):
        self.config = dict(TRANSPORT_CONFIG)
        self.outcomes, self.lookups = list(outcomes), list(lookups)
        self.envelopes, self.references, self.closed = [], [], False

    def __call__(self, envelope):
        self.envelopes.append(envelope)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(envelope) if callable(outcome) else outcome

    def lookup(self, reference):
        self.references.append(reference)
        if "engine_epoch_probe" in json.dumps(reference) or reference["body_sha256"] == scene.digest({"engine_epoch_probe": True}):
            return {"version": 1, "request_id": reference["request_id"], "body_sha256": reference["body_sha256"],
                    "terminal": False, "state": "not_found", "engine_epoch": "la-epoch", "artifact_sha256": "a" * 64,
                    "reason": None}
        outcome = self.lookups.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(reference) if callable(outcome) else outcome

    def close(self):
        self.closed = True


def completed(facts=None, *, finish="stop", epoch="la-epoch", text=None):
    def answer(envelope):
        return {"version": 1, "request_id": envelope["request_id"], "body_sha256": envelope["body_sha256"], "terminal": True,
                "state": "completed", "engine_epoch": epoch, "artifact_sha256": "a" * 64, "reason": None,
                "usage": {"prompt_tokens": 900, "completion_tokens": 80},
                "response": {"id": "chatcmpl-x", "model": "qwen3-vl-30b", "choices": [{"index": 0, "finish_reason": finish,
                             "message": {"role": "assistant", "content": text if text is not None else json.dumps(facts or FACTS)}}]}}
    return answer


def terminal(state, reason=None):
    return lambda envelope: {"version": 1, "request_id": envelope["request_id"], "body_sha256": envelope["body_sha256"],
                             "terminal": state not in {"indeterminate", "running", "queued"}, "state": state,
                             "engine_epoch": "la-epoch", "artifact_sha256": "a" * 64, "reason": reason}


class PumpTests(StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.mono = Clock(50.0)
        self.frames_every_half_second(990.0, 999.5)
        self.switch = {"on": True}

    def tearDown(self):
        self.close_store()

    def pump(self, transport, *, indeterminate_seconds=None, **config):
        return live.QwenLivePump(self.store, self.session, transport=transport, profile=dict(PROFILE), config=config or None,
                                 clock=self.clock, monotonic=self.mono, local_time=lambda epoch: (19, 40),
                                 enabled=lambda: self.switch["on"], occupied=OCCUPIED,
                                 indeterminate_seconds=indeterminate_seconds)

    def advance(self, seconds):
        self.clock.now += seconds
        self.mono.now += seconds
        self.frames_every_half_second(self.clock.now - seconds + .5, self.clock.now - .5)

    def test_one_tick_sends_latest_frames_and_records_validated_enum_facts(self):
        transport = FakeTransport(completed())
        pump = self.pump(transport)
        self.assertEqual(pump.tick(), {"state": "current", "reason": "recorded_valid"})
        [envelope] = transport.envelopes
        self.assertEqual((envelope["task"], envelope["prompt_version"], envelope["deadline"]), (scene.TASK, scene.PROMPT_VERSION, 1007.0))
        self.assertEqual(envelope["profile"]["engine_epoch"], "la-epoch")  # taken from the identity-only probe
        body = envelope["body"]
        self.assertEqual(envelope["body_sha256"], scene.digest(body))
        self.assertEqual(body["response_format"], scene.response_format())
        context = json.loads(body["messages"][1]["content"][0]["text"])
        self.assertEqual(context, {"placement": scene.PLACEMENT, "local_time": "evening 19:40", scene.PRIOR_KEY: None})
        self.assertEqual(len(body["messages"][1]["content"]), 7)  # context plus three frames
        [saved] = self.events("qwen.observation")
        self.assertEqual((saved["status"], saved["facts"], saved["observed_at"], saved["window_start"]),
                         ("valid", FACTS, 999.5, 996.5))
        self.assertEqual((saved["available_at"], saved["generated_at"], saved["valid_until"]), (1000.0, 1000.0, 1009.5))
        self.assertEqual(saved["engine_epoch"], "la-epoch")
        self.assertNotIn("content", json.dumps(saved))

    def test_the_prior_output_is_not_sent_by_default(self):
        # Live 2026-09-30: the model anchored on its own earlier reading.
        transport = FakeTransport(completed(), completed())
        pump = self.pump(transport)
        pump.tick()
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        context = json.loads(transport.envelopes[1]["body"]["messages"][1]["content"][0]["text"])
        self.assertIsNone(context[scene.PRIOR_KEY])
        self.assertEqual(self.events("qwen.observation")[1]["prior_supplied"], False)

    def test_the_prior_output_is_offered_only_for_change_vs_prior(self):
        transport = FakeTransport(completed(), completed({**FACTS, "change_vs_prior": "no_change"}))
        pump = self.pump(transport, send_prior=True)
        pump.tick()
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        context = json.loads(transport.envelopes[1]["body"]["messages"][1]["content"][0]["text"])
        self.assertEqual(context[scene.PRIOR_KEY], FACTS)
        self.assertEqual(self.events("qwen.observation")[1]["prior_supplied"], True)

    def test_invalid_and_unbound_replies_are_recorded_without_facts_and_never_become_the_prior(self):
        transport = FakeTransport(completed(text="The person is reading."), completed(epoch="other-epoch"),
                                  completed(finish="length"), completed())
        pump = self.pump(transport)
        reasons = []
        for _ in range(4):
            reasons.append(pump.tick()["reason"])
            self.advance(4)
        self.assertEqual(reasons, ["recorded_invalid_not_json", "recorded_invalid_response_unbound",
                                   "recorded_invalid_finish_not_stop", "recorded_valid"])
        stored = self.events("qwen.observation")
        self.assertEqual([s["facts"] for s in stored[:3]], [None, None, None])
        self.assertNotIn("The person is reading", json.dumps(stored))
        self.assertIsNone(json.loads(transport.envelopes[3]["body"]["messages"][1]["content"][0]["text"])[scene.PRIOR_KEY])

    def test_typed_refusals_skip_the_tick_and_closed_admission_backs_off(self):
        transport = FakeTransport(QwenNotExecuted("interactive_queued"), QwenNotExecuted("admission_closed"), completed())
        pump = self.pump(transport)
        self.assertEqual(pump.tick()["reason"], "interactive_queued")
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "admission_closed")
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "admission_closed_backoff")
        self.assertEqual(len(transport.envelopes), 2)
        self.advance(live.BACKOFF_SECONDS)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(self.events("qwen.observation")[0]["status"], "valid")

    def test_a_thermal_shed_backs_off_like_closed_admission_with_no_ledger_write(self):
        transport = FakeTransport(QwenNotExecuted("thermal_shed"), completed())
        pump = self.pump(transport)
        self.assertEqual(pump.tick(), {"state": "skipped", "reason": "thermal_shed"})
        for _ in range(3):
            self.advance(4)
            self.assertEqual(pump.tick()["reason"], "thermal_shed_backoff")
        self.assertEqual(len(transport.envelopes), 1)
        self.assertEqual((pump.outstanding, pump.snapshot()["counts"]["thermal_shed_backoff"]), (None, 3))
        for kind in live.EVENT_KINDS:
            self.assertEqual(self.events(kind), [])  # not sent, nothing recorded
        self.advance(live.BACKOFF_SECONDS)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(len(transport.envelopes), 2)

    def test_terminal_non_answers_are_typed_skips(self):
        transport = FakeTransport(terminal("queue_expired"), terminal("rejected", "capacity"), terminal("not_admitted"))
        pump = self.pump(transport)
        reasons = []
        for _ in range(3):
            reasons.append(pump.tick()["reason"])
            self.advance(4)
        self.assertEqual(reasons, ["la_queue_expired", "la_rejected_capacity", "la_not_admitted"])
        self.assertEqual(self.events("qwen.observation"), [])

    def test_an_unproven_outcome_blocks_new_work_until_a_lookup_resolves_it(self):
        transport = FakeTransport(CompletionUnproven("lost"), completed(),
                                  lookups=[CompletionUnproven("down"), terminal("running"), completed()])
        pump = self.pump(transport)
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        for expected in ("outcome_unproven", "awaiting_terminal"):
            self.advance(4)
            self.assertEqual(pump.tick()["reason"], expected)
        self.assertEqual(len(transport.envelopes), 1)  # never resubmitted
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "receipt_expired_on_receipt")  # resolved late: stale on receipt
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(len(transport.envelopes), 2)

    def test_an_indeterminate_la_row_halts_the_recording_pump_with_no_further_la_call(self):
        # The recording pump's owner-reviewed semantics (plan S1.3(c) changes
        # only the always-on perceiver): halted for the rest of the recording.
        transport = FakeTransport(CompletionUnproven("lost"), completed(),
                                  lookups=[terminal("indeterminate"), terminal("failed", "unknown_resolved")])
        pump = self.pump(transport)
        self.assertIsNone(pump.indeterminate_seconds)
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        self.advance(4)
        self.assertEqual(pump.tick(), {"state": "halted", "reason": "halted_la_indeterminate"})
        lookups = len(transport.references)
        for _ in range(30):  # two minutes: no lookup, no probe and never a new submission
            self.advance(4)
            self.assertEqual(pump.tick(), {"state": "halted", "reason": "halted_la_indeterminate"})
        self.switch["on"] = False
        self.assertEqual(pump.tick()["reason"], "halted_la_indeterminate")  # the halt comes first, as before
        self.assertEqual((len(transport.references), len(transport.envelopes)), (lookups, 1))
        snapshot = pump.snapshot()
        self.assertEqual((snapshot["halted"], snapshot["status"], snapshot["outstanding"]),
                         ("halted_la_indeterminate", "halted", True))
        for kind in live.EVENT_KINDS:
            self.assertEqual(self.events(kind), [])

    def indeterminate_then(self, resolved):
        transport = FakeTransport(CompletionUnproven("lost"), completed(),
                                  lookups=[terminal("indeterminate"), terminal("indeterminate"), resolved])
        pump = self.pump(transport, indeterminate_seconds=live.BACKOFF_SECONDS)
        pump.tick()
        self.advance(4)
        self.assertEqual(pump.tick(), {"state": "awaiting_terminal", "reason": "la_indeterminate"})
        self.assertEqual(pump.snapshot()["halted"], "la_indeterminate")
        lookups = len(transport.references)
        for _ in range(5):  # within the re-probe interval: no lookup and never a new submission
            self.advance(4)
            self.assertEqual(pump.tick()["reason"], "la_indeterminate_backoff")
        self.assertEqual((len(transport.references), len(transport.envelopes)), (lookups, 1))
        self.advance(live.BACKOFF_SECONDS)
        self.assertEqual(pump.tick()["reason"], "la_indeterminate")  # still open on LA
        self.advance(live.BACKOFF_SECONDS)
        # LA's reconcile ended the row with no answer: no fact, and the pump resumes.
        self.assertEqual(pump.tick(), {"state": "skipped", "reason": "la_unknown_resolved"})
        self.assertEqual((pump.outstanding, pump.snapshot()["halted"]), (None, None))
        for kind in live.EVENT_KINDS:
            self.assertEqual(self.events(kind), [])
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")  # no leftover wait after the pause
        self.assertEqual(len(transport.envelopes), 2)

    def test_with_a_reprobe_interval_an_indeterminate_row_pauses_until_la_reconciles_it(self):
        self.indeterminate_then(terminal("failed", "unknown_resolved"))  # LA's shared outcome mapping

    def test_an_unknown_resolved_state_also_ends_the_pause(self):
        self.indeterminate_then(terminal("unknown_resolved"))

    def test_a_failed_or_unusable_reprobe_still_waits_the_whole_interval(self):
        transport = FakeTransport(CompletionUnproven("lost"), completed(),
                                  lookups=[terminal("indeterminate"), CompletionUnproven("down"),
                                           lambda reference: {"request_id": "other"}, terminal("running"),
                                           terminal("failed", "unknown_resolved")])
        pump = self.pump(transport, indeterminate_seconds=60)
        pump.tick()
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "la_indeterminate")
        for expected in ("outcome_unproven", "outcome_unproven", "awaiting_terminal"):
            self.advance(60)
            before = len(transport.references)
            self.assertEqual(pump.tick()["reason"], expected)  # the re-probe at 60 s fails or is not terminal
            self.assertEqual(len(transport.references), before + 1)
            for _ in range(14):  # 56 s: no further lookup until the next interval
                self.advance(4)
                self.assertEqual(pump.tick()["reason"], "la_indeterminate_backoff")
            self.assertEqual(len(transport.references), before + 1)
            self.assertEqual(pump.snapshot()["halted"], "la_indeterminate")  # still paused
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "la_unknown_resolved")
        self.assertEqual((len(transport.envelopes), pump.outstanding), (1, None))

    def test_an_unknown_resolved_reply_to_the_submission_itself_is_terminal_with_no_fact(self):
        transport = FakeTransport(terminal("failed", "unknown_resolved"), completed())
        pump = self.pump(transport)
        self.assertEqual(pump.tick()["reason"], "la_unknown_resolved")
        self.assertIsNone(pump.outstanding)
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")

    def test_rate_cap_latest_frame_wins_and_kill_switch(self):
        # The prior is offered only while recent: after a minute's gap it is no_prior again.
        transport = FakeTransport(completed(), completed({**FACTS, "change_vs_prior": "no_change"}), completed())
        pump = self.pump(transport, max_per_minute=2)
        pump.tick()
        self.assertEqual(pump.tick()["reason"], "no_new_frame")  # same latest frame: nothing sent
        self.advance(4)
        pump.tick()
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "rate_capped")
        self.advance(60)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(transport.envelopes[-1]["body_sha256"], scene.digest(transport.envelopes[-1]["body"]))
        latest = json.loads(transport.envelopes[-1]["body"]["messages"][1]["content"][-2]["text"])
        self.assertEqual(latest["seconds_before_latest"], 0.0)
        self.switch["on"] = False
        self.advance(4)
        self.assertEqual(pump.tick(), {"state": "disabled", "reason": "disabled"})
        self.assertEqual(len(transport.envelopes), 3)

    def test_the_widest_cadence_still_sends_one_at_a_time_and_only_new_frames(self):
        transport = FakeTransport(completed(), CompletionUnproven("lost"), completed(),
                                  lookups=[terminal("running"), completed()])
        pump = self.pump(transport, interval_seconds=.5, max_per_minute=120)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(pump.tick()["reason"], "no_new_frame")  # the interval never outruns the frames
        self.advance(1)
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        self.advance(1)
        self.assertEqual(pump.tick()["reason"], "awaiting_terminal")  # one in flight: reconciled, nothing sent
        self.assertEqual(len(transport.envelopes), 2)
        self.advance(1)
        pump.tick()
        self.advance(1)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(len(transport.envelopes), 3)

    def test_skips_when_not_recording_or_frames_are_stale(self):
        transport = FakeTransport()
        pump = self.pump(transport)
        self.clock.now += 30
        self.assertEqual(pump.tick()["reason"], "frames_stale")
        self.store.transition(self.session["id"], "stopping")
        self.assertEqual(pump.tick()["reason"], "not_recording")
        self.assertEqual(transport.envelopes, [])

    def test_a_different_la_artifact_is_never_used(self):
        transport = FakeTransport()
        transport.lookup = lambda reference: {"request_id": reference["request_id"], "state": "not_found",
                                              "engine_epoch": "e", "artifact_sha256": "9" * 64}
        self.assertEqual(self.pump(transport).tick()["reason"], "artifact_mismatch")
        self.assertEqual(transport.envelopes, [])

    def test_configuration_bounds(self):
        for config in ({"interval_seconds": .4}, {"interval_seconds": 11}, {"frames": 1}, {"frames": 5},
                       {"max_per_minute": 121}, {"max_per_minute": 0}, {"max_per_minute": 90.0}, {"span_seconds": 5},
                       {"horizon_seconds": 90}, {"surprise": 1}):
            with self.assertRaises(ValueError):
                live.pump_config(config)
        # Defaults unchanged (4 s, 15/min); the bounds now reach S1.4's 1.0 s and 90/min.
        self.assertEqual({k: live.pump_config()[k] for k in ("interval_seconds", "max_per_minute")},
                         {"interval_seconds": 4.0, "max_per_minute": 15})
        for config in ({"interval_seconds": .5}, {"interval_seconds": 1.0, "max_per_minute": 90},
                       {"interval_seconds": 10, "max_per_minute": 120}):
            self.assertEqual({k: live.pump_config(config)[k] for k in config}, config)
        slow = FakeTransport()
        slow.config["deadline_seconds"] = 15
        with self.assertRaises(ValueError):
            self.pump(slow)
        with self.assertRaises(ValueError):
            live.QwenLivePump(self.store, self.session, transport=FakeTransport(),
                              profile={**PROFILE, "adapter_revision": "lab-qwen-cutoff-2"}, occupied=OCCUPIED)

    def test_thread_starts_and_stops_cleanly_with_the_recording(self):
        transport = FakeTransport()
        pump = live.QwenLivePump(self.store, self.session, transport=transport, profile=dict(PROFILE),
                                 config={"interval_seconds": 3}, local_time=lambda epoch: (19, 40), occupied=OCCUPIED)
        pump.start()
        started = time.monotonic()
        pump.stop(timeout=2)
        self.assertFalse(pump.thread.is_alive())
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(transport.closed)
        self.assertEqual(transport.envelopes, [])
        self.assertEqual(pump.tick()["reason"], "stopped")


class TerminalLedger:
    """A ledger with terminal() (as la_fact_log.FactLog): every LA terminal outcome."""
    def __init__(self, fail_outcomes=False):
        self.calls, self.fail_outcomes = [], fail_outcomes

    def terminal(self, request, outcome, draft):
        self.calls.append((request["reference"]["request_id"], request["sent_at"], outcome, draft))
        if draft is None and self.fail_outcomes:
            raise OSError("disk full")
        return {"state": "available", "event_id": None}


class ReuseHookTests(StoreFixture, unittest.TestCase):
    """The always-on perceiver's injection points; the recording pump's defaults stay as they were."""

    def setUp(self):
        self.build_store()
        self.mono = Clock(50.0)
        self.frames_every_half_second(990.0, 999.5)

    def tearDown(self):
        self.close_store()

    def pump(self, transport, **kwargs):
        return live.QwenLivePump(self.store, self.session, transport=transport, profile=dict(PROFILE), clock=self.clock,
                                 monotonic=self.mono, local_time=lambda epoch: (19, 40), occupied=OCCUPIED, **kwargs)

    def test_a_terminal_ledger_gets_every_outcome_with_its_send_time(self):
        ledger = TerminalLedger()
        transport = FakeTransport(completed(), terminal("failed", "unknown_resolved"), terminal("rejected", "capacity"))
        pump = self.pump(transport, ledger=ledger)
        reasons = []
        for _ in range(3):
            reasons.append(pump.tick()["reason"])
            self.clock.now += 4
            self.mono.now += 4
            self.frames_every_half_second(self.clock.now - 3.5, self.clock.now - .5)
        self.assertEqual(reasons, ["recorded_valid", "la_unknown_resolved", "la_rejected_capacity"])
        self.assertEqual([(c[2], c[3] is None) for c in ledger.calls],
                         [("completed", False), ("unknown_resolved", True), ("rejected", True)])
        self.assertEqual([c[0] for c in ledger.calls], [e["request_id"] for e in transport.envelopes])
        self.assertEqual([c[1] for c in ledger.calls], [1000.0, 1004.0, 1008.0])  # Victoria clock, at the send
        self.assertEqual(ledger.calls[0][3]["status"], "valid")
        for kind in live.EVENT_KINDS:
            self.assertEqual(self.events(kind), [])  # nothing in the recording's ledger
        self.assertNotIn("sent_at", transport.envelopes[0])  # never sent to LA

    def test_a_failing_outcome_line_never_stops_the_pump(self):
        transport = FakeTransport(terminal("queue_expired"), completed())
        pump = self.pump(transport, ledger=TerminalLedger(fail_outcomes=True))
        self.assertEqual(pump.tick()["reason"], "la_queue_expired")
        self.assertEqual(pump.snapshot()["counts"]["record_failed"], 1)
        self.clock.now += 4
        self.mono.now += 4
        self.frames_every_half_second(self.clock.now - 3.5, self.clock.now - .5)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")

    def test_the_indeterminate_reprobe_interval_is_opt_in_and_bounded(self):
        self.assertIsNone(self.pump(FakeTransport()).indeterminate_seconds)  # the recording pump halts
        self.assertIsNone(live.QwenLivePump.__init__.__kwdefaults__["indeterminate_seconds"])
        self.assertEqual(self.pump(FakeTransport(), indeterminate_seconds=60).indeterminate_seconds, 60)
        for bad in (0, 601, float("nan"), "60"):
            with self.assertRaises(ValueError):
                self.pump(FakeTransport(), indeterminate_seconds=bad)

    def test_only_a_frame_source_that_reports_new_frames_wakes_a_pump_waiting_for_frames(self):
        recording = self.pump(FakeTransport())
        self.assertFalse(recording.frame_wake)

        class Ring(live.RegisteredPreviewFrames):
            def __init__(self, store):
                super().__init__(store)
                self.events = set()

            def subscribe(self, event):
                self.events.add(event)

            def unsubscribe(self, event):
                self.events.discard(event)
        ring = Ring(self.store)
        always_on = self.pump(FakeTransport(), frames=ring)
        self.assertTrue(always_on.frame_wake)
        self.assertEqual(ring.events, {always_on.wake})
        for reason, recording_wakes, ring_wakes in (("frames_unavailable", False, True), ("no_new_frame", False, True),
                                                    ("no_person", True, True), ("recorded_valid", False, False),
                                                    ("rate_capped", False, False)):
            recording.last_reason = always_on.last_reason = reason
            self.assertEqual((recording._woken(), always_on._woken()), (recording_wakes, ring_wakes), reason)
        always_on.stop()
        self.assertEqual(ring.events, set())


if __name__ == "__main__":
    unittest.main()
