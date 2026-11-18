"""Owner-started, bounded worker preparation. Inspection never starts workers.

Lifecycle services own process/resource enforcement. This adapter owns the paired
session and never treats a timeout as proof that a remote worker has stopped.
"""
from copy import deepcopy
import logging
import threading
import time
import uuid

# Live recipes a managed pair may start. The pose recipes prepare the pose
# worker variant on both hosts and bind the pose profiles; vjepa-onoff is
# placement-best's pipeline with the temporal_encoder family as its lever.
POSE_EXPERIMENTS = frozenset({'placement-best', 'vjepa-onoff','apartment-viewing'})
LIVE_EXPERIMENTS = frozenset({'placement-same'}) | POSE_EXPERIMENTS

# Why a preparation ended without a recording ('worker_preparation_uncertain'
# for anything else). Only 'cancelled' follows a Stop; the rest are worker or
# lease outcomes that a later start may not repeat.
PREPARATION_REASONS = frozenset({'cancelled', 'worker_preparation_failed', 'worker_preparation_timeout',
                                 'insufficient_worker_lease', 'paired_start_failed', 'worker_busy'})
# The reason a recording row is stopped with once its workers were proven
# terminal by reconciliation, after the process that prepared them was gone.
RECONCILED_REASON = 'worker_cleanup_reconciled'
# Stop reasons that are worker outcomes, never an operator's Stop.
WORKER_STOP_REASONS = (PREPARATION_REASONS - {'cancelled'}) | {'worker_preparation_uncertain', RECONCILED_REASON}
TERMINAL_RECEIPTS = frozenset({'stopped', 'failed'})
# A cleanup that could not prove both workers released (an arm still stopping,
# e.g. behind LA's preflight scan, or a supervisor that could not be read) is
# retried by reap after this delay, doubling up to the cap.
CLEANUP_RETRY_SECONDS, CLEANUP_RETRY_MAX_SECONDS = 15, 300
# Indeterminate rows no worker item of this process owns are reconciled at
# the first reap after startup, then at this interval.
ORPHAN_CHECK_SECONDS = 60
# A supervisor with no lease for a recorded request id proves its start never
# arrived only once any start request still in flight has long ended.
ABSENT_GRACE_SECONDS = 120
_log = logging.getLogger('perception_lab.managed_pair')


class ManagedPair:
    deferred_start = True

    def __init__(self, store, clients, factory, catalog, *, clock=time.time, prepare_seconds=40, reasoning_available=False, reasoning_readiness=None,
                 analysis_jobs=None, caption_readiness=None, spatial_readiness=None, continuous_la_only=False, stop_wait_seconds=10):
        if set(clients) != {'a', 'b'}:
            raise ValueError('Exactly two provisioned lifecycle clients required')
        self.store, self.clients, self.factory = store, clients, factory
        self.catalog, self.clock = deepcopy(catalog), clock
        self.prepare_seconds = prepare_seconds
        # How long one cleanup waits for a stopping worker. A longer stop is
        # not an error: reap retries the cleanup until the receipt is terminal.
        self.stop_wait_seconds = stop_wait_seconds
        # Durable reconciliation of indeterminate rows: the first reap runs it.
        self.orphan_check_at = 0
        self.orphan_thread = None
        if type(continuous_la_only) is not bool:
            raise ValueError('continuous_la_only must be boolean')
        self.continuous_la_only = continuous_la_only
        self.lock = threading.RLock()
        self.current = None
        self.reasoning_available=reasoning_available
        self.reasoning_readiness=reasoning_readiness
        self.closed = False
        self.clients_closed = False
        self.last_cleanup = None
        # Startup-checked provisioning facts (managed_detector_app): the caption
        # source and granting recipes, and per-localization spatial reasons.
        self.caption_readiness = deepcopy(caption_readiness)
        self.spatial_readiness = deepcopy(spatial_readiness)
        # Saved analysis outlives every paired session (it requires a stopped
        # one), so it belongs to this controller-lifetime owner, built with
        # qwen_transport.saved_analysis. None means not provisioned
        # (qwen_admission_not_provisioned).
        self.analysis = analysis_jobs
        if self.analysis is not None:
            self.analysis.gate = self._analysis_gate
            self.analysis.start_reconcile()

    def _analysis_gate(self):
        """Live paired work has priority: no analysis step starts beside it."""
        with self.lock:
            if self.closed:
                return 'runtime_shutting_down'
            if self.current is None:
                return None
            return {'recording': 'paired_recording_active', 'starting': 'worker_preparation_active'}.get(
                self.current['phase'], 'worker_cleanup_pending')

    def analysis_capability(self):
        from analysis import RUNS, UNSUPPORTED_RUNS
        value = {'available': False, 'reason': 'qwen_admission_not_provisioned', 'runs': list(RUNS),
                 'unsupported_runs': dict(UNSUPPORTED_RUNS), 'modes': ['causal', 'retrospective'],
                 'default_mode': 'causal', 'labels': 'excluded', 'queue_limit': 2,
                 'active': False, 'uncertain_steps': 0}
        if self.analysis is None:
            return value
        reason = self._analysis_gate()
        try:
            uncertain = self.analysis.analysis.uncertain_steps()
        except Exception:
            uncertain = None
        value.update(available=reason is None, reason=reason, active=self.analysis.active(), uncertain_steps=uncertain)
        return value

    def caption_capability(self, reasoning):
        """Captions reach JEV only with the caption source provisioned (checked
        at startup) and the household caption egress currently granted."""
        value = self.caption_readiness or {}
        source = value.get('source') or {'provisioned': False, 'reason': 'caption_history_not_provisioned'}
        if not source.get('provisioned'):
            reason = source.get('reason') or 'caption_history_not_provisioned'
        elif not isinstance(reasoning, dict) or reasoning.get('available') is not True:
            reason = (reasoning or {}).get('reason') if isinstance(reasoning, dict) else None
            reason = reason or 'jev_not_provisioned'
        elif reasoning.get('historical_caption') != 'granted':
            reason = 'household_caption_egress_not_granted'
        else:
            reason = None
        return {'available': reason is None, 'reason': reason, 'granted_recipes': list(value.get('granted_recipes') or [])}

    def spatial_capability(self):
        if self.spatial_readiness is None:
            return {'state': 'not_provisioned', 'reason': 'spatial_not_provisioned'}
        return {'state': 'provisioned', 'reason': None, 'localizations': {
            kind: {'available': reason is None, 'reason': reason} for kind, reason in sorted(self.spatial_readiness.items())}}

    def capabilities(self):
        analysis = self.analysis_capability()
        reasoning = (self.reasoning_readiness() if self.reasoning_readiness else
                     {'available': self.reasoning_available, 'reason': None if self.reasoning_available else 'jev_not_provisioned'})
        caption, spatial = self.caption_capability(reasoning), self.spatial_capability()
        with self.lock:
            phase = self.current['phase'] if self.current is not None else None
            reason = {'indeterminate': 'worker_cleanup_pending', 'draining': 'local_drain_pending'}.get(phase)
            gates = [{'id': 'workers', 'state': 'blocked', 'reason': reason}] if reason else []
            # Informational: saved analysis, captions and spatial evidence never gate recording.
            gates.append({'id': 'saved_analysis', 'state': 'available' if analysis['available'] else 'unavailable',
                          'reason': analysis['reason']})
            gates.append({'id': 'caption_history', 'state': 'available' if caption['available'] else 'unavailable',
                          'reason': caption['reason']})
            gates.append({'id': 'spatial', 'state': 'available' if spatial['state'] == 'provisioned' else 'unavailable',
                          'reason': spatial['reason']})
            catalog = deepcopy(self.catalog)
            for recipe in catalog:
                if recipe.get('id') == 'qwen-evidence' and recipe.get('status') == 'blocked':
                    # Saved-session only; ManagedPair never starts it live.
                    recipe.update(status='unverified' if self.analysis is not None else 'blocked',
                                  readiness_reason='saved_analysis_on_request' if self.analysis is not None else 'qwen_admission_not_provisioned')
            return {'recording_allowed': not self.closed and not reason, 'experiments': catalog,
                    'gates': gates, 'worker_mode': 'on_demand_bounded', 'last_worker_cleanup': deepcopy(self.last_cleanup),
                    'analysis': analysis, 'caption': caption, 'spatial': spatial, 'reasoning': reasoning,
                    **({'apartment_viewing':deepcopy(self.apartment_viewing_capability)}
                       if hasattr(self,'apartment_viewing_capability') else {})}

    def run_job(self, job):
        if job['kind'] == 'diagnostic':
            # Operator hook: identity-only reconciliation, run on the analysis
            # worker so it never overlaps an admitted analysis request.
            parameters = job.get('parameters') or {}
            if (self.analysis is None or set(parameters) - {'operation', 'job_id'}
                    or parameters.get('operation') != 'analysis_reconcile'):
                return {'state': 'blocked', 'reason': 'job_handler_not_provisioned'}
            target = parameters.get('job_id')
            if target is not None:
                from store import identifier, LabError
                try:
                    identifier(target)
                except LabError:
                    return {'state': 'blocked', 'reason': 'invalid_reconcile_target'}
            return self.analysis.submit_maintenance(job, target)
        if job['kind'] != 'analysis':
            return {'state': 'blocked', 'reason': 'job_handler_not_provisioned'}
        if self.analysis is None:
            return {'state': 'blocked', 'reason': 'qwen_admission_not_provisioned'}
        blocked = self._analysis_gate()
        if blocked:
            return {'state': 'blocked', 'reason': blocked}
        return self.analysis.submit(job)

    def cancel_job(self, job):
        if self.analysis is None:
            return {'state': 'indeterminate', 'reason': 'analysis_not_reconciled'}
        return self.analysis.cancel(job)

    def list_jobs(self, session_id=None):
        """Stored jobs plus whether this controller's worker still owns each."""
        jobs = self.store.list_jobs(session_id)
        if self.analysis is None:
            return [{**job, 'worker_active': False} for job in jobs]
        with self.analysis.lock:
            running = {jid for jid, (future, _) in self.analysis.work.items() if not future.done()}
        return [{**job, 'worker_active': job['id'] in running} for job in jobs]

    def cleanup_pending(self, sid):
        with self.lock:
            return (self.current is not None and self.current['session']['id'] == sid
                    and self.current['phase'] in {'indeterminate', 'draining'})

    def start_session(self, session):
        with self.lock:
            if self.closed or self.current is not None:
                return {'state': 'blocked', 'reason': 'worker_cleanup_pending'}
            if session['experiment_id'] not in LIVE_EXPERIMENTS or not any(
                    r['id']==session['experiment_id'] and r.get('status')!='blocked' for r in self.catalog):
                return {'state': 'blocked', 'reason': 'experiment_pair_not_provisioned'}
            item = {'session': session, 'request_id': str(uuid.uuid4()), 'cancel': threading.Event(),
                    'receipts': {}, 'inner': None, 'phase': 'starting', 'reason': None,
                    'attempted': set(), 'thread': None, 'inner_stopped': False,
                    'requested_at': self.clock(), 'cleanup_attempts': 0, 'retry_at': None}
            # Only the commissioned automatic apartment loop uses LA alone.
            # Ordinary Lab comparisons retain both workers and both receipts.
            item['execution_arms'] = ('b',) if (self.continuous_la_only
                and session['experiment_id'] == 'apartment-viewing'
                and session.get('request_id', '').startswith('activity-loop-')) else ('a', 'b')
            # Write the identity before transmitting; never invent a new ID after
            # a lost start response. Supervisor reservations survive our restart.
            self.store.append_event(session['id'], 'workers.preparing',
                                    {'request_id': item['request_id'], 'execution_arms': list(item['execution_arms']),
                                     'comparison': len(item['execution_arms']) == 2})
            self.current = item
            item['thread'] = threading.Thread(target=self._prepare, args=(item,), daemon=True)
            item['thread'].start()
            return {'state': 'starting', 'reason': 'preparing_comparison'}

    def _prepare(self, item):
        try:
            for arm, client in self.clients.items():
                if arm not in item['execution_arms']:
                    continue
                if item['cancel'].is_set():
                    raise RuntimeError('cancelled')
                item['attempted'].add(arm)
                from worker_lifecycle_client import StartRejected
                try:
                    item['receipts'][arm] = (client.start(item['request_id'], item['session']['id'],variant='pose')
                        if item['session']['experiment_id'] in POSE_EXPERIMENTS else client.start(item['request_id'], item['session']['id']))
                except StartRejected:
                    item['attempted'].remove(arm)
                    raise RuntimeError('worker_busy') from None
            deadline = time.monotonic() + self.prepare_seconds
            while not item['cancel'].is_set():
                for arm, client in self.clients.items():
                    if arm not in item['execution_arms']:
                        continue
                    item['receipts'][arm] = client.status(item['request_id'])
                states = [r['state'] for r in item['receipts'].values()]
                if all(s == 'ready' for s in states):
                    break
                if any(s not in {'starting', 'ready'} for s in states):
                    raise RuntimeError('worker_preparation_failed')
                if time.monotonic() >= deadline:
                    raise RuntimeError('worker_preparation_timeout')
                item['cancel'].wait(.25)
            if item['cancel'].is_set():
                raise RuntimeError('cancelled')
            remaining = min(r['expires_at'] for r in item['receipts'].values()) - self.clock()
            if remaining < self.store.settings.max_session_seconds + 10:
                raise RuntimeError('insufficient_worker_lease')
            inner = (self.factory(item['receipts'],experiment_id=item['session']['experiment_id'])
                     if item['session']['experiment_id'] in POSE_EXPERIMENTS else self.factory(item['receipts']))
            with self.lock:
                item['inner'] = inner
                if item['cancel'].is_set():
                    raise RuntimeError('cancelled')
                result = inner.start_session(item['session'])
                if result.get('state') != 'recording' or result.get('inference') != 'ready':
                    raise RuntimeError('paired_start_failed')
                self.store.append_event(item['session']['id'], 'workers.ready',
                                        {'request_id': item['request_id'], 'receipts': item['receipts'],
                                         'execution_arms': list(item['execution_arms']),
                                         'comparison': len(item['execution_arms']) == 2})
                self.store.transition(item['session']['id'], 'recording')
                item['phase'] = 'recording'
        except Exception as exc:
            item['reason'] = str(exc) if str(exc) in PREPARATION_REASONS else 'worker_preparation_uncertain'
            self._cleanup(item)

    def _drain(self, item):
        # Local users of the ledger (recorder, pumps, JEV and remote futures).
        # Separate from worker release; a slow future must not wedge the Lab.
        inner = item['inner']
        if inner is None:
            return True
        try:
            if not item['inner_stopped']:
                item['inner_stopped'] = True
                inner.stop_session(self.store.get_session(item['session']['id']))
            return inner.close(timeout=5).get('drained') is True
        except Exception:
            return False

    def _settle(self, item):
        # Caller holds self.lock. Recording stops only after release and drain.
        sid = item['session']['id']
        current = self.store.get_session(sid)
        if item['phase'] != 'draining' and current['state'] in {'starting', 'recording', 'stopping', 'indeterminate'}:
            self.store.transition(sid, item['phase'], item['reason'])
        # Keep uncertainty visible and keep new admission closed.
        if item['phase'] == 'stopped' and self.current is item:
            self.current = None

    def _release_arm(self, client, request_id, requested_at, session_id=None):
        """Stop one lifecycle request unless its receipt is terminal, waiting
        briefly while it stops. Returns (receipt, released); raises when the
        supervisor cannot be read. With session_id, the receipt must be bound to
        that session before anything is stopped.

        A supervisor holding no lease for the request proves release only once
        a start still in flight could no longer reach it: the identity is
        written before any start is sent, so it may never have arrived."""
        from worker_lifecycle_client import RequestNotFound
        try:
            receipt = client.status(request_id)
        except RequestNotFound:
            absent = self.clock() - requested_at >= ABSENT_GRACE_SECONDS
            return {'state': 'absent' if absent else 'unknown', 'reason': 'request_not_found'}, absent
        if session_id is not None and (receipt.get('request_id') != request_id or receipt.get('session_id') != session_id):
            raise ValueError('Lifecycle receipt bound to another session')
        if receipt['state'] not in TERMINAL_RECEIPTS:
            receipt = client.stop(request_id, receipt['engine_epoch'])
        deadline = time.monotonic() + self.stop_wait_seconds
        while receipt['state'] == 'stopping' and time.monotonic() < deadline:
            time.sleep(.1)
            receipt = client.status(request_id)
        return receipt, receipt['state'] in TERMINAL_RECEIPTS

    def _cleanup(self, item):
        drained = self._drain(item)
        released = True
        for arm in item['attempted']:
            try:
                receipt, done = self._release_arm(self.clients[arm], item['request_id'], item['requested_at'])
                item['receipts'][arm] = receipt
                released = done and released
            except Exception:
                released = False
        with self.lock:
            # Worker receipts decide release; an undrained local future only
            # delays clearing admission until reap sees it drained.
            item['phase'] = ('stopped' if drained else 'draining') if released else 'indeterminate'
            if not released:
                # Not a final state: an arm still stopping (LA's preflight scan
                # can outlast any fixed wait) or an unreadable supervisor. Reap
                # retries this cleanup, so the session settles once every
                # receipt is terminal instead of blocking admission for good.
                item['cleanup_attempts'] += 1
                item['retry_at'] = self.clock() + min(
                    CLEANUP_RETRY_MAX_SECONDS, CLEANUP_RETRY_SECONDS * 2 ** (item['cleanup_attempts'] - 1))
            sid = item['session']['id']
            workers = {arm: {'state': r.get('state'), 'reason': r.get('reason')} for arm, r in item['receipts'].items()}
            self.store.append_event(sid, 'workers.cleanup', {
                'request_id': item['request_id'], 'state': 'stopped' if released else 'indeterminate',
                'reason': item['reason'], 'local_drain': 'drained' if drained else 'pending', 'workers': workers})
            self.last_cleanup = {'session_id': sid, 'state': item['phase'], 'reason': item['reason'], 'workers': deepcopy(workers)}
            self._settle(item)

    def _finish_drain(self, item):
        drained = self._drain(item)
        with self.lock:
            if not drained or item['phase'] != 'draining':
                return
            item['phase'] = 'stopped'
            self.store.append_event(item['session']['id'], 'workers.local_drained', {'request_id': item['request_id']})
            if self.last_cleanup and self.last_cleanup['session_id'] == item['session']['id']:
                self.last_cleanup['state'] = 'stopped'
            self._settle(item)

    def stop_session(self, session):
        with self.lock:
            item = self.current
            if item is None:
                return {'state': 'stopped'}
            if item['session']['id'] != session['id']:
                return {'state': 'indeterminate', 'reason': 'worker_session_mismatch'}
            item['cancel'].set()
            if item['thread'].is_alive():
                return {'state': 'stopping', 'reason': 'worker_cleanup_pending'}
            item['phase'] = 'stopping'
            item['thread'] = threading.Thread(target=self._cleanup, args=(item,), daemon=True)
            item['thread'].start()
            return {'state': 'stopping', 'reason': 'worker_cleanup_pending'}

    def live(self, session):
        with self.lock:
            item = self.current
            if item and item['session']['id'] == session['id']:
                if item['phase'] == 'recording':
                    return item['inner'].live(session)
                if item['phase'] == 'draining':
                    return {'state': 'stopping', 'reason': 'local_drain_pending', 'arms': {}}
                return {'state': item['phase'], 'reason': item['reason'] or 'preparing_comparison', 'arms': {}}
            return {'state': 'stopped', 'arms': {}}

    def reap_finished_sessions(self):
        with self.lock:
            item = self.current
            if item and item['phase'] == 'recording':
                session = self.store.get_session(item['session']['id'])
                expiry = min(r['expires_at'] for r in item['receipts'].values())
                if session['state'] != 'recording' or self.clock() >= expiry - 10:
                    self.stop_session(session)
            elif item and item['phase'] == 'draining' and not item['thread'].is_alive():
                # Released workers are not contacted again; only local drain is
                # rechecked, once per reap, until it completes.
                item['thread'] = threading.Thread(target=self._finish_drain, args=(item,), daemon=True)
                item['thread'].start()
            elif (item and item['phase'] == 'indeterminate' and not item['thread'].is_alive()
                  and not self.closed and self.clock() >= item['retry_at']):
                # Release was not proven last time. The same cleanup reads,
                # and if needed stops, only this session's own requests.
                item['thread'] = threading.Thread(target=self._cleanup, args=(item,), daemon=True)
                item['thread'].start()
            if (not self.closed and self.clock() >= self.orphan_check_at
                    and not (self.orphan_thread and self.orphan_thread.is_alive())):
                self.orphan_check_at = self.clock() + ORPHAN_CHECK_SECONDS
                self.orphan_thread = threading.Thread(target=self._reconcile_orphans_quietly, daemon=True)
                self.orphan_thread.start()
        return {'state': 'reconciled'}

    def _reconcile_orphans_quietly(self):
        try:
            self.reconcile_orphans()
        except Exception as error:
            # Class only: the next pass retries; the row stays indeterminate.
            _log.warning('perception-lab orphan reconciliation failed: error_class=%s', type(error).__name__)

    def reconcile_orphans(self):
        """Settle indeterminate rows that no worker item of this process owns.

        Such a row outlived the process that prepared its workers (a Lab
        restart drops the in-memory item, and Store.recover marks every row it
        was running indeterminate) or never reached them. While it stays, the
        Store refuses every new recording on its camera. It moves to 'stopped'
        only when each lifecycle request its durable 'workers.preparing' event
        names is proven terminal by that supervisor's receipt for the same
        session (a non-terminal one is stopped, as cleanup would), or proven
        never reserved. Without that event no request was ever sent. Records
        are kept; any doubt leaves the row for the next pass. Returns the ids
        settled."""
        settled = []
        for session in self.store.indeterminate_sessions():
            with self.lock:
                if self.closed:
                    break
                if self.current is not None and self.current['session']['id'] == session['id']:
                    continue
            evidence = self._orphan_release(session)
            if evidence is None:
                continue
            with self.lock:
                # Re-checked under the lock that guards admission and cleanup:
                # the row must still be exactly the one that was proven.
                if self.closed or (self.current is not None and self.current['session']['id'] == session['id']):
                    continue
                if self.store.settle_indeterminate(session['id'], session['revision'], RECONCILED_REASON, evidence):
                    settled.append(session['id'])
                    _log.info('perception-lab reconciled an indeterminate recording: workers=%s',
                              ','.join(f"{arm}:{w['state']}" for arm, w in sorted(evidence['workers'].items())) or 'none')
        return settled

    def _orphan_release(self, session):
        """Evidence that every worker request of an unowned session is
        released, or None while any is unproven."""
        found = self.store.latest_event(session['id'], 'workers.preparing')
        prior = {'state': session['state'], 'reason': session['reason'], 'revision': session['revision']}
        if found is None:
            return {'request_id': None, 'workers': {}, 'prior': prior}
        prepared_at, preparing = found
        request_id = preparing.get('request_id') if isinstance(preparing, dict) else None
        # Older events name no arms: both were prepared.
        arms = preparing.get('execution_arms', ['a', 'b']) if isinstance(preparing, dict) else None
        if (not isinstance(request_id, str) or not isinstance(arms, list) or not arms
                or not set(arms) <= set(self.clients) or type(prepared_at) not in (int, float)):
            return None
        variant = 'pose' if session['experiment_id'] in POSE_EXPERIMENTS else 'detector'
        workers = {}
        for arm in sorted(set(arms)):
            client = self.clients[arm]
            try:
                adopt = getattr(client, 'adopt', None)
                if callable(adopt):
                    # The previous process's binding, from its durable record.
                    adopt(request_id, session['id'], variant)
                receipt, released = self._release_arm(client, request_id, prepared_at, session_id=session['id'])
            except Exception:
                return None
            if not released:
                return None
            workers[arm] = {'state': receipt['state'], 'reason': receipt.get('reason')}
        return {'request_id': request_id, 'workers': workers, 'prior': prior}

    def set_reasoning(self,session,enabled):
        with self.lock:
            if not self.current or self.current["session"]["id"]!=session["id"] or self.current["phase"]!="recording":
                return {"state":"blocked","reason":"recording_not_ready"}
            return self.current["inner"].set_reasoning(session,enabled)

    def switch(self, session, request):
        return {'state': 'blocked', 'reason': 'stop_before_changing_comparison'}

    def close(self, timeout=10):
        # Under the lock: an orphan reconciliation writes only while open.
        with self.lock:
            self.closed = True
        # Queued analysis is cancelled now; paired workers stop without waiting
        # for it. An in-flight admitted step keeps the store open (drained
        # False) until its worker exits.
        if self.analysis is not None:
            self.analysis.close()
        with self.lock:
            item = self.current
            orphans = self.orphan_thread
        if item:
            self.stop_session(item['session'])
            item['thread'].join(timeout)
        if orphans is not None:
            # It only reads and stops this Lab's own orphaned requests; once
            # closed it writes nothing, so a slow supervisor read never holds
            # shutdown beyond this bound.
            orphans.join(min(timeout, 5))
        if self.current is None and not self.clients_closed:
            for client in self.clients.values():
                if callable(getattr(client, 'close', None)):
                    if client.close() is not True:
                        return {'drained': False, 'state': 'worker_cleanup_pending'}
            self.clients_closed = True
        analysis_drained = self.analysis is None or self.analysis.close(timeout=min(timeout, 5))
        drained = self.current is None and analysis_drained
        return {'drained': drained,
                'state': 'closed' if drained else ('worker_cleanup_pending' if self.current is not None else 'analysis_step_pending')}

    def delete_session(self, session, expected_revision, *, erase_records=False):
        from store import LabError
        with self.lock:
            if self.current is not None:
                raise LabError('Worker cleanup is pending', 409, 'execution_pending')
            if self.analysis is not None and self.analysis.active(session['id']):
                raise LabError('Saved analysis is active for this session', 409, 'execution_pending')
            return self.store.delete_session(session['id'], expected_revision, erase_records=erase_records)
