"""Server-provisioned HA snapshot ingestion; no bearer, network or action access."""
import re
from datetime import datetime
from reasoning import canonical, digest, finite
from store import identifier

# Derived facts are computed locally from allowlisted raw states; the rule
# version travels with the fact. Ids use the reserved 'derived.' domain.
DERIVED_RULES = {'computer_active': 'computer_active_v1', 'computer_input_recency': 'computer_active_v2'}
# v2/v3 bucket input recency at fixed bounds, so they take no idle bound.
DERIVED_FIELDS = {'computer_active': {'session_entity', 'unlocked_values', 'last_active_entity', 'max_idle_seconds'},
                  'computer_input_recency': {'session_entity', 'unlocked_values', 'last_active_entity'}}
# Optional per-fact 'rule' key: computer_input_recency may ask for the finer
# v3 buckets. Absent, it stays v2 so existing source files keep their meaning.
RULE_CHOICES = {'computer_input_recency': ('computer_active_v2', 'computer_active_v3')}
RECENCY = ((60, 'under_60s'), (300, '1_to_5_min'))
# v3 splits the first minute. HASS.Agent publishes lastactive on its sensor
# update interval, so the recorded idle time can only be longer than the true
# one: under_15s is conservative (missed, never invented) while that interval
# is at most ~15 s, and undercounts with a longer interval.
RECENCY_V3 = ((15, 'under_15s'), (60, '15_to_60s'), (300, '1_to_5_min'))
CLOCK_SKEW_SECONDS = 5
UNUSABLE = {None, 'unknown', 'unavailable'}
INSTANT = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})')


def derived_rules(spec, entities):
    """Validate an HA source file's optional 'derived' key against its allowlist.

    Returns {derived_id: rule}. Inputs must be read entities; nothing else is read.
    """
    entities = tuple(entities)
    if any(isinstance(e, str) and e.split('.', 1)[0] == 'derived' for e in entities):
        raise ValueError('the derived HA domain is reserved')
    if spec is None:
        return {}
    if not isinstance(spec, dict) or not spec or set(spec) - set(DERIVED_RULES):
        raise ValueError('registered derived HA facts required')
    rules = {}
    for name, rule in spec.items():
        values = rule.get('unlocked_values') if isinstance(rule, dict) else None
        idle = rule.get('max_idle_seconds', 60) if isinstance(rule, dict) else None
        choices = RULE_CHOICES.get(name, ())
        chosen = rule.get('rule', DERIVED_RULES[name]) if isinstance(rule, dict) else None
        if (not isinstance(rule, dict) or set(rule) - ({'rule'} if choices else set()) != DERIVED_FIELDS[name]
                or (choices and chosen not in choices)
                or not isinstance(rule['session_entity'], str) or rule['session_entity'] not in entities
                or not isinstance(rule['last_active_entity'], str) or rule['last_active_entity'] not in entities
                or rule['session_entity'] == rule['last_active_entity']
                or not isinstance(values, list) or not 1 <= len(values) <= 4
                or any(not isinstance(v, str) or not 1 <= len(v) <= 32 or v in UNUSABLE for v in values)
                or len(set(values)) != len(values)
                or type(idle) not in (int, float) or not finite(idle) or not 30 <= idle <= 3600):
            raise ValueError('bounded computer_active derivation required')
        rules['derived.' + name] = {'rule': chosen, 'session_entity': rule['session_entity'],
            'last_active_entity': rule['last_active_entity'], 'unlocked_values': tuple(values)}
        if 'max_idle_seconds' in rule:
            rules['derived.' + name]['max_idle_seconds'] = idle
    return rules


def instant(value):
    """Epoch seconds for an offset-qualified ISO-8601 state, else None."""
    if not isinstance(value, str) or not INSTANT.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None


def computer_active(rule, facts, observed_at):
    """'on' only for an unlocked session with input within the idle bound at
    snapshot acquisition. Any unusable input or a future last-active beyond
    the clock-skew allowance is unknown, never guessed."""
    session = facts[rule['session_entity']]['state']
    last = instant(facts[rule['last_active_entity']]['state'])
    idle = None if last is None else observed_at - last
    if session in UNUSABLE or idle is None or idle < -CLOCK_SKEW_SECONDS:
        state = None
    elif session in rule['unlocked_values'] and idle <= rule['max_idle_seconds']:
        state = 'on'
    else:
        state = 'off'
    return {'state': state, 'status': 'unknown' if state is None else 'known',
            'provenance': {'inputs': [rule['session_entity'], rule['last_active_entity']],
                           'rule': rule['rule'], 'max_idle_seconds': rule['max_idle_seconds']}}


def computer_input_recency(rule, facts, observed_at, buckets=RECENCY):
    """Input-recency bucket at snapshot acquisition (computer_active_v2): the
    v1 inputs and clock-skew rule, bucketed at 60 s and 5 min. A known
    non-unlocked session is 'locked'; any unusable input, or a last-active
    beyond the skew allowance, is unknown exactly as in v1."""
    session = facts[rule['session_entity']]['state']
    last = instant(facts[rule['last_active_entity']]['state'])
    idle = None if last is None else observed_at - last
    # 'Unknown' in any case is never read as a lock.
    if session in UNUSABLE or session.lower() in UNUSABLE or idle is None or idle < -CLOCK_SKEW_SECONDS:
        state = None
    elif session not in rule['unlocked_values']:
        state = 'locked'
    else:
        state = next((label for bound, label in buckets if idle <= bound), 'over_5_min')
    return {'state': state, 'status': 'unknown' if state is None else 'known',
            'provenance': {'inputs': [rule['session_entity'], rule['last_active_entity']], 'rule': rule['rule']}}


def computer_input_recency_v3(rule, facts, observed_at):
    """computer_active_v3: the v2 inputs, lock and clock-skew rules with the
    first minute split: under_15s, 15_to_60s, 1_to_5_min, over_5_min, locked
    (unknown stays status unknown). Bounds are inclusive, as in v2."""
    return computer_input_recency(rule, facts, observed_at, RECENCY_V3)


DERIVE = {'computer_active_v1': computer_active, 'computer_active_v2': computer_input_recency,
          'computer_active_v3': computer_input_recency_v3}


class HAIngestor:
    def __init__(self, store, *, site_id, camera_id, source_id, entities, ttl_seconds=3, camera_derived_entities=(), derived=None):
        if site_id != store.settings.site_id or camera_id not in {c['id'] for c in store.settings.cameras}:
            raise ValueError('registered local source required')
        if (not isinstance(entities, (list, tuple)) or not 1 <= len(entities) <= 64
                or len(set(entities)) != len(entities)
                or any(not isinstance(e, str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]{1,100}', e) for e in entities)):
            raise ValueError('bounded exact entity allowlist required')
        if not finite(ttl_seconds) or not 0 < ttl_seconds <= 10:
            raise ValueError('bounded freshness required')
        self.store, self.site_id, self.camera_id = store, site_id, camera_id
        self.source_id, self.entities, self.ttl = identifier(source_id), frozenset(entities), ttl_seconds
        if not isinstance(camera_derived_entities, (list, tuple)) or set(camera_derived_entities) - self.entities:
            raise ValueError('camera-derived entities must belong to the entity allowlist')
        self.camera_derived_entities = frozenset(camera_derived_entities)
        # Derived facts are independent of the camera: never 'same_camera_visual'.
        self.derived = derived_rules(derived, sorted(self.entities))

    def ingest(self, sid, *, snapshot_id, observed_at, states, acquisition_duration_s=0):
        """Accept a fresh reconciled snapshot from a trusted local adapter.

        observed_at is validated snapshot acquisition time, NOT HA last_changed.
        Missing, unknown and unavailable entities remain explicit unknown values.
        Never accept caller attributes, friendly names, identities or service calls.
        """
        snapshot_id = identifier(snapshot_id)
        now = self.store.clock()
        if not finite(observed_at) or not now - self.ttl <= observed_at <= now:
            raise ValueError('fresh snapshot acquisition time required')
        if not finite(acquisition_duration_s) or not 0 <= acquisition_duration_s <= 2 or observed_at + acquisition_duration_s > now:
            raise ValueError('bounded completed acquisition interval required')
        if not isinstance(states, dict) or set(states) - self.entities:
            raise ValueError('snapshot outside entity allowlist')
        if any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in states.values()):
            raise ValueError('bounded scalar HA states required')
        facts = {e: {'state': states.get(e) if states.get(e) not in {None, 'unknown', 'unavailable'} else None,
                     'status': 'known' if states.get(e) not in {None, 'unknown', 'unavailable'} else 'unknown'}
                 for e in sorted(self.entities)}
        for entity in self.camera_derived_entities:
            facts[entity]['origin'] = 'same_camera_visual'
        for derived_id, rule in sorted(self.derived.items()):
            facts[derived_id] = DERIVE[rule['rule']](rule, facts, observed_at)
        key = f'ha:{self.source_id}:{snapshot_id}'
        content = {'observed_at': observed_at, 'ha': facts, 'acquisition_duration_s':acquisition_duration_s}
        fingerprint = digest(content)
        value = {**content, 'site_id': self.site_id, 'camera_id': self.camera_id,
                 'session_id': sid, 'available_at': now, 'valid_until': observed_at + self.ttl,
                 'evidence_roots': [key], 'snapshot_key': key, 'snapshot_digest': fingerprint,
                 'quality': {'timing_basis': 'reconciled_snapshot_acquisition', 'source_id': self.source_id,
                             'atomic_snapshot':False,'acquisition_duration_s':acquisition_duration_s}}
        with self.store.transaction() as db:
            session = self.store.get_session(sid)
            if session['state'] != 'recording' or session['camera_id'] != self.camera_id:
                raise ValueError('matching active recording required')
            prior = db.execute("SELECT id,payload FROM events WHERE session_id=? AND kind='ha.observation' AND json_extract(payload,'$.snapshot_key')=?", (sid,key)).fetchone()
            if prior:
                import json
                if json.loads(prior['payload'])['snapshot_digest'] != fingerprint:
                    raise ValueError('snapshot identity reused with changed content')
                return prior['id']
            latest = db.execute("SELECT max(json_extract(payload,'$.observed_at')) FROM events WHERE session_id=? AND kind='ha.observation' AND json_extract(payload,'$.quality.source_id')=?", (sid,self.source_id)).fetchone()[0]
            if latest is not None and observed_at < latest:
                raise ValueError('out-of-order HA snapshot')
            self.store.check_capacity(len(canonical(value)) + 65536)
            return self.store._event(db, sid, 'ha.observation', value)
