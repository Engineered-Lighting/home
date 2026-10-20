"""Recording-independent demand through the existing commissioned camera owner.

Only fixed loopback /frame-pair transport is provided. No device is opened,
endpoint selected from observations, or background acquisition started here.
"""
import base64
from copy import deepcopy
import http.client
import json
import math
import re
import threading
import time

import chrony_preview_client
import frame_ring

PAIR_MAX_BYTES = 8 * 1024**2


class CaptureOwnerRefusal(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate pair field')
        result[key] = value
    return result


def pair_transport(token, *, timeout=2.0):
    """One bounded authenticated request to the incumbent local camera owner."""
    if (not isinstance(token, str) or not 24 <= len(token) <= 1024
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
            or type(timeout) not in (float, int) or not 0 < timeout <= 2):
        raise CaptureOwnerRefusal('capture_transport_invalid')
    connection = http.client.HTTPConnection('127.0.0.1', 8090, timeout=timeout)
    timer = None
    try:
        connection.connect()
        sock = connection.sock
        def abort():
            try:
                import socket
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(timeout, abort)
        timer.daemon = True
        timer.start()
        connection.request('GET', '/frame-pair', headers={'Authorization': 'Bearer ' + token,
                           'Accept': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        fields = response.getheaders()
        headers = {key.lower(): value for key, value in fields}
        if (response.status != 200 or len(headers) != len(fields)
                or sum(len(k) + len(v) for k, v in fields) > 16384
                or headers.get('content-type') != 'application/json'
                or 'transfer-encoding' in headers or 'content-encoding' in headers):
            raise ValueError('pair transport rejected')
        length = headers.get('content-length', '')
        if not length.isascii() or not length.isdecimal() or not 0 < int(length) <= PAIR_MAX_BYTES:
            raise ValueError('pair size rejected')
        body = response.read(int(length) + 1)
        if len(body) != int(length):
            raise ValueError('pair body truncated')
        return body
    except Exception:
        raise CaptureOwnerRefusal('paired_capture_unavailable') from None
    finally:
        if timer:
            timer.cancel()
        connection.close()


_PHYSICAL_PAIR_TRANSPORT = pair_transport
_COMMISSIONED_RUNTIME = chrony_preview_client.commissioned_runtime


class _PairedPreviewClient(chrony_preview_client.ChronyPreviewClient):
    """Run the original commissioned acceptance over a fixed paired response."""
    def __init__(self, owner):
        self.owner = owner

    def runtime(self):
        return self.owner.runtime_factory()

    def fetch(self, pilot, path, token):
        self.owner._check()
        if path != '/frame.jpg':
            return pilot.fetch(path, token)
        timeout = min(2.0, self.owner.deadline - self.owner.monotonic())
        raw = self.owner.transport(token, timeout=timeout)
        received = self.owner.clock()
        self.owner._check()
        if not isinstance(raw, bytes) or not 0 < len(raw) <= PAIR_MAX_BYTES:
            raise CaptureOwnerRefusal('paired_capture_invalid')
        try:
            value = json.loads(raw, object_pairs_hook=_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'jpeg_b64', 'header', 'bundle_b64', 'calibration'}
                    or value['schema'] != 'home-camera-pair-1' or type(value['version']) is not int or value['version'] != 1
                    or not isinstance(value['header'], str) or not 0 < len(value['header']) <= 8192
                    or not isinstance(value['calibration'], dict)
                    or len(json.dumps(value['calibration'], allow_nan=False).encode()) > 24576):
                raise ValueError()
            jpeg = base64.b64decode(value['jpeg_b64'], validate=True)
            bundle = base64.b64decode(value['bundle_b64'], validate=True)
            if not 0 < len(jpeg) <= 256 * 1024 or not 0 < len(bundle) <= 4 * 1024**2:
                raise ValueError()
            with self.owner.state:
                if self.owner.closed or self.owner.acquired_generation != self.owner.generation:
                    raise CaptureOwnerRefusal('local_acquisition_denied')
                self.owner.pair = {'jpeg': jpeg, 'header': value['header'], 'bundle': bundle,
                                   'calibration': value['calibration'], 'received_at': received}
        except CaptureOwnerRefusal:
            raise
        except Exception:
            raise CaptureOwnerRefusal('paired_capture_invalid') from None
        return {'content-type': 'image/jpeg', 'x-victoria-frame-metadata': value['header']}, jpeg


class ControllerCaptureOwner:
    def __init__(self, *, clock=time.time, monotonic=time.monotonic, turns=None,
                 runtime_factory=chrony_preview_client.commissioned_runtime, pair_transport=pair_transport,
                 clock_authority_ref=None):
        if not all(callable(port) for port in (clock, monotonic, runtime_factory, pair_transport)):
            raise ValueError('trusted capture ports required')
        self.clock, self.monotonic = clock, monotonic
        physical_ports = runtime_factory is _COMMISSIONED_RUNTIME or pair_transport is _PHYSICAL_PAIR_TRANSPORT
        self.controller_scope = frame_ring.existing_controller_capture_scope() if turns is None or physical_ports else None
        shared_turns = self.controller_scope.require_turns() if self.controller_scope is not None else None
        if turns is not None and shared_turns is not None and turns is not shared_turns:
            raise ValueError('controller_capture_context_unavailable')
        self.turns = shared_turns if shared_turns is not None else turns
        self.runtime_factory, self.transport = runtime_factory, pair_transport
        if clock_authority_ref is not None and (
                not isinstance(clock_authority_ref, dict) or set(clock_authority_ref) != {'id', 'digest'}
                or not isinstance(clock_authority_ref['id'], str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', clock_authority_ref['id']) is None
                or re.fullmatch(r'[a-f0-9]{64}', str(clock_authority_ref['digest'])) is None):
            raise ValueError('pinned commissioned clock acceptor reference required')
        self.clock_authority_ref = deepcopy(clock_authority_ref)
        self.accepted_clock = None
        self.pair = self.deadline = self.cancel = self.authorized = None
        self.closed = False
        self.generation = 0
        self.acquired_generation = None
        self.active = threading.Lock()
        self.state = threading.Lock()
        self.client = _PairedPreviewClient(self)

    def _check(self):
        if self.controller_scope is not None:
            try:
                self.controller_scope.require_turns()
            except ValueError:
                raise CaptureOwnerRefusal('controller_capture_context_unavailable') from None
        with self.state:
            if self.closed:
                raise CaptureOwnerRefusal('capture_owner_closed')
            if self.acquired_generation != self.generation:
                raise CaptureOwnerRefusal('local_acquisition_denied')
            cancel, deadline, allowed = self.cancel, self.deadline, self.authorized
        try:
            cancelled = cancel.is_set() if callable(getattr(cancel, 'is_set', None)) else cancel()
            granted = allowed() is True
        except Exception:
            raise CaptureOwnerRefusal('local_acquisition_denied') from None
        if cancelled or not granted:
            raise CaptureOwnerRefusal('local_acquisition_denied')
        if self.monotonic() >= deadline:
            raise CaptureOwnerRefusal('capture_deadline_exceeded')

    def acquire(self, *, cancel, deadline, authorized):
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not callable(authorized)
                or not (callable(cancel) or callable(getattr(cancel, 'is_set', None)))):
            raise ValueError('bounded capture deadline, cancellation and local authority required')
        if not self.active.acquire(False):
            raise CaptureOwnerRefusal('capture_owner_busy')
        turns = None
        entered = held = False
        try:
            with self.state:
                self.cancel, self.deadline, self.authorized = cancel, deadline, authorized
                self.acquired_generation = self.generation
                self.pair = None
                self.accepted_clock = None
            self._check()
            turns = self.turns
            if not isinstance(turns, frame_ring.CaptureTurns):
                raise ValueError('existing controller capture turns required')
            turns.enter()
            entered = True
            if turns.other_busy() or not turns.acquire(False):
                raise CaptureOwnerRefusal('camera_capture_busy')
            held = True
            self._check()
            # _capture itself performs one attempt. Calling __call__ here would
            # reacquire the shared permit and introduce in-lock retries.
            self.client._capture()
            self._check()
            with self.state:
                if self.closed or self.pair is None:
                    raise CaptureOwnerRefusal('paired_capture_unavailable')
                result = deepcopy(self.pair)
                self.pair = None
                if self.clock_authority_ref is not None:
                    try:
                        receipt = json.loads(base64.b64decode(result['header'] + '=' * ((-len(result['header'])) % 4),
                                                             altchars=b'-_', validate=True), object_pairs_hook=_pairs)
                        if (not isinstance(receipt, dict) or receipt.get('schema') != 3
                                or receipt.get('validation_id') != 'causal:' + receipt['challenge_nonce']):
                            raise ValueError()
                    except Exception:
                        raise CaptureOwnerRefusal('capture_clock_authority_unproven') from None
                    self.accepted_clock = {'receipt': json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False),
                                           'generation': self.generation,
                                           'cancel': cancel, 'authorized': authorized}
                return result
        except CaptureOwnerRefusal:
            raise
        except Exception:
            raise CaptureOwnerRefusal('paired_capture_unavailable') from None
        finally:
            with self.state:
                self.pair = self.cancel = self.deadline = self.authorized = None
                self.acquired_generation = None
            if held:
                turns.release()
            if entered:
                turns.leave()
            self.active.release()

    def clear(self):
        """Discard only this producer's transient response; no shared ring reset."""
        with self.state:
            self.generation += 1
            self.pair = None
            self.accepted_clock = None

    def close(self):
        with self.state:
            self.closed = True
            self.pair = None
            self.accepted_clock = None

    def authorize_clock_receipt(self, receipt, authority_ref):
        """Attest only the exact header accepted by the commissioned pilot.

        This is an in-process capability, never an HTTP assertion or a model
        probability. One latest result is held and explicit local withdrawal
        invalidates it before the qualification adapter can use it.
        """
        if self.controller_scope is not None:
            try:
                self.controller_scope.require_turns()
            except ValueError:
                self.clear()
                return False
        try:
            if not isinstance(receipt, dict):
                return False
            encoded = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if len(encoded.encode()) > 8192:
                return False
        except (ValueError, TypeError, RecursionError):
            return False
        with self.state:
            accepted = self.accepted_clock
            if (self.closed or self.clock_authority_ref is None or authority_ref != self.clock_authority_ref
                    or accepted is None or accepted['generation'] != self.generation
                    or encoded != accepted['receipt']):
                return False
        try:
            cancel = accepted['cancel']
            cancelled = cancel.is_set() if callable(getattr(cancel, 'is_set', None)) else cancel()
            allowed = accepted['authorized']() is True
        except Exception:
            self.clear()
            return False
        if cancelled or not allowed:
            self.clear()
            return False
        with self.state:
            return not self.closed and self.accepted_clock is accepted and accepted['generation'] == self.generation


def create_controller_capture_owner(**fixed_ports):
    return ControllerCaptureOwner(**fixed_ports)
