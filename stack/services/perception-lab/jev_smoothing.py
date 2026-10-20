"""Code-owned activity timeline over JEV answers (plan §5.3). Pure; no I/O.

JEV answers "now" on every call and is stateless; this module owns time. A
7-state HMM (sticky activities, a short-lived transition state, no_person)
filters the answers causally, and a hysteresis rule decides what is shown.
Emissions are tempered (max(p, floor) ** alpha) because repeated answers on
near-identical facts are correlated, not independent evidence. An
insufficient_evidence answer is a flat emission; code gates are hard: two
frames with no person force no_person, multiple people force abstention.

The same function runs live and offline; sweep() re-runs it over cached
answers for free parameter searches.
"""
import functools
import itertools
import math

STATES = ('reading', 'phone_use', 'computer_work', 'resting', 'transition', 'other', 'no_person')
JEV_STATES = ('reading', 'phone_use', 'computer_work', 'resting', 'transition', 'other')
DEFAULTS = {'stay': 0.97, 'transition_stay': 0.80, 'transition_share': 0.5, 'alpha': 0.6, 'floor': 0.02,
            'switch': 0.60, 'margin': 0.15, 'abstain': 0.50, 'hold_max': 12.0, 'sticky_stay': 0.99,
            'sticky_threshold': 0.7, 'insufficient': 'flat', 'min_choice': 0.0, 'release': None,
            'confidence': 'posterior'}
# How an answer's insufficient_evidence mass is read (abstain handling):
#   flat    an insufficient_evidence choice is uninformative; its mass is ignored
#           in any other answer (jev-smoothing-1).
#   spread  the insufficient_evidence mass is "cannot tell": it supports every
#           JEV state equally (and no_person), so a hedged answer (resting 0.6,
#           insufficient 0.4) is weak evidence, a confident one strong, and a
#           hedged insufficient answer (phone 0.4, insufficient 0.6) still
#           leans toward phone.
# min_choice: a confidence floor; an answer whose chosen activity has a lower
# probability is read as insufficient_evidence.
# release: an insufficient_evidence answer at or above this probability hides
# the shown activity (abstains) until the belief is re-established; None: off.
INSUFFICIENT_MODES = ('flat', 'spread')
# The shown activity's confidence:
#   posterior  the filtered belief (jev-smoothing-1). Repeated answers on
#              near-identical facts are correlated, so it can exceed what any
#              single answer said.
#   evidence   the belief capped by the highest probability any one JEV answer
#              gave that activity within the last hold_max seconds: smoothing
#              extends coverage, never confidence beyond the evidence.
CONFIDENCE_MODES = ('posterior', 'evidence')
MAX_STEP_SECONDS = 15
GATES = ('no_person', 'multiple_people')
HARD_FLOOR = 1e-6


def params(**overrides):
    value = dict(DEFAULTS)
    unknown = set(overrides) - set(value)
    if unknown:
        raise ValueError('unknown smoothing parameter')
    value.update(overrides)
    numbers = [k for k in value if k not in ('insufficient', 'release', 'confidence')] + (['release'] if value['release'] is not None else [])
    if any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) for k in numbers):
        raise ValueError('invalid smoothing parameters')
    if (not 0 < value['stay'] < 1 or not 0 < value['transition_stay'] < 1 or not 0 <= value['transition_share'] <= 1
            or not 0 < value['alpha'] <= 1 or not 0 < value['floor'] < 1 or not 0 < value['hold_max'] <= 60
            or not 0 < value['sticky_stay'] < 1 or not 0 <= value['margin'] < 1
            or not 0 < value['abstain'] <= value['switch'] <= 1
            or value['insufficient'] not in INSUFFICIENT_MODES or not 0 <= value['min_choice'] < 1
            or (value['release'] is not None and not 0 < value['release'] <= 1)
            or value['confidence'] not in CONFIDENCE_MODES):
        raise ValueError('invalid smoothing parameters')
    return value


def transition_matrix(stay, transition_stay, transition_share):
    """Per-second transition rows (each sums to one): an activity keeps
    `stay`; the leaving mass goes `transition_share` to transition and the
    rest evenly to the other states; transition keeps `transition_stay`."""
    n, t = len(STATES), STATES.index('transition')
    rows = []
    for i in range(n):
        row = [0.0]*n
        if i == t:
            row = [(1-transition_stay)/(n-1)]*n
            row[i] = transition_stay
        else:
            leave = 1-stay
            others = [j for j in range(n) if j not in (i, t)]
            for j in others:
                row[j] = leave*(1-transition_share)/len(others)
            row[t] = leave*transition_share
            row[i] = stay
        rows.append(row)
    return rows


def _multiply(a, b):
    n = len(a)
    return [[sum(a[i][k]*b[k][j] for k in range(n)) for j in range(n)] for i in range(n)]


def powers(matrix, limit=MAX_STEP_SECONDS):
    """{dt: matrix ** dt} for dt in 1..limit, precomputed once."""
    result, current = {1: matrix}, matrix
    for step in range(2, limit+1):
        current = _multiply(current, matrix)
        result[step] = current
    return result


def _probability(probabilities, key):
    value = probabilities.get(key, 0.0)
    return float(value) if type(value) in (int, float) and math.isfinite(value) else 0.0


def emission(answer, p):
    """Per-state likelihoods for one observation."""
    gate = answer.get('gate')
    if gate == 'no_person':
        return [1.0 if s == 'no_person' else HARD_FLOOR for s in STATES]
    probabilities = answer.get('probabilities')
    if gate == 'multiple_people' or not isinstance(probabilities, dict):
        return [1.0]*len(STATES)
    choice = answer.get('choice')
    if choice != 'insufficient_evidence' and p['min_choice'] > 0 and _probability(probabilities, choice) < p['min_choice']:
        choice = 'insufficient_evidence'
    if p['insufficient'] == 'flat':
        if choice == 'insufficient_evidence':
            return [1.0]*len(STATES)
        unsure = 0.0
    else:
        unsure = _probability(probabilities, 'insufficient_evidence')
        if choice == 'insufficient_evidence' and choice != answer.get('choice'):
            # Below the confidence floor: the chosen activity's mass is unsure too.
            unsure += _probability(probabilities, answer.get('choice'))
    values = []
    for state in STATES:
        value = _probability(probabilities, state) if state in JEV_STATES else 0.0
        if choice != answer.get('choice') and state == answer.get('choice'):
            value = 0.0
        values.append(max(value + unsure, p['floor'])**p['alpha'])
    return values


@functools.lru_cache(maxsize=16)
def _tables(stay, transition_stay, transition_share, sticky_stay):
    return (powers(transition_matrix(stay, transition_stay, transition_share)),
            powers(transition_matrix(sticky_stay, transition_stay, transition_share)))


class Smoother:
    """The causal filter, one answer at a time (live: one per arm and
    experiment generation). smooth() is exactly this over a list, so the live
    timeline equals the offline one for the same answers."""

    def __init__(self, **overrides):
        self.p = params(**overrides)
        self.base, self.sticky = _tables(self.p['stay'], self.p['transition_stay'], self.p['transition_share'],
                                         self.p['sticky_stay'])
        self.belief = [1.0/len(STATES)]*len(STATES)
        self.shown = self.previous = None
        self.recent = []  # (t, probabilities) of JEV answers within hold_max, for the evidence cap

    def update(self, answer):
        """One prediction (score_intervals shape plus shown and posterior)."""
        p, t = self.p, answer['t']
        if self.previous is not None and t < self.previous:
            raise ValueError('answers must be in time order')
        dt = 1 if self.previous is None else min(MAX_STEP_SECONDS, max(1, int(round(t-self.previous))))
        continues = answer.get('continues')
        table = self.sticky if isinstance(continues, (int, float)) and continues >= p['sticky_threshold'] else self.base
        matrix = table[dt]
        n = len(STATES)
        prior = [sum(self.belief[i]*matrix[i][j] for i in range(n)) for j in range(n)]
        joint = [a*b for a, b in zip(prior, emission(answer, p))]
        total = sum(joint)
        belief = self.belief = [x/total for x in joint] if total > 0 and math.isfinite(total) else prior
        best = max(range(n), key=lambda i: (belief[i], -i))
        shown = self.shown
        probabilities = answer.get('probabilities')
        released = (p['release'] is not None and answer.get('choice') == 'insufficient_evidence'
                    and isinstance(probabilities, dict) and _probability(probabilities, 'insufficient_evidence') >= p['release'])
        if answer.get('gate') == 'multiple_people' or belief[best] < p['abstain'] or released:
            shown = None
        elif shown is None:
            shown = best if belief[best] >= p['switch'] else None
        elif best != shown and belief[best] >= p['switch'] and belief[best]-belief[shown] >= p['margin']:
            shown = best
        self.shown, self.previous = shown, t
        state = STATES[shown] if shown is not None else None
        activity = state if state not in (None, 'no_person') else None
        available = answer.get('available_at', t)
        self.recent = [r for r in self.recent if t - r[0] < p['hold_max']]
        if answer.get('gate') is None and isinstance(probabilities, dict):
            self.recent.append((t, probabilities))
        confidence = belief[shown] if shown is not None else None
        if confidence is not None and p['confidence'] == 'evidence' and state != 'no_person':
            confidence = min(confidence, max((_probability(r, state) for _, r in self.recent), default=0.0))
        return {'start': t, 'end': t+p['hold_max'], 'available_at': available, 'valid_until': t+p['hold_max'],
                'activities': [activity] if activity else [],
                'confidence': confidence, 'mode': answer.get('mode', 'offline'),
                'shown': state or 'abstain', 'posterior': dict(zip(STATES, belief))}


def smooth(answers, **overrides):
    """Causal filtered timeline. answers: dicts sorted by 't' (session seconds)
    with probabilities (JEV choice distribution) and choice, or a code 'gate',
    and optional 'continues' (v7 continues_current_segment Noul) and
    'available_at'. Returns one prediction per answer, in score_intervals
    shape, plus the posterior."""
    smoother = Smoother(**overrides)
    return [smoother.update(answer) for answer in answers]


def sweep(answers, grid):
    """(parameters, predictions) for every combination in grid ({name: [values]}):
    free, because it only re-filters cached answers."""
    names = sorted(grid)
    for values in itertools.product(*(grid[n] for n in names)):
        chosen = dict(zip(names, values))
        yield chosen, smooth(answers, **chosen)


def answers_from_outputs(outputs):
    """Smoother input from reasoning.output payloads (live or replayed)."""
    result = []
    for output in outputs:
        prediction, judgment = output.get('prediction') or {}, output.get('judgment') or {}
        t = prediction.get('available_at')
        if not isinstance(t, (int, float)):
            continue
        result.append({'t': t, 'available_at': t, 'probabilities': judgment.get('probabilities'),
                       'choice': judgment.get('activity'), 'mode': prediction.get('mode', 'live')})
    return sorted(result, key=lambda a: a['t'])


# --- Facts-bound validity of raw answers (plan §5.2) ------------------------------------
# An answer stays valid while its arm's facts key equals the key of the packet
# it answered, up to hold_max after arrival. It ends earlier when the key
# changes, a newer answer (or a local abstention) for that arm arrives, or the
# occupancy gate reads vacant. Recorded beside the strict frame-TTL validity
# (prediction.valid_until), never instead of it.
HOLD_MAX_S = DEFAULTS['hold_max']
# Registered parameter sets (overrides of DEFAULTS), selected by the private
# JEV config key 'smoothing'. hold_max always comes from the live scheduler.
SMOOTHING_DEFAULT = 'jev-smoothing-1'
# jev-smoothing-2 (2026-09-30 offline sweep on 0dc34636): the best trade-off
# found for the facts-key cadence, with round values: faster-leaking belief,
# untempered emissions, insufficient_evidence read as "cannot tell" and the
# shown confidence capped by the strongest recent answer. It cuts flicker by
# about 60% at equal exact match and false-confident seconds, but it does
# NOT meet the plan's §7.4 smoothing bar (coverage fell 0.03-0.07, first
# detection lags about 4 s more) and it is sensitive to stay (0.95 instead of
# 0.9 moves coverage +0.19 and exact match -0.12). Not a recommended default.
PARAMETER_SETS = {'jev-smoothing-1': {},
                  'jev-smoothing-2': {'stay': 0.9, 'alpha': 1.0, 'insufficient': 'spread', 'switch': 0.6, 'abstain': 0.5,
                                      'margin': 0.1, 'confidence': 'evidence'}}
VALIDITY_RULE = 'facts_bound'
VALIDITY_REASONS = ('facts_changed', 'facts_changed_before_arrival', 'superseded', 'local_abstain', 'occupancy_vacant',
                    'hold_max')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class Validity:
    """Facts-bound validity of one arm's latest answer, on one clock (session
    seconds). observe() once per scheduler tick; deliver() when an answer
    arrives; local_abstain() when a code gate answers locally. Each returns the
    closures it caused: {'request_id', 'valid_through', 'reason'}."""

    def __init__(self, hold_max=HOLD_MAX_S):
        if not _finite(hold_max) or not 0 < hold_max <= 60:
            raise ValueError('bounded hold required')
        self.hold_max = float(hold_max)
        self.open = None               # {'request_id', 'key', 'available_at'}
        self.key = self.vacant = None  # the latest observed facts key and occupancy

    def _close(self, at, reason):
        closed, self.open = self.open, None
        cap = closed['available_at'] + self.hold_max
        if at >= cap:
            at, reason = cap, 'hold_max'
        return [{'request_id': closed['request_id'], 'valid_through': max(at, closed['available_at']), 'reason': reason}]

    def _expire(self, t):
        if self.open is not None and t >= self.open['available_at'] + self.hold_max:
            return self._close(t, 'hold_max')
        return []

    def observe(self, t, key=None, vacant=False):
        self.key, self.vacant = (key if key is not None else self.key), bool(vacant)
        closed = self._expire(t)
        if self.open is None:
            return closed
        if vacant:
            return closed + self._close(t, 'occupancy_vacant')
        if key is not None and key != self.open['key']:
            return closed + self._close(t, 'facts_changed')
        return closed

    def deliver(self, t, request_id, key):
        """A new answer at t for the packet whose facts key was `key` (None: not
        facts-bound; it keeps only its strict validity)."""
        closed = self._expire(t)
        if self.open is not None:
            closed += self._close(t, 'superseded')
        if key is None:
            return closed
        self.open = {'request_id': request_id, 'key': key, 'available_at': t}
        if self.vacant:
            closed += self._close(t, 'occupancy_vacant')
        elif self.key is not None and self.key != key:
            # The facts moved on while the request was in flight.
            closed += self._close(t, 'facts_changed_before_arrival')
        return closed

    def local_abstain(self, t, vacant=False):
        self.vacant = bool(vacant) or self.vacant
        closed = self._expire(t)
        if self.open is not None:
            closed += self._close(t, 'occupancy_vacant' if vacant else 'local_abstain')
        return closed


def facts_bound(predictions, closers=None, hold_max=HOLD_MAX_S):
    """The facts-bound (valid_facts) view of one run's predictions, in arrival
    order: each prediction that carries valid_through is extended to
    min(valid_through, its recorded closure, the next prediction's arrival,
    arrival + hold_max), and never below its own strict valid_until.
    predictions: [(request_id, prediction)]; closers: {request_id: valid_through}.
    Predictions without valid_through (older outputs) are returned unchanged."""
    closers = closers or {}
    items = [(rid, p) for rid, p in predictions if isinstance(p, dict)]
    arrivals = [p.get('available_at') for _, p in items]
    result = []
    for index, (rid, p) in enumerate(items):
        through = p.get('valid_through')
        if not all(_finite(p.get(k)) for k in ('start', 'end', 'available_at', 'valid_until')) or not _finite(through):
            result.append(p)
            continue
        ends = [through, p['available_at'] + hold_max]
        if _finite(closers.get(rid)):
            ends.append(closers[rid])
        later = [a for a in arrivals[index+1:] if _finite(a) and a >= p['available_at']]
        if later:
            ends.append(min(later))
        effective = max(min(ends), p['valid_until'])
        result.append({**p, 'end': max(p['end'], effective), 'valid_until': effective})
    return result
