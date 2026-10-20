"""Explicit four-fixture fake-process owner; import performs no external I/O.

The caller supplies a reviewed provider constructor from an isolated guarded
host. This module imports no producer and grants no execution authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time

CLOSE_TIMEOUT_S = 10


def _require(condition, reason):
    if not condition:
        raise RoomFakePlantRefusal(reason)


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _request_value(value, depth=0):
    _require(depth <= 12, "step_value_bound")
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _require(len(value) <= 16384, "step_value_bound")
        return
    if type(value) is int:
        _require(-(1 << 63) <= value < 1 << 64, "step_value_bound")
        return
    if type(value) is list:
        _require(len(value) <= 256, "step_value_bound")
        for child in value:
            _request_value(child, depth + 1)
        return
    if type(value) is dict:
        _require(len(value) <= 128 and all(type(key) is str for key in value), "step_value_bound")
        for child in value.values():
            _request_value(child, depth + 1)
        return
    raise RoomFakePlantRefusal("step_value_invalid")


def _status(member):
    value = copy.deepcopy(member.recording_status)
    flags = ("complete", "cleanup_unconfirmed", "observer_failed", "terminal")
    _require(type(value) is dict and set(value) == {*flags, "active_callbacks"}
             and all(type(value[name]) is bool for name in flags)
             and type(value["active_callbacks"]) is int and value["active_callbacks"] >= 0,
             "member_status_unavailable")
    return value


def _document(path, maximum):
    path = Path(path)
    _require(path.is_absolute() and path.is_file() and not path.is_symlink(), "producer_file_invalid")
    _require(path.stat().st_size <= maximum, "producer_file_bound")
    raw = path.read_bytes()
    _require(0 < len(raw) <= maximum, "producer_file_bound")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "producer_duplicate_key")
            result[key] = value
        return result

    def invalid(_):
        raise RoomFakePlantRefusal("producer_json_invalid")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    _require(type(value) is dict, "producer_json_invalid")
    return raw, value


class RoomFakePlantRefusal(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class RoomFakePlant:
    def __init__(self, manifest_path, expected_manifest_sha256, recorder, *, fixture_ids, process_factory=None):
        self.manifest_path = manifest_path
        self.expected_manifest_sha256 = expected_manifest_sha256
        self.recorder = recorder
        self.fixture_ids = fixture_ids
        self.process_factory = process_factory
        self._attempted = False
        self._starting = False
        self._active_steps = 0
        self._started = False
        self._terminal = None
        self._members = [None] * 4
        self._header = None
        self._receipt = None
        self._closing = False
        self._state_lock = threading.RLock()

    def _abort(self, reason):
        with self._state_lock:
            if self._terminal is None:
                self._terminal = reason
                try:
                    self.recorder.abort(reason, finalize=False)
                except Exception:
                    pass

    def _binding(self):
        _require(_digest(self.expected_manifest_sha256), "manifest_pin_invalid")
        raw, manifest = _document(self.manifest_path, 131072)
        _require(hashlib.sha256(raw).hexdigest() == self.expected_manifest_sha256, "manifest_pin_mismatch")
        _require(manifest["schema"] == "c12-fake-plant-build-1" and type(manifest["version"]) is int
                 and manifest["version"] == 1 and manifest["abi"] == "c12-fake-plant-bridge-1"
                 and manifest["physical_permission"] is False and type(manifest["hardware_operations"]) is int
                 and manifest["hardware_operations"] == 0, "producer_manifest_invalid")
        producer = {"abi": manifest["abi"], "build_id": manifest["build_id"],
                    "manifest_sha256": self.expected_manifest_sha256, "source_sha256": manifest["source_digest"],
                    "config_sha256": manifest["config_sha256"],
                    "executable_sha256": manifest["executable"]["sha256"]}
        _require(all(_digest(producer[name]) for name in ("manifest_sha256", "source_sha256", "config_sha256",
                                                         "executable_sha256")), "producer_pins_invalid")
        header = self.recorder.header
        _require(type(header) is dict and header.get("producer") == producer
                 and header.get("evidence_source") == "simulated"
                 and header.get("physical_permission") is False, "recorder_producer_binding_mismatch")
        config_raw, config = _document(manifest["config"]["path"], 65536)
        _require(hashlib.sha256(config_raw).hexdigest() == manifest["config"]["file_sha256"]
                 and hashlib.sha256(_canonical(config)).hexdigest() == manifest["config"]["sha256"]
                 == producer["config_sha256"], "producer_config_pin_mismatch")
        _require(config["schema"] == "c12-fake-plant-config-1" and type(config["version"]) is int
                 and config["version"] == 1 and config["profile"] == "fictional-c12-v1"
                 and config["initial_clock_device_us"] == "0" and config["default_effect"] == "rejected"
                 and config["evidence_source"] == "simulated" and config["physical_permission"] is False,
                 "producer_config_invalid")
        ids = self.fixture_ids
        _require(type(ids) in (list, tuple) and len(ids) == 4
                 and all(type(value) is str and value.isascii() and 0 < len(value) <= 128 for value in ids)
                 and len(set(ids)) == 4, "fixture_ids_invalid")
        bindings, fixtures = header["fixtures"], config["fixtures"]
        _require(type(bindings) is list and type(fixtures) is list and len(bindings) == len(fixtures) == 4,
                 "fixture_bindings_invalid")
        for index, (bound, fixture) in enumerate(zip(bindings, fixtures)):
            _require(type(bound) is dict and type(fixture) is dict
                     and type(bound["fixture_index"]) is int and bound["fixture_index"] == index
                     and type(fixture["index"]) is int and fixture["index"] == index
                     and bound["fixture_id"] == ids[index] and bound["device_id"] == fixture["device_id"]
                     and bound["boot"] == fixture["boot"], "fixture_binding_mismatch")
        self._header = copy.deepcopy(header)

    def start(self):
        with self._state_lock:
            _require(not self._attempted and not self._closing and self._receipt is None, "room_already_started")
            self._attempted = True
            self._starting = True
        try:
            _require(self.recorder.ready is True, "recorder_not_ready")
            _require(callable(self.process_factory), "provider_unconfigured")
            self._binding()
            for index in range(4):
                with self._state_lock:
                    _require(not self._closing and self._terminal is None, "room_not_active")
                _require(self.recorder.ready is True, "recorder_not_ready")
                member = self.process_factory(self.manifest_path, self.expected_manifest_sha256,
                                              raw_observer=self.recorder.observer(index))
                with self._state_lock:
                    _require(not self._closing and self._terminal is None, "room_not_active")
                    self._members[index] = member
                result = member.start(index)
                with self._state_lock:
                    _require(not self._closing and self._terminal is None, "room_not_active")
                _require(type(result) is dict and result.get("ok") is True
                         and type(result.get("fixture_index")) is int and result["fixture_index"] == index,
                         "member_start_unconfirmed")
            with self._state_lock:
                _require(not self._closing and self._terminal is None, "room_not_active")
                self._started = True
            return {"status": "started", "members": [
                {"fixture_index": index, "launch_id": row["launch_id"]}
                for index, row in enumerate(self._header["fixtures"])]}
        except RoomFakePlantRefusal as error:
            self._abort(error.reason)
            raise
        except Exception:
            self._abort("member_start_failed")
            raise RoomFakePlantRefusal("member_start_failed") from None
        finally:
            with self._state_lock:
                self._starting = False

    def step(self, index, op, **fields):
        with self._state_lock:
            _require(self._started and not self._closing and self._terminal is None and self._receipt is None,
                     "room_not_active")
            self._active_steps += 1
        try:
            _require(self.recorder.ready is True, "recorder_not_ready")
            _require(type(index) is int and 0 <= index < 4, "fixture_index_invalid")
            shapes = {"tick": {"at_device_us"}, "disconnect": {"at_device_us"},
                      "observe": {"at_device_us", "observation"},
                      "set_effect": {"at_device_us", "driver", "effect"},
                      "exchange": {"at_device_us", "ingress_device_us", "packet_hex"}}
            _require(type(op) is str and op in shapes and set(fields) == shapes[op], "step_fields_invalid")
            for name in ("at_device_us", "ingress_device_us"):
                if name in fields:
                    value = fields[name]
                    _require(type(value) is str and re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is not None
                             and int(value) < 1 << 64, "step_device_time_invalid")
            _request_value(fields)
            if op == "set_effect":
                _require(fields["driver"] in ("can", "lighting")
                         and fields["effect"] in ("submitted", "rejected", "uncertain"), "step_effect_invalid")
            elif op == "observe":
                _require(type(fields["observation"]) is dict, "step_observation_invalid")
            elif op == "exchange":
                packet = fields["packet_hex"]
                _require(type(packet) is str and len(packet) <= 128 and len(packet) % 2 == 0
                         and re.fullmatch(r"[0-9a-f]*", packet) is not None, "step_packet_invalid")
            envelope = {"schema": "c12-fake-plant-bridge-1", "version": 1, "op": op,
                        "request_id": "request-100000", **fields}
            _require(len(_canonical(envelope)) + 1 <= 16384, "step_request_bound")
            result = self._members[index].request(op, **copy.deepcopy(fields))
            with self._state_lock:
                _require(not self._closing and self._terminal is None, "room_not_active")
            return copy.deepcopy(result)
        except RoomFakePlantRefusal as error:
            self._abort(error.reason)
            raise
        except Exception:
            self._abort("member_step_failed")
            raise RoomFakePlantRefusal("member_step_failed") from None
        finally:
            with self._state_lock:
                self._active_steps -= 1

    def close(self):
        with self._state_lock:
            if self._receipt is not None:
                return copy.deepcopy(self._receipt)
            _require(not self._closing, "room_close_in_progress")
            self._closing = True
            self._started = False
            if self._starting:
                self._abort("member_start_inflight")
            if self._active_steps:
                self._abort("member_step_inflight")
            owned_members = list(self._members)
        deadline = time.monotonic() + CLOSE_TIMEOUT_S
        pending = []
        for index, member in enumerate(owned_members):
            if member is None:
                pending.append(None)
                continue
            outcome = {}

            def cleanup(selected=member, recorded=outcome):
                try:
                    recorded["result"] = selected.close()
                except BaseException:
                    recorded["failed"] = True

            worker = threading.Thread(target=cleanup, name=f"room-fake-plant-close-{index}", daemon=True)
            worker.start()
            pending.append((worker, outcome))
        for item in pending:
            if item is not None:
                item[0].join(timeout=max(0, deadline - time.monotonic()))
        members = []
        for index, member in enumerate(owned_members):
            acknowledged = False
            status = None
            if member is not None:
                worker, outcome = pending[index]
                if worker.is_alive():
                    self._abort("member_cleanup_unconfirmed")
                elif outcome.get("failed"):
                    self._abort("member_close_failed")
                else:
                    result = outcome.get("result")
                    acknowledged = (type(result) is dict and result.get("closed") is True
                                    and result.get("terminal") is True and type(result.get("exit_code")) is int
                                    and result["exit_code"] == 0 and result.get("exit_reason") == "owner_close"
                                    and result.get("physical_permission", False) is False)
                    if not acknowledged:
                        self._abort("member_close_unconfirmed")
                try:
                    status = _status(member)
                except Exception:
                    self._abort("member_status_unavailable")
            else:
                self._abort("member_not_started")
            header = self._header if self._header is not None else self.recorder.header
            members.append({"fixture_index": index, "launch_id": header["fixtures"][index]["launch_id"],
                            "close_acknowledged": acknowledged, "recording_status": status})
        try:
            self._receipt = copy.deepcopy(self.recorder.finalize(members))
        except Exception:
            self._abort("recorder_finalization_failed")
            raise RoomFakePlantRefusal("recorder_finalization_failed") from None
        return copy.deepcopy(self._receipt)
