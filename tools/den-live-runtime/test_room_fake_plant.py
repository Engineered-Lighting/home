"""Public four-member wrapper tests; the external process port is authored."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def authored_package(directory):
    fixtures = [{"index": index, "name": chr(65 + index), "device_id": f"{index + 1:032x}",
                 "key_hex": f"{index + 1:064x}", "boot": str(index + 1)} for index in range(4)]
    config = {"schema": "c12-fake-plant-config-1", "version": 1, "profile": "fictional-c12-v1",
              "initial_clock_device_us": "0", "default_effect": "rejected", "fixtures": fixtures,
              "evidence_source": "simulated", "physical_permission": False}
    config_path = Path(directory) / "config.json"
    config_path.write_bytes(canonical(config))
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    manifest = {"schema": "c12-fake-plant-build-1", "version": 1, "abi": "c12-fake-plant-bridge-1",
                "source_digest": "1" * 64, "config_sha256": config_sha,
                "build_id": "authored-external-provider", "executable": {"sha256": "2" * 64},
                "config": {"path": str(config_path), "sha256": config_sha, "file_sha256": config_sha},
                "physical_permission": False, "hardware_operations": 0}
    manifest_path = Path(directory) / "manifest.json"
    manifest_path.write_bytes(canonical(manifest))
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    header = {"session_id": "authored-session", "producer": {"abi": manifest["abi"],
              "build_id": manifest["build_id"], "manifest_sha256": manifest_sha,
              "source_sha256": manifest["source_digest"], "config_sha256": config_sha,
              "executable_sha256": "2" * 64}, "fixtures": [
                  {"fixture_index": index, "fixture_id": chr(65 + index), "device_id": fixture["device_id"],
                   "boot": fixture["boot"], "launch_id": "launch-" + chr(65 + index)}
                  for index, fixture in enumerate(fixtures)],
              "evidence_source": "simulated", "physical_permission": False}
    return manifest_path, manifest_sha, header


class RecorderPort:
    """The pre-agreed recorder boundary; no native replay claim is made here."""
    def __init__(self, header, timeline):
        self.header = copy.deepcopy(header)
        self.timeline = timeline
        self.ready = True
        self.failure = None

    def observer(self, index):
        self.timeline.append(("observer", index))
        return lambda event: self.timeline.append(("io", index, event))

    def abort(self, reason, *, finalize=False):
        self.failure = reason
        self.timeline.append(("abort", reason, finalize))

    def finalize(self, members):
        self.timeline.append(("finalize",))
        return {"status": "incomplete" if self.failure else "complete", "members": copy.deepcopy(members)}


def external_module(timeline):
    """An injected external SDK module; it opens no subprocess or producer."""
    module = types.ModuleType("authored_external_fakeprocess")

    class FakePlantProcess:
        def __init__(self, manifest_path, expected_manifest_sha256, *, raw_observer):
            self.observer = raw_observer
            self.closed = False
            self.index = None
            timeline.append(("construct", str(manifest_path), expected_manifest_sha256))

        def start(self, index):
            self.index = index
            timeline.append(("start", index))
            return {"ok": True, "fixture_index": index}

        def close(self):
            self.closed = True
            timeline.append(("close", self.index))
            return {"closed": True, "terminal": True, "exit_code": 0, "exit_reason": "owner_close"}

        def request(self, op, **fields):
            timeline.append(("request", self.index, op, copy.deepcopy(fields)))
            return {"ok": True, "fixture_index": self.index, "op": op, "fields": copy.deepcopy(fields)}

        @property
        def recording_status(self):
            return {"complete": self.closed, "cleanup_unconfirmed": False, "observer_failed": False,
                    "terminal": False, "active_callbacks": 0}

    module.FakePlantProcess = FakePlantProcess
    return module


class NotReadyRecorder:
    ready = False

    def abort(self, reason, *, finalize=False):
        self.failure = reason


class RoomFakePlantTests(unittest.TestCase):
    def test_unready_recorder_prevents_provider_construction_and_start(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        opened = []

        def external_constructor(*args, **kwargs):
            opened.append((args, kwargs))
            raise AssertionError("provider must remain unopened")

        room = RoomFakePlant("unused-manifest.json", "0" * 64, NotReadyRecorder(),
                             fixture_ids=["A", "B", "C", "D"], process_factory=external_constructor)
        self.assertEqual(opened, [])
        with self.assertRaises(RoomFakePlantRefusal) as caught:
            room.start()
        self.assertEqual(caught.exception.reason, "recorder_not_ready")
        self.assertEqual(opened, [])

    def test_absent_provider_stays_unconfigured_without_loading_a_default(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            recorder = RecorderPort(header, [])
            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"])
            with self.assertRaises(RoomFakePlantRefusal) as caught:
                room.start()
            self.assertEqual(caught.exception.reason, "provider_unconfigured")
            result = room.close()
            self.assertEqual(result["status"], "incomplete")
            self.assertTrue(all(row["recording_status"] is None for row in result["members"]))

    def test_changed_original_pins_or_fixture_binding_prevent_every_provider_construction(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        for changed in ("manifest", "config", "producer", "device", "index", "fixture_ids"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                path, pin, header = authored_package(directory)
                ids = ["A", "B", "C", "D"]
                if changed == "manifest":
                    path.write_bytes(path.read_bytes() + b"\n")
                elif changed == "config":
                    config_path = Path(directory) / "config.json"
                    config_path.write_bytes(config_path.read_bytes() + b"\n")
                elif changed == "producer":
                    header["producer"]["executable_sha256"] = "f" * 64
                elif changed == "device":
                    header["fixtures"][1]["device_id"] = "f" * 32
                elif changed == "index":
                    header["fixtures"][0]["fixture_index"] = False
                elif changed == "fixture_ids":
                    ids[3] = "A"
                timeline = []
                recorder = RecorderPort(header, timeline)
                room = RoomFakePlant(path, pin, recorder, fixture_ids=ids,
                                     process_factory=external_module(timeline).FakePlantProcess)
                with self.assertRaises(RoomFakePlantRefusal):
                    room.start()
                self.assertEqual([row for row in timeline if row[0] == "construct"], [])
                self.assertEqual(room.close()["status"], "incomplete")

    def test_explicit_start_binds_four_members_and_close_finalizes_after_all_members(self):
        from room_fake_plant import RoomFakePlant

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)
            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=provider.FakePlantProcess)
            self.assertEqual(timeline, [])
            self.assertEqual(room.start(), {"status": "started", "members": [
                {"fixture_index": index, "launch_id": "launch-" + chr(65 + index)} for index in range(4)]})
            result = room.close()
            self.assertEqual(result["status"], "complete")
            self.assertEqual([row["fixture_index"] for row in result["members"]], [0, 1, 2, 3])
            self.assertTrue(all(row["close_acknowledged"] and row["recording_status"]["complete"]
                                for row in result["members"]))
            self.assertEqual([item for item in timeline if item[0] == "start"],
                             [("start", 0), ("start", 1), ("start", 2), ("start", 3)])
            self.assertEqual(sorted(item for item in timeline if item[0] == "close"),
                             [("close", 0), ("close", 1), ("close", 2), ("close", 3)])
            self.assertEqual(timeline[-1], ("finalize",))

    def test_only_explicit_step_is_sent_to_the_selected_member_without_clock_conversion(self):
        from room_fake_plant import RoomFakePlant

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)
            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=provider.FakePlantProcess)
            room.start()
            result = room.step(2, "tick", at_device_us="9007199254740993")
            self.assertEqual(result, {"ok": True, "fixture_index": 2, "op": "tick",
                                      "fields": {"at_device_us": "9007199254740993"}})
            self.assertEqual([item for item in timeline if item[0] == "request"],
                             [("request", 2, "tick", {"at_device_us": "9007199254740993"})])
            room.close()

    def test_all_five_explicit_operation_shapes_preserve_the_authored_input(self):
        from room_fake_plant import RoomFakePlant

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            room = RoomFakePlant(path, pin, RecorderPort(header, timeline), fixture_ids=["A", "B", "C", "D"],
                                 process_factory=external_module(timeline).FakePlantProcess)
            room.start()
            operations = [("set_effect", {"at_device_us": "1", "driver": "lighting", "effect": "rejected"}),
                          ("observe", {"at_device_us": "2", "observation": {"kind": "temperature", "millic": 25000,
                           "sampled_us": "2", "valid": True, "config_valid": True, "conversion_ready": True}}),
                          ("exchange", {"at_device_us": "3", "ingress_device_us": "2", "packet_hex": "00ff"}),
                          ("disconnect", {"at_device_us": "4"}), ("tick", {"at_device_us": "5"})]
            for op, fields in operations:
                self.assertEqual(room.step(3, op, **fields)["fields"], fields)
            self.assertEqual([item for item in timeline if item[0] == "request"],
                             [("request", 3, op, fields) for op, fields in operations])
            room.close()

    def test_member_exception_is_terminal_without_retry_and_other_members_still_close(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            provider = external_module(timeline)

            class FailedExternalStep(provider.FakePlantProcess):
                def request(self, op, **fields):
                    super().request(op, **fields)
                    raise OSError("private external diagnostic must not become a public reason")

                def close(self):
                    if self.index == 2:
                        raise OSError("authored close failure")
                    return super().close()

            room = RoomFakePlant(path, pin, RecorderPort(header, timeline), fixture_ids=["A", "B", "C", "D"],
                                 process_factory=FailedExternalStep)
            room.start()
            with self.assertRaises(RoomFakePlantRefusal) as caught:
                room.step(2, "tick", at_device_us="1")
            self.assertEqual(str(caught.exception), "member_step_failed")
            with self.assertRaises(RoomFakePlantRefusal):
                room.step(0, "tick", at_device_us="2")
            with self.assertRaises(RoomFakePlantRefusal):
                room.start()
            result = room.close()
            self.assertEqual(result["status"], "incomplete")
            self.assertFalse(result["members"][2]["close_acknowledged"])
            self.assertTrue(all(result["members"][index]["close_acknowledged"] for index in (0, 1, 3)))
            self.assertEqual(len([row for row in timeline if row[0] == "request"]), 1)

    def test_unconfirmed_start_is_terminal_and_cleanup_preserves_unstarted_member_status(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)

            class RefusingExternalProcess(provider.FakePlantProcess):
                def start(self, index):
                    result = super().start(index)
                    return {"ok": False, "fixture_index": index} if index == 2 else result

            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=RefusingExternalProcess)
            with self.assertRaises(RoomFakePlantRefusal) as caught:
                room.start()
            self.assertEqual(caught.exception.reason, "member_start_unconfirmed")
            with self.assertRaises(RoomFakePlantRefusal):
                room.start()
            with self.assertRaises(RoomFakePlantRefusal):
                room.step(0, "tick", at_device_us="1")
            result = room.close()
            self.assertEqual(result["status"], "incomplete")
            self.assertIsNone(result["members"][3]["recording_status"])
            self.assertEqual([item for item in timeline if item[0] == "start"],
                             [("start", 0), ("start", 1), ("start", 2)])
            self.assertEqual([item for item in timeline if item[0] == "close"],
                             [("close", 0), ("close", 1), ("close", 2)])

    def test_untyped_original_status_stays_unavailable_and_cannot_complete(self):
        from room_fake_plant import RoomFakePlant

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)

            class UntypedExternalStatus(provider.FakePlantProcess):
                @property
                def recording_status(self):
                    status = super().recording_status
                    if self.index == 1:
                        status["active_callbacks"] = False
                    return status

            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=UntypedExternalStatus)
            room.start()
            result = room.close()
            self.assertEqual(result["status"], "incomplete")
            self.assertIsNone(result["members"][1]["recording_status"])
            self.assertEqual(recorder.failure, "member_status_unavailable")

    def test_step_request_bounds_refuse_before_external_dispatch(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        cases = [(True, "tick", {"at_device_us": "1"}),
                 (0, "tick", {"at_device_us": "01"}),
                 (0, "tick", {"at_device_us": "18446744073709551616"}),
                 (0, "tick", {"at_device_us": 1}),
                 (0, "tick", {"at_device_us": "1", "request_id": "injected"}),
                 (0, "close", {}),
                 (0, "set_effect", {"at_device_us": "1", "driver": "hardware", "effect": "submitted"}),
                 (0, "exchange", {"at_device_us": "1", "ingress_device_us": "0", "packet_hex": "AA"}),
                 (0, "observe", {"at_device_us": "1", "observation": {"value": float("nan")}}),
                 (0, "observe", {"at_device_us": "1", "observation": {"value": "x" * 16384}})]
        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            for index, op, fields in cases:
                with self.subTest(index=index, op=op, keys=list(fields)):
                    timeline = []
                    recorder = RecorderPort(header, timeline)
                    room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                         process_factory=external_module(timeline).FakePlantProcess)
                    room.start()
                    try:
                        with self.assertRaises(RoomFakePlantRefusal):
                            room.step(index, op, **fields)
                        self.assertEqual([item for item in timeline if item[0] == "request"], [])
                        with self.assertRaises(RoomFakePlantRefusal):
                            room.step(0, "tick", at_device_us="2")
                    finally:
                        room.close()

    def test_close_during_start_prevents_every_later_member_activation(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)
            entered, release = threading.Event(), threading.Event()
            failures = []

            class DelayedExternalStart(provider.FakePlantProcess):
                def start(self, index):
                    result = super().start(index)
                    if index == 1:
                        entered.set()
                        release.wait()
                    return result

            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=DelayedExternalStart)

            def start():
                try:
                    room.start()
                except RoomFakePlantRefusal as error:
                    failures.append(error.reason)

            caller = threading.Thread(target=start)
            caller.start()
            try:
                self.assertTrue(entered.wait(1))
                result = room.close()
                release.set()
                caller.join(1)
                self.assertFalse(caller.is_alive())
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(failures, ["room_not_active"])
                self.assertEqual([item for item in timeline if item[0] == "start"], [("start", 0), ("start", 1)])
                self.assertEqual(timeline.count(("finalize",)), 1)
            finally:
                release.set()
                caller.join(1)

    def test_close_with_inflight_step_stays_incomplete_and_suppresses_late_success(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)
            entered, release = threading.Event(), threading.Event()
            outcomes = []

            class DelayedExternalReply(provider.FakePlantProcess):
                def request(self, op, **fields):
                    entered.set()
                    release.wait()
                    return super().request(op, **fields)

            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=DelayedExternalReply)
            room.start()

            def step():
                try:
                    outcomes.append(room.step(0, "tick", at_device_us="1"))
                except RoomFakePlantRefusal as error:
                    outcomes.append(error.reason)

            caller = threading.Thread(target=step)
            caller.start()
            try:
                self.assertTrue(entered.wait(1))
                result = room.close()
                self.assertEqual(result["status"], "incomplete")
                release.set()
                caller.join(1)
                self.assertFalse(caller.is_alive())
                self.assertEqual(outcomes, ["room_not_active"])
                self.assertEqual(room.close(), result)
            finally:
                release.set()
                caller.join(1)

    def test_shared_close_deadline_preserves_unconfirmed_cleanup_without_reopen_or_late_finalization(self):
        from room_fake_plant import RoomFakePlant, RoomFakePlantRefusal

        with tempfile.TemporaryDirectory() as directory:
            path, pin, header = authored_package(directory)
            timeline = []
            recorder = RecorderPort(header, timeline)
            provider = external_module(timeline)
            release = threading.Event()
            finished = threading.Event()

            class StalledExternalCleanup(provider.FakePlantProcess):
                def close(self):
                    if self.index == 1:
                        release.wait()
                    result = super().close()
                    if self.index == 1:
                        finished.set()
                    return result

                @property
                def recording_status(self):
                    status = super().recording_status
                    if self.index == 1 and not self.closed:
                        status.update(cleanup_unconfirmed=True, active_callbacks=1)
                    return status

            room = RoomFakePlant(path, pin, recorder, fixture_ids=["A", "B", "C", "D"],
                                 process_factory=StalledExternalCleanup)
            room.start()
            timer = threading.Timer(11.2, release.set)
            timer.daemon = True
            timer.start()
            try:
                before = time.monotonic()
                result = room.close()
                self.assertLess(time.monotonic() - before, 10.8)
                self.assertEqual(result["status"], "incomplete")
                member = result["members"][1]
                self.assertFalse(member["close_acknowledged"])
                self.assertEqual(member["recording_status"]["active_callbacks"], 1)
                self.assertTrue(member["recording_status"]["cleanup_unconfirmed"])
                self.assertTrue(all(result["members"][index]["close_acknowledged"] for index in (0, 2, 3)))
                with self.assertRaises(RoomFakePlantRefusal):
                    room.step(0, "tick", at_device_us="1")
                self.assertEqual(room.close(), result)
                release.set()
                self.assertTrue(finished.wait(1))
                self.assertEqual(room.close(), result)
                self.assertEqual(timeline.count(("finalize",)), 1)
            finally:
                timer.cancel()
                release.set()
                finished.wait(1)


if __name__ == "__main__":
    unittest.main()
