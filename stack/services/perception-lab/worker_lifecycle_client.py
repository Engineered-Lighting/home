"""Fixed authenticated lifecycle transport; no caller-selected URLs or commands."""
from copy import deepcopy
import http.client
import json
import math
import re
import ssl
from urllib.parse import urlsplit


class StartRejected(Exception):
    """Trusted supervisor refused this new reservation before launching it."""


# Never used for a start: an unknown-request status read has no lease to act on.
PROBE_REQUEST = '00000000-0000-4000-8000-000000000000'


class LifecycleClient:
    def __init__(self, origin, credential, ca_file, site_id, *, connection_factory=None, probe_connection_factory=None):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or parsed.hostname not in {'192.168.1.168', '192.168.0.100'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'} or site_id not in {'victoria', 'echo'}):
            raise ValueError('Provisioned private worker origin required')
        self.host, self.port = parsed.hostname, parsed.port or 443
        self.credential, self.site_id = credential, site_id
        context = ssl.create_default_context(cafile=ca_file) if connection_factory is None else None
        self.connection = connection_factory or (lambda: http.client.HTTPSConnection(
            self.host, self.port, timeout=4, context=context))
        self.probe_connection = probe_connection_factory or connection_factory or (lambda: http.client.HTTPSConnection(
            self.host, self.port, timeout=2, context=context))
        self.sessions = {}
        self.variants = {}

    def probe(self):
        """Read-only supervisor reachability for setup readiness; [] when reachable.

        One authenticated status read of a request id that is never started: the
        supervisor answers request_not_found without starting, stopping or
        reserving anything. It proves reachability, not worker or GPU capacity."""
        try:
            token = self.credential()
        except Exception:
            token = None
        if not isinstance(token, str) or not 32 <= len(token) <= 512 or '\r' in token or '\n' in token:
            return [{'code': 'la_credential_unavailable'}]
        connection = self.probe_connection()
        try:
            connection.request('GET', '/v1/lifecycle/requests/' + PROBE_REQUEST, None, {'Authorization': 'Bearer ' + token})
            response = connection.getresponse()
            status, raw = response.status, response.read(1025)
        except ssl.SSLError:
            return [{'code': 'la_tls_failed'}]
        except (OSError, http.client.HTTPException):
            return [{'code': 'la_unreachable'}]
        finally:
            connection.close()
        try:
            body = json.loads(raw) if len(raw) <= 1024 else None
        except ValueError:
            body = None
        if status == 404 and body == {'reason': 'request_not_found'}:
            return []
        if status == 503 and body == {'reason': 'supervisor_unavailable'}:
            return [{'code': 'la_supervisor_unavailable'}]
        return [{'code': 'la_credential_rejected' if status == 401 else 'la_probe_unexpected'}]

    def _call(self, method, path, body, request_id):
        connection = self.connection()
        try:
            data = None if body is None else json.dumps(body, allow_nan=False).encode()
            token = self.credential()
            if not isinstance(token, str) or not 32 <= len(token) <= 512 or '\r' in token or '\n' in token:
                raise ValueError('Invalid lifecycle credential')
            connection.request(method, path, data, {'Authorization': 'Bearer ' + token,
                                                   'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            if method == 'POST' and path == '/v1/lifecycle/start' and response.status in {409, 503} and len(raw) <= 65536:
                error = json.loads(raw)
                if error == {'reason': 'worker_busy'} or error == {'reason': 'ledger_capacity'}:
                    raise StartRejected(error['reason'])
            if response.status not in {200, 202} or len(raw) > 65536:
                raise ValueError('Lifecycle response unavailable')
            value = json.loads(raw)
            if (value.get('version') != 1 or value.get('request_id') != request_id
                    or value.get('session_id') != self.sessions.get(request_id)
                    or value.get('site_id') != self.site_id
                    or value.get('variant','detector') != self.variants.get(request_id,'detector')
                    or value.get('state') not in {'starting', 'ready', 'stopping', 'stopped', 'failed', 'indeterminate'}
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value.get('engine_epoch', ''))):
                raise ValueError('Lifecycle binding mismatch')
            if value['state'] == 'ready':
                expiry = value.get('expires_at')
                profile = value.get('profile')
                if (type(expiry) not in {float, int} or not math.isfinite(expiry)
                        or not isinstance(profile, dict) or profile.get('engine_epoch') != value['engine_epoch']):
                    raise ValueError('Invalid ready receipt')
            return deepcopy(value)
        finally:
            connection.close()

    def start(self, request_id, session_id, variant='detector'):
        if variant not in {'detector','pose'}:raise ValueError('Unknown worker variant')
        if not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', request_id):
            raise ValueError('Canonical request UUID required')
        if not re.fullmatch(r'[0-9a-f]{32}', session_id):
            raise ValueError('Session identity required')
        if request_id in self.sessions and (self.sessions[request_id] != session_id or self.variants[request_id]!=variant):
            raise ValueError('Lifecycle request cannot change session')
        self.sessions[request_id] = session_id
        self.variants[request_id] = variant
        return self._call('POST', '/v1/lifecycle/start',
                          {'version': 1, 'request_id': request_id, 'session_id': session_id,**({'variant':variant} if variant!='detector' else {})}, request_id)

    def status(self, request_id):
        if request_id not in self.sessions:
            raise ValueError('Unknown lifecycle request')
        return self._call('GET', '/v1/lifecycle/requests/' + request_id, None, request_id)

    def stop(self, request_id, engine_epoch):
        if request_id not in self.sessions:
            raise ValueError('Unknown lifecycle request')
        return self._call('POST', '/v1/lifecycle/stop',
                          {'version': 1, 'request_id': request_id, 'engine_epoch': engine_epoch}, request_id)
