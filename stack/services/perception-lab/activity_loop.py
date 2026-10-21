"""Always-on, occupancy-gated activity loop for the den lighting brain (AL-3).

The loop wakes the deep stack while the den is occupied and pauses it about
2 min after the last occupied reading. It publishes the four contract-v1 event
types (activity, person_pose, health, cost) to the lighting brain and nothing
else: it never commands a light. There is no 300 s run cap in this mode. Stop
stays available and holds until resume(); resource limits and back-off are the
deep stack's and the publisher's own.

The deep stack is an adapter (activity_loop_runtime.ManagedDeepStack in the
controller; fakes in tests) with start(), stop(reason), tick() and observe().
observe() returns {'running', 'ended_externally', 'activity', 'person_count',
'pose', 'health', 'usage'}; see ManagedDeepStack.observe for the field shapes.
"""
import json
import math
import threading
import time

import activity_events
import activity_pose

SCHEMA = 'perception-lab-activity-loop-1'
SOURCE = 'perception-lab/activity-loop'
STOP_REASONS = ('operator_stop', 'monitor_backoff')
# Cadence levels for the AL-5 monitor's step-1 back-off: reduced doubles the JEV
# interval and halves the pose rate. Recovery returns to normal.
CADENCE = {'normal': 1, 'reduced': 2}


class ResumeRefused(ValueError):
    """A conditional resume that does not match why the loop stopped."""
COMPONENTS = ('jev', 'deep_stack', 'camera')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ActivityLoop:
    def __init__(self, *, deep, occupancy, publisher, home='victoria', room='den', source=SOURCE, clock=time.time,
                 vacancy_pause_s=120., pose_interval_s=1., health_heartbeat_s=60., cost_interval_s=60.,
                 jev_stale_s=30., pricing=None, zones=None, position_interval_s=.2, position_stale_s=3.,
                 person_position=True):
        if not callable(occupancy) or not all(callable(getattr(deep, k, None)) for k in ('start', 'stop', 'tick', 'observe')):
            raise ValueError('occupancy reading and deep-stack adapter required')
        if not (30 <= vacancy_pause_s <= 900 and 1 <= pose_interval_s <= 2 and 10 <= health_heartbeat_s <= 120
                and 10 <= cost_interval_s <= 300 and 10 <= jev_stale_s <= 120):
            raise ValueError('bounded activity-loop timings required')
        self.deep, self.occupancy, self.publisher = deep, occupancy, publisher
        self.scope = dict(home=home, room=room, source=source)
        self.clock = clock
        self.vacancy_pause_s, self.pose_interval_s = float(vacancy_pause_s), float(pose_interval_s)
        self.base_pose_interval_s, self.cadence = self.pose_interval_s, 'normal'
        # v1.1 person_position: each new placement, at most 1/position_interval_s
        # (5 Hz). It never waits on JEV; the placement rate is the real limit.
        if not (.2 <= position_interval_s <= 1. and 1. <= position_stale_s <= 30.):
            raise ValueError('bounded person_position timings required')
        self.position_interval_s = self.base_position_interval_s = float(position_interval_s)
        self.position_stale_s, self.person_position = float(position_stale_s), bool(person_position)
        self.last_position_key = self.last_position_at = None
        self.position_health = (False, 'position_not_started')
        self.health_heartbeat_s, self.cost_interval_s, self.jev_stale_s = health_heartbeat_s, cost_interval_s, jev_stale_s
        self.pricing = dict(pricing or {})
        self.zones = activity_pose.check_zones(zones, home=home, room=room)
        self.lock = threading.RLock()
        self.running = False
        self.stopped = None
        self.started_at = self.last_occupied = None
        self.last_activity_id = self.last_activity_at = None
        self.last_pose_key = self.last_pose_at = None
        self.health_sent = {}
        self.cost_at = None
        self.usage_base = None
        self.counts = dict(ticks=0, starts=0, pauses=0, activity=0, person_pose=0, person_position=0, health=0, cost=0,
                           errors=0)
        self.thread = None
        self.halt = threading.Event()

    # --- control -----------------------------------------------------------
    def stop(self, reason='operator_stop'):
        """Operator Stop: end the deep stack now and hold until resume()."""
        with self.lock:
            reason = reason if isinstance(reason, str) and reason else 'operator_stop'
            if not (self.stopped == 'operator_stop' and reason == 'monitor_backoff'):
                self.stopped = reason
            if reason == 'monitor_backoff':
                backoff = getattr(self.deep, 'set_monitor_backoff', None)
                if backoff is not None:
                    backoff(True)  # also while vacant: no recording owns Qwen
            if self.running:
                self.deep.stop(self.stopped)
                self.running = False
        return self.snapshot()

    def resume(self, only_reason=None):
        """Clear a Stop. With only_reason (the monitor's 'monitor_backoff'), only
        a Stop for that reason is cleared: the owner's operator_stop stays."""
        with self.lock:
            if only_reason is not None and self.stopped is not None and self.stopped != only_reason:
                raise ResumeRefused('activity_loop_stopped_by_' + self.stopped)
            backoff = getattr(self.deep, 'set_monitor_backoff', None)
            if backoff is not None:
                backoff(False)
            self.stopped = None
        return self.snapshot()

    def set_cadence(self, level):
        """'normal' or 'reduced' (JEV interval x2, pose rate halved)."""
        if level not in CADENCE:
            raise ValueError('activity-loop cadence must be normal or reduced')
        with self.lock:
            factor = CADENCE[level]
            self.cadence, self.pose_interval_s = level, self.base_pose_interval_s * factor
            self.position_interval_s = min(1., self.base_position_interval_s * factor)
            scale = getattr(self.deep, 'set_cadence', None)
            if scale is not None:
                scale(factor)
        return self.cadence_status()

    def cadence_status(self):
        with self.lock:
            jev = getattr(self.deep, 'cadence_status', None)
            result = {'level': self.cadence, 'factor': CADENCE[self.cadence], 'pose_interval_s': self.pose_interval_s,
                      'jev': jev() if jev is not None else None}
            qwen = getattr(self.deep, 'qwen_control_status', None)
            if qwen is not None and (control := qwen()) is not None:
                result['qwen'] = control
            return result

    # --- one step ----------------------------------------------------------
    def _reading(self):
        try:
            value = self.occupancy()
        except Exception:
            return 'unknown'
        return value if value in ('on', 'off', 'unknown') else 'unknown'

    def tick(self):
        with self.lock:
            now = self.clock()
            self.counts['ticks'] += 1
            reading = self._reading()
            if reading == 'on':
                self.last_occupied = now
            want = (self.stopped is None and self.last_occupied is not None
                    and now - self.last_occupied < self.vacancy_pause_s)
            if want and not self.running:
                self.deep.start()
                self.running, self.started_at = True, now
                self.counts['starts'] += 1
            elif not want and self.running:
                self.deep.stop('activity_loop_room_empty')
                self.running = False
                self.counts['pauses'] += 1
            maintain = getattr(self.deep, 'maintain', None)
            if maintain is not None:
                maintain()         # the bounded JEV egress release, even while paused
            if self.running:
                self.deep.tick()
            seen = self.deep.observe() or {}
            if self.running and seen.get('ended_externally'):
                # A Stop of the loop's own recording from outside is an operator Stop.
                self.stopped, self.running = 'operator_stop', False
            running = self.running and seen.get('running', True)
            self._activity(seen if running else {}, now)
            self._pose(seen if running else {}, now)
            self._position(seen if running else {}, now, running)
            self._health(seen, now, running)
            self._cost(seen, now)
        self.publisher.flush()
        return self.snapshot()

    def _emit(self, event):
        self.publisher.submit(event)
        self.counts[event['type']] += 1

    def _activity(self, seen, now):
        answer = seen.get('activity')
        if not isinstance(answer, dict) or answer.get('event_id') in (None, self.last_activity_id):
            return
        self.last_activity_id, self.last_activity_at = answer['event_id'], now
        if answer.get('label') not in activity_events.LABELS:
            return
        person = ((seen.get('pose') or {}).get('people') or [None])[0]
        bound = {k: answer[k] for k in ('binding', 'valid_until') if k in answer}
        self._emit(activity_events.activity(**self.scope, observed_at=answer['observed_at'], label=answer['label'],
            confidence=answer.get('confidence'), person_count=seen.get('person_count'),
            model=answer.get('model') or 'jev-1.13.0', facts_key=answer.get('facts_key') or 'request:%s' % answer['event_id'],
            zone_hint=activity_pose.zone_hint(person, self.zones), **bound))

    def _pose(self, seen, now):
        pose = seen.get('pose')
        if not isinstance(pose, dict) or pose.get('key') in (None, self.last_pose_key):
            return
        if self.last_pose_at is not None and now - self.last_pose_at < self.pose_interval_s:
            return
        self.last_pose_key, self.last_pose_at = pose['key'], now
        people = list(pose.get('people') or [])
        count = seen.get('person_count')
        bound = {k: pose[k] for k in ('binding', 'valid_until') if k in pose}
        self._emit(activity_events.person_pose(**self.scope, observed_at=pose['observed_at'], people=people,
            person_count=count if type(count) is int else len(people), **bound))

    def _position(self, seen, now, running):
        position = seen.get('position') if self.person_position else None
        if position and position.get('key') != self.last_position_key and (
                self.last_position_at is None or now - self.last_position_at >= self.position_interval_s - 1e-3):
            count = seen.get('person_count')
            people = list(position['people'])
            self._emit(activity_events.person_position(**self.scope, observed_at=position['observed_at'],
                people=people, person_count=count if type(count) is int and count >= len(people) else len(people)))
            self.last_position_key, self.last_position_at = position.get('key'), now
        if not self.person_position:
            self.position_health = (False, 'position_disabled')
        elif not running:
            self.position_health = (False, (self.stopped and 'stopped_' + self.stopped) or 'paused_room_empty')
        elif self.last_position_at is not None and now - self.last_position_at <= self.position_stale_s:
            self.position_health = (True, None)
        else:
            self.position_health = (False, 'position_unavailable:' + str(seen.get('position_reason') or 'no_placement'))

    def _health(self, seen, now, running):
        reported = seen.get('health') or {}
        for component in COMPONENTS:
            if running:
                ok, detail = reported.get(component, (False, 'not_reported'))
                if (component == 'jev' and ok and self.started_at is not None
                        and now - (self.last_activity_at or self.started_at) > self.jev_stale_s):
                    ok, detail = False, 'jev_stale'
            elif component == 'camera' and component in reported:
                ok, detail = reported[component]
            else:
                ok, detail = False, (self.stopped and 'stopped_' + self.stopped) or 'paused_room_empty'
            previous = self.health_sent.get(component)
            if previous is None or previous[:2] != (ok, detail) or now - previous[2] >= self.health_heartbeat_s:
                self.health_sent[component] = (ok, detail, now)
                self._emit(activity_events.health(**self.scope, observed_at=now, component=component,
                                                  ok=bool(ok), detail=detail))

    def _usd(self, calls, tokens_in, tokens_out):
        per_call = self.pricing.get('usd_per_call')
        per_in, per_out = self.pricing.get('usd_per_mtok_input'), self.pricing.get('usd_per_mtok_output')
        if _finite(per_in) and _finite(per_out) and tokens_in is not None and tokens_out is not None:
            return round((tokens_in * per_in + tokens_out * per_out) / 1e6, 6)
        if _finite(per_call):
            return round(calls * per_call, 6)
        return None

    def _cost(self, seen, now):
        usage = seen.get('usage') or {}
        current = (int(usage.get('calls') or 0), usage.get('input_tokens'), usage.get('output_tokens'))
        if self.cost_at is None:
            self.cost_at, self.usage_base = now, current
            return
        if now - self.cost_at < self.cost_interval_s:
            return
        base = self.usage_base or (0, None, None)
        # A deep-stack roll restarts its counters: a lower count is a new run.
        calls = current[0] - base[0] if current[0] >= base[0] else current[0]
        delta = lambda i: (current[i] - base[i] if None not in (current[i], base[i]) and current[i] >= base[i]
                           else current[i])
        self.cost_at, self.usage_base = now, current
        self._emit(activity_events.cost(**self.scope, observed_at=now, calls=calls,
                                        est_usd=self._usd(calls, delta(1), delta(2))))

    # --- thread ------------------------------------------------------------
    def run_forever(self, interval_s=0.5):
        while not self.halt.wait(interval_s):
            try:
                self.tick()
            except Exception:
                with self.lock:
                    self.counts['errors'] += 1

    def start_thread(self, interval_s=0.5):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run_forever, args=(interval_s,), name='activity-loop', daemon=True)
            self.thread.start()

    def shutdown(self, timeout_s=5.):
        self.halt.set()
        if self.thread is not None:
            self.thread.join(timeout_s)
        try:
            self.set_cadence('normal')  # hand the runtime back at its configured cadence
        except Exception:
            pass
        with self.lock:
            if self.running:
                self.deep.stop('activity_loop_shutdown')
                self.running = False
        self.publisher.flush()

    def snapshot(self):
        with self.lock:
            now = self.clock()
            return dict(schema=SCHEMA, monitor_control_version=2,
                state='stopped' if self.stopped else 'running' if self.running else 'paused',
                stopped_reason=self.stopped, cadence=self.cadence,
                position=dict(enabled=self.person_position, ok=self.position_health[0], detail=self.position_health[1],
                              interval_s=self.position_interval_s, last_published_age_s=None if self.last_position_at is None
                              else now - self.last_position_at), running_for_s=None if not self.running else now - self.started_at,
                last_occupied_age_s=None if self.last_occupied is None else now - self.last_occupied,
                vacancy_pause_s=self.vacancy_pause_s, counts=dict(self.counts),
                health={k: {'ok': v[0], 'detail': v[1]} for k, v in self.health_sent.items()},
                publisher=self.publisher.snapshot(), commands_lights=False)


def read_config(path, *, read_private):
    """The private activity-loop config; read_private returns the file's bytes."""
    def unique(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate activity-loop config key')
            out[key] = value
        return out
    return check_config(json.loads(read_private(path), object_pairs_hook=unique))


def check_config(value):
    keys = {'schema', 'enabled', 'home', 'room', 'camera_id', 'experiment_id', 'brain', 'timings', 'pricing', 'zones'}
    if (not isinstance(value, dict) or not keys <= set(value) <= keys | {'jev_release', 'vjepa', 'person_position'} or value['schema'] != SCHEMA
            or type(value['enabled']) is not bool):
        raise ValueError('exact activity-loop config required')
    if type(value.get('person_position', True)) is not bool:
        raise ValueError('activity-loop person_position must be true or false')
    if type(value.get('vjepa', False)) is not bool:
        raise ValueError('activity-loop vjepa must be true or false (off for the MVP)')
    release = value.get('jev_release')
    # The bounded JEV egress release is on unless this says enabled:false.
    if release is not None and (not isinstance(release, dict) or not set(release) <= {'enabled', 'abandon_after_s', 'backoff_max_s'}
            or type(release.get('enabled', True)) is not bool
            or not all(_finite(release[k]) for k in ('abandon_after_s', 'backoff_max_s') if k in release)
            or not 30 <= release.get('abandon_after_s', 120) <= 3600
            or not release.get('abandon_after_s', 120) <= release.get('backoff_max_s', 1920) <= 86400):
        raise ValueError('bounded JEV release settings required')
    if value['experiment_id'] != 'apartment-viewing' or value['camera_id'] != 'den':
        raise ValueError('the activity loop runs the den apartment-viewing recording')
    brain = value['brain']
    if not isinstance(brain, dict) or set(brain) != {'origin', 'token_file', 'ca_file'}:
        raise ValueError('exact lighting-brain binding required')
    import brain_publisher
    brain_publisher.origin_parts(brain['origin'])
    timings = value['timings']
    allowed = {'vacancy_pause_s', 'pose_interval_s', 'health_heartbeat_s', 'cost_interval_s', 'jev_stale_s', 'roll_margin_s',
               'position_interval_s', 'position_stale_s'}
    if not isinstance(timings, dict) or not set(timings) <= allowed or not all(_finite(v) for v in timings.values()):
        raise ValueError('bounded activity-loop timings required')
    pricing = value['pricing']
    if (not isinstance(pricing, dict) or not set(pricing) <= {'usd_per_call', 'usd_per_mtok_input', 'usd_per_mtok_output'}
            or not all(v is None or (_finite(v) and v >= 0) for v in pricing.values())):
        raise ValueError('activity-loop pricing must be non-negative numbers or null')
    zones = value['zones']
    if isinstance(zones, str):
        # The path of the private lighting-brain-geometry-1 file, read at build.
        if not (zones.startswith('/') or (len(zones) > 2 and zones[1] == ':')):
            raise ValueError('the activity-loop zones file must be an absolute path')
    else:
        activity_pose.check_zones(zones, home=value['home'], room=value['room'])
    return value
