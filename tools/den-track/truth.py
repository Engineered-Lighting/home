"""Ground truth for E2: who each observation really is, per frame.

Format `den-track-truth/v1`, JSON Lines, one object per line, each with
`truth_version` and `line`:

    {"truth_version": "den-track-truth/v1", "line": "take",
     "take_id": "take-07", "people": ["A", "B"], "labeller": "owner",
     "blind": true, "observations_sha256": "sha256:..."}
    {"truth_version": "den-track-truth/v1", "line": "frame",
     "sensor_id": "den-oak", "capture_ts": 1793066610.42,
     "labels": [{"obs_id": "o-1", "person": "A"},
                {"obs_id": "o-2", "person": null}]}
    {"truth_version": "den-track-truth/v1", "line": "window",
     "window_id": "c01", "kind": "crossing",
     "start_ts": 1793066608.0, "end_ts": 1793066614.5}

- Exactly one `take` line. `people` lists the labels used, single capital
  letters (A, B, C...), never names. `observations_sha256`, when given, is
  the hash `den_track.py` prints for the take's observations file, so the
  labels are known to belong to those observations. `blind` says the
  labeller had not seen the tracker's output.
- One `frame` line per labelled frame. `person` is a label from `people`,
  null for "no person" (a false detection), or "?" for "cannot tell", which
  is left out of the score and counted.
- One `window` line per crossing or occlusion the owner marks: the scored
  unit. `kind` is crossing | crossing_occluded | occlusion | near_pass |
  entry_exit. Windows may not overlap.

Rung 4's two-person labeller is meant to write this file; until it exists,
synth.py writes it for synthetic takes.
"""
import re

import records

VERSION = 'den-track-truth/v1'
KINDS = ('crossing', 'crossing_occluded', 'occlusion', 'near_pass', 'entry_exit')
LABEL = re.compile(r'^[A-Z]$')
UNSURE = '?'


class TruthError(Exception):
    pass


def take_line(take_id, people, labeller='owner', blind=True, observations_sha256=None):
    rec = {'truth_version': VERSION, 'line': 'take', 'take_id': take_id, 'people': list(people),
           'labeller': labeller, 'blind': blind}
    if observations_sha256:
        rec['observations_sha256'] = observations_sha256
    return rec


def frame_line(sensor_id, capture_ts, labels):
    return {'truth_version': VERSION, 'line': 'frame', 'sensor_id': sensor_id, 'capture_ts': capture_ts,
            'labels': [{'obs_id': o, 'person': p} for o, p in labels]}


def window_line(window_id, kind, start_ts, end_ts):
    return {'truth_version': VERSION, 'line': 'window', 'window_id': window_id, 'kind': kind,
            'start_ts': start_ts, 'end_ts': end_ts}


def read(path):
    """Returns {'take', 'labels': {obs_id: (capture_ts, person)}, 'windows', 'unsure'}."""
    take, labels, windows, unsure = None, {}, [], 0
    for n, rec in records.read_jsonl(path):
        where = '%s line %d' % (path, n)
        if rec.get('truth_version') != VERSION:
            raise TruthError('%s: truth_version must be %s' % (where, VERSION))
        kind = rec.get('line')
        if kind == 'take':
            if take is not None:
                raise TruthError('%s: a second take line' % where)
            people = rec.get('people')
            if not isinstance(people, list) or not people or not all(
                    isinstance(p, str) and LABEL.match(p) for p in people):
                raise TruthError('%s: people must be single capital letters (A, B...), never names' % where)
            if len(set(people)) != len(people):
                raise TruthError('%s: a label appears twice in people' % where)
            take = rec
        elif kind == 'frame':
            if take is None:
                raise TruthError('%s: frame line before the take line' % where)
            ts = rec.get('capture_ts')
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                raise TruthError('%s: capture_ts must be a number' % where)
            for lab in rec.get('labels') or []:
                obs_id, person = lab.get('obs_id'), lab.get('person')
                if not isinstance(obs_id, str):
                    raise TruthError('%s: a label without obs_id' % where)
                if obs_id in labels:
                    raise TruthError('%s: obs_id %r labelled twice' % (where, obs_id))
                if person == UNSURE:
                    unsure += 1
                    continue
                if person is not None and person not in take['people']:
                    raise TruthError('%s: person %r is not in the take line\'s people' % (where, person))
                labels[obs_id] = (float(ts), person)
        elif kind == 'window':
            w = {'id': rec.get('window_id'), 'kind': rec.get('kind'),
                 'start': rec.get('start_ts'), 'end': rec.get('end_ts')}
            if not isinstance(w['id'], str) or w['kind'] not in KINDS:
                raise TruthError('%s: a window needs window_id and a kind in %s' % (where, ', '.join(KINDS)))
            if not all(isinstance(w[k], (int, float)) for k in ('start', 'end')) or w['end'] <= w['start']:
                raise TruthError('%s: window %s needs start_ts < end_ts' % (where, w['id']))
            windows.append(w)
        else:
            raise TruthError('%s: line must be take, frame or window' % where)
    if take is None:
        raise TruthError('%s: no take line' % path)
    windows.sort(key=lambda w: (w['start'], w['id']))
    if len({w['id'] for w in windows}) != len(windows):
        raise TruthError('%s: a window_id appears twice' % path)
    for a, b in zip(windows, windows[1:]):
        if b['start'] < a['end']:
            raise TruthError('%s: windows %s and %s overlap' % (path, a['id'], b['id']))
    return {'take': take, 'labels': labels, 'windows': windows, 'unsure': unsure}
