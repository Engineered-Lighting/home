"""Bounded anonymous 2D viewing continuity, never identity or operational geometry.

The caller owns serialization and commits a preview only after its immutable
presentation has been persisted. No models, clocks, files or network are read.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import uuid

MAX_PERSONS = 16
SCOPE = {'site_id', 'camera_id', 'session_id', 'session_generation', 'arm', 'generation',
         'capture_epoch', 'engine_epoch', 'artifact_sha256', 'model_version'}
SOURCE = {'frame_id', 'request_id', 'observed_at', 'available_at', 'valid_until', 'width', 'height'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 256


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _basis(scope, source):
    return (isinstance(scope, dict) and set(scope) == SCOPE and isinstance(source, dict) and set(source) == SOURCE
            and all(_text(scope.get(k)) for k in SCOPE-{'session_generation', 'generation'})
            and scope['arm'] in {'a', 'b'}
            and all(type(scope[k]) is int and 0 <= scope[k] <= 9007199254740991 for k in ('session_generation', 'generation'))
            and len(scope['artifact_sha256']) == 64 and all(c in '0123456789abcdef' for c in scope['artifact_sha256'])
            and all(_text(source.get(k)) for k in ('frame_id', 'request_id'))
            and all(_finite(source.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
            and 0 <= source['observed_at'] <= source['available_at'] and source['observed_at'] < source['valid_until']
            and all(type(source.get(k)) is int and 1 <= source[k] <= 8192 for k in ('width', 'height')))


def _people(persons, source):
    if not isinstance(persons, list) or len(persons) > MAX_PERSONS:
        return None
    rows, seen = [], set()
    for person in persons:
        if not isinstance(person, dict) or not _text(person.get('instance_id')) or person['instance_id'] in seen:
            return None
        seen.add(person['instance_id'])
        box = person.get('box')
        if not isinstance(box, dict) or not all(_finite(box.get(k)) for k in ('x', 'y', 'w', 'h')):
            return None
        x, y, w, h = (box[k] for k in ('x', 'y', 'w', 'h'))
        if not 0 <= x < x+w <= source['width'] or not 0 <= y < y+h <= source['height']:
            return None
        rows.append((person['instance_id'], (x, y, x+w, y+h)))
    return rows


def _iou(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    area = lambda box: (box[2]-box[0])*(box[3]-box[1])
    return intersection/(area(a)+area(b)-intersection) if intersection else 0.


@dataclass(frozen=True)
class _Preview:
    association: object
    _state: dict
    _owner: object
    _revision: int
    _changed: bool = True


class ViewPersonAssociator:
    def __init__(self):
        self._seed = uuid.uuid4().hex
        self._owner, self._revision = object(), 0
        self._state = dict(epoch=_hash([self._seed, 0])[:32], resets=0, counter=0, tracks={},
                           scope=None, dimensions=None, last_key=None, last_time=None, fingerprint=None, output=None)

    def _reset(self, state, reason):
        retired = [dict(track_ref=ref, reason=reason) for ref in state['tracks']]
        state.update(resets=state['resets']+1, tracks={}, counter=0)
        state['epoch'] = _hash([self._seed, state['resets']])[:32]
        return retired

    def _packet(self, state, scope, source, assignments, retired, reason=None):
        return dict(schema='home-lab-view-person-association-1', version=1, scope=deepcopy(scope), source=deepcopy(source),
                    track_epoch=state['epoch'], status='unavailable' if reason else 'available', reason=reason,
                    assignments=assignments, retired=retired, viewing_only=True, identity_authority=False,
                    geometry_authority=False, physical_permission=False)

    def _proposal(self, packet, state, changed=True):
        return _Preview(deepcopy(packet), deepcopy(state), self._owner, self._revision, changed)

    def preview(self, scope, source, persons, *, current=True, clock_verified=True, uncertainty_s=None):
        """Return a copy without changing the bank.

        clock_verified denotes a usable finite local time, not commissioned
        exposure timing. Unknown uncertainty is allowed; a known bound must fit
        within the original RGB retirement deadline. No timing qualification is
        manufactured on the wire.
        """
        state = deepcopy(self._state)
        if not _basis(scope, source):
            self._reset(state, 'invalid_basis')
            state.update(scope=None, dimensions=None, last_key=None, last_time=None, fingerprint=None, output=None)
            return self._proposal(None, state)
        dimensions = (source['width'], source['height'])
        retired = []
        if state['scope'] != scope or state['dimensions'] != dimensions:
            retired = self._reset(state, 'scope_changed' if state['scope'] != scope else 'dimensions_changed')
            state.update(scope=deepcopy(scope), dimensions=dimensions, last_key=None, last_time=None, fingerprint=None, output=None)
        key = (source['frame_id'], source['request_id'])
        try:
            fingerprint = _hash([scope, source, persons])
        except (TypeError, ValueError):
            fingerprint = None
        rows = _people(persons, source)
        reason = ('person_budget_exceeded' if isinstance(persons, list) and len(persons) > MAX_PERSONS
                  else 'invalid_persons' if rows is None or fingerprint is None
                  else 'clock_unverified' if clock_verified is not True or uncertainty_s is not None and (not _finite(uncertainty_s) or uncertainty_s < 0)
                  else 'source_expired' if source['available_at'] + (uncertainty_s or 0) >= source['valid_until']
                  else 'not_current' if current is not True else None)
        duplicate = key == state['last_key'] and fingerprint is not None and fingerprint == state['fingerprint']
        if duplicate and (reason is None or reason == state['output']['reason']):
            return self._proposal(state['output'], state, False)
        if not duplicate and state['last_key'] is not None and (key[0] == state['last_key'][0] or key[1] == state['last_key'][1]):
            return self._proposal(self._packet(state, scope, source, [], [], 'frame_identity_conflict'), self._state, False)
        if not duplicate and state['last_time'] is not None and source['observed_at'] <= state['last_time']:
            return self._proposal(self._packet(state, scope, source, [], [], 'late_frame'), self._state, False)
        if reason:
            retired += self._reset(state, reason)
            packet = self._packet(state, scope, source, [], retired, reason)
        else:
            if state['last_time'] is not None and source['observed_at']-state['last_time'] > 2.:
                retired += self._reset(state, 'observation_gap')
            tracks = state['tracks']
            candidates = {i: {ref for ref, box in tracks.items() if _iou(row[1], box) >= .3} for i, row in enumerate(rows)}
            graph = {('new', i): {('old', ref) for ref in refs} for i, refs in candidates.items()}
            for ref in tracks:
                graph[('old', ref)] = {('new', i) for i, refs in candidates.items() if ref in refs}
            ambiguous = set()
            for i, row in enumerate(rows):
                for j in range(i+1, len(rows)):
                    if _iou(row[1], rows[j][1]) >= .3:
                        graph[('new', i)].add(('new', j));graph[('new', j)].add(('new', i))
                        ambiguous.update({('new', i), ('new', j)})
                if len(candidates[i]) > 1:
                    ambiguous.add(('new', i))
            ambiguous.update(node for node in graph if node[0] == 'old' and len(graph[node]) > 1)
            pending = list(ambiguous)
            while pending:
                for node in graph[pending.pop()]-ambiguous:
                    ambiguous.add(node);pending.append(node)
            assignments, next_tracks, continued = [], {}, set()
            for i, (identity, box) in enumerate(rows):
                if ('new', i) in ambiguous:
                    assignments.append(dict(instance_id=identity, track_ref=None, status='ambiguous', reason='competing_person_association'))
                    continue
                if candidates[i]:
                    ref = next(iter(candidates[i]));continued.add(ref);status = 'continued'
                else:
                    state['counter'] += 1
                    ref = _hash([state['epoch'], state['counter']])[:32];status = 'new'
                next_tracks[ref] = box
                assignments.append(dict(instance_id=identity, track_ref=ref, status=status, reason=None))
            retired += [dict(track_ref=ref, reason='competing_person_association' if ('old', ref) in ambiguous else 'person_missing')
                        for ref in tracks if ref not in continued]
            state['tracks'] = next_tracks
            packet = self._packet(state, scope, source, assignments, retired)
        state.update(last_key=key, last_time=source['observed_at'], fingerprint=fingerprint, output=deepcopy(packet))
        return self._proposal(packet, state)

    def commit(self, proposal):
        if not isinstance(proposal, _Preview) or proposal._owner is not self._owner or proposal._revision != self._revision:
            raise ValueError('stale_or_foreign_association_preview')
        if proposal._changed:
            self._state = deepcopy(proposal._state)
            self._revision += 1
