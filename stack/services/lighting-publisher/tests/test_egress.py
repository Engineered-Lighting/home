"""Egress gate matrix: every switch combination, plus record security, toggle validity, rate and in-flight limits."""
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path

from lighting_beliefs import egress as eg

SCOPE = "public_eval"
RECORD_STATES = ("enabled", "disabled", "absent")
ENV_STATES = ("on", "off")
TOGGLE_STATES = ("on", "off", "stale", "boundary", "absent", "nan", "inf", "negative", "future", "str", "bool")
SCOPE_STATES = ("match", "mismatch")
INVALID_TOGGLES = ("nan", "inf", "negative", "str", "bool")


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def write_record(path, enabled, mode=0o600, text=None):
    path.write_text(text if text is not None else json.dumps({"schema": eg.RECORD_SCHEMA, "enabled": enabled, "scopes": [SCOPE]}))
    os.chmod(path, mode)


def toggle_for(state):
    ages = {
        "stale": eg.TOGGLE_MAX_AGE_S + 1, "boundary": eg.TOGGLE_MAX_AGE_S, "nan": float("nan"),
        "inf": float("inf"), "negative": -1e9, "future": -1.0, "str": "5", "bool": True,
    }
    if state == "absent":
        return lambda: None
    if state in ages:
        return lambda: eg.ToggleState("on", ages[state])
    return lambda: eg.ToggleState(state, 12.0)


class GateMatrixTest(unittest.TestCase):
    def test_every_row(self):
        rows = list(itertools.product(RECORD_STATES, ENV_STATES, TOGGLE_STATES, SCOPE_STATES))
        self.assertEqual(len(rows), 132)
        with tempfile.TemporaryDirectory() as tmp:
            for record, env, toggle, scope in rows:
                record_path = Path(tmp) / f"record-{record}.json"
                if record != "absent":
                    write_record(record_path, record == "enabled")
                gate = eg.EgressGate(record_path, toggle_for(toggle), Path(tmp) / "journal.jsonl",
                                     env={eg.ENV_FLAG: "1"} if env == "on" else {}, clock=FakeClock())
                decision = gate.request(SCOPE if scope == "match" else "household_shadow")
                expected = record == "enabled" and env == "on" and toggle in ("on", "future") and scope == "match"
                self.assertEqual(decision.allowed, expected, (record, env, toggle, scope, decision.reason))
                if expected:
                    self.assertEqual(decision.reason, "allowed")
                elif record == "absent":
                    self.assertEqual(decision.reason, "record_absent")
                elif record == "disabled":
                    self.assertEqual(decision.reason, "record_disabled")
                elif scope == "mismatch":
                    self.assertEqual(decision.reason, "scope_mismatch")
                elif env == "off":
                    self.assertEqual(decision.reason, "env_absent")
                elif toggle in INVALID_TOGGLES:
                    self.assertEqual(decision.reason, "toggle_invalid")
                elif toggle == "boundary":
                    self.assertEqual(decision.reason, "toggle_stale")
                else:
                    self.assertEqual(decision.reason, f"toggle_{toggle}")
                self.assertEqual(set(decision.checks), set(eg.CHECK_ORDER))
            journal = [json.loads(l) for l in (Path(tmp) / "journal.jsonl").read_text().splitlines()]
            self.assertEqual(len(journal), 132)
            self.assertEqual(sum(1 for e in journal if e["allowed"]), 2)
            for entry in journal:
                self.assertIn("reason", entry)
                self.assertIn("checks", entry)
                self.assertNotIn("nan", json.dumps(entry["checks"]).lower())

    def test_env_off_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_record(Path(tmp) / "r.json", True)
            gate = eg.EgressGate(Path(tmp) / "r.json", toggle_for("on"), Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "0"})
            self.assertEqual(gate.request(SCOPE).reason, "env_off")

    def test_invalid_record_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_record(Path(tmp) / "r.json", True, text="{not json")
            gate = eg.EgressGate(Path(tmp) / "r.json", toggle_for("on"), Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "1"})
            self.assertEqual(gate.request(SCOPE).reason, "record_invalid")

    def test_record_file_security(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "r.json"
            for mode in (0o600, 0o640, 0o644):
                write_record(record, True, mode=mode)
                self.assertEqual(eg.load_record(record)[1], "ok", oct(mode))
            for mode in (0o666, 0o664, 0o620, 0o602):
                write_record(record, True, mode=mode)
                self.assertEqual(eg.load_record(record), (None, "insecure"), oct(mode))
                gate = eg.EgressGate(record, toggle_for("on"), Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "1"})
                self.assertEqual(gate.request(SCOPE).reason, "record_insecure")
            write_record(record, True)
            link = Path(tmp) / "link.json"
            link.symlink_to(record)
            self.assertEqual(eg.load_record(link), (None, "not_a_file"))
            gate = eg.EgressGate(link, toggle_for("on"), Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "1"})
            self.assertEqual(gate.request(SCOPE).reason, "record_not_a_file")
            self.assertEqual(eg.load_record(Path(tmp))[1], "not_a_file")
            self.assertEqual(eg.load_record(record, owner_uid=os.getuid())[1], "ok")
            self.assertEqual(eg.load_record(record, owner_uid=os.getuid() + 1), (None, "insecure"))
            gate = eg.EgressGate(record, toggle_for("on"), Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "1"},
                                 record_owner_uid=os.getuid() + 1)
            self.assertEqual(gate.request(SCOPE).reason, "record_insecure")


class RateAndInFlightTest(unittest.TestCase):
    def make_gate(self, tmp, clock, **kwargs):
        write_record(Path(tmp) / "r.json", True)
        return eg.EgressGate(Path(tmp) / "r.json", toggle_for("on"), Path(tmp) / "j.jsonl",
                             env={eg.ENV_FLAG: "1"}, clock=clock, **kwargs)

    def test_one_in_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.make_gate(tmp, FakeClock())
            first = gate.request(SCOPE)
            self.assertTrue(first.allowed)
            self.assertTrue(gate.in_flight())
            second = gate.request(SCOPE)
            self.assertFalse(second.allowed)
            self.assertEqual(second.reason, "in_flight_busy")
            gate.release(first, outcome="ok", request_id="req-1")
            self.assertFalse(gate.in_flight())
            self.assertTrue(gate.request(SCOPE).allowed)
            journal = [json.loads(l) for l in (Path(tmp) / "j.jsonl").read_text().splitlines()]
            self.assertEqual([e["event"] for e in journal], ["decision", "decision", "release", "decision"])
            self.assertEqual(journal[2]["outcome"], "ok")

    def test_in_flight_expires_after_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FakeClock()
            gate = self.make_gate(tmp, clock)
            first = gate.request(SCOPE)
            self.assertTrue(first.allowed)
            clock.advance(eg.IN_FLIGHT_TIMEOUT_S - 1)
            self.assertEqual(gate.request(SCOPE).reason, "in_flight_busy")
            clock.advance(1)
            second = gate.request(SCOPE)
            self.assertTrue(second.allowed, second.reason)
            self.assertNotEqual(second.ticket, first.ticket)
            journal = [json.loads(l) for l in (Path(tmp) / "j.jsonl").read_text().splitlines()]
            self.assertEqual([e["event"] for e in journal], ["decision", "decision", "in_flight_expired", "decision"])
            self.assertEqual(journal[2]["ticket"], first.ticket)
            self.assertEqual(journal[2]["held_s"], eg.IN_FLIGHT_TIMEOUT_S)
            gate.release(first)
            self.assertTrue(gate.in_flight(), "a late release of an expired ticket must not free the new one")
            gate.release(second)
            self.assertFalse(gate.in_flight())

    def test_six_per_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FakeClock()
            gate = self.make_gate(tmp, clock)
            for _ in range(eg.MAX_CALLS_PER_MINUTE):
                d = gate.request(SCOPE)
                self.assertTrue(d.allowed)
                gate.release(d)
                clock.advance(1.0)
            seventh = gate.request(SCOPE)
            self.assertFalse(seventh.allowed)
            self.assertEqual(seventh.reason, "rate_6/6")
            clock.advance(60.0)
            self.assertTrue(gate.request(SCOPE).allowed)

    def test_file_toggle_reader_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FakeClock()
            mirror = Path(tmp) / "toggle.json"
            mirror.write_text(json.dumps({"state": "on", "received_at": clock() - 10}))
            reader = eg.file_toggle_reader(mirror, clock)
            self.assertEqual(reader(), eg.ToggleState("on", 10.0))
            write_record(Path(tmp) / "r.json", True)
            gate = eg.EgressGate(Path(tmp) / "r.json", reader, Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "1"}, clock=clock)
            first = gate.request(SCOPE)
            self.assertTrue(first.allowed)
            gate.release(first)
            clock.advance(eg.TOGGLE_MAX_AGE_S - 10)
            d = gate.request(SCOPE)
            self.assertEqual(d.reason, "toggle_stale", "age exactly TOGGLE_MAX_AGE_S is stale")
            self.assertEqual(d.checks["toggle"]["status"], "stale")
            mirror.unlink()
            self.assertIsNone(reader())

    def test_file_toggle_reader_rejects_bad_clocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FakeClock()
            mirror = Path(tmp) / "toggle.json"
            reader = eg.file_toggle_reader(mirror, clock)
            for text in ('{"state": "on", "received_at": Infinity}', '{"state": "on", "received_at": NaN}',
                         '{"state": "on", "received_at": -Infinity}', '{"state": "on", "received_at": "5"}',
                         '{"state": "on", "received_at": true}', '{"state": "on", "received_at": null}'):
                mirror.write_text(text)
                self.assertIsNone(reader(), text)
            mirror.write_text(json.dumps({"state": "on", "received_at": clock() + 2}))
            self.assertEqual(reader(), eg.ToggleState("on", -2.0))
            write_record(Path(tmp) / "r.json", True)
            gate = eg.EgressGate(Path(tmp) / "r.json", reader, Path(tmp) / "j.jsonl", env={eg.ENV_FLAG: "1"}, clock=clock)
            self.assertTrue(gate.request(SCOPE).allowed, "small skew is tolerated")
            mirror.write_text(json.dumps({"state": "on", "received_at": 9e15}))
            self.assertEqual(reader(), eg.ToggleState("on", clock() - 9e15))
            gate = eg.EgressGate(Path(tmp) / "r.json", reader, Path(tmp) / "j2.jsonl", env={eg.ENV_FLAG: "1"}, clock=clock)
            self.assertEqual(gate.request(SCOPE).reason, "toggle_invalid")


if __name__ == "__main__":
    unittest.main()
