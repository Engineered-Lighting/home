"""Sequential saved-session analysis through a supplied common admission executor.

This is orchestration, not a model server. No model is loaded or contacted here
unless an explicitly provisioned executor is called by run().

Step states in lab_analysis_steps:
- dispatching: reserved before any byte is sent; a restart makes it indeterminate.
- indeterminate: sent with an unproven outcome. Every later dispatch waits for an
  identity-only reconciliation; nothing is resent.
- completed: matching terminal completion proof; analysis.output is published.
- failed: matching terminal non-completed proof (rejected, failed, cancelled,
  queue_expired, not_admitted). The reservation is released, no judgment exists.
- not_dispatched (transient): typed proof that the request never reached
  admission. The reservation row is removed, since no copy left Victoria; the
  job result keeps the reason.
- reused: a later job's step whose identical window (same inputs and pinned
  model, see window_key) already has a completed step. Its judgment is
  republished under the new job with reused_from, and nothing is sent.
- ended_unknown: an authenticated lookup reports a newer engine epoch and no
  record of the step. The recorded epoch ended, so the step cannot still be
  running, but whether it executed stays unknown. No judgment is inferred.
"""
import json
import re
import threading
from reasoning import canonical, digest, finite, qwen_windows, causal_evidence
from analysis_result import SCHEMA_VERSION, render_result
from experiment_history import history, at_cutoff

RUNS = ("qBase", "qA", "qB")
# Recognised but unimplemented comparisons stay explicit refusals, never guesses.
UNSUPPORTED_RUNS = {"qJev": "qwen_assisted_jev_not_implemented"}
TERMINAL_FAILED = frozenset({"rejected", "failed", "cancelled", "queue_expired", "not_admitted"})
_REASON = re.compile(r"[a-z0-9_]{1,64}")


class AnalysisRejected(ValueError):
    """A known refusal before any request exists; never an execution outcome."""
    def __init__(self, reason, detail=None):
        super().__init__(detail or reason)
        self.reason = reason


class NotDispatched(ValueError):
    """Proof that a step never reached admission (local check or typed refusal)."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class EngineEpochEnded(ValueError):
    """Authenticated lookup: the recorded engine epoch ended with no record of the step."""
    def __init__(self, engine_epoch):
        super().__init__("analysis_engine_epoch_ended")
        self.engine_epoch = engine_epoch


def window_key(request):
    """Attempt-independent identity of one window's inputs and pinned model.

    request_id binds the job, so each LA request is one attempt and a refused
    window can be asked again by a new job. This key leaves out the job and the
    engine instance (which rotates on every LA engine restart), so a new job
    reuses a proven completion instead of resending it.
    """
    value={k:v for k,v in request.items() if k not in {"job_id","request_id"}}
    value["profile"]={k:v for k,v in request["profile"].items() if k!="engine_epoch"}
    return digest(value)


def _stamp(request, profile):
    """Bind a planned request to the engine epoch it will be sent to."""
    value={k:v for k,v in request.items() if k!="request_id"}
    value["profile"]=dict(profile)
    value["request_id"]=digest(value)
    return value


class SequentialAnalysis:
    def __init__(self, store, profile, executor):
        if not profile.get("engine_epoch") or not profile.get("model_version") or not re.fullmatch(r"[a-f0-9]{64}",profile.get("artifact_sha256","")):
            raise ValueError("pinned admitted analysis engine required")
        self.store,self.profile,self.executor=store,dict(profile),executor
        self.lock=threading.Lock()
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS lab_analysis_steps(
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, engine TEXT NOT NULL,
                state TEXT NOT NULL, request TEXT NOT NULL, receipt TEXT)""")
            if "window_key" not in {r[1] for r in db.execute("PRAGMA table_info(lab_analysis_steps)")}:
                db.execute("ALTER TABLE lab_analysis_steps ADD COLUMN window_key TEXT")
            db.execute("UPDATE lab_analysis_steps SET state='indeterminate' WHERE state='dispatching'")
            db.execute("CREATE INDEX IF NOT EXISTS analysis_completed_window ON lab_analysis_steps(window_key) WHERE state='completed'")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS analysis_one_engine ON lab_analysis_steps(engine) WHERE state IN ('dispatching','indeterminate')")
            db.execute("CREATE TABLE IF NOT EXISTS lab_analysis_manifests(job_id TEXT PRIMARY KEY, digest TEXT NOT NULL)")

    def plan(self, job, frames, observations):
        session=self.store.get_session(job["session_id"])
        if session["state"] != "stopped":
            raise AnalysisRejected("analysis_requires_finalized_recording")
        parameters=job["parameters"]
        if set(parameters)-{"run_ids","mode","artifact_ids"} or parameters.get("artifact_ids"):
            raise AnalysisRejected("analysis_parameters_invalid","analysis accepts curated runs, not caller model paths")
        runs=parameters.get("run_ids") or list(RUNS)
        unsupported=[UNSUPPORTED_RUNS[r] for r in runs if isinstance(r,str) and r in UNSUPPORTED_RUNS] if isinstance(runs,list) else []
        if unsupported:
            raise AnalysisRejected(unsupported[0])
        if not isinstance(runs,list) or not runs or any(not isinstance(r,str) for r in runs) or len(runs)!=len(set(runs)) or set(runs)-set(RUNS):
            raise AnalysisRejected("invalid_analysis_runs","invalid analysis arms")
        mode=parameters.get("mode") or "causal"
        if mode not in {"causal","retrospective"}:
            raise AnalysisRejected("invalid_analysis_mode")
        start=session["started_at"]
        duration=session["ended_at"]-start
        selected=[]
        if len(frames)>9000 or len(observations)>100000:
            raise AnalysisRejected("analysis_input_unbounded","select a bounded analysis input set")
        try:
            changes,history_kind=history(self.store,session,observations)
        except ValueError as error:
            raise AnalysisRejected("experiment_history_invalid",str(error)) from None
        for frame in frames:
            if (frame.get("site_id")!=session["site_id"] or frame.get("session_id")!=session["id"]
                    or not finite(frame.get("observed_at")) or not finite(frame.get("available_at"))):
                raise AnalysisRejected("invalid_source_frame")
            self.store.get_media(session["id"],frame["media_id"])
            selected.append({k:frame[k] for k in ("media_id","observed_at","available_at")})
        requests=[]
        from evidence_policy import ha_policy, filter_ha
        for run in [r for r in RUNS if r in runs]:
            for window in qwen_windows(duration):
                cutoff=start+window["cutoff"]
                activation,next_activation=at_cutoff(changes,session,cutoff)
                inclusion=ha_policy(activation.get('ha_evidence') if activation else None)
                # Saved runs compare specialist evidence only. Every run of a
                # window gets identical HA, and only when the recorded
                # experiment admitted HA to both arms (an ablation withholds it).
                include_ha=inclusion['a'] and inclusion['b']
                candidates=[f for f in selected if start+window["start"]<=f["observed_at"]<=cutoff and (mode=="retrospective" or f["available_at"]<=cutoff)]
                sampled={}
                for target in window["sample_times"]:
                    if candidates:
                        frame=min(candidates,key=lambda f:abs(f["observed_at"]-start-target))
                        sampled[frame["media_id"]]=frame
                if not sampled:
                    raise AnalysisRejected("analysis_window_without_frames","analysis window has no eligible captured frames")
                # Shared HA facts have no comparison arm. Copy only the HA field
                # and causal provenance, never labels or arbitrary event text.
                shared_ha=[]
                for record in observations:
                    if (record.get("kind") in {None,"observation"} and record.get("arm") is None
                            and record.get("site_id")==session["site_id"] and record.get("session_id")==session["id"]
                            and isinstance(record.get("ha"),dict)
                            and all(finite(record.get(k)) for k in ("observed_at","available_at","valid_until"))
                            and start+window["start"]<=record["observed_at"]<=cutoff<=record["valid_until"]
                            and (mode=="retrospective" or record["available_at"]<=cutoff)):
                        if include_ha:
                            shared_ha.append({k:record[k] for k in ("ha","observed_at","available_at","valid_until","id","evidence_roots") if k in record})
                evidence=[]
                evidence_window=None
                evidence_window_source="not_applicable"
                if run != "qBase":
                    arm="a" if run=="qA" else "b"
                    if activation is not None:
                        recorded=activation.get("reasoning_windows")
                        evidence_window=min(recorded[arm] if recorded is not None else window["cutoff"]-window["start"],cutoff-activation["effective_at"])
                        evidence_window_source="recorded_activation" if recorded is not None else "legacy_analysis_window"
                        eligible=[r for r in observations if finite(r.get("observed_at")) and r["observed_at"]>=activation["effective_at"]]
                        evidence=causal_evidence(eligible,site_id=session["site_id"],session_id=session["id"],arm=arm,
                            generation=activation["generation"],cutoff=cutoff,
                            window_seconds=evidence_window,
                            retrospective=mode=="retrospective")
                        evidence=filter_ha(evidence,{"a":include_ha,"b":include_ha})
                request={"job_id":job["id"],"session_id":session["id"],"site_id":session["site_id"],"run_id":run,"mode":mode,
                         "window":window,"frames":list(sampled.values()),"ha":shared_ha,"evidence":evidence,"profile":self.profile,
                         "result_schema":SCHEMA_VERSION,"judgment_end_s":min(duration,window["cutoff"]+5,next_activation-start),
                         "generation":activation["generation"] if activation else 0,
                         "experiment_id":activation.get("experiment_id") if activation else None,
                         "evidence_window_seconds":evidence_window,"evidence_window_source":evidence_window_source,
                         "experiment_history_kind":history_kind}
                request['ha_evidence_included']=include_ha
                request['ha_evidence_policy']={"recorded":inclusion,"applied_to_every_run":include_ha}
                if len(canonical(request))>128*1024:
                    raise AnalysisRejected("analysis_window_exceeds_bound","analysis window exceeds bound")
                request["request_id"]=digest(request)
                requests.append(request)
        return requests

    def _output(self, request, **fields):
        return {"run_id":request["run_id"],"job_id":request["job_id"],"mode":request["mode"],
            "generation":request.get("generation"),"experiment_id":request.get("experiment_id"),
            "experiment_history_kind":request.get("experiment_history_kind","legacy_unverified"),
            "evidence_window_seconds":request.get("evidence_window_seconds"),
            "evidence_window_source":request.get("evidence_window_source","legacy_analysis_window"),
            "source_start_s":request["window"]["start"],"source_end_s":request["window"]["cutoff"],
            "request_id":request["request_id"],"profile":request["profile"],
            "processing_completed_at":self.store.clock(),"live_latency":None,**fields}

    def _complete(self, request, receipt, expected_state, reference=None):
        """Publish a matching terminal proof; returns the step's terminal state."""
        profile=request["profile"]
        if (not isinstance(receipt,dict) or receipt.get("request_id")!=request["request_id"] or receipt.get("terminal") is not True
                or len(canonical(receipt))>128*1024):
            raise ValueError("matching terminal proof required")
        state=receipt.get("state")
        if state=="completed":
            if receipt.get("engine_epoch")!=profile["engine_epoch"] or receipt.get("artifact_sha256")!=profile["artifact_sha256"]:
                raise ValueError("matching completion proof required")
            step="completed"
            payload=self._output(request,**{"status":"completed","result":receipt.get("result"),"processing":receipt.get("processing"),
                                 **render_result(request,receipt.get("result"))})
        elif state in TERMINAL_FAILED:
            reason=receipt.get("reason")
            if reason is not None and (not isinstance(reason,str) or not _REASON.fullmatch(reason)):
                raise ValueError("bounded terminal reason required")
            # Terminal without an answer: the reservation is released and the
            # window keeps an explicit gap instead of an inferred judgment.
            step="failed"
            payload=self._output(request,status="failed",failure={"state":state,"reason":reason},
                                 processing=receipt.get("processing"))
        else:
            raise ValueError("matching terminal proof required")
        stored={**receipt,"dispatch":reference} if reference else receipt
        with self.store.transaction() as db:
            self.store.check_capacity(len(canonical(stored))+65536)
            changed=db.execute("UPDATE lab_analysis_steps SET state=?,receipt=? WHERE id=? AND state=?",
                (step,canonical(stored),request["request_id"],expected_state)).rowcount
            if changed!=1:
                raise ValueError("analysis reservation changed")
            self.store._event(db,request["session_id"],"analysis.output",payload)
            if expected_state=="indeterminate":
                self.store._event(db,request["session_id"],"analysis.reconciled",{
                    "job_id":request["job_id"],"request_id":request["request_id"],"engine_epoch":profile["engine_epoch"],"outcome":step})
        return step

    def _ended(self, request, reference, current_epoch):
        recorded=request["profile"]["engine_epoch"]
        if not isinstance(current_epoch,str) or not current_epoch or current_epoch==recorded:
            raise ValueError("a different current engine epoch is required")
        with self.store.transaction() as db:
            changed=db.execute("UPDATE lab_analysis_steps SET state='ended_unknown',receipt=? WHERE id=? AND state='indeterminate'",
                (canonical({"dispatch":reference,"engine_epoch_ended":recorded,"current_engine_epoch":current_epoch}),request["request_id"])).rowcount
            if changed!=1:
                raise ValueError("analysis reservation changed")
            self.store._event(db,request["session_id"],"analysis.output",self._output(request,status="unknown",
                failure={"state":"engine_epoch_ended","reason":"outcome_unknown"}))
            self.store._event(db,request["session_id"],"analysis.reconciled",{
                "job_id":request["job_id"],"request_id":request["request_id"],"engine_epoch":recorded,
                "current_engine_epoch":current_epoch,"outcome":"ended_unknown"})
        return "ended_unknown"

    def _reuse(self, request):
        """Republish another job's proven completion of this exact window."""
        key=window_key(request)
        with self.store.transaction() as db:
            row=db.execute("SELECT id,job_id,request,receipt FROM lab_analysis_steps WHERE window_key=? AND state='completed' ORDER BY rowid LIMIT 1",
                           (key,)).fetchone()
            if row is None:
                return False
            source,receipt=json.loads(row[2]),json.loads(row[3])
            if window_key(source)!=key or receipt.get("state")!="completed":
                return False
            reused={"job_id":row[1],"request_id":row[0]}
            # The judgment keeps the profile (and engine epoch) that produced it.
            payload=self._output(request,**{"status":"completed","result":receipt.get("result"),"processing":receipt.get("processing"),
                                 **render_result(request,receipt.get("result")),"profile":source["profile"],"reused_from":reused})
            self.store.check_capacity(len(canonical(request))+65536)
            db.execute("INSERT INTO lab_analysis_steps(id,job_id,engine,state,request,receipt,window_key) VALUES(?,?,?,'reused',?,?,?)",
                       (request["request_id"],request["job_id"],request["profile"]["engine_epoch"],canonical(request),
                        canonical({"reused_from":reused}),key))
            self.store._event(db,request["session_id"],"analysis.output",payload)
        return True

    def uncertain_jobs(self):
        """Analysis jobs holding an unproven step or an unreconciled job state."""
        with self.store.lock:
            steps={r[0] for r in self.store.db.execute("SELECT DISTINCT job_id FROM lab_analysis_steps WHERE state='indeterminate'")}
            jobs={r[0] for r in self.store.db.execute("SELECT id FROM jobs WHERE kind='analysis' AND state='indeterminate'")}
        return sorted(steps|jobs)

    def uncertain_steps(self, job_id=None):
        with self.store.lock:
            return self.store.db.execute("SELECT COUNT(*) FROM lab_analysis_steps WHERE state IN ('dispatching','indeterminate') AND (? IS NULL OR job_id=?)",
                                         (job_id,job_id)).fetchone()[0]

    def reconcile(self, job_id, lookup):
        """Server-provisioned terminal lookup; never dispatch, cancel or restart.

        lookup(request, reference) receives the step exactly as recorded, with
        its recorded profile and dispatch reference (identifiers and digests,
        never the inference body). An engine restart (EngineEpochEnded) resolves
        to ended_unknown. Resolving a step never resumes unsent analysis.
        """
        job=self.store.get_job(job_id)
        if job["kind"]!="analysis":
            raise ValueError("analysis job required")
        if not self.lock.acquire(blocking=False):
            return {"state":"blocked","reason":"analysis_engine_busy"}
        try:
            with self.store.lock:
                rows=self.store.db.execute("SELECT request,receipt FROM lab_analysis_steps WHERE job_id=? AND state='indeterminate'",(job_id,)).fetchall()
            if not rows:
                return {"state":"reconciled","steps":0,"resumed":False,"outcomes":{}}
            outcomes={}
            for row in rows:
                request=json.loads(row[0])
                if request["request_id"]!=digest({k:v for k,v in request.items() if k!="request_id"}):
                    return {"state":"indeterminate","reason":"analysis_manifest_changed"}
                stored=json.loads(row[1]) if row[1] else None
                reference=stored.get("dispatch") if isinstance(stored,dict) else None
                try:
                    try:
                        outcome=self._complete(request,lookup(json.loads(canonical(request)),reference),"indeterminate",reference)
                    except EngineEpochEnded as ended:
                        outcome=self._ended(request,reference,ended.engine_epoch)
                except Exception:
                    return {"state":"indeterminate","reason":"analysis_completion_unproven"}
                outcomes[outcome]=outcomes.get(outcome,0)+1
            return {"state":"reconciled","steps":len(rows),"resumed":False,"outcomes":outcomes}
        finally:
            self.lock.release()

    def _set_step(self, request_id, state, receipt):
        # A failed write leaves the row 'dispatching', which restart and the
        # constructor turn into indeterminate: never a released reservation.
        with self.store.transaction() as db:
            db.execute("UPDATE lab_analysis_steps SET state=?,receipt=? WHERE id=? AND state='dispatching'",
                       (state,canonical(receipt) if receipt is not None else None,request_id))

    def _release(self, request_id):
        # Typed proof that nothing reached admission: no external copy exists,
        # so no step row remains to hold erasure (the job keeps the reason).
        with self.store.transaction() as db:
            db.execute("DELETE FROM lab_analysis_steps WHERE id=? AND state='dispatching'",(request_id,))

    def run(self, job, frames, observations, *, cancelled=lambda:False, gate=lambda:None, progress=lambda _value:None):
        """Execute unsent steps in order; returns a job result, never raises for known outcomes.

        Every result carries dispatched (whether any step of this job reached
        admission), steps_total, steps_done and reused. gate() returning a reason
        stops before the next step (a recording or preparation started);
        cancelled() stops between terminal steps. progress(summary) is called
        per sent step. A window another job already completed is reused, not sent.
        """
        try:
            requests=self.plan(job,frames,observations)
        except Exception as error:
            # Nothing exists to dispatch: a known refusal, never uncertainty.
            return {"state":"failed","reason":getattr(error,"reason","analysis_plan_invalid"),"dispatched":False}
        if not self.lock.acquire(blocking=False):
            return {"state":"blocked","reason":"analysis_engine_busy"}
        try:
            with self.store.lock:
                states=dict(self.store.db.execute("SELECT id,state FROM lab_analysis_steps WHERE job_id=?",(job["id"],)).fetchall())
            summary={"steps_total":len(requests),"steps_done":0,
                     "dispatched":any(s not in {"reused","not_dispatched"} for s in states.values()),"reused":0}
            current=getattr(self.executor,"current_profile",None)
            if callable(current) and requests:
                # The engine instance rotates on every LA restart; model and
                # artifact stay pinned. An authenticated identity-only probe
                # names the epoch these requests are bound to.
                try:
                    profile=current(json.loads(canonical(requests[0])))
                except Exception as error:
                    return {"state":"failed","reason":"analysis_step_not_dispatched",
                            "detail":getattr(error,"reason","qwen_engine_epoch_unavailable"),**summary}
                if profile!=self.profile:
                    requests=[_stamp(r,profile) for r in requests]
            fingerprint=digest(requests)
            with self.store.transaction() as db:
                prior=db.execute("SELECT digest FROM lab_analysis_manifests WHERE job_id=?",(job["id"],)).fetchone()
                if not prior:
                    db.execute("INSERT INTO lab_analysis_manifests VALUES(?,?)",(job["id"],fingerprint))
            if prior and prior[0]!=fingerprint:
                return {"state":"failed","reason":"analysis_inputs_changed",**summary}
            prepare=getattr(self.executor,"prepare",None)
            send=getattr(self.executor,"send",None)
            for request in requests:
                if cancelled():
                    return {"state":"cancelled","reason":"cancelled_between_terminal_steps",**summary}
                blocked=gate()
                if blocked:
                    return {"state":"interrupted","reason":blocked,"resumed":False,**summary}
                step=states.get(request["request_id"])
                if step in {"completed","reused"}:
                    summary["steps_done"]+=1
                    continue
                if step in {"dispatching","indeterminate"}:
                    return {"state":"indeterminate","reason":"analysis_completion_unproven",**summary}
                if step is not None:
                    return {"state":"failed","reason":"analysis_step_"+step,**summary}
                if self._reuse(request):
                    summary["steps_done"]+=1
                    summary["reused"]+=1
                    continue
                progress({**summary,"run_id":request["run_id"],"window_cutoff_s":request["window"]["cutoff"]})
                envelope=None
                if callable(prepare) and callable(send):
                    # Local media and envelope checks precede any reservation.
                    try:
                        envelope=prepare(json.loads(canonical(request)))
                    except Exception as error:
                        return {"state":"failed","reason":"analysis_step_not_dispatched",
                                "detail":getattr(error,"reason","analysis_step_preparation_failed"),"run_id":request["run_id"],**summary}
                reference=None if envelope is None else {k:v for k,v in envelope.items() if k!="body"}
                with self.store.transaction() as db:
                    busy=db.execute("SELECT job_id FROM lab_analysis_steps WHERE state IN ('dispatching','indeterminate') LIMIT 1").fetchone()
                    if busy:
                        # One admitted request at a time; another job's
                        # unproven step must be reconciled before this one.
                        if busy[0]==job["id"]:
                            return {"state":"indeterminate","reason":"analysis_completion_unproven",**summary}
                        return {"state":"failed","reason":"analysis_reconciliation_pending",**summary}
                    db.execute("INSERT INTO lab_analysis_steps(id,job_id,engine,state,request,receipt,window_key) VALUES(?,?,?,'dispatching',?,?,?)",
                        (request["request_id"],job["id"],request["profile"]["engine_epoch"],canonical(request),
                         canonical({"dispatch":reference}) if reference else None,window_key(request)))
                try:
                    receipt=send(envelope) if envelope is not None else self.executor(json.loads(canonical(request)))
                except NotDispatched as error:
                    self._release(request["request_id"])
                    return {"state":"failed","reason":"analysis_step_not_dispatched","detail":error.reason,"run_id":request["run_id"],**summary}
                except Exception:
                    summary["dispatched"]=True
                    self._set_step(request["request_id"],"indeterminate",{"dispatch":reference} if reference else None)
                    return {"state":"indeterminate","reason":"analysis_completion_unproven",**summary}
                summary["dispatched"]=True
                try:
                    outcome=self._complete(request,receipt,"dispatching",reference)
                except Exception:
                    self._set_step(request["request_id"],"indeterminate",{"dispatch":reference} if reference else None)
                    return {"state":"indeterminate","reason":"analysis_completion_unproven",**summary}
                if outcome!="completed":
                    return {"state":"failed","reason":"analysis_admission_"+receipt["state"],"detail":receipt.get("reason"),
                            "run_id":request["run_id"],**summary}
                summary["steps_done"]+=1
            progress(dict(summary))
            return {"state":"completed","steps":len(requests),**summary}
        finally:
            self.lock.release()
