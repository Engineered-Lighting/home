"""Framework-independent orchestration; runtime absence always blocks execution."""
from pathlib import Path
import json
import logging
import re
import threading
import time
import controller_load
from artifacts import ArtifactRegistry
from recorder import SampleRecorder
from store import LabError, identifier


# Background storage reconciliation cadence (off the request/write path).
STORAGE_RECONCILE_INTERVAL_S=60.0
# Automatic per-activity results of finished recordings (lab_scoring), also
# off the request path: one session per pass, a pass at most this often.
AUTO_RESULTS_INTERVAL_S=15.0
_log=logging.getLogger("perception_lab.controller")


JEV_METRICS=("coverage","exact_match_at_coverage","false_confident_seconds","covered_seconds","reviewed_seconds")
JEV_METRIC_SUFFIX={"strict":"","valid_facts":"_valid_facts","smoothed":"_smoothed"}


def jev_metrics(scored,gates=None):
    """Live JEV per arm and generation, three separately named views:
    strict (frame-TTL validity, the comparable metric), valid_facts (answers
    held while their facts key is unchanged, up to hold_max) and smoothed (the
    code-owned HMM/hysteresis timeline, run jevX-smooth)."""
    rows={}
    for row in scored:
        if row["execution"]!="live" or row["mode"]!="live" or not re.fullmatch(r"jev[AB](-smooth)?",row["run_id"]):
            continue
        slot=rows.setdefault((row["arm"],row["generation"]),{"arm":row["arm"],"generation":row["generation"]})
        views=({"smoothed":row["metrics"]} if row.get("metric_kind")=="smoothed"
               else {"strict":row["metrics"],**({"valid_facts":row["metrics_valid_facts"]} if "metrics_valid_facts" in row else {})})
        for view,metrics in views.items():
            suffix=JEV_METRIC_SUFFIX[view]
            for name in JEV_METRICS:
                slot[name+suffix]=metrics.get(name)
            slot["per_activity"+suffix]={k:{m:v.get(m) for m in ("coverage","exact_match_at_coverage","false_confident_seconds")}
                                         for k,v in (metrics.get("per_activity") or {}).items()}
    for (arm,generation),slot in rows.items():
        slot["local_abstains"]=dict((gates or {}).get((arm,generation),{}))
    return sorted(rows.values(),key=lambda r:(str(r["arm"]),r["generation"] if type(r["generation"]) is int else -1))


class Controller:
    def __init__(self,store,runtime=None,*,load_monitor=None,monotonic=time.monotonic):
        self.store,self.runtime=store,runtime
        self.artifacts=ArtifactRegistry(store)
        self.recorder=SampleRecorder(store)
        self.commands=threading.RLock()
        self._load=load_monitor
        self.monotonic=monotonic
        # The Store reconciled at startup; the next background pass is due later.
        self._last_reconcile=monotonic()
        self._last_load=monotonic()
        self._accounting=dict(getattr(store,"accounting",None) or {})
        # Content-free counters of expiry-loop steps that raised (by step).
        self.upkeep_failures={"expiry":0,"reconcile":0,"load":0}
        self._last_results=monotonic()
        self.results_failures=0

    @property
    def load(self):
        return self._load if self._load is not None else controller_load.MONITOR

    def capabilities(self):
        experiments=[]
        catalog=Path(__file__).with_name("experiments.json")
        if catalog.exists():
            raw=json.loads(catalog.read_text(encoding="utf-8"))
            experiments=raw.get("experiments",[]) if isinstance(raw,dict) else raw
        runtime={}
        if self.runtime is not None:
            try:
                runtime=self.runtime.capabilities()
            except Exception:
                runtime={"gates":[{"id":"runtime","state":"blocked","reason":"runtime_status_unavailable"}]}
        result={"version":1,"site_id":self.store.settings.site_id,"cameras":list(self.store.settings.cameras),"devices":list(self.store.settings.devices),"artifacts":self.artifacts.list(),"experiments":experiments,"gates":[],"fixture":self.store.settings.developer_fixture_mode,"recording_allowed":runtime.get("recording_allowed") is True}
        result["annotation_time_bases"]=["session","encoded_video"]
        if runtime.get('worker_mode') == 'on_demand_bounded':
            result['worker_mode'] = 'on_demand_bounded'
        for field in ("cameras","devices","artifacts","experiments","gates","reasoning","last_worker_cleanup","apartment_viewing"):
            if field in runtime:
                result[field]=runtime[field]
        if self.runtime is None:
            result["gates"].append({"id":"runtime","state":"blocked","reason":"runtime_not_configured"})
        result["storage"]={"used_bytes":self.store.storage_usage(),"quota_bytes":self.store.settings.quota_bytes,"auto_delete":False,"policy":"keep_within_quota","max_session_seconds":self.store.settings.max_session_seconds}
        return result

    def _invoke(self,method,*args):
        if self.runtime is None or not callable(getattr(self.runtime,method,None)):
            return {"state":"blocked","reason":"runtime_not_configured"}
        try:
            result=getattr(self.runtime,method)(*args)
            if not isinstance(result,dict):
                return {"state":"blocked","reason":"invalid_runtime_response"}
            return result
        except Exception:
            return {"state":"indeterminate","reason":"runtime_execution_uncertain"}

    def start(self,body):
        with self.commands:
            cameras={c.get("id",c.get("camera_id")) for c in self.capabilities()["cameras"]}
            if body["camera_id"] not in cameras:
                raise LabError("camera is not registered at this site",422,"unknown_camera")
            experiments={e.get("id",e.get("experiment_id")) for e in self.capabilities()["experiments"]}
            if experiments and body["experiment_id"] not in experiments:
                raise LabError("unknown experiment",422,"unknown_experiment")
            if body.get("reasoning_enabled") is True and not self.capabilities().get("reasoning",{}).get("available"):
                raise LabError("JEV is not provisioned for this Lab",409,"jev_not_provisioned")
            session,created=self.store.create_session(body["camera_id"],body["experiment_id"],body["name"],body["request_id"],body.get("reasoning_enabled",False))
            if not created:
                return session
            self.store.transition(session["id"],"starting")
            outcome=self._invoke("start_session",self.store.get_session(session["id"]))
            self.store.append_event(session["id"],"runtime.start_result",outcome)
            state=outcome.get("state")
            if state == 'starting' and getattr(self.runtime, 'deferred_start', False) is True:
                # Managed preparation owns the eventual transition. It may have
                # already completed before this response is serialized.
                return self.store.get_session(session['id'])
            if state not in {"recording","blocked","failed","indeterminate"}:
                state="indeterminate"
            return self.store.transition(session["id"],state,outcome.get("reason"))

    def stop(self,sid,reason="user_stop"):
        with self.commands:
            session=self.store.get_session(sid)
            if session["state"] in {"stopped","failed","interrupted"}:
                # The recording may end before its workers are released or its
                # local work drains. Stop then retries only that cleanup.
                pending=getattr(self.runtime,"cleanup_pending",None)
                if callable(pending) and pending(sid) is True:
                    self.store.append_event(sid,"runtime.stop_result",self._invoke("stop_session",session),critical=True)
                return session
            if session["state"] in {"requested","blocked"}:
                return self.store.transition(sid,"stopped",reason)
            if session["state"] in {"starting","recording"} and isinstance(reason,str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}",reason):
                # Durable, content-free: why this stop was requested. A recorder
                # that sees 'stopping' before its own stop signal finalizes
                # with this reason, not as an unexplained state change.
                self.store.append_event(sid,"recording.stop_requested",{"reason":reason},critical=True)
            outcome=self._invoke("stop_session",session)
            self.store.append_event(sid,"runtime.stop_result",outcome,critical=True)
            # Asynchronous cleanup can finish between reading and transitioning.
            # Keep the terminal-state check and transition under the ledger lock.
            with self.store.lock:
                current=self.store.get_session(sid)
                if current["state"] in {"stopped","failed","interrupted"}:
                    return current
                if outcome.get("state")=="stopped":
                    return self.store.transition(sid,"stopped",reason)
                if current["state"]!="stopping":
                    return self.store.transition(sid,"stopping",outcome.get("reason","stop_not_confirmed"))
                return current

    def delete(self,sid,expected_revision,*,erase_records=False):
        with self.commands:
            session=self.store.get_session(sid)
            if self.runtime is not None:
                handler=getattr(self.runtime,'delete_session',None)
                if not callable(handler):
                    raise LabError('runtime deletion reconciliation unavailable',409,'execution_pending')
                return handler(session,expected_revision,erase_records=erase_records)
            return self.store.delete_session(sid,expected_revision,erase_records=erase_records)

    def switch(self,sid,body):
        if "reasoning_enabled" in body:
            if set(body)!={"request_id","reasoning_enabled","expected_reasoning_revision"} or type(body["reasoning_enabled"]) is not bool:
                raise LabError("exact reasoning switch required",422,"invalid_reasoning")
        elif "experiment_id" in body:
            if "arm" in body or "profile_id" in body:
                raise LabError("choose experiment switch or single-arm switch",422,"invalid_switch")
            identifier(body["experiment_id"])
        else:
            if body.get("arm") not in {"A","B"}:
                raise LabError("arm must be A or B",422,"invalid_arm")
            identifier(body.get("profile_id"))
        identifier(body.get("request_id"))
        with self.commands:
            session=self.store.get_session(sid)
            previous=self.store.command_result(sid,body["request_id"],body)
            if previous is not None:
                return previous
            if session["state"]!="recording":
                raise LabError("recording must be active before switching")
            if "reasoning_enabled" in body:
                if body["expected_reasoning_revision"]!=session["reasoning_revision"]:
                    raise LabError("reasoning setting changed",409,"revision_conflict")
                outcome=self._invoke("set_reasoning",session,body["reasoning_enabled"])
            else:
                outcome=self._invoke("switch",session,body)
            result={"session_id":sid,"requested":body,"outcome":outcome,"event_id":self.store.append_event(sid,"pipeline.switch",{"request":body,"outcome":outcome})}
            self.store.command_result(sid,body["request_id"],body,result)
            return result

    def bookmark(self,sid,body,actor):
        from recorder import bounded_number
        at=bounded_number(body.get("at_ms"),"bookmark time")
        if at>self.store.settings.max_session_seconds*1000 or not isinstance(body.get("label"),str) or not 1<=len(body["label"])<=500:
            raise LabError("invalid bookmark",422,"invalid_bookmark")
        identifier(body.get("request_id"))
        with self.commands:
            self.store.get_session(sid)
            previous=self.store.command_result(sid,body["request_id"],body)
            if previous:
                return previous
            result={"id":self.store.append_event(sid,"bookmark",{**body,"actor":actor}),"session_id":sid,**body}
            self.store.command_result(sid,body["request_id"],body,result)
            return result

    def job(self,body):
        with self.commands:
            job,created=self.store.create_job(body["kind"],body["request_id"],body.get("parameters",{}),body.get("session_id"))
            if not created:
                return job
            self.store.update_job(job["id"],"starting")
            outcome=self._invoke("run_job",self.store.get_job(job["id"]))
            state=outcome.get("state","blocked")
            if state not in {"blocked","queued","running","completed","failed","indeterminate"}:
                state="blocked"
            return self.store.update_job(job["id"],state,outcome,outcome.get("reason"),expected_state="starting")

    def cancel_job(self,jid):
        with self.commands:
            job=self.store.get_job(jid)
            if job["state"] in {"completed","cancelled","failed","interrupted"}:
                return job
            if job["state"] in {"requested","blocked"}:
                return self.store.update_job(jid,"cancelled")
            outcome=self._invoke("cancel_job",job)
            current=self.store.get_job(jid)
            if current["state"] in {"completed","cancelled","failed","interrupted"}:
                return current
            if outcome.get("state")!="cancelled":
                return self.store.update_job(jid,"indeterminate",outcome,"cancellation_not_confirmed",expected_state=current["state"])
            return self.store.update_job(jid,"cancelled",outcome,expected_state=current["state"])

    def detail(self,sid,at_ms=None):
        events=self.store.list_events(sid)
        with self.store.lock:
            finalization=self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind IN ('recording.finalized','recording.indeterminate') ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
        capture_status=json.loads(finalization[0]) if finalization else None
        from room_evidence import room_at
        session=self.store.get_session(sid)
        room=room_at(self.store,session,session['started_at']+at_ms/1000) if at_ms is not None and session['started_at'] is not None else None
        timeline=self.store.timeline_events(sid)
        outputs=[e for e in events if e["kind"] in {"perception.output","runtime.output","analysis.output","reasoning.output"}]
        if at_ms is not None:
            outputs=self.store.outputs_at(sid,at_ms)
        return {"version":1,"site_id":self.store.settings.site_id,"session":self.store.get_session(sid),"capture_status":capture_status,"room":room,"events":events,"timeline_events":timeline,"events_after_id":events[-1]["id"] if events else 0,"annotations":self.store.list_annotations(sid),"runs":self.store.list_jobs(sid),"outputs":outputs,"results":outputs,"media":self.store.list_media(sid)}

    def graph(self,sid,at_ms=None):
        # Recorded stage status at a cursor for the graph UI; never inference.
        from graph import graph
        with self.load.timed("graph_handler_ms"):
            return graph(self.store,sid,at_ms)

    def timeline(self,sid,from_ms=None,to_ms=None):
        from graph import timeline
        return timeline(self.store,sid,from_ms,to_ms)

    def replay(self,sid,at_ms):
        # The replay window needs only causal outputs and room, not full detail.
        from graph import replay
        return replay(self.store,sid,at_ms)

    def findings(self,sid):
        from reasoning import score_intervals
        from delivery_metrics import specialist_delivery
        session=self.store.get_session(sid)
        annotations=[]
        for item in self.store.list_annotations(sid):
            # Video-relative labels remain useful training/review evidence, but
            # must not be compared with session-clock predictions implicitly.
            if item.get("time_basis","session")!="session":
                continue
            if item.get("reviewed") is not True or item.get("origin")!="human" or item.get("end_ms") is None or item["end_ms"]<=item["at_ms"]:
                continue
            annotations.append({"start":item["at_ms"]/1000,"end":item["end_ms"]/1000,"activities":[item["label"]],"reviewed":True,"source":"human"})
        groups={}
        events=[]
        cursor=0
        while True:
            page=self.store.list_events(sid,after_id=cursor,limit=5000)
            if not page:
                break
            events.extend(page)
            cursor=page[-1]["id"]
            if len(events)>100000:
                raise LabError("explicit analysis job required for oversized event history",413,"history_limit")
        # Facts-bound validity closures of live JEV answers (reasoning.validity):
        # the earliest recorded end per request.
        closers={}
        gates={}
        for event in events:
            value=event["payload"]
            if event["kind"]=="reasoning.validity" and isinstance(value,dict) and isinstance(value.get("request_id"),str):
                through=value.get("valid_through")
                if type(through) in (int,float):
                    closers[value["request_id"]]=min(through,closers.get(value["request_id"],through))
            elif event["kind"]=="reasoning.gate" and isinstance(value,dict):
                slot=gates.setdefault((str(value.get("arm","unknown")),value.get("generation")),{})
                reason=str(value.get("reason"))
                slot[reason]=slot.get(reason,0)+1
        for event in events:
            # reasoning.smoothed is the code-owned timeline, scored as its own run.
            if event["kind"] not in {"perception.output","analysis.output","reasoning.output","reasoning.smoothed"}:
                continue
            value=event["payload"]
            # A typed prediction is required; observations/proximity are never activity truth.
            prediction=value.get("prediction")
            if not isinstance(prediction,dict) or prediction.get("time_basis")!="session_relative_seconds":
                continue
            # Causality describes the permitted evidence, not execution time.
            # Distinct offline jobs must never pool their predictions silently.
            execution="offline" if event["kind"]=="analysis.output" else "live"
            key=(str(value.get("run_id","live")),str(value.get("arm","unknown")),str(value.get("mode","live")),value.get("generation"),value.get("job_id"),execution)
            groups.setdefault(key,[]).append((event["kind"],value.get("request_id"),prediction))
        from jev_smoothing import facts_bound
        scored=[]
        activations=[e['payload'] for e in events if e['kind']=='experiment.activated']
        for (run_id,arm,mode,generation,job_id,execution),items in groups.items():
            if mode=="diagnostic":
                continue
            kinds={kind for kind,_,_ in items}
            predictions=[p for _,_,p in items]
            # The facts-bound view of live JEV answers (plan §5.2): a second,
            # separately named metric; the strict frame-TTL metric is unchanged.
            valid_facts=(facts_bound([(rid,p) for _,rid,p in items],closers)
                         if kinds=={"reasoning.output"} and execution=="live" else None)

            def clamp(predictions):
                if execution=='live' and activations:
                    current=next((a for a in activations if a.get('generation')==generation),None)
                    if current is None:
                        return []
                    from reasoning import finite
                    begin=current.get('effective_at');start=session.get('started_at')
                    ends=[a.get('effective_at') for a in activations if type(a.get('generation')) is int and a['generation']>generation]
                    if not finite(begin) or not finite(start) or any(not finite(t) for t in ends):
                        return []
                    end=min(ends)-start if ends else float('inf')
                    return [{**p,'start':max(p['start'],begin-start),'end':min(p['end'],end),
                        'valid_until':min(p.get('valid_until',p['end']),end)} for p in predictions
                        if finite(p.get('start')) and finite(p.get('end')) and finite(p.get('valid_until',p.get('end')))]
                return predictions
            run_mode="live" if execution=="live" and mode=="live" else "offline"
            row={"run_id":run_id,"arm":arm,"mode":mode,"generation":generation,"job_id":job_id,"execution":execution,
                 "metrics":score_intervals(annotations,clamp(predictions),run_mode=run_mode)}
            if kinds=={"reasoning.smoothed"}:
                row["metric_kind"]="smoothed"
            elif valid_facts is not None:
                row["metric_kind"]="raw"
                row["metrics_valid_facts"]=score_intervals(annotations,clamp(valid_facts),run_mode=run_mode)
            scored.append(row)
        # The one lever each experiment generation varied, as recorded at
        # activation. Older activations did not record it: lever is null.
        levers=[{"generation":a.get("generation"),"experiment_id":a.get("experiment_id"),
                 "lever":a.get("lever") if isinstance(a.get("lever"),dict) else None,
                 "baseline_sha256":a.get("baseline_sha256") if isinstance(a.get("baseline_sha256"),str) else None}
                for a in activations if isinstance(a,dict)]
        from lab_scoring import latest_results
        return {"session_id":sid,"site_id":self.store.settings.site_id,"status":"scored" if any(row["metrics"]["status"]=="provisional" for row in scored) else "insufficient_reviewed_data","runs":scored,"jev_metrics":jev_metrics(scored,gates),"specialist_delivery":specialist_delivery(self.store,sid),"reviewed_intervals":len(annotations),"levers":levers,"source":"stored_human_reviews_and_typed_predictions",
                # Automatic results stored after the recording stopped (draft
                # labels from bookmarks, unreviewed); separate from the scores above.
                "auto_results":latest_results(self.store,sid)}

    def live(self,sid):
        with self.load.timed("live_handler_ms"):
            return self._live(sid)

    def _live(self,sid):
        load=self.load
        session=self.store.get_session(sid)
        with load.timed("live_runtime_ms"):
            result=self._invoke("live",session)
        from room_evidence import room_at
        if "arms" not in result:
            result={"arms":{},"frames":[],"room":None,"state":"unavailable","reason":result.get("reason","no_live_evidence")}
        with load.timed("live_room_ms"):
            result['room']=room_at(self.store,session,self.store.clock()) if session['state']=='recording' else None
        # Latest content-free controller load sample (see controller.load).
        result['controller_load']=load.live_view()
        # Live polling must not download the growing event/media history just
        # to discover recording state. Re-read after runtime access: Stop may
        # have completed while the snapshot was being assembled.
        session=self.store.get_session(sid)
        if session['state']!='recording':
            result['arms']={}
        elif result.get('arms'):
            from qwen_person_live import attach_person_semantics
            result=attach_person_semantics(self.store,session,result,self.store.clock())
            from jev_person_view import attach_person_judgments
            result=attach_person_judgments(self.store,session,result,self.store.clock())
        # Controller epoch seconds, for display age against browser receipt time.
        result.setdefault("server_time",self.store.clock())
        return {**result,"session":session,"site_id":session["site_id"],"session_id":sid,"revision":session["revision"]}

    def _upkeep_failed(self,step,error):
        # Exception class only: never a message, path or payload.
        self.upkeep_failures[step]+=1
        _log.warning("perception-lab controller upkeep failed: step=%s error_class=%s failures=%d",
                     step,type(error).__name__,self.upkeep_failures[step])

    def expire(self):
        """Once a second on the expiry thread. Each step is guarded, so one
        error cannot stop duration-limit enforcement, reconciliation or load
        receipts, nor end the expiry loop itself."""
        try:
            # Only recording sessions: never every session's record each second.
            for session in self.store.recording_sessions():
                try:
                    if self.store.clock()-session["started_at"]>=self.store.settings.max_session_seconds:
                        self.stop(session["id"],"duration_limit")
                except Exception as error:
                    self._upkeep_failed("expiry",error)
        except Exception as error:
            self._upkeep_failed("expiry",error)
        if self.runtime is not None and callable(getattr(self.runtime,"reap_finished_sessions",None)):
            self._invoke("reap_finished_sessions")
        self.maintenance()

    def maintenance(self):
        """Background upkeep on the expiry thread, never on a request path:
        the periodic exact storage reconciliation and the load receipt."""
        now=self.monotonic()
        if now-self._last_reconcile>=STORAGE_RECONCILE_INTERVAL_S:
            self._last_reconcile=now
            try:
                self.store.reconcile_storage("interval")
            except Exception as error:
                # The Store keeps admission refused until a scan succeeds.
                self._upkeep_failed("reconcile",error)
        if now-self._last_load>=controller_load.LOAD_INTERVAL_S:
            self._last_load=now
            try:
                self.record_load(self.store.recording_sessions())
            except Exception as error:
                self._upkeep_failed("load",error)
        if now-self._last_results>=AUTO_RESULTS_INTERVAL_S:
            self._last_results=now
            self.auto_results()

    def auto_results(self):
        """After a recording stops (by any route: user, duration limit,
        failure), store its per-activity results with draft labels from its
        bookmarks as one local event. Never raises into the expiry loop."""
        if not hasattr(self.store,"db"):
            return []
        from lab_scoring import pending_sessions,record_results
        done=[]
        try:
            for sid in pending_sessions(self.store):
                record_results(self.store,sid)
                done.append(sid)
        except Exception as error:
            self.results_failures+=1
            _log.warning("perception-lab automatic results failed: error_class=%s failures=%d",
                         type(error).__name__,self.results_failures)
        return done

    def record_load(self,recording):
        """Sample the interval; while recording, one bounded receipt per session."""
        summary=self.load.sample()
        # Storage accounting in the same interval: capacity checks answered
        # from the running total, full scans, drift corrections, scan failures.
        stats=dict(getattr(self.store,"accounting",None) or {})
        previous,self._accounting=self._accounting,stats
        summary={**summary,"storage":{**{k:stats.get(k,0)-previous.get(k,0) for k in ("checks","full_scans","drift_corrections","scan_failures")},
                                      "last_scan_ms":stats.get("last_scan_ms"),"unowned_entries":stats.get("unowned_entries")},
                 "upkeep_failures":dict(self.upkeep_failures)}
        if not recording:
            return summary
        payload={**summary,"available_at":self.store.clock()}
        for session in recording:
            try:
                self.store.append_event(session["id"],"controller.load",payload)
            except Exception:
                pass
        return summary
