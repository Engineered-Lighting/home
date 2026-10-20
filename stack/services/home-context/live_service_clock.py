"""Externally qualified, monotonic-anchored service UTC with latched refusal.

The policy asserts an independent UTC qualification of the host wall clock.
Comparing two local clocks detects later discontinuities, not an unknown
constant UTC offset. No constructor or sample manufactures that qualification.
"""
from copy import deepcopy
import math
import re
import threading
import time


_KEYS = {'schema', 'version', 'qualification_ref', 'valid_until', 'uncertainty_s',
         'max_anchor_divergence_s', 'max_round_trip_s', 'max_mapping_age_s'}
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}')
_SHA = re.compile(r'[a-f0-9]{64}')


class ServiceClockUnqualified(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class QualifiedServiceClock:
    """One immutable qualification/anchor epoch, shared by the same journal.

    Step, backward monotonic time, excessive local read delay and qualification
    expiry latch this instance unavailable. Recovery needs a new explicitly
    qualified instance/epoch. A sample's short mapping lease does not renew the
    external qualification or replace the original divergence anchor.
    """
    def __init__(self, policy, *, anchor_epoch, wall=time.time, monotonic=time.monotonic):
        ref = policy.get('qualification_ref') if isinstance(policy, dict) else None
        if (not isinstance(policy, dict) or set(policy) != _KEYS
                or policy.get('schema') != 'home-live-service-clock-policy-1'
                or type(policy.get('version')) is not int or policy['version'] != 1
                or not isinstance(ref, dict) or set(ref) != {'id', 'digest'}
                or not isinstance(ref['id'], str) or not _ID.fullmatch(ref['id'])
                or not isinstance(ref['digest'], str) or not _SHA.fullmatch(ref['digest'])
                or not isinstance(anchor_epoch, str) or not _ID.fullmatch(anchor_epoch)
                or not all(callable(port) for port in (wall, monotonic))
                or not all(_finite(policy.get(key)) for key in _KEYS - {'schema', 'version', 'qualification_ref'})
                or not 0 <= policy['uncertainty_s'] <= 1
                or not 0 < policy['max_anchor_divergence_s'] <= 1
                or not 0 < policy['max_round_trip_s'] <= 1
                or not 0 < policy['max_mapping_age_s'] <= 5
                or policy['valid_until'] <= 0):
            raise ValueError('explicit bounded external service clock qualification required')
        self.policy, self.anchor_epoch = deepcopy(policy), anchor_epoch
        self.wall, self.monotonic = wall, monotonic
        self.lock = threading.RLock()
        self.reason = None
        utc, mono, half_read = self._read()
        if utc + policy['uncertainty_s'] + half_read >= policy['valid_until']:
            raise ServiceClockUnqualified('service_clock_qualification_expired')
        self.anchor_utc, self.anchor_mono, self.last_mono = utc, mono, mono

    def _read(self):
        before = self.monotonic()
        wall = self.wall()
        after = self.monotonic()
        if not all(_finite(value) for value in (before, wall, after)):
            raise ServiceClockUnqualified('service_clock_unavailable')
        if after < before:
            raise ServiceClockUnqualified('service_clock_monotonic_regressed')
        duration = after - before
        if duration > self.policy['max_round_trip_s']:
            raise ServiceClockUnqualified('service_clock_read_deadline_exceeded')
        return wall, before + duration / 2, duration / 2

    def _unqualified(self, reason):
        self.reason = reason
        return dict(qualified=False, reason=reason, utc_at=None, uncertainty_s=None,
                    anchor_epoch=self.anchor_epoch, qualification_ref=deepcopy(self.policy['qualification_ref']))

    def sample(self):
        with self.lock:
            if self.reason is not None:
                return self._unqualified(self.reason)
            try:
                wall, mono, half_read = self._read()
                if mono < self.last_mono:
                    return self._unqualified('service_clock_monotonic_regressed')
                utc = self.anchor_utc + (mono - self.anchor_mono)
                divergence = abs(wall - utc)
                if divergence > self.policy['max_anchor_divergence_s']:
                    return self._unqualified('service_clock_anchor_diverged')
                # Divergence inside the admitted band still consumes clock
                # uncertainty; a tolerance must never become free extra lease.
                uncertainty = self.policy['uncertainty_s'] + divergence + half_read
                if max(utc, wall) + uncertainty >= self.policy['valid_until']:
                    return self._unqualified('service_clock_qualification_expired')
                self.last_mono = mono
                return dict(qualified=True, reason='qualified', utc_at=utc,
                    uncertainty_s=uncertainty, monotonic_at=mono, sampled_wall_at=wall,
                    anchor_epoch=self.anchor_epoch, qualification_ref=deepcopy(self.policy['qualification_ref']),
                    valid_until=min(self.policy['valid_until'], utc + self.policy['max_mapping_age_s']),
                    max_mapping_age_s=self.policy['max_mapping_age_s'])
            except ServiceClockUnqualified as error:
                return self._unqualified(error.reason)
            except Exception:
                return self._unqualified('service_clock_unavailable')

    def __call__(self):
        sample = self.sample()
        if not sample['qualified']:
            raise ServiceClockUnqualified(sample['reason'])
        return sample['utc_at']
