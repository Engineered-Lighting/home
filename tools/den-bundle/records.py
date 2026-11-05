"""JSON Lines in and out, and a store that dates track lines.

Records travel as JSON Lines: one record per line, UTF-8, LF (README of
docs/architecture/live-v1). Reading accepts CRLF too. A blank line is skipped;
anything else that is not a JSON object is an error with its line number.

The Track record carries no time of its own, so a track line is dated by the
newest `capture_ts` among the observations its `last_obs_ids` names, and never
earlier than the line before it for the same track. A line naming no known
observation (a coasting step) takes the date of the line before it; a track's
first line with no known observation cannot be placed and is counted in
`undated`.
"""
import json

EPS = 1e-6
BUNDLE_VERSION = 'person_bundle/v1'
REQUEST_VERSION = 'router_request/v0'


class RecordError(Exception):
    pass


def read_jsonl(path):
    out = []
    with open(path, encoding='utf-8', newline=None) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                raise RecordError('%s line %d: not JSON (%s)' % (path, n, e))
            if not isinstance(rec, dict):
                raise RecordError('%s line %d: not a JSON object' % (path, n))
            out.append(rec)
    return out


def write_jsonl(path, records):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def is_valid(obs):
    """`validity` is 'valid', or rejected with why. Absent counts as valid."""
    v = obs.get('validity')
    if v is None or v == 'valid':
        return True
    if isinstance(v, dict):
        return 'valid' in (v.get('status'), v.get('value'))
    return False


def camera_of(obs):
    return (obs.get('source') or {}).get('sensor_id')


class Line:
    __slots__ = ('ts', 'rec', 'index')

    def __init__(self, ts, rec, index):
        self.ts, self.rec, self.index = ts, rec, index

    @property
    def status(self):
        return (self.rec.get('association') or {}).get('status')

    @property
    def since(self):
        return (self.rec.get('association') or {}).get('since_ts')


class Store:
    def __init__(self, observations, track_lines):
        self.obs = {}
        for o in observations:
            oid = o.get('obs_id')
            if not isinstance(oid, str) or not isinstance(o.get('capture_ts'), (int, float)):
                raise RecordError('an observation needs a string obs_id and a numeric capture_ts: %r' % (oid,))
            if oid in self.obs:
                raise RecordError('obs_id %s appears twice' % oid)
            self.obs[oid] = o
        self.lines = {}          # track_id -> [Line] in file order, dates never decreasing
        self.order = []          # track ids, first seen first
        self.undated = 0
        for i, rec in enumerate(track_lines):
            tid = rec.get('track_id')
            if not isinstance(tid, str):
                raise RecordError('track line %d has no track_id' % (i + 1))
            known = [self.obs[o]['capture_ts'] for o in rec.get('last_obs_ids') or [] if o in self.obs]
            prev = self.lines.get(tid)
            ts = max(known) if known else None
            if prev:
                ts = prev[-1].ts if ts is None else max(ts, prev[-1].ts)
            if ts is None:
                self.undated += 1
                continue
            if tid not in self.lines:
                self.lines[tid] = []
                self.order.append(tid)
            self.lines[tid].append(Line(ts, rec, i))

    def lines_of(self, tid, until=None):
        lines = self.lines.get(tid, [])
        return lines if until is None else [l for l in lines if l.ts <= until + EPS]

    def latest(self, tid, at):
        lines = self.lines_of(tid, at)
        return lines[-1] if lines else None

    def listed(self, tid, until):
        """obs_id -> the association statuses of every line of `tid`, dated by `until`, that names it."""
        out = {}
        for line in self.lines_of(tid, until):
            for oid in line.rec.get('last_obs_ids') or []:
                if oid in self.obs:
                    out.setdefault(oid, set()).add(line.status)
        return out

    def next_line_after(self, t):
        later = [l.ts for lines in self.lines.values() for l in lines if l.ts > t + EPS]
        return min(later) if later else None
