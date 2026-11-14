"""The coordinator's link to the den home's Home Assistant (coordinator plan C10, C12). Standard library.

The same Home Assistant user (`den-live`, admin, owner decision 08) and long-lived token as the Qwen ask's occupancy
read (occupancy.py, `ha-token.txt`), over the same REST API:
- GET  <ha_url>/api/states/<entity>              each entity's state and attributes;
- POST <ha_url>/api/services/<domain>/<service>  den_lighting.coordinator_plan, switch.turn_on/off, fixture actions.

It never follows a redirect (urllib would carry the bearer token to whatever host it names), never logs the token, the
URL or a reply's text, and answers a failure with a short sentence of its own.
"""
import http.client
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 3.0
POLL_S = 1.0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HaError(Exception):
    """A short, safe sentence: never the URL, the token or Home Assistant's reply."""


class HaClient:
    def __init__(self, url, token, *, cafile=None, timeout_s=TIMEOUT_S):
        handlers = [NoRedirect()]
        if url.startswith("https:"):
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))
        self.opener, self.base, self.token, self.timeout_s = urllib.request.build_opener(*handlers), url.rstrip("/"), token, timeout_s

    def _request(self, path, body=None):
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method="GET" if body is None else "POST",
                                         headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                                                  "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                return json.loads(response.read(1 << 20).decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            raise HaError("Home Assistant said %d" % exc.code) from None
        except Exception:   # never the text: it could hold the URL
            raise HaError("Home Assistant didn't answer") from None

    def state(self, entity):
        """{"state": ..., "attributes": {...}} or None when Home Assistant has no such entity."""
        try:
            got = self._request("/api/states/" + entity)
        except HaError as exc:
            if str(exc) == "Home Assistant said 404":
                return None
            raise
        return {"state": got.get("state"), "attributes": got.get("attributes") or {}} if isinstance(got, dict) else None

    def call(self, domain, service, data, *, response=False):
        suffix = "?return_response" if response else ""
        result = self._request("/api/services/%s/%s%s" % (domain, service, suffix), data)
        if response:
            value = result.get("service_response") if isinstance(result, dict) else None
            if not isinstance(value, dict):
                raise HaError("Home Assistant did not confirm the service result")
            return value



class HaSession:
    """One persistent connection to Home Assistant, for the FC13 sender (plan 05 §3.1), which calls a service every
    second while a spot is lit: the same user, token and rules as HaClient, without a TLS handshake per call.

    One per thread: it isn't thread-safe. It never follows a redirect (http.client doesn't), never retries a POST (a
    dropped call may still have reached Home Assistant), and opens a fresh connection before a call when the last one
    idled past IDLE_S, well inside the server's keep-alive."""

    IDLE_S = 30.0
    LIMIT = 1 << 20

    def __init__(self, url, token, *, cafile=None, timeout_s=TIMEOUT_S, clock=time.monotonic):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("Home Assistant URL must be http(s)://host[:port]")
        self.https, self.host, self.port = parts.scheme == "https", parts.hostname, parts.port
        self.prefix = parts.path.rstrip("/")
        self.context = ssl.create_default_context(cafile=cafile) if self.https else None
        self.token, self.timeout_s, self.clock = token, timeout_s, clock
        self.connection = self.used = None
        self.connections = 0

    def close(self):
        if self.connection is not None:
            self.connection.close()
        self.connection = None

    def _request(self, path, body, timeout_s):
        if self.connection is not None and self.clock() - self.used > self.IDLE_S:
            self.close()
        if self.connection is None:
            extra = {"context": self.context} if self.https else {}
            factory = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
            self.connection = factory(self.host, self.port, timeout=timeout_s, **extra)
            self.connections += 1
        elif self.connection.sock is not None:
            self.connection.sock.settimeout(timeout_s)
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            self.connection.request("GET" if body is None else "POST", self.prefix + path, body=data,
                                    headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                                             "Content-Type": "application/json"})
            response = self.connection.getresponse()
            status, text = response.status, response.read(self.LIMIT)
            if not response.isclosed() or response.will_close:
                self.close()   # an answer too long to finish, or the server's own close: the next call reconnects
        except Exception:   # never the text: it could hold the URL
            self.close()
            raise HaError("Home Assistant didn't answer") from None
        self.used = self.clock()
        if not 200 <= status < 300:
            raise HaError("Home Assistant said %d" % status)
        try:
            return json.loads(text.decode("utf-8") or "null")
        except ValueError:
            raise HaError("Home Assistant's answer wasn't JSON") from None

    def state(self, entity):
        """As HaClient.state: {"state": ..., "attributes": {...}}, or None when Home Assistant has no such entity."""
        try:
            got = self._request("/api/states/" + entity, None, self.timeout_s)
        except HaError as exc:
            if str(exc) == "Home Assistant said 404":
                return None
            raise
        return {"state": got.get("state"), "attributes": got.get("attributes") or {}} if isinstance(got, dict) else None

    def call(self, domain, service, data, *, response=False, timeout_s=None):
        suffix = "?return_response" if response else ""
        result = self._request("/api/services/%s/%s%s" % (domain, service, suffix), data,
                               self.timeout_s if timeout_s is None else timeout_s)
        if response:
            value = result.get("service_response") if isinstance(result, dict) else None
            if not isinstance(value, dict):
                raise HaError("Home Assistant did not confirm the service result")
            return value


class Poller(threading.Thread):
    """Reads `entities` every poll_s and hands {entity: state or None} to on_snapshot. A failed read hands nothing and
    keeps a short reason in `problem`; the coordinator treats a stale snapshot as unknown."""

    def __init__(self, client, entities, on_snapshot, *, poll_s=POLL_S, clock=time.monotonic):
        super().__init__(daemon=True, name="den-live-ha-poll")
        self.client, self.entities, self.on_snapshot, self.poll_s, self.clock = client, list(entities), on_snapshot, poll_s, clock
        self.halt, self.problem, self.counts = threading.Event(), "not read yet", {"reads": 0, "failed": 0}

    def poll_once(self):
        try:
            snapshot = {entity: self.client.state(entity) for entity in self.entities}
        except HaError as exc:
            self.counts["failed"] += 1
            self.problem = str(exc)
            return
        self.counts["reads"] += 1
        self.problem = None
        self.on_snapshot(snapshot)

    def run(self):
        while not self.halt.is_set():
            try:
                self.poll_once()
            except Exception:   # the coordinator's own failure: counted, never the thread's end
                self.counts["failed"] += 1
                self.problem = "the coordinator couldn't take Home Assistant's states"
            self.halt.wait(self.poll_s)
