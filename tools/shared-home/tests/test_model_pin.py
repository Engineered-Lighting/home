"""private/model-pin.json: exact load, atomic write, the pinned health check and
the owner's auto re-pin rule. Temp files and fake `docker inspect` objects only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace

from home_shared import model_pin
from home_shared.model_pin import (EngineIdentityMismatch, ModelPin, PinnedEngine, check_live_identity,
                                   load_engine, load_model_pin, load_read_only_engine, repinned_epoch,
                                   write_model_pin)

NAME = "vllm"
CONTAINER_ID = "e" * 64
IMAGE = "sha256:" + "b" * 64
STARTED = "2026-09-27T21:42:03.337370461Z"
EPOCH = "live-fixture-epoch"  # test_lab_live_scene.EPOCH, quoted by test_lab_live_pilot.config_value()
ARTIFACT = "a" * 64
RESTARTED = "2026-10-08T03:15:00.123456789Z"


def pin_value(**changes):
    value = {"version": 1, "container_name": NAME, "container_id": CONTAINER_ID, "image": IMAGE,
             "started_at": STARTED, "engine_epoch": EPOCH, "artifact_sha256": ARTIFACT}
    value.update(changes)
    return {k: v for k, v in value.items() if v is not None}


def write_pin(path, **changes):
    Path(path).write_text(json.dumps(pin_value(**changes)))
    return Path(path)


def inspect(**changes):
    """One `docker inspect` container object for the pinned engine."""
    state = {"Running": True, "StartedAt": changes.pop("started_at", STARTED),
             "Health": {"Status": changes.pop("health", "healthy")}}
    value = {"Id": CONTAINER_ID, "Name": "/" + NAME, "Image": IMAGE, "State": state}
    value.update(changes)
    return value


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / model_pin.PIN_NAME

    def load(self, value):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))
        return load_model_pin(self.path)

    def test_exact_pin_and_absent_auto_repin_means_false(self):
        pin = self.load(pin_value())
        self.assertEqual(pin, ModelPin(NAME, CONTAINER_ID, IMAGE, STARTED, EPOCH, ARTIFACT, False))
        self.assertIs(self.load(pin_value(auto_repin=True)).auto_repin, True)
        self.assertIs(self.load(pin_value(auto_repin=False)).auto_repin, False)

    def test_malformed_partial_or_widened_pins_are_refused(self):
        bad = [pin_value(version=2), pin_value(container_id="f579e204"), pin_value(image="sha256:" + "B" * 64),
               pin_value(image="b" * 64), pin_value(started_at="2026-09-27 21:42:03Z"), pin_value(container_name="/vllm"),
               pin_value(engine_epoch="-epoch"), pin_value(artifact_sha256="qwen"), pin_value(auto_repin="true"),
               pin_value(auto_repin=1), pin_value(extra=1), pin_value(started_at=None), [pin_value()]]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)
        with self.assertRaises(ValueError):  # duplicate member
            self.load(json.dumps(pin_value())[:-1] + ', "auto_repin": true, "auto_repin": false}')
        with self.assertRaises(ValueError):
            self.load(json.dumps(pin_value(engine_epoch="e" * 150, container_name="n" * 120)) + " " * 4096)
        with self.assertRaises((ValueError, OSError)):
            load_model_pin(Path(self.temp.name) / "absent.json")

    def test_atomic_write_round_trips_and_leaves_no_temporary(self):
        write_pin(self.path, auto_repin=True)
        pin = load_model_pin(self.path)
        moved = ModelPin(NAME, "1" * 64, IMAGE, RESTARTED, "next-epoch", ARTIFACT, True)
        write_model_pin(self.path, moved)
        self.assertEqual(load_model_pin(self.path), moved)
        self.assertEqual(os.listdir(self.temp.name), [model_pin.PIN_NAME])
        with self.assertRaises(ValueError):  # never writes an invalid pin
            write_model_pin(self.path, ModelPin(NAME, "bad", IMAGE, RESTARTED, "e", ARTIFACT, True))
        self.assertEqual(load_model_pin(self.path), moved)
        self.assertNotEqual(pin, moved)

    def test_repinned_from_is_an_optional_bounded_lineage(self):
        self.assertEqual(self.load(pin_value()).repinned_from, ())
        pin = self.load(pin_value(repinned_from=["older-epoch", "oldest-epoch"]))
        self.assertEqual(pin.repinned_from, ("older-epoch", "oldest-epoch"))
        self.assertEqual(self.load(pin_value(repinned_from=[])).repinned_from, ())
        lineage = ["e%d" % index for index in range(model_pin.MAX_REPINNED_FROM)]
        self.assertEqual(len(self.load(pin_value(repinned_from=lineage)).repinned_from), model_pin.MAX_REPINNED_FROM)
        for bad in ("older-epoch", ["-bad"], [1], ["a", "a"], [EPOCH], lineage + ["one-more"], {"a": 1}):
            with self.subTest(repinned_from=bad), self.assertRaises(ValueError):
                self.load(pin_value(repinned_from=bad))
        # Written only when there is a lineage, so an operator's pin keeps its shape.
        write_model_pin(self.path, ModelPin(NAME, CONTAINER_ID, IMAGE, STARTED, EPOCH, ARTIFACT, True))
        self.assertNotIn("repinned_from", json.loads(self.path.read_text()))
        moved = ModelPin(NAME, "1" * 64, IMAGE, RESTARTED, "next-epoch", ARTIFACT, True, (EPOCH,))
        write_model_pin(self.path, moved)
        self.assertEqual(json.loads(self.path.read_text())["repinned_from"], [EPOCH])
        self.assertEqual(load_model_pin(self.path), moved)

    def test_repinned_epoch_is_a_valid_distinct_identifier(self):
        epoch = repinned_epoch(NAME, "1" * 64, RESTARTED)
        self.assertEqual(epoch, "vllm-111111111111-20261008T031500.123456789Z")
        self.assertRegex(epoch, model_pin._EPOCH)
        self.assertNotEqual(epoch, repinned_epoch(NAME, "1" * 64, STARTED))


class LiveIdentityTests(unittest.TestCase):
    def test_live_scene_must_quote_the_pinned_epoch_and_artifact(self):
        pin = ModelPin(NAME, CONTAINER_ID, IMAGE, STARTED, EPOCH, ARTIFACT)
        check_live_identity(pin, SimpleNamespace(engine_epoch=EPOCH, artifact_sha256=ARTIFACT))
        for config in (SimpleNamespace(engine_epoch="old-epoch", artifact_sha256=ARTIFACT),
                       SimpleNamespace(engine_epoch=EPOCH, artifact_sha256="c" * 64)):
            with self.subTest(config=config), self.assertRaises(EngineIdentityMismatch) as caught:
                check_live_identity(pin, config)
            self.assertIn("does not match model-pin.json", str(caught.exception))
        with self.assertRaisesRegex(EngineIdentityMismatch, "engine_epoch=old-epoch \\(model-pin.json %s\\)" % EPOCH):
            check_live_identity(pin, SimpleNamespace(engine_epoch="old-epoch", artifact_sha256=ARTIFACT))

    def test_an_epoch_an_accepted_re_pin_replaced_is_accepted_as_not_current(self):
        pin = ModelPin(NAME, CONTAINER_ID, IMAGE, RESTARTED, "repinned-epoch", ARTIFACT, True, (EPOCH, "first-epoch"))
        self.assertIs(check_live_identity(pin, SimpleNamespace(engine_epoch="repinned-epoch",
                                                               artifact_sha256=ARTIFACT)), True)
        for epoch in (EPOCH, "first-epoch"):
            self.assertIs(check_live_identity(pin, SimpleNamespace(engine_epoch=epoch, artifact_sha256=ARTIFACT)), False)
        # An epoch no re-pin produced, or another artifact, is still a misconfiguration.
        for config in (SimpleNamespace(engine_epoch="typo-epoch", artifact_sha256=ARTIFACT),
                       SimpleNamespace(engine_epoch=EPOCH, artifact_sha256="c" * 64),
                       SimpleNamespace(engine_epoch="repinned-epoch", artifact_sha256="c" * 64)):
            with self.subTest(config=config), self.assertRaises(EngineIdentityMismatch):
                check_live_identity(pin, config)

    def test_a_full_lineage_never_turns_a_pending_bump_into_a_refused_start(self):
        # More re-pins than the lineage keeps have dropped the epoch live-scene.json
        # still quotes: with a full lineage any epoch of the pin's artifact starts
        # live closed (camera chat runs), and only another artifact refuses.
        full = tuple("e%d" % index for index in range(model_pin.MAX_REPINNED_FROM))
        pin = ModelPin(NAME, CONTAINER_ID, IMAGE, RESTARTED, "repinned-epoch", ARTIFACT, True, full)
        for epoch in (EPOCH, "e0", full[-1]):
            with self.subTest(epoch=epoch):
                self.assertIs(check_live_identity(pin, SimpleNamespace(engine_epoch=epoch, artifact_sha256=ARTIFACT)),
                              False)
        with self.assertRaises(EngineIdentityMismatch):
            check_live_identity(pin, SimpleNamespace(engine_epoch=EPOCH, artifact_sha256="c" * 64))
        # The same holds with the rule turned off after the re-pins.
        self.assertIs(check_live_identity(ModelPin(NAME, CONTAINER_ID, IMAGE, RESTARTED, "repinned-epoch", ARTIFACT,
                                                   False, full),
                                          SimpleNamespace(engine_epoch=EPOCH, artifact_sha256=ARTIFACT)), False)
        # A lineage with room to spare dropped nothing: an epoch outside it is a typo.
        short = ModelPin(NAME, CONTAINER_ID, IMAGE, RESTARTED, "repinned-epoch", ARTIFACT, True, full[:-1])
        with self.assertRaises(EngineIdentityMismatch):
            check_live_identity(short, SimpleNamespace(engine_epoch=EPOCH, artifact_sha256=ARTIFACT))


class EngineCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / model_pin.PIN_NAME
        self.logs, self.others = [], []

    def engine(self, **changes):
        write_pin(self.path, **changes)
        return load_engine(self.path, log=self.logs.append)

    def other_checks(self, error=None):
        def run():
            self.others.append(1)
            if error is not None:
                raise error
        return run

    def test_the_pinned_incarnation_runs_every_other_check(self):
        engine = self.engine()
        engine.check(inspect(), self.other_checks())
        self.assertEqual(self.others, [1])
        with self.assertRaisesRegex(ValueError, "resource guard"):
            engine.check(inspect(), self.other_checks(ValueError("pilot resource guard closed")))
        for value in (inspect(health="starting"), inspect(Image="sha256:" + "c" * 64), inspect(Name="/other"),
                      {**inspect(), "State": {**inspect()["State"], "Running": False}}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, model_pin.UNHEALTHY):
                engine.check(value, self.other_checks())
        self.assertEqual(self.logs, [])

    def test_changed_started_at_without_the_rule_refuses_and_logs_once_per_change(self):
        engine = self.engine()
        before = self.path.read_bytes()
        for _ in range(3):
            with self.assertRaisesRegex(ValueError, model_pin.UNHEALTHY):
                engine.check(inspect(started_at=RESTARTED), self.other_checks())
        self.assertEqual(self.others, [])  # refused before any other check
        self.assertEqual(self.logs, ["vLLM StartedAt changed; pilot refusing old=eeeeeeeeeeee@%s new=eeeeeeeeeeee@%s"
                                     % (STARTED, RESTARTED)])
        with self.assertRaises(ValueError):  # a second change is a new line
            engine.check(inspect(Id="2" * 64, started_at="2026-10-09T00:00:00Z"), self.other_checks())
        self.assertEqual(len(self.logs), 2)
        self.assertTrue(self.logs[1].startswith("vLLM StartedAt changed; pilot refusing"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(engine.pin.started_at, STARTED)

    def test_auto_repin_accepts_a_restart_of_the_same_engine_and_persists_it(self):
        engine = self.engine(auto_repin=True)
        engine.check(inspect(started_at=RESTARTED), self.other_checks())
        new_epoch = repinned_epoch(NAME, CONTAINER_ID, RESTARTED)
        self.assertEqual((engine.pin.started_at, engine.engine_epoch, engine.container_id),
                         (RESTARTED, new_epoch, CONTAINER_ID))
        self.assertEqual(engine.provisioned.engine_epoch, EPOCH)  # the cross-check keeps the start-time pin
        self.assertEqual(load_model_pin(self.path), engine.pin)
        self.assertEqual(engine.pin.repinned_from, (EPOCH,))      # the epoch this re-pin replaced
        self.assertEqual(self.logs, ["engine re-pinned old=eeeeeeeeeeee@%s new=eeeeeeeeeeee@%s engine_epoch=%s"
                                     % (STARTED, RESTARTED, new_epoch)])
        engine.check(inspect(started_at=RESTARTED), self.other_checks())  # now the pinned incarnation
        self.assertEqual(len(self.logs), 1)
        # A recreated container (new id) under the same name and image digest.
        engine.check(inspect(Id="3" * 64, started_at="2026-10-09T00:00:00Z"), self.other_checks())
        self.assertEqual((load_model_pin(self.path).container_id, engine.container_id), ("3" * 64, "3" * 64))
        self.assertTrue(load_model_pin(self.path).auto_repin)
        self.assertEqual(load_model_pin(self.path).repinned_from, (new_epoch, EPOCH))   # newest first

    def test_the_re_pin_lineage_is_bounded_newest_first(self):
        engine = self.engine(auto_repin=True)
        epochs = [EPOCH]
        for index in range(model_pin.MAX_REPINNED_FROM + 2):
            started = "2026-10-%02dT00:00:00Z" % (10 + index)
            engine.check(inspect(started_at=started), self.other_checks())
            epochs.append(repinned_epoch(NAME, CONTAINER_ID, started))
        pinned = load_model_pin(self.path)
        self.assertEqual(pinned.engine_epoch, epochs[-1])
        self.assertEqual(list(pinned.repinned_from), epochs[-2::-1][:model_pin.MAX_REPINNED_FROM])

    def test_more_re_pins_than_the_lineage_keeps_never_refuse_the_unbumped_live_scene(self):
        # live-scene.json left at the provisioned epoch (live parked) across many
        # vLLM restarts: every later start composes live closed, none refuses.
        engine = self.engine(auto_repin=True)
        config = SimpleNamespace(engine_epoch=EPOCH, artifact_sha256=ARTIFACT)
        for index in range(model_pin.MAX_REPINNED_FROM + 3):
            started = "2026-10-%02dT00:00:00Z" % (10 + index)
            engine.check(inspect(started_at=started), self.other_checks())
            with self.subTest(re_pins=index + 1):
                self.assertIs(check_live_identity(load_model_pin(self.path), config), False)
        self.assertNotIn(EPOCH, load_model_pin(self.path).repinned_from)   # dropped by the bound

    def test_auto_repin_refuses_another_image_name_health_or_failing_check(self):
        engine = self.engine(auto_repin=True)
        before = self.path.read_bytes()
        cases = ((inspect(started_at=RESTARTED, Image="sha256:" + "c" * 64), None),
                 (inspect(started_at=RESTARTED, Name="/vllm-old"), None),
                 (inspect(started_at=RESTARTED, health="starting"), None),
                 (inspect(started_at=RESTARTED), ValueError("new failed system unit")))
        for value, error in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                engine.check(value, self.other_checks(error))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((engine.pin.started_at, engine.engine_epoch), (STARTED, EPOCH))
        self.assertEqual(self.logs, ["vLLM StartedAt changed; pilot refusing old=eeeeeeeeeeee@%s new=eeeeeeeeeeee@%s"
                                     % (STARTED, RESTARTED)])
        # Once the restarted engine is healthy and every check passes it is accepted.
        engine.check(inspect(started_at=RESTARTED), self.other_checks())
        self.assertEqual(engine.pin.started_at, RESTARTED)
        self.assertTrue(self.logs[-1].startswith("engine re-pinned old="))

    def test_a_failed_persist_refuses_and_keeps_the_old_pin(self):
        engine = self.engine(auto_repin=True)

        def fail(path, pin):
            raise OSError("disk full")
        engine._write = fail
        with self.assertRaises(OSError):
            engine.check(inspect(started_at=RESTARTED), self.other_checks())
        self.assertEqual((engine.pin.started_at, engine.engine_epoch), (STARTED, EPOCH))
        self.assertTrue(self.logs[0].startswith("vLLM StartedAt changed; pilot refusing"))

    def test_concurrent_checks_re_pin_once(self):
        engine = self.engine(auto_repin=True)
        gate = threading.Barrier(4)
        errors = []

        def worker():
            try:
                gate.wait(5)
                engine.check(inspect(started_at=RESTARTED), self.other_checks())
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(sum(line.startswith("engine re-pinned") for line in self.logs), 1)
        self.assertEqual(load_model_pin(self.path).started_at, RESTARTED)

    def test_engine_is_constructed_from_an_exact_pin_only(self):
        with self.assertRaises(ValueError):
            PinnedEngine(self.path, ModelPin(NAME, "short", IMAGE, STARTED, EPOCH, ARTIFACT))

    def test_a_read_only_engine_never_re_pins_or_writes(self):
        # A standalone preflight's engine: the pinned incarnation passes, a
        # restart fails it even under "auto_repin": true, and the file stays.
        write_pin(self.path, auto_repin=True)
        before = self.path.read_bytes()
        engine = load_read_only_engine(self.path, log=self.logs.append)
        engine.check(inspect(), self.other_checks())
        self.assertEqual(self.others, [1])
        for value in (inspect(started_at=RESTARTED), inspect(Id="3" * 64)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, model_pin.UNHEALTHY):
                engine.check(value, self.other_checks())
        self.assertEqual(self.others, [1])   # refused before any other check
        self.assertTrue(self.logs[0].startswith("vLLM StartedAt changed; pilot refusing"))
        self.assertFalse(any(line.startswith("engine re-pinned") for line in self.logs))
        self.assertEqual((engine.pin.started_at, engine.engine_epoch), (STARTED, EPOCH))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.listdir(self.temp.name), [model_pin.PIN_NAME])
        with self.assertRaises(ValueError):   # no writer even if the rule were reached
            engine._write(self.path, engine.pin)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
