"""Bounded background analysis. Navigation never owns this worker's lifetime.

One worker thread serializes analysis runs, identity-only reconciliation and
operator maintenance, so at most one admitted analysis request is ever active.
"""
from concurrent.futures import ThreadPoolExecutor, wait
import threading


class AnalysisJobs:
    def __init__(self, store, analysis, input_loader, *, kind="analysis", lookup=None, gate=None, stop=None):
        self.store,self.analysis,self.input_loader=store,analysis,input_loader
        if kind not in {"analysis","preparation"}:raise ValueError("unsupported worker kind")
        self.kind=kind
        # lookup(request, reference) -> terminal proof; identifiers only.
        self.lookup=lookup
        # gate() -> None or a reason that stops analysis before its next step.
        self.gate=gate or (lambda:None)
        # stop() ends in-flight network waits (the transport's close) when a
        # bounded close did not drain; the step stays unproven for reconcile.
        self.stop=stop
        self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="lab-"+kind)
        self.work={}
        self.maintenance=set()
        self.closed=False

    def submit(self, job):
        if job["kind"] != self.kind:
            return {"state":"blocked","reason":"job_handler_not_provisioned"}
        with self.lock:
            if self.closed:
                return {"state":"blocked","reason":self.kind+"_dispatch_closed"}
            self.work={jid:entry for jid,entry in self.work.items() if not entry[0].done()}
            if job["id"] in self.work:
                return {"state":self.store.get_job(job["id"])["state"]}
            if sum(not f.done() for f,_ in self.work.values())>=2:
                return {"state":"blocked","reason":self.kind+"_queue_full"}
            # Durable queued state is written before the worker can finish. The
            # controller's conditional update cannot overwrite a newer result.
            self.store.update_job(job["id"],"queued")
            cancellation=threading.Event()
            future=self.pool.submit(self._run,job,cancellation)
            self.work[job["id"]]=(future,cancellation)
            return {"state":"queued"}

    def _progress(self, job, value):
        try:
            self.store.update_job(job["id"],"running",{"progress":value},expected_state="running")
        except Exception:
            pass  # Progress is advisory; the terminal job result is authoritative.

    def _run(self, job, cancellation):
        try:
            if cancellation.is_set():
                self.store.update_job(job["id"],"cancelled")
                return
            blocked=self.gate() if self.kind=="analysis" else None
            if blocked:
                self.store.update_job(job["id"],"interrupted",{"state":"interrupted","reason":blocked,
                    "dispatched":False,"resumed":False},blocked)
                return
            self.store.update_job(job["id"],"running")
            if self.kind=="analysis":
                # Resolve earlier unproven steps by identity lookup first; this
                # never resends them. Unresolved ones still block dispatch.
                self._reconcile_pending()
            # Loader is server-provisioned and only receives a stored job. It
            # must resolve registered source IDs, never accept arbitrary URLs.
            try:
                frames,observations=self.input_loader(job)
            except Exception:
                # This loader only reads registered inputs; the execution path
                # below has not been called. Missing media/clock mapping is a
                # known failure, not an unknown remote reservation.
                self.store.update_job(job["id"],"failed",{
                    "state":"failed","reason":self.kind+"_inputs_unavailable","dispatched":False},
                    self.kind+"_inputs_unavailable")
                return
            if self.kind=="analysis":
                result=self.analysis.run(job,frames,observations,cancelled=cancellation.is_set,gate=self.gate,
                                         progress=lambda value:self._progress(job,value))
            else:
                result=self.analysis.run(job,frames,observations,cancelled=cancellation.is_set)
        except Exception:
            # Unknown exceptions may follow dispatch. Preserve uncertainty;
            # do not expose provider errors or silently retry.
            result={"state":"indeterminate","reason":self.kind+"_worker_uncertain"}
        self.store.update_job(job["id"],result["state"],result,result.get("reason"))

    def cancel(self, job):
        with self.lock:
            current=self.store.get_job(job["id"])
            if current["state"] in {"completed","cancelled","failed"}:
                return {"state":current["state"]}
            entry=self.work.get(job["id"])
            if not entry:
                return {"state":"indeterminate","reason":"analysis_worker_not_reconciled"}
            future,cancellation=entry
            cancellation.set()
            if future.cancel():
                self.store.update_job(job["id"],"cancelled")
                return {"state":"cancelled"}
            # Cooperative cancellation cannot prove an in-flight request ended.
            return {"state":"indeterminate","reason":"waiting_for_terminal_analysis_step"}

    def active(self, session_id=None):
        """Whether queued or running work exists, optionally for one session."""
        with self.lock:
            running=[jid for jid,(future,_) in self.work.items() if not future.done()]
            busy=any(not f.done() for f in self.maintenance)
        if session_id is None:
            return bool(running) or busy
        return any(self.store.get_job(jid).get("session_id")==session_id for jid in running)

    def close(self, timeout=0):
        """Cancel queued work, signal running work and report whether it drained.

        An in-flight admitted step is never abandoned as finished: the caller
        keeps the store open until the worker exits (drained False). When a
        bounded wait expires, stop() ends the transport's polling so the worker
        exits soon, leaving the step indeterminate for identity reconciliation.
        """
        with self.lock:
            self.closed=True
            for jid,(future,cancellation) in self.work.items():
                cancellation.set()
                if future.cancel():
                    self.store.update_job(jid,"cancelled")
            for future in self.maintenance:
                future.cancel()  # Queued startup lookups; nothing was sent.
            futures=[f for f,_ in self.work.values()]+list(self.maintenance)
            self.pool.shutdown(wait=False,cancel_futures=False)
        if futures and timeout:
            wait(futures,timeout=max(0,min(timeout,60)))
            if not all(f.done() for f in futures) and callable(self.stop):
                try:
                    self.stop()
                except Exception:
                    pass
        return all(f.done() for f in futures)

    def _reconcile(self, job_id, lookup):
        job=self.store.get_job(job_id)
        result=self.analysis.reconcile(job_id,lookup)
        # A step still reserved (for example a failed local write) keeps the
        # job unresolved; restart turns it indeterminate for a later lookup.
        if result["state"]!="reconciled" or job["state"]!="indeterminate" or self.analysis.uncertain_steps(job_id):
            return result
        outcomes=result.get("outcomes") or {}
        if "ended_unknown" in outcomes or "failed" in outcomes:
            reason="analysis_engine_epoch_ended" if "ended_unknown" in outcomes else "analysis_step_failed"
            self.store.update_job(job_id,"failed",{**result,"dispatched":True},reason,expected_state="indeterminate")
        else:
            # Resume is explicit: a new analysis job for the session reuses
            # every completed window and sends only the rest.
            self.store.update_job(job_id,"interrupted",result,"completion_recovered_explicit_resume_required",expected_state="indeterminate")
        return result

    def _reconcile_pending(self):
        if self.lookup is None or self.kind!="analysis":
            return {}
        results={}
        for job_id in self.analysis.uncertain_jobs():
            try:
                results[job_id]=self._reconcile(job_id,self.lookup)
            except Exception:
                results[job_id]={"state":"indeterminate","reason":"analysis_completion_unproven"}
        return results

    def start_reconcile(self):
        """Controller-start recovery: identity lookups on the analysis worker."""
        with self.lock:
            if self.closed or self.lookup is None or self.kind!="analysis":
                return False
            future=self.pool.submit(self._reconcile_pending)
            self.maintenance={f for f in self.maintenance if not f.done()}|{future}
            return True

    def submit_maintenance(self, job, target=None):
        """Operator hook: a stored diagnostic job reconciles by identity lookup only."""
        with self.lock:
            if self.closed:
                return {"state":"blocked","reason":self.kind+"_dispatch_closed"}
            if self.lookup is None or self.kind!="analysis":
                return {"state":"blocked","reason":"analysis_lookup_not_provisioned"}
            self.work={jid:entry for jid,entry in self.work.items() if not entry[0].done()}
            if job["id"] in self.work:
                return {"state":self.store.get_job(job["id"])["state"]}
            if sum(not f.done() for f,_ in self.work.values())>=2:
                return {"state":"blocked","reason":self.kind+"_queue_full"}
            self.store.update_job(job["id"],"queued")
            future=self.pool.submit(self._maintain,job,target)
            self.work[job["id"]]=(future,threading.Event())
            return {"state":"queued"}

    def _maintain(self, job, target):
        try:
            self.store.update_job(job["id"],"running")
            if target is None:
                results=self._reconcile_pending()
            else:
                current=self.store.get_job(target)
                if current["kind"]!="analysis":
                    raise ValueError("analysis job required")
                results={target:self._reconcile(target,self.lookup)}
            result={"state":"completed","operation":"analysis_reconcile","results":results,"resumed":False}
        except Exception:
            # Lookups never dispatch, so a failed maintenance run creates no
            # new uncertainty; the target keeps its previous state.
            result={"state":"failed","reason":"analysis_reconcile_failed","operation":"analysis_reconcile"}
        self.store.update_job(job["id"],result["state"],result,result.get("reason"))

    def reconcile(self, job_id, terminal_lookup=None):
        """Provisioned maintenance path. Never resume work as a side effect."""
        lookup=terminal_lookup or self.lookup
        with self.lock:
            entry=self.work.get(job_id)
            if entry and not entry[0].done():
                return {"state":"blocked","reason":"analysis_worker_still_active"}
        job=self.store.get_job(job_id)
        if self.kind!="analysis" or lookup is None or (job["state"]!="indeterminate" and job_id not in self.analysis.uncertain_jobs()):
            return {"state":"blocked","reason":"no_uncertain_analysis_job"}
        return self._reconcile(job_id,lookup)
