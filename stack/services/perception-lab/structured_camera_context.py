"""Bounded readback of the incumbent perceiver's original, words-only receipt.

This cache never schedules inference, captures media, opens a log, or renews an
original lease. Its daily perceiver scope is explicitly separate from Lab
recordings and supplies no geometric qualification or command authority.
"""
from copy import deepcopy
import re
import threading
import time

import la_fact_log
import qwen_scene as scene


SCHEMA = 'home-camera-structured-context-1'
MAX_METADATA_BYTES = 4096
MAX_CONTEXT_BYTES = 16384
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}')
_SHA = re.compile(r'[a-f0-9]{64}')
_SESSION = re.compile(r'[a-f0-9]{32}')
_METADATA_KEYS = {'reference', 'frames', 'sent_at', 'profile'}
_REFERENCE_KEYS = {'request_id', 'session_id', 'site_id', 'body_sha256', 'prompt_version'}
_FRAME_KEYS = {'media_id', 'frame_id', 'capture_epoch', 'observed_at'}
_PROVIDER_KEYS = {'state', 'pump_status', 'last_reason', 'outstanding', 'halted'}


def _token(value):
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _provider(value):
    if (not isinstance(value, dict) or set(value) != _PROVIDER_KEYS
            or not all(_token(value[key]) for key in ('state', 'pump_status'))
            or value['last_reason'] is not None and not _token(value['last_reason'])
            or any(type(value[key]) is not bool for key in ('outstanding', 'halted'))):
        raise ValueError('bounded_provider_status_required')
    return deepcopy(value)


def missing_context(*, site_id, camera_id, server_time=None):
    """Pure explicit fallback for a controller with no provisioned perceiver."""
    if not _token(site_id) or not _token(camera_id):
        raise ValueError('registered_camera_identity_required')
    return dict(schema=SCHEMA, version=1, site_id=site_id, camera_id=camera_id,
        provider=dict(state='unavailable', pump_status='unavailable', last_reason='not_provisioned',
                      outstanding=False, halted=False),
        scope=dict(kind='perceiver_daily', perceiver_session_id=None, lab_session_id=None),
        latest=None, freshness='missing', qualification=None, physical_commands=False,
        server_time=server_time if scene.finite(server_time) and server_time >= 0 else None)


def _checked(metadata, receipt, site_id, camera_id):
    if (not isinstance(metadata, dict) or set(metadata) != _METADATA_KEYS
            or not isinstance(receipt, dict) or set(receipt) != la_fact_log.RECEIPT_KEYS):
        raise ValueError('closed_original_required')
    reference, frames, profile = metadata['reference'], metadata['frames'], metadata['profile']
    if (not isinstance(reference, dict) or set(reference) != _REFERENCE_KEYS
            or not isinstance(profile, dict) or set(profile) != {'engine_epoch'}
            or type(frames) is not list or not scene.MIN_IMAGES <= len(frames) <= scene.MAX_IMAGES
            or any(not isinstance(frame, dict) or set(frame) != _FRAME_KEYS for frame in frames)):
        raise ValueError('closed_request_metadata_required')
    if (receipt['schema'] != scene.RECEIPT_SCHEMA or receipt['result_schema'] != scene.SCHEMA
            or receipt['site_id'] != site_id or receipt['camera_id'] != camera_id
            or receipt['prompt_version'] != scene.PROMPT_VERSION or receipt['origin'] != scene.ORIGIN
            or receipt['text_scope'] != 'digest_only_reply_not_retained'
            or type(receipt['prior_supplied']) is not bool
            or receipt['max_age_seconds'] != scene.MAX_AGE_SECONDS
            or not isinstance(receipt['session_id'], str) or not _SESSION.fullmatch(receipt['session_id'])
            or not _token(receipt['engine_epoch']) or not _token(receipt['capture_epoch'])):
        raise ValueError('original_identity_required')
    for key in ('request_id', 'artifact_sha256', 'body_sha256', 'context_digest', 'fingerprint'):
        if not isinstance(receipt[key], str) or not _SHA.fullmatch(receipt[key]):
            raise ValueError('original_digest_required')
    if (receipt['response_sha256'] is not None
            and (not isinstance(receipt['response_sha256'], str) or not _SHA.fullmatch(receipt['response_sha256']))):
        raise ValueError('original_response_digest_required')
    if (any(reference[key] != receipt[key] for key in _REFERENCE_KEYS)
            or profile['engine_epoch'] != receipt['engine_epoch']
            or any(not _token(frame[key]) for frame in frames for key in ('media_id', 'frame_id', 'capture_epoch'))
            or any(frame['capture_epoch'] != receipt['capture_epoch'] for frame in frames)
            or receipt['frame_ids'] != [frame['frame_id'] for frame in frames]
            or receipt['media_ids'] != [frame['media_id'] for frame in frames]
            or len(set(receipt['frame_ids'])) != len(frames)
            or len(set(receipt['media_ids'])) != len(frames)
            or receipt['evidence_roots'] != ['media:' + frame['media_id'] for frame in frames]):
        raise ValueError('original_request_binding_required')
    stamps = [frame['observed_at'] for frame in frames]
    if (not all(scene.finite(value) and value >= 0 for value in stamps)
            or any(a >= b for a, b in zip(stamps, stamps[1:]))
            or stamps[-1] - stamps[0] > scene.MAX_SPAN_SECONDS
            or receipt['window_start'] != stamps[0] or receipt['observed_at'] != stamps[-1]
            or not all(scene.finite(receipt[key]) and receipt[key] >= 0 for key in
                       ('window_start', 'observed_at', 'generated_at', 'available_at', 'first_received_at', 'valid_until'))
            or not scene.finite(metadata['sent_at'])
            or not receipt['observed_at'] <= metadata['sent_at'] <= receipt['generated_at'] <= receipt['available_at']
            or receipt['available_at'] != receipt['first_received_at']):
        raise ValueError('original_causal_times_required')
    original_until = receipt['original_valid_until']
    if (original_until is not None and (not scene.finite(original_until) or original_until < 0)
            or receipt['valid_until'] != min(receipt['observed_at'] + scene.MAX_AGE_SECONDS,
                                            original_until if original_until is not None else float('inf'))):
        raise ValueError('original_exclusive_lease_required')
    if receipt['status'] == 'valid':
        facts, reason = scene.validate_facts(receipt['facts'], prior_supplied=receipt['prior_supplied'])
        if (reason is not None or facts != receipt['facts'] or receipt['invalid_reason'] is not None
                or receipt['response_sha256'] is None):
            raise ValueError('closed_original_facts_required')
    elif (receipt['status'] != 'invalid' or receipt['facts'] is not None
          or receipt['invalid_reason'] not in scene.INVALID_REASONS):
        raise ValueError('original_result_status_required')
    if (receipt['fingerprint'] != scene.fingerprint(receipt)
            or len(scene.canonical(receipt).encode('utf-8')) > la_fact_log.MAX_RECEIPT_BYTES
            or len(scene.canonical(metadata).encode('utf-8')) > MAX_METADATA_BYTES):
        raise ValueError('bounded_original_fingerprint_required')
    return dict(metadata=deepcopy(metadata), receipt=deepcopy(receipt))


class StructuredCameraContext:
    """One latest original; no files, media bytes, scheduler, or session owner."""
    def __init__(self, *, site_id, camera_id, clock=time.time):
        if not _token(site_id) or not _token(camera_id) or not callable(clock):
            raise ValueError('fixed_camera_and_read_clock_required')
        self.site_id, self.camera_id, self.clock = site_id, camera_id, clock
        self.lock = threading.Lock()
        self.latest = None
        self.closed = False

    def observe(self, metadata, receipt):
        """FactLog's metadata-only callback; rejected input never replaces it."""
        with self.lock:
            if self.closed:
                return 'closed'
        try:
            value = _checked(metadata, receipt, self.site_id, self.camera_id)
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
            return 'refused'
        with self.lock:
            if self.closed:
                return 'closed'
            if self.latest is not None:
                previous = self.latest['receipt']
                if value['receipt']['request_id'] == previous['request_id']:
                    return 'duplicate' if value == self.latest else 'conflict'
                if value['receipt']['observed_at'] < previous['observed_at']:
                    return 'out_of_order'
                if value['receipt']['observed_at'] == previous['observed_at']:
                    return 'conflict'
            self.latest = value
        return 'accepted'

    def snapshot(self, provider):
        provider = _provider(provider)
        with self.lock:
            latest = deepcopy(self.latest)
        try:
            now = self.clock()
        except Exception:
            now = None
        clock_valid = scene.finite(now) and now >= 0
        result = missing_context(site_id=self.site_id, camera_id=self.camera_id, server_time=now)
        result.update(provider=provider, latest=latest)
        if not clock_valid:
            result['freshness'] = 'invalid'
        elif latest is not None:
            receipt = latest['receipt']
            result['scope']['perceiver_session_id'] = receipt['session_id']
            result['freshness'] = ('invalid' if receipt['status'] != 'valid' or not scene.causal(receipt, now)
                                   else 'stale' if now >= receipt['valid_until'] else 'current')
        if latest is not None:
            result['scope']['perceiver_session_id'] = latest['receipt']['session_id']
        if len(scene.canonical(result).encode('utf-8')) > MAX_CONTEXT_BYTES:
            raise ValueError('structured_context_size_exceeded')
        return result

    def close(self):
        with self.lock:
            self.closed = True
        return True
