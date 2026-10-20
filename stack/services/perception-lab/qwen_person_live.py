"""Opt-in person facts in the incumbent bounded Qwen pump and Lab read model.

No inference, service, thread or transport is started on import. Anonymous
membership remains local and never becomes qualified operational identity.
"""
from copy import deepcopy
import hashlib
import io
import json
import math
import re

import qwen_person_scene as scene
from qwen_live import FramesUnavailable, LiveSceneTransport, QwenLivePump, RegisteredPreviewFrames, spread
from qwen_transport import CompletionUnproven

EVENT = 'qwen.person_observation'
WIRE = 'home-lab-view-person-semantics-1'
SUBMIT_PATH = '/v1/lab/live-person-scene'
LOOKUP_PATH = SUBMIT_PATH + '/lookup'
HEX = re.compile(r'[a-f0-9]{64}\Z')
FLAGS = dict(viewing_only=True, identity_authority=False, geometry_authority=False,
             engagement_authority=False, physical_permission=False)
DECISION = dict(status='unavailable', reason='subject_semantics_unqualified_for_decision')


class PersonSceneTransport(LiveSceneTransport):
    def submit(self, envelope, *, budget=10):
        body = self._check_envelope(envelope)
        status, value = self._post(SUBMIT_PATH, body, budget, submission=True)
        return self._proof(status, value, envelope, submission=True)

    def lookup(self, reference, *, budget=10):
        if not isinstance(reference, dict) or 'body' in reference:
            raise CompletionUnproven('lookup_reference_invalid')
        body = scene.canonical(reference).encode()
        if len(body) > 16384:
            raise CompletionUnproven('lookup_reference_invalid')
        status, value = self._post(LOOKUP_PATH, body, budget, submission=False)
        return self._proof(status, value, reference, submission=False)


class PersonFrames(RegisteredPreviewFrames):
    """Read registered PNG/JPEG originals or existing RAM, without reencoding."""
    def __init__(self, store, *, arm='b'):
        super().__init__(store)
        self.arm = arm

    def select(self, session, now, *, count=2, span=1.):
        # Capture may already have a newer unanalysed image. Anchor to the
        # actual arm's latest completed original rather than starving forever
        # waiting for that newest image to acquire detector membership.
        latest = _latest_output(self.store, session, self.arm)
        if latest is None:
            raise FramesUnavailable('person_membership_unavailable')
        payload = latest[1]
        pool = self.candidates(session, now)
        anchor = next((f for f in pool if f['frame_id'] == payload.get('frame_id')
                       and f['capture_epoch'] == payload.get('capture_epoch')), None)
        if anchor is None:
            raise FramesUnavailable('person_original_unavailable')
        membership(self.store, session, anchor, now, arm=self.arm)
        chosen = spread([f for f in pool if f['capture_epoch'] == anchor['capture_epoch']
                         and f['observed_at'] <= anchor['observed_at']], now, count=count, span=span)
        return [dict(frame, data=self.read(session, frame)) for frame in chosen]

    def candidates(self, session, now):
        viewing = session.get('experiment_id') == 'apartment-viewing'
        if viewing:
            source = getattr(self.store, '_apartment_viewing_sources', {}).get(session['id'])
            if source is None:
                raise FramesUnavailable('frames_unavailable')
            try:
                source.current()
                source.prune()
                with source.lock:
                    rows = [deepcopy(entry['record']) for entry in source.entries.values()]
            except Exception:
                raise FramesUnavailable('frames_unavailable') from None
        else:
            with self.store.lock:
                if self.store.db.execute("SELECT 1 FROM media_deletions WHERE session_id=? AND state='pending'",
                                         (session['id'],)).fetchone():
                    raise FramesUnavailable('media_deleted')
                found = self.store.db.execute("SELECT id,size,sha256,mime_type,metadata FROM media WHERE session_id=? "
                    "AND mime_type IN ('image/jpeg','image/png') ORDER BY rowid DESC LIMIT ?",
                    (session['id'], self.scan_rows)).fetchall()
            rows = [{**dict(row), 'metadata': json.loads(row['metadata'])} for row in found]
        result = []
        for row in rows:
            meta = row['metadata']
            if (row.get('mime_type') not in ('image/jpeg', 'image/png') or viewing and row.get('viewing_only') is not True
                    or meta.get('stream') != 'rgb' or meta.get('clock_mapping_status') != 'verified'
                    or meta.get('site_id') != session['site_id'] or meta.get('camera_id') != session['camera_id']
                    or (meta.get('width'), meta.get('height')) != scene.IMAGE_SIZE
                    or not all(scene.finite(meta.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
                    or not isinstance(meta.get('frame_id'), str) or not isinstance(meta.get('capture_epoch'), str)
                    or not 0 <= meta['observed_at'] <= meta['available_at'] <= now
                    or viewing and not now < meta['valid_until']
                    or not 0 < row['size'] <= scene.MAX_IMAGE_BYTES):
                continue
            result.append(dict(media_id=row['id'], frame_id=meta['frame_id'], capture_epoch=meta['capture_epoch'],
                               observed_at=meta['observed_at'], available_at=meta['available_at'],
                               valid_until=meta['valid_until'], size=row['size'], sha256=row['sha256'], mime_type=row['mime_type']))
        return result

    def read(self, session, frame):
        from apartment_viewing_source import is_viewing_id, registered_media, viewing_bytes
        from PIL import Image
        try:
            if is_viewing_id(frame['media_id']):
                record = registered_media(self.store, session['id'], frame['media_id'], session['generation'], fresh=True)
                data = viewing_bytes(self.store, session['id'], frame['media_id'], session['generation'], fresh=True)
            else:
                record = self.store.get_media(session['id'], frame['media_id'], generation=session['generation'])
                with self.store.media_path(session['id'], frame['media_id']).open('rb') as original:
                    data = original.read(record['size'] + 1)
            if (record['sha256'] != frame['sha256'] or record['size'] != frame['size']
                    or record['mime_type'] not in ('image/jpeg', 'image/png')
                    or record['metadata']['frame_id'] != frame['frame_id']
                    or len(data) != frame['size'] or hashlib.sha256(data).hexdigest() != frame['sha256']):
                raise ValueError('changed original')
            expected = 'JPEG' if record['mime_type'] == 'image/jpeg' else 'PNG'
            with Image.open(io.BytesIO(data)) as image:
                if (image.format != expected or image.size != scene.IMAGE_SIZE or image.mode not in ('RGB', 'L')
                        or getattr(image, 'n_frames', 1) != 1):
                    raise ValueError('bounded original required')
                image.load()
            return data
        except Exception:
            raise FramesUnavailable('frame_read_failed') from None


def _same(a, b):
    return scene.canonical(a) == scene.canonical(b)


def _latest_output(store, session, arm):
    with store.lock:
        state = store.db.execute('SELECT generation FROM lab_experiment_state WHERE session_id=?', (session['id'],)).fetchone()
        rows = store.db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output' "
                                "AND json_extract(payload,'$.arm')=? ORDER BY id DESC LIMIT 128", (session['id'], arm)).fetchall()
    if state is None:
        return None
    candidates = [(row, json.loads(row['payload'])) for row in rows]
    candidates = [(row, payload) for row, payload in candidates if payload.get('generation') == state[0]
                  and scene.finite(payload.get('observed_at'))]
    return max(candidates, key=lambda pair: (pair[1]['observed_at'], pair[0]['id']), default=None)


def membership(store, session, frame, now, *, arm='b'):
    """Freeze the exact completed original and current experiment membership.

    No nearest-frame fallback, order-based identity or private caller roster.
    The original is selected from the real runner ledger, not supplied by UI.
    """
    if arm not in ('a', 'b') or not scene.finite(now):
        raise FramesUnavailable('person_membership_unavailable')
    latest = _latest_output(store, session, arm)
    if latest is None:
        raise FramesUnavailable('person_membership_unavailable')
    for row, payload in [latest]:
        if (payload.get('frame_id') != frame.get('frame_id') or payload.get('capture_epoch') != frame.get('capture_epoch')
                or payload.get('current') is not True):
            continue
        shown = payload.get('arm_snapshot', {})
        people = shown.get('persons2d', {})
        association = people.get('association', {})
        scope, source = association.get('scope', {}), association.get('source', {})
        assignments, persons = association.get('assignments'), people.get('persons')
        expected = dict(site_id=session['site_id'], camera_id=session['camera_id'], session_id=session['id'],
                        session_generation=session['generation'], arm=arm, generation=payload['generation'], capture_epoch=frame['capture_epoch'])
        if (payload.get('status') != 'completed' or association.get('schema') != 'home-lab-view-person-association-1'
                or association.get('version') != 1 or association.get('status') != 'available'
                or association.get('reason') is not None
                or any(scope.get(k) != v for k, v in expected.items())
                or any(scope.get(k) != payload.get(k) for k in ('engine_epoch', 'artifact_sha256', 'model_version'))
                or association.get('viewing_only') is not True
                or any(association.get(k) is not False for k in ('identity_authority', 'geometry_authority', 'physical_permission'))
                or not isinstance(association.get('track_epoch'), str)
                or not isinstance(assignments, list) or not isinstance(persons, list)
                or not 1 <= len(persons) <= scene.MAX_SUBJECTS or len(assignments) != len(persons)
                or source.get('request_id') != payload.get('request_id')
                or source.get('frame_id') != frame['frame_id'] or source.get('observed_at') != frame['observed_at']
                or source.get('available_at') != payload.get('available_at')
                or not all(scene.finite(source.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
                or not source['observed_at'] <= source['available_at'] <= now < source['valid_until']
                or source['valid_until'] > frame['valid_until']
                or shown.get('frame', {}).get('media_id') != frame['media_id']
                or (source.get('width'), source.get('height')) != scene.IMAGE_SIZE):
            raise FramesUnavailable('person_membership_unavailable')
        ids = [p.get('instance_id') for p in persons]
        assigned = {a.get('instance_id'): a for a in assignments}
        refs = [a.get('track_ref') for a in assignments]
        if (len(set(ids)) != len(ids) or set(ids) != set(assigned) or len(assigned) != len(assignments)
                or any(not isinstance(ref, str) or not 0 < len(ref) <= 256 for ref in refs)
                or len(set(refs)) != len(refs)
                or any(a.get('status') not in ('new', 'continued') or a.get('reason') is not None for a in assignments)):
            raise FramesUnavailable('person_membership_ambiguous')
        subjects = []
        for index, person in enumerate(sorted(persons, key=lambda p: p['instance_id']), 1):
            box = person.get('box', {})
            if not all(scene.finite(box.get(k)) for k in ('x', 'y', 'w', 'h')):
                raise FramesUnavailable('person_box_unavailable')
            x, y = math.floor(box['x']), math.floor(box['y'])
            right, bottom = math.ceil(box['x'] + box['w']), math.ceil(box['y'] + box['h'])
            if not 0 <= x < right <= scene.IMAGE_SIZE[0] or not 0 <= y < bottom <= scene.IMAGE_SIZE[1]:
                raise FramesUnavailable('person_box_unavailable')
            subjects.append(dict(subject_slot='person-' + str(index), instance_id=person['instance_id'],
                                 track_ref=assigned[person['instance_id']]['track_ref'],
                                 box=dict(x=x, y=y, w=right-x, h=bottom-y)))
        return dict(basis=deepcopy({k: association[k] for k in ('scope', 'source', 'track_epoch')}),
                    subjects=subjects, source_event_id=row['id'], source_payload_digest=scene.digest(payload),
                    evidence=dict(media_id=frame['media_id'], sha256=frame['sha256'], width=source['width'],
                                  height=source['height'], frame_id=frame['frame_id'], capture_epoch=frame['capture_epoch']))
    raise FramesUnavailable('person_membership_unavailable')


def _packet(request, facts, reason, now):
    frozen = request['membership']
    by_slot = {row['subject_slot']: row['facts'] for row in facts['subjects']} if facts is not None else {}
    packet = dict(schema=WIRE, version=1, basis=deepcopy(frozen['basis']),
                  model=dict(request_id=request['reference']['request_id'], body_sha256=request['reference']['body_sha256'],
                             prompt_version=scene.PROMPT_VERSION, engine_epoch=request['profile']['engine_epoch'],
                             artifact_sha256=request['profile']['artifact_sha256']),
                  evidence=deepcopy(frozen['evidence']), observed_at=frozen['basis']['source']['observed_at'],
                  available_at=now, valid_until=frozen['basis']['source']['valid_until'],
                  scene=deepcopy(facts['scene']) if facts is not None else None, subjects=[], decision=dict(DECISION), **FLAGS)
    for subject in frozen['subjects']:
        value = by_slot.get(subject['subject_slot'])
        packet['subjects'].append(dict(instance_id=subject['instance_id'], track_ref=subject['track_ref'],
                                       status='available' if value is not None else 'unknown',
                                       reason=None if value is not None else reason or 'model_output_unavailable', facts=deepcopy(value)))
    return packet


class PersonSceneLedger:
    def __init__(self, store):
        self.store = store

    def record(self, session_id, draft):
        """First terminal receipt wins; exact retry cannot renew availability."""
        with self.store.transaction() as db:
            session = self.store.get_session(session_id)
            if session['state'] != 'recording':
                raise ValueError('active_matching_recording_required')
            packet = deepcopy(draft['packet'])
            basis = packet['basis']['scope']
            if any(basis.get(k) != session[v] for k, v in (('site_id', 'site_id'), ('camera_id', 'camera_id'), ('session_id', 'id'), ('session_generation', 'generation'))):
                raise ValueError('person_receipt_scope_mismatch')
            request_id = packet['model']['request_id']
            prior = db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind=? "
                               "AND json_extract(payload,'$.packet.model.request_id')=? ORDER BY id LIMIT 1",
                               (session_id, EVENT, request_id)).fetchone()
            if prior is not None:
                saved = json.loads(prior['payload'])
                if saved['fingerprint'] != draft['fingerprint']:
                    raise ValueError('person_receipt_identity_conflict')
                return dict(state='duplicate', event_id=prior['id'])
            self.store.check_capacity(len(scene.canonical(draft)) + 65536)
            event_id = self.store._event(db, session_id, EVENT, draft)
            return dict(state='available' if self.store.clock() < packet['valid_until'] else 'expired', event_id=event_id)


class QwenPersonPump(QwenLivePump):
    SCENE = scene
    person_mode = True

    def __init__(self, store, session, *, arm='b', **kwargs):
        if arm not in ('a', 'b') or session.get('site_id') != 'victoria' or session.get('camera_id') != 'den':
            raise ValueError('fixed_person_arm_required')
        self.person_arm = arm
        kwargs.setdefault('ledger', PersonSceneLedger(store))
        kwargs.setdefault('frames', PersonFrames(store, arm=arm))
        kwargs['config'] = {'frames': 2, 'span_seconds': 1., **(kwargs.get('config') or {})}
        super().__init__(store, session, **kwargs)

    def _request(self, frames, now):
        frozen = membership(self.store, self.store.get_session(self.session['id']), frames[-1], now, arm=self.person_arm)
        visible = [{k: deepcopy(s[k]) for k in ('subject_slot', 'box')} for s in frozen['subjects']]
        context = scene.model_context(scene.local_time_label(*self.local_time(now)), visible)
        body = scene.request_body(context, [f['data'] for f in frames], [f['observed_at'] for f in frames])
        body_sha = scene.digest(body)
        request_id = scene.digest(dict(session_id=self.session['id'], body_sha256=body_sha,
                                       membership_digest=scene.digest(frozen), prompt_version=scene.PROMPT_VERSION))
        envelope = dict(request_id=request_id, site_id=self.session['site_id'], session_id=self.session['id'],
                        profile=dict(self.profile), body=body, body_sha256=body_sha, prompt_version=scene.PROMPT_VERSION,
                        task=scene.TASK, deadline=now+self.deadline_seconds)
        return dict(envelope=envelope, reference={k: v for k, v in envelope.items() if k != 'body'},
                    frames=[{k: f[k] for k in ('media_id', 'frame_id', 'capture_epoch', 'observed_at', 'valid_until', 'sha256')} for f in frames],
                    context=context, membership=frozen, profile=dict(self.profile), prior_supplied=False)

    def _terminal(self, request, proof):
        if not isinstance(proof, dict) or proof.get('state') != 'completed':
            return super()._terminal(request, proof)
        content, finish = scene.reply_from_proof(proof)
        bound = (proof.get('request_id') == request['reference']['request_id']
                 and proof.get('body_sha256') == request['reference']['body_sha256'] and proof.get('terminal') is True
                 and proof.get('engine_epoch') == request['profile']['engine_epoch']
                 and proof.get('artifact_sha256') == request['profile']['artifact_sha256'])
        if not bound:
            content = None
        facts, reason = scene.parse_reply(content, finish_reason=finish, slots=[s['subject_slot'] for s in request['membership']['subjects']])
        now = self.clock()
        if not scene.finite(now) or now < request['membership']['basis']['source']['available_at']:
            return self._note('person_receipt_clock_unavailable', 'skipping')
        packet = _packet(request, facts, reason, now)
        draft = dict(packet=packet, source_event_id=request['membership']['source_event_id'],
                     source_payload_digest=request['membership']['source_payload_digest'],
                     membership_digest=scene.digest(request['membership']),
                     input_frames=deepcopy(request['frames']),
                     response_sha256=hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else None,
                     invalid_reason=reason)
        # Exclude delivery time from immutable identity, so a terminal retry
        # compares original content and cannot renew a successful receipt.
        identity = deepcopy(draft)
        identity['packet'].pop('available_at')
        draft['fingerprint'] = scene.digest(identity)
        try:
            result = self.ledger.record(self.session['id'], draft)
        except Exception:
            return self._note('person_record_failed', 'skipping')
        ready=getattr(self,'evidence_ready',None)
        if result['state']=='available' and ready is not None:
            ready.set()
        return self._note('person_' + result['state'], 'current' if result['state'] == 'available' else 'skipping')


def attach_person_semantics(store, session, result, now):
    """Attach only to the exact original already chosen by the existing live view."""
    if session.get('state') != 'recording' or not result.get('arms') or not scene.finite(now):
        return result
    with store.lock:
        rows = store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind=? ORDER BY id DESC LIMIT 128",
                                (session['id'], EVENT)).fetchall()
    if not rows:
        return result
    result = deepcopy(result)
    for shown in result['arms'].values():
        people = shown.get('persons2d') if isinstance(shown, dict) else None
        association = people.get('association') if isinstance(people, dict) else None
        if not isinstance(association, dict) or association.get('status') != 'available':
            continue
        basis = {k: association.get(k) for k in ('scope', 'source', 'track_epoch')}
        for row in rows:
            packet = json.loads(row['payload']).get('packet', {})
            if not _same(packet.get('basis'), basis):
                continue
            if (not scene.finite(packet.get('available_at')) or not scene.finite(packet.get('valid_until'))
                    or not packet['available_at'] <= now < packet['valid_until']
                    or packet['valid_until'] > association['source']['valid_until']):
                break
            people['semantics'] = deepcopy(packet)
            break
    return result
