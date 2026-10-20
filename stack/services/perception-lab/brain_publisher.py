"""Bounded, events-only outbox from the activity loop to the lighting brain.

One fixed route (contract v1: POST /api/lighting-brain/v1/homes/{home}/events)
with a per-producer bearer token read from private config. It never sends any
other route, so it cannot command a light. Events wait in a bounded buffer while
the brain is unreachable; the oldest go first when it is full, and anything past
its contract ttl is dropped because the brain would ignore it anyway.
"""
from collections import deque
from copy import deepcopy
import http.client
import json
import threading
import time
from urllib.parse import urlsplit

import activity_events

PRODUCER_TYPES = ('activity', 'person_pose', 'health', 'cost', 'person_position')
ROUTE = '/api/lighting-brain/v1/homes/{home}/events'
LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def origin_parts(origin):
    """https://host[:port], or http only to loopback (local development)."""
    parts = urlsplit(origin) if isinstance(origin, str) else None
    if (parts is None or parts.scheme not in ('https', 'http') or not parts.hostname or parts.path not in ('', '/')
            or parts.query or parts.fragment or parts.username or parts.password
            or (parts.scheme == 'http' and parts.hostname not in LOOPBACK)):
        raise ValueError('lighting brain origin must be https (or loopback http) with no path')
    return parts.scheme, parts.hostname, parts.port


def http_post(origin, path, body, token, timeout_s, *, ca_file=None):
    """POST one JSON body; returns the HTTP status. No redirects, no body read beyond 4 KiB."""
    scheme, host, port = origin_parts(origin)
    if scheme == 'https':
        import ssl
        context = ssl.create_default_context(cafile=ca_file)
        connection = http.client.HTTPSConnection(host, port, timeout=timeout_s, context=context)
    else:
        connection = http.client.HTTPConnection(host, port, timeout=timeout_s)
    try:
        connection.request('POST', path, body=body, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            'Content-Length': str(len(body))})
        response = connection.getresponse()
        response.read(4096)
        return response.status
    finally:
        connection.close()


class BrainPublisher:
    def __init__(self, *, origin, home, token, post=None, clock=time.time, monotonic=time.monotonic,
                 capacity=256, batch=32, timeout_s=3., backoff_s=(1., 30.), ca_file=None):
        origin_parts(origin)
        if not callable(token):
            raise ValueError('producer token reader required')
        if not (type(capacity) is int and 1 <= capacity <= 4096 and type(batch) is int and 1 <= batch <= 256
                and 0 < timeout_s <= 10 and 0 < backoff_s[0] <= backoff_s[1] <= 300):
            raise ValueError('bounded publisher settings required')
        self.origin, self.home, self.token, self.ca_file = origin, home, token, ca_file
        self.path = ROUTE.format(home=home)
        self.post = post or (lambda body: http_post(origin, self.path, body, token(), timeout_s, ca_file=ca_file))
        self.clock, self.monotonic = clock, monotonic
        self.capacity, self.batch, self.backoff_s = capacity, batch, tuple(backoff_s)
        self.queue = deque()
        self.lock = threading.Lock()
        self.retry_at = None
        self.delay = None
        self.counts = dict(submitted=0, sent=0, dropped_overflow=0, dropped_expired=0, refused=0, failed_posts=0)
        self.last_status = None

    def submit(self, event):
        """Queue one validated event; True when queued."""
        if not isinstance(event, dict) or event.get('type') not in PRODUCER_TYPES:
            raise activity_events.EventRefused('activity_loop_event_type_not_produced_here')
        if event.get('home') != self.home:
            raise activity_events.EventRefused('activity_event_home_mismatch')
        activity_events.check(event, sent=False)
        with self.lock:
            if len(self.queue) >= self.capacity:
                self.queue.popleft()
                self.counts['dropped_overflow'] += 1
            self.queue.append(deepcopy(event))
            self.counts['submitted'] += 1
        return True

    def _prune(self, now):
        kept = deque(e for e in self.queue if now < e['observed_at'] + e['ttl_s'])
        self.counts['dropped_expired'] += len(self.queue) - len(kept)
        self.queue = kept

    def flush(self):
        """Send at most one batch; returns 'idle', 'sent', 'waiting', 'refused' or 'failed'."""
        now, mono = self.clock(), self.monotonic()
        with self.lock:
            self._prune(now)
            if not self.queue:
                return 'idle'
            if self.retry_at is not None and mono < self.retry_at:
                return 'waiting'
            batch = [dict(e, sent_at=float(now)) for e in list(self.queue)[:self.batch]]
        body = json.dumps(activity_events.envelope(batch), sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode()
        try:
            status = self.post(body)
        except Exception:
            status = None
        with self.lock:
            self.last_status = status
            if status is not None and 200 <= status < 300:
                for _ in batch:
                    if self.queue:
                        self.queue.popleft()
                self.counts['sent'] += len(batch)
                self.retry_at = self.delay = None
                return 'sent'
            if status is not None and 400 <= status < 500 and status not in (401, 403, 408, 429):
                # A contract refusal (e.g. 422) will not change on retry.
                for _ in batch:
                    if self.queue:
                        self.queue.popleft()
                self.counts['refused'] += len(batch)
                return 'refused'
            self.counts['failed_posts'] += 1
            self.delay = self.backoff_s[0] if self.delay is None else min(self.backoff_s[1], self.delay * 2)
            self.retry_at = mono + self.delay
            return 'failed'

    def snapshot(self):
        with self.lock:
            return dict(queued=len(self.queue), capacity=self.capacity, last_status=self.last_status,
                        backoff_s=self.delay, **self.counts)
