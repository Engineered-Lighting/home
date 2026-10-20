"""Durable originals for four explicit offline native fixture processes.

Pure file/callback module: no provider import, device I/O or execution authority.
The footer describes recording, never admission or achieved lighting.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time


class RecordingIncomplete(ValueError):
    pass


def require(value, reason):
    if not value:
        raise RecordingIncomplete(reason)


def canonical(value):
    def check(x, depth=0):
        require(depth <= 12, 'record_depth_bound')
        if x is None or type(x) is bool:
            return
        if type(x) is str:
            require(x.isascii() and len(x) <= 32768, 'record_string_bound')
            return
        if type(x) is int:
            require(-(1 << 31) <= x < (1 << 32), 'record_integer_bound')
            return
        if type(x) is list:
            require(len(x) <= 128, 'record_array_bound')
            for child in x:
                check(child, depth+1)
            return
        require(type(x) is dict and len(x) <= 64 and all(type(k) is str and k.isascii() for k in x),
                'record_object_bound')
        for child in x.values():
            check(child, depth+1)
    check(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def u64(value):
    require(type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}', value) is not None
            and int(value) < 1 << 64, 'record_u64_string_required')
    return int(value)


def text(value, limit=128):
    require(type(value) is str and 0 < len(value) <= limit and value.isascii()
            and all(32 <= ord(c) <= 126 for c in value), 'record_identity_required')


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'record_digest_required')


def validate_header(h):
    require(type(h) is dict and set(h) == {'session_id', 'producer', 'fixtures', 'evidence_source',
                                         'physical_permission'}, 'record_header_fields')
    text(h['session_id'])
    require(h['evidence_source'] == 'simulated' and h['physical_permission'] is False,
            'record_header_provenance')
    p=h['producer']
    require(type(p) is dict and set(p) == {'abi', 'build_id', 'manifest_sha256', 'source_sha256',
                                         'config_sha256', 'executable_sha256'}, 'record_producer_fields')
    require(p['abi'] == 'c12-fake-plant-bridge-1', 'record_producer_abi')
    text(p['build_id'])
    for key in ('manifest_sha256','source_sha256','config_sha256','executable_sha256'):
        digest(p[key])
    require(type(h['fixtures']) is list and len(h['fixtures']) == 4, 'record_four_fixtures_required')
    for i, f in enumerate(h['fixtures']):
        require(type(f) is dict and set(f) == {'fixture_index','fixture_id','device_id','boot','launch_id'}
                and type(f['fixture_index']) is int and f['fixture_index'] == i, 'record_fixture_fields')
        text(f['fixture_id']); text(f['launch_id'])
        require(type(f['device_id']) is str and re.fullmatch('[0-9a-f]{32}', f['device_id']) is not None,
                'record_device_identity')
        require(u64(f['boot']) > 0, 'record_boot_required')
    for key in ('fixture_id', 'device_id', 'launch_id'):
        require(len({f[key] for f in h['fixtures']}) == 4, 'record_fixture_binding_duplicate')
    canonical(h)


STATUS_FIELDS={'complete','cleanup_unconfirmed','observer_failed','terminal','active_callbacks'}
COMMON={'schema','version','ordinal','kind','captured_monotonic_ns','captured_utc_ns'}
EXTRA={
    'stdin_attempt':{'request_id','raw'},
    'stdin_write_result':{'request_id','reported_bytes','flush_completed','error','effect',
                          'flush_captured_monotonic_ns','flush_captured_utc_ns'},
    'stdout_bytes':{'raw'}, 'stderr_bytes':{'raw'},
    'stdout_eof':set(), 'stderr_eof':set(), 'process_exit':{'exit_code'},
}


class RoomNativeRecorder:
    def __init__(self, path, *, header, max_records=8192, max_bytes=33554432):
        validate_header(header)
        require(type(max_records) is int and 4 <= max_records <= 8192
                and type(max_bytes) is int and 4096 <= max_bytes <= 33554432, 'record_capacity_bounds')
        self.path=Path(path)
        self._header=copy.deepcopy(header)
        self._lock=threading.RLock()
        self._sequence=0; self._previous='0'*64; self._bytes=0
        self._max_records=max_records; self._max_bytes=max_bytes
        self._failure=None; self._closed=False; self._receipt=None; self._final_input=None
        self._final_guard=threading.RLock(); self._final_done=threading.Event()
        self._final_thread=None; self._final_members=None; self._storage_receipt=None; self._finishing=False
        self._state=[dict(ordinal=0,stdout_eof=False,stderr_eof=False,exit_code=None) for _ in range(4)]
        self._file=self.path.open('xb')
        try:
            self._append('header', self._header)
        except BaseException as exc:
            self._failure='record_header_not_durable'; self._closed=True; self._file.close()
            raise RecordingIncomplete(self._failure) from exc

    @property
    def header(self):
        return copy.deepcopy(self._header)

    @property
    def ready(self):
        return not self._closed and not self._finishing and self._failure is None and self._sequence > 0

    def _append(self, kind, payload):
        require(not self._closed, 'record_already_finalized')
        limit=self._max_records if kind=='final' else self._max_records-1
        require(self._sequence < limit, 'record_count_exhausted')
        row=dict(schema='home-room-fake-plant-record-1',version=1,sequence=str(self._sequence+1),
                 kind=kind,previous_sha256=self._previous,payload=payload)
        row['record_sha256']=sha(canonical(row))
        raw=canonical(row)+b'\n'
        require(len(raw) <= 49152, 'record_row_byte_bound')
        # Reserve enough room for the explicit incomplete terminal receipt.
        reserve=0 if kind=='final' else 4096
        require(self._bytes+len(raw)+reserve <= self._max_bytes, 'record_byte_capacity_exhausted')
        require(self._file.write(raw) == len(raw), 'record_partial_write')
        self._file.flush(); os.fsync(self._file.fileno())
        self._sequence+=1; self._previous=row['record_sha256']; self._bytes+=len(raw)

    def observer(self, fixture_index):
        require(type(fixture_index) is int and 0 <= fixture_index < 4, 'record_fixture_index')
        def callback(event):
            callback_mono=str(time.monotonic_ns()); callback_utc=str(time.time_ns())
            with self._lock:
                try:
                    require(self.ready, self._failure or 'record_not_ready')
                    state=self._state[fixture_index]
                    kind=event.get('kind') if type(event) is dict else None
                    require(kind in EXTRA and set(event) == COMMON | EXTRA[kind], 'record_original_fields')
                    require(event['schema']=='c12-fake-plant-io-1' and type(event['version']) is int
                            and event['version']==1, 'record_original_abi')
                    ordinal=u64(event['ordinal'])
                    require(ordinal==state['ordinal']+1 and state['exit_code'] is None, 'record_original_order')
                    require(u64(event['captured_monotonic_ns']) <= u64(callback_mono), 'record_capture_after_callback')
                    u64(event['captured_utc_ns']); u64(callback_utc)
                    copied=copy.deepcopy(event); original=None
                    if 'raw' in copied:
                        raw=copied.pop('raw')
                        maximum={'stdin_attempt':16384,'stdout_bytes':4096,'stderr_bytes':4097}[kind]
                        require(type(raw) is bytes and 0 < len(raw) <= maximum, 'record_original_bytes_bound')
                        original=dict(base64=base64.b64encode(raw).decode('ascii'),bytes=len(raw),sha256=sha(raw))
                    if kind in ('stdout_bytes','stdout_eof'):
                        require(not state['stdout_eof'], 'record_stdout_after_eof')
                    if kind in ('stderr_bytes','stderr_eof'):
                        require(not state['stderr_eof'], 'record_stderr_after_eof')
                    if kind in ('stdin_attempt','stdin_write_result'):
                        text(event['request_id'])
                    if kind=='stdin_write_result':
                        n=event['reported_bytes']
                        # Preserve invalid/partial reported counts as failure
                        # evidence. They cannot qualify a confirmed stdin stream.
                        require(n is None or type(n) is int and -(1 << 31) <= n < 1 << 32, 'record_write_count')
                        require(type(event['flush_completed']) is bool and event['effect']=='unknown',
                                'record_write_effect_must_remain_unknown')
                        if event['error'] is not None: text(event['error'])
                        mono=event['flush_captured_monotonic_ns']; utc=event['flush_captured_utc_ns']
                        require((mono is None)==(utc is None), 'record_flush_clocks_pair')
                        if mono is not None:
                            require(u64(event['captured_monotonic_ns']) <= u64(mono) <= u64(callback_mono),
                                    'record_flush_clock_order')
                            u64(utc)
                        require(not event['flush_completed'] or mono is not None and event['error'] is None,
                                'record_flush_result_inconsistent')
                    if kind=='process_exit':
                        require(type(event['exit_code']) is int and -(1 << 31) <= event['exit_code'] < 1 << 32,
                                'record_exit_code')
                    payload=dict(fixture_index=fixture_index,launch_id=self._header['fixtures'][fixture_index]['launch_id'],
                        event=copied,original=original,callback_monotonic_ns=callback_mono,callback_utc_ns=callback_utc)
                    self._append('io',payload)
                    require(self._failure is None, 'record_invalidated_during_callback')
                    state['ordinal']=ordinal
                    if kind in ('stdout_eof','stderr_eof'): state[kind]=True
                    if kind=='process_exit': state['exit_code']=event['exit_code']
                except BaseException as exc:
                    self._failure=self._failure or (str(exc) if isinstance(exc,RecordingIncomplete) else 'record_io_failure')
                    raise RecordingIncomplete(self._failure) from exc
        return callback

    def abort(self, reason, *, finalize=False):
        text(reason,256)
        # This guard is never held over file I/O or a callback. Invalidation
        # cannot wait for an arbitrary storage syscall to finish.
        with self._final_guard:
            if self._receipt is not None:
                return copy.deepcopy(self._receipt)
            self._failure=self._failure or reason
            original_members=copy.deepcopy(self._final_members)
        if not finalize:
            return None
        unknown=original_members or [dict(fixture_index=i,launch_id=f['launch_id'],close_acknowledged=False,
            recording_status=None) for i,f in enumerate(self._header['fixtures'])]
        return self.finalize(unknown)

    def _validate_members(self, members):
        canonical(members)
        require(type(members) is list and len(members)==4,'record_four_member_statuses')
        for i,member in enumerate(members):
            require(type(member) is dict and set(member)=={'fixture_index','launch_id','close_acknowledged','recording_status'}
                    and type(member['fixture_index']) is int and member['fixture_index']==i
                    and member['launch_id']==self._header['fixtures'][i]['launch_id']
                    and type(member['close_acknowledged']) is bool,'record_member_status_binding')
            status=member['recording_status']
            require(status is None or type(status) is dict and set(status)==STATUS_FIELDS
                    and all(type(status[k]) is bool for k in STATUS_FIELDS-{'active_callbacks'})
                    and type(status['active_callbacks']) is int and 0 <= status['active_callbacks'] < 1 << 31,
                    'record_member_status_fields')

    def _result(self,members,*,clean,failure,durable,unconfirmed):
        return dict(schema='home-room-fake-plant-recording-receipt-1',version=1,
            status='complete' if clean else 'incomplete',failure_reason=failure,
            path=str(self.path),records=self._sequence,terminal_record_sha256=self._previous,
            members=copy.deepcopy(members),footer_durable=durable,cleanup_unconfirmed=unconfirmed,
            native_recomputed=False,home_room_replayed=False,evidence_source='simulated',physical_permission=False)

    def _finish_storage(self,members):
        # Arbitrary OS storage waits cannot be cancelled truthfully. Only this
        # daemon owns final writes/close; caller has one separate bounded wait.
        with self._lock:
            clean=self._failure is None
            unconfirmed=False
            for i,member in enumerate(members):
                status=member['recording_status'];state=self._state[i]
                unconfirmed=unconfirmed or status is None or status['cleanup_unconfirmed'] or status['active_callbacks']>0
                clean=clean and status is not None and member['close_acknowledged'] and status['complete'] \
                    and not status['cleanup_unconfirmed'] and not status['observer_failed'] \
                    and not status['terminal'] and status['active_callbacks']==0 \
                    and state['stdout_eof'] and state['stderr_eof'] and state['exit_code']==0
            failure=self._failure or (None if clean else 'record_members_not_cleanly_drained')
            payload=dict(status='complete' if clean else 'incomplete',failure_reason=failure,members=copy.deepcopy(members))
            durable=False
            try:
                self._append('final',payload);durable=True
            except BaseException:
                clean=False;failure='record_final_not_durable'
            try:
                self._file.close()
            except BaseException:
                clean=False;unconfirmed=True;failure='record_close_unconfirmed'
            with self._final_guard:
                if self._failure is not None:
                    clean=False;failure=self._failure
                self._closed=True
                self._storage_receipt=self._result(members,clean=clean,failure=failure,
                    durable=durable,unconfirmed=unconfirmed)
                self._final_done.set()

    def finalize(self, member_statuses):
        deadline=time.monotonic()+1
        self._validate_members(member_statuses)
        request=canonical(member_statuses)
        with self._final_guard:
            if self._final_input is not None:
                require(request==self._final_input,'record_changed_finalize_retry')
            if self._receipt is not None:
                return copy.deepcopy(self._receipt)
            if self._final_thread is None:
                self._final_input=request;self._final_members=copy.deepcopy(member_statuses);self._finishing=True
                self._final_thread=threading.Thread(target=self._finish_storage,args=(copy.deepcopy(member_statuses),),daemon=True)
                self._final_thread.start()
        done=self._final_done.wait(max(0,deadline-time.monotonic()))
        with self._final_guard:
            if self._receipt is None:
                if done and time.monotonic() <= deadline:
                    self._receipt=copy.deepcopy(self._storage_receipt)
                    if self._failure is not None:
                        self._receipt.update(status='incomplete',failure_reason=self._failure)
                else:
                    self._failure=self._failure or 'record_finalization_unconfirmed'
                    self._receipt=self._result(member_statuses,clean=False,failure=self._failure,
                        durable=False,unconfirmed=True)
            # First returned outcome is immutable. A late syscall/callback can
            # settle cleanup, but cannot upgrade this original unknown result.
            return copy.deepcopy(self._receipt)
