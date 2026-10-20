"""Offline JEV replay over recorded sessions (plan §7.1), dry run only.

No network, no provider and no paid call: this module never imports a
transport. It opens a ledger read-only (sqlite ?mode=ro), and for every
recorded JEV request:

1. Fidelity. Rebuilds the request's inputs from ledger rows through the live
   path (live_records, filter_ha, causal_evidence, qwen_live.scene_context,
   vjepa_live.scene_context),
   compares them with the stored request payload, re-projects them, and
   rebuilds the v5 packet with today's code. The digest is compared with the
   recorded packet_sha256 only when the recorded packet used today's semantic
   and question versions; otherwise the evidence projection is compared and
   the digest is reported as version_differs.
2. v6. Builds den-facts-6 packets at the same cutoffs (paired comparison) and
   at the cutoffs a simulated facts-key scheduler would offer, and reports
   sizes, budget levels, facts keys and simulated call counts.

3. Audit (--audit ZONE). Runs the v6 policy checks, audit-only, over the v6
   packets the shadow would have built beside each recorded v5 request.

Usage: python jev_replay.py LEDGER.sqlite3 [--monitor-side image_left|image_right] [--audit IANA_ZONE|UTC-7] SESSION_PREFIX [...]
"""
from collections import Counter
from datetime import timedelta, timezone
import json
import re
import sqlite3
import statistics
import sys
import threading

import jev_wire as wire
from evidence_policy import filter_ha, ha_policy
from jev_evidence import (DERIVED_HA, HA_FACTS, AMBIENT_FACTS, granted_bindings, ha_state, project,
                          project_context)
from live_inputs import live_records
from reasoning import build_jev_request, canonical, causal_evidence, digest, finite, shared_evidence
import store as ledger_store

# Scheduler rule (plan §6.1), identical for both arms.
MIN_OFFER_S = 2.0
KEEPALIVE_S = 10.0
GATE_KEEPALIVE_S = 30.0


class LedgerView:
    """A read-only, store-shaped view of a Lab ledger for the live read paths
    (live_records, QwenSceneLedger.receipts, events_by_ids)."""

    def __init__(self, path):
        self.db = sqlite3.connect('file:%s?mode=ro' % path, uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def get_session(self, sid):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None:
            raise ValueError('session not found')
        return dict(row)

    def events_by_ids(self, session_id, ids, kinds):
        return ledger_store.events_by_ids(self.db, self.lock, session_id, ids, kinds)

    def session_ids(self, prefixes):
        rows = self.db.execute('SELECT id FROM sessions ORDER BY started_at DESC').fetchall()
        return [r[0] for r in rows if any(r[0].startswith(p) for p in prefixes)]

    def close(self):
        self.db.close()


def infer_ha_bindings(pairs):
    """{entity: fact} recovered from recorded packets: for each projected HA
    fact, the one raw entity whose mapped state matched it in every recorded
    record. Ambient facts never reach records, so they are not recovered."""
    candidates = {}
    for raw, projected in pairs:
        raw_ha, facts = raw.get('ha') or {}, projected.get('ha') or {}
        for fact, value in facts.items():
            if fact not in HA_FACTS or fact in AMBIENT_FACTS:
                continue
            matches = set()
            for entity, item in raw_ha.items():
                if entity.split('.', 1)[0] == 'derived' and DERIVED_HA.get(entity) != fact:
                    continue
                state = item.get('state') if isinstance(item, dict) else None
                if ha_state(fact, state) == value:
                    matches.add(entity)
            candidates[fact] = matches if fact not in candidates else candidates[fact] & matches
    bindings = {}
    for fact, entities in sorted(candidates.items()):
        derived = [e for e in entities if DERIVED_HA.get(e) == fact]
        chosen = derived or sorted(entities)
        if len(chosen) == 1:
            bindings[chosen[0]] = fact
    return bindings


def recorded_ha_pairs(recorded):
    """(raw HA record, projected facts) pairs for infer_ha_bindings from the
    recorded requests. A v5 state carries its projected records (evidence),
    paired by id. The facts-only v6 state (den-facts-6) carries none: its
    fact values are the devices block (plus now.frigate_occupancy for
    person_present), each from the newest HA snapshot carrying that fact, so
    it pairs with the newest raw state per entity among the request's HA
    records. Only registered fact values pair; a 'stale', coverage or unknown
    word says nothing about which entity feeds the fact. Without this a v6
    session recovers no binding, the replay projects no HA fact and every
    device reads stale."""
    pairs = []
    for item in recorded:
        state = item['event']['request']['state']
        records = [r for r in item['payload']['records']
                   if r.get('source_kind') == 'ha.observation' and isinstance(r.get('ha'), dict)]
        if state.get('schema') not in wire.WIRE_VERSIONS:
            evidence = {str(r['id']): r for r in state.get('evidence', [])}
            pairs += [(r, evidence[str(r['id'])]) for r in records if str(r['id']) in evidence]
            continue
        devices = state.get('devices') or {}
        facts = {fact: devices[name] for fact, name in wire.DEVICE_FIELDS.items()
                 if devices.get(name) in HA_FACTS[fact]}
        occupancy = (state.get('now') or {}).get('frigate_occupancy')
        if occupancy in HA_FACTS['person_present']:
            facts['person_present'] = occupancy
        cutoff, newest = item['payload']['cutoff'], {}
        for r in records:
            if not finite(r.get('observed_at')) or r['observed_at'] > cutoff:
                continue
            for entity, raw in r['ha'].items():
                key = (r['observed_at'], str(r['id']))
                if entity not in newest or key > newest[entity][0]:
                    newest[entity] = (key, raw)
        if facts and newest:
            pairs.append(({'ha': {e: raw for e, (_, raw) in newest.items()}}, {'ha': facts}))
    return pairs


def _activations(view, sid):
    rows = view.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id",
                           (sid,)).fetchall()
    return {p['generation']: p for p in (json.loads(r[0]) for r in rows)}


def _recorded(view, sid):
    requests = {}
    for row in view.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.request' ORDER BY id", (sid,)):
        value = json.loads(row[0])
        requests[value['request_id']] = value
    result = []
    for row in view.db.execute('SELECT id,arm,state,payload,result,dispatched_at FROM lab_reasoning_requests WHERE session_id=?'
                               ' ORDER BY dispatched_at', (sid,)):
        if row['id'] not in requests:
            continue
        result.append({'request_id': row['id'], 'arm': row['arm'], 'state': row['state'],
                       'payload': json.loads(row['payload']), 'result': json.loads(row['result']) if row['result'] else None,
                       'event': requests[row['id']]})
    return result


def _horizon(shared):
    for record in shared or []:
        for key in (record.get('qwen_scene') or {}):
            if key.startswith('summary_last_'):
                return int(key[len('summary_last_'):-1])
    return 120


def rebuild_inputs(view, session, activation, payload):
    """(records, shared) the live scheduler would have offered at the request
    cutoff, rebuilt from ledger rows (strictly causal: available by cutoff)."""
    import qwen_live
    import vjepa_live
    windows, generation, arm, cutoff = activation['reasoning_windows'], payload['generation'], payload['arm'], payload['cutoff']
    rows = live_records(view, session, generation, cutoff, window_seconds=max(windows.values()), retain_history=True)
    rows = filter_ha(rows, ha_policy(activation.get('ha_evidence')))
    records = causal_evidence(rows, site_id=session['site_id'], session_id=session['id'], arm=arm, generation=generation,
                              cutoff=cutoff, window_seconds=payload['window_seconds'], retain_history=True)
    shared = []
    kinds = {r.get('source_kind') for r in payload.get('shared_context') or []}
    if 'qwen.observation' in kinds:
        qwen = qwen_live.scene_context(view, session, cutoff, generation=generation, arms={arm},
                                       horizon_seconds=_horizon(payload['shared_context']))
        shared += qwen.get(arm, [])
    if 'vjepa.observation' in kinds:
        shared += vjepa_live.scene_context(view, session, cutoff, generation=generation, arms={arm}).get(arm, [])
    if shared:
        shared = shared_evidence(shared, site_id=session['site_id'], session_id=session['id'], arm=arm,
                                 generation=generation, cutoff=cutoff)
    return records, shared


def rebuild_v5(payload, bindings, model, budget=False, monitor_side=None):
    """Today's v5 packet from the stored request payload, via the provider path.
    budget: rebuild with the live pre-send trim, as a request that recorded
    provenance.budget_trimmed was built."""
    context = project_context(payload.get('context_snapshot'))
    dropped = [f for f, v in (context or {}).get('ha_facts', {}).items() if v != 'supplied']
    bound = dict(ha_bindings=granted_bindings(bindings, dropped), zone_bindings={})
    packet, provenance = build_jev_request(
        project(payload['records'], **bound), model=model, site_id=payload['site_id'], session_id=payload['session_id'],
        arm=payload['arm'], generation=payload['generation'], cutoff=payload['cutoff'], window_seconds=payload['window_seconds'],
        retain_history=True, allow_recent_history=True, semantic=True,
        shared_context=project(payload.get('shared_context') or [], **bound), context=context, budget=budget,
        monitor_side=monitor_side)
    return packet, provenance, bound, context


class LedgerTimeline:
    """Frame facts re-derived from the ledger (the policy's derivation path),
    each perception.output row once, for one session."""

    def __init__(self, view, session, bound):
        self.view, self.session, self.bound, self.facts = view, session, bound, {}

    def entries(self, arm, generation, cutoff, seconds=max(wire.STORYLINE_SECONDS)):
        rows = live_records(self.view, self.session, generation, cutoff, window_seconds=seconds, retain_history=True)
        result = []
        for record in rows:
            if record.get('source_kind') != 'perception.output' or record.get('arm') != arm:
                continue
            key = (arm, generation, record['id'])
            if key not in self.facts:
                admitted = causal_evidence([record], site_id=self.session['site_id'], session_id=self.session['id'], arm=arm,
                                           generation=generation, cutoff=cutoff, window_seconds=seconds, retain_history=True)
                self.facts[key] = wire.frame_facts(project(admitted, **self.bound)[0]) if admitted else None
            if self.facts[key] is not None:
                result.append(self.facts[key])
        return wire.select_timeline(result, cutoff, seconds)


def code_gate(state):
    """Local abstention without a JEV call (plan §6.1), else None."""
    now = state['now']
    if now['camera_evidence'] != 'current':
        return 'camera_stale'
    debounced = wire.debounced_state(state)
    if debounced == 'multiple_people':
        return 'multiple_people'
    if debounced == 'no_person':
        qwen = state.get('qwen') or {}
        facts = (qwen.get('latest') or {}).get('facts') or {}
        if qwen.get('coverage') != 'supplied' or facts.get('people_visible') in ('none', None):
            return 'no_person'
    return None


def simulate(ticks):
    """Simulated facts-key scheduler over [(t, state)] of one arm: offers,
    keepalives, gated abstentions and skips (plan §6.1)."""
    counts = Counter()
    last_offer = last_key = last_gate_at = None
    for t, state in ticks:
        gate = code_gate(state)
        if gate:
            if last_gate_at is None or t - last_gate_at >= GATE_KEEPALIVE_S or last_key != ('gate', gate):
                counts['gate_' + gate] += 1
                last_gate_at, last_key = t, ('gate', gate)
            else:
                counts['skip_gate_keepalive_not_due'] += 1
            continue
        key = wire.facts_key(state)
        due = last_offer is None or t - last_offer >= MIN_OFFER_S
        if key != last_key and due:
            counts['offer_changed'] += 1
        elif key == last_key and state['now']['people_in_view'] == 'one' and t - last_offer >= KEEPALIVE_S:
            counts['offer_keepalive'] += 1
        else:
            counts['skip_unchanged_facts' if key == last_key else 'skip_rate_limited'] += 1
            continue
        last_offer, last_key = t, key
    counts['calls'] = counts['offer_changed'] + counts['offer_keepalive']
    return counts


def _summary(values):
    values = sorted(values)
    if not values:
        return None
    return {'n': len(values), 'median': statistics.median(values), 'p95': values[min(len(values)-1, int(.95*len(values)))],
            'max': values[-1]}


def audit_session(view, sid, *, site_timezone=None, storyline_seconds=60, monitor_side=None):
    """The v6 policy checks (LabJevPolicy.verify_facts, audit-only: no
    admission) over v6 packets built from every recorded request exactly as
    the shadow builds them beside live v5: a Phase-2 shadow over history."""
    from jev_evidence import AMBIENT_EGRESS, DEVICE_EGRESS, PRESENCE_EGRESS, QWEN_EGRESS
    from jev_policy import SHADOW_REASONS, LabJevPolicy
    session = view.get_session(sid)
    recorded = _recorded(view, sid)
    bindings = infer_ha_bindings(recorded_ha_pairs(recorded))
    gate = LabJevPolicy(view, None, None, ha_bindings=bindings, site_timezone=site_timezone, monitor_side=monitor_side,
                        audit_only=True)
    # The audit checks derivation, not permission: every family is granted.
    policy = {'site_id': session['site_id'], 'camera_ids': [session['camera_id']], 'ambient_context': AMBIENT_EGRESS,
              'device_context': DEVICE_EGRESS, 'owner_presence': PRESENCE_EGRESS}
    sources = frozenset({QWEN_EGRESS, wire.WIRE_VERSION})
    rings, outcomes, verified = {}, Counter(), Counter()
    for item in recorded:
        payload = item['payload']
        _, _, bound, context = rebuild_v5(payload, bindings, item['event']['request']['model'], monitor_side=monitor_side)
        ring = rings.setdefault((payload['arm'], payload['generation']), wire.FrameRing())
        local, packet, binding = wire.prepare(
            project(payload['records'], **bound), project(payload.get('shared_context') or [], **bound), context,
            site_id=payload['site_id'], session_id=payload['session_id'], arm=payload['arm'], generation=payload['generation'],
            cutoff=payload['cutoff'], window_seconds=payload['window_seconds'], model=item['event']['request']['model'],
            storyline_seconds=storyline_seconds, ring=ring, monitor_side=monitor_side)
        request = {'request_id': item['request_id'], 'site_id': payload['site_id'], 'session_id': payload['session_id'],
                   'arm': payload['arm'], 'generation': payload['generation'],
                   **{k: v for k, v in binding.items() if k not in ('packet_bytes', 'estimated_tokens')}}
        try:
            result = gate.verify_facts(request, packet, local, policy, sources)
            outcomes['match'] += 1
            verified[result['timeline_verified']] += 1
        except ValueError as error:
            outcomes[SHADOW_REASONS.get(str(error), 'verification_failed')] += 1
    return {'session': sid[:8], 'requests': len(recorded), 'verdicts': dict(outcomes), 'timeline_verified': dict(verified)}


def replay_session(view, sid, *, storyline_seconds=60, simulate_scheduler=True, monitor_side=None):
    session = view.get_session(sid)
    activations = _activations(view, sid)
    recorded = _recorded(view, sid)
    bindings = infer_ha_bindings(recorded_ha_pairs(recorded))
    fidelity, versions = Counter(), Counter()
    sizes, levels, keys, ring_story = {'state': [], 'packet': []}, Counter(), set(), Counter()
    rings, timeline, contexts = {}, None, {}
    invalid = 0
    for item in recorded:
        payload, event = item['payload'], item['event']
        activation = activations.get(payload['generation'])
        recorded_state = event['request']['state']
        model = event['request']['model']
        trimmed = ((item['result'] or {}).get('provenance') or {}).get('budget_trimmed') is not None
        packet, provenance, bound, context = rebuild_v5(payload, bindings, model, budget=trimmed, monitor_side=monitor_side)
        if timeline is None:
            timeline = LedgerTimeline(view, session, bound)
        # 1a. Inputs: ledger rows -> the stored request payload.
        if activation is not None:
            records, shared = rebuild_inputs(view, session, activation, payload)
            fidelity['inputs_match' if canonical(records) == canonical(payload['records'])
                     and canonical(shared) == canonical(payload.get('shared_context') or []) else 'inputs_mismatch'] += 1
        # 1b. Projection and packet digest.
        fidelity['evidence_match' if canonical(packet['state']['evidence']) == canonical(recorded_state.get('evidence'))
                 else 'evidence_mismatch'] += 1
        version = ((recorded_state.get('semantic') or {}).get('version'), (item['result'] or {}).get('diagnostics', {}).get('question_version'))
        versions[version] += 1
        if version == (packet['state']['semantic']['version'], provenance['question_version']):
            fidelity['digest_match' if digest(packet) == event['packet_sha256'] else 'digest_mismatch'] += 1
        else:
            fidelity['digest_version_differs'] += 1
        # 2. v6 at the same cutoff.
        cutoff, arm, generation = payload['cutoff'], payload['arm'], payload['generation']
        projected = project(payload['records'], **bound)
        shared = project(payload.get('shared_context') or [], **bound)
        contexts.setdefault(arm, []).append((cutoff, context))
        entries = timeline.entries(arm, generation, cutoff)
        local = wire.sidecar(projected, shared, context, entries, cutoff, storyline_seconds)
        value, level, size, _ = wire.build_packet(local, model=model, cutoff=cutoff, storyline_seconds=storyline_seconds,
                                                  monitor_side=monitor_side)
        try:
            wire.validate_state(value['state'])
        except ValueError:
            invalid += 1
        sizes['state'].append(len(canonical(value['state']).encode()))
        sizes['packet'].append(size)
        levels[level] += 1
        keys.add(wire.facts_key(value['state']))
        # The Phase-2 ring (fed only by request payloads) against the ledger timeline.
        ring = rings.setdefault((arm, generation), wire.FrameRing())
        ring.update(projected)
        ring_state = wire.build_state(wire.sidecar(projected, shared, context, ring.window(cutoff, storyline_seconds), cutoff,
                                                   storyline_seconds), cutoff=cutoff, storyline_seconds=storyline_seconds,
                                       monitor_side=monitor_side)
        ring_story['match' if canonical(ring_state) == canonical(value['state']) else 'differs'] += 1
    recorded_calls = Counter(i['arm'] for i in recorded)
    report = {'session': sid[:8], 'requests': len(recorded), 'recorded_versions': {'%s/%s' % k: v for k, v in versions.items()},
              'ha_bindings_recovered': sorted(bindings.values()), 'fidelity': dict(fidelity),
              'v6_same_cutoffs': {'state_bytes': _summary(sizes['state']), 'packet_bytes': _summary(sizes['packet']),
                                  'budget_levels': dict(levels), 'invalid_states': invalid, 'distinct_facts_keys': len(keys),
                                  'ring_vs_ledger_state': dict(ring_story)},
              'recorded_v5_calls': dict(recorded_calls), 'smoothing_on_recorded_v5': smoothing_on_recorded(view, sid)}
    if simulate_scheduler and timeline is not None:
        report['simulated_v6_scheduler'] = _simulate_session(view, session, activations, recorded, bindings, timeline,
                                                             contexts, storyline_seconds, monitor_side)
    return report


def smoothing_on_recorded(view, sid):
    """The code-owned timeline (jev_smoothing, default parameters) over the
    recorded v5 answers, per arm: the share of answers showing an activity
    and the number of shown-activity changes (flicker), raw against smoothed.
    Free: no call, only the answers already in the ledger."""
    import jev_smoothing
    outputs = {}
    for (raw,) in view.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.output' ORDER BY id", (sid,)):
        value = json.loads(raw)
        outputs.setdefault(value.get('arm'), []).append(value)
    result = {}
    for arm, items in sorted((a, v) for a, v in outputs.items() if a in ('a', 'b')):
        answers = jev_smoothing.answers_from_outputs(items)
        if not answers:
            continue
        smoothed = jev_smoothing.smooth(answers)
        raw_shown = [a['choice'] if a['choice'] != 'insufficient_evidence' else None for a in answers]
        shown = [p['activities'][0] if p['activities'] else None for p in smoothed]

        def changes(values):
            present = [v for v in values if v]
            return sum(1 for x, y in zip(present, present[1:]) if x != y)
        result[arm] = {'answers': len(answers), 'raw_activity_share': round(sum(1 for v in raw_shown if v)/len(answers), 3),
                       'smoothed_activity_share': round(sum(1 for v in shown if v)/len(answers), 3),
                       'raw_changes': changes(raw_shown), 'smoothed_changes': changes(shown)}
    return result


def _context_at(contexts, arm, t):
    best = None
    for cutoff, context in contexts.get(arm, []):
        if cutoff <= t and (best is None or cutoff >= best[0]):
            best = (cutoff, context)
    return best[1] if best else None


def _simulate_session(view, session, activations, recorded, bindings, timeline, contexts, storyline_seconds, monitor_side=None):
    """Ticks at each new frame's availability per arm. The shared context block
    at a tick is the newest recorded one for that arm (ambient/device coverage
    words change rarely); records and the Qwen record are rebuilt at the tick."""
    import qwen_live
    import vjepa_live
    result = {}
    horizon = next((_horizon(i['payload'].get('shared_context')) for i in recorded if i['payload'].get('shared_context')), None)
    for generation, activation in sorted(activations.items()):
        rows = view.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='perception.output'
            AND json_extract(payload,'$.generation')=? ORDER BY json_extract(payload,'$.available_at')""",
                               (session['id'], generation)).fetchall()
        ticks = {'a': [], 'b': []}
        for (raw,) in rows:
            value = json.loads(raw)
            arm, t = value.get('arm'), value.get('available_at')
            if arm not in ticks or not isinstance(t, (int, float)) or value.get('status') != 'completed':
                continue
            cutoff = t
            context = _context_at(contexts, arm, cutoff)
            dropped = [f for f, v in (context or {}).get('ha_facts', {}).items() if v != 'supplied']
            bound = dict(ha_bindings=granted_bindings(bindings, dropped), zone_bindings={})
            window = live_records(view, session, generation, cutoff, window_seconds=10, retain_history=True)
            window = filter_ha(window, ha_policy(activation.get('ha_evidence')))
            records = causal_evidence(window, site_id=session['site_id'], session_id=session['id'], arm=arm,
                                      generation=generation, cutoff=cutoff, window_seconds=10, retain_history=True)
            shared = []
            policy = activation.get('source_policy') or {}
            if horizon is not None and policy.get('qwen_scene', {}).get(arm) == 'granted':
                qwen = qwen_live.scene_context(view, session, cutoff, generation=generation, arms={arm}, horizon_seconds=horizon)
                shared += qwen.get(arm, [])
            if policy.get('temporal_encoder', {}).get(arm) == 'granted':
                shared += vjepa_live.scene_context(view, session, cutoff, generation=generation, arms={arm}).get(arm, [])
            if shared:
                shared = shared_evidence(shared, site_id=session['site_id'], session_id=session['id'], arm=arm,
                                         generation=generation, cutoff=cutoff)
            local = wire.sidecar(project(records, **bound), project(shared, **bound), context,
                                 timeline.entries(arm, generation, cutoff), cutoff, storyline_seconds)
            ticks[arm].append((cutoff, wire.build_state(local, cutoff=cutoff, storyline_seconds=storyline_seconds,
                                                        monitor_side=monitor_side)))
        for arm, items in ticks.items():
            counts = simulate(items)
            result.setdefault(arm, Counter()).update(counts)
            result[arm]['ticks'] += len(items)
    return {arm: dict(c) for arm, c in result.items()}


def main(argv):
    if len(argv) < 3 or argv[2:3] == ['--audit'] and len(argv) < 5:
        print(__doc__.strip().splitlines()[-1])
        return 2
    view, prefixes, zone, side = LedgerView(argv[1]), list(argv[2:]), None, None
    if prefixes[:1] == ['--monitor-side'] and len(prefixes) >= 3:
        side, prefixes = prefixes[1], prefixes[2:]
    if prefixes[:1] == ['--audit']:
        zone, prefixes = prefixes[1], prefixes[2:]
        offset = re.fullmatch(r'UTC([+-]\d{1,2})', zone)
        if offset:
            # A fixed offset for hosts without tzdata (the zone would not load).
            zone = timezone(timedelta(hours=int(offset.group(1))))
    try:
        for sid in view.session_ids(prefixes):
            result = (audit_session(view, sid, site_timezone=zone, monitor_side=side) if zone
                      else replay_session(view, sid, monitor_side=side))
            print(json.dumps(result, sort_keys=True))
    finally:
        view.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
