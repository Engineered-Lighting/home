"""A fake clock and a fake sender. Nothing here opens a socket or reads real time."""


class FakeClock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t

    def set(self, t):
        if t < self.t:
            raise ValueError('the fake clock never goes back (%.3f < %.3f)' % (t, self.t))
        self.t = float(t)

    def advance(self, dt):
        self.set(self.t + dt)


class FakeSender:
    """Records each request and answers it `latency_s` later.

    `script` maps a request's 1-based number to an outcome (`ok`, `refused`,
    `error`), or to `silent` (never answers: the router times it out), or to
    `raise` (send() raises at once). Unscripted requests answer `ok`.
    """

    def __init__(self, clock, latency_s=1.25, script=None):
        self.clock, self.latency_s = clock, latency_s
        self.script = dict(script or {})
        self.sent = []          # (time, request)
        self.pending = []       # (reply time, request_id, outcome)

    def send(self, request):
        self.sent.append((self.clock(), request))
        outcome = self.script.get(len(self.sent), 'ok')
        if outcome == 'raise':
            raise ConnectionError('fake sender: refused to connect')
        if outcome != 'silent':
            self.pending.append((self.clock() + self.latency_s, request['request_id'], outcome))

    def next_reply(self):
        return min(p[0] for p in self.pending) if self.pending else None

    def due(self, now):
        ready = sorted(p for p in self.pending if p[0] <= now + 1e-9)
        self.pending = [p for p in self.pending if p[0] > now + 1e-9]
        return [(rid, outcome) for _, rid, outcome in ready]
