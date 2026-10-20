"""Explicit canonical ABI on the existing four-member durable raw recorder.

No provider imports or process/device I/O. Original pipe callbacks, fsync,
bounded finalization and incomplete outcomes are inherited without alteration.
"""
from __future__ import annotations
import copy
import os
from pathlib import Path
import threading

from room_fake_plant_recording import (RoomNativeRecorder, RecordingIncomplete,
    require, text, digest, u64, canonical, sha)

ABI = 'c12-canonical-capture-1'
RECORD_SCHEMA = 'home-room-canonical-plant-record-1'
RECEIPT_SCHEMA = 'home-room-canonical-plant-recording-receipt-1'


def validate_header(header):
    require(type(header) is dict and set(header) == {'session_id', 'producer', 'fixtures',
        'evidence_source', 'physical_permission', 'physical_truth'}, 'canonical_header_fields')
    text(header['session_id'])
    require(header['evidence_source'] == 'simulated' and header['physical_permission'] is False
            and header['physical_truth'] is False, 'canonical_header_provenance')
    producer = header['producer']
    require(type(producer) is dict and set(producer) == {'abi', 'build_id', 'manifest_sha256',
        'source_sha256', 'config_sha256', 'executable_sha256'}, 'canonical_producer_fields')
    require(producer['abi'] == ABI, 'canonical_producer_abi')
    text(producer['build_id'])
    for key in ('manifest_sha256', 'source_sha256', 'config_sha256', 'executable_sha256'):
        digest(producer[key])
    fixtures = header['fixtures']
    require(type(fixtures) is list and len(fixtures) == 4, 'canonical_four_fixtures')
    for index, item in enumerate(fixtures):
        require(type(item) is dict and set(item) == {'fixture_index', 'fixture_id', 'device_id',
            'boot', 'launch_id'} and type(item['fixture_index']) is int
            and item['fixture_index'] == index, 'canonical_fixture_fields')
        text(item['fixture_id']); text(item['launch_id'])
        require(type(item['device_id']) is str and len(item['device_id']) == 32
                and all(c in '0123456789abcdef' for c in item['device_id']), 'canonical_device_id')
        require(u64(item['boot']) > 0, 'canonical_boot')
    for key in ('fixture_id', 'device_id', 'launch_id'):
        require(len({item[key] for item in fixtures}) == 4, 'canonical_duplicate_fixture')
    canonical(header)


class RoomCanonicalRecorder(RoomNativeRecorder):
    def __init__(self, path, *, header, max_records=8192, max_bytes=33554432):
        # The existing constructor hard-codes the historical header ABI. Keep
        # that constructor unchanged; initialize its same bounded state here.
        validate_header(header)
        require(type(max_records) is int and 4 <= max_records <= 8192
                and type(max_bytes) is int and 4096 <= max_bytes <= 33554432, 'record_capacity_bounds')
        self.path = Path(path)
        self._header = copy.deepcopy(header)
        self._lock = threading.RLock()
        self._sequence = 0; self._previous = '0' * 64; self._bytes = 0
        self._max_records = max_records; self._max_bytes = max_bytes
        self._failure = None; self._closed = False; self._receipt = None; self._final_input = None
        self._final_guard = threading.RLock(); self._final_done = threading.Event()
        self._final_thread = None; self._final_members = None; self._storage_receipt = None; self._finishing = False
        self._state = [dict(ordinal=0, stdout_eof=False, stderr_eof=False, exit_code=None) for _ in range(4)]
        self._validators = [None] * 4
        self._file = self.path.open('xb')
        try:
            self._append('header', self._header)
        except BaseException as exc:
            self._failure = 'record_header_not_durable'; self._closed = True; self._file.close()
            raise RecordingIncomplete(self._failure) from exc

    def observer(self, fixture_index):
        from room_canonical_plant_replay import CanonicalStreamValidator
        require(type(fixture_index) is int and 0 <= fixture_index < 4, 'record_fixture_index')
        require(self._validators[fixture_index] is None, 'canonical_observer_already_bound')
        validator = CanonicalStreamValidator(self._header, fixture_index)
        self._validators[fixture_index] = validator
        durable = super().observer(fixture_index)
        def callback(event):
            # Preserve every byte and receipt clock before parsing any response.
            durable(event)
            try:
                if event['kind'] == 'stdin_attempt': validator.request(event['raw'])
                elif event['kind'] == 'stdout_bytes': validator.stdout(event['raw'])
                elif event['kind'] == 'stdout_eof': validator.finish()
            except BaseException as exc:
                self.abort('canonical_stream_invalid')
                raise RecordingIncomplete('canonical_stream_invalid') from exc
        return callback

    def validated_response(self, fixture_index):
        require(type(fixture_index) is int and 0 <= fixture_index < 4, 'record_fixture_index')
        validator = self._validators[fixture_index]
        require(validator is not None, 'canonical_observer_not_bound')
        with validator.lock:
            return copy.deepcopy(validator.last_response)

    def _append(self, kind, payload):
        require(not self._closed, 'record_already_finalized')
        limit = self._max_records if kind == 'final' else self._max_records - 1
        require(self._sequence < limit, 'record_count_exhausted')
        row = dict(schema=RECORD_SCHEMA, version=1, sequence=str(self._sequence + 1),
                   kind=kind, previous_sha256=self._previous, payload=payload)
        row['record_sha256'] = sha(canonical(row))
        raw = canonical(row) + b'\n'
        require(len(raw) <= 49152, 'record_row_byte_bound')
        reserve = 0 if kind == 'final' else 4096
        require(self._bytes + len(raw) + reserve <= self._max_bytes, 'record_byte_capacity_exhausted')
        require(self._file.write(raw) == len(raw), 'record_partial_write')
        self._file.flush(); os.fsync(self._file.fileno())
        self._sequence += 1; self._previous = row['record_sha256']; self._bytes += len(raw)

    def _result(self, members, *, clean, failure, durable, unconfirmed):
        result = super()._result(members, clean=clean, failure=failure, durable=durable, unconfirmed=unconfirmed)
        result.update(schema=RECEIPT_SCHEMA, physical_truth=False)
        return result
