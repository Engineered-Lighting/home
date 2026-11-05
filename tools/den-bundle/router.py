"""Router v0: who gets the next interpreter request, and when (ROUTER-V0.md).

The router owns no clock and no network. It reads time from `clock()` and hands
each request to `sender.send(request)`, which returns at once; the answer comes
back later through `on_result(request_id, outcome)`, with outcome `ok`,
`refused` (an admission refusal, LA's 429) or `error`. A sender that raises
counts as an error. Tests use fakes.FakeClock and fakes.FakeSender; nothing in
this tool talks to LA.
"""
from assemble import assemble, has_box
from records import EPS, REQUEST_VERSION

OUTCOMES = ('ok', 'refused', 'error')


class Router:
    def __init__(self, store, method, rule, clock, sender, scored=False):
        if scored:
            method.require_closed('a scored routed run')
            rule.require_closed('a scored routed run')
        self.store, self.method, self.rule = store, method, rule
        self.clock, self.sender = clock, sender
        self.starts = []             # start times of every request sent, refused or not
        self.in_flight = None        # the record of the one request in flight
        self.backoff_until = None
        self.last_served = None
        self.records = []
        self.bundles = []

    # -- the schedule -------------------------------------------------------
    def eligible(self, now):
        out = []
        for tid in self.store.order:
            line = self.store.latest(tid, now)
            if (line is not None and line.rec.get('state') in self.rule['eligible_states']
                    and line.ts >= now - self.method['window_s'] - EPS):
                out.append(tid)
        return out

    def due(self, now):
        """The earliest time >= now that a request may start, or None while one is in flight."""
        if self.in_flight is not None:
            return None
        t = now
        if self.starts:
            t = max(t, self.starts[-1] + self.rule['min_interval_s'])
        if self.backoff_until is not None:
            t = max(t, self.backoff_until)
        cap = self.rule['max_per_minute']
        if len(self.starts) >= cap:
            t = max(t, self.starts[-cap] + self.rule['minute_s'])
        return t

    def next_wake(self, now):
        """When tick() could next act: a due start, a timeout, or new track evidence."""
        if self.in_flight is not None:
            return self.in_flight['sent_ts'] + self.rule['timeout_s']
        due = self.due(now)
        if self.eligible(due):
            return due
        later = self.store.next_line_after(due)
        return None if later is None else max(due, later)

    def rotation(self, tracks):
        """Eligible tracks, starting after the one served last (first-seen order)."""
        order = self.store.order
        if self.last_served in order:
            i = order.index(self.last_served)
            order = order[i + 1:] + order[:i + 1]
        return [t for t in order if t in tracks]

    # -- acting ---------------------------------------------------------------
    def tick(self):
        """Act on the current time; returns the request record sent, or None."""
        now = self.clock()
        self._check_timeout(now)
        due = self.due(now)
        if due is None or due > now + EPS:
            return None
        tracks = self.eligible(now)
        for tid in self.rotation(tracks):
            self.last_served = tid
            bundle = assemble(self.store, self.method, tid, now)
            record = self._record(tid, now, bundle, tracks)
            if not has_box(bundle):
                record.update(outcome='no_box', done_ts=now)
                self.records.append(record)
                continue
            self.bundles.append(bundle)
            self.records.append(record)
            self.starts.append(now)
            self.in_flight = record
            try:
                self.sender.send({'request_id': record['request_id'], 'bundle': bundle,
                                  'rule': record['rule'], 'method': record['method']})
            except Exception as e:      # a sender that fails at once is an error
                self._finish('error', now, str(e) or type(e).__name__)
            return record
        return None

    def on_result(self, request_id, outcome, detail=None):
        """The sender's answer. False when it is not for the request in flight (late, or unknown)."""
        if outcome not in OUTCOMES:
            raise ValueError('outcome must be one of %s' % (OUTCOMES,))
        if self.in_flight is None or self.in_flight['request_id'] != request_id:
            return False
        self._finish(outcome, self.clock(), detail)
        return True

    def _check_timeout(self, now):
        rec = self.in_flight
        if rec is not None and now >= rec['sent_ts'] + self.rule['timeout_s'] - EPS:
            self._finish('timeout', rec['sent_ts'] + self.rule['timeout_s'], None)

    def _finish(self, outcome, at, detail):
        rec = self.in_flight
        self.in_flight = None
        rec['outcome'], rec['done_ts'] = outcome, at
        if detail:
            rec['detail'] = detail
        if outcome != 'ok':
            self.backoff_until = at + self.rule['backoff_s']
            rec['backoff_until'] = self.backoff_until

    def _record(self, tid, now, bundle, tracks):
        n = len(self.records) + 1
        return {
            'version': REQUEST_VERSION,
            'request_id': 'rq-%05d' % n,
            'track_id': tid,
            'sent_ts': now,
            'bundle_id': bundle['bundle_id'],
            'interval': list(bundle['interval']),
            'missing': list(bundle['missing']),
            'association_safe': bundle['association_safe'],
            'tracks': list(tracks),
            'per_person_period_s': max(self.rule['min_interval_s'],
                                       self.rule['minute_s'] / float(self.rule['max_per_minute'])) * len(tracks),
            'rule': self.rule.stamp(),
            'method': self.method.stamp(),
            'outcome': None,
            'done_ts': None,
            'backoff_until': None,
        }


def run(router, clock, sender, until):
    """Drive a router on a fake clock, event to event, until `until`. Returns its records."""
    while True:
        now = clock()
        for request_id, outcome in sender.due(now):
            router.on_result(request_id, outcome)
        router.tick()
        wakes = [w for w in (router.next_wake(now), sender.next_reply()) if w is not None]
        wakes = [w for w in wakes if w > now + EPS]
        if not wakes or min(wakes) > until + EPS:
            break
        clock.set(min(wakes))
    return router.records
