"""Bounded paired execution using an explicitly supplied common admission executor.

This module never loads a model or contacts a host by itself. The executor must
own admission/dispatch and return an epoch-bound completion receipt. Expiry or a
lost response is not completion; uncertain requests block the affected arm.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import re
import threading
import time
from presentation import arm_snapshot
from view_person_association import ViewPersonAssociator
from activity_reading import SoleReaderBank


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


# Arm evidence validity (rule arm-sampling-1). A source frame claims validity
# until observed + 3 s minus its clock uncertainty U (the causal preview:
# about 2.1 s). That claim budgets for a consumer of every source frame: the
# next frame is due one source interval later and its result a latency after
# that. An arm that samples below the source rate waits one sampling period
# instead of one source interval for its next frame (its next sampled frame is
# the first source frame at or after the period), so each of its results stays
# current for exactly that much longer: the source's validity plus (sampling
# period - source interval). For the live preview and a 1 Hz arm that is
# observed + 2.6 s at U = 0.9 s, i.e. the next sample (due within 1.5 s) plus a
# latency budget of 2.5 s - U (about 1.6 s; measured observed-to-result p99 is
# about 1.1 s). Never past observed + ARM_VALIDITY_CAP_S (the wire's 3 s 'now'
# horizon and fresh_under_3s age bucket) unless the source itself claims more,
# and never shorter than the source's claim. Unsampled arms and arms at the
# source rate keep the source's validity unchanged.
SOURCE_FRAME_INTERVAL_S = 0.5  # the recording-owned preview pump's 2 Hz grid
ARM_VALIDITY_CAP_S = 3.0
ARM_VALIDITY_RULE = "arm-sampling-1"


def original_frame(request):
    """The offered frame. A development preview request carries a longer
    dispatch deadline (its LA ledger records that one); strict readers use this."""
    timing = request.get("preview_timing")
    return {**request["frame"], "valid_until": timing["original_valid_until"]} if timing else request["frame"]


def arm_valid_until(frame, sampling_hz=None):
    """The time an arm's result for this frame stops describing the present."""
    source = frame["valid_until"]
    if sampling_hz is None:
        return source
    extension = max(0.0, 1/sampling_hz - SOURCE_FRAME_INTERVAL_S)
    return max(source, min(source + extension, frame["observed_at"] + ARM_VALIDITY_CAP_S))


class CompletionUnproven(ValueError):
    """Execution may have started. Offers identity-only lookups, never a resend."""
    def __init__(self, lookup=None):
        super().__init__("remote_specialist_completion_unproven")
        self.lookup = lookup


class LookupNotPerformed(ValueError):
    """The identity lookup itself provably never ran (its ingress was busy)."""


class PairedRunner:
    """One active and one replaceable pending frame per arm, durable dispatch IDs.

    Offered input is shared; availability, drops and completion remain per arm.
    An executor receives metadata and registered media IDs, never arbitrary URLs.
    No synthetic observation is generated when an executor or receipt fails.
    """

    def __init__(self, store, session_id, profiles, executor, *, clock=time.time, experiment_generation=None, sampling_hz=None, evidence_ready=None):
        self.evidence_ready=evidence_ready
        self.sampling_hz=None if sampling_hz is None else dict(sampling_hz)
        if self.sampling_hz is not None and (set(self.sampling_hz)!={"a","b"} or any(not _finite(v) or not 0<v<=30 for v in self.sampling_hz.values())):
            raise ValueError("bounded per-arm source sampling rates required")
        if set(profiles) != {"a", "b"}:
            raise ValueError("exactly two profiles required")
        for profile in profiles.values():
            if (not isinstance(profile,dict) or not isinstance(profile.get("artifact_sha256"),str)
                    or not re.fullmatch(r"[a-f0-9]{64}", profile["artifact_sha256"])
                    or any(not isinstance(profile.get(k),str) or not 1<=len(profile[k])<=256 for k in ("model_version","engine_epoch","profile_id"))
                    or ("device_id" in profile and not isinstance(profile["device_id"],str))):
                raise ValueError("pinned artifact and admitted engine epoch required")
        if not callable(executor):
            raise ValueError("common admission executor required")
        self.store, self.session_id, self.profiles = store, session_id, json.loads(_json(profiles))
        self.executor, self.clock = executor, clock
        self.session = store.get_session(session_id)
        self.generation = self.session["generation"] if experiment_generation is None else experiment_generation
        self.lock = threading.RLock()
        self.active = {"a": None, "b": None}
        self.pending = {"a": None, "b": None}
        self.closed = False
        self.view_person_associations = {arm: ViewPersonAssociator() for arm in ('a', 'b')}
        self.reading_sources = {arm: SoleReaderBank() for arm in ('a', 'b')}
        self.stopping = threading.Event()
        self.reconcile_delay = 1.5
        self.lookup_attempts = 5
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lab-paired")
        self.futures = set()
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paired_sampling(
                session_id TEXT NOT NULL,generation INTEGER NOT NULL,arm TEXT NOT NULL,
                capture_epoch TEXT NOT NULL,observed_at REAL NOT NULL,
                PRIMARY KEY(session_id,generation,arm,capture_epoch))""")
            db.execute("""CREATE TABLE IF NOT EXISTS paired_requests(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL,
                generation INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, updated_at REAL NOT NULL)""")
            db.execute("""CREATE INDEX IF NOT EXISTS paired_requests_arm_state
                ON paired_requests(session_id,arm,generation,state,updated_at)""")
            # Dispatch time lives beside the ledger: paired_requests keeps its
            # shape, so a rolled-back controller's positional insert still works.
            # Rows orphaned by an erasure under such a controller are swept here.
            db.execute("""CREATE TABLE IF NOT EXISTS paired_dispatch(
                request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL,
                generation INTEGER NOT NULL, dispatched_at REAL NOT NULL)""")
            db.execute("""CREATE INDEX IF NOT EXISTS paired_dispatch_arm
                ON paired_dispatch(session_id,arm,generation,dispatched_at)""")
            db.execute("DELETE FROM paired_dispatch WHERE session_id IN (SELECT session_id FROM session_erasures WHERE state='completed')")
            # A previous dispatcher may have sent bytes. No heartbeat retry.
            db.execute("UPDATE paired_requests SET state='indeterminate' WHERE session_id=? AND state IN ('dispatching','running')", (session_id,))
            rows = db.execute("SELECT arm,id FROM paired_requests WHERE session_id=? AND state='indeterminate'", (session_id,)).fetchall()
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS paired_one_active_arm
                ON paired_requests(session_id,arm)
                WHERE state IN ('dispatching','running','indeterminate')""")
        for row in rows:
            self.active[row[0]] = row[1]

    def _event(self, kind, payload):
        self.store.append_event(self.session_id, kind, payload)

    def offer(self, frame):
        required = ("frame_id", "capture_epoch", "site_id", "camera_id", "session_id", "observed_at", "available_at", "valid_until", "media_ids")
        if any(k not in frame for k in required):
            raise ValueError("frame provenance is incomplete")
        if any(frame[k] != self.session[k] for k in ("site_id", "camera_id")) or frame["session_id"] != self.session_id:
            raise ValueError("frame belongs to another source")
        if not all(_finite(frame[k]) for k in ("observed_at", "available_at", "valid_until")):
            raise ValueError("finite source and availability times required")
        if frame["observed_at"] > self.clock() or frame["available_at"] > self.clock() or frame["valid_until"] < frame["observed_at"]:
            raise ValueError("invalid frame time interval")
        if not isinstance(frame["media_ids"], list) or not 1 <= len(frame["media_ids"]) <= 8:
            raise ValueError("bounded registered media references required")
        for media_id in frame["media_ids"]:
            from apartment_viewing_source import registered_media
            registered_media(self.store,self.session_id,media_id)
        # The source cannot smuggle fetch instructions or private label fields.
        frame = json.loads(_json({key: frame[key] for key in required}))
        # Development preview timing (owner decision, 2026-10-19): only the viewing
        # owner's own metric PNG, arm B only, gets a longer dispatch deadline so the
        # unchanged dispatch and LA checks pass. Arm A and strict readers keep the original.
        from apartment_viewing_source import development_preview
        hold = development_preview(self.store, self.session_id, frame["media_ids"])
        if len(_json(frame)) > 8192:
            raise ValueError("frame envelope too large")
        # One exact storage scan covers this frame's small ledger writes: up to
        # offered, dropped and cancelled/expired events per arm.
        with self.lock, self.store.capacity_batch(6*(65536+1024)):
            if self.closed:
                raise RuntimeError("runner closed")
            for arm in ("a", "b"):
                request = {"frame": frame, "arm": arm, "generation": self.generation,
                           "profile": self.profiles[arm], "session_id": self.session_id}
                if self.sampling_hz is not None:
                    request["sampling_hz"]=self.sampling_hz[arm]
                if hold and arm == "b":
                    request["frame"] = {**frame, "valid_until": max(frame["valid_until"], frame["available_at"] + hold)}
                    request["preview_timing"] = {"policy": "development", "original_valid_until": frame["valid_until"], "hold_s": hold}
                identity = [self.session_id, self.generation, arm, frame["capture_epoch"], frame["frame_id"]]
                request_id = hashlib.sha256(_json(identity).encode()).hexdigest()
                request["request_id"] = request_id
                encoded = _json(request)
                content_digest = hashlib.sha256(encoded.encode()).hexdigest()
                with self.store.transaction() as db:
                    previous = db.execute("SELECT digest FROM paired_requests WHERE id=?", (request_id,)).fetchone()
                    if previous:
                        if previous[0] != content_digest:
                            raise ValueError("frame identity reused with changed content")
                        continue
                    db.execute("INSERT INTO paired_requests(id,session_id,arm,generation,digest,payload,state,result,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               (request_id,self.session_id,arm,self.generation,content_digest,encoded,"offered",None,self.clock()))
                self._event("perception.offered", {"arm": arm, "frame_id": frame["frame_id"], "request_id": request_id, "generation": self.generation})
                if self.sampling_hz is not None:
                    with self.store.transaction() as db:
                        key=(self.session_id,self.generation,arm,frame["capture_epoch"])
                        prior=db.execute("SELECT observed_at FROM paired_sampling WHERE session_id=? AND generation=? AND arm=? AND capture_epoch=?",key).fetchone()
                        sampled=prior is None or frame["observed_at"]-prior[0]>=1/self.sampling_hz[arm]-1e-9
                        if sampled:
                            db.execute("INSERT INTO paired_sampling VALUES(?,?,?,?,?) ON CONFLICT(session_id,generation,arm,capture_epoch) DO UPDATE SET observed_at=excluded.observed_at",(*key,frame["observed_at"]))
                    if not sampled:
                        self._terminal_local(request,"dropped","source_sampling_cadence")
                        continue
                if self.pending[arm] is not None:
                    self._terminal_local(self.pending[arm], "dropped", "replaced_by_latest")
                self.pending[arm] = request
                self._dispatch(arm)

    def _terminal_local(self, request, state, reason):
        with self.store.transaction() as db:
            db.execute("UPDATE paired_requests SET state=?,result=?,updated_at=? WHERE id=? AND state='offered'",
                       (state,_json({"reason":reason}),self.clock(),request["request_id"]))
        self._event("perception."+state, {"request_id":request["request_id"],"arm":request["arm"],"reason":reason})
        from apartment_viewing_source import prune
        prune(self.store,self.session_id)

    def _dispatch(self, arm):
        if self.closed or self.active[arm] is not None or self.pending[arm] is None:
            return
        request = self.pending[arm]
        if self.store.get_session(self.session_id)["state"]!="recording":
            self._terminal_local(request,"cancelled","recording_not_active")
            self.pending[arm]=None
            return
        if arm_valid_until(request["frame"], request.get("sampling_hz")) < self.clock():
            self._terminal_local(request,"expired","expired_before_dispatch")
            self.pending[arm] = None
            return
        # Commit before executor may send bytes. Global resource reservations are
        # owned by that executor, not guessed from local idle-memory observations.
        with self.store.transaction() as db:
            occupied = db.execute("SELECT id FROM paired_requests WHERE session_id=? AND arm=? AND state IN ('dispatching','running','indeterminate')", (self.session_id,arm)).fetchone()
            if occupied:
                self.active[arm] = occupied[0]
                return
            now = self.clock()
            db.execute("UPDATE paired_requests SET state='dispatching',updated_at=? WHERE id=?", (now,request["request_id"]))
            db.execute("""INSERT INTO paired_dispatch(request_id,session_id,arm,generation,dispatched_at) VALUES(?,?,?,?,?)
                ON CONFLICT(request_id) DO NOTHING""", (request["request_id"],self.session_id,arm,request["generation"],now))
        self.pending[arm] = None
        self.active[arm] = request["request_id"]
        future = self.pool.submit(self._execute, request)
        self.futures.add(future)
        future.add_done_callback(self._future_finished)

    def _future_finished(self, future):
        with self.lock:
            self.futures.discard(future)

    def _execute(self, request):
        try:
            receipt = self.executor(json.loads(_json(request)))
            self.reconcile(request["request_id"], receipt)
        except Exception as error:
            with self.store.transaction() as db:
                db.execute("UPDATE paired_requests SET state='indeterminate',updated_at=? WHERE id=? AND state NOT IN ('completed','rejected','cancelled')", (self.clock(),request["request_id"]))
            self._event("perception.indeterminate", {"request_id":request["request_id"],"arm":request["arm"],"reason":"terminal_proof_missing"})
            lookup = error.lookup if isinstance(error, CompletionUnproven) else None
            if callable(lookup):
                self._lookup(request, lookup)

    def _lookup(self, request, lookup):
        # Bounded identity-only lookups after the worker could settle, on this
        # pool thread and outside runner/store locks. They never resend pixels or
        # re-execute. A worker keeps its ingress busy until an orphaned execution
        # ends, so only a lookup proven never to have run is tried again, after a
        # doubling delay (1.5 s to 24 s, 46.5 s in all). Any other failure, or
        # exhaustion, leaves the arm indeterminate. Close skips pending lookups.
        reason = "terminal_proof_missing"
        for attempt in range(self.lookup_attempts):
            if self.stopping.wait(self.reconcile_delay*2**attempt):
                return
            try:
                self.reconcile(request["request_id"], lookup(), proof_source="identity_lookup")
                return
            except LookupNotPerformed:
                reason = "lookup_not_performed"
            except Exception:
                reason = "terminal_proof_missing"
                break
        try:
            self._event("perception.lookup_unproven", {"request_id":request["request_id"],"arm":request["arm"],
                                                       "attempts":attempt+1,"reason":reason})
        except Exception:
            pass

    def reconcile(self, request_id, receipt, *, proof_source=None):
        timing = None
        if isinstance(receipt, dict) and "delivery_timing" in receipt:
            # Local measurements travel beside, never inside, the terminal proof.
            receipt = dict(receipt)
            timing = receipt.pop("delivery_timing")
            if (not isinstance(timing, dict) or set(timing) - {"encode_ms","body_bytes","transport_ms"}
                    or not all(_finite(v) and v >= 0 for v in timing.values())):
                timing = None
        with self.lock:
            with self.store.lock:
                row = self.store.db.execute("SELECT * FROM paired_requests WHERE id=? AND session_id=?", (request_id,self.session_id)).fetchone()
                dispatched = self.store.db.execute("SELECT dispatched_at FROM paired_dispatch WHERE request_id=?", (request_id,)).fetchone()
            if not row:
                raise ValueError("unknown dispatched request")
            request = json.loads(row["payload"])
            profile = request["profile"]
            if (not isinstance(receipt,dict) or receipt.get("request_id") != request_id
                    or receipt.get("engine_epoch") != profile["engine_epoch"]
                    or receipt.get("artifact_sha256") != profile["artifact_sha256"]
                    or receipt.get("terminal") is not True
                    or receipt.get("state") not in {"completed","rejected","cancelled"}):
                raise ValueError("matching affirmative terminal proof required")
            state = receipt["state"]
            if len(_json(receipt)) > 256*1024:
                raise ValueError("bounded worker result required")
            if row["state"] in {"completed","rejected","cancelled"}:
                if row["result"] != _json(receipt):
                    raise ValueError("conflicting terminal receipt")
                return
            if row["state"] not in {"dispatching","running","indeterminate"}:
                raise ValueError("request was not dispatched")
            now = self.clock()
            # The runner is prepared while the session is still starting.
            # Read the committed recording origin, not that pre-start snapshot.
            session = self.store.get_session(self.session_id)
            valid_until = arm_valid_until(original_frame(request), request.get("sampling_hz"))
            current = (not self.closed and session["state"]=="recording"
                       and request["generation"] == self.generation and valid_until >= now)
            delivery = {"request_id":request_id,"arm":request["arm"],"state":state,
                        "generation":request["generation"],"current":current,"completed_at":now,
                        "observed_at":request["frame"]["observed_at"],"frame_id":request["frame"]["frame_id"],
                        "model_version":profile["model_version"],"result":receipt.get("result")}
            output = {**delivery, "status":state, "mode":"live", "run_id":profile["profile_id"],
                      "site_id":request["frame"]["site_id"], "camera_id":request["frame"]["camera_id"],
                      "capture_epoch":request["frame"]["capture_epoch"], "media_ids":request["frame"]["media_ids"],
                      "artifact_sha256":profile["artifact_sha256"], "engine_epoch":profile["engine_epoch"],
                      "available_at":now, "valid_until":valid_until}
            from apartment_viewing_source import is_viewing_id
            if any(is_viewing_id(mid) for mid in request['frame']['media_ids']):
                output.update(viewing_only=True,operational_geometry=None,geometry_authority=False,
                    beam_authority=False,physical_permission=False)
            if valid_until != original_frame(request)["valid_until"]:
                # Instrumentation only: which rule extended the source's claim.
                delivery["valid_until"] = valid_until
                delivery["validity_rule"] = ARM_VALIDITY_RULE
            # Delivery instrumentation stays on the ledger event only; the output
            # (evidence and replay payload) is unchanged.
            if dispatched is not None:
                delivery["dispatched_at"] = dispatched[0]
            if timing:
                delivery.update(timing)
            if proof_source is not None:
                delivery["proof_source"] = proof_source
            association_bank = self.view_person_associations[request['arm']]
            association_preview = association_bank.preview(None, None, None)
            reading_bank = self.reading_sources[request['arm']]
            # Sampling/development holds belong to the viewing presentation.
            # Reading authority must never inherit their extended deadline.
            # Consult the registered original, not caller fields or a remote
            # worker's result. Development timing warnings permit inspection,
            # but cannot grant reading authority. Missing originals fail closed
            # without discarding the worker's terminal completion receipt.
            from apartment_viewing_source import registered_media
            from store import LabError
            try:
                timing_qualified = all(registered_media(self.store, self.session_id, mid)
                    .get('metadata', {}).get('reading_timing_qualified', True) is True
                    for mid in request['frame']['media_ids'])
            except (LabError, ValueError):
                timing_qualified = False
            reading_preview = reading_bank.preview(
                {**output, 'valid_until': original_frame(request)['valid_until'],
                 'current': current and timing_qualified}, session)
            if state == 'completed':
                output['reading_binding'] = reading_preview[0]
            if state=="completed":
                output["arm_snapshot"]=arm_snapshot(self.store,request,receipt.get("result"),completed_at=now,current=current)
                # A rendering projection is bounded separately from raw evidence.
                if len(_json(output["arm_snapshot"]))>128*1024:
                    output["arm_snapshot"]={"id":request["arm"].upper(),"status":"unavailable","validation":["presentation_size_limit"]}
                shown = output['arm_snapshot']; people = shown.get('persons2d'); shown_frame = shown.get('frame', {})
                if (isinstance(people, dict) and people.get('schema') == 'home-lab-frame-persons-1'
                        and people.get('version') == 1 and people.get('frame_id') == request['frame']['frame_id']
                        and shown_frame.get('id') == request['frame']['frame_id']):
                    association_scope = {key: request['frame'][key] for key in ('site_id', 'camera_id', 'session_id', 'capture_epoch')}
                    association_scope.update(session_generation=session['generation'], arm=request['arm'], generation=request['generation'],
                        **{key: profile[key] for key in ('engine_epoch', 'artifact_sha256', 'model_version')})
                    association_source = {key: original_frame(request)[key] for key in ('frame_id', 'observed_at', 'valid_until')}
                    association_source.update(request_id=request_id, available_at=now,
                        width=shown_frame.get('width'), height=shown_frame.get('height'))
                    association_preview = association_bank.preview(association_scope, association_source, people.get('persons'),
                        current=current, clock_verified=_finite(now), uncertainty_s=None)
                    if association_preview.association is not None:
                        people['association'] = association_preview.association
                        if len(_json(shown)) > 128*1024:
                            # Keep all incumbent geometry if additive detail will
                            # not fit; no invisible continuity may survive it.
                            people.pop('association')
                            association_preview = association_bank.preview(None, None, None)
                source_time=output["arm_snapshot"].get("frame",{}).get("session_time_s")
                if source_time is not None:
                    output["source_end_s"]=source_time
                    output["available_at_s"]=now-session["started_at"]
            # Persist proof and replay evidence together. In particular, retrying
            # an acknowledged receipt after a crash must not silently omit output.
            # Specialist observations remain observations: never manufacture an
            # activity prediction or human label from a detector response.
            with self.store.transaction() as db:
                self.store.check_capacity(len(_json(receipt))+len(_json(output))+65536)
                db.execute("UPDATE paired_requests SET state=?,result=?,updated_at=? WHERE id=?", (state,_json(receipt),now,request_id))
                self.store._event(db,self.session_id,"perception.delivery",delivery)
                if state == "completed":
                    self.store._event(db,self.session_id,"perception.output",output)
            # No anonymous state advances if immutable output storage rolled back.
            association_bank.commit(association_preview)
            reading_bank.commit(reading_preview)
            if self.active[request["arm"]] == request_id:
                self.active[request["arm"]] = None
            if state == 'completed' and self.evidence_ready is not None:
                self.evidence_ready.set()
            self._dispatch(request["arm"])
            from apartment_viewing_source import prune
            prune(self.store,self.session_id)

    def snapshot(self):
        with self.lock:
            return {"generation":self.generation,"active":dict(self.active),
                    "pending":{arm:req["request_id"] if req else None for arm,req in self.pending.items()},
                    "closed":self.closed}

    def close(self):
        self.stopping.set()
        with self.lock:
            self.closed = True
            for arm, request in self.pending.items():
                if request:
                    self._terminal_local(request,"dropped","runner_closed_before_dispatch")
                self.pending[arm] = None
        # An active executor may still be running. Do not cancel it or release its
        # durable/global reservation merely because the frontend disconnected.
        self.pool.shutdown(wait=False, cancel_futures=False)
