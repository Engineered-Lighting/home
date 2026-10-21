"""The activity loop's deep stack inside the managed controller (AL-3).

ManagedDeepStack keeps one den apartment-viewing recording with live JEV
reasoning open while the loop wants it. Every existing per-recording limit
stays in force: the recording, its reasoning grant and its V-JEPA/LA leases are
bounded by the commissioned session budget (300 s), so the stack rolls to a
fresh recording before that limit. Nobody starts anything and the run has no
overall cap. A Stop of the loop's recording from outside is an operator Stop.

It only reads the ledger and the RAM viewing source: arm-B JEV answers, the
arm-B model output (person count, 2D head measures) and the approximate
room-frame placement. install() mounts the loop on the managed app and adds
GET/POST /activity-loop status, stop and resume routes.
"""
import json
import threading
import time
import uuid

import activity_pose
from activity_reading import reader_key

TERMINAL = ('stopped', 'failed', 'interrupted', 'blocked', 'indeterminate')
OPEN = ('requested', 'starting', 'recording', 'stopping')
REQUEST_PREFIX = 'activity-loop-'
RELEASE_REASON = 'activity_loop_auto_release_indeterminate'


class ActivityMediaCleanup:
    """Opt-in deletion of this commissioned loop's media; structured rows survive.

    The exact dedicated root and commissioning cutoff are explicit. Deletion
    goes through Controller so pending local/remote work retains its existing
    refusal guards. This is not permission to clean unrelated Lab recordings.
    """
    @staticmethod
    def check_config(config):
        from pathlib import Path
        from occupancy_gate import _finite
        if (not isinstance(config, dict) or set(config) != {'root', 'created_after', 'age_s'}
                or not isinstance(config['root'], str) or not Path(config['root']).is_absolute()
                or not _finite(config['created_after']) or config['created_after'] <= 0
                or not _finite(config['age_s']) or not 300 <= config['age_s'] <= 86400):
            raise ValueError('exact root, commissioning cutoff and 5-minute to 24-hour media age required')
        return config

    def __init__(self, controller, config):
        from pathlib import Path
        self.config = dict(self.check_config(config))
        self.controller, self.store = controller, controller.store
        root = Path(config['root'])
        if root.is_symlink() or root.resolve() != self.store.root:
            raise ValueError('cleanup must name the exact commissioned Store root')
        self.next_at, self.deleted, self.error, self.checked_at = 0., 0, None, None

    def tick(self):
        now = self.store.clock()
        if now < self.next_at:
            return
        self.next_at = now + 30.
        try:
            with self.store.lock:
                rows = self.store.db.execute(
                    "SELECT s.id,s.revision,s.state FROM sessions s WHERE s.request_id GLOB 'activity-loop-*' "
                    "AND s.camera_id='den' AND s.experiment_id='apartment-viewing' AND s.fixture=0 "
                    "AND s.created_at>=? AND s.created_at<=? "
                    "AND s.state!='deleted' "
                    "AND NOT EXISTS (SELECT 1 FROM media_deletions d WHERE d.session_id=s.id AND d.state='completed') "
                    "ORDER BY s.created_at LIMIT 16",
                    (self.config['created_after'], now - self.config['age_s'])).fetchall()
            for sid, revision, state in rows:
                if state not in ('stopped', 'failed', 'interrupted', 'blocked'):
                    raise ValueError('expired automatic recording still requires execution reconciliation')
                self.controller.delete(sid, revision)
                self.store.append_event(sid, 'activity.media_retention',
                    {'policy': 'commissioned_activity_loop', 'age_s': self.config['age_s'],
                     'media_deleted': True, 'structured_records_preserved': True})
                self.deleted += 1
            self.error = None
        except Exception as error:
            self.error = type(error).__name__  # never expose private paths or transport details
        self.checked_at = now

    def snapshot(self):
        return dict(enabled=True, ok=self.error is None, error=self.error,
                    checked_at=self.checked_at, deleted=self.deleted, age_s=self.config['age_s'])


class EgressRelease:
    """Bounded auto-release of the JEV egress freeze, for the always-on loop only.

    jev_policy admits no JEV egress while any reasoning request is
    indeterminate (its completion is unknown), and only a terminal proof
    reconciles one. In the always-on mode the owner chose (2026-10-19) a
    bounded release instead: an indeterminate request of a recording this loop
    opened is abandoned once it has been indeterminate for abandon_after_s
    (120 s). Its row becomes 'abandoned' and a reasoning.abandoned receipt
    (request id, reason, abandoned_at) is written in the same transaction, so
    egress resumes. Each further release with no completed JEV delivery in
    between waits twice as long, up to backoff_max_s. Lab recordings are never
    touched: their indeterminate requests keep the freeze until reconciled.
    """
    def __init__(self, store, *, enabled=True, abandon_after_s=120., backoff_max_s=1920., clock=None):
        if not (30 <= abandon_after_s <= 3600 and abandon_after_s <= backoff_max_s <= 86400):
            raise ValueError('bounded JEV release timings required')
        self.store, self.enabled = store, bool(enabled)
        self.abandon_after_s, self.backoff_max_s = float(abandon_after_s), float(backoff_max_s)
        self.clock = clock or store.clock
        self.streak = 0            # releases since the last completed JEV delivery
        self.mark = None           # event id of the latest release receipt
        self.first_seen = {}       # request id -> when this loop first saw it (restart recovery)
        self.released = 0
        self.last_release = None

    def wait_s(self):
        return min(self.backoff_max_s, self.abandon_after_s * 2 ** self.streak)

    def _query(self, sql, args=()):
        with self.store.lock:
            try:
                return self.store.db.execute(sql, args).fetchall()
            except Exception:      # no reasoning table until reasoning is provisioned
                return []

    def blocking(self):
        """The indeterminate requests that hold all JEV egress: [(id, session_id, ours)]."""
        return [(r[0], r[1], bool(r[2])) for r in self._query(
            "SELECT r.id, r.session_id, s.request_id LIKE ? FROM lab_reasoning_requests r "
            "LEFT JOIN sessions s ON s.id=r.session_id WHERE r.state='indeterminate' ORDER BY r.dispatched_at",
            (REQUEST_PREFIX + '%',))]

    def _since(self, rid, sid, now):
        rows = self._query(
            "SELECT payload FROM events WHERE session_id=? AND kind='reasoning.delivery' "
            "AND json_extract(payload,'$.request_id')=? AND json_extract(payload,'$.state')='indeterminate' "
            "ORDER BY id LIMIT 1", (sid, rid))
        at = json.loads(rows[0][0]).get('available_at') if rows else None
        if type(at) in (int, float):
            return float(at)
        # Recovered at a controller restart, with no delivery: count from now.
        return self.first_seen.setdefault(rid, now)

    def tick(self):
        """Release what is due; returns the receipts written this tick."""
        if not self.enabled:
            return []
        now = self.clock()
        if self.streak and self._query(
                "SELECT 1 FROM events WHERE id>? AND kind='reasoning.delivery' "
                "AND json_extract(payload,'$.state')='completed' LIMIT 1", (self.mark or 0,)):
            self.streak = 0        # JEV answered again: the back-off starts over
        blocking = self.blocking()
        live = {rid for rid, _, _ in blocking}
        self.first_seen = {rid: at for rid, at in self.first_seen.items() if rid in live}
        written = []
        for rid, sid, ours in blocking:
            if not ours:
                continue
            since, wait = self._since(rid, sid, now), self.wait_s()
            if now - since < wait:
                continue
            receipt = {'request_id': rid, 'session_id': sid, 'reason': RELEASE_REASON, 'abandoned_at': now,
                       'indeterminate_since': since, 'waited_s': now - since, 'backoff_s': wait,
                       'release': self.released + 1, 'available_at': now, 'current': False, 'published': False}
            try:
                with self.store.transaction() as db:
                    row = db.execute("SELECT arm,generation FROM lab_reasoning_requests WHERE id=? AND state='indeterminate'",
                                     (rid,)).fetchone()
                    if row is None:
                        continue
                    receipt.update(arm=row[0], generation=row[1])
                    body = json.dumps(receipt, sort_keys=True, separators=(',', ':'))
                    self.store.check_capacity(len(body) + 65536)
                    if db.execute("UPDATE lab_reasoning_requests SET state='abandoned',result=? WHERE id=? AND state='indeterminate'",
                                  (body, rid)).rowcount != 1:
                        continue
                    event_id = self.store._event(db, sid, 'reasoning.abandoned', receipt)
            except Exception:
                continue           # retried next tick; the freeze stays meanwhile
            self.released += 1
            self.streak += 1
            self.mark = event_id
            self.first_seen.pop(rid, None)
            self.last_release = receipt
            written.append(receipt)
        return written

    def snapshot(self):
        blocking = self.blocking()
        return dict(enabled=self.enabled, abandon_after_s=self.abandon_after_s, backoff_max_s=self.backoff_max_s,
                    next_wait_s=self.wait_s(), released=self.released, blocked=bool(blocking),
                    blocked_by_lab=any(not ours for _, _, ours in blocking),
                    last_release=dict(self.last_release) if self.last_release else None)


class ManagedDeepStack:
    def __init__(self, controller, *, camera_id='den', experiment_id='apartment-viewing', roll_margin_s=20.,
                 output_fresh_s=10., retry_s=10., retry_max_s=160., name='Activity loop', clock=None, release=None,
                 media_cleanup=None):
        self.controller, self.store, self.runtime = controller, controller.store, controller.runtime
        self.camera_id, self.experiment_id, self.name = camera_id, experiment_id, name
        self.clock = clock or self.store.clock
        limit = getattr(self.store.settings, 'max_session_seconds', 300)
        if not (5 <= roll_margin_s < limit / 2):
            raise ValueError('roll margin must leave most of each recording')
        self.limit, self.roll_margin_s = float(limit), float(roll_margin_s)
        self.output_fresh_s, self.retry_s = output_fresh_s, retry_s
        if not 1 <= retry_s <= retry_max_s <= 600:
            raise ValueError('bounded recording retry backoff required')
        self.retry_max_s, self.retry_failures = retry_max_s, 0
        self.producer_epoch = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.sid = None
        self.wanted = False
        self.ours = set()          # recordings this stack asked to stop
        self.ended_externally = False
        self.retry_at = None
        self.last_error = None
        self.rolls = 0
        self.usage = dict(calls=0, input_tokens=0, output_tokens=0)
        self.counted = {}          # sid -> last counted reasoning event id
        self.release = release if release is not None else EgressRelease(self.store, clock=self.clock)
        self.media_cleanup = ActivityMediaCleanup(controller, media_cleanup) if media_cleanup is not None else None

    # --- control -----------------------------------------------------------
    def start(self):
        with self.lock:
            if self.media_cleanup is not None and not self.media_cleanup.snapshot()['ok']:
                raise ValueError('activity media cleanup requires recovery')
            self.wanted, self.ended_externally = True, False
            if self.sid is None:
                self._open()

    def stop(self, reason='activity_loop_stop'):
        with self.lock:
            self.wanted = False
            self._close(reason)

    def _inner(self, sid=None):
        """The ServiceRuntime serving the loop's recording. On the managed
        controller, controller.runtime is a ManagedPair that builds one inner
        runtime per recording (managed_pair.py); its own object has no viewing
        sources, reasoning errors or cadence. None until that recording's inner
        runtime exists (while its workers prepare)."""
        runtime = self.runtime
        if runtime is None or hasattr(runtime, 'apartment_viewing_sessions') or hasattr(runtime, 'reasoning_cadence'):
            return runtime
        sid = self.sid if sid is None else sid
        lock = getattr(runtime, 'lock', None)
        if lock is not None:
            with lock:
                item = getattr(runtime, 'current', None)
        else:
            item = getattr(runtime, 'current', None)
        if isinstance(item, dict) and sid is not None and (item.get('session') or {}).get('id') == sid:
            return item.get('inner')
        return None

    def set_cadence(self, factor):
        """Scale the live JEV cadence (read on every reasoning tick) of the
        runtime serving the recording, from its configured values: factor 1
        restores them, 2 doubles the offer interval, debounce, keepalive and
        answer hold within bounds. Each rolled recording gets a fresh inner
        runtime, so the factor is reapplied to it as soon as it exists."""
        with self.lock:
            self.cadence_factor = factor
            self._apply_cadence()
        perceiver = getattr(self.runtime, 'la_live_perceiver', None)
        if perceiver is not None:
            perceiver.set_cadence(factor)

    def set_monitor_backoff(self, blocked):
        perceiver = getattr(self.runtime, 'la_live_perceiver', None)
        if perceiver is not None:
            perceiver.set_monitor_backoff(blocked)

    def qwen_control_status(self):
        perceiver = getattr(self.runtime, 'la_live_perceiver', None)
        return perceiver.monitor_status() if perceiver is not None else None

    def qwen_workload_status(self):
        perceiver = getattr(self.runtime, 'la_live_perceiver', None)
        return perceiver.workload_status() if perceiver is not None else None

    def _apply_cadence(self):
        runtime = self._inner()
        factor = getattr(self, 'cadence_factor', 1)
        if runtime is None or not hasattr(runtime, 'reasoning_cadence'):
            return
        applied = getattr(self, 'cadence_applied', None)
        if applied is None or applied[0] is not runtime:
            if factor == 1 and applied is None:
                return             # nothing to scale or restore yet
            # A runtime not yet scaled by this stack still holds its configured values.
            base = (dict(runtime.reasoning_cadence), runtime.reasoning_min_offer_interval_s)
        else:
            base = applied[1]
            if applied[2] == factor:
                return
        cadence, interval = base
        caps = {'keepalive_s': 60., 'debounce_s': 10., 'hold_max_s': 30., 'gate_keepalive_s': 60.}
        runtime.reasoning_cadence = {**cadence, **{k: min(caps[k], cadence[k] * factor) for k in caps if k in cadence}}
        runtime.reasoning_min_offer_interval_s = min(10., interval * factor)
        self.cadence_applied = (runtime, base, factor)

    def cadence_status(self):
        runtime = self._inner()
        if runtime is None or not hasattr(runtime, 'reasoning_cadence'):
            return None
        cadence = runtime.reasoning_cadence
        return {'min_offer_interval_s': runtime.reasoning_min_offer_interval_s,
                **{k: cadence.get(k) for k in ('debounce_s', 'keepalive_s', 'hold_max_s', 'gate_keepalive_s')}}

    def maintain(self):
        """Every loop tick, running or paused: the bounded egress release."""
        with self.lock:
            receipts = self.release.tick()
            if self.media_cleanup is not None:
                self.media_cleanup.tick()
            return receipts

    def maintenance_ok(self):
        return self.media_cleanup is None or self.media_cleanup.snapshot()['ok']

    def tick(self):
        with self.lock:
            if not self.wanted:
                return
            if self.sid is None:
                if self.retry_at is None or self.clock() >= self.retry_at:
                    self._open()
                return
            session = self.store.get_session(self.sid)
            if session['state'] in TERMINAL:
                self._count(self.sid)
                if self.sid in self.ours or session.get('reason') == 'duration_limit':
                    self.ours.discard(self.sid)
                    self.rolls += 1
                    self.sid = None  # our roll, or the budget expired first: reopen
                    self._open()
                elif session['state'] != 'stopped' or session.get('reason') in (
                        'worker_preparation_failed', 'worker_failed', 'runtime_failed', 'recording_failed'):
                    self._retry(session.get('reason') or session['state'])
                else:
                    # A stopped recording with an unknown reason may be an
                    # intentional external stop. Never guess that it can resume.
                    self.ended_externally, self.wanted, self.sid = True, False, None
                return
            started = session.get('started_at')
            if session['state'] == 'recording' and started is not None and self.clock() - started >= self.limit - self.roll_margin_s:
                self.rolls += 1
                self._close('activity_loop_roll')
                self._open()

    def _open(self):
        body = {'camera_id': self.camera_id, 'experiment_id': self.experiment_id, 'name': self.name,
                'request_id': 'activity-loop-' + uuid.uuid4().hex, 'reasoning_enabled': True}
        try:
            session = self.controller.start(body)
        except Exception as error:
            self._retry(getattr(error, 'code', None) or type(error).__name__)
            return
        if session['state'] in TERMINAL:
            self._retry(session.get('reason') or session['state'])
            return
        self.sid, self.retry_at, self.last_error = session['id'], None, None

    def _retry(self, reason):
        delay = min(self.retry_max_s, self.retry_s * 2 ** min(self.retry_failures, 16))
        self.retry_failures += 1
        self.sid, self.retry_at, self.last_error = None, self.clock() + delay, reason

    def _close(self, reason):
        if self.sid is None:
            return
        sid, self.sid = self.sid, None
        self.ours.add(sid)
        try:
            self.controller.stop(sid, reason)
        except Exception as error:
            self.last_error = getattr(error, 'code', None) or type(error).__name__
        self._count(sid)

    # --- reads -------------------------------------------------------------
    def _latest(self, sid, kind, arm='b'):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT id,payload FROM events WHERE session_id=? AND kind=? AND json_extract(payload,'$.arm')=? "
                "ORDER BY id DESC LIMIT 1", (sid, kind, arm)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def _count(self, sid):
        """Accumulate JEV calls and tokens (both arms are billed) since the last read."""
        after = self.counted.get(sid, 0)
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT id,kind,payload FROM events WHERE session_id=? AND id>? AND kind IN ('reasoning.delivery','reasoning.output') "
                "ORDER BY id", (sid, after)).fetchall()
        for event_id, kind, raw in rows:
            payload = json.loads(raw)
            if kind == 'reasoning.delivery' and payload.get('state') in ('completed', 'failed', 'indeterminate'):
                self.usage['calls'] += 1
            elif kind == 'reasoning.output':
                usage = ((payload.get('judgment') or {}).get('diagnostics') or {}).get('usage') or {}
                for key in ('input_tokens', 'output_tokens'):
                    if type(usage.get(key)) is int:
                        self.usage[key] += usage[key]
            self.counted[sid] = event_id
        if len(self.counted) > 16:
            for old in sorted(self.counted)[:-16]:
                self.counted.pop(old, None)

    def _health(self, session, now, output):
        jev = (False, 'jev_starting')
        delivery_id, delivery = self._latest(session['id'], 'reasoning.delivery')
        if delivery is not None:
            jev = {'completed': (True, None), 'failed': (False, 'jev_failed'),
                   'indeterminate': (False, 'jev_egress_blocked_indeterminate')}.get(delivery.get('state'),
                                                                                     (False, 'jev_' + str(delivery.get('state'))))
        inner = self._inner(session['id'])
        error = (getattr(inner, 'reasoning_errors', {}) or {}).get(session['id'])
        if error:
            jev = (False, error)
        if self.release.blocking():
            # Any indeterminate request holds all JEV egress (jev_policy).
            jev = (False, 'jev_egress_blocked_indeterminate')
        elif jev == (False, 'jev_egress_blocked_indeterminate'):
            jev = (False, 'jev_resuming')  # that request was abandoned; egress is open again
        if session['state'] != 'recording':
            deep = (False, 'deep_stack_' + session['state'])
        elif output is None or now - output.get('available_at', 0) > self.output_fresh_s:
            deep = (False, 'deep_stack_no_recent_output')
        else:
            deep = (True, None)
            self.retry_failures = 0  # preparation alone is not a successful recovery
        source = (getattr(inner, 'apartment_viewing_sessions', None) or {}).get(session['id'])
        camera = (False, 'camera_no_viewing_source') if source is None else (
            (True, None) if source.status().get('accepted_frames', 0) > 0 else (False, 'camera_no_frames_yet'))
        return {'jev': jev, 'deep_stack': deep, 'camera': camera}

    def observe(self):
        with self.lock:
            sid, ended = self.sid, self.ended_externally
            if sid is None:
                return {'running': False, 'ended_externally': ended, 'health': {}, 'usage': dict(self.usage)}
            session = self.store.get_session(sid)
            self._count(sid)
            now = self.clock()
            output_id, output = self._latest(sid, 'perception.output')
            result = (output or {}).get('result') if isinstance(output, dict) else None
            seen = {'running': session['state'] == 'recording', 'ended_externally': ended,
                    'health': self._health(session, now, output), 'usage': dict(self.usage),
                    'person_count': activity_pose.person_count(result) if isinstance(result, dict) else None}
            answer_id, answer = self._latest(sid, 'reasoning.output')
            binding = (output or {}).get('reading_binding')
            current_binding = (isinstance(binding, dict) and binding.get('status') == 'available'
                               and binding.get('session_id') == sid and binding.get('valid_until', 0) > now)
            context = (answer or {}).get('reading_context')
            answer_ok = (isinstance(context, dict) and current_binding
                         and reader_key(context.get('binding')) == reader_key(binding)
                         and answer.get('status') == 'ready' and answer.get('fresh_on_arrival') is True
                         and answer.get('provider_status') == 'ready'
                         and isinstance(answer.get('available_at_s'), (int, float))
                         and min(answer.get('valid_until', 0), context.get('valid_until', 0)) > now)
            if answer_ok:
                judgment = answer.get('judgment') or {}
                start = session.get('started_at') or 0
                seen['activity'] = {'event_id': answer_id, 'label': judgment.get('activity'),
                    'confidence': judgment.get('probability', judgment.get('confidence')),
                    'model': judgment.get('model_version'), 'facts_key': answer.get('facts_key') or (judgment.get('diagnostics') or {}).get('facts_key'),
                    'observed_at': start + answer['source_end_s'],
                    'binding': context['binding'], 'valid_until': min(answer['valid_until'], context['valid_until'])}
            self._apply_cadence()
            source = (getattr(self._inner(sid), 'apartment_viewing_sessions', None) or {}).get(sid)
            metric = getattr(source, 'metric', None)
            placement = metric.snapshot() if metric is not None else None
            people = activity_pose.positions(placement)
            if people:
                times = placement['frame'].get('original_times') or {}
                seen['position'] = {'key': placement['approximate_pose']['provenance'].get('source_event_id'),
                                    'people': people, 'observed_at': times.get('observed_at') or now}
            else:
                seen['position_reason'] = ('approximate_placement_unavailable' if metric is None
                                           else (placement or {}).get('reason') or 'approximate_no_placement')
            if current_binding and isinstance(placement, dict) and placement.get('renderable'):
                provenance = placement['approximate_pose']['provenance']
                raw = None
                frame = placement.get('frame') or {}
                placed = placement['approximate_pose']
                # Placement completes asynchronously. Join its exact immutable
                # source, while the latest raw observation remains the veto for
                # ambiguity, discontinuity and a changed reader. Never borrow
                # the newest detector's binding for an older placed frame.
                placed_id = provenance.get('source_event_id')
                row = None
                with self.store.lock:
                    if type(placed_id) is int and placed_id > 0:
                        row = self.store.db.execute(
                            "SELECT payload FROM events WHERE session_id=? AND id=? AND kind='perception.output' "
                            "AND json_extract(payload,'$.arm')='b'", (sid, placed_id)).fetchone()
                placed_output = json.loads(row[0]) if row else {}
                placed_binding = placed_output.get('reading_binding') or {}
                placed_result = placed_output.get('result') or {}
                if (placed_binding.get('status') == 'available'
                        and reader_key(placed_binding) == reader_key(binding)
                        and placed_binding.get('valid_until', 0) > now
                        and placed_output.get('status') == 'completed'
                        and placed_output.get('current') is True
                        and frame.get('frame_id') == placed_binding.get('source_frame_id')
                        and frame.get('source_epoch') == placed_binding.get('capture_epoch')):
                    poses = placed_result.get('pose_instances') or []
                    index = placed.get('instance_index')
                    selected = poses[index] if type(index) is int and 0 <= index < len(poses) else None
                    if selected is not None and selected.get('instance_id') == placed_binding.get('instance_id'):
                        raw = selected
                person = activity_pose.placed_person(placement, raw) if raw is not None else None
                if person is not None:
                    bound = dict(placed_binding, source_event_id=placed_id)
                    seen['pose'] = {'key': provenance.get('source_event_id'), 'people': [person],
                                    'observed_at': placed_binding['observed_at'], 'binding': bound,
                                    'valid_until': placed_binding['valid_until']}
            if 'pose' not in seen:
                seen['pose'] = dict(key='unbound:%s:%s' % (sid, output_id), people=[], observed_at=now,
                                    binding=None, valid_until=now + 1.)
            return seen

    def snapshot(self):
        with self.lock:
            return dict(session_id=self.sid, producer_epoch=self.producer_epoch, wanted=self.wanted, rolls=self.rolls,
                        last_error=self.last_error, retry_at=self.retry_at, retry_failures=self.retry_failures,
                        ended_externally=self.ended_externally, usage=dict(self.usage), budget_s=self.limit,
                        roll_margin_s=self.roll_margin_s, jev_release=self.release.snapshot(),
                        media_cleanup=self.media_cleanup.snapshot() if self.media_cleanup is not None else {'enabled': False},
                        qwen_control=self.qwen_control_status(), qwen_workload=self.qwen_workload_status())


def recipe_vjepa_arms(experiment_id, catalog_path=None):
    """Arms of a catalog recipe granted the temporal_encoder (V-JEPA) family."""
    from pathlib import Path
    from evidence_policy import recipe_grants
    path = Path(catalog_path) if catalog_path else Path(__file__).with_name('experiments.json')
    catalog = json.loads(path.read_text(encoding='utf-8'))
    recipe = next(r for r in catalog['experiments'] if r['id'] == experiment_id)
    defaults = {k: v['default'] for k, v in catalog['baseline']['families'].items()}
    grants = recipe_grants(recipe, defaults)
    return {arm for arm in ('a', 'b') if grants['temporal_encoder'][arm] == 'granted'}


def build(config, *, controller, occupancy, read_token, post=None, read_json=None, catalog_path=None):
    """An ActivityLoop for the managed controller from a checked private config.
    A string `zones` is the path of the private lighting-brain-geometry-1 file, read with read_json."""
    from activity_loop import ActivityLoop, SOURCE
    from brain_publisher import BrainPublisher
    # MVP (owner, 2026-10-19): the loop runs without V-JEPA and folds it in later.
    # Its recipe must grant no temporal_encoder, so no recording starts a V-JEPA
    # pump or waits on an LA V-JEPA lease, and JEV sees pose plus Qwen facts only.
    if config.get('vjepa', False):
        raise ValueError('V-JEPA is not provisioned for the always-on loop yet; set vjepa false')
    if recipe_vjepa_arms(config['experiment_id'], catalog_path):
        raise ValueError('the activity loop recipe grants V-JEPA while the loop runs with vjepa off')
    timings = dict(config['timings'])
    release = EgressRelease(controller.store, **{'enabled': True, **(config.get('jev_release') or {})})
    deep = ManagedDeepStack(controller, camera_id=config['camera_id'], experiment_id=config['experiment_id'],
                            roll_margin_s=timings.pop('roll_margin_s', 20.), release=release,
                            media_cleanup=config.get('media_cleanup'))
    brain = config['brain']
    publisher = BrainPublisher(origin=brain['origin'], home=config['home'], ca_file=brain['ca_file'], post=post,
                               token=lambda path=brain['token_file']: read_token(path))
    zones = config['zones']
    if isinstance(zones, str):
        if read_json is None:
            raise ValueError('a private file reader is required for the zones export')
        zones = read_json(zones)
    return ActivityLoop(deep=deep, occupancy=occupancy, publisher=publisher, home=config['home'], room=config['room'],
                        source=SOURCE, clock=time.time, pricing=config['pricing'], zones=zones,
                        person_position=config.get('person_position', True), **timings)


def install(app, loop, *, prefix='/api/perception-lab/v1'):
    """Run the loop with the app (started after it, stopped before its own
    shutdown stops recordings) and add the status/stop/resume routes."""
    import asyncio
    import contextlib
    from fastapi import HTTPException, Request
    from activity_loop import CADENCE, STOP_REASONS, ResumeRefused
    original = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(application):
        async with original(application) as state:
            loop.start_thread()
            try:
                yield state
            finally:
                await asyncio.to_thread(loop.shutdown)

    app.router.lifespan_context = lifespan

    def view():
        value = loop.snapshot()
        value['deep_stack'] = loop.deep.snapshot() if callable(getattr(loop.deep, 'snapshot', None)) else None
        return value

    @app.get(prefix + '/activity-loop')
    async def activity_loop_status(request: Request):
        request.state.principal.require('read')
        return await asyncio.to_thread(view)

    async def body(request, keys, required=()):
        raw = await request.body()
        try:
            value = json.loads(raw) if raw.strip() else {}
        except ValueError:
            value = None
        if not isinstance(value, dict) or not set(value) <= set(keys) or not set(required) <= set(value):
            raise HTTPException(422, 'activity_loop_body_invalid')
        return value

    @app.post(prefix + '/activity-loop/stop')
    async def activity_loop_stop(request: Request):
        # reason: 'operator_stop' (default, the owner) or 'monitor_backoff' (AL-5).
        request.state.principal.require('record')
        reason = (await body(request, ('reason',))).get('reason', 'operator_stop')
        if reason not in STOP_REASONS:
            raise HTTPException(422, 'activity_loop_stop_reason_invalid')
        await asyncio.to_thread(loop.stop, reason)
        return await asyncio.to_thread(view)

    @app.post(prefix + '/activity-loop/resume')
    async def activity_loop_resume(request: Request):
        # {"reason": "monitor_backoff"} resumes only the monitor's own Stop and
        # refuses the owner's operator_stop (409). No body: the owner resumes.
        request.state.principal.require('record')
        reason = (await body(request, ('reason',))).get('reason')
        if reason is not None and reason not in STOP_REASONS:
            raise HTTPException(422, 'activity_loop_stop_reason_invalid')
        try:
            await asyncio.to_thread(loop.resume, reason)
        except ResumeRefused as refused:
            raise HTTPException(409, str(refused))
        return await asyncio.to_thread(view)

    @app.get(prefix + '/activity-loop/cadence')
    async def activity_loop_cadence_status(request: Request):
        request.state.principal.require('read')
        return await asyncio.to_thread(loop.cadence_status)

    @app.post(prefix + '/activity-loop/cadence')
    async def activity_loop_cadence(request: Request):
        # {"level": "normal" | "reduced"}: the AL-5 monitor's step-1 back-off and its recovery.
        request.state.principal.require('record')
        level = (await body(request, ('level',), ('level',)))['level']
        if level not in CADENCE:
            raise HTTPException(422, 'activity_loop_cadence_level_invalid')
        return await asyncio.to_thread(loop.set_cadence, level)
    app.state.activity_loop = loop
    return app
