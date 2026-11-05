"""The ground-truth format, `den-track-truth/1`, which rung 4's labeller will write.

JSON Lines, UTF-8, one take per file. The first line is the header; then come
frames and crossings, in any order:

    {"version": "den-track-truth/1", "kind": "header", "take": "2026-11-10-1", "people": ["A", "B"]}
    {"kind": "frame", "capture_ts": 1793066610.42, "labels": {"o-den-oak-1793066610420-0": "A", "o-den-oak-1793066610420-1": null}}
    {"kind": "crossing", "crossing_id": "c01", "type": "crossing", "interval": [1793066608.0, 1793066611.5]}

- A frame's `labels` give, per observation at that capture time, the true person
  or null for "not a person". One person may have two observations in a frame
  (a person_box and a keypoints).
- A person is one capital letter. People are A and B, never names.
- A crossing's `interval` is the owner's mark of the contact: from when two
  people come within arm's reach, or one goes out of sight, until they are apart
  again or back in view. The scorer pads it by `scoring.window_pad_s`.
"""
import re

import records

VERSION = 'den-track-truth/1'
CROSSING_TYPES = ('crossing', 'crossing_occluded', 'occlusion', 'near_pass', 'door')
PERSON = re.compile(r'^[A-Z]$')


class TruthError(Exception):
    pass


class Truth:
    def __init__(self, take, people, labels, frame_ts, crossings):
        self.take, self.people = take, people
        self.labels = labels            # obs_id -> person or None
        self.frame_ts = frame_ts        # obs_id -> the frame's capture_ts
        self.crossings = crossings      # [{'crossing_id', 'type', 'interval'}]


def parse(lines, source='truth'):
    if not lines or lines[0].get('kind') != 'header':
        raise TruthError('%s: the first line must be the header' % source)
    head = lines[0]
    if head.get('version') != VERSION:
        raise TruthError('%s: version is %r, this tool reads %s' % (source, head.get('version'), VERSION))
    people = head.get('people')
    if not isinstance(people, list) or not people or len(set(people)) != len(people):
        raise TruthError('%s: the header needs a list of distinct people' % source)
    for person in people:
        if not isinstance(person, str) or not PERSON.match(person):
            raise TruthError('%s: person %r is not one capital letter (people are A and B, never names)'
                             % (source, person))
    labels, frame_ts, crossings = {}, {}, []
    ids = set()
    for rec in lines[1:]:
        kind = rec.get('kind')
        if kind == 'frame':
            ts, row = rec.get('capture_ts'), rec.get('labels')
            if not records.is_number(ts) or not isinstance(row, dict):
                raise TruthError('%s: a frame needs a numeric capture_ts and a labels object' % source)
            for obs_id, person in row.items():
                if obs_id in labels:
                    raise TruthError('%s: observation %s is labelled twice' % (source, obs_id))
                if person is not None and person not in people:
                    raise TruthError('%s: observation %s has person %r, not in the header' % (source, obs_id, person))
                labels[obs_id], frame_ts[obs_id] = person, ts
        elif kind == 'crossing':
            cid, ctype, span = rec.get('crossing_id'), rec.get('type'), rec.get('interval')
            if not isinstance(cid, str) or not cid or cid in ids:
                raise TruthError('%s: crossing id %r is missing or repeated' % (source, cid))
            if ctype not in CROSSING_TYPES:
                raise TruthError('%s: crossing %s has type %r, one of %s' % (source, cid, ctype, list(CROSSING_TYPES)))
            if (not isinstance(span, list) or len(span) != 2 or not all(records.is_number(v) for v in span)
                    or span[0] > span[1]):
                raise TruthError('%s: crossing %s needs an interval [t0, t1] with t0 <= t1' % (source, cid))
            ids.add(cid)
            crossings.append({'crossing_id': cid, 'type': ctype, 'interval': [span[0], span[1]]})
        else:
            raise TruthError('%s: a line has kind %r (frame or crossing, after the header)' % (source, kind))
    crossings.sort(key=lambda c: (c['interval'][0], c['crossing_id']))
    return Truth(head.get('take'), people, labels, frame_ts, crossings)


def read(path):
    return parse(records.read_jsonl(path), path)


def write(path, take, people, frames, crossings):
    """frames: [(capture_ts, {obs_id: person})]; crossings: [{crossing_id, type, interval}]."""
    lines = [{'version': VERSION, 'kind': 'header', 'take': take, 'people': list(people)}]
    lines += [{'kind': 'frame', 'capture_ts': ts, 'labels': labels} for ts, labels in frames]
    lines += [dict(c, kind='crossing') for c in crossings]
    records.write_jsonl(path, lines)
