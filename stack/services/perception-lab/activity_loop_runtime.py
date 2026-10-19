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

TERMINAL = ('stopped', 'failed', 'interrupted', 'blocked', 'indeterminate')
OPEN = ('requested', 'starting', 'recording', 'stopping')
REQUEST_PREFIX = 'activity-loop-'
RELEASE_REASON = 'activity_loop_auto_release_indeterminate'


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
                 output_fresh_s=10., retry_s=10., name='Activity loop', clock=None, release=None):
        self.controller, self.store, self.runtime = controller, controller.store, controller.runtime
        self.camera_id, self.experiment_id, self.name = camera_id, experiment_id, name
        self.clock = clock or self.store.clock
        limit = getattr(self.store.settings, 'max_session_seconds', 300)
        if not (5 <= roll_margin_s < limit / 2):
            raise ValueError('roll margin must leave most of each recording')
        self.limit, self.roll_margin_s = float(limit), float(roll_margin_s)
        self.output_fresh_s, self.retry_s = output_fresh_s, retry_s
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

    # --- control -----------------------------------------------------------
    def start(self):
        with self.lock:
            self.wanted, self.ended_externally = True, False
            if self.sid is None:
                self._open()

    def stop(self, reason='activity_loop_stop'):
        with self.lock:
            self.wanted = False
            self._close(reason)

    def maintain(self):
        """Every loop tick, running or paused: the bounded egress release."""
        with self.lock:
            return self.release.tick()

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
                    self.sid = None  # our roll, or the budget expired first: reopen
                    self._open()
                else:
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
            self.sid, self.retry_at = None, self.clock() + self.retry_s
            self.last_error = getattr(error, 'code', None) or type(error).__name__
            return
        if session['state'] in TERMINAL:
            self.sid, self.retry_at = None, self.clock() + self.retry_s
            self.last_error = session.get('reason') or session['state']
            return
        self.sid, self.retry_at, self.last_error = session['id'], None, None

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
        error = (getattr(self.runtime, 'reasoning_errors', {}) or {}).get(session['id'])
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
        source = getattr(self.runtime, 'apartment_viewing_sessions', {}).get(session['id'])
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
            if answer is not None:
                judgment = answer.get('judgment') or {}
                start = session.get('started_at') or 0
                seen['activity'] = {'event_id': answer_id, 'label': judgment.get('activity'),
                    'confidence': judgment.get('probability', judgment.get('confidence')),
                    'model': judgment.get('model_version'), 'facts_key': answer.get('facts_key') or (judgment.get('diagnostics') or {}).get('facts_key'),
                    'observed_at': start + answer['source_end_s'] if isinstance(answer.get('source_end_s'), (int, float)) else now}
            source = getattr(self.runtime, 'apartment_viewing_sessions', {}).get(sid)
            metric = getattr(source, 'metric', None)
            placement = metric.snapshot() if metric is not None else None
            if isinstance(placement, dict) and placement.get('renderable'):
                provenance = placement['approximate_pose']['provenance']
                raw = None
                if output_id == provenance.get('source_event_id') and isinstance(result, dict):
                    poses = result.get('pose_instances') or []
                    raw = poses[placement['approximate_pose'].get('instance_index', 0)] if poses else None
                person = activity_pose.placed_person(placement, raw)
                if person is not None:
                    times = placement['frame'].get('original_times') or {}
                    seen['pose'] = {'key': provenance.get('source_event_id'), 'people': [person],
                                    'observed_at': times.get('observed_at') or now}
            return seen

    def snapshot(self):
        with self.lock:
            return dict(session_id=self.sid, wanted=self.wanted, rolls=self.rolls, last_error=self.last_error,
                        ended_externally=self.ended_externally, usage=dict(self.usage), budget_s=self.limit,
                        roll_margin_s=self.roll_margin_s, jev_release=self.release.snapshot())


def build(config, *, controller, occupancy, read_token, post=None, read_json=None):
    """An ActivityLoop for the managed controller from a checked private config.
    A string `zones` is the path of AL-4's private export, read with read_json."""
    from activity_loop import ActivityLoop, SOURCE
    from brain_publisher import BrainPublisher
    timings = dict(config['timings'])
    release = EgressRelease(controller.store, **{'enabled': True, **(config.get('jev_release') or {})})
    deep = ManagedDeepStack(controller, camera_id=config['camera_id'], experiment_id=config['experiment_id'],
                            roll_margin_s=timings.pop('roll_margin_s', 20.), release=release)
    brain = config['brain']
    publisher = BrainPublisher(origin=brain['origin'], home=config['home'], ca_file=brain['ca_file'], post=post,
                               token=lambda path=brain['token_file']: read_token(path))
    zones = config['zones']
    if isinstance(zones, str):
        if read_json is None:
            raise ValueError('a private file reader is required for the zones export')
        zones = read_json(zones)
    return ActivityLoop(deep=deep, occupancy=occupancy, publisher=publisher, home=config['home'], room=config['room'],
                        source=SOURCE, clock=time.time, pricing=config['pricing'], zones=zones, **timings)


def install(app, loop, *, prefix='/api/perception-lab/v1'):
    """Run the loop with the app (started after it, stopped before its own
    shutdown stops recordings) and add the status/stop/resume routes."""
    import asyncio
    import contextlib
    from fastapi import Request
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

    @app.post(prefix + '/activity-loop/stop')
    async def activity_loop_stop(request: Request):
        request.state.principal.require('record')
        await asyncio.to_thread(loop.stop, 'operator_stop')
        return await asyncio.to_thread(view)

    @app.post(prefix + '/activity-loop/resume')
    async def activity_loop_resume(request: Request):
        request.state.principal.require('record')
        await asyncio.to_thread(loop.resume)
        return await asyncio.to_thread(view)
    app.state.activity_loop = loop
    return app
