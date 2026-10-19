"""Bake-off arms D (a local classifier over the facts) and E (rules), written
as tick records (lab-ticks-1, read by lab_eval).

Offline operator tool, not part of the controller release. The ledger is
opened read-only (jev_replay.LedgerView and lab_regression.open_ro, sqlite
?mode=ro) and never written; the only outputs are the files named by --out.
No network, no provider, no model call: every word is rebuilt from ledger rows
by the v6 builders (jev_cadence_replay.TickStates, jev_wire.build_state).

Privacy: the feature file stays on this host, as the ledger does. It carries
den-facts-6 words, object label counts, enum-like HA state words and the
occupancy reading per tick; never frames, boxes, keypoints, free text, times
of day finer than the ambient bucket, or identifiers of people. Placement-
agnostic by owner principle: every state is built with monitor_side=None and
no desk_seat, a state carrying now.position or now.head_toward is refused, and
now.head never becomes a feature.

  features  --ledger DB --session PREFIX [...] --out F.jsonl [--jev-config FILE]
            One line per (session, arm, 1 Hz tick from start+1 to the end), on
            the generation active at the tick: the state words as sparse
            one-hot features plus raw counts, the occupancy gate reading and
            people_in_view. '<out>.meta.json' holds the vocabulary. HA bindings
            come from the config's ha_bindings, else are recovered from the
            recorded requests. A session recorded with reasoning off has no
            request rows: its shared context is then synthesised per tick as
            the live scheduler builds it (ambient_context, horizon 120).
  rules     --features F --out T.jsonl              arm E, the fixed table RULES
  fit       --features F --ledger DB --split S.json --out T.jsonl [--manifest M] [--fit loo|dev]
            arm D: multinomial logistic regression (numpy) on gold labels,
            one model per held-out calendar day fitted on the other days
            (the lab_eval split, lab-split-1; dev sessions are never test).
            Within a fold the latest CALIBRATION_SHARE of the training sessions
            (by start time) is held back: the weights are fitted on the rest
            and the temperature on the held-back ticks. A fold with a single
            labelled training session fits T in-sample and says so in the
            header's fold report ('temperature_fit').
"""
import argparse
from collections import Counter
import json
import os
import re
import sys

import jev_wire as wire
from ambient_context import shared_context, zone
from evidence_policy import filter_ha, ha_policy, lever_families, source_policy
from jev_cadence_replay import ReplayOccupancy, TickStates, occupancy_entity
from jev_evidence import OBJECTS, project_context
from jev_replay import LedgerView, _activations, _horizon, _recorded, infer_ha_bindings, recorded_ha_pairs
from live_inputs import live_records
from occupancy_gate import check

SCHEMA = 'lab-ticks-1'
FEATURE_SCHEMA = 'lab-features-1'
CLASSES = ('reading', 'computer_work', 'phone_use', 'resting', 'transition', 'other', 'empty', 'abstain')
ANSWERS = CLASSES[:6]                         # arm D's softmax classes; empty is a rule, abstain always 0
GOLD = ('owner_reviewed', 'owner_statement')  # gold provenances (lab_eval); qwen_proposed never: D consumes Qwen words
TICK_SECONDS = 1.0
WINDOW_SECONDS = 10                           # the raw-feature window (the v6 'now' window)
STORYLINE_SECONDS = 60
FORBIDDEN_NOW = ('position', 'head_toward')   # placement words: absent with monitor_side=None and no desk_seat
DROPPED_NOW = ('head', 'origin', 'method', 'calibration')  # head never a feature; the rest are constants
OBJECT_LABELS = tuple(sorted(OBJECTS))
AMBIENT_WORDS = ('part_of_day', 'day_type', 'daylight', 'weather')
HA_STATE = re.compile(r'[A-Za-z0-9_-]{1,32}')  # an enum-like HA state; timestamps and text never become features

# Arm E: (required features, class, weight). Every row whose features are all
# present adds its weight; ABSTAIN_FLOOR keeps a share of abstention; the
# masses are then normalised. A fixed table, not fitted.
QWEN_NOW = 'qwen.latest.state=current'
RULES = (
    (('now.cue_state=book_near_hand',), 'reading', 1.0),
    (('story.current=book_near_hand',), 'reading', 0.5),
    ((QWEN_NOW, 'qwen.latest.facts.activity_hypothesis=reading'), 'reading', 0.6),
    ((QWEN_NOW, 'qwen.latest.facts.held_object=book'), 'reading', 0.4),
    (('now.cue_state=phone_near_face',), 'phone_use', 1.0),
    (('now.cue_state=phone_near_hand',), 'phone_use', 0.8),
    (('story.current=phone_near_face',), 'phone_use', 0.5),
    (('story.current=phone_near_hand',), 'phone_use', 0.4),
    ((QWEN_NOW, 'qwen.latest.facts.activity_hypothesis=phone_use'), 'phone_use', 0.6),
    (('now.cue_state=keyboard_or_mouse_near_hand',), 'computer_work', 1.0),
    (('story.current=keyboard_or_mouse_near_hand',), 'computer_work', 0.5),
    (('support.computer_work.pc_input_role=counts_camera_cue_at_pc',), 'computer_work', 0.8),
    ((QWEN_NOW, 'qwen.latest.facts.activity_hypothesis=computer_work'), 'computer_work', 0.6),
    ((QWEN_NOW, 'qwen.latest.facts.typing_visible=yes'), 'computer_work', 0.4),
    (('story.current=person_no_hand_cue', 'story.current.stability=steady'), 'resting', 0.6),
    ((QWEN_NOW, 'qwen.latest.facts.activity_hypothesis=resting'), 'resting', 0.6),
    ((QWEN_NOW, 'qwen.latest.facts.subject_posture=lying'), 'resting', 0.4),
    ((QWEN_NOW, 'qwen.latest.facts.activity_hypothesis=transition'), 'transition', 0.6),
    ((QWEN_NOW, 'qwen.latest.facts.subject_posture=walking'), 'transition', 0.5),
    ((QWEN_NOW, 'qwen.latest.facts.activity_hypothesis=other'), 'other', 0.6),
)
ABSTAIN_FLOOR = 0.25
# Arm D: deterministic full-batch gradient descent from zero weights.
L2, STEPS, RATE = 1e-2, 300, 0.5
TEMPERATURES = tuple(round(0.5 + 0.1 * i, 1) for i in range(26))  # 0.5 .. 3.0
CALIBRATION_SHARE = 0.2                       # the latest training sessions held back for the temperature


# --- Features ----------------------------------------------------------------------

def _words(prefix, value, out):
    """Sparse one-hot features of a closed-vocabulary block: 'prefix=word' per
    word, 'prefix.item' per list item, recursing into nested blocks."""
    if isinstance(value, dict):
        for key, item in value.items():
            _words('%s.%s' % (prefix, key), item, out)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                out['%s.%s' % (prefix, item)] = 1
    elif isinstance(value, bool):
        out['%s=%s' % (prefix, 'yes' if value else 'no')] = 1
    elif isinstance(value, str):
        out['%s=%s' % (prefix, value)] = 1


def check_placement_free(state):
    """ValueError when the state carries a placement word (owner principle)."""
    segments = (state.get('storyline') or {}).get('segments') or []
    if any(key in state['now'] for key in FORBIDDEN_NOW) or any('head_toward' in s for s in segments):
        raise ValueError('placement word in a state: build it with monitor_side=None and no desk_seat')


def state_features(state):
    """The den-facts-6 words of one state as sparse features."""
    check_placement_free(state)
    now = {k: v for k, v in state['now'].items() if k not in DROPPED_NOW}
    out = {}
    _words('now', now, out)
    story = state.get('storyline') or {}
    current = (story.get('segments') or [{}])[-1]
    out['story.current=%s' % wire.debounced_state(state)] = 1
    for key in ('span', 'stability'):
        if key in current:
            out['story.current.%s=%s' % (key, current[key])] = 1
    _words('story', {'last_change': story.get('last_change')}, out)
    _words('support', state['support'], out)
    _words('devices', {k: v for k, v in (state.get('devices') or {}).items() if k != 'origin'}, out)
    ambient = state.get('ambient') or {}
    _words('ambient', {k: ambient[k] for k in AMBIENT_WORDS if k in ambient}, out)
    _words('coverage', state['coverage'], out)
    qwen = state.get('qwen') or {}
    latest = qwen.get('latest') or {}
    _words('qwen', {'coverage': qwen.get('coverage'), 'latest': {'state': latest.get('state')}}, out)
    _words('qwen.latest.facts', {k: v for k, v in (latest.get('facts') or {}).items() if k != 'change_vs_prior'}, out)
    return out


def raw_features(window, arm, state):
    """Counts from the recorded rows at the tick: the arm's latest frame's
    person and object label counts and pose presence, the frames in the
    window, the number of cues, and each HA entity's newest enum-like state."""
    frames = sorted((r for r in window if r.get('source_kind') == 'perception.output' and r.get('arm') == arm),
                    key=lambda r: (r['observed_at'], str(r['id'])))
    out = {'raw.frames_10s': len(frames), 'raw.cues': len(state['now'].get('cues') or [])}
    if frames:
        latest = frames[-1]
        labels = Counter(o.get('label') for o in latest.get('objects') or [])
        out['raw.persons'] = labels.get('person', 0)
        out['raw.pose_present'] = 1 if latest.get('pose_instances') else 0
        for label in OBJECT_LABELS:
            if labels.get(label):
                out['raw.objects.%s' % label.replace(' ', '_')] = labels[label]
    newest = {}
    for record in window:
        if record.get('source_kind') != 'ha.observation' or record.get('arm') != arm or not isinstance(record.get('ha'), dict):
            continue
        for entity, item in record['ha'].items():
            key = (record['observed_at'], str(record['id']))
            if entity not in newest or key > newest[entity][0]:
                newest[entity] = (key, item.get('state') if isinstance(item, dict) else None)
    for entity, (_, value) in sorted(newest.items()):
        if isinstance(value, str) and HA_STATE.fullmatch(value):
            out['ha.%s=%s' % (entity, value.lower())] = 1
    return out


def _has_rows(view, sid, kind):
    return view.db.execute('SELECT 1 FROM events WHERE session_id=? AND kind=? LIMIT 1', (sid, kind)).fetchone() is not None


def synthesised_context(view, session, activation, generation, bindings, config, cutoff, *, horizon, perceivers):
    """{arm: projected shared context} as the live scheduler builds it at this
    cutoff (service_runtime._shared_context), for a session that recorded no
    request. Offline assumptions: ambient is provisioned iff a site_timezone
    is configured, every bound fact is granted unless the config names
    fact_grants, and a Qwen/V-JEPA perceiver ran iff its rows exist."""
    import qwen_live
    import vjepa_live
    grants = source_policy(activation.get('source_policy'), ha=activation.get('ha_evidence'),
                           caption=activation.get('caption_evidence'))
    window = max((activation.get('reasoning_windows') or {}).values(), default=WINDOW_SECONDS)
    rows = live_records(view, session, generation, cutoff, window_seconds=window, retain_history=True)
    statuses = {}
    for family, key in (('qwen_scene', 'qwen'), ('temporal_encoder', 'vjepa')):
        arms = {arm for arm in ('a', 'b') if grants[family][arm] == 'granted'}
        if not arms or not perceivers[key]:
            continue
        if key == 'qwen':
            found = qwen_live.scene_context(view, session, cutoff, generation=generation, arms=arms, horizon_seconds=horizon)
        else:
            found = vjepa_live.scene_context(view, session, cutoff, generation=generation, arms=arms)
        record = next((r[0] for r in found.values() if r), None)
        statuses[key] = record['qwen_scene' if key == 'qwen' else 'vjepa_scene']['status'] if record else 'stale'
    tz_name = config.get('site_timezone') or activation.get('site_timezone')
    fact_grants = config.get('fact_grants') if isinstance(config.get('fact_grants'), dict) else {f: True for f in bindings.values()}
    blocks = shared_context(rows, cutoff, grants=grants, lever=lever_families(activation.get('lever')),
                            tz=zone(tz_name) if tz_name else None, provisioned=tz_name is not None, bindings=bindings,
                            ha_provisioned=bool(bindings), captions={}, fact_grants=fact_grants, **statuses)
    return {arm: project_context(block) for arm, block in blocks.items()}


def generation_at(starts, t):
    """The generation active at t: the newest one effective by t, else the first."""
    active = [g for g, at in starts if at is not None and at <= t]
    return active[-1] if active else starts[0][0]


def session_features(view, sid, *, config=None, storyline_seconds=STORYLINE_SECONDS):
    """(records, summary) of one session: one feature record per arm and 1 Hz
    tick from start+1 to the end, on the generation active at the tick."""
    config = config or {}
    session = view.get_session(sid)
    start, end = session['started_at'], session['ended_at']
    activations, recorded = _activations(view, sid), _recorded(view, sid)
    if not activations or end is None:
        return [], {'ticks': 0, 'skipped': 'no activation' if not activations else 'not ended'}
    if config.get('ha_bindings'):
        bindings, binding_source = dict(config['ha_bindings']), 'config'
    else:
        bindings, binding_source = infer_ha_bindings(recorded_ha_pairs(recorded)), 'recorded'
    contexts = {}
    for item in recorded:
        contexts.setdefault(item['arm'], []).append((item['payload']['cutoff'], project_context(item['payload'].get('context_snapshot'))))
    synthesise = not contexts
    perceivers = {'qwen': _has_rows(view, sid, 'qwen.observation'), 'vjepa': _has_rows(view, sid, 'vjepa.observation')}
    states = {}
    for generation, activation in sorted(activations.items()):
        states[generation] = TickStates(view, session, activation, generation, recorded, bindings, contexts,
                                        storyline_seconds=storyline_seconds, monitor_side=None)
        if states[generation].horizon is None and synthesise:
            states[generation].horizon = _horizon(None)
    starts = sorted(((g, a.get('effective_at')) for g, a in activations.items()), key=lambda x: (x[1] is None, x[1] or 0, x[0]))
    entity = occupancy_entity(view, sid)
    gate = ReplayOccupancy(view, session, entity) if entity else None
    records = []
    for k in range(1, int(end - start) + 1):
        t = start + k
        generation = generation_at(starts, t)
        tick, activation = states[generation], activations[generation]
        if synthesise:
            tick.contexts = {arm: [(t, context)] for arm, context in synthesised_context(
                view, session, activation, generation, bindings, config, t, horizon=tick.horizon, perceivers=perceivers).items()}
        if gate is not None:
            gate.now = t
        occupancy = check(gate)[1]['state'] if gate is not None else 'unknown'
        window = filter_ha(live_records(view, session, generation, t, window_seconds=WINDOW_SECONDS, retain_history=True),
                           ha_policy(activation.get('ha_evidence')))
        for arm in ('a', 'b'):
            state = tick.state(arm, t)
            features = state_features(state)
            features.update(raw_features(window, arm, state))
            features['occupancy=%s' % occupancy] = 1
            records.append({'session_id': sid, 'arm': arm, 'generation': generation, 't': float(k),
                            'facts_key': wire.facts_key(state), 'occupancy': occupancy,
                            'people': state['now']['people_in_view'], 'features': features})
    return records, {'ticks': len(records), 'bindings': binding_source, 'contexts': 'synthesised' if synthesise else 'recorded',
                     'occupancy_entity': 'bound' if entity else 'not_found', 'generations': sorted(activations)}


def write_features(view, prefixes, out, config=None, ledger_name=''):
    """Write the feature file and its '.meta.json'; {session_id: summary}."""
    vocabulary, sessions = set(), {}
    with open(out, 'w', encoding='utf-8') as stream:
        for sid in view.session_ids(prefixes):
            records, sessions[sid] = session_features(view, sid, config=config)
            for record in records:
                vocabulary.update(record['features'])
                stream.write(json.dumps(record, sort_keys=True) + '\n')
    meta = {'schema': FEATURE_SCHEMA, 'vocabulary': sorted(vocabulary), 'sessions': sessions,
            'source': {'ledger': ledger_name, 'wire': wire.WIRE_VERSION, 'facts_key': wire.FACTS_KEY_DEFAULT,
                       'storyline_seconds': STORYLINE_SECONDS, 'window_seconds': WINDOW_SECONDS, 'monitor_side': None,
                       'desk_seat': None, 'jev_config': bool(config)}}
    with open(out + '.meta.json', 'w', encoding='utf-8') as stream:
        json.dump(meta, stream, sort_keys=True, indent=1)
    return sessions


def read_features(file):
    with open(file, encoding='utf-8') as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    with open(file + '.meta.json', encoding='utf-8') as stream:
        meta = json.load(stream)
    if meta.get('schema') != FEATURE_SCHEMA:
        raise SystemExit('feature file schema %s required' % FEATURE_SCHEMA)
    return records, meta


# --- Tick records ------------------------------------------------------------------

def header(run, arm, source, *, consumes_qwen, calibration):
    return {'schema': SCHEMA, 'run': run, 'arm': arm, 'consumes_qwen': bool(consumes_qwen), 'calibration': calibration,
            'classes': list(CLASSES), 'source': source}


def emission(record, probabilities):
    """An emission at the record's tick, available at once, closed at the next tick."""
    return {'session_id': record['session_id'], 'arm': record['arm'], 't': record['t'], 'available_at': record['t'],
            'closed_at': record['t'] + TICK_SECONDS, 'probabilities': probabilities, 'facts_key': record['facts_key']}


def write_ticks(file, head, emissions):
    with open(file, 'w', encoding='utf-8') as out:
        out.write(json.dumps(head, sort_keys=True) + '\n')
        for item in emissions:
            out.write(json.dumps(item, sort_keys=True) + '\n')


def normalise(masses):
    """Masses over exactly CLASSES summing to 1; nothing positive is abstain."""
    full = {c: max(0.0, float(masses.get(c, 0.0))) for c in CLASSES}
    total = sum(full.values())
    return {c: v / total for c, v in full.items()} if total > 0 else {**{c: 0.0 for c in CLASSES}, 'abstain': 1.0}


def empty_by_rule(record):
    """The local empty-room rule of both arms: the occupancy gate reads vacant,
    or nobody is in a CURRENT latest frame. A stale (expired_historical) frame
    reading nobody is not a positive empty: it goes to the arm's own logic
    (arm E abstains on it)."""
    if record['occupancy'] == 'vacant':
        return True
    return record['people'] == 'none' and 'now.camera_evidence=current' in record['features']


# --- Arm E -------------------------------------------------------------------------

def rule_distribution(record):
    if empty_by_rule(record):
        return normalise({'empty': 1.0})
    features = record['features']
    if record['people'] in ('none', 'multiple', 'not_observed') or 'now.camera_evidence=none' in features:
        return normalise({'abstain': 1.0})
    masses = {'abstain': ABSTAIN_FLOOR}
    for names, cls, weight in RULES:
        if all(name in features for name in names):
            masses[cls] = masses.get(cls, 0.0) + weight
    return normalise(masses)


def run_rules(features_file, out, run_name='facts-rules'):
    records, meta = read_features(features_file)
    emissions = [emission(r, rule_distribution(r)) for r in records]
    source = {'features': os.path.basename(features_file), 'features_source': meta['source'], 'sessions': sorted(meta['sessions']),
              'rule_table': [[list(names), cls, weight] for names, cls, weight in RULES], 'abstain_floor': ABSTAIN_FLOOR,
              'empty_rule': 'occupancy vacant, or people_in_view none on a current frame',
              'abstain_rule': 'people none on a stale frame, multiple people, or no camera evidence',
              'note': 'a fixed rule table, not fitted: its masses are hand-set weights, so calibration is none'}
    write_ticks(out, header(run_name, 'E', source, consumes_qwen=True, calibration='none'), emissions)
    return len(emissions)


# --- Arm D -------------------------------------------------------------------------

def gold_labels(intervals):
    """{session_id: [(start, end, class)]} of the gold intervals arm D learns from."""
    labels = {}
    for item in intervals:
        cls = 'empty' if item['activity'] == 'empty_room' else item['activity']
        if item['gold'] and item['provenance'] in GOLD and cls in CLASSES[:7]:
            labels.setdefault(item['session_id'], []).append((item['start'], item['end'], cls))
    return labels


def truth_at(labels, record):
    """The class of the gold intervals covering the tick, else None (none or a conflict)."""
    found = {cls for start, end, cls in labels.get(record['session_id'], []) if start <= record['t'] < end}
    return found.pop() if len(found) == 1 else None


def folds(assignment, sessions, mode):
    """[(name, train sessions, test sessions)]. loo: per calendar day, test the
    day's non-dev sessions on a model fitted on every other day's sessions.
    dev: one model fitted on the dev sessions, tested on all the others."""
    missing = sorted(s for s in sessions if s not in assignment)
    if missing:
        raise SystemExit('sessions not in the ledger: ' + ' '.join(s[:8] for s in missing))
    test_pool = sorted(s for s in sessions if not assignment[s]['dev'])
    if mode == 'dev':
        return [('dev', sorted(s for s in sessions if assignment[s]['dev']), test_pool)]
    day = {s: assignment[s]['day'] or 'unknown' for s in sessions}
    return [(d, sorted(s for s in sessions if day[s] != d), [s for s in test_pool if day[s] == d])
            for d in sorted({day[s] for s in test_pool})]


def matrix(np, records, vocabulary):
    index = {name: i for i, name in enumerate(vocabulary)}
    X = np.zeros((len(records), len(vocabulary)))
    for row, record in enumerate(records):
        for name, value in record['features'].items():
            if name in index:
                X[row, index[name]] = float(value)
    return X


def softmax(np, logits):
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def fit_softmax(np, X, y):
    """L2-regularised multinomial logistic regression by full-batch gradient
    descent: fixed steps, zero init, deterministic."""
    n, d = X.shape
    Y = np.zeros((n, len(ANSWERS)))
    Y[np.arange(n), y] = 1.0
    W, b = np.zeros((d, len(ANSWERS))), np.zeros(len(ANSWERS))
    for _ in range(STEPS):
        G = softmax(np, X @ W + b) - Y
        W -= RATE * (X.T @ G / n + L2 * W)
        b -= RATE * G.mean(axis=0)
    return W, b


def fit_temperature(np, logits, y):
    """The grid temperature with the lowest mean negative log-likelihood."""
    best = None
    for T in TEMPERATURES:
        nll = float(-np.log(softmax(np, logits / T)[np.arange(len(y)), y] + 1e-12).mean())
        if best is None or nll < best[0]:
            best = (nll, T)
    return best[1]


def calibration_split(train, started):
    """(weight ticks, temperature ticks, 'held_back'|'in_sample'): the latest
    CALIBRATION_SHARE of the labelled training sessions by start time (at least
    one) are held back for the temperature. With a single labelled session
    nothing can be held back, and T is fitted in-sample on the weight ticks."""
    sids = sorted({r['session_id'] for r, _ in train}, key=lambda s: (started.get(s) is None, started.get(s) or 0, s))
    if len(sids) < 2:
        return train, train, 'in_sample'
    held = set(sids[len(sids) - max(1, int(len(sids) * CALIBRATION_SHARE + 0.999)):])
    return ([p for p in train if p[0]['session_id'] not in held], [p for p in train if p[0]['session_id'] in held],
            'held_back')


def fit_fold(np, train, vocabulary, started=None):
    """(predict(records) -> probability rows over ANSWERS, temperature, how T
    was fitted): weights on the earlier training sessions, the temperature on
    the held-back latest ones (calibration_split)."""
    weight_ticks, temperature_ticks, how = calibration_split(train, started or {})
    X = matrix(np, [r for r, _ in weight_ticks], vocabulary)
    scale = np.maximum(1.0, np.abs(X).max(axis=0))
    y = np.array([ANSWERS.index(cls) for _, cls in weight_ticks])
    W, b = fit_softmax(np, X / scale, y)
    Xt = matrix(np, [r for r, _ in temperature_ticks], vocabulary) / scale
    T = fit_temperature(np, Xt @ W + b, np.array([ANSWERS.index(cls) for _, cls in temperature_ticks]))
    return (lambda records: softmax(np, ((matrix(np, records, vocabulary) / scale) @ W + b) / T)), T, how


def run_fit(features_file, db, manifest, split, out, mode='loo', run_name='facts-logistic'):
    import numpy as np
    import lab_eval
    records, meta = read_features(features_file)
    sessions, intervals, _ = lab_eval.labels(db, manifest)
    assignment, note = lab_eval.assign(sessions, split)
    if note:
        print(note)
    labels = gold_labels(intervals)
    vocabulary = list(meta['vocabulary'])
    emissions, report = [], []
    for name, train_sids, test_sids in folds(assignment, sorted({r['session_id'] for r in records}), mode):
        train_set = set(train_sids)
        train = [(r, truth_at(labels, r)) for r in records if r['session_id'] in train_set and not empty_by_rule(r)]
        train = [(r, cls) for r, cls in train if cls in ANSWERS]
        test = [r for r in records if r['session_id'] in set(test_sids)]
        fold = {'fold': name, 'train_sessions': len(train_sids), 'train_ticks': len(train), 'test_sessions': len(test_sids),
                'test_ticks': len(test), 'train_classes': dict(sorted(Counter(cls for _, cls in train).items()))}
        if not train:
            print('fold %s: no labelled training ticks; its %d test sessions get no emissions' % (name, len(test_sids)))
            report.append(dict(fold, temperature=None, skipped='no labelled training ticks'))
            continue
        started = {sid: (sessions.get(sid) or {}).get('started_at') for sid in train_sids}
        predict, fold['temperature'], fold['temperature_fit'] = fit_fold(np, train, vocabulary, started)
        learned = [r for r in test if not empty_by_rule(r)]
        rows = predict(learned) if learned else []
        for record, row in zip(learned, rows):
            emissions.append(emission(record, normalise({cls: float(p) for cls, p in zip(ANSWERS, row)})))
        emissions += [emission(r, normalise({'empty': 1.0})) for r in test if empty_by_rule(r)]
        report.append(fold)
        print('fold %s: train %d sessions, %d labelled ticks %s; test %d sessions, %d ticks; temperature %.1f (%s)'
              % (name, len(train_sids), len(train), json.dumps(fold['train_classes']), len(test_sids), len(test),
                 fold['temperature'], fold['temperature_fit']))
    emissions.sort(key=lambda e: (e['session_id'], e['arm'], e['available_at']))
    source = {'features': os.path.basename(features_file), 'features_source': meta['source'], 'fit': mode, 'folds': report,
              'gold': list(GOLD), 'split_frozen_at': split.get('frozen_at'),
              'model': {'kind': 'multinomial_logistic_regression', 'l2': L2, 'steps': STEPS, 'rate': RATE,
                        'classes': list(ANSWERS), 'features': len(vocabulary), 'temperature_grid': [TEMPERATURES[0], TEMPERATURES[-1]]},
              'calibration_share': CALIBRATION_SHARE,
              'empty_rule': 'occupancy vacant, or people_in_view none on a current frame (forced, not learned)',
              'abstain': 'always 0 (lab_eval thresholds)'}
    write_ticks(out, header(run_name, 'D', source, consumes_qwen=True, calibration='distribution'), emissions)
    return report


# --- CLI ---------------------------------------------------------------------------

def main(argv):
    import lab_regression
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    features = commands.add_parser('features')
    features.add_argument('--ledger', default=lab_regression.LEDGER, help='the ledger snapshot, opened read-only')
    features.add_argument('--session', nargs='+', required=True, help='session id prefixes')
    features.add_argument('--out', required=True)
    features.add_argument('--jev-config', help='JEV provisioning file (ha_bindings, site_timezone, fact_grants)')
    rules = commands.add_parser('rules')
    rules.add_argument('--features', required=True)
    rules.add_argument('--out', required=True)
    rules.add_argument('--run', default='facts-rules')
    fit = commands.add_parser('fit')
    fit.add_argument('--features', required=True)
    fit.add_argument('--ledger', default=lab_regression.LEDGER, help='the ledger with the labels, opened read-only')
    fit.add_argument('--manifest', default=None)
    fit.add_argument('--split', required=True, help='the lab_eval split (lab-split-1)')
    fit.add_argument('--out', required=True)
    fit.add_argument('--fit', choices=('loo', 'dev'), default='loo')
    fit.add_argument('--run', default='facts-logistic')
    args = parser.parse_args(argv)
    if args.command == 'features':
        config = None
        if args.jev_config:
            with open(lab_regression.path(args.jev_config), encoding='utf-8') as stream:
                config = json.load(stream)
        if not os.path.isfile(lab_regression.path(args.ledger)):
            raise SystemExit('not found: %s' % args.ledger)
        view = LedgerView(lab_regression.path(args.ledger))
        try:
            sessions = write_features(view, args.session, args.out, config, os.path.basename(args.ledger))
        finally:
            view.close()
        for sid, summary in sessions.items():
            print('%s %s' % (sid[:8], json.dumps(summary, sort_keys=True)))
    elif args.command == 'rules':
        print('emissions %d' % run_rules(args.features, args.out, args.run))
    else:
        import lab_eval
        split = lab_eval.load_split(args.split)
        manifest = lab_regression.load_manifest(args.manifest) if args.manifest else {}
        db = lab_regression.open_ro(args.ledger)
        try:
            run_fit(args.features, db, manifest, split, args.out, args.fit, args.run)
        finally:
            db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
