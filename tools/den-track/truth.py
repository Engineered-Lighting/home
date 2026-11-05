"""The ground-truth format, `den-track-truth/1`, which rung 4's labeller will write.

JSON Lines, UTF-8, one take per file. The first line is the header; then come
frames and crossings, in any order:

    {"version": "den-track-truth/1", "kind": "header", "take": "2026-11-10-1", "people": ["A", "B"],
     "labeller": "owner", "blind": true, "observations_sha256": "sha256:..."}
    {"kind": "frame", "capture_ts": 1793066610.42, "labels": {"o-den-oak-1793066610420-0": "A", "o-den-oak-1793066610420-1": null}}
    {"kind": "crossing", "crossing_id": "c01", "type": "crossing", "interval": [1793066608.0, 1793066611.5]}

- The header says how the labels were made, and a scored run refuses a file without
  it. `labeller` is `owner`, one capital letter, or `synthetic`, never a name. `blind`
  is true if the labeller had seen no system output: self-declared, so a scored run can
  only refuse a false one. `observations_sha256` is what `den_track.py hash` prints for
  the observations labelled, so the labels are known to belong to them.
- A frame's `labels` give, per observation at that capture time, the true person
  or null for "not a person". Rung 2 writes one observation per detector box, so a
  person normally has one; a duplicate box of one person gets the same letter.
- `?` is a label too: "cannot tell". The observation is left out of the score and
  counted, and its track line's status still counts for a switch across it. See METHOD.md.
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
UNSURE = '?'
LABELLER = re.compile(r'^(owner|synthetic|[A-Z])$')


class TruthError(Exception):
    pass


class Truth:
    def __init__(self, take, people, labels, frame_ts, crossings, labeller=None, blind=None, observations_sha256=None,
                 unsure=()):
        self.take, self.people = take, people
        self.labeller, self.blind = labeller, blind     # None: the file does not say
        self.observations_sha256 = observations_sha256
        self.labels = labels            # obs_id -> person or None
        self.unsure = set(unsure)       # obs_ids labelled "cannot tell"
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
    labeller, blind, obs_hash = head.get('labeller'), head.get('blind'), head.get('observations_sha256')
    if labeller is not None and not (isinstance(labeller, str) and LABELLER.match(labeller)):
        raise TruthError('%s: labeller %r is not owner, synthetic or one capital letter (never a name)' % (source, labeller))
    if blind is not None and not isinstance(blind, bool):
        raise TruthError('%s: blind must be true or false' % source)
    if obs_hash is not None and not (isinstance(obs_hash, str) and obs_hash.startswith('sha256:')):
        raise TruthError('%s: observations_sha256 must be what den_track.py hash prints' % source)
    labels, frame_ts, crossings, unsure = {}, {}, [], set()
    ids = set()
    for rec in lines[1:]:
        kind = rec.get('kind')
        if kind == 'frame':
            ts, row = rec.get('capture_ts'), rec.get('labels')
            if not records.is_number(ts) or not isinstance(row, dict):
                raise TruthError('%s: a frame needs a numeric capture_ts and a labels object' % source)
            for obs_id, person in row.items():
                if obs_id in frame_ts:
                    raise TruthError('%s: observation %s is labelled twice' % (source, obs_id))
                frame_ts[obs_id] = ts
                if person == UNSURE:
                    unsure.add(obs_id)
                    continue
                if person is not None and person not in people:
                    raise TruthError('%s: observation %s has person %r, not in the header' % (source, obs_id, person))
                labels[obs_id] = person
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
    return Truth(head.get('take'), people, labels, frame_ts, crossings, labeller, blind, obs_hash, unsure)


def read(path):
    return parse(records.read_jsonl(path), path)


def write(path, take, people, frames, crossings, labeller=None, blind=None, observations_sha256=None):
    """frames: [(capture_ts, {obs_id: person})]; crossings: [{crossing_id, type, interval}]."""
    head = {'version': VERSION, 'kind': 'header', 'take': take, 'people': list(people)}
    for key, value in (('labeller', labeller), ('blind', blind), ('observations_sha256', observations_sha256)):
        if value is not None:
            head[key] = value
    lines = [head]
    lines += [{'kind': 'frame', 'capture_ts': ts, 'labels': labels} for ts, labels in frames]
    lines += [dict(c, kind='crossing') for c in crossings]
    records.write_jsonl(path, lines)
