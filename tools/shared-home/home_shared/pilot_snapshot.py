"""One explicitly requested Victoria JPEG acceptance; no inference or image storage."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import threading
import time

from .host_clock_service import HostClockPublisher, load_config, _boot_id, _json, _point, _continuous
from .protocol import camera_frame

ROOT = Path('/home/engineeredlightingllm/victoria-camera-pilot')
CONFIG = ROOT / 'private/host-clock-config.json'
TOKEN_FILE = Path('/home/engineeredlightingllm/victoria-camera/private/camera.env')
MAX_JPEG = 256 * 1024


def read_token():
    # The existing capture directory has legacy group-write permissions. Require
    # the private credential directory/file themselves to remain owner-only.
    for path in (TOKEN_FILE.parent, TOKEN_FILE):
        info = path.lstat()
        if info.st_uid != 1000 or info.st_mode & 0o077 or stat.S_ISLNK(info.st_mode):
            raise ValueError('private camera credential required')
    fd = os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 1000 or info.st_mode & 0o077:
            raise ValueError('private camera credential required')
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValueError('credential file exceeds bound')
    values = [line.split('=', 1)[1].strip() for line in raw.decode('utf-8').splitlines()
              if line.startswith('VICTORIA_CAMERA_TOKEN=')]
    if len(values) != 1:
        raise ValueError('camera credential unavailable')
    value = values[0]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not 24 <= len(value) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('camera credential invalid')
    return value


def fetch(path, token):
    if path not in ('/health', '/frame.jpg'):
        raise ValueError('unregistered camera path')
    bound = MAX_JPEG if path == '/frame.jpg' else 8192
    connection = http.client.HTTPConnection('127.0.0.1', 8090, timeout=2)
    timer = None
    try:
        connection.connect()  # Literal loopback; no proxy, DNS, redirect or retry.
        sock = connection.sock
        def abort():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(2, abort)
        timer.daemon = True
        timer.start()
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + token, 'Connection': 'close'})
        response = connection.getresponse()
        pairs = response.getheaders()
        headers = {key.lower(): value for key, value in pairs}
        if (response.status != 200 or len(headers) != len(pairs)
                or sum(len(k) + len(v) for k, v in pairs) > 16384
                or 'transfer-encoding' in headers or 'content-encoding' in headers):
            raise ValueError('camera response rejected')
        length = headers.get('content-length', '')
        if not length.isascii() or not length.isdecimal() or not 1 <= int(length) <= bound:
            raise ValueError('camera body bound invalid')
        body = response.read(int(length) + 1)
        if len(body) != int(length):
            raise ValueError('camera body truncated')
        return headers, body
    finally:
        if timer:
            timer.cancel()
        connection.close()


def accept_once(config, token, *, publisher_factory=HostClockPublisher,
                transport=fetch, monotonic_ns=time.monotonic_ns, boot_id=_boot_id,
                point=_point, continuous=_continuous, on_phase=lambda phase: None):
    on_phase('decoder_import')
    from PIL import Image  # Load the decoder before starting the short clock TTL.
    def health():
        headers, body = transport('/health', token)
        if headers.get('content-type') != 'application/json':
            raise ValueError('camera health type invalid')
        value = _json(body)
        if not isinstance(value, dict) or value.get('healthy') is not True or not isinstance(value.get('session'), str):
            raise ValueError('camera unavailable')
        return value['session']

    on_phase('initial_health')
    session = health()
    on_phase('clock_publisher_open')
    publisher = publisher_factory(config)
    try:
        started = point()
        on_phase('clock_publication')
        evidence = publisher.publish_once()
        on_phase('jpeg_request')
        headers, jpeg = transport('/frame.jpg', token)
        on_phase('jpeg_timing_validation')
        received = monotonic_ns()
        if headers.get('content-type') != 'image/jpeg' or not 1 <= len(jpeg) <= MAX_JPEG:
            raise ValueError('JPEG response invalid')
        frame = camera_frame(jpeg, headers.get('x-victoria-frame-metadata'))
        expected_unix = evidence['checked_unix_ns'] / 1e9 + frame.observed_mono - evidence['checked_monotonic_ns'] / 1e9
        if (frame.timing_evidence_version != 2 or frame.camera_boot_id != boot_id()
                or frame.camera_boot_id != evidence['host_boot_id']
                or frame.host_clock_validation_id != evidence['validation_id']
                or frame.camera_host_uncertainty_seconds != evidence['uncertainty_seconds']
                or not evidence['checked_monotonic_ns'] <= received < evidence['valid_until_monotonic_ns']
                or not evidence['checked_monotonic_ns'] / 1e9 <= frame.observed_mono <= received / 1e9
                or abs(frame.observed_at - expected_unix) > frame.uncertainty_seconds
                or not 0 <= received / 1e9 - frame.observed_mono <= 3 - frame.uncertainty_seconds
                or frame.capture_session != session):
            raise ValueError('camera timing acceptance failed')
        on_phase('jpeg_decode')
        with Image.open(io.BytesIO(jpeg)) as decoded:
            if decoded.format != 'JPEG' or decoded.size != (640, 360):
                raise ValueError('decoded image dimensions invalid')
            decoded.load()
        on_phase('final_health')
        if health() != session or boot_id() != frame.camera_boot_id:
            raise ValueError('camera continuity changed')
        on_phase('final_freshness')
        finished = point()
        continuous(started, finished, config.ntp)
        if (not received <= finished.before <= finished.after < evidence['valid_until_monotonic_ns']
                or not 0 <= finished.after / 1e9 - frame.observed_mono <= 3 - frame.uncertainty_seconds):
            raise ValueError('camera receipt expired during validation')
        return {'status': 'accepted', 'site_id': 'victoria', 'camera_id': 'den_oak',
                'capture_session': session, 'frame_id': frame.frame_id,
                'jpeg_sha256': hashlib.sha256(jpeg).hexdigest(), 'jpeg_bytes': len(jpeg),
                'width': 640, 'height': 360, 'observed_at': frame.observed_at,
                'uncertainty_seconds': frame.uncertainty_seconds,
                'age_at_receipt_seconds': received / 1e9 - frame.observed_mono,
                'host_clock_validation_id': frame.host_clock_validation_id,
                'host_boot_id': frame.camera_boot_id}
    finally:
        publisher.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', required=True)
    parser.parse_args(argv)
    phase = ['provisioning']
    try:
        if sys.platform != 'linux' or os.geteuid() != 1000 or socket.gethostname() != 'victoria-den-jetson':
            raise ValueError('pilot host mismatch')
        config = load_config(CONFIG)
        if config.expected_hostname != 'victoria-den-jetson' or config.uid != 1000:
            raise ValueError('pilot provisioning mismatch')
        result = accept_once(config, read_token(), on_phase=lambda value: phase.__setitem__(0, value))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception:
        # Fixed internal stage only: no exception text, credentials or response bytes.
        print(f'Victoria snapshot acceptance unavailable at {phase[0]}; no inference was requested.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
