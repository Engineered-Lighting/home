"""Shared, code-computed context for both JEV arms; pure, no network, no identity.

Ambient facts (clock, daylight, weather) are priors about the setting, never
observations of activity: they are not records, never refresh visual
freshness and never make a packet current or dispatchable. The shared context
block is built once per scheduler tick from the same rows for both arms; its
digest covers every family except the experiment's lever.
"""
from datetime import datetime, tzinfo
from functools import lru_cache
import re

from reasoning import digest, finite

SCHEMA = 'ambient-context-1'
# 2: per-fact HA coverage (ha_facts), not_granted and timezone_unavailable.
CONTEXT_SCHEMA = 'shared-context-2'
# [start, end) local hours; anything unresolvable is 'unknown', never guessed.
PARTS = ((0, 5, 'night'), (5, 8, 'early_morning'), (8, 12, 'morning'), (12, 17, 'afternoon'),
         (17, 21, 'evening'), (21, 24, 'late_evening'))
DAYLIGHT = {'above_horizon': 'day', 'below_horizon': 'dark'}
# Home Assistant weather condition states; anything else is 'unknown'.
WEATHER = ('clear-night', 'cloudy', 'fog', 'hail', 'lightning', 'lightning-rainy', 'partlycloudy', 'pouring',
           'rainy', 'snowy', 'snowy-rainy', 'sunny', 'windy', 'windy-variant', 'exceptional')
ORIGINS = {'clock': ['day_type', 'local_hour', 'part_of_day'], 'independent_device': ['daylight', 'weather']}
INTERPRETATION = 'context_prior_not_activity_observation'
# HA facts that feed this context only; they never join per-arm device state.
HA_FACTS = {'daylight': 'daylight', 'weather_condition': 'weather'}
# Shared families, in packet order. Per-arm localization/room maps are pipeline
# settings; 'spatial' is the future validated shared spatial family.
FAMILIES = ('ha', 'ambient', 'caption', 'qwen_scene', 'temporal_encoder', 'spatial')
# not_granted: provisioned, but the household policy does not grant its egress
# schema this tick. timezone_unavailable: ambient is granted but the site zone
# cannot load, so the clock facts are unknown (daylight/weather still apply).
COVERAGE = ('supplied', 'stale', 'withheld_by_experiment', 'not_in_baseline', 'not_provisioned', 'invalid',
            'not_granted', 'timezone_unavailable')
# Per bound HA device fact: carried, bound but absent this tick, or why not.
FACT_COVERAGE = ('supplied', 'stale', 'not_granted', 'withheld_by_experiment', 'not_in_baseline', 'not_provisioned')
# Projection statuses the continuous Qwen scene record may report.
QWEN_STATUSES = ('supplied', 'stale', 'invalid')
# Projection statuses the V-JEPA scene record (temporal_encoder) may report.
VJEPA_STATUSES = ('supplied', 'stale', 'invalid')
ZONE = re.compile(r'[A-Za-z][A-Za-z0-9_+-]{0,31}(/[A-Za-z0-9_+-]{1,32}){0,2}')


@lru_cache(maxsize=8)
def _load(name):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def zone(value):
    """tzinfo for a configured IANA name (or an injected tzinfo), else None.

    A zone that cannot load (e.g. no tzdata) is unknown, never a silent UTC."""
    if isinstance(value, tzinfo):
        return value
    if not isinstance(value, str) or not ZONE.fullmatch(value):
        return None
    try:
        return _load(value)
    except Exception:
        return None


def facts(cutoff, tz, *, daylight='unknown', weather='unknown'):
    """Bucketed local-time facts at the offer cutoff plus mapped HA states."""
    local = None
    if finite(cutoff) and tz is not None:
        try:
            local = datetime.fromtimestamp(cutoff, tz)
        except Exception:
            local = None
    hour = local.hour if local is not None else None
    return {'schema': SCHEMA,
            'part_of_day': next(name for low, high, name in PARTS if low <= hour < high) if local else 'unknown',
            'day_type': ('weekend' if local.weekday() >= 5 else 'weekday') if local else 'unknown',
            'local_hour': f'{hour:02d}:00-{hour+1:02d}:00' if local else 'unknown',
            'daylight': daylight if daylight in DAYLIGHT.values() else 'unknown',
            'weather': weather if weather in WEATHER else 'unknown',
            'origin_family': {k: list(v) for k, v in ORIGINS.items()}, 'interpretation': INTERPRETATION}


def latest_ha(records, cutoff, entity):
    """The newest admitted HA snapshot at the cutoff that carries `entity`
    (arm copies share an id). Per entity, so a newer snapshot of another HA
    source (e.g. LA presence) never hides the den's sun or weather."""
    best = None
    for record in records or []:
        observed, available = record.get('observed_at'), record.get('available_at')
        if (record.get('source_kind') != 'ha.observation' or not isinstance(record.get('ha'), dict)
                or entity not in record['ha']
                or not finite(observed) or not finite(available) or observed > cutoff or available > cutoff):
            continue
        if best is None or (observed, str(record.get('id'))) > (best['observed_at'], str(best.get('id'))):
            best = record
    return best


def ha_inputs(records, cutoff, bindings):
    """Mapped daylight/weather from bound entities; unbound or unusable is unknown."""
    values = {'daylight': 'unknown', 'weather': 'unknown'}
    for entity, fact in (bindings or {}).items():
        if fact not in HA_FACTS:
            continue
        record = latest_ha(records, cutoff, entity)
        raw = record['ha'].get(entity) if record else None
        state = raw.get('state') if isinstance(raw, dict) else None
        values[HA_FACTS[fact]] = DAYLIGHT.get(state, 'unknown') if fact == 'daylight' else state if state in WEATHER else 'unknown'
    return values


def context_digest(block):
    """sha256 over the shared (non-lever) families only: equal across arms."""
    scope = block['digest_scope']
    return digest({'schema': block['schema'], 'coverage': {f: block['coverage'][f] for f in scope},
                   'ambient': block.get('ambient') if 'ambient' in scope else None,
                   'ha_facts': block.get('ha_facts') if 'ha' in scope else None})


def ha_fact_coverage(records, cutoff, bindings, fact_grants):
    """{fact: 'supplied'|'stale'|'not_granted'} for every bound HA device fact.

    supplied: an HA snapshot known at the cutoff carries an entity bound to the
    fact, so the projection binds it. The same rows serve both arms, so the
    coverage (and the digest) is identical in both. Ambient facts
    (daylight/weather) feed the ambient block only and are not listed.
    """
    from jev_evidence import AMBIENT_FACTS, FACT_GRANTS
    carried = set()
    for record in records or []:
        observed, available = record.get('observed_at'), record.get('available_at')
        if (record.get('source_kind') == 'ha.observation' and isinstance(record.get('ha'), dict)
                and finite(observed) and finite(available) and observed <= cutoff and available <= cutoff):
            carried.update(record['ha'])
    result = {}
    for entity, fact in sorted((bindings or {}).items()):
        if fact in AMBIENT_FACTS:
            continue
        if fact in FACT_GRANTS and not (fact_grants or {}).get(fact):
            status = 'not_granted'
        else:
            status = 'supplied' if entity in carried else 'stale'
        # Bindings are one-to-one; keep the strongest status if not.
        if result.get(fact) != 'supplied':
            result[fact] = status
    return dict(sorted(result.items()))


def shared_context(records, cutoff, *, grants, lever=(), tz=None, provisioned=False, bindings=None,
                   ha_provisioned=False, captions=None, fact_grants=None, qwen=None, ambient_state=None, vjepa=None):
    """Per-arm shared context blocks for one offer tick.

    grants: {family: {'a': status, 'b': status}} from evidence_policy.source_policy
    ('granted' or an explicit withheld/not-provisioned status). Every granted
    family is computed from the same rows for both arms; captions is the
    per-arm shared caption context already selected for this tick.

    provisioned: ambient is configured and granted this tick; tz its loaded
    zone (None when it cannot load: coverage timezone_unavailable, clock facts
    unknown). ambient_state: 'not_granted' when the zone is configured but the
    household grant is absent. fact_grants: {fact: bool} for HA facts that need
    their own household grant (jev_evidence.FACT_GRANTS); an absent grant is
    not_granted and the provider drops that fact. qwen: the tick's Qwen scene
    projection status, or 'not_granted', or None when not provisioned. vjepa:
    the same for the V-JEPA scene record (the temporal_encoder family).
    """
    facts_coverage = ha_fact_coverage(records, cutoff, bindings, fact_grants)
    supplied_ha = 'supplied' in facts_coverage.values()
    ambient = facts(cutoff, tz, **ha_inputs(records, cutoff, bindings)) if provisioned else None
    blocks = {}
    for arm in ('a', 'b'):
        coverage = {}
        for family in FAMILIES:
            status = (grants.get(family) or {}).get(arm, 'not_provisioned')
            if status == 'granted':
                if family == 'ha':
                    # Supplied only when the packet carries a bound, granted device fact.
                    status = ('supplied' if supplied_ha
                              else 'not_granted' if facts_coverage and set(facts_coverage.values()) == {'not_granted'}
                              else 'stale' if ha_provisioned and facts_coverage else 'not_provisioned')
                elif family == 'ambient':
                    status = (('supplied' if tz is not None else 'timezone_unavailable') if ambient is not None
                              else 'not_granted' if ambient_state == 'not_granted' else 'not_provisioned')
                elif family == 'caption':
                    status = 'supplied' if (captions or {}).get(arm) else 'stale'
                elif family == 'qwen_scene':
                    status = qwen if qwen in QWEN_STATUSES + ('not_granted',) else 'not_provisioned'
                elif family == 'temporal_encoder':
                    status = vjepa if vjepa in VJEPA_STATUSES + ('not_granted',) else 'not_provisioned'
                else:
                    status = 'not_provisioned'
            coverage[family] = status if status in COVERAGE else 'invalid'
        # Per-fact coverage follows the arm's HA family: a withheld or absent
        # family marks every bound fact with that same reason.
        family = coverage['ha']
        block = {'schema': CONTEXT_SCHEMA, 'coverage': coverage,
                 'digest_scope': sorted(f for f in FAMILIES if f not in set(lever)),
                 'ha_facts': {fact: value if family in ('supplied', 'stale', 'not_granted')
                              else family if family in FACT_COVERAGE else 'not_provisioned'
                              for fact, value in facts_coverage.items()}}
        if coverage['ambient'] in ('supplied', 'timezone_unavailable'):
            block['ambient'] = dict(ambient, origin_family={k: list(v) for k, v in ambient['origin_family'].items()})
        block['shared_context_digest'] = context_digest(block)
        blocks[arm] = block
    return blocks
