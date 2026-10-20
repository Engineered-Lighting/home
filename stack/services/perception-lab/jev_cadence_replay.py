"""Offline check of facts-bound validity, live smoothing and the facts-key
cadence over recorded sessions (plan §5.2, §5.3, §6.1). Dry run only.

No network, no provider and no paid call: the ledger is opened read-only
(jev_replay.LedgerView), the facts keys are rebuilt from ledger rows with the
v6 builders (jev_replay's causal path), the occupancy gate reads the recorded
ha.observation rows at each tick's time, and the live rules are the live
functions themselves (service_runtime.cadence_decision and occupancy_step,
jev_smoothing.Validity, Smoother and facts_bound).

For each arm it scores, against the session's reviewed human labels:
  recorded   the recorded answers as delivered (strict frame-TTL validity:
             exactly what findings reports), then the same answers held on
             unchanged facts (valid_facts) and smoothed;
  simulated  the facts-key cadence's offers over 1 Hz scheduler ticks, each
             answered by a recorded answer (the recorded answer on the same
             facts key nearest in time, else the nearest recorded answer; its
             recorded latency kept), strict, valid_facts and smoothed, with
             local gate abstentions while the room reads vacant.
The simulated answers assume JEV answers the same facts the same way (it is
highly consistent on identical packets); no new answer is ever requested.

Offline-only operator tool: not part of the controller release.

Usage: python jev_cadence_replay.py LEDGER.sqlite3 [--monitor-side image_left|image_right] SESSION_PREFIX [...]
"""
from collections import Counter
import json
import sys

import jev_smoothing as smoothing
import jev_wire as wire
from evidence_policy import filter_ha, ha_policy
from jev_evidence import granted_bindings, project, project_context
from jev_replay import (LedgerTimeline, LedgerView, _activations, _context_at, _horizon, _recorded, infer_ha_bindings,
                        recorded_ha_pairs)
from live_inputs import live_records
from occupancy_gate import CAMERA_ORIGIN, DEFAULTS, ENTITY, SCAN_ROWS, check
from reasoning import causal_evidence, finite, score_intervals, shared_evidence
from service_runtime import REASONING_CADENCE_DEFAULTS, REASONING_MIN_OFFER_INTERVAL_S, cadence_decision, occupancy_step


def labels(view, sid):
    """Reviewed human session-clock labels, exactly as findings admits them."""
    result = []
    for (raw,) in view.db.execute('SELECT payload FROM annotations WHERE session_id=? AND deleted=0', (sid,)):
        item = json.loads(raw)
        if (item.get('time_basis', 'session') != 'session' or item.get('reviewed') is not True or item.get('origin') != 'human'
                or item.get('end_ms') is None or item['end_ms'] <= item['at_ms']):
            continue
        result.append({'start': item['at_ms']/1000, 'end': item['end_ms']/1000, 'activities': [item['label']],
                       'reviewed': True, 'source': 'human'})
    return result


def occupancy_entity(view, sid):
    """The one camera-derived binary_sensor the recorded HA rows carry, else None."""
    found = set()
    for (raw,) in view.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='ha.observation' ORDER BY id LIMIT 20", (sid,)):
        for entity, item in (json.loads(raw).get('ha') or {}).items():
            if ENTITY.fullmatch(entity) and isinstance(item, dict) and item.get('origin') == CAMERA_ORIGIN:
                found.add(entity)
    return found.pop() if len(found) == 1 else None


class ReplayOccupancy:
    """occupancy_gate.OccupancyGate at a replay time. The live gate reads the
    newest SCAN_ROWS rows of the ledger, which is 'now' only live; here the
    same rule runs over the rows recorded as available by the replay time."""

    def __init__(self, view, session, entity, *, hold_seconds=DEFAULTS['hold_seconds'], grace_seconds=DEFAULTS['grace_seconds']):
        self.session, self.entity, self.hold, self.grace = session, entity, hold_seconds, grace_seconds
        self.rows, self.now, self.last_on = [], None, None
        for (raw,) in view.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='ha.observation' ORDER BY id",
                                      (session['id'],)):
            value = json.loads(raw)
            item = (value.get('ha') or {}).get(entity)
            self.rows.append((value.get('available_at'), value.get('observed_at'), value.get('valid_until'), value.get('site_id'),
                              value.get('camera_id'), *((item.get('state'), item.get('status'), item.get('origin'))
                                                        if isinstance(item, dict) else (None, None, None))))

    def __call__(self):
        now = self.now
        causal = [r for r in self.rows if finite(r[0]) and r[0] <= now][-SCAN_ROWS:]
        latest = newest_on = None
        for available, observed, valid_until, site, camera, state, status, origin in reversed(causal):
            if (not finite(observed) or not finite(valid_until) or observed > now or site != self.session['site_id']
                    or camera != self.session['camera_id'] or status not in ('known', 'unknown') or origin != CAMERA_ORIGIN):
                continue
            if latest is None:
                latest = {'observed_at': observed, 'valid_until': valid_until, 'state': state if status == 'known' else None}
            if status == 'known' and state == 'on' and (newest_on is None or observed > newest_on):
                newest_on = observed
        if newest_on is not None and (self.last_on is None or newest_on > self.last_on):
            self.last_on = newest_on
        if latest is None:
            return {'state': 'unknown', 'reason': 'occupancy_unavailable'}
        if now > latest['valid_until'] + self.grace:
            return {'state': 'unknown', 'reason': 'occupancy_stale'}
        if latest['state'] == 'on':
            return {'state': 'occupied', 'reason': 'person_detected'}
        if latest['state'] == 'off':
            if self.last_on is not None and now - self.last_on <= self.hold:
                return {'state': 'occupied', 'reason': 'occupancy_hold'}
            return {'state': 'vacant', 'reason': 'no_person'}
        return {'state': 'unknown', 'reason': 'occupancy_state_unknown'}


class TickStates:
    """The v6 state an arm's request would carry at any cutoff, rebuilt from
    ledger rows (jev_replay's simulated-scheduler path)."""

    def __init__(self, view, session, activation, generation, recorded, bindings, contexts, *, storyline_seconds=60,
                 monitor_side=None):
        self.view, self.session, self.activation, self.generation = view, session, activation, generation
        self.bindings, self.contexts, self.storyline = bindings, contexts, storyline_seconds
        self.monitor_side = monitor_side
        self.horizon = next((_horizon(i['payload'].get('shared_context')) for i in recorded if i['payload'].get('shared_context')), None)
        first = next((c for items in contexts.values() for _, c in items), None)
        dropped = [f for f, v in (first or {}).get('ha_facts', {}).items() if v != 'supplied']
        self.timeline = LedgerTimeline(view, session, dict(ha_bindings=granted_bindings(bindings, dropped), zone_bindings={}))

    def state(self, arm, cutoff):
        import qwen_live
        import vjepa_live
        view, session, generation = self.view, self.session, self.generation
        context = _context_at(self.contexts, arm, cutoff)
        dropped = [f for f, v in (context or {}).get('ha_facts', {}).items() if v != 'supplied']
        bound = dict(ha_bindings=granted_bindings(self.bindings, dropped), zone_bindings={})
        window = live_records(view, session, generation, cutoff, window_seconds=10, retain_history=True)
        window = filter_ha(window, ha_policy(self.activation.get('ha_evidence')))
        records = causal_evidence(window, site_id=session['site_id'], session_id=session['id'], arm=arm, generation=generation,
                                  cutoff=cutoff, window_seconds=10, retain_history=True)
        shared = []
        policy = self.activation.get('source_policy') or {}
        if self.horizon is not None and policy.get('qwen_scene', {}).get(arm) == 'granted':
            shared += qwen_live.scene_context(view, session, cutoff, generation=generation, arms={arm},
                                              horizon_seconds=self.horizon).get(arm, [])
        if policy.get('temporal_encoder', {}).get(arm) == 'granted':
            shared += vjepa_live.scene_context(view, session, cutoff, generation=generation, arms={arm}).get(arm, [])
        if shared:
            shared = shared_evidence(shared, site_id=session['site_id'], session_id=session['id'], arm=arm,
                                     generation=generation, cutoff=cutoff)
        local = wire.sidecar(project(records, **bound), project(shared, **bound), context,
                             self.timeline.entries(arm, generation, cutoff), cutoff, self.storyline)
        return wire.build_state(local, cutoff=cutoff, storyline_seconds=self.storyline, monitor_side=self.monitor_side)


def simulate_cadence(ticks, *, settings, interval, gated_by_occupancy, hold):
    """The live facts-key rule over [(t, key, people, visual, occupancy_state)]
    of one arm: (offers [(t, key, reason)], gates [(t, vacant)], counts)."""
    last, track, last_gate = None, {}, None
    offers, gates, counts = [], [], Counter()
    for t, key, people, visual, occupancy in ticks:
        if gated_by_occupancy:
            gated, vacant, occupied = occupancy_step(track, occupancy, t, hold)
        else:
            gated, vacant, occupied = False, False, people in ('one', 'multiple')
        action, reason, _ = cadence_decision(last, now=t, key=key, visual=visual, config=(1,), gated=gated, occupied=occupied,
                                             interval=interval, settings=settings)
        if action == 'offer':
            offers.append((t, key, reason))
            counts['offer_' + reason] += 1
            last, last_gate = {'key': key, 'at': t, 'config': (1,)}, None
            continue
        counts['skip_' + reason] += 1
        if action == 'gate':
            if last_gate is None or not 0 <= t - last_gate < settings['gate_keepalive_s']:
                gates.append((t, vacant))
                counts['local_abstain'] += 1
                last_gate = t
        else:
            last_gate = None
    counts['calls'] = len(offers)
    return offers, gates, counts


def validity_closers(ticks, deliveries, gates, hold):
    """{request_id: valid_through} from the live Validity rule over session-time
    ticks [(t, key, vacant)], deliveries [(t, request_id, key)] and local gate
    abstentions [(t, vacant)]."""
    events = ([(t, 0, ('observe', key, vacant)) for t, key, vacant in ticks]
              + [(t, 1, ('gate', vacant)) for t, vacant in gates]
              + [(t, 2, ('deliver', rid, key)) for t, rid, key in deliveries])
    validity, closers = smoothing.Validity(hold), {}
    for t, _, item in sorted(events, key=lambda e: (e[0], e[1])):
        if item[0] == 'observe':
            closed = validity.observe(t, key=item[1], vacant=item[2])
        elif item[0] == 'gate':
            closed = validity.local_abstain(t, vacant=item[1])
        else:
            closed = validity.deliver(t, item[1], item[2])
        for c in closed:
            closers[c['request_id']] = c['valid_through']
    return closers


def _run(items, hold):
    """Live smoothing (one Smoother, as LiveReasoning runs it per arm) over
    delivered answers and local gate abstentions in time order."""
    smoother = smoothing.Smoother(hold_max=hold)
    for item in items:
        prediction = smoother.update({**item, 'mode': 'live'})
        prediction.pop('posterior')
        yield prediction


def summary(metrics):
    return {'coverage': _r(metrics['coverage']), 'exact_match_at_coverage': _r(metrics['exact_match_at_coverage']),
            'false_confident_seconds': _r(metrics['false_confident_seconds']),
            'per_activity': {k: {'coverage': _r(v['coverage']), 'exact_match_at_coverage': _r(v['exact_match_at_coverage']),
                                 'false_confident_seconds': _r(v['false_confident_seconds'])}
                             for k, v in metrics['per_activity'].items()},
            'missed_transitions': metrics['missed_transitions'],
            'transition_delay_s': [_r(t['delay_s']) for t in metrics['transitions']]}


def _r(value):
    return round(value, 3) if finite(value) else value


def _changes(predictions):
    shown = [p['activities'][0] for p in predictions if p.get('activities')]
    return sum(1 for x, y in zip(shown, shown[1:]) if x != y)


def cadence_session(view, sid, *, storyline_seconds=60, monitor_side=None, settings=None, interval=REASONING_MIN_OFFER_INTERVAL_S):
    settings = dict(settings or REASONING_CADENCE_DEFAULTS)
    hold = settings['hold_max_s']
    session = view.get_session(sid)
    start, end = session['started_at'], session['ended_at']
    activations, recorded = _activations(view, sid), _recorded(view, sid)
    bindings = infer_ha_bindings(recorded_ha_pairs(recorded))
    contexts = {}
    for item in recorded:
        contexts.setdefault(item['arm'], []).append((item['payload']['cutoff'], project_context(item['payload'].get('context_snapshot'))))
    truth = labels(view, sid)
    entity = occupancy_entity(view, sid)
    outputs = {}
    for (raw,) in view.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.output' ORDER BY id", (sid,)):
        value = json.loads(raw)
        outputs[value['request_id']] = value
    report = {'session': sid[:8], 'duration_s': round(end - start, 1), 'reviewed_labels': len(truth),
              'occupancy_entity': 'bound' if entity else 'not_found', 'settings': {**settings, 'min_offer_interval_s': interval},
              'arms': {}}
    for generation, activation in sorted(activations.items()):
        states = TickStates(view, session, activation, generation, recorded, bindings, contexts,
                            storyline_seconds=storyline_seconds, monitor_side=monitor_side)
        frames = {'a': set(), 'b': set()}
        for (raw,) in view.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='perception.output'
                AND json_extract(payload,'$.generation')=?""", (sid, generation)):
            value = json.loads(raw)
            if value.get('arm') in frames and finite(value.get('available_at')) and value.get('status') == 'completed':
                frames[value['arm']].add(value['available_at'])
        grid = {start + k for k in range(1, int(end - start) + 1)}
        for arm in ('a', 'b'):
            requests = [i for i in recorded if i['arm'] == arm and i['payload']['generation'] == generation]
            cutoffs = {i['payload']['cutoff'] for i in requests}
            gate = ReplayOccupancy(view, session, entity) if entity else None
            keyed, ticks, observe = {}, [], []
            for t in sorted(grid | frames[arm] | cutoffs):
                if gate is not None:
                    gate.now = t
                state = states.state(arm, t)
                key = wire.facts_key(state)
                keyed[t] = key
                occupancy = check(gate)[1]['state'] if gate else 'unknown'
                if t in grid or t in frames[arm]:
                    ticks.append((t, key, state['now']['people_in_view'], state['now']['camera_evidence'] != 'none', occupancy))
                observe.append((t - start, key, occupancy == 'vacant'))
            # Recorded answers, as delivered.
            answers = []
            for item in requests:
                output = outputs.get(item['request_id'])
                prediction = (output or {}).get('prediction')
                if not isinstance(prediction, dict):
                    continue
                judgment = output['judgment']
                answers.append({'request_id': item['request_id'], 'cutoff': item['payload']['cutoff'], 'key': keyed[item['payload']['cutoff']],
                                'prediction': prediction, 'probabilities': judgment.get('probabilities'), 'choice': judgment.get('activity')})
            answers.sort(key=lambda a: a['prediction']['available_at'])
            recorded_view = variants(truth, answers, observe, [], hold)
            # The facts-key cadence over the same session, answered from recorded answers.
            offers, gates, counts = simulate_cadence(ticks, settings=settings, interval=interval, gated_by_occupancy=gate is not None,
                                                     hold=getattr(gate, 'hold', hold))
            simulated, reused = [], Counter()
            for index, (t, key, _) in enumerate(offers):
                same = [a for a in answers if a['key'] == key]
                pool = same or answers
                if not pool:
                    continue
                chosen = min(pool, key=lambda a: (abs(a['cutoff'] - t), a['cutoff'] > t))
                reused['same_facts_key' if same else 'nearest_in_time'] += 1
                shift = t - chosen['cutoff']
                p = chosen['prediction']
                simulated.append({**chosen, 'request_id': 'sim-%s-%d' % (arm, index), 'cutoff': t, 'key': key,
                                  'prediction': {**p, **{k: p[k] + shift for k in ('start', 'end', 'available_at', 'valid_until')}}})
            simulated.sort(key=lambda a: a['prediction']['available_at'])
            simulated_view = variants(truth, simulated, observe, [(t - start, vacant) for t, vacant in gates], hold)
            report['arms'][arm] = {
                'recorded': {'requests': len(requests), 'answers': len(answers), **recorded_view},
                'simulated_facts_key_cadence': {'requests': len(offers), 'answers': len(simulated), 'answers_reused': dict(reused),
                                                'scheduler': dict(counts), **simulated_view}}
    return report


def variants(truth, answers, observe, gates, hold):
    """strict / valid_facts / smoothed predictions of one arm's answers, scored
    when there are reviewed labels; activity-display share and flicker always."""
    strict = [a['prediction'] for a in answers]
    deliveries = [(a['prediction']['available_at'], a['request_id'], a['key']) for a in answers]
    closers = validity_closers(observe, deliveries, gates, hold)
    valid = smoothing.facts_bound([(a['request_id'], {**a['prediction'], 'valid_through': a['prediction']['available_at'] + hold})
                                   for a in answers], closers, hold)
    steps = list(_run(sorted([{'t': a['prediction']['available_at'], 'available_at': a['prediction']['available_at'],
                               'probabilities': a['probabilities'], 'choice': a['choice']} for a in answers]
                             + [({'t': t, 'gate': 'no_person'} if vacant else
                                 {'t': t, 'choice': 'insufficient_evidence', 'probabilities': None}) for t, vacant in gates],
                             key=lambda x: x['t']), hold))
    result = {'local_abstains': len(gates), 'raw_changes': _changes(strict), 'smoothed_changes': _changes(steps)}
    if answers:
        seconds = [min(p['end'], p['valid_until']) - max(p['start'], p['available_at']) for p in valid]
        result['valid_facts_hold_s_median'] = _r(sorted(seconds)[len(seconds)//2])
    if truth:
        for name, predictions in (('strict', strict), ('valid_facts', valid), ('smoothed', steps)):
            result[name] = summary(score_intervals(truth, predictions, run_mode='live'))
    return result


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    view, prefixes, side = LedgerView(argv[1]), list(argv[2:]), None
    if prefixes[:1] == ['--monitor-side'] and len(prefixes) >= 3:
        side, prefixes = prefixes[1], prefixes[2:]
    try:
        for sid in view.session_ids(prefixes):
            print(json.dumps(cadence_session(view, sid, monitor_side=side), sort_keys=True))
    finally:
        view.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
