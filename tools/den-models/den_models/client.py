"""Small loopback client for an asynchronous Den bridge; no model imports."""
from http.client import HTTPConnection

from .contracts import ContractError, canonical, ident, strict_json
from .server import bearer_token


class Client:
    def __init__(self, port, token_file, timeout=.25):
        if type(port) is not int or not 1024 <= port <= 65535 or not 0 < timeout <= 2:
            raise ContractError("invalid_client_config")
        self.port = port
        self.token = bearer_token(token_file)
        self.timeout = timeout

    def _call(self, method, path, value=None):
        # Direct local socket: never use environment proxies or redirects that
        # could forward credentials/frame lineage outside the host.
        connection = HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            connection.request(method, path, body=None if value is None else canonical(value).encode(),
                               headers={"Authorization": "Bearer " + self.token,
                                        "Content-Type": "application/json", "Connection": "close"})
            response = connection.getresponse()
            raw = response.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                raise ContractError("oversize_sidecar_response")
            if not 200 <= response.status < 300:
                raise ContractError("sidecar_request_rejected")
            return strict_json(raw)
        finally:
            connection.close()

    def submit(self, request):
        return self._call("POST", "/v1/intent-jobs", request)

    def poll(self, request_id):
        if not ident(request_id):
            raise ContractError("invalid_request_identity")
        return self._call("GET", "/v1/intent-jobs/" + request_id)

    def cancel(self, request_id):
        if not ident(request_id):
            raise ContractError("invalid_request_identity")
        return self._call("DELETE", "/v1/intent-jobs/" + request_id)
