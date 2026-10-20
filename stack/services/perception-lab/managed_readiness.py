"""Launch-free, cached setup readiness for the managed Lab catalog.

'ready' means only that a recipe has a provisioned managed binding and both hosts
passed a cheap read-only probe. Start still runs every preflight gate and may be
refused. Nothing here starts, stops or reserves a worker or reads model state,
and a probe result never outlives its bounded cache age.
"""
from copy import deepcopy
import re
import threading
import time

from managed_pair import ManagedPair

# Recipe requirements the managed runtime cannot yet prove, as prerequisite codes.
# The provisioned capture source already satisfies 'source'.
PREREQUISITES = {'source': None, 'network': 'network', 'model_artifacts': 'model_artifacts',
                 'resource_admission': 'resource_admission', 'depth': 'depth', 'calibration': 'calibration',
                 'room_registration': 'room_registration', 'furniture_map': 'furniture_map',
                 'high_resolution_source': 'high_resolution_source', 'source_rate_4hz': 'source_rate_4hz',
                 'saved_session': 'saved_session', 'qwen_admission': 'qwen_admission',
                 'reviewed_preferences': 'reviewed_preferences', 'jev_household_egress': 'jev_household_egress',
                 'vjepa_worker': 'vjepa_worker'}
CODE = re.compile(r'[a-z][a-z0-9_]{0,63}')


def prerequisites(recipe):
    """Explicit codes for a recipe without a managed binding; unknown stays visible."""
    codes = ['worker_binding']
    for requirement in recipe.get('requirements', []):
        code = PREREQUISITES.get(requirement, requirement if isinstance(requirement, str) and CODE.fullmatch(requirement)
                                 else 'unrecognized_requirement')
        if code and code not in codes:
            codes.append(code)
    return codes


class ManagedReadiness:
    def __init__(self, clients, enabled, *, max_age=5, clock=time.monotonic, wall=time.time):
        if set(clients) != {'a', 'b'}:
            raise ValueError('Exactly two provisioned lifecycle clients required')
        self.clients, self.enabled = clients, frozenset(enabled)
        self.max_age, self.clock, self.wall = max_age, clock, wall
        self.lock = threading.Lock()
        self.cached = None

    def probe(self):
        """Per-arm reasons from each client's read-only probe, cached for max_age seconds."""
        with self.lock:
            if self.cached is not None and 0 <= self.clock() - self.cached[0] < self.max_age:
                return deepcopy(self.cached[1]), self.cached[2]
            reasons = []
            for arm in ('a', 'b'):
                probe = getattr(self.clients[arm], 'probe', None)
                if not callable(probe):
                    reasons.append({'code': 'readiness_probe_unavailable', 'arm': arm})
                    continue
                try:
                    found = probe()
                    if not isinstance(found, list) or not all(
                            isinstance(r, dict) and isinstance(r.get('code'), str) and CODE.fullmatch(r['code']) for r in found):
                        raise ValueError('invalid probe result')
                except Exception:
                    found = [{'code': 'readiness_probe_failed'}]
                reasons.extend({**deepcopy(r), 'arm': arm} for r in found)
            # Monotonic age bounds reuse; wall time is only shown to the viewer.
            self.cached = (self.clock(), reasons, self.wall())
            return deepcopy(reasons), self.cached[2]

    def annotate(self, experiments, *, busy=None):
        """Catalog rows with readiness. busy names an active pair phase; no probe then."""
        bound = any(e.get('id') in self.enabled for e in experiments)
        if busy:
            reasons, checked = [{'code': busy}], None
        else:
            reasons, checked = self.probe() if bound else ([], None)
        result = []
        for recipe in experiments:
            if recipe.get('id') in self.enabled:
                state = 'blocked' if reasons else 'ready'
                readiness = {'state': state, 'reasons': deepcopy(reasons), 'prerequisites': [], 'prepares_on_start': True,
                             'checked_at': checked, 'max_age_seconds': self.max_age}
                # Compatible fields: an older Lab UI disables Start on 'blocked'.
                result.append({**recipe, 'status': state, 'readiness': readiness,
                               'readiness_reason': 'prepares_on_start' if state == 'ready' else reasons[0]['code']})
            elif recipe.get('readiness_reason') == 'saved_analysis_on_request':
                # ManagedPair offers it on saved sessions only: keep its fields; never live-startable.
                result.append({**recipe, 'readiness': {'state': 'saved_session', 'reasons': [], 'prerequisites': [],
                                                       'prepares_on_start': False, 'checked_at': None, 'max_age_seconds': None}})
            else:
                result.append({**recipe, 'status': 'blocked', 'readiness_reason': 'experiment_pair_not_provisioned',
                               'readiness': {'state': 'coming_later', 'reasons': [{'code': 'experiment_pair_not_provisioned'}],
                                             'prerequisites': prerequisites(recipe), 'prepares_on_start': False,
                                             'checked_at': None, 'max_age_seconds': None}})
        return result


class ReadinessPair(ManagedPair):
    """ManagedPair whose advertised catalog carries launch-free setup readiness.

    Admission is unchanged: start_session still uses the static provisioned
    catalog and the lifecycle preflight, never a cached probe result."""

    def __init__(self, *args, setup_readiness, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_readiness = setup_readiness

    def capabilities(self):
        value = super().capabilities()
        with self.lock:
            phase = self.current['phase'] if self.current is not None else None
            busy = 'service_closing' if self.closed else None if phase is None else {
                'indeterminate': 'worker_cleanup_pending', 'draining': 'local_drain_pending'}.get(phase, 'comparison_active')
        # Probe outside the pair lock: a slow host read never blocks preparation,
        # cleanup or live reads. Controller.start may wait for one bounded probe.
        value['experiments'] = self.setup_readiness.annotate(value['experiments'], busy=busy)
        return value
