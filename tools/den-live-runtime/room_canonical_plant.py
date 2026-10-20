"""Pinned canonical ABI on the existing explicit four-process room owner.

No provider discovery/import, encoder, scheduler, device or network path. The
trusted caller supplies the reviewed inert CanonicalCaptureProcess constructor.
"""
from __future__ import annotations
import copy
import hashlib

from room_fake_plant import (RoomFakePlant, RoomFakePlantRefusal, _require, _document,
                             _canonical, _digest)
from room_canonical_plant_recording import ABI, RoomCanonicalRecorder, validate_header
from room_canonical_plant_replay import _request, u64


class _RecordedMember:
    """Require returned projections to match the already-fsynced raw response."""
    def __init__(self, delegate, recorder):
        self.delegate, self.recorder, self.index = delegate, recorder, None
        self._close_result = None

    def _before(self):
        prior = self.recorder.validated_response(self.index)
        _require(type(prior) is dict, 'canonical_member_not_initialized')
        return u64(prior['step_id'])

    def _checked(self, result, expected_step):
        _require(type(result) is dict and result == self.recorder.validated_response(self.index),
                 'canonical_response_not_retained_and_validated')
        _require(u64(result['step_id']) == expected_step, 'canonical_response_not_fresh')
        return result

    def start(self, index):
        self.index = index
        _require(self.recorder.validated_response(index) is None, 'canonical_member_already_initialized')
        return self._checked(self.delegate.start(index), 2)

    def request(self, op, **fields):
        before = self._before()
        return self._checked(self.delegate.request(op, **fields), before + 1)

    def can_port(self, at, **fields):
        before = self._before()
        return self._checked(self.delegate.can_port(at, **fields), before + 1)

    def hal_fault(self, at, mode):
        before = self._before()
        return self._checked(self.delegate.hal_fault(at, mode), before + 1)

    def close(self):
        if self._close_result is not None:
            return copy.deepcopy(self._close_result)
        try:
            before = self._before()
        except Exception:
            before = None
        # A partial start still owns cleanup even when no response qualified.
        # The inherited room close bounds this call and records uncertainty.
        result = self.delegate.close()
        _require(before is not None, 'canonical_close_original_progress_unavailable')
        result = self._checked(result, before + 1)
        self._close_result = copy.deepcopy(result)
        return result

    @property
    def recording_status(self):
        return copy.deepcopy(self.delegate.recording_status)


class RoomCanonicalPlant(RoomFakePlant):
    def __init__(self, manifest_path, expected_manifest_sha256, recorder, *, fixture_ids, process_factory=None):
        self._canonical_factory = process_factory
        def factory(*args, **kwargs):
            return _RecordedMember(self._canonical_factory(*args, **kwargs), recorder)
        super().__init__(manifest_path, expected_manifest_sha256, recorder, fixture_ids=fixture_ids,
                         process_factory=factory if callable(process_factory) else None)

    def _binding(self):
        _require(isinstance(self.recorder, RoomCanonicalRecorder), 'canonical_durable_recorder_required')
        _require(_digest(self.expected_manifest_sha256), 'manifest_pin_invalid')
        raw, manifest = _document(self.manifest_path, 131072)
        _require(hashlib.sha256(raw).hexdigest() == self.expected_manifest_sha256, 'manifest_pin_mismatch')
        _require(manifest['schema'] == 'c12-canonical-capture-build-1' and type(manifest['version']) is int
                 and manifest['version'] == 1 and manifest['abi'] == ABI
                 and manifest['physical_permission'] is False and type(manifest['hardware_operations']) is int
                 and manifest['hardware_operations'] == 0, 'canonical_manifest_invalid')
        producer = {'abi': ABI, 'build_id': manifest['build_id'], 'manifest_sha256': self.expected_manifest_sha256,
            'source_sha256': manifest['source_digest'], 'config_sha256': manifest['config_sha256'],
            'executable_sha256': manifest['executable']['sha256']}
        _require(all(_digest(producer[key]) for key in ('manifest_sha256', 'source_sha256',
            'config_sha256', 'executable_sha256')), 'canonical_producer_pins')
        header = self.recorder.header; validate_header(header)
        _require(header['producer'] == producer, 'recorder_producer_binding_mismatch')
        config_raw, config = _document(manifest['config']['path'], 65536)
        _require(hashlib.sha256(config_raw).hexdigest() == manifest['config']['file_sha256']
                 and hashlib.sha256(_canonical(config)).hexdigest() == manifest['config']['sha256']
                 == producer['config_sha256'], 'producer_config_pin_mismatch')
        _require(config['schema'] == 'c12-canonical-capture-config-1' and type(config['version']) is int
                 and config['version'] == 1 and config['profile'] == 'fictional-c12-v1'
                 and config['initial_clock_device_us'] == '0' and config['default_effect'] == 'rejected'
                 and config['evidence_source'] == 'simulated' and config['physical_permission'] is False
                 and config['physical_truth'] is False
                 and config['coordinate_contract'] == 'joint-centidegrees-and-joint-degrees-per-second-to-canonical-arbiter-once',
                 'canonical_config_invalid')
        ids, fixtures = self.fixture_ids, config['fixtures']
        _require(type(ids) in (list, tuple) and len(ids) == 4 and len(set(ids)) == 4
                 and all(type(item) is str and item.isascii() and 0 < len(item) <= 128 for item in ids)
                 and type(fixtures) is list and len(fixtures) == 4, 'canonical_fixture_ids')
        for index, (bound, fixture) in enumerate(zip(header['fixtures'], fixtures)):
            _require(type(fixture) is dict and type(fixture['index']) is int and fixture['index'] == index
                     and bound['fixture_index'] == index and bound['fixture_id'] == ids[index]
                     and bound['device_id'] == fixture['device_id'] and bound['boot'] == fixture['boot'],
                     'canonical_fixture_binding')
        self._header = copy.deepcopy(header)

    def step(self, index, op, **fields):
        with self._state_lock:
            _require(self._started and not self._closing and self._terminal is None and self._receipt is None,
                     'room_not_active')
            self._active_steps += 1
        try:
            _require(self.recorder.ready is True, 'recorder_not_ready')
            _require(type(index) is int and 0 <= index < 4 and op not in ('initialize', 'close'),
                     'canonical_step_scope')
            _require(not set(fields).intersection(('schema', 'version', 'op', 'request_id')),
                     'canonical_reserved_step_metadata')
            request = {'schema': ABI, 'version': 1, 'request_id': 'request-100000', 'op': op, **fields}
            raw = _canonical(request) + b'\n'
            _request(raw, index, self._header)
            member = self._members[index]
            if op == 'can_port':
                result = member.can_port(u64(fields['at_device_us']),
                    **{key: copy.deepcopy(value) for key, value in fields.items() if key != 'at_device_us'})
            elif op == 'hal_fault':
                result = member.hal_fault(u64(fields['at_device_us']), fields['mode'])
            else:
                result = member.request(op, **copy.deepcopy(fields))
            with self._state_lock:
                _require(not self._closing and self._terminal is None, 'room_not_active')
            return copy.deepcopy(result)
        except RoomFakePlantRefusal as error:
            self._abort(error.reason); raise
        except Exception:
            self._abort('canonical_member_step_failed')
            raise RoomFakePlantRefusal('canonical_member_step_failed') from None
        finally:
            with self._state_lock: self._active_steps -= 1
