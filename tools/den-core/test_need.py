"""Tests for need.py (den-core need v0). Offline, standard library, no wall clock.

Fact lines are real la-fact-log-1 lines, built with the perceiver's own
qwen_scene.build_observation and la_fact_log.line, wrapped by hand as
den-event-1 facts events. The parity test loads den_lighting's policy.py by
path and skips when it is not checked out above this folder.

    python -B -m unittest discover -s tools/den-core -p "test_need.py"
"""
import importlib.util
import json
import random
import re
import socket
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
LAB = Path(__file__).resolve().parents[2] / "stack" / "services" / "perception-lab"
sys.path.insert(0, str(LAB))
import la_fact_log  # noqa: E402
import qwen_scene as scene  # noqa: E402

sys.path.insert(0, str(HERE))
import need  # noqa: E402

T0 = 1793764800.0
SESSION = "5e" * 16
READING = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book", "book_state": "open",
           "gaze_target": "book", "typing_visible": "no", "screen_facing_subject": "not_visible",
           "activity_hypothesis": "reading", "visible_basis": ["person_visible", "book_in_hands"],
           "change_vs_prior": "no_prior"}
PHONE = {**READING, "held_object": "phone", "book_state": "not_visible", "gaze_target": "phone",
         "activity_hypothesis": "phone_use", "visible_basis": ["person_visible", "phone_in_hands"]}
TWO = {**READING, "people_visible": "multiple", "visible_basis": ["multiple_people_visible", "book_in_hands"]}
# One-letter sample kinds for the dwell tests.
KINDS = {"R": {"facts": READING}, "P": {"facts": PHONE}, "T": {"facts": TWO},
         "N": {"facts": {**READING, "people_visible": "none"}},
         "U": {"facts": {**READING, "activity_hypothesis": "unclear"}},
         "X": {"text": "not json"}, "F": {"outcome": "timeout"}}
POLICY_PATH = Path("ha-config") / "custom_components" / "den_lighting" / "policy.py"
SERIALISE = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": True, "allow_nan": False}


def fact_line(index, observed, available=None, facts=READING, text=None, outcome="completed"):
    available = observed + 1.5 if available is None else available
    request_id = scene.digest({"fixture": index})
    frames = [{"media_id": "ring-%064x" % (index * 10 + k), "frame_id": "cap-1-0-%d" % (index * 10 + k),
               "capture_epoch": "cap-1", "observed_at": observed - 3 + 1.5 * k} for k in range(3)]
    receipt = None
    if outcome == "completed":
        draft = scene.build_observation(
            site_id="victoria", camera_id="den", session_id=SESSION, request_id=request_id, frames=frames,
            generated_at=available - 0.1, prompt_version=scene.PROMPT_VERSION,
            context=scene.model_context("evening 20:00"), body_sha256=scene.digest({"body": index}),
            engine_epoch="la-epoch", artifact_sha256="a" * 64,
            content=json.dumps(facts) if text is None else text, finish_reason="stop", prior_supplied=False)
        receipt = scene.first_receipt(draft, available)
    line, _ = la_fact_log.line(
        request_id=request_id, session_id=SESSION, observed_at=observed, sent_at=observed + 0.1,
        available_at=available, engine_epoch="la-epoch", prompt_version=scene.PROMPT_VERSION, outcome=outcome,
        observation=receipt)
    return line


def epoch(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


def facts_event(line, seq):
    data = json.loads(line)
    return {"schema": "den-event-1", "t": epoch(data["available_at"]), "seq": seq, "kind": "facts",
            "source": "synthetic", "data": data}


def tick(t, seq):
    return {"schema": "den-event-1", "t": t, "seq": seq, "kind": "tick", "source": "runner", "data": {}}


def events(samples):
    """facts events, in the given order, for [(kind, observed, latency)]."""
    return [facts_event(fact_line(n, observed, observed + latency, **KINDS[kind]), n)
            for n, (kind, observed, latency) in enumerate(samples)]


def every4(kinds):
    """A sample of each kind every 4 s from T0, each arriving 1.5 s after capture."""
    return events([(kind, T0 + 4.0 * i, 1.5) for i, kind in enumerate(kinds)])


def states(evs, policy=None):
    """focused_engagement.state after each event (None before the first record)."""
    policy, out, current = policy or need.make_policy(), [], None
    for event in evs:
        for output in policy.handle(event):
            current = output["data"]["focused_engagement"]["state"]
        out.append(current)
    return out


def records(evs, policy=None):
    policy = policy or need.make_policy()
    return [output["data"] for event in evs for output in policy.handle(event)]


def changes(record):
    return (record["focused_engagement"]["state"], record["posture"], record["visual_demand"]["basis"],
            record["field_support"]["focused_engagement"]["support_status"], record["flags"])


def parity_stream():
    """30 minutes of samples: blocks of reading, phone, unclear, invalid and failed answers, two 60 s capture
    gaps and three answers arriving 50 s after capture. [(facts event, kind)], in arrival order."""
    rng = random.Random(7)
    kinds = ("reading", "phone", "unclear", "invalid", "failed")
    captures, observed, gaps = [], T0, [T0 + 600, T0 + 1200]
    while observed < T0 + 1800:
        kind, block_end = rng.choices(kinds, weights=(4, 3, 1, 1, 1))[0], observed + rng.uniform(15, 150)
        while observed < min(block_end, T0 + 1800):
            captures.append((observed, kind))
            observed += rng.uniform(2.0, 5.0)
            if gaps and observed >= gaps[0]:
                observed += 60
                gaps.pop(0)
    late = set(rng.sample(range(10, len(captures) - 10), 3))
    style = {"reading": {"facts": READING}, "phone": {"facts": PHONE},
             "unclear": {"facts": {**READING, "activity_hypothesis": "unclear"}},
             "invalid": {"text": "not json"}, "failed": {"outcome": "failed"}}
    stream = []
    for n, (observed, kind) in enumerate(captures):
        latency = 50.0 if n in late else rng.uniform(0.5, 4.0)
        stream.append((facts_event(fact_line(n, observed, observed + latency, **style[kind]), n), kind))
    stream.sort(key=lambda pair: (pair[0]["t"], pair[0]["seq"]))
    return stream


def find_policy():
    for folder in HERE.parents:
        if (folder / POLICY_PATH).is_file():
            return folder / POLICY_PATH
    return None


def load_policy(path):
    spec = importlib.util.spec_from_file_location("den_lighting_policy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while the class is built
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # leave no __pycache__ in den_lighting
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


class SampleRule(unittest.TestCase):
    def sample(self, **kind):
        return need.sample(json.loads(fact_line(1, T0, **kind)))

    def test_reading_is_playing(self):
        s = self.sample()
        self.assertEqual(s["status"], "playing")
        self.assertTrue(s["answered"])
        self.assertEqual((s["observed_at"], s["available_at"]), (T0, T0 + 1.5))
        self.assertEqual(s["request_id"], scene.digest({"fixture": 1}))
        self.assertEqual(s["facts"], READING)

    def test_other_valid_answers_are_idle(self):
        for facts in (PHONE, TWO, {**READING, "people_visible": "none"}):
            self.assertEqual(self.sample(facts=facts)["status"], "idle", facts["people_visible"])

    def test_unclear_invalid_and_failed_are_unknown(self):
        cases = [({"facts": {**READING, "people_visible": "unclear"}}, True),
                 ({"facts": {**READING, "activity_hypothesis": "unclear"}}, True),
                 ({"text": "not json"}, False), ({"outcome": "timeout"}, False)]
        for kind, answered in cases:
            s = self.sample(**kind)
            self.assertEqual((s["status"], s["answered"]), ("unknown", answered), kind)


class Dwell(unittest.TestCase):
    def test_second_playing_sample_engages(self):
        out = records(every4("RR"))
        self.assertEqual([r["focused_engagement"]["state"] for r in out], ["starting", "engaged"])
        self.assertEqual([r["focused_engagement"]["onset_ts"] for r in out], [T0, T0])
        self.assertEqual([r["focused_engagement"]["since_s"] for r in out], [1.5, 5.5])
        self.assertEqual([r["field_support"]["focused_engagement"]["value_set_ts"] for r in out],
                         [T0 + 1.5, T0 + 5.5])

    def test_unknown_resets_the_count(self):
        self.assertEqual(states(every4("RURR")), ["starting", "none", "starting", "engaged"])

    def test_capture_gap_over_ttl_resets_the_count(self):
        out = records(events([("R", T0, 1.5), ("R", T0 + 46, 1.5), ("R", T0 + 50, 1.5)]))
        self.assertEqual([r["focused_engagement"]["state"] for r in out], ["starting", "engaged"])
        self.assertEqual(out[-1]["focused_engagement"]["onset_ts"], T0 + 46)

    def test_freshness_bounds_are_inclusive(self):
        self.assertEqual(states(events([("R", T0, 1.5), ("R", T0 + 45, 1.5)])), ["starting", "engaged"])
        self.assertEqual(states(events([("R", T0, 1.5), ("R", T0 + 4, 45.0)])), ["starting", "engaged"])
        for ahead, expected in ((5.0, "engaged"), (6.0, "none")):
            evs = every4("RR")
            evs[1]["t"] = epoch(evs[1]["data"]["observed_at"]) - ahead  # a capture ahead of the clock
            self.assertEqual(states(evs)[-1], expected, ahead)

    def test_late_arrival_is_ignored_and_resets_the_count(self):
        evs = events([("R", T0, 1.5), ("R", T0 + 4, 46.0), ("R", T0 + 40, 11.5), ("R", T0 + 44, 7.6)])
        out = records(evs)
        self.assertEqual([r["focused_engagement"]["state"] for r in out], ["starting", "none", "starting", "engaged"])
        refs = [r["sources"]["focused_engagement"][0]["ref"] for r in out]
        self.assertEqual(refs, [evs[n]["data"]["request_id"] for n in (0, 0, 2, 3)])

    def test_sample_not_newer_than_the_last_is_ignored(self):
        evs = events([("R", T0 + 4, 1.5), ("R", T0, 6.0), ("R", T0 + 4, 3.0), ("R", T0 + 8, 1.5)])
        policy = need.make_policy()
        outputs = [policy.handle(e) for e in evs]
        self.assertEqual([len(o) for o in outputs], [1, 0, 0, 1])
        self.assertEqual(outputs[3][0]["data"]["focused_engagement"]["state"], "engaged")

    def test_idle_ends_after_exit_dwell(self):
        self.assertEqual(states(every4("RR" + "P" * 9)), ["starting", "engaged"] + ["ending"] * 8 + ["none"])
        exact = events([("R", T0, 1.5), ("R", T0 + 4, 1.5), ("P", T0 + 8, 1.5), ("P", T0 + 38, 1.5)])
        self.assertEqual(states(exact), ["starting", "engaged", "ending", "none"])
        self.assertIsNone(records(exact)[-1]["focused_engagement"]["onset_ts"])

    def test_playing_during_ending_engages_again(self):
        out = records(every4("RRPR"))
        self.assertEqual([r["focused_engagement"]["state"] for r in out], ["starting", "engaged", "ending", "engaged"])
        self.assertEqual(out[-1]["focused_engagement"]["onset_ts"], T0)

    def test_dwell_numbers_come_from_config(self):
        policy = need.make_policy({"dwell": {"exit_idle_s": 60}})
        self.assertEqual(policy.config["dwell"]["entry_samples"], 2)
        self.assertEqual(need.DEFAULT_CONFIG["dwell"]["exit_idle_s"], 30)
        exact = events([("R", T0, 1.5), ("R", T0 + 4, 1.5), ("P", T0 + 8, 1.5), ("P", T0 + 38, 1.5)])
        self.assertEqual(states(exact, policy), ["starting", "engaged", "ending", "ending"])


POLICY = find_policy()


class DenLightingParity(unittest.TestCase):
    @unittest.skipIf(POLICY is None, "den_lighting policy.py is not checked out above this folder")
    def test_on_off_timing_matches_den_lighting_dwell(self):
        controller = load_policy(POLICY).Controller()
        stream = parity_stream()
        controller.set_gaming_enabled(True, stream[0][0]["t"])
        policy, state, seen, ignored = need.make_policy(), None, [], 0
        expected = {"reading": "playing", "phone": "idle"}
        for event, kind in stream:
            for output in policy.handle(event):
                state = output["data"]["focused_engagement"]["state"]
            last = controller.last_sample
            controller.gaming_sample(expected.get(kind, "unknown"), epoch(event["data"]["observed_at"]), event["t"])
            ignored += last is not None and controller.last_sample == last
            self.assertEqual(state in ("engaged", "ending"), controller.gaming, event["seq"])
            incumbent = (("ending" if controller.idle_since is not None else "engaged") if controller.gaming
                         else "starting" if controller.playing_samples >= 1 else "none")
            self.assertEqual(state, incumbent, event["seq"])
            seen.append(controller.gaming)
        switches = sum(a != b for a, b in zip(seen, seen[1:]))
        self.assertGreaterEqual(switches, 6, "the stream should switch the dwell on and off several times")
        self.assertGreaterEqual(ignored, 3, "the late arrivals should be ignored")


class Records(unittest.TestCase):
    def test_emitted_only_on_change(self):
        out = records(every4("RRRRRR"))
        self.assertEqual(len(out), 2)
        mixed = records(every4("RFPXUFRRPPT"))
        for a, b in zip(mixed, mixed[1:]):
            self.assertNotEqual(changes(a), changes(b))

    def test_records_carry_support_and_latest_answered_ref(self):
        kinds = "RFPXUFR"
        evs = every4(kinds)
        policy, latest, emitted = need.make_policy(), None, 0
        for event, kind in zip(evs, kinds):
            if kind not in "FX":  # failed and invalid lines carry no answer
                latest = event["data"]["request_id"]
            for output in policy.handle(event):
                record, emitted = output["data"], emitted + 1
                support = record["field_support"]["focused_engagement"]
                self.assertEqual(set(support), {"last_support_capture_ts", "value_set_ts", "support_status"})
                self.assertEqual(record["sources"]["focused_engagement"], [{"origin": "qwen", "ref": latest}])
        self.assertEqual(emitted, 5)

    def test_record_fields(self):
        record = records(every4("R"))[0]
        self.assertRegex(record["state_id"], r"^room@\d+\.\d{3}$")
        self.assertEqual(record["state_id"], "room@%.3f" % (T0 + 1.5))
        self.assertEqual(record["emitted_ts"], T0 + 1.5)
        self.assertEqual((record["schema_version"], record["track_id"], record["identity"]),
                         ("person_state/v1", "room", "unbound"))
        self.assertEqual(record["visual_demand"], {"level": None, "basis": "qwen:reading", "confidence": None})
        self.assertEqual(record["focused_engagement"]["kind"], "reading")
        self.assertIsNone(record["focused_engagement"]["confidence"])
        self.assertEqual(record["posture"], "sitting")
        self.assertEqual(record["interpreter"], {"path": "rules", "version": "need-v0"})
        self.assertEqual((record["field_support"]["focused_engagement"]["support_status"], record["flags"]),
                         ("observed", []))

    def test_posture_words(self):
        for word, posture in (("walking", "standing"), ("lying", "lying"), ("unclear", "unknown")):
            evs = [facts_event(fact_line(1, T0, facts={**PHONE, "subject_posture": word}), 0)]
            self.assertEqual(records(evs)[0]["posture"], posture, word)

    def test_before_any_answer(self):
        policy = need.make_policy()
        self.assertEqual(policy.handle(tick(T0, 0)), [])
        record = records(every4("F"), policy)[0]
        self.assertEqual(record["focused_engagement"]["state"], "none")
        self.assertEqual((record["posture"], record["visual_demand"]["basis"]), ("unknown", None))
        self.assertEqual(record["sources"], {"focused_engagement": []})
        self.assertEqual(record["field_support"]["focused_engagement"],
                         {"last_support_capture_ts": None, "value_set_ts": T0 + 1.5, "support_status": "expired"})
        self.assertEqual(record["flags"], ["stale:qwen"])

    def test_support_expires_on_ticks(self):
        policy = need.make_policy()
        first = records(every4("R"), policy)
        ticks = [tick(T0 + 2.0 * k, k + 1) for k in range(1, 40)]
        emitted = [(e["t"], o["data"]) for e in ticks for o in policy.handle(e)]
        self.assertEqual(len(first), 1)
        self.assertEqual([t for t, _ in emitted], [T0 + 46.0])
        record = emitted[0][1]
        self.assertEqual((record["field_support"]["focused_engagement"]["support_status"], record["flags"]),
                         ("expired", ["stale:qwen"]))
        self.assertEqual(record["field_support"]["focused_engagement"]["last_support_capture_ts"], T0)
        self.assertEqual(record["state_id"], "room@%.3f" % (T0 + 46.0))


class Purity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stream = [event for event, _ in parity_stream()] + [tick(T0 + 2.0 * k, 0) for k in range(950)]
        cls.stream = sorted(stream, key=lambda e: e["t"])

    def run_stream(self):
        policy = need.make_policy()
        return [json.dumps(output, **SERIALISE) for event in self.stream for output in policy.handle(event)]

    def test_same_events_give_identical_output(self):
        first = self.run_stream()
        self.assertGreater(len(first), 20)
        self.assertEqual(first, self.run_stream())

    def test_no_clock_or_network(self):
        expected = self.run_stream()
        with mock.patch.object(time, "time", side_effect=AssertionError("wall clock")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("network")):
            self.assertEqual(self.run_stream(), expected)

    def test_policy_attributes(self):
        policy = need.make_policy()
        self.assertEqual((policy.id, policy.version, policy.code_path), ("need", "need-v0", need.__file__))
        self.assertEqual(json.loads(json.dumps(policy.config, **SERIALISE)), need.DEFAULT_CONFIG)
        self.assertIsNot(policy.config, need.DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()
