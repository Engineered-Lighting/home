"""Producer-owned sole-reader correlation; never identity or geometry qualification.

The bank consumes raw detector results before presentation. Only unambiguous,
continuous single-person evidence may bind a global JEV answer to a later pose.
Camera/development alignment policy is independent and remains unchanged.
"""
import json
import uuid

from apartment_desk_preview import distinct_people
from reasoning import digest, finite
from view_person_association import _iou

SCHEMA = 'den-sole-reader-source-1'
KEYS = ('session_id', 'generation', 'capture_epoch', 'engine_epoch', 'reader_epoch')
MAX_GAP_S = 2.


def reader_key(binding):
    return tuple(binding.get(k) for k in KEYS) if isinstance(binding, dict) else None


class SoleReaderBank:
    """One raw paired runner, with preview/commit matching immutable output storage."""
    def __init__(self):
        self.state = None

    def preview(self, output, session):
        scope = {k: output.get(k) for k in ('generation', 'capture_epoch', 'engine_epoch')}
        scope.update(session_id=session['id'])
        observed, available, until = (output.get(k) for k in ('observed_at', 'available_at', 'valid_until'))
        reason, box, instance = None, None, None
        try:
            people = distinct_people(output['result'])
            if len(people) != 1:
                reason = 'sole_person_required'
            else:
                instance = people[0].get('instance_id')
                boxes = [b for b in output['result'].get('boxes', []) if isinstance(b, dict)
                         and b.get('label') == 'person' and b.get('instance_id') == instance]
                if not isinstance(instance, str) or not instance or len(boxes) != 1:
                    reason = 'sole_person_box_missing'
                else:
                    x, y, w, h = (boxes[0][k] for k in ('x', 'y', 'w', 'h'))
                    if not all(finite(v) for v in (x, y, w, h)) or min(w, h) <= 0:
                        reason = 'sole_person_box_invalid'
                    else:
                        box = (x, y, x + w, y + h)
        except (KeyError, TypeError, AttributeError):
            reason = 'sole_person_source_invalid'
        if (output.get('status') != 'completed' or output.get('current') is not True
                or not all(finite(v) for v in (observed, available, until))
                or not observed <= available < until
                or type(scope['generation']) is not int or scope['generation'] < 1
                or any(not isinstance(scope[k], str) or not scope[k] for k in ('capture_epoch', 'engine_epoch'))):
            reason = 'sole_person_source_not_current'
        if reason:
            return dict(schema=SCHEMA, status='unavailable', reason=reason), None
        old = self.state
        if old is not None and old['scope'] == scope and observed <= old['observed_at']:
            return dict(schema=SCHEMA, status='unavailable', reason='sole_person_source_out_of_order'), None
        continued = (old is not None and old['scope'] == scope
                     and 0 < observed - old['observed_at'] <= MAX_GAP_S
                     and _iou(old['box'], box) >= .3)
        epoch = old['epoch'] if continued else uuid.uuid4().hex
        since = old['since'] if continued else observed
        state = dict(scope=scope, observed_at=observed, box=box, epoch=epoch, since=since)
        binding = dict(schema=SCHEMA, status='available', reason=None, **scope,
                       reader_epoch=epoch, interval_start=since, instance_id=instance,
                       source_frame_id=output['frame_id'], source_request_id=output['request_id'],
                       observed_at=observed, available_at=available, valid_until=until)
        return binding, state

    def commit(self, preview):
        self.state = preview[1]


def freeze_context(store, session, records, shared, cutoff, *, qwen_source=None, validity=None):
    """Freeze raw originals; no presentation packet can supply a binding.

    Legacy/unbound inputs still support Lab inspection but cannot earn reading
    authority. History and Qwen summaries are trimmed to this sole-reader epoch.
    """
    def refuse(reason):
        if validity is not None:validity.update(status='unavailable',reason=reason)
        return records,shared,None
    qwen_required = any(r.get('source_kind') == 'qwen.observation' for r in shared)
    visuals = [r for r in records if r.get('source_kind') == 'perception.output']
    bound = []
    with store.lock:
        for record in visuals:
            row = store.db.execute("SELECT payload FROM events WHERE session_id=? AND id=? AND kind='perception.output'",
                                   (session['id'], record.get('id'))).fetchone()
            raw = json.loads(row[0]) if row else {}
            binding = raw.get('reading_binding')
            if (isinstance(binding, dict) and binding.get('status') == 'available'
                    and raw.get('frame_id') == record.get('frame_id')
                    and raw.get('capture_epoch') == record.get('capture_epoch')):
                bound.append((record, dict(binding, source_event_id=int(record['id']))))
    if not bound:
        return refuse('reader_binding_unavailable')
    latest_record, latest = max(bound, key=lambda row: (row[0]['observed_at'], int(row[0]['id'])))
    newest = max(visuals, key=lambda r: (r['observed_at'], int(r['id'])))
    if newest['id'] != latest_record['id']:
        return refuse('latest_reader_binding_unavailable')  # never fall back
    if not cutoff < latest['valid_until']:
        return refuse('original_detector_evidence_expired')
    key = reader_key(latest)
    allowed = {r['id'] for r, b in bound if reader_key(b) == key}
    # Room-derived words need a source binding too; omit them for this bounded v2.
    records = [r for r in records if r.get('source_kind') == 'ha.observation' or r['id'] in allowed]
    shared = [r for r in shared if r.get('source_kind') not in ('qwen.observation', 'room.facts', 'caption.receipt')]
    # The runtime's recipe/household gate decides whether Qwen may be carried.
    # Original-cache access must not undo withholding or qualify the configured
    # always-on path while its semantic dependency is missing.
    if qwen_source is not None and not qwen_required:
        return refuse('qwen_context_withheld')
    from qwen_live import QwenSceneLedger
    import qwen_scene as scene
    receipts, conflicts = QwenSceneLedger(store).receipts(session, cutoff) if qwen_required else ([], [])
    if qwen_source is not None and qwen_required:
        try:
            originals = qwen_source.reading_receipts(session, latest, cutoff)
            if not originals:
                return refuse('compatible_qwen_original_unavailable')
            receipts += originals
        except Exception:
            return refuse('qwen_original_source_unavailable')
    known = scene.known_receipts(receipts, cutoff, conflicts=conflicts)
    if known and (known[-1].get('capture_epoch') != latest['capture_epoch']
            or not finite(known[-1].get('window_start')) or known[-1]['window_start'] < latest['interval_start']
            or known[-1]['observed_at'] > latest['observed_at']):
        return refuse('qwen_reader_interval_incompatible')
    receipts = [r for r in receipts if r.get('capture_epoch') == latest['capture_epoch']
                and finite(r.get('window_start')) and r['window_start'] >= latest['interval_start']
                and finite(r.get('observed_at')) and r['observed_at'] <= latest['observed_at']]
    known = scene.known_receipts(receipts, cutoff, conflicts=conflicts)
    expiry = latest['valid_until']
    if known:
        last = known[-1]
        if (last.get('status') != 'valid' or not cutoff < last['valid_until']
                or (last.get('facts') or {}).get('people_visible') != 'one'):
            return refuse('latest_qwen_original_invalid_expired_or_ambiguous')
        projected = scene.projection(receipts, cutoff, conflicts=conflicts)
        qwen = scene.scene_record(projected, receipts, cutoff, site_id=session['site_id'], camera_id=session['camera_id'],
                                 session_id=session['id'], conflicts=conflicts)
        qwen.update(arm=latest_record['arm'], generation=latest_record['generation'])
        shared.append(qwen)
        expiry = min(expiry, last['valid_until'])
    # If Qwen was supplied upstream but cannot be bound, do not silently drop
    # that semantic dependency and claim an equivalent answer.
    elif qwen_required:
        return refuse('compatible_qwen_original_unavailable')
    context = dict(binding=latest, valid_until=expiry, input_digest=digest([records, shared]))
    if validity is not None:validity.update(status='available',reason=None,valid_until=expiry,
        source_event_id=latest['source_event_id'],remaining_s=expiry-cutoff)
    return records, shared, context
