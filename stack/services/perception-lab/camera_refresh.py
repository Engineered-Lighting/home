"""Disabled-by-default camera refresh routes; no models or camera ownership.

The injected backend serializes calls onto the existing Victoria runtime owner
thread. accept must commit durable idempotency before returning. The gateway
namespace header is trusted only from a dedicated, provisioned service subject
with camera_refresh capability, never from an ordinary Lab reader.
"""
import asyncio
import base64
import hashlib
import json
import math
import re
import time
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse
from store import LabError


def install_camera_refresh(app, *, settings, require, bounded_body, backend=None, gateway_subject=None):
    prefix = '/api/perception-lab/v1/cameras/{camera_id}/context-refresh'
    rates = {}
    active = 0

    def identity(request, camera_id):
        principal = require(request, 'camera_refresh')
        if request.url.query:
            raise LabError('Refresh query parameters are not accepted', 422, 'invalid_request')
        if (backend is None or not gateway_subject or principal.subject != gateway_subject
                or settings.site_id != 'victoria' or camera_id != 'den'
                or not any(c.get('id') == camera_id for c in settings.cameras)):
            raise LabError('Refresh not commissioned', 503, 'refresh_unavailable')
        namespace = request.headers.get('x-home-refresh-namespace', '')
        if re.fullmatch(r'[a-f0-9]{64}', namespace) is None:
            raise LabError('Authenticated request context required', 403, 'refresh_context_required')
        namespace = hashlib.sha256((gateway_subject + ':' + namespace).encode()).hexdigest()
        now = time.monotonic()
        for key in list(rates):
            if now - rates[key][0] >= 60:
                del rates[key]
        if namespace not in rates:
            if len(rates) >= 1024:
                raise LabError('Refresh busy', 429, 'refresh_busy')
            rates[namespace] = [now, 0, 0]
        bucket = rates[namespace]
        index, maximum = (1, 2) if request.method == 'POST' else (2, 30)
        if bucket[index] >= maximum:
            raise LabError('Refresh rate exceeded', 429, 'refresh_rate_limited')
        bucket[index] += 1
        return namespace

    def request_id(value):
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, AttributeError):
            raise LabError('Canonical request UUID required', 422, 'invalid_request') from None
        return value

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate field')
            result[key] = value
        return result

    async def call(method, namespace, rid):
        nonlocal active
        if active >= 2:
            raise LabError('Refresh busy', 429, 'refresh_busy')
        active += 1
        try:
            value = await asyncio.wait_for(getattr(backend, method)(namespace, rid), timeout=2)
        except asyncio.TimeoutError:
            # Timeout is not permission to enqueue again or free inference.
            raise LabError('Refresh status unknown; query the same request ID', 504, 'refresh_indeterminate') from None
        finally:
            active -= 1
        if value is None:
            raise LabError('Request unavailable', 404, 'refresh_not_found')
        if method == 'image':
            try:
                encoded = value['image_base64']
                if (value['request_id'] != rid or not isinstance(encoded, str) or len(encoded) > 349528
                        or not isinstance(value['frame_id'], str) or not 1 <= len(value['frame_id']) <= 256
                        or any(type(value[k]) not in (int, float) or not math.isfinite(value[k])
                               for k in ('observed_at', 'valid_until'))
                        or value['valid_until'] != value['observed_at'] + 180):
                    raise ValueError()
                jpeg = base64.b64decode(encoded, validate=True)
                if not 4 <= len(jpeg) <= 262144 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
                    raise ValueError()
                return {k: value[k] for k in ('request_id', 'frame_id', 'observed_at', 'valid_until', 'image_base64')}
            except (KeyError, TypeError, ValueError):
                raise LabError('Invalid runtime image', 502, 'refresh_invalid_image') from None
        if (not isinstance(value, dict) or value.get('request_id') != rid
                or value.get('state') not in {'queued', 'dispatching', 'running', 'indeterminate', 'terminal'}
                or any(type(value.get(k)) not in (int, float) or not math.isfinite(value[k]) for k in ('accepted_at', 'deadline'))
                or value['deadline'] != value['accepted_at'] + 15):
            raise LabError('Invalid runtime receipt', 502, 'refresh_invalid_receipt')
        outcome = value.get('outcome')
        if outcome is not None and (not isinstance(outcome, str) or re.fullmatch(r'[a-z_]{1,64}', outcome) is None):
            raise LabError('Invalid runtime outcome', 502, 'refresh_invalid_receipt')
        return {**{k: value[k] for k in ('request_id', 'accepted_at', 'deadline', 'state')}, 'outcome': outcome}

    @app.post(prefix)
    async def refresh(camera_id: str, request: Request):
        namespace = identity(request, camera_id)
        try:
            value = json.loads(await bounded_body(request, 1024), object_pairs_hook=pairs)
            if (not isinstance(value, dict) or set(value) != {'version', 'request_id', 'site_id'}
                    or type(value['version']) is not int or value['version'] != 1 or value['site_id'] != 'victoria'):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise LabError('Invalid refresh request', 422, 'invalid_request') from None
        result = await call('accept', namespace, request_id(value['request_id']))
        return JSONResponse({'version': 1, 'site_id': 'victoria', 'camera_id': 'den', **result},
                            status_code=200 if result['state'] == 'terminal' else 202)

    @app.get(prefix + '/{rid}')
    async def status(camera_id: str, rid: str, request: Request):
        namespace = identity(request, camera_id)
        result = await call('status', namespace, request_id(rid))
        return {'version': 1, 'site_id': 'victoria', 'camera_id': 'den', **result}

    @app.get(prefix + '/{rid}/image')
    async def image(camera_id: str, rid: str, request: Request):
        namespace = identity(request, camera_id)
        result = await call('image', namespace, request_id(rid))
        return JSONResponse({'version': 1, 'site_id': 'victoria', 'camera_id': 'den', **result},
                            headers={'Cache-Control': 'private, no-store'})
