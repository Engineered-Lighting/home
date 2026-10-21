"""Durable, bounded independent JEV arms. No inference on construction/replay.

One instance belongs to the controller's exclusively owned session lifetime.
Existing dispatching rows are never retried or freed merely on reconstruction.
The provider is explicitly provisioned; this module grants no cloud permission.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import uuid

import jev_smoothing
import activity_reading
from jev_provider import ProviderFailure
from reasoning import canonical, causal_evidence, shared_evidence, QUESTIONS, recommendation, digest, finite

# Controlled vocabulary for requests that end without reaching the provider.
# One `reasoning.lifecycle` receipt per such request: fixed fields, no evidence.
LIFECYCLE_REASONS = {
    "replaced": ("superseded_by_newer_request",),
    "expired": ("session_not_recording", "experiment_generation_changed", "deadline_passed_before_dispatch"),
    # controller_restart is written by Store.recover, never by close().
    "cancelled": ("reasoning_disabled", "session_stopped", "session_ended", "runtime_shutdown", "reasoning_closed", "controller_restart"),
    "failed": ("local_dispatch_failed",),
}
# Local-only timeline records (plan §5.2/5.3). None of them is ever read into a
# JEV request, packet or sidecar (live_inputs reads only perception, HA and
# room rows); they exist for the timeline display and findings.
#   reasoning.validity: a delivered answer's facts-bound validity ended early.
#   reasoning.smoothed: the code-owned (HMM + hysteresis) activity timeline.
#   reasoning.gate: a code-gate abstention answered locally instead of a request.
LOCAL_ABSTAIN_REASONS = ("occupancy_vacant", "occupancy_unknown")
SMOOTHING_VERSION = "jev-smoothing-1"
OFFER_KEYS_LIMIT = 64


class LiveReasoning:
    # Reasons a caller may give close(); see LIFECYCLE_REASONS.
    CLOSE_REASONS = ("reasoning_disabled", "session_stopped", "session_ended", "runtime_shutdown", "reasoning_closed")

    def __init__(self, store, session_id, provider, *, policy=None, smoothing=None):
        self.store, self.sid, self.provider = store, session_id, provider
        self.session = store.get_session(session_id)
        self.policy = json.loads(canonical(policy or {}))
        self.lock = threading.RLock()
        self.reading_qwen_source = None
        self.closed = False
        self.timers = {}
        self.futures = set()
        # Facts-bound validity and smoothing per arm (the scheduler may set
        # hold_max_s from its cadence settings), and the scheduler's facts key
        # of each offered request, kept only until its answer arrives.
        self.hold_max_s = jev_smoothing.HOLD_MAX_S
        # The registered smoothing parameter set (None: jev-smoothing-1); its
        # hold_max always comes from the scheduler (hold_max_s).
        self.smoothing = jev_smoothing.SMOOTHING_DEFAULT if smoothing is None else smoothing
        if self.smoothing not in jev_smoothing.PARAMETER_SETS:
            raise ValueError("registered smoothing parameter set required")
        self.tracks = {}
        self.offer_keys = {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lab-jev")
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS lab_reasoning_requests(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL,
                generation INTEGER NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
                dispatched_at REAL, result TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS reasoning_active_arm
                ON lab_reasoning_requests(session_id,arm)
                WHERE state IN ('dispatching','indeterminate')""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS reasoning_pending_arm
                ON lab_reasoning_requests(session_id,arm) WHERE state='pending'""")
            # The per-arm dispatch spacing reads MAX(dispatched_at). The column
            # follows the large payload, so without this index every dispatch
            # walked each earlier request of the session through its payload.
            db.execute("""CREATE INDEX IF NOT EXISTS reasoning_arm_dispatched
                ON lab_reasoning_requests(session_id,arm,dispatched_at)""")

    def bind_reading_qwen(self, source):
        """Attach only the incumbent controller's trusted original receipt bank."""
        from la_qwen_context import LaQwenReceipts
        if type(source) is not LaQwenReceipts:
            raise ValueError('trusted original Qwen receipt source required')
        with self.lock:
            if self.closed or self.reading_qwen_source is not None and self.reading_qwen_source is not source:
                raise ValueError('reading Qwen source already retired or bound')
            self.reading_qwen_source = source

    def _generation(self, db):
        row = db.execute("SELECT generation FROM lab_experiment_state WHERE session_id=?", (self.sid,)).fetchone()
        return row[0] if row else None

    def _lifecycle(self, db, rows, state, reason, replaced_by=None):
        """Bounded receipt, in the caller's transaction, for each pending request
        that ends without dispatch. Admission, freshness and budgets are unchanged:
        it only records the state change the caller already makes."""
        if reason not in LIFECYCLE_REASONS[state]:
            raise ValueError("uncontrolled reasoning lifecycle reason")
        now = self.store.clock()
        for request_id, arm, generation in rows:
            payload = {"request_id": request_id, "arm": arm, "generation": generation, "state": state,
                       "reason": reason, "at": now, "available_at": now}
            if replaced_by is not None:
                payload["replaced_by"] = replaced_by
            # Best effort under a SAVEPOINT: a failed receipt never undoes the
            # state change, masks the caller's error or blocks stop. An erased
            # session keeps no new records.
            self.store.receipt(db, self.sid, "reasoning.lifecycle", payload)

    def facts_keys(self, records, *, generation, cutoff, windows=None, shared_context=None, context_snapshot=None,
                   arms=("a", "b")):
        """{arm: provider facts ({'key', 'people', ...}) or None} for the inputs
        offer() would admit now; None when the provider has no facts key (v5).
        Local only: nothing is admitted, recorded or sent."""
        compute = getattr(self.provider, "facts_key", None)
        if not callable(compute) or getattr(self.provider, "wire", None) is None:
            return None
        if getattr(self.provider, 'person_mode', False):
            raw = self._person_records()
            return {arm: compute(raw, site_id=self.session['site_id'], session_id=self.sid, arm=arm,
                                 generation=generation, cutoff=cutoff, window_seconds=(windows or {}).get(arm, 10))
                    if arm == 'b' else None for arm in arms}
        windows = {"a": 10, "b": 10} if windows is None else dict(windows)
        shared, snapshot = dict(shared_context or {}), dict(context_snapshot or {})
        result = {}
        for arm in arms:
            try:
                evidence = causal_evidence(records, site_id=self.session["site_id"], session_id=self.sid, arm=arm,
                                           generation=generation, cutoff=cutoff, window_seconds=windows[arm], retain_history=True)
                context = shared_evidence(shared.get(arm), site_id=self.session["site_id"], session_id=self.sid, arm=arm,
                                          generation=generation, cutoff=cutoff)
                result[arm] = compute(evidence, site_id=self.session["site_id"], session_id=self.sid, arm=arm,
                                      generation=generation, cutoff=cutoff, window_seconds=windows[arm],
                                      shared_context=context, context_snapshot=snapshot.get(arm))
            except Exception:
                result[arm] = None
        return result

    def _track(self, arm, generation):
        """This arm's validity and smoother, restarted for a new generation."""
        track = self.tracks.get(arm)
        if track is None or track["generation"] != generation:
            track = self.tracks[arm] = {"generation": generation, "validity": jev_smoothing.Validity(self.hold_max_s),
                                        "smoother": self._smoother()}
        return track

    def _smoother(self):
        return jev_smoothing.Smoother(**{**jev_smoothing.PARAMETER_SETS[self.smoothing], "hold_max": self.hold_max_s})

    def _validity_receipts(self, db, arm, generation, closures, now):
        # A hold_max end is already the answer's own valid_through: no receipt.
        start = self.session["started_at"]
        for closed in (c for c in closures if c["reason"] != "hold_max"):
            self.store.receipt(db, self.sid, "reasoning.validity", {
                "request_id": closed["request_id"], "arm": arm, "generation": generation, "run_id": "jev" + arm.upper(),
                "rule": jev_smoothing.VALIDITY_RULE, "reason": closed["reason"], "valid_through": closed["valid_through"],
                "valid_through_at": closed["valid_through"] + start, "hold_max_s": self.hold_max_s, "available_at": now})

    def _smoothed(self, db, track, arm, generation, answer, now, *, source, request_id=None):
        smoother = track["smoother"]
        if smoother.previous is not None and answer["t"] < smoother.previous:
            # A ledger clock stepped back: restart the filter, never refuse the answer.
            smoother = track["smoother"] = self._smoother()
        prediction = smoother.update(answer)
        posterior = {k: round(v, 4) for k, v in prediction.pop("posterior").items()}
        shown = prediction.pop("shown")
        prediction = {**prediction, "time_basis": "session_relative_seconds", "mode": "live"}
        self.store.receipt(db, self.sid, "reasoning.smoothed", {
            "arm": arm, "generation": generation, "site_id": self.session["site_id"], "run_id": "jev" + arm.upper() + "-smooth",
            "mode": "live", "source": source, "request_id": request_id, "shown": shown, "posterior": posterior,
            "version": self.smoothing, "available_at": now, "available_at_s": answer["t"], "source_end_s": answer["t"],
            "prediction": prediction})

    def observe_facts(self, arm, *, generation, key, now, vacant=False):
        """One scheduler tick's facts key and occupancy for an arm: ends the
        delivered answer's validity when its facts changed, the room read
        vacant or hold_max passed. Local receipts only."""
        if getattr(self.provider, 'person_mode', False):
            return []  # The existing context reducer owns temporal engagement.
        with self.lock:
            if self.closed:
                return []
            track = self._track(arm, generation)
            closures = track["validity"].observe(now-self.session["started_at"], key=key, vacant=vacant)
            if any(c["reason"] != "hold_max" for c in closures):
                with self.store.transaction() as db:
                    self._validity_receipts(db, arm, generation, closures, now)
            return closures

    def local_abstain(self, arm, *, generation, reason, now):
        """A code gate answered for this arm instead of a JEV request (the room
        is vacant, or occupancy unknown, beyond the hold): an
        insufficient_evidence abstention recorded locally, never sent."""
        if reason not in LOCAL_ABSTAIN_REASONS:
            raise ValueError("uncontrolled local abstention reason")
        if getattr(self.provider, 'person_mode', False):
            with self.lock:
                if self.closed:
                    raise ValueError('reasoning is closed')
                self.store.append_event(self.sid, 'reasoning.person_gate', {
                    'site_id': self.session['site_id'], 'arm': arm, 'generation': generation,
                    'available_at': now, 'status': 'abstain', 'reason': reason,
                    'viewing_only': True, 'physical_permission': False})
            return
        with self.lock:
            if self.closed:
                raise ValueError("reasoning is closed")
            start = self.session["started_at"]
            t = now-start
            track = self._track(arm, generation)
            saved = deepcopy(track)
            vacant = reason == "occupancy_vacant"
            try:
                with self.store.transaction() as db:
                    self.store._event(db, self.sid, "reasoning.gate", {
                        "arm": arm, "generation": generation, "site_id": self.session["site_id"], "run_id": "jev" + arm.upper() + "-gate",
                        "mode": "live", "provider": "code_gate", "status": "abstain", "reason": reason,
                        "choice": "insufficient_evidence", "available_at": now, "available_at_s": t, "source_end_s": t,
                        "prediction": {"time_basis": "session_relative_seconds", "start": t, "end": t+self.hold_max_s,
                                       "available_at": t, "valid_until": t+self.hold_max_s, "activities": [],
                                       "confidence": None, "mode": "live"}})
                    self._validity_receipts(db, arm, generation, track["validity"].local_abstain(t, vacant=vacant), now)
                    # Vacant is a hard no_person emission; unknown is uninformative.
                    answer = {"t": t, "available_at": t, "mode": "live",
                              **({"gate": "no_person"} if vacant else {"choice": "insufficient_evidence", "probabilities": None})}
                    self._smoothed(db, track, arm, generation, answer, now, source="code_gate")
            except Exception:
                self.tracks[arm] = saved
                raise

    def _timeline(self, db, request, output, now):
        """The delivered answer's facts-bound validity and its smoothed step,
        in the output's transaction. Adds fields to the output; it never
        changes the strict (frame TTL) valid_until."""
        arm, generation = request["arm"], request["generation"]
        track = self._track(arm, generation)
        key = self.offer_keys.pop(request["request_id"], None)
        prediction = output["prediction"]
        t = prediction["available_at"]
        closures = track["validity"].deliver(t, request["request_id"], key)
        if key is not None:
            output["facts_key"] = key
            prediction["valid_through"] = t+self.hold_max_s
            output["validity"] = {"rule": jev_smoothing.VALIDITY_RULE, "hold_max_s": self.hold_max_s}
        judgment = output["judgment"]
        answer = {"t": t, "available_at": t, "probabilities": judgment.get("probabilities"), "choice": judgment.get("activity"),
                  "mode": "live"}
        nouls = ((judgment.get("diagnostics") or {}).get("fusion") or {}).get("nouls") or {}
        if isinstance(nouls.get("continues_current_segment"), (int, float)):
            # v7: the stickiness feature for the smoother (plan §5.3).
            answer["continues"] = nouls["continues_current_segment"]
        return track, closures, answer

    def _person_records(self):
        """Bounded raw originals; legacy projections do not admit person facts."""
        with self.store.lock:
            rows = self.store.db.execute("SELECT id,kind,payload,created_at FROM events WHERE session_id=? "
                                         "AND kind IN ('perception.output','qwen.person_observation') ORDER BY id DESC LIMIT 128",
                                         (self.sid,)).fetchall()
        return [dict(id=row['id'], kind=row['kind'], payload=json.loads(row['payload']),
                     created_at=row['created_at'], session_id=self.sid) for row in reversed(rows)]

    def offer(self, records, *, generation, cutoff, windows=None, shared_context=None, arms=("a", "b"), context_snapshot=None,
              facts_keys=None):
        """Pending request per offered arm; `arms` lets the scheduler offer only
        arms with new evidence. Each offered arm replaces only its own pending.
        context_snapshot is the tick's per-arm shared context block (one digest
        for both arms unless a family is the experiment's lever). facts_keys:
        the scheduler's facts key per arm, kept locally (never in the request)
        to bind the answer's facts-bound validity."""
        windows={"a":10,"b":10} if windows is None else dict(windows)
        if set(windows)!={"a","b"} or any(not finite(v) or not 0<=v<=120 for v in windows.values()):
            raise ValueError("bounded per-arm history windows required")
        shared=dict(shared_context or {})
        if set(shared)-{"a","b"}:
            raise ValueError("shared context is per comparison arm")
        snapshot=dict(context_snapshot or {})
        if snapshot and set(snapshot)!={"a","b"}:
            raise ValueError("context snapshot is per comparison arm")
        arms = tuple(arm for arm in ("a", "b") if arm in set(arms))
        if not arms:
            raise ValueError("at least one comparison arm required")
        now = self.store.clock()
        if type(cutoff) not in (int, float) or not now-3 <= cutoff <= now:
            raise ValueError("fresh mapped evidence cutoff required")
        requests, encoded = [], {}
        person_mode = getattr(self.provider, 'person_mode', False)
        raw = self._person_records() if person_mode else None
        for arm in arms:
            frozen = None
            if person_mode:
                if arm != 'b':
                    continue
                frozen = self.provider.freeze_person_context(raw, site_id=self.session['site_id'], session_id=self.sid,
                    arm=arm, generation=generation, cutoff=cutoff)
                if frozen is None:
                    continue
                ids = {frozen['visual_event_id'], frozen['semantics_event_id']}
                evidence = [r for r in raw if r['id'] in ids]
            else:
                evidence = causal_evidence(records, site_id=self.session["site_id"], session_id=self.sid,
                    arm=arm, generation=generation, cutoff=cutoff,window_seconds=windows[arm],retain_history=True)
            request = {"request_id": uuid.uuid4().hex, "site_id": self.session["site_id"],
                "session_id": self.sid, "arm": arm, "generation": generation,
                "cutoff": cutoff, "deadline": cutoff+15, "records": evidence,"window_seconds":windows[arm]}
            if person_mode:
                request['person_context'] = frozen
            # Independent of this arm's window and retained history; strictly valid.
            context = [] if person_mode else shared_evidence(shared.get(arm), site_id=self.session["site_id"], session_id=self.sid,
                arm=arm, generation=generation, cutoff=cutoff)
            bound_loop = (arm == 'b' and not person_mode
                          and self.session.get('request_id', '').startswith('activity-loop-'))
            if bound_loop:
                evidence, context, reading_context = activity_reading.freeze_context(
                    self.store, self.session, evidence, context, cutoff,
                    qwen_source=self.reading_qwen_source)
                request['records'] = evidence
                request['reading_context'] = reading_context
            if context:
                request["shared_context"] = context
            if snapshot and not person_mode and not bound_loop:
                request["context_snapshot"] = snapshot[arm]
            # One canonical encoding per request: budget, admission and row.
            encoded[request["request_id"]] = canonical(request)
            if len(encoded[request["request_id"]].encode()) > 65536:
                raise ValueError("reasoning history exceeds budget")
            requests.append(request)
        with self.lock:
            if self.closed:
                raise ValueError("reasoning is closed")
            with self.store.transaction() as db:
                if self.store.get_session(self.sid)["state"] != "recording" or self._generation(db) != generation:
                    raise ValueError("inactive experiment generation")
                self.store.check_capacity(sum(len(encoded[r["request_id"]].encode()) for r in requests)+65536)
                for req in requests:
                    # Superseded pending rows remain as delivery-coverage evidence.
                    replaced = db.execute("SELECT id,arm,generation FROM lab_reasoning_requests WHERE session_id=? AND arm=? AND state='pending'",
                                          (self.sid, req["arm"])).fetchall()
                    db.execute("UPDATE lab_reasoning_requests SET state='replaced' WHERE session_id=? AND arm=? AND state='pending'", (self.sid, req["arm"]))
                    self._lifecycle(db, replaced, "replaced", "superseded_by_newer_request", replaced_by=req["request_id"])
                    db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?,?,?,?,NULL,NULL)",
                        (req["request_id"], self.sid, req["arm"], generation, "pending", encoded[req["request_id"]]))
            for req in requests:
                key = (facts_keys or {}).get(req["arm"])
                if isinstance(key, str):
                    self.offer_keys[req["request_id"]] = key
            while len(self.offer_keys) > OFFER_KEYS_LIMIT:
                self.offer_keys.pop(next(iter(self.offer_keys)))
            for arm in arms:
                self._pump(arm)
        return [r["request_id"] for r in requests]

    def _pump(self, arm):
        with self.lock:
            if self.closed:
                return
            with self.store.transaction() as db:
                active = db.execute("SELECT 1 FROM lab_reasoning_requests WHERE session_id=? AND arm=? AND state IN ('dispatching','indeterminate')", (self.sid, arm)).fetchone()
                row = db.execute("SELECT * FROM lab_reasoning_requests WHERE session_id=? AND arm=? AND state='pending'", (self.sid, arm)).fetchone()
                if active or not row:
                    return
                req = json.loads(row["payload"])
                now = self.store.clock()
                recording = self.store.get_session(self.sid)["state"] == "recording"
                current = self._generation(db) == req["generation"]
                if req["deadline"] <= now or not current or not recording:
                    # First matching cause, in this fixed order.
                    reason = ("session_not_recording" if not recording else "experiment_generation_changed" if not current
                              else "deadline_passed_before_dispatch")
                    db.execute("UPDATE lab_reasoning_requests SET state='expired' WHERE id=?", (req["request_id"],))
                    self._lifecycle(db, [(req["request_id"], arm, req["generation"])], "expired", reason)
                    return
                last = db.execute("SELECT MAX(dispatched_at) FROM lab_reasoning_requests WHERE session_id=? AND arm=?", (self.sid, arm)).fetchone()[0]
                # One dispatch per second per arm. A clock stepped back (now
                # before the last dispatch) counts as elapsed, never a long wait;
                # the policy's per-minute and per-session budgets still apply.
                delay = max(0, (last+1-now) if last is not None and last <= now else 0)
                if delay:
                    old = self.timers.pop(arm, None)
                    if old:
                        old.cancel()
                    timer = threading.Timer(delay, self._pump, (arm,))
                    timer.daemon = True
                    self.timers[arm] = timer
                    timer.start()
                    return
                db.execute("UPDATE lab_reasoning_requests SET state='dispatching',dispatched_at=? WHERE id=?", (now, req["request_id"]))
            # Persist reservation before the provider can send bytes.
            try:
                future = self.pool.submit(self._execute, req)
                self.futures.add(future)
                future.add_done_callback(self._finished)
            except Exception:
                with self.store.transaction() as db:
                    db.execute("UPDATE lab_reasoning_requests SET state='failed',result=? WHERE id=?", (canonical({"reason":"local_dispatch_failed"}), req["request_id"]))
                    self._lifecycle(db, [(req["request_id"], arm, req["generation"])], "failed", "local_dispatch_failed")
                raise

    def _finished(self, future):
        with self.lock:
            self.futures.discard(future)

    def _execute(self, request):
        try:
            self._complete(request)
        finally:
            self._shadow(request["request_id"])

    def _shadow(self, request_id):
        """v6 shadow beside the v5 wire (provisioned only by managed_jev): run
        after the v5 result is committed and the next dispatch pumped, so it
        never delays v5 delivery. It never sends; a failure is swallowed."""
        shadow = getattr(self.provider, "shadow", None)
        if shadow is None or self.closed:
            return
        try:
            shadow.run(request_id)
        except Exception:
            pass

    def _complete(self, request):
        state = "completed"
        try:
            args = {k: v for k, v in request.items() if k != "records"}
            result = self.provider.evaluate(request["records"], **args)
        except ProviderFailure as error:
            state = "indeterminate" if error.indeterminate else "failed"
            result = {"status": state, "reason": error.reason}
            if getattr(error, "http_status", None) is not None:
                # The provider's status code only, never its body.
                result["http_status"] = error.http_status
        except Exception:
            state, result = "indeterminate", {"status":"indeterminate", "reason":"provider_completion_unknown"}
        with self.lock:
            saved = deepcopy(self.tracks.get(request["arm"]))
            try:
                with self.store.transaction() as db:
                    now = self.store.clock()
                    current = (not self.closed and self._generation(db) == request["generation"]
                        and self.store.get_session(self.sid)["state"] == "recording")
                    output = self._output(request, result, now) if current and state == "completed" else None
                    person_mode = getattr(self.provider, 'person_mode', False)
                    timeline = self._timeline(db, request, output, now) if output and not person_mode else None
                    self.offer_keys.pop(request["request_id"], None)
                    self.store.check_capacity(len(canonical(result).encode())+len(canonical(output).encode())+65536)
                    db.execute("UPDATE lab_reasoning_requests SET state=?,result=? WHERE id=?", (state, canonical(result), request["request_id"]))
                    self.store._event(db, self.sid, "reasoning.delivery", {
                        "request_id":request["request_id"], "arm":request["arm"],
                        "generation":request["generation"], "state":state,
                        "status":result.get("status"), "current":current, "available_at":now})
                    if output:
                        self.store._event(db, self.sid, "reasoning.person_output" if person_mode else "reasoning.output", output)
                        if timeline is not None:
                            track, closures, answer = timeline
                            self._validity_receipts(db, request["arm"], request["generation"], closures, now)
                            self._smoothed(db, track, request["arm"], request["generation"], answer, now, source="jev",
                                           request_id=request["request_id"])
            except Exception:
                # Failed atomic commit leaves dispatching reserved. Never infer
                # completion from a discarded in-memory response after restart.
                if saved is None:
                    self.tracks.pop(request["arm"], None)
                else:
                    self.tracks[request["arm"]] = saved
                return
            self._pump(request["arm"])

    def _output(self, req, result, now):
        if getattr(getattr(self, 'provider', None), 'person_mode', False):
            return self._person_output(req, result, now)
        start = self.session["started_at"]
        visual=[r for r in req['records'] if r.get('source_kind')!='ha.observation' and (r.get('objects') or r.get('pose_instances'))]
        latest=max(visual,key=lambda r:r['observed_at']) if visual else None
        expiry = latest['valid_until'] if latest else req['cutoff']
        observed = latest['observed_at'] if latest else req['cutoff']
        status = result.get("status")
        if status not in {"ready", "abstain"} or now > req["deadline"]:
            return None
        judgment = result["judgment"]
        activity = judgment["activity"]
        rendered = {**judgment, "provider":"JEV", "question":QUESTIONS["activity"]["instructions"],
            "probability":judgment["probabilities"][activity],
            "distribution":[{"label":k, "p":v} for k,v in judgment["probabilities"].items()]}
        if result.get('diagnostics'):
            rendered['diagnostics']=result['diagnostics']
            instructions=result['diagnostics']['request']['questions']['activity']['instructions']
            # v7 instructions are structured ({question, use, rules}): show the question.
            rendered['question']=instructions.get('question') if isinstance(instructions,dict) else instructions
        suggestion = recommendation(judgment, self.policy, current_time=now, valid_until=expiry)
        output = {"request_id":req["request_id"], "site_id":req["site_id"], "arm":req["arm"],
            "generation":req["generation"], "mode":"live", "run_id":"jev"+req["arm"].upper(),
            "status":status if expiry > now else "stale", "provider_status":status,
            "fresh_on_arrival":expiry > now,
            "source_end_s":observed-start, "available_at_s":now-start,
            "valid_until":expiry, "judgment":rendered, "recommendation":suggestion.get("text"),
            "policy_result":suggestion, "provenance":result["provenance"],
            "prediction":{"time_basis":"session_relative_seconds", "start":observed-start,
                "end":expiry-start, "available_at":now-start, "valid_until":expiry-start,
                "activities":[] if status == "abstain" else [activity],
                "confidence":judgment["probabilities"][activity], "mode":"live"}}
        if 'reading_context' in req:
            context = req['reading_context']
            output['reading_context'] = deepcopy(context)
            if context is not None:
                expiry = min(expiry, context['valid_until'])
                output['valid_until'] = expiry
                output['prediction']['valid_until'] = expiry - start
                output['prediction']['end'] = expiry - start
                output['status'] = status if expiry > now else 'stale'
                output['fresh_on_arrival'] = expiry > now
        # The redundant kind IN term names the events_output_time partial index,
        # which serves source_end_s order: a few rows instead of every output.
        row = self.store.db.execute("""SELECT payload FROM events WHERE session_id=?
            AND kind IN ('perception.output','runtime.output','analysis.output')
            AND kind='perception.output' AND json_extract(payload,'$.arm')=?
            AND json_extract(payload,'$.generation')=?
            AND json_extract(payload,'$.source_end_s')<=?
            AND json_extract(payload,'$.available_at')<=?
            ORDER BY json_extract(payload,'$.source_end_s') DESC,id DESC LIMIT 1""",
            (self.sid,req["arm"],req["generation"],observed-start,req["cutoff"])).fetchone()
        if row:
            output["arm_snapshot"] = json.loads(row[0]).get("arm_snapshot")
        return output

    def _person_output(self, req, result, now):
        if result.get('status') not in ('ready', 'abstain') or now > req['deadline']:
            return None
        frozen = req['person_context']
        if result.get('person_context') != frozen:
            return None
        packet = frozen['packet']
        current = self.provider.freeze_person_context(self._person_records(), site_id=self.session['site_id'],
            session_id=self.sid, arm=req['arm'], generation=req['generation'], cutoff=now)
        fresh = current == frozen and now < packet['valid_until']
        return dict(schema='home-lab-person-jev-output-1', version=1, request_id=req['request_id'],
                    site_id=req['site_id'], session_id=self.sid, arm=req['arm'], generation=req['generation'],
                    mode='live', run_id='jev-person-B', status=result['status'] if fresh else 'stale',
                    provider_status=result['status'], fresh_on_arrival=fresh, person_context=deepcopy(frozen),
                    subjects=deepcopy(result['subjects']), provenance=deepcopy(result['provenance']),
                    diagnostics=deepcopy(result.get('diagnostics', {})), observed_at=packet['observed_at'],
                    available_at=now, valid_until=packet['valid_until'],
                    source_end_s=packet['observed_at']-self.session['started_at'],
                    available_at_s=now-self.session['started_at'], viewing_only=True,
                    physical_permission=False, actuation=False)

    def reconcile(self, request_id, terminal_lookup):
        """Trusted, read-only lookup of terminal execution proof; never redispatch.

        The provisioned lookup must bind its receipt to the exact persisted
        request digest. It is not a timeout, cancellation attempt, or a model
        response alone. No public HTTP endpoint or provider lookup is assumed.
        Recovery records delivery evidence only: it cannot establish the original
        publication grant or deliver a late answer into current context.
        """
        with self.lock:
            if self.futures:
                return {"state":"blocked","reason":"local_reasoning_still_active"}
            with self.store.lock:
                row=self.store.db.execute("SELECT * FROM lab_reasoning_requests WHERE id=? AND session_id=?",
                    (request_id,self.sid)).fetchone()
            if row is None:
                raise ValueError("unknown session reasoning request")
            if row["state"] not in {"dispatching","indeterminate"}:
                return {"state":row["state"],"reconciled":False,"resumed":False}
            request=json.loads(row["payload"])
            reference={"request_id":request_id,"session_id":self.sid,"site_id":self.session["site_id"],
                "arm":row["arm"],"generation":row["generation"],"request_sha256":digest(request)}
            try:
                proof=terminal_lookup(dict(reference))
                if (not isinstance(proof,dict) or any(proof.get(k)!=v for k,v in reference.items())
                        or proof.get("terminal") is not True or proof.get("state") not in {"completed","failed","cancelled"}
                        or not isinstance(proof.get("receipt_id"),str) or not 1<=len(proof["receipt_id"])<=256):
                    raise ValueError("matching terminal proof required")
                # Only bounded identifiers and terminal status are retained. A
                # lookup may not inject judgments, private response bodies or
                # credentials into a recovery receipt.
                receipt={**reference,"state":proof["state"],"receipt_id":proof["receipt_id"],
                    "terminal":True,"status":"reconciled_without_publication"}
                with self.store.transaction() as db:
                    self.store.check_capacity(len(canonical(receipt).encode())+65536)
                    changed=db.execute("UPDATE lab_reasoning_requests SET state=?,result=? WHERE id=? AND session_id=? AND state=?",
                        (proof["state"],canonical(receipt),request_id,self.sid,row["state"])).rowcount
                    if changed!=1: raise ValueError("reasoning reservation changed")
                    self.store._event(db,self.sid,"reasoning.reconciled",{
                        **receipt,"available_at":self.store.clock(),"current":False,"resumed":False})
            except Exception:
                return {"state":"indeterminate","reason":"provider_completion_unproven","resumed":False}
            # A later normal offer may dispatch fresh evidence. The recovery
            # operation itself never starts pending or replacement work.
            return {"state":proof["state"],"reconciled":True,"resumed":False}

    def close(self, reason="reasoning_closed"):
        if reason not in self.CLOSE_REASONS:
            reason = "reasoning_closed"
        with self.lock:
            self.closed = True
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()
            self.offer_keys.clear()
            with self.store.transaction() as db:
                pending = db.execute("SELECT id,arm,generation FROM lab_reasoning_requests WHERE session_id=? AND state='pending'", (self.sid,)).fetchall()
                db.execute("UPDATE lab_reasoning_requests SET state='cancelled' WHERE session_id=? AND state='pending'", (self.sid,))
                self._lifecycle(db, pending, "cancelled", reason)
            self.pool.shutdown(wait=False, cancel_futures=False)
