"""Record existing captions as historical interpretations; never request inference.

Only the stored caption file is read: no refresh request and no model call.
Accepted captions become durable 'caption.receipt' events. The raw caption text
is never persisted: event payloads reach API readers and session exports, so a
receipt keeps only its content digest and the bounded enum below, which is all
the cloud path sees.
"""
import json
import os
from pathlib import Path
import re
from reasoning import canonical, digest, finite
from shared_caption import read_caption

MAX_AGE_SECONDS = 30
READER_FIELDS = ('site_id','camera_id','processing_site_id','model','request_id','frame_id','text',
                 'observed_at','valid_until','generated_at','timestamp_semantics','uncertainty_seconds')
IDENTITY_FIELDS = tuple(k for k in READER_FIELDS if k not in ('text','valid_until'))
REVISION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}')

# Per-sentence rules. A sentence counts only as a complete, generic, unhedged
# statement of one activity; the whole caption abstains on any instruction-like
# text, identity reference, several people, a negation or hedge in any
# sentence, or more than one activity. Unmatched prose is never guessed.
_DET = r'(?:a|an|the|their|his|her)'
_PLACE = rf'(?:{_DET} )?(?:couch|sofa|chair|armchair|bed|desk|table|floor|living room|bedroom|room)'
_FRAME = r'(?:image|photo|picture|scene|frame)'
_PREFIX = rf'(?:(?:the|this) {_FRAME} (?:shows|depicts) |in (?:the|this) {_FRAME},? |there is )?'
_SUBJECT = (rf'(?:(?:a|the|one) (?:(?:young|older|elderly|seated|lone) )?(?:person|man|woman|adult|individual)'
            rf'(?: (?:in|on|at) (?:{_PLACE}|the {_FRAME}))?|someone|they|he|she)')
_CLOTHING = (rf'(?:,? (?:wearing|dressed in) (?:{_DET} )?(?:[a-z-]+ ){{0,3}}'
             r'(?:shirt|t-shirt|sweater|hoodie|jacket|coat|dress|top|pajamas|clothes|clothing|glasses),?)?')
_POSTURE = rf'(?:sitting|seated|lying|reclining|standing|relaxing|sits|lies|reclines|stands|relaxes)(?: (?:on|in|at) {_PLACE})?'
_TAIL = rf'(?: (?:while )?{_POSTURE}| (?:on|in|at) {_PLACE})?[.!]?'
ACTS = {
    'reading': rf'(?:reading|reads) {_DET} (?:(?:open|paperback|hardcover) )?(?:book|magazine|newspaper|novel)',
    'phone_use': (rf'(?:using|uses|looking at|looks at|scroll(?:ing|s) (?:on|through)|text(?:ing|s) on|talk(?:ing|s) on'
                  rf'|typ(?:ing|es) on) {_DET} (?:phone|cell phone|smartphone|mobile phone)'),
    'computer_work': (rf'(?:using|uses|typ(?:ing|es) on|work(?:ing|s) (?:on|at)) {_DET} '
                      r'(?:laptop|computer|keyboard|laptop computer|desktop computer)'),
}
PATTERNS = {label: rf'{_PREFIX}{_SUBJECT}{_CLOTHING}(?: {_POSTURE},?)?(?: (?:is|are))?(?: {_POSTURE}(?:,|, and| and| while)?)? {act}{_TAIL}'
            for label, act in ACTS.items()}
CUES = {
    'instruction_like_text': (r'\b(?:ignore|disregard|instructions?|system|assistant|prompt|override|jailbreak|polic(?:y|ies)'
                              r'|respond|reply|answer|output|classify|label|jev|you|your|must|should|command|execute|tools?)\b'
                              r'|["\u201c\u201d<>{}]|https?:|://'),
    'identity_reference': r'\b(?:named|name|names|identity|identified|identifies|identify|recogni[sz]e[ds]?|known as)\b',
    'multiple_people': (r'\b(?:people|persons|men|women|children|kids|crowd|group|couple|others|everyone'
                        r'|child|kid|baby|toddler|boys?|girls?|teen(?:ager)?s?|man\b[\s\S]*\bwoman|woman\b[\s\S]*\bman'
                        r'|(?:two|three|four|five|several|multiple|many|other|another|second|both)\s+(?:[a-z-]+\s+){0,2}?'
                        r'(?:person|people|man|men|woman|women|adults?|individuals?|child(?:ren)?|kids?|figures?)'
                        r'|wife|husband|spouse|partner|(?:boy|girl)?friends?|sons?|daughters?|mother|father|mom|mum|dad|parents?'
                        r'|brothers?|sisters?|siblings?|grand(?:mother|father|parent|child|children|son|daughter)s?|family(?!\s+room)'
                        r'|guests?|visitors?|roommates?|flatmates?|neighbou?rs?|(?:some|any)(?:one|body)\s+else|another\s+one)\b'),
}
# An explicit statement that nobody else is present is not a second person.
ALONE = r'\b(?:no other (?:people|persons?|individuals?)|no one else|nobody else)\b'
# Caption-wide checks: a second referent (female and male terms together, or
# more than one introduced person), and any activity outside the three families.
REFERENTS = (r'\b(?:woman|women|lady|ladies|girls?|she|her|hers|herself)\b', r'\b(?:man|men|guys?|gentleman|boys?|he|him|his|himself)\b')
INTRODUCTION = r'\b(?:(?:a|an|one)\s+(?:[a-z-]+\s+){0,2}?(?:person|man|woman|adult|individual|figure)|some(?:one|body))\b'
OTHER_ACTIVITY = (r'\b(?:watch(?:es|ing)|sleep(?:s|ing)?|asleep|nap(?:s|ping)|doz(?:es|ing)|eat(?:s|ing)|drink(?:s|ing)'
                  r'|cook(?:s|ing)|clean(?:s|ing)|walk(?:s|ing)|run(?:s|ning)|exercis(?:es|ing)|knit(?:s|ting)|sew(?:s|ing)'
                  r'|writ(?:es|ing)|play(?:s|ing)|gaming|listen(?:s|ing)|danc(?:es|ing)|sing(?:s|ing)|stretch(?:es|ing)'
                  r'|meditat(?:es|ing)|fold(?:s|ing)|chat(?:s|ting)|talk(?:s|ing)\s+(?:to|with))\b')
ACTIVITY_VERB = (r'\b(?:reads?|reading|uses?|using|typ(?:e|es|ing)|texts?|texting|scroll(?:s|ing)?|work(?:s|ing)?'
                 r'|looks?\s+at|looking\s+at|brows(?:e|es|ing)|watch(?:es|ing)?|play(?:s|ing)?|talk(?:s|ing)?|writ(?:e|es|ing))\b')
FAMILIES = {'reading': r'\b(?:read|reads|reading|books?|magazines?|newspapers?|novels?|paperback|kindle|e-reader|documents?|pages?)\b',
            'phone_use': r'\b(?:phones?|smartphones?|cellphones?|texting)\b',
            'computer_work': r'\b(?:laptops?|computers?|keyboards?|desktop|monitors?)\b'}
QUALIFIERS = (r"\b(?:not|no|never|neither|nor|without|none|nobody|cannot|may|might|could|possibly|perhaps|likely|unlikely"
              r"|probably|seems?|seemingly|appears?|apparently|unclear|uncertain|unsure|suggests?|presumably|maybe|either|or"
              r"|whether|if|pretend(?:s|ing)?|looks?\s+like|however|although|though|but|actually|instead|rather|otherwise"
              r"|alternatively|hard\s+to|difficult|unable|blurr(?:y|ed)|obscured|ambiguous|certain(?:ly|ty)?|sure|guess"
              r"|assum(?:e|es|ed|ing))\b|n't\b")
RULES = {'patterns': PATTERNS, 'cues': CUES, 'alone': ALONE, 'referents': REFERENTS, 'introduction': INTRODUCTION,
         'other_activity': OTHER_ACTIVITY, 'activity_verb': ACTIVITY_VERB, 'families': FAMILIES,
         'qualifiers': QUALIFIERS, 'max_sentences': 32, 'max_age_seconds': MAX_AGE_SECONDS}
# The rules digest is part of the version: any rule change is a new projection.
VERSION = 'caption-projection-2:' + digest(RULES)[:12]


def _rule(text):
    lines = text.splitlines() if isinstance(text, str) else []
    sentences = [' '.join(part.split()) for line in lines for part in re.split(r'(?<=[.!?])\s+', line.strip()) if part.strip()]
    if not sentences or len(text) > 4000 or len(sentences) > RULES['max_sentences']:
        return 'unsupported_length', None
    for rule, pattern in CUES.items():
        subject = re.sub(ALONE, ' ', text, flags=re.IGNORECASE) if rule == 'multiple_people' else text
        if re.search(pattern, subject, flags=re.IGNORECASE):
            return rule, None
    # Every check below is caption-wide: a second person, hedge, negation or
    # other activity in any sentence withholds the whole caption.
    rest = re.sub(ALONE, ' ', text, flags=re.IGNORECASE)
    if (all(re.search(p, rest, flags=re.IGNORECASE) for p in REFERENTS)
            or len(re.findall(INTRODUCTION, rest, flags=re.IGNORECASE)) > 1):
        return 'multiple_people', None
    if re.search(QUALIFIERS, rest, flags=re.IGNORECASE):
        return 'negated_or_hedged', None
    if re.search(OTHER_ACTIVITY, rest, flags=re.IGNORECASE):
        return 'conflicting_activities', None
    matched, mentioned = set(), set()
    for sentence in sentences:
        families = {f for f, p in FAMILIES.items() if re.search(p, sentence, flags=re.IGNORECASE)}
        if families and re.search(ACTIVITY_VERB, sentence, flags=re.IGNORECASE):
            mentioned |= families
        matched |= {label for label, p in PATTERNS.items() if re.fullmatch(p, sentence, flags=re.IGNORECASE)}
    if len(matched | mentioned) > 1:
        return 'conflicting_activities', None
    return ('single_clear_statement', next(iter(matched))) if matched else ('no_clear_statement', None)


def semantic_projection(text):
    rule, activity = _rule(text)
    return {'version': VERSION, 'interpretation': 'historical_model_interpretation',
            'activity': activity or 'unavailable', 'confidence': 'not_supplied', 'rule': rule}


def refresh_publisher_binding(started_at, *, environ=None, read=None):
    """Managed refresh composition only (refresh_app.create_refresh_app).

    The Lab reader must be the refresh publisher's own file. Its pinned model
    revision is joined read-only, and only when the protected configuration has
    not changed since this process started (the publisher loaded it then).
    """
    environ = os.environ if environ is None else environ
    if read is None:
        from home_shared.service_config import protected_file
        def read(path):
            raw = protected_file(path, enforce_linux=True).read_bytes()
            if len(raw) > 16384:
                raise ValueError('refresh configuration too large')
            return json.loads(raw), Path(path).stat().st_mtime
    config, modified = read(environ['PERCEPTION_LAB_REFRESH_CONFIG'])
    state = Path(config['state_directory'])
    path = environ.get('PERCEPTION_LAB_SHARED_CAPTION_FILE', '')
    if not state.is_absolute() or '..' in state.parts or not path or Path(path) != state/'shared-caption.json':
        raise ValueError('Caption history requires the refresh publisher path')
    revision = config.get('model_revision')
    if not isinstance(revision, str) or not REVISION.fullmatch(revision) or not finite(modified) or not finite(started_at):
        raise ValueError('Pinned refresh model revision required')
    return path, (revision if modified <= started_at else None)


class CaptionHistory:
    def __init__(self, store, path, camera_id, *, reader=read_caption, model_revision=None, revision_since=None):
        if camera_id not in {c.get('id') for c in store.settings.cameras}:
            raise ValueError('Registered caption camera required')
        if model_revision is not None and (not isinstance(model_revision, str) or not REVISION.fullmatch(model_revision)
                                           or not finite(revision_since)):
            raise ValueError('Pinned model revision and binding time required')
        self.store, self.path, self.camera_id, self.reader = store, path, camera_id, reader
        self.model_revision, self.revision_since = model_revision, revision_since

    def read(self):
        """Bounded local file read only; never contacts a model or refresh route."""
        return self.reader(self.path, self.store.settings.site_id, self.camera_id, self.store.clock())

    def poll(self, sid):
        return self.ingest(sid, self.read())

    def ingest(self, sid, value):
        status = value.get('status') if isinstance(value, dict) else None
        if status not in ('current', 'stale'):
            return {'state': status if status == 'missing' else 'invalid'}
        if (value.get('site_id') != self.store.settings.site_id or value.get('camera_id') != self.camera_id
                or not isinstance(value.get('text'), str) or not 0 < len(value['text']) <= 4000
                or any(not isinstance(value.get(k), str) or not value[k] for k in ('request_id', 'frame_id'))
                or not all(finite(value.get(k)) for k in ('observed_at', 'generated_at', 'valid_until'))):
            return {'state': 'invalid'}
        now = self.store.clock()
        key = digest([value['site_id'], value['camera_id'], value['request_id'], value['frame_id']])
        fingerprint = digest({k: value[k] for k in READER_FIELDS if k in value})
        expiry = min(value['valid_until'], value['observed_at']+MAX_AGE_SECONDS)
        base = {k: value[k] for k in IDENTITY_FIELDS if k in value}
        base.setdefault('uncertainty_seconds', 0)
        base.update(available_at=now, first_received_at=now, valid_until=expiry, original_valid_until=value['valid_until'],
                    max_age_seconds=MAX_AGE_SECONDS, caption_key=key, caption_digest=fingerprint)
        with self.store.transaction() as db:
            session = self.store.get_session(sid)
            if (session['state'] != 'recording' or session['camera_id'] != self.camera_id
                    or session['site_id'] != self.store.settings.site_id):
                raise ValueError('Active matching recording required')
            base['session_id'] = sid
            prior = db.execute("""SELECT id,kind,payload FROM events WHERE session_id=? AND kind IN ('caption.receipt','caption.rejected')
                AND json_extract(payload,'$.caption_key')=? ORDER BY id LIMIT 1""", (sid, key)).fetchone()
            if prior:
                saved = json.loads(prior['payload'])
                if saved['caption_digest'] != fingerprint:
                    # Same request/frame with changed content: durable, content-free
                    # audit. The receipt is excluded from later selection.
                    seen = db.execute("""SELECT id FROM events WHERE session_id=? AND kind='caption.conflict'
                        AND json_extract(payload,'$.caption_key')=? AND json_extract(payload,'$.caption_digest')=?""",
                        (sid, key, fingerprint)).fetchone()
                    if seen:
                        return {'state': 'identity_conflict', 'event_id': seen['id']}
                    conflict = {'site_id': value['site_id'], 'camera_id': self.camera_id, 'session_id': sid,
                                'caption_key': key, 'caption_digest': fingerprint, 'prior_event_id': prior['id'],
                                'prior_caption_digest': saved['caption_digest'], 'available_at': now, 'reason': 'identity_conflict'}
                    self.store.check_capacity(len(canonical(conflict))+65536)
                    return {'state': 'identity_conflict', 'event_id': self.store._event(db, sid, 'caption.conflict', conflict)}
                if prior['kind'] == 'caption.rejected':
                    return {'state': saved['reason'], 'event_id': prior['id']}
                return {'state': 'available' if now < saved['valid_until'] else 'expired', 'event_id': prior['id']}
            latest = db.execute("""SELECT max(json_extract(payload,'$.observed_at')) FROM events WHERE session_id=?
                AND kind='caption.receipt' AND json_extract(payload,'$.camera_id')=?""", (sid, self.camera_id)).fetchone()[0]
            reason = ('out_of_order' if latest is not None and value['observed_at'] < latest
                      else 'expired_on_receipt' if expiry <= now else None)
            if reason:
                # Explicit, text-free rejection; never selectable as evidence.
                rejected = {**base, 'reason': reason}
                self.store.check_capacity(len(canonical(rejected))+65536)
                return {'state': reason, 'event_id': self.store._event(db, sid, 'caption.rejected', rejected)}
            joined = self.model_revision is not None and value['generated_at'] >= self.revision_since
            payload = {**base, 'projection': semantic_projection(value['text']),
                       'text_sha256': digest(value['text']),
                       'text_scope': 'digest_only_text_not_retained', 'origin': 'same_camera_visual',
                       'evidence_roots': ['caption:'+key], 'cloud_projection': 'enum_activity_only',
                       'model_revision': self.model_revision if joined else 'not_supplied',
                       'model_revision_source': 'refresh_publisher_config' if joined else 'not_supplied'}
            self.store.check_capacity(len(canonical(payload))+65536)
            return {'state': 'available', 'event_id': self.store._event(db, sid, 'caption.receipt', payload)}


class UnavailableCaption(CaptionHistory):
    """This session's caption binding failed validation. Nothing is read; one
    content-free reason is recorded and the pump reports unavailable."""
    def __init__(self, store, camera_id, reason='caption_source_invalid'):
        self.store, self.path, self.camera_id, self.reason = store, None, camera_id, reason
        self.reader, self.model_revision, self.revision_since = None, None, None

    def read(self):
        return {'status': 'unavailable'}

    def ingest(self, sid, value):
        with self.store.transaction() as db:
            session = self.store.get_session(sid)
            if (session['state'] == 'recording' and session['camera_id'] == self.camera_id
                    and not db.execute("SELECT 1 FROM events WHERE session_id=? AND kind='caption.unavailable'", (sid,)).fetchone()):
                self.store.check_capacity(65536)
                self.store._event(db, sid, 'caption.unavailable', {'reason': self.reason, 'available_at': self.store.clock()})
        raise ValueError(self.reason)


def causal_caption(receipt, cutoff):
    """Pure replay selection; duplicate reads cannot move availability or expiry."""
    return (receipt['observed_at'] <= cutoff and receipt['first_received_at'] <= cutoff
            and receipt['generated_at'] <= cutoff and cutoff < receipt['valid_until'])


def latest_caption(store, session, cutoff):
    """The latest caption known at cutoff, as a projection-only record, or None.

    One query, identical for live reasoning, replay and saved analysis. A newer
    known caption supersedes older ones even once expired (no fallback to older
    context), and a receipt whose identity conflicted by cutoff is excluded.
    """
    if not finite(cutoff):
        raise ValueError('finite caption cutoff required')
    from persisted_evidence import observation_record
    with store.lock:
        row = store.db.execute("""SELECT e.id,e.payload,EXISTS(SELECT 1 FROM events c WHERE c.session_id=e.session_id
              AND c.kind='caption.conflict' AND json_extract(c.payload,'$.caption_key')=json_extract(e.payload,'$.caption_key')
              AND json_extract(c.payload,'$.available_at')<=?) AS conflicted
            FROM events e WHERE e.session_id=? AND e.kind='caption.receipt'
              AND json_extract(e.payload,'$.site_id')=? AND json_extract(e.payload,'$.camera_id')=?
              AND json_extract(e.payload,'$.first_received_at')<=? AND json_extract(e.payload,'$.generated_at')<=?
              AND json_extract(e.payload,'$.observed_at')<=?
            ORDER BY json_extract(e.payload,'$.observed_at') DESC,e.id DESC LIMIT 1""",
            (cutoff, session['id'], session['site_id'], session['camera_id'], cutoff, cutoff, cutoff)).fetchone()
    if row is None or row['conflicted']:
        return None
    value = json.loads(row['payload'])
    if value.get('session_id') != session['id'] or not causal_caption(value, cutoff):
        return None
    return observation_record(row['id'], 'caption.receipt', value, session)


def caption_context(store, session, cutoff, *, generation, policy):
    """Identical shared context for every arm the recorded policy grants.

    Independent of per-arm history windows and retained history; strictly
    within the caption's own validity at cutoff.
    """
    from evidence_policy import caption_policy
    policy = caption_policy(policy)
    caption = latest_caption(store, session, cutoff) if any(policy.values()) else None
    return {arm: [{**caption, 'arm': arm, 'generation': generation}] for arm in ('a', 'b') if caption and policy[arm]}
