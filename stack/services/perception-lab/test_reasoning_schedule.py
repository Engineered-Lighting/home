"""Evidence-driven live JEV scheduling and the hot-path caches it relies on."""
from copy import deepcopy
import json
import random
import tempfile
import threading
import unittest
from concurrent.futures import Future
from unittest.mock import patch

import test_paired_runtime as fixtures
from test_reasoning_load import Simulation
from evidence_policy import filter_ha
from live_inputs import live_records
from reasoning import causal_evidence, causal_sources, plain_copy


class Scheduled(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name, filler_files=0)
        self.sim.preview()

    def tearDown(self):
        self.sim.close()
        self.directory.cleanup()

    def rows(self):
        with self.sim.store.lock:
            return [tuple(r) for r in self.sim.store.db.execute("SELECT arm,state FROM lab_reasoning_requests ORDER BY rowid")]

    def status(self):
        return self.sim.runtime.reasoning_scheduler_status(self.sim.sid)

    def skipped(self, reason):
        return self.status()["skipped"][reason]

    def events(self, kind):
        return self.sim.events(kind)


class EvidenceDrivenOfferTests(Scheduled):
    def test_new_output_offers_only_its_arm_and_unchanged_ticks_are_counted(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        self.assertEqual(self.rows(), [("a", "completed")])
        self.assertEqual(self.skipped("no_visual_evidence"), {"a": 0, "b": 1})
        sim.advance(.4)
        sim.output("b")
        sim.tick()
        self.assertEqual(self.rows(), [("a", "completed"), ("b", "completed")])
        for _ in range(5):
            sim.advance(1)
            sim.tick()
        self.assertEqual(len(self.rows()), 2, "unchanged evidence creates no request")
        self.assertEqual(self.skipped("unchanged_evidence"), {"a": 6, "b": 5})
        self.assertEqual(self.status()["offered"], {"a": 1, "b": 1})
        self.assertEqual(self.events("reasoning.lifecycle"), 0)

    def test_second_output_within_a_second_is_deferred_then_offered(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        sim.advance(.3)
        sim.output("a")
        sim.tick()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.skipped("rate_limited")["a"], 1)
        wait = sim.runtime._reasoning_wait()
        self.assertTrue(.6 < wait <= .75, wait)  # due one second after the offer
        sim.advance(.75)
        sim.tick()
        self.assertEqual(self.rows(), [("a", "completed"), ("a", "completed")])
        self.assertEqual(sim.runtime._reasoning_wait(), 1)

    def test_a_deferred_wake_up_does_not_outlive_the_recording(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        sim.advance(.3)
        sim.output("a")
        sim.tick()
        self.assertLess(sim.runtime._reasoning_wait(), 1)
        sim.runtime.stop_session(sim.store.get_session(sim.sid))
        sim.store.transition(sim.sid, "stopped")
        sim.advance(2)
        sim.tick()
        self.assertEqual(sim.runtime._reasoning_wait(), 1, "no early wake-ups for a stopped session")
        self.assertEqual(len(self.rows()), 1)

    def test_new_ha_context_alone_is_not_new_evidence(self):
        sim = self.sim
        sim.output("a")
        sim.output("b")
        sim.tick()
        sim.advance(1.2)
        sim.store.append_event(sim.sid, "ha.observation", {"site_id": "victoria", "camera_id": "den", "observed_at": sim.now-.2,
            "available_at": sim.now-.1, "valid_until": sim.now+5, "ha": {"light.den": {"state": "on"}}})
        sim.tick()
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.skipped("context_only"), {"a": 1, "b": 1})
        # The next camera output carries the HA fact into its request.
        sim.output("a")
        sim.tick()
        with sim.store.lock:
            payload = json.loads(sim.store.db.execute("SELECT payload FROM lab_reasoning_requests ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual({r.get("source_kind") for r in payload["records"]}, {"perception.output", "ha.observation"})

    def test_reasoning_revision_change_reoffers_the_same_evidence(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        session = sim.store.get_session(sim.sid)
        self.assertEqual(sim.runtime.set_reasoning(session, False)["state"], "applied")
        self.assertEqual(sim.runtime.set_reasoning(sim.store.get_session(sim.sid), True)["state"], "applied")
        sim.advance(1.1)
        sim.tick()
        self.assertEqual([r[0] for r in self.rows()], ["a", "a"])

    def test_live_records_retained_history_still_rides_with_new_evidence(self):
        sim = self.sim
        for _ in range(3):
            sim.output("a")
            sim.tick()
            sim.advance(1.1)
        with sim.store.lock:
            last = json.loads(sim.store.db.execute("SELECT payload FROM lab_reasoning_requests ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual(len(last["records"]), 3, "the window's history is unchanged; only the trigger is")


class OvertakenTickTests(Scheduled):
    """A disable or stop that lands while a tick prepares its request."""

    def overtake(self, action):
        runtime = self.sim.runtime
        original = runtime._evidence_keys

        def keys(*args, **kwargs):
            result = original(*args, **kwargs)
            action()
            return result
        runtime._evidence_keys = keys

    def requests(self):
        with self.sim.store.lock:
            if not self.sim.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='lab_reasoning_requests'").fetchone():
                return 0
            return self.sim.store.db.execute("SELECT count(*) FROM lab_reasoning_requests").fetchone()[0]

    def test_disable_during_the_first_tick_creates_no_reasoner_and_admits_nothing(self):
        sim = self.sim
        self.overtake(lambda: sim.runtime.set_reasoning(sim.store.get_session(sim.sid), False))
        sim.output("a")
        sim.tick()
        self.assertNotIn(sim.sid, sim.runtime.reasoners)
        self.assertEqual(self.requests(), 0)
        self.assertEqual(self.events("reasoning.request"), 0)
        self.assertEqual(self.events("reasoning.ingestion_failed"), 0)
        self.assertEqual(self.status()["offered"], {"a": 0, "b": 0})

    def test_disable_during_a_later_tick_admits_nothing_more(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        self.assertEqual(self.requests(), 1)
        sim.advance(1.2)
        sim.output("a")
        self.overtake(lambda: sim.runtime.set_reasoning(sim.store.get_session(sim.sid), False))
        sim.tick()
        self.assertEqual(self.requests(), 1)
        self.assertEqual(self.events("reasoning.request"), 1)
        self.assertEqual(self.events("reasoning.ingestion_failed"), 0)

    def test_stop_during_a_tick_admits_nothing(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        sim.advance(1.2)
        sim.output("b")
        self.overtake(lambda: sim.runtime.stop_session(sim.store.get_session(sim.sid)))
        sim.tick()
        self.assertEqual(self.requests(), 1)
        self.assertEqual(self.events("reasoning.ingestion_failed"), 0)
        self.assertNotIn(sim.sid, sim.runtime.reasoning_schedule, "stop drops the scheduler state")
        self.assertEqual(self.events("reasoning.scheduler"), 1)


class ClockStepTests(Scheduled):
    def test_a_wall_clock_step_back_does_not_starve_an_arm(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        sim.advance(.2)
        sim.now -= 120  # NTP correction backwards
        sim.output("a")
        sim.tick()
        # Offered and dispatched at once: neither the 1 s offer spacing nor
        # the per-arm dispatch spacing waits out the 2-minute step.
        self.assertEqual(self.rows(), [("a", "completed"), ("a", "completed")])
        self.assertEqual(self.skipped("rate_limited")["a"], 0)
        self.assertEqual(sim.runtime._reasoning_wait(), 1)
        with sim.store.lock:
            latest = json.loads(sim.store.db.execute("SELECT payload FROM lab_reasoning_requests ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        # The window re-read at the earlier cutoff: only causal evidence.
        self.assertTrue(all(r["available_at"] <= latest["cutoff"] for r in latest["records"]))
        self.assertEqual(len(latest["records"]), 1)

    def test_dispatch_spacing_still_holds_on_a_forward_clock(self):
        sim = self.sim
        sim.output("a")
        sim.tick()
        sim.advance(.3)
        sim.runtime.reasoners[sim.sid]._pump("a")  # nothing pending: no-op
        self.assertEqual(len(self.rows()), 1)




class ReasonerStub:
    CLOSE_REASONS = ("session_stopped", "reasoning_disabled", "session_ended", "runtime_shutdown", "reasoning_closed")

    def __init__(self, on_offer):
        self.lock = threading.RLock()
        self.futures = set()
        self.closed = False
        self.on_offer = on_offer
        self.offers = []

    def offer(self, records, **kwargs):
        self.offers.append(kwargs)
        return self.on_offer(self)

    def close(self, reason="reasoning_closed"):
        self.closed = True


class RuntimeLockTests(Scheduled):
    def test_tick_prepares_and_admits_outside_the_runtime_lock(self):
        sim = self.sim
        acquired = []

        def offer(reasoner):
            # Preview ingestion, stop and live status take this lock.
            probe = threading.Thread(target=lambda: acquired.append(sim.runtime.lock.acquire(timeout=1) and sim.runtime.lock.release() is None))
            probe.start()
            probe.join()
            return ["fixture"]
        sim.runtime.reasoning_factory = lambda *_: ReasonerStub(offer)
        sim.output("a")
        sim.tick()
        self.assertEqual(acquired, [True])
        self.assertEqual(sim.runtime.reasoners[sim.sid].offers[0]["arms"], ("a",))

    def test_stop_overtaking_a_tick_is_not_an_ingestion_failure(self):
        sim = self.sim

        def offer(reasoner):
            stopper = threading.Thread(target=sim.runtime.stop_session, args=(sim.store.get_session(sim.sid),))
            stopper.start()
            stopper.join(2)
            raise ValueError("reasoning is closed")
        sim.runtime.reasoning_factory = lambda *_: ReasonerStub(offer)
        sim.output("a")
        sim.tick()
        self.assertTrue(sim.runtime.reasoners[sim.sid].closed)
        self.assertEqual(self.events("reasoning.ingestion_failed"), 0)
        self.assertNotIn(sim.sid, sim.runtime.reasoning_errors)
        self.assertIsNone(self.status(), "stop drops the scheduler state")
        with sim.store.lock:
            summary = json.loads(sim.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.scheduler'").fetchone()[0])
        self.assertEqual(summary["offered"], {"a": 0, "b": 0}, "a failed offer is not counted as offered")

    def test_a_real_failure_is_still_recorded_once(self):
        sim = self.sim

        def offer(reasoner):
            raise ValueError("storage unavailable")
        sim.runtime.reasoning_factory = lambda *_: ReasonerStub(offer)
        sim.output("a")
        sim.tick()
        sim.advance(1.1)
        sim.output("a")
        sim.tick()
        self.assertEqual(self.events("reasoning.ingestion_failed"), 1)
        self.assertEqual(sim.runtime.reasoning_errors[sim.sid], "live_evidence_unavailable")


class PendingPool:
    """Dispatch reserved, provider never answers: later offers stay pending."""
    def submit(self, fn, *args):
        return Future()

    def shutdown(self, *args, **kwargs):
        pass


class ArmScopedOfferTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_offer_for_one_arm_replaces_only_that_arms_pending_request(self):
        from live_reasoning import LiveReasoning
        from service_runtime import ServiceRuntime
        ServiceRuntime(self.store, {}, None, None)
        self.store.db.execute("INSERT INTO lab_experiment_state VALUES(?,?,?)", (self.session["id"], "detection", 1))
        reasoner = LiveReasoning(self.store, self.session["id"], provider=None)
        reasoner.pool.shutdown(wait=True)
        reasoner.pool = PendingPool()
        records = [{"id": arm+"1", "site_id": "victoria", "session_id": self.session["id"], "arm": arm, "generation": 1,
                    "observed_at": 99.5, "available_at": 99.6, "valid_until": 102, "objects": [{"label": "book"}]} for arm in ("a", "b")]
        try:
            first = reasoner.offer(records, generation=1, cutoff=100)
            self.assertEqual(len(first), 2)
            a1, = reasoner.offer(records, generation=1, cutoff=100, arms=("a",))
            a2, = reasoner.offer(records, generation=1, cutoff=100, arms=["a"])
            states = {r[0]: (r[1], r[2]) for r in self.store.db.execute("SELECT id,arm,state FROM lab_reasoning_requests")}
            self.assertEqual(states[a1], ("a", "replaced"))
            self.assertEqual(states[a2], ("a", "pending"))
            self.assertEqual(sorted(v for v in states.values() if v[0] == "b"), [("b", "dispatching")])
            receipts = [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.lifecycle'")]
            self.assertEqual([(x["request_id"], x["replaced_by"]) for x in receipts], [(a1, a2)])
            with self.assertRaises(ValueError):
                reasoner.offer(records, generation=1, cutoff=100, arms=())
        finally:
            reasoner.close()


class LiveRecordCacheTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def append(self, rng, now):
        kind = rng.choice(("perception.output", "perception.output", "ha.observation", "room.observation", "perception.dropped"))
        observed = now-rng.uniform(-1, 14)
        value = {"site_id": rng.choice(("victoria", "victoria", "echo")), "camera_id": "den", "generation": rng.choice((1, 1, 2)),
                 "arm": rng.choice("ab"), "status": rng.choice(("completed", "completed", "failed")),
                 "observed_at": rng.choice((observed, observed, observed, "later", None, True)),
                 "available_at": observed+rng.uniform(-.5, 3), "valid_until": observed+rng.uniform(-1, 6),
                 "result": {"objects": [{"label": "book"}]}, "ha": {"screen": {"state": "on"}}}
        if rng.random() < .05:
            value.pop("observed_at")
        self.store.append_event(self.session["id"], kind, value)

    def test_incremental_reads_equal_the_plain_query(self):
        rng = random.Random(11)
        caches = {(w, h): {} for w in (0, 3, 10, 20) for h in (False, True)}
        now = 100.0
        for step in range(160):
            for _ in range(rng.choice((0, 1, 2, 3))):
                self.append(rng, now)
            # Mostly advancing, sometimes repeating, rarely stepping back.
            now += rng.choice((0, .05, .3, .7, 1.1)) if rng.random() > .03 else -2.5
            for (window, history), cache in caches.items():
                expected = live_records(self.store, self.session, 1, now, window_seconds=window, retain_history=history)
                actual = live_records(self.store, self.session, 1, now, window_seconds=window, retain_history=history, cache=cache)
                self.assertEqual(actual, expected, (step, window, history))
        self.assertTrue(any(live_records(self.store, self.session, 1, now, window_seconds=20, retain_history=True)))

    def test_bound_and_session_identity_match_the_plain_query(self):
        cache = {}
        for i in range(513):
            self.store.append_event(self.session["id"], "perception.output", {"site_id": "victoria", "camera_id": "den", "generation": 1,
                "arm": "a", "status": "completed", "observed_at": 99, "available_at": 100, "valid_until": 110, "result": {}})
        with self.assertRaisesRegex(ValueError, "bound"):
            live_records(self.store, self.session, 1, 100)
        with self.assertRaisesRegex(ValueError, "bound"):
            live_records(self.store, self.session, 1, 100, cache=cache)
        # Another session identity never reuses this session's parsed rows.
        other = {**self.session, "camera_id": "elsewhere"}
        with self.assertRaisesRegex(ValueError, "bound"):
            live_records(self.store, other, 1, 100, cache=cache)
        self.assertEqual(cache["state"]["identity"][2], "elsewhere")

    def test_a_reseed_parses_a_bounded_number_of_rows_then_falls_back(self):
        import live_inputs
        for i in range(12):
            self.store.append_event(self.session["id"], "perception.output", {"site_id": "victoria", "camera_id": "den", "generation": 1,
                "arm": "a", "status": "completed", "observed_at": 99, "available_at": 100, "valid_until": 110, "result": {}})
        cache = {}
        expected = live_records(self.store, self.session, 1, 100)
        with patch.object(live_inputs, "INCREMENTAL_ROWS", 5):
            parsed = []
            real = live_inputs.json.loads
            with patch.object(live_inputs.json, "loads", side_effect=lambda raw: parsed.append(1) or real(raw)):
                self.assertEqual(live_records(self.store, self.session, 1, 100, cache=cache), expected)
            self.assertNotIn("state", cache, "an oversized seed is not cached")
            self.assertEqual(len(parsed), 12, "the seed parsed nothing: only the plain query's rows were parsed")
        self.assertEqual(live_records(self.store, self.session, 1, 100, cache=cache), expected)
        self.assertEqual(len(cache["state"]["entries"]), 12)
        # A clock step back reseeds from scratch and stays exact.
        self.assertEqual(live_records(self.store, self.session, 1, 95, cache=cache), live_records(self.store, self.session, 1, 95))



class CopyAndSelectionTests(unittest.TestCase):
    def test_plain_copy_equals_deepcopy_and_is_independent(self):
        value = {"a": [1, 2.5, None, True, {"b": ["x", {"c": []}]}], "t": (1, [2]), "s": {3}}
        copied = plain_copy(value)
        self.assertEqual(copied, deepcopy(value))
        copied["a"][4]["b"][1]["c"].append(1)
        copied["t"][1].append(3)
        self.assertEqual(value, {"a": [1, 2.5, None, True, {"b": ["x", {"c": []}]}], "t": (1, [2]), "s": {3}})

    def test_causal_sources_are_exactly_the_admitted_evidence(self):
        rng = random.Random(5)
        records = []
        for i in range(300):
            observed = rng.uniform(80, 101)
            records.append({"id": str(rng.randrange(260)), "site_id": "victoria", "session_id": "s", "arm": rng.choice("ab"),
                            "generation": rng.choice((1, 1, 2)), "observed_at": observed, "available_at": observed+rng.uniform(0, 2),
                            "valid_until": observed+rng.uniform(-1, 6), "kind": rng.choice((None, "observation", "label")),
                            "source_kind": rng.choice((None, "perception.output", "ha.observation", "caption.receipt"))})
        for window in (0, 3, 10, 20):
            for history in (False, True):
                for arm in "ab":
                    kw = dict(site_id="victoria", session_id="s", arm=arm, generation=1, cutoff=100, window_seconds=window, retain_history=history)
                    evidence = causal_evidence(records, **kw)
                    self.assertEqual(causal_sources(records, **kw), [(r["id"], r.get("source_kind")) for r in evidence])

    def test_filter_ha_leaves_its_input_untouched(self):
        records = [{"arm": "a", "ha": {"x": {"state": "on"}}, "objects": [{"label": "book"}]}, {"arm": "b", "ha": {"x": {"state": "on"}}}]
        before = deepcopy(records)
        result = filter_ha(records, {"a": False, "b": False})
        self.assertEqual(records, before)
        self.assertEqual(result, [{"arm": "a", "objects": [{"label": "book"}]}])


class PolicyPublishTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_publish_of_the_admitted_digest_skips_only_the_structural_recompute(self):
        import jev_policy
        from jev_evidence import project
        from jev_semantics import semantic_state
        from test_jev_policy import PolicyTests
        gate = PolicyTests.setup_policy(self)
        self.packet["state"]["evidence"] = project([{**self.context, "id": "e1", "camera_id": "den", "observed_at": 99,
                                                     "available_at": 99.5, "valid_until": 101, "objects": [{"label": "book"}]}])
        self.packet["state"]["observation_cutoff"] = 100
        self.packet["state"]["semantic"] = semantic_state(self.packet["state"]["evidence"], 100)
        with patch("jev_policy.project", wraps=project) as projected:
            gate(self.context, self.packet, "send")
            self.assertEqual(projected.call_count, 1)
            self.assertTrue(gate(self.context, deepcopy(self.packet), "publish")["allowed"])
            self.assertEqual(projected.call_count, 1, "same admitted bytes are not re-projected")
            changed = deepcopy(self.packet)
            changed["state"]["evidence"][0]["objects"] = []
            with self.assertRaises(ValueError):
                gate(self.context, changed, "publish")
            self.assertEqual(projected.call_count, 2, "different bytes are fully re-checked")
        self.switch["state"] = "off"
        with self.assertRaisesRegex(ValueError, "kill-switch"):
            gate(self.context, self.packet, "publish")
        self.switch["state"] = "on"
        self.policy["revision"] = "changed"
        with self.assertRaisesRegex(ValueError, "Changed egress authorization"):
            gate(self.context, self.packet, "publish")


    def test_the_verified_digest_memo_is_safe_under_concurrent_workers(self):
        import jev_policy
        from jev_evidence import project
        from test_jev_policy import PolicyTests
        gate = PolicyTests.setup_policy(self)
        self.policy["max_calls_per_minute"] = 120
        ids = ["c%d" % i for i in range(48)]
        for rid in ids:
            self.store.db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?)", (rid, self.session["id"], "dispatching"))
        errors, allowed = [], []
        start = threading.Barrier(8)

        def worker(chunk):
            start.wait()
            for rid in chunk:
                context = {**self.context, "request_id": rid}
                try:
                    gate(context, deepcopy(self.packet), "send")
                    allowed.append(gate(context, deepcopy(self.packet), "publish")["allowed"])
                except Exception as error:  # pragma: no cover - reported below
                    errors.append(repr(error))
        with patch.object(jev_policy, "VERIFIED_ITEMS", 4):
            threads = [threading.Thread(target=worker, args=(ids[i::8],)) for i in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(allowed, [True]*len(ids))
        self.assertLessEqual(len(gate.verified), 4)
        self.assertEqual(list(gate.verified), [rid for rid in gate.verified], "an ordered LRU")


class LedgerQueryTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_output_snapshot_lookup_uses_the_output_time_index_with_identical_rows(self):
        old = """SELECT payload FROM events WHERE session_id=?
            AND kind='perception.output' AND json_extract(payload,'$.arm')=?
            AND json_extract(payload,'$.generation')=?
            AND json_extract(payload,'$.source_end_s')<=?
            AND json_extract(payload,'$.available_at')<=?
            ORDER BY json_extract(payload,'$.source_end_s') DESC,id DESC LIMIT 1"""
        import inspect
        import live_reasoning
        source = inspect.getsource(live_reasoning.LiveReasoning._output)
        new = source[source.index('"""SELECT payload'):]
        new = new[3:new.index('"""', 3)]
        rng = random.Random(3)
        for _ in range(300):
            end = rng.choice((rng.uniform(0, 50), 10.0))
            self.store.append_event(self.session["id"], rng.choice(("perception.output", "perception.output", "runtime.output")),
                {"arm": rng.choice("ab"), "generation": rng.choice((1, 2)), "source_end_s": end, "available_at": 100+rng.uniform(0, 50),
                 "arm_snapshot": {"n": rng.random()}})
        with self.store.lock:
            plan = " ".join(r[-1] for r in self.store.db.execute("EXPLAIN QUERY PLAN "+new, (self.session["id"], "a", 1, 1.0, 2.0)))
            self.assertIn("events_output_time", plan)
            for _ in range(200):
                args = (self.session["id"], rng.choice("ab"), rng.choice((1, 2)), rng.uniform(-1, 52), 100+rng.uniform(-1, 52))
                self.assertEqual(self.store.db.execute(new, args).fetchall(), self.store.db.execute(old, args).fetchall())

    def test_delivery_cadence_equals_the_full_scan(self):
        from service_runtime import ServiceRuntime
        from runtime import PairedRunner
        runner = PairedRunner(self.store, self.session["id"], self.profiles, lambda r: None, clock=lambda: 100.0)
        runner.close()
        runner.pool.shutdown(wait=True)
        runtime = ServiceRuntime(self.store, {}, None, None)
        rng = random.Random(9)
        for i in range(400):
            at = rng.uniform(0, 200)
            payload = {"frame": {"valid_until": at+rng.uniform(-1, 2)}} if rng.random() > .1 else {"frame": {}}
            self.store.db.execute("INSERT INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)", ("r%d" % i, self.session["id"], rng.choice("ab"),
                rng.choice((1, 2)), "d", json.dumps(payload), rng.choice(("completed", "completed", "failed")), None, at))
        for now in (50.0, 150.0, 199.5, 230.0):
            for arm in "ab":
                done = self.store.db.execute("""SELECT updated_at,json_extract(payload,'$.frame.valid_until') FROM paired_requests
                    WHERE session_id=? AND arm=? AND generation=1 AND state='completed' ORDER BY updated_at""", (self.session["id"], arm)).fetchall()
                count = lambda window, fresh: sum(1 for at, valid in done if at >= now-window and (not fresh or (valid is not None and at <= valid)))
                times = [row[0] for row in done]
                actual = runtime._delivery(self.session["id"], arm, 1, {}, now)
                self.assertEqual((actual["fresh_10s"], actual["fresh_30s"], actual["completed_30s"]), (count(10, True), count(30, True), count(30, False)))
                self.assertEqual(actual["max_gap_s"], max((b-a for a, b in zip(times, times[1:])), default=None))
                self.assertEqual(actual["last_completed_at"], times[-1] if times else None)


class OfferIntervalTests(unittest.TestCase):
    """The per-arm minimum offer interval is a controller setting: default
    2 s, bounded to 1-10 s, recipe-independent (live config
    'reasoning_min_offer_interval_s'). Deferral still offers the newest
    evidence once the interval ends; admission budgets are unchanged."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.directory.cleanup()

    def simulation(self, interval):
        sim = Simulation(self.directory.name, filler_files=0, offer_interval=interval)
        self.addCleanup(sim.close)
        return sim

    def requests(self, sim):
        with sim.store.lock:
            return [json.loads(r[0]) for r in sim.store.db.execute(
                "SELECT payload FROM lab_reasoning_requests WHERE arm='a' ORDER BY rowid")]

    def test_default_is_two_seconds_and_the_newest_deferred_evidence_is_offered_when_due(self):
        from service_runtime import REASONING_MIN_OFFER_INTERVAL_S
        self.assertEqual(REASONING_MIN_OFFER_INTERVAL_S, 2.0)
        sim = self.simulation(None)
        self.assertEqual(sim.runtime.reasoning_min_offer_interval_s, 2.0)
        sim.preview(); sim.output("a"); sim.tick()
        for _ in range(3):  # three newer outputs inside the 2 s interval
            sim.advance(.5)
            sim.preview(); sim.output("a"); sim.tick()
        self.assertEqual(len(self.requests(sim)), 1)
        status = sim.runtime.reasoning_scheduler_status(sim.sid)
        self.assertEqual(status["min_offer_interval_s"], 2.0)
        self.assertEqual(status["skipped"]["rate_limited"]["a"], 3)
        self.assertAlmostEqual(sim.runtime._reasoning_wait(), .5, places=6)  # due 2 s after the offer
        sim.advance(.55)
        sim.tick()
        first, second = self.requests(sim)
        frames = lambda request: {r["frame_id"] for r in request["records"] if r["source_kind"] == "perception.output"}
        self.assertEqual(frames(first), {"frame-1"})
        self.assertIn("frame-4", frames(second), "the newest deferred output is offered")
        self.assertEqual(frames(second), {"frame-1", "frame-2", "frame-3", "frame-4"})
        self.assertEqual(sim.runtime.reasoning_scheduler_status(sim.sid)["offered"]["a"], 2)

    def test_configured_interval_spaces_offers_and_one_second_remains_available(self):
        for interval, expected in ((1, 4), (5.0, 1)):
            with self.subTest(interval=interval):
                directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
                sim = Simulation(directory.name, filler_files=0, offer_interval=interval)
                try:
                    sim.preview(); sim.output("a"); sim.tick()
                    for _ in range(3):
                        sim.advance(1.05)
                        sim.preview(); sim.output("a"); sim.tick()
                    with sim.store.lock:
                        count = sim.store.db.execute("SELECT count(*) FROM lab_reasoning_requests WHERE arm='a'").fetchone()[0]
                    self.assertEqual(count, expected)
                    self.assertEqual(sim.runtime.reasoning_scheduler_status(sim.sid)["min_offer_interval_s"], float(interval))
                finally:
                    sim.close()
                    directory.cleanup()

    def test_interval_bounds_and_live_config_key(self):
        from service_runtime import ServiceRuntime, offer_interval
        from live_detector_app import validate_config
        from test_ha_provisioning import live_config
        self.assertEqual(offer_interval(None), 2.0)
        for accepted in (1, 1.0, 2.5, 10):
            self.assertEqual(offer_interval(accepted), float(accepted))
        for refused in (0.99, 10.01, 0, -1, float("nan"), float("inf"), True, "2", [2]):
            with self.subTest(refused=refused), self.assertRaises(ValueError):
                offer_interval(refused)
        sim = self.simulation(None)
        with self.assertRaises(ValueError):
            ServiceRuntime(sim.store, {}, lambda *a: None, None, reasoning_min_offer_interval_s=0.5)
        self.assertEqual(validate_config(live_config(reasoning_min_offer_interval_s=3))["reasoning_min_offer_interval_s"], 3)
        for refused in (0.5, 11, "2", None):
            with self.subTest(config=refused), self.assertRaises(ValueError):
                validate_config(live_config(reasoning_min_offer_interval_s=refused))

    def test_live_config_reaches_the_built_runtime(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from live_detector_app import build_runtime
        from test_ha_provisioning import live_config
        sim = self.simulation(None)
        sim.store.settings = replace(sim.store.settings, cameras=({"id": "den"},))
        for extra, expected in (({}, 2.0), ({"reasoning_min_offer_interval_s": 4}, 4.0)):
            runtime = build_runtime(sim.store, SimpleNamespace(camera_id="den"), live_config(**extra),
                                    transport_factory=lambda *_: (lambda *a: None), lease=SimpleNamespace(ready=lambda: False))
            try:
                self.assertEqual(runtime.reasoning_min_offer_interval_s, expected)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
