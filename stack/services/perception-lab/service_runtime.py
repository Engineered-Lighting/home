"""Composition of registered recording and explicitly admitted paired execution.

Provisioning supplies recorder instances, a profile resolver and common admission
executor. Merely constructing this object performs no capture or inference.
"""
import threading
import json
import logging
import math
import time
import re
from concurrent.futures import wait
from contextlib import nullcontext
import controller_load
from runtime import PairedRunner
from live_inputs import live_records
from preview_client import PreviewPump
from profile_resolver import RegisteredPairs

# The occupancy reader's own logger (occupancy_reader.py): edges and fallbacks.
_occupancy_log=logging.getLogger("perception_lab.occupancy")


def _close(worker, reason):
    # Reasoners that record why pending work was cancelled take a controlled
    # reason (LiveReasoning.CLOSE_REASONS); every other worker closes as before.
    reasons=getattr(type(worker),"CLOSE_REASONS",None)
    if isinstance(reasons,tuple) and reason in reasons:
        worker.close(reason=reason)
    else:
        worker.close()


def _lever_args(prepared):
    # One-lever metadata a resolver may return; absent for legacy resolvers.
    return {k:prepared.get(k) for k in ("source_policy","lever","baseline_sha256")}


class _Signals:
    """Wake every recording-owned scheduler when a specialist output lands."""
    def __init__(self, *events):
        self.events=events

    def set(self):
        for event in self.events:
            event.set()


SPATIAL_LOCALIZATIONS=("stereo","floor_projection")
PREVIEW_RETRY_S=.1  # the preview pump's one in-slot retry after a failed capture
# Live JEV scheduling. An arm is offered only for new admitted camera evidence,
# at most once per interval per arm; skips are counted, never requested. The
# interval is a controller setting (live config 'reasoning_min_offer_interval_s'),
# independent of the recipe, bounded to REASONING_OFFER_INTERVAL_BOUNDS_S.
REASONING_MIN_OFFER_INTERVAL_S=2.0
REASONING_OFFER_INTERVAL_BOUNDS_S=(1.0,10.0)


def offer_interval(value):
    """The validated per-arm minimum offer interval, in seconds (default 2.0)."""
    if value is None:
        return REASONING_MIN_OFFER_INTERVAL_S
    low,high=REASONING_OFFER_INTERVAL_BOUNDS_S
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError("reasoning_min_offer_interval_s must be a number from 1 to 10 seconds")
    return float(value)
REASONING_SKIPS=("unchanged_evidence","context_only","rate_limited","no_visual_evidence",
                 "facts_unchanged","keepalive_not_due","no_person","awaiting_inflight_frame")
# Facts-key cadence (plan §6.1/6.2), the live default. An arm is offered when
# its facts key changed since its last offer (at most once per
# max(reasoning_min_offer_interval_s, debounce_s)), or on a keepalive_s
# re-offer of an unchanged key while the room is occupied. While the occupancy
# gate reads vacant or unknown for longer than its hold, nothing is requested:
# the arm answers locally (a code_gate abstention, every gate_keepalive_s).
# hold_max_s bounds how long a delivered answer stays valid on unchanged facts.
# Active only for a reasoner that computes facts keys (the v6 facts wire);
# otherwise, and with mode "new_evidence", the new-admitted-evidence rule runs.
REASONING_CADENCE_DEFAULTS={"mode":"facts_key","keepalive_s":10.0,"debounce_s":2.0,"hold_max_s":12.0,"gate_keepalive_s":10.0}
REASONING_CADENCE_BOUNDS={"keepalive_s":(2.0,60.0),"debounce_s":(0.0,10.0),"hold_max_s":(1.0,30.0),"gate_keepalive_s":(1.0,60.0)}
REASONING_CADENCE_MODES=("facts_key","new_evidence")
REASONING_OFFER_REASONS=("new_evidence","facts_changed","keepalive")


def cadence_settings(value):
    """Validated live cadence settings (config 'reasoning_cadence'); None is the default."""
    if value is None:
        return dict(REASONING_CADENCE_DEFAULTS)
    if not isinstance(value,dict) or set(value)-set(REASONING_CADENCE_DEFAULTS):
        raise ValueError("reasoning_cadence takes only mode, keepalive_s, debounce_s, hold_max_s and gate_keepalive_s")
    result={**REASONING_CADENCE_DEFAULTS,**value}
    if result["mode"] not in REASONING_CADENCE_MODES:
        raise ValueError("reasoning_cadence mode must be facts_key or new_evidence")
    for key,(low,high) in REASONING_CADENCE_BOUNDS.items():
        number=result[key]
        if type(number) not in (int,float) or not math.isfinite(number) or not low<=number<=high:
            raise ValueError(f"reasoning_cadence {key} must be a number from {low:g} to {high:g} seconds")
        result[key]=float(number)
    return result


def occupancy_step(track, state, now, hold):
    """One tick of the scheduler's occupancy view (pure; also used offline).

    track: mutable {'since', 'last_occupied'}; state: an occupancy_gate state
    ('occupied', 'vacant', 'unknown'; the gate's own hold already reads a
    brief 'off' after 'on' as occupied). Returns (gated, vacant, occupied):
    gated once the room has not read occupied for longer than hold (the first
    tick starts that clock; unknown is bridged too), vacant for a vacant
    reading, occupied for an occupied reading (only then is a keepalive due)."""
    if track.get("since") is None or now<track["since"]:
        track["since"]=now
    if track.get("last_occupied") is not None and track["last_occupied"]>now:
        track["last_occupied"]=None  # a clock step back never holds on the future
    if state=="occupied":
        track["last_occupied"]=now
    reference=track["last_occupied"] if track.get("last_occupied") is not None else track["since"]
    return state!="occupied" and now-reference>hold,state=="vacant",state=="occupied"


def inflight_hold(*, camera, now, expired_at, inflight_deadline, debounce_s):
    """Until when an arm whose camera just expired waits for its own newer frame
    (pure; also used offline), else None.

    The scheduler wakes on any arm's delivery. With paired arms, one arm's
    previous frame has often just expired when the other arm's result lands,
    while its own newer frame is already dispatched and lands a moment later:
    offering then would ask JEV about an expired camera, only to offer again
    when the frame lands. So while the arm's camera reads expired_historical and
    a dispatched request for a newer frame has not passed its deadline (that
    frame's own validity), nothing is offered, at most until expired_at +
    debounce_s: a stalled worker or camera still surfaces within debounce_s."""
    if camera!="expired_historical" or expired_at is None or inflight_deadline is None:
        return None
    until=min(inflight_deadline,expired_at+debounce_s)
    return until if now<until else None


def cadence_decision(last, *, now, key, visual, config, gated, occupied, interval, settings, hold_until=None):
    """(action, reason, wake_at) for one arm on one tick (pure; also used offline).

    action: 'offer' (reason facts_changed or keepalive), 'gate' (reason
    no_person: answer locally, request nothing) or 'skip' (a REASONING_SKIPS
    reason). last: the arm's last offer {'key','at','config'} or None.
    hold_until (inflight_hold): an offer due before then is skipped as
    awaiting_inflight_frame, waking then."""
    if gated:
        return "gate","no_person",None
    if not visual or key is None:
        return "skip","no_visual_evidence",None

    def due(reason):
        if hold_until is not None and now<hold_until:
            return "skip","awaiting_inflight_frame",hold_until
        return "offer",reason,None
    # A ledger clock stepped back (now before the last offer) counts as elapsed.
    elapsed=None if last is None else now-last["at"]
    fresh=elapsed is not None and elapsed>=0
    if last is None or last.get("config")!=config or last.get("key")!=key:
        spacing=max(interval,settings["debounce_s"])
        if fresh and elapsed<spacing:
            return "skip","rate_limited",last["at"]+spacing
        return due("facts_changed")
    if not occupied:
        return "skip","facts_unchanged",None
    if fresh and elapsed<max(interval,settings["keepalive_s"]):
        return "skip","keepalive_not_due",None
    return due("keepalive")


class BundleSynchronizedAssociation:
    """Associations for one recorded perception.output, depth from its own frame.

    The camera owner serves /frame.jpg from the RGB frame of its latest sync
    group and /frame.npz with that group, but the Lab polls them independently,
    so the nearest recorded bundle is usually 100-250 ms from the detected RGB:
    beyond any skew bound, and a looser bound would pair a person with another
    instant's depth. A stereo arm therefore uses a bundle only when the bundle's
    own RGB frame is the detection's frame (same capture epoch and sequence, and
    the same device exposure timestamp within the receipt's quantization). The
    depth clock mapping is anchored on that frame, so the remaining skew is the
    sync group's own RGB-depth offset, which the consumer still bounds.
    Otherwise the arm abstains ('no_depth_frame_in_window') with a controlled
    reason. Nothing here widens a tolerance or extends a validity.
    """
    REASONS=frozenset({"depth_clock_not_commissioned","rgb_frame_unavailable","rgb_frame_not_causal",
                       "bundle_rgb_frame_not_recorded","depth_clock_mapping_refused"})

    def __init__(self, store, spatial, *, site_id, camera_id, unavailable):
        from depth_association import DepthAssociator, SessionTracks
        from depth_clock import DepthClock
        if set(unavailable)!=set(SPATIAL_LOCALIZATIONS):
            raise ValueError("per-localization spatial readiness required")
        self.store,self.site_id,self.camera_id=store,site_id,camera_id
        self.reasons=dict(unavailable)
        registration=spatial["registration"]
        self.depth_clock=(DepthClock(store,site_id=site_id,camera_id=camera_id,clock=spatial["depth_clock"],
                                     calibration=spatial["calibration"]) if "depth_clock" in spatial else None)
        self.associator=DepthAssociator(store,association_revision=spatial["association_revision"],
            calibration=spatial["calibration"],tracks=SessionTracks(site_id,camera_id),
            floor_homography=registration.get("floor_homography"),target_frame=registration["target_frame"])

    def unavailable(self, localization):
        return self.reasons.get(localization,"unsupported_localization")

    def _own_bundle_depth(self, sid, source):
        """(clock-mapped depth media id, None) or (None, controlled reason)."""
        if self.depth_clock is None:
            return None,"depth_clock_not_commissioned"
        session=self.store.get_session(sid)
        frames=[]
        for mid in source.get("media_ids") or []:
            item=self.store.get_media(sid,mid,generation=session["generation"])
            if item["metadata"].get("stream")=="rgb" and item["metadata"].get("frame_id")==source.get("frame_id"):
                frames.append(item)
        if len(frames)!=1:
            return None,"rgb_frame_unavailable"
        rgb=frames[0]
        meta=rgb["metadata"]
        receipt=meta.get("source_timing_receipt")
        epoch,sequence=meta.get("capture_epoch"),meta.get("sequence")
        if (not isinstance(receipt,dict) or type(receipt.get("frame_device_ns")) is not int
                or type(receipt.get("timestamp_quantization_ns")) is not int
                or not 1000<=receipt["timestamp_quantization_ns"]<=1_000_000
                or type(sequence) is not int or not epoch or epoch!=source.get("capture_epoch")):
            return None,"rgb_frame_not_causal"
        target=receipt["frame_device_ns"]/1e9
        # Device timestamps are exported as float seconds: 1 us rounding.
        tolerance=receipt["timestamp_quantization_ns"]/1e9+1e-6
        with self.store.lock:
            rows=self.store.db.execute("""SELECT id FROM media WHERE session_id=?
                AND json_extract(metadata,'$.stream')='rgbd_bundle' AND json_extract(metadata,'$.capture_epoch')=?
                AND json_extract(metadata,'$.source_metadata.frames.rgb.sequence')=? ORDER BY created_at DESC LIMIT 4""",
                (sid,epoch,sequence)).fetchall()
        for (bundle_id,) in rows:
            bundle=self.store.get_media(sid,bundle_id,generation=session["generation"])
            stamp=((bundle["metadata"].get("source_metadata") or {}).get("frames") or {}).get("rgb",{}).get("device_timestamp_seconds")
            if type(stamp) in (int,float) and abs(stamp-target)<=tolerance:
                try:
                    return self.depth_clock.derive(sid,bundle_id,rgb["id"])["id"],None
                except ValueError:
                    return None,"depth_clock_mapping_refused"
        return None,"bundle_rgb_frame_not_recorded"

    def associate(self, sid, event_id, source, *, localization):
        """(associations, synchrony reason or None); always at least one entry."""
        if self.unavailable(localization) is not None:
            raise ValueError("localization not provisioned")
        if localization=="floor_projection":
            return self.associator.associations(sid,event_id,source,localization="floor_projection"),None
        boxes=(source.get("result") or {}).get("boxes") or []
        if not any(isinstance(box,dict) and box.get("label")=="person" for box in boxes):
            # No region to associate: no depth record is derived for it.
            return self.associator.associations(sid,event_id,source,localization="stereo"),None
        depth_id,reason=self._own_bundle_depth(sid,source)
        return self.associator.associations(sid,event_id,source,localization="stereo",depth_media_id=depth_id),reason


class ServiceRuntime:
    def __init__(self, store, recorders, resolve_profiles, executor, *, analysis_jobs=None, preparation_jobs=None, reasoning_factory=None, preview_sources=None, preview_clients=None, bundle_sources=None, bundle_clients=None, ha_sources=None, ha_clients=None, recording_only=False, spatial_sources=None, caption_sources=None, spatial_associators=None, spatial_unavailable=None, reasoning_min_offer_interval_s=None, qwen_live=None, vjepa_live=None, reasoning_cadence=None, room_facts=None, occupancy_readers=None, preview_taps=None, paired_preview_sources=None, paired_preview_policy=None, paired_depth_clocks=None, bundled_rgb_sources=None, apartment_viewing_sources=None, la_qwen=None, execution_arms=('a', 'b')):
        from runtime import validate_execution_arms
        self.execution_arms = validate_execution_arms(execution_arms)
        self.store = store
        self.reasoning_min_offer_interval_s = offer_interval(reasoning_min_offer_interval_s)
        self.reasoning_cadence = cadence_settings(reasoning_cadence)
        self.recorders = dict(recorders)
        self.resolve_profiles = resolve_profiles
        self.executor = executor
        if type(recording_only) is not bool or (recording_only and any(v is not None for v in (analysis_jobs,preparation_jobs,reasoning_factory,qwen_live,vjepa_live))):
            raise ValueError("recording-only runtime cannot bind model jobs or reasoning")
        # Continuous Qwen perceiver: a provisioned factory
        # (store, session, *, active, enabled, occupied) -> QwenLivePump, one
        # pump per recording. Its factory may expose kill_switch(),
        # horizon_seconds and occupancy (occupancy_gate.gate_config settings).
        if qwen_live is not None and not callable(qwen_live):
            raise ValueError("Qwen live perceiver factory must be callable")
        self.qwen_live = qwen_live
        # The always-on LA perceiver's accepted receipts (la_qwen_context): the
        # Qwen facts for recordings that run no qwen_live pump of their own.
        self.la_qwen = la_qwen
        self.qwen_live_pumps = {}
        self.qwen_live_errors = {}
        # Continuous V-JEPA clip pump: the same factory shape, gate and
        # lifecycle (-> VjepaLivePump); it may expose kill_switch() and occupancy.
        if vjepa_live is not None and not callable(vjepa_live):
            raise ValueError("V-JEPA live pump factory must be callable")
        self.vjepa_live = vjepa_live
        self.vjepa_live_pumps = {}
        self.vjepa_live_errors = {}
        # Victoria-LAN occupancy readers (occupancy_reader.OccupancyReader, one
        # per camera, from the live config's 'occupancy_reader' block): the live
        # pumps' gate prefers one over the recording's ha.observation rows. A
        # reader starts polling with the first pump that uses it and stops with
        # the runtime; absent changes nothing.
        self.occupancy_readers = dict(occupancy_readers or {})
        if any(not callable(r) or not all(callable(getattr(r, k, None)) for k in ("start", "stop", "subscribe"))
               or not isinstance(getattr(r, "entity", None), str) for r in self.occupancy_readers.values()):
            raise ValueError("occupancy readers must be startable edge-reporting gates")
        self.occupancy_readers_passed_over = set()  # cameras whose reader fell back to the ledger gate (logged once)
        self.recording_only=recording_only
        from spatial_consumer import SpatialConsumer
        self.spatial_sources = dict(spatial_sources or {})
        if (recording_only and self.spatial_sources) or any(
                not isinstance(source, SpatialConsumer) or source.store is not store
                or source.camera_id != camera for camera, source in self.spatial_sources.items()):
            raise ValueError('spatial consumers require the active controller and inference mode')
        # Association adapters feed their camera's consumer; absent means spatial
        # localization is not provisioned (spatial_unavailable says why).
        self.spatial_associators = dict(spatial_associators or {})
        if set(self.spatial_associators) - set(self.spatial_sources) or any(
                not callable(getattr(a, 'associate', None)) or not callable(getattr(a, 'unavailable', None))
                for a in self.spatial_associators.values()):
            raise ValueError('spatial associators require matching spatial consumers')
        if spatial_unavailable is not None and (not isinstance(spatial_unavailable, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", spatial_unavailable)):
            raise ValueError('controlled spatial reason required')
        self.spatial_unavailable = spatial_unavailable
        self.spatial_state = {}
        self.spatial_stop = threading.Event()
        self.spatial_ready = threading.Event()
        self.spatial_thread = None
        self.spatial_busy = None
        self.spatial_error = None
        from ha_ingest import HAIngestor
        self.ha_sources = dict(ha_sources or {})
        self.ha_clients = dict(ha_clients or {})
        self.ha_pumps = {}
        if set(self.ha_clients) - set(self.ha_sources):
            raise ValueError("HA clients require matching source ingestors")
        if any(not isinstance(source, HAIngestor) or source.store is not store or source.camera_id != camera
               for camera, source in self.ha_sources.items()):
            raise ValueError('HA sources must use the controller ledger and registered camera')
        from caption_history import CaptionHistory
        # Stored-caption readers only: polling never requests a refresh or inference.
        self.caption_sources = dict(caption_sources or {})
        self.caption_pumps = {}
        self.caption_states = {}
        if any(not isinstance(source, CaptionHistory) or source.store is not store or source.camera_id != camera
               for camera, source in self.caption_sources.items()):
            raise ValueError('caption sources must use the controller ledger and registered camera')
        self.preview_sources=dict(preview_sources or {})
        self.preview_clients=dict(preview_clients or {})
        self.preview_pumps={}
        # Preview taps (camera -> callable(jpeg, header)): the always-on LA
        # perceiver's RAM ring (la_live_perceiver.offer_preview). Each capture
        # the recording ingests is offered after its ingest; a tap never
        # affects the recording. Absent changes nothing.
        self.preview_taps=dict(preview_taps or {})
        self.bundled_rgb_sources=dict(bundled_rgb_sources or {})
        self.bundled_rgb_sessions={}
        self.apartment_viewing_sources=dict(apartment_viewing_sources or {})
        self.apartment_viewing_sessions={}
        if self.apartment_viewing_sources:
            from apartment_viewing_source import ViewingFactory
            if recording_only or any(not isinstance(s,ViewingFactory) or s.store is not store or s.camera_id!=camera
                    for camera,s in self.apartment_viewing_sources.items()):
                raise ValueError('viewing source requires its existing controller and camera')
        if any(not callable(tap) for tap in self.preview_taps.values()):
            raise ValueError("preview taps must be callable")
        self.bundle_sources=dict(bundle_sources or {})
        self.bundle_clients=dict(bundle_clients or {})
        self.bundle_pumps={}
        self.paired_preview_sources=dict(paired_preview_sources or {})
        self.paired_depth_clocks=dict(paired_depth_clocks or {})
        self.paired_preview_state={}
        self.paired_preview_policy=None
        if paired_preview_policy is not None:
            from paired_preview_client import PairedPreviewClient, policy
            from bundle_ingest import BundleIngestor
            from capture_timing import commissioning
            self.paired_preview_policy=policy(paired_preview_policy)
            if (recording_only or not self.paired_preview_sources
                    or set(self.paired_depth_clocks)-set(self.paired_preview_sources)
                    or any(not isinstance(source,BundleIngestor) or source.store is not store
                           or source.camera_id!=camera or source.site_id!=store.settings.site_id
                           or not isinstance(self.preview_clients.get(camera),PairedPreviewClient)
                           or camera not in self.preview_sources
                           for camera,source in self.paired_preview_sources.items())):
                raise ValueError('paired retention requires the incumbent preview and controller Store')
            self.paired_depth_clocks={camera:commissioning(value) for camera,value in self.paired_depth_clocks.items()}
        elif self.paired_preview_sources or self.paired_depth_clocks:
            raise ValueError('explicit paired preview policy required')
        # Room facts (room_facts.RoomFactsStage): a validated site map's 2 Hz
        # depth sampling for one camera, shared by both arms and never a lever.
        # Its pump is kept with the numerical-bundle pumps (stopped with them);
        # absent (no site map provisioned) changes nothing.
        if room_facts is not None and (recording_only or self.bundle_sources or not callable(getattr(room_facts,"pump",None))):
            raise ValueError("room facts need the inference runtime and no depth-recipe bundle source")
        self.room_facts=room_facts
        self.analysis_jobs = analysis_jobs
        self.preparation_jobs = preparation_jobs
        self.reasoning_factory = reasoning_factory
        self.reasoners = {}
        self.runners = {}
        self.lock = threading.RLock()
        self.closed = False
        self.reasoning_stop=threading.Event()
        self.reasoning_ready=threading.Event()
        self.reasoning_thread=None
        self.reasoning_errors={}
        # Evidence-driven scheduling state per recording session: the evidence
        # last offered per arm, content-free skip counters, and the parsed
        # evidence window cache (see live_records).
        self.reasoning_schedule={}
        self.evidence_caches={}
        self._live_payloads={}
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS lab_experiment_state(
                session_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                generation INTEGER NOT NULL)""")

    def ingest_spatial(self, session_id, associations, *, generation):
        """Trusted association adapter entrypoint; no caller-selected public route."""
        with self.lock:
            if self.closed:
                raise ValueError('runtime shutting down')
            session = self.store.get_session(session_id)
            source = self.spatial_sources.get(session['camera_id'])
            if source is None:
                raise ValueError('spatial source not provisioned')
            return source.ingest(session_id, associations, generation=generation)

    def ingest_ha(self, session_id, **snapshot):
        """Trusted adapter entrypoint; deliberately absent from the public API."""
        with self.lock:
            if self.closed:
                raise ValueError('runtime shutting down')
            session = self.store.get_session(session_id)
            source = self.ha_sources.get(session['camera_id'])
            if source is None:
                raise ValueError('HA source not provisioned')
            return source.ingest(session_id, **snapshot)

    def ingest_caption(self, session_id, value):
        """Recording-owned persistence of an already stored caption; no inference."""
        with self.lock:
            if self.closed:
                raise ValueError('runtime shutting down')
            session = self.store.get_session(session_id)
            source = self.caption_sources.get(session['camera_id'])
            if source is None:
                raise ValueError('caption source not provisioned')
            result = source.ingest(session_id, value)
            self.caption_states[session_id] = result['state']
            return result

    def capabilities(self):
        result={"recording_allowed":bool(self.recorders) and not self.closed, "gates":[]}
        if self.recording_only:
            result["gates"].append({"id":"inference","state":"blocked","reason":"recording_only_commissioning"})
        if isinstance(self.resolve_profiles,RegisteredPairs):
            result["experiments"]=self.resolve_profiles.catalog()
        # Informational provisioning facts; content-free and never an admission.
        result["caption"]={"state":"provisioned" if getattr(self,"caption_sources",None) else "not_provisioned"}
        result["qwen_live"]={"state":"provisioned" if getattr(self,"qwen_live",None) is not None else "not_provisioned"}
        result["vjepa_live"]={"state":"provisioned" if getattr(self,"vjepa_live",None) is not None else "not_provisioned"}
        result["spatial"]=self.spatial_capability()
        return result

    def spatial_capability(self):
        associators=getattr(self,"spatial_associators",None)
        if not associators:
            return {"state":"not_provisioned","reason":getattr(self,"spatial_unavailable",None) or "spatial_not_provisioned"}
        associator=next(iter(associators.values()))
        return {"state":"provisioned","reason":None,"localizations":{kind:{"available":associator.unavailable(kind) is None,
                "reason":associator.unavailable(kind)} for kind in SPATIAL_LOCALIZATIONS}}

    def delete_session(self,session,expected_revision,*,erase_records=False):
        """Delete only after local users have actually stopped; never stop models."""
        from store import LabError
        with self.lock:
            sid=session['id']
            recorder=self.recorders.get(session['camera_id'])
            if recorder is not None and getattr(recorder,'sid',None)==sid:
                thread=getattr(recorder,'thread',None);process=getattr(recorder,'process',None)
                if (thread is not None and thread.is_alive()) or (process is not None and process.poll() is None):
                    raise LabError('recording process still active',409,'execution_pending')
            for pumps in (self.preview_pumps,self.bundle_pumps,self.ha_pumps,self.caption_pumps,self.qwen_live_pumps,self.vjepa_live_pumps):
                pump=pumps.get(sid)
                if pump is not None and pump.thread.is_alive():
                    raise LabError('source reader still active',409,'execution_pending')
            if self.spatial_busy==sid:
                raise LabError('spatial association still active',409,'execution_pending')
            for workers in (self.runners,self.reasoners):
                worker=workers.get(sid)
                if worker is not None:
                    with worker.lock:
                        if not worker.closed or any(not f.done() for f in worker.futures):
                            raise LabError('session worker still active',409,'execution_pending')
            job_ids={job['id'] for job in self.store.list_jobs(sid)}
            for jobs in (self.analysis_jobs,self.preparation_jobs):
                if jobs is not None:
                    with jobs.lock:
                        if any(jid in job_ids and not f.done() for jid,(f,_) in jobs.work.items()):
                            raise LabError('job worker still active',409,'execution_pending')
            try:
                return self.store.delete_session(sid,expected_revision,erase_records=erase_records)
            finally:
                with self.store.lock:
                    erased=self.store.db.execute('SELECT 1 FROM session_erasures WHERE session_id=?',(sid,)).fetchone()
                if erased:
                    for group in (self.runners,self.reasoners,self.preview_pumps,self.bundle_pumps,self.ha_pumps,self.caption_pumps,self.caption_states,self.reasoning_errors,self.spatial_state,
                                  self.reasoning_schedule,self.evidence_caches,self.qwen_live_pumps,self.qwen_live_errors,
                                  self.vjepa_live_pumps,self.vjepa_live_errors):
                        group.pop(sid,None)
                    for jobs in (self.analysis_jobs,self.preparation_jobs):
                        if jobs is not None:
                            with jobs.lock:
                                for jid in job_ids:jobs.work.pop(jid,None)

    def run_job(self, job):
        if self.closed:
            return {"state":"blocked","reason":"runtime_shutting_down"}
        if job["kind"]=="preparation":
            return self.preparation_jobs.submit(job) if self.preparation_jobs else {"state":"blocked","reason":"preparation_not_provisioned"}
        if self.analysis_jobs is None:
            return {"state":"blocked","reason":"analysis_not_provisioned"}
        return self.analysis_jobs.submit(job)

    def cancel_job(self, job):
        if job["kind"]=="preparation":
            return self.preparation_jobs.cancel(job) if self.preparation_jobs else {"state":"indeterminate","reason":"preparation_not_reconciled"}
        if self.analysis_jobs is None:
            return {"state":"indeterminate","reason":"analysis_not_reconciled"}
        return self.analysis_jobs.cancel(job)

    def _prepare(self, session, experiment):
        # Resolver is provisioned, not selected by a caller URL or module name.
        # It must return an existing admitted pair; no implicit model preparation.
        if self.recording_only:
            return {"state":"blocked","reason":"recording_only_commissioning"}
        outcome = self.resolve_profiles(session, experiment)
        if not isinstance(outcome,dict) or outcome.get("state") != "ready":
            reason=outcome.get("reason") if isinstance(outcome,dict) else None
            return {"state":"blocked","reason":reason if isinstance(reason,str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}",reason) else "pair_not_admitted"}
        return outcome

    def _activate(self, session, experiment, profiles, reasoning_windows=None, sampling_hz=None, spatial_policy=None, ha_evidence=None, caption_evidence=None,
                  source_policy=None, lever=None, baseline_sha256=None):
        from reasoning import finite
        from evidence_policy import ha_policy, caption_policy, source_policy as family_policy, lever_families
        ha_evidence = ha_policy(ha_evidence)
        caption_evidence = caption_policy(caption_evidence)
        # Resolved family statuses; they must agree with the HA/caption switches.
        source_policy = family_policy(source_policy, ha=ha_evidence, caption=caption_evidence)
        if (lever is not None and not isinstance(lever, dict)) or (baseline_sha256 is not None and not isinstance(baseline_sha256, str)):
            raise ValueError("invalid recipe lever metadata")
        windows={"a":10,"b":10} if reasoning_windows is None else dict(reasoning_windows)
        if set(windows)!={"a","b"} or any(not finite(v) or not 0<=v<=120 for v in windows.values()):
            raise ValueError("invalid reasoning windows")
        spatial_policy=spatial_policy or {arm:{"localization":"none","room_relations":False} for arm in ("a","b")}
        if (set(spatial_policy)!={"a","b"} or any(not isinstance(p,dict)
                or p.get("localization") not in {"none","stereo","floor_projection"}
                or type(p.get("room_relations")) is not bool for p in spatial_policy.values())):
            raise ValueError("invalid spatial evidence policy")
        sid=session["id"]
        with self.store.lock:
            row=self.store.db.execute("SELECT generation FROM lab_experiment_state WHERE session_id=?",(sid,)).fetchone()
        previous=row[0] if row else 0
        generation=previous+1
        # Validate/build the replacement before changing the durable revision or
        # closing the old pair. Construction loads no model and dispatches nothing.
        signals=[self.reasoning_ready,self.spatial_ready]
        viewing=self.apartment_viewing_sessions.get(sid);pump=self.preview_pumps.get(sid)
        if viewing is not None and viewing.metric is not None and pump is not None and pump.wake_event is not None:
            signals.append(pump.wake_event)
        runner=PairedRunner(self.store,sid,profiles,self.executor,clock=self.store.clock,experiment_generation=generation,sampling_hz=sampling_hz,
            evidence_ready=_Signals(*signals),execution_arms=self.execution_arms)
        runner.reasoning_windows=windows
        runner.ha_evidence=ha_evidence
        runner.caption_evidence=caption_evidence
        runner.source_policy=source_policy
        runner.lever_families=lever_families(lever)
        runner.spatial_policy=spatial_policy
        try:
            if any(runner.snapshot()["active"].values()):
                raise ValueError("prior execution must be reconciled")
            with self.store.transaction() as db:
                actual=db.execute("SELECT generation FROM lab_experiment_state WHERE session_id=?",(sid,)).fetchone()
                if (actual[0] if actual else 0)!=previous:
                    raise ValueError("experiment generation changed during preparation")
                db.execute("INSERT INTO lab_experiment_state VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET experiment_id=excluded.experiment_id,generation=excluded.generation",(sid,experiment,generation))
                db.execute("UPDATE sessions SET experiment_id=?,revision=revision+1 WHERE id=?",(experiment,sid))
                self.store._event(db,sid,"experiment.activated",{
                    "site_id":session["site_id"],"camera_id":session["camera_id"],
                    "generation":generation,"experiment_id":experiment,"effective_at":self.store.clock(),"reasoning_windows":windows,"sampling_hz":sampling_hz,"spatial_policy":spatial_policy,"ha_evidence":ha_evidence,"caption_evidence":caption_evidence,
                    "source_policy":source_policy,"lever":lever,"baseline_sha256":baseline_sha256,
                    "execution_arms":list(runner.execution_arms),"comparison":len(runner.execution_arms)==2,
                    "profiles":{arm:{k:profiles[arm][k] for k in
                        ("profile_id","model_version","artifact_sha256","engine_epoch")} for arm in runner.execution_arms}})
        except Exception:
            runner.close()
            raise
        old=self.runners.get(sid)
        if old:
            old.close()
        self.runners[sid]=runner
        if self.reasoning_factory is not None and self.reasoning_thread is None:
            self.reasoning_thread=threading.Thread(target=self._reasoning_loop,name="lab-live-evidence",daemon=True)
            self.reasoning_thread.start()
        self._activate_spatial(session,generation,spatial_policy)
        return generation

    def _activate_spatial(self, session, generation, spatial_policy):
        """Bind this generation's localized arms to the provisioned associator.

        An arm whose localization is not provisioned is recorded once per
        generation as spatial.unavailable; nothing is guessed in its place."""
        sid=session["id"]
        requested={arm:p["localization"] for arm,p in spatial_policy.items() if p["localization"]!="none"}
        if not requested:
            self.spatial_state.pop(sid,None)
            return
        associator=self.spatial_associators.get(session["camera_id"])
        arms,refused={},{}
        for arm,kind in sorted(requested.items()):
            reason=(self.spatial_unavailable or "spatial_not_provisioned") if associator is None else associator.unavailable(kind)
            if reason is None:
                arms[arm]=kind
            else:
                refused[arm]={"localization":kind,"reason":reason}
        with self.store.lock:
            cursor=self.store.db.execute("SELECT coalesce(max(id),0) FROM events WHERE session_id=?",(sid,)).fetchone()[0]
        self.spatial_state[sid]={"state":"active" if arms else "not_provisioned","generation":generation,"arms":arms,
            "refused":refused,"cursor":cursor,"last_reason":None,"failure_recorded":False,
            "counts":{"published":0,"measured":0,"abstained":0,"expired":0,"superseded":0,"failed":0}}
        if refused:
            self.store.append_event(sid,"spatial.unavailable",{"generation":generation,"arms":refused,
                "available_at":self.store.clock()})
        if arms and self.spatial_thread is None:
            self.spatial_thread=threading.Thread(target=self._spatial_loop,name="lab-spatial",daemon=True)
            self.spatial_thread.start()

    def _spatial_loop(self):
        while not self.spatial_stop.is_set():
            self.spatial_ready.wait(1)
            self.spatial_ready.clear()
            if self.spatial_stop.is_set():break
            try:
                self.spatial_tick()
            except Exception:
                # Positions keep expiring on their own; nothing is republished.
                self.spatial_error="spatial_scheduler_unavailable"

    def spatial_tick(self):
        """Recording-owned: associate each localized arm's newest output once.

        Association (depth decode and projection) runs outside the runtime
        lock; publication goes through ingest_spatial, which re-checks the
        session, generation and every freshness bound. An output already past
        its validity is never published, and an older output superseded by a
        newer one of the same arm is skipped, never replayed later."""
        with self.lock:
            if self.closed:
                return
            work=[]
            for sid,state in list(self.spatial_state.items()):
                if state["state"]!="active":
                    continue
                runner=self.runners.get(sid)
                if (runner is None or runner.generation!=state["generation"] or runner.snapshot()["closed"]
                        or self.store.get_session(sid)["state"]!="recording"):
                    state["state"]="stopped"
                    continue
                work.append(sid)
        for sid in work:
            with self.lock:
                state=self.spatial_state.get(sid)
                if self.closed or self.spatial_stop.is_set():
                    return
                if state is None or state["state"]!="active":
                    continue
                self.spatial_busy=sid
            try:
                self._spatial_session(sid,state)
            finally:
                with self.lock:
                    self.spatial_busy=None

    def _spatial_failed(self, sid, state, reason):
        state["counts"]["failed"]+=1
        state["last_reason"]=reason
        if not state["failure_recorded"] and not self.closed:
            state["failure_recorded"]=True
            self.store.append_event(sid,"spatial.ingestion_failed",{"generation":state["generation"],
                "reason":reason,"available_at":self.store.clock()})

    def _spatial_session(self, sid, state):
        camera=self.store.get_session(sid)["camera_id"]
        associator=self.spatial_associators[camera]
        with self.store.lock:
            rows=self.store.db.execute("""SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output'
                AND id>? ORDER BY id DESC LIMIT 64""",(sid,state["cursor"])).fetchall()
        if not rows:
            return
        state["cursor"]=max(row[0] for row in rows)
        latest={}
        for event_id,payload in rows:
            value=json.loads(payload)
            arm=value.get("arm")
            if arm not in state["arms"] or value.get("generation")!=state["generation"] or value.get("status")!="completed":
                continue
            if arm in latest:
                state["counts"]["superseded"]+=1
                continue
            latest[arm]=(event_id,value)
        for arm,(event_id,value) in sorted(latest.items()):
            if self.spatial_stop.is_set():
                return
            valid_until=value.get("valid_until")
            if type(valid_until) not in (int,float) or valid_until<self.store.clock():
                state["counts"]["expired"]+=1
                continue
            try:
                associations,reason=associator.associate(sid,event_id,value,localization=state["arms"][arm])
            except Exception:
                self._spatial_failed(sid,state,"spatial_association_failed")
                continue
            try:
                self.ingest_spatial(sid,associations,generation=state["generation"])
            except Exception:
                self._spatial_failed(sid,state,"spatial_ingest_refused")
                continue
            measured=sum(1 for a in associations if "abstention" not in a)
            state["counts"]["published"]+=1
            state["counts"]["measured"]+=measured
            state["counts"]["abstained"]+=len(associations)-measured
            state["last_reason"]=reason

    def _spatial_status(self, sid):
        state=self.spatial_state.get(sid)
        if state is None:
            return {"state":"not_requested"}
        return {"state":state["state"],"generation":state["generation"],"arms":dict(state["arms"]),
                "refused":json.loads(json.dumps(state["refused"])),"counts":dict(state["counts"]),"last_reason":state["last_reason"],
                "scheduler_error":self.spatial_error}

    def set_reasoning(self,session,enabled):
        with self.lock:
            if enabled and self.reasoning_factory is None:return {"state":"blocked","reason":"jev_not_provisioned"}
            old=self.reasoners.get(session["id"])
            if enabled and old and old.closed:
                if old.futures:return {"state":"blocked","reason":"previous_reasoning_still_active"}
                del self.reasoners[session["id"]]
            if not enabled and old:_close(old,"reasoning_disabled")
            revision=self.store.get_session(session["id"])["reasoning_revision"]+1
            self.store.append_event(session["id"],"reasoning.setting",{"enabled":enabled,"revision":revision})
            return {"state":"applied","enabled":enabled,"revision":revision}

    def _reasoning_loop(self):
        while not self.reasoning_stop.is_set():
            self.reasoning_ready.wait(self._reasoning_wait())
            self.reasoning_ready.clear()
            if self.reasoning_stop.is_set():break
            try:
                self.reasoning_tick()
            except Exception:
                # Storage failure must not kill the scheduler silently or spam
                # disk writes. Current context remains independently expiring.
                self.reasoning_errors["controller"]="evidence_scheduler_unavailable"

    def _reasoning_wait(self):
        """Wake on new output, when a rate-deferred arm becomes due, or at 1 s."""
        due=[state["wake_at"] for state in list(self.reasoning_schedule.values()) if state.get("wake_at") is not None]
        if not due:
            return 1
        return max(0.05,min(1,min(due)-self.store.clock()))

    def _reasoning_current(self, sid, runner):
        """False once a stop, disable, switch or shutdown overtook this tick."""
        try:
            session=self.store.get_session(sid)
        except Exception:
            return False
        reasoner=self.reasoners.get(sid)
        return (not self.closed and self.runners.get(sid) is runner and session["state"]=="recording"
                and session.get("reasoning_enabled",False) and not getattr(reasoner,"closed",False))

    def reasoning_tick(self):
        """Recording-owned scheduler; opening/replaying the UI never calls it.

        Evidence reads, request preparation and admission run outside the
        runtime lock, so a tick never delays preview ingestion, stop or live
        status. Each arm is offered only for new admitted camera evidence (see
        _new_evidence_arms); unchanged histories are counted, never requested.
        """
        with self.lock:
            if self.closed or self.reasoning_factory is None:
                return
            work=list(self.runners.items())
        # A deferred arm re-arms its wake-up below; nothing else wakes early.
        for state in list(self.reasoning_schedule.values()):
            state["wake_at"]=None
        for sid,runner in work:
            with self.lock:
                if self.closed:
                    return
                if self.runners.get(sid) is not runner:
                    continue
                reasoner=self.reasoners.get(sid)
                if getattr(reasoner,"closed",False) or runner.snapshot()["closed"]:
                    continue
                # Created only here, under the lock stop_session takes: a stop
                # drops it and a tick already under way never recreates it.
                schedule=self._schedule(sid)
            session=self.store.get_session(sid)
            if (session["state"]!="recording" or not session.get("reasoning_enabled",False) or runner.snapshot()["closed"]
                    or getattr(reasoner,"closed",False)):
                continue
            cutoff=self.store.clock()
            started=time.perf_counter()
            try:
                records=live_records(self.store,session,runner.generation,cutoff,window_seconds=max(runner.reasoning_windows.values()),
                                     retain_history=True,cache=self.evidence_caches.setdefault(sid,{}))
                schedule["counters"]["ticks"]+=1
                # No source evidence is not a reason to contact a provider.
                if records:
                    self.offer_reasoning(sid,records,generation=runner.generation,cutoff=cutoff,only_new=True)
                    # Content-free: evidence read, keys and (when due) the offer.
                    controller_load.observe("reasoning_offer_ms",(time.perf_counter()-started)*1000)
                self.reasoning_errors.pop(sid,None)
            except Exception:
                # A stop, disable or generation switch that overtook this tick
                # is not an ingestion failure: nothing was offered for it.
                if not self._reasoning_current(sid,runner):
                    continue
                # Content-free failure evidence; no fallback or model retry.
                if sid not in self.reasoning_errors:
                    self.reasoning_errors[sid]="live_evidence_unavailable"
                    self.store.append_event(sid,"reasoning.ingestion_failed",{
                        "generation":runner.generation,"cutoff":cutoff,"reason":"live_evidence_unavailable"})

    def _schedule(self, sid):
        state=self.reasoning_schedule.get(sid)
        if state is None:
            zero=lambda:{"a":0,"b":0}
            state=self.reasoning_schedule[sid]={"arms":{},"wake_at":None,"summary_written":False,
                "counters":{"ticks":0,"offered":zero(),"skipped":{reason:zero() for reason in REASONING_SKIPS},
                            "offer_reasons":{reason:zero() for reason in REASONING_OFFER_REASONS},"local_abstains":zero(),
                            "facts_ticks":0},
                # Facts-key cadence: last local abstention per arm and the occupancy view.
                "gates":{},"occupancy":None}
        return state

    def _evidence_keys(self, session, records, shared, *, generation, cutoff, windows, ha, caption):
        """Per arm: the admitted camera evidence ids, other admitted ids and the
        request configuration, exactly as LiveReasoning.offer would admit them."""
        from reasoning import causal_sources, shared_evidence, VISUAL_SOURCES
        keys={}
        for arm in ("a","b"):
            sources=causal_sources(records,site_id=session["site_id"],session_id=session["id"],arm=arm,generation=generation,
                cutoff=cutoff,window_seconds=windows[arm],retain_history=True)
            context=[r["id"] for r in shared_evidence(shared.get(arm),site_id=session["site_id"],session_id=session["id"],
                arm=arm,generation=generation,cutoff=cutoff)]
            keys[arm]={"visual":frozenset(i for i,kind in sources if kind in VISUAL_SOURCES),
                       "context":frozenset([i for i,kind in sources if kind not in VISUAL_SOURCES]+context),
                       "config":(generation,windows[arm],bool(ha.get(arm)),bool(caption.get(arm)),session.get("reasoning_revision"))}
        return keys

    def _new_evidence_arms(self, sid, arms, keys, now):
        """Arms to offer now. An arm is offered only when it has camera evidence
        that was not in its last offered request (or its configuration changed),
        and at most once per reasoning_min_offer_interval_s. A newer output
        inside the interval is deferred and offered, newest evidence, when the
        interval ends. Everything else is a counted skip: no request id, no
        pending row, no lifecycle receipt."""
        interval=self.reasoning_min_offer_interval_s
        state=self.reasoning_schedule.get(sid)
        if state is None:
            return []  # stopped since this tick began: its state is final
        counters=state["counters"]
        due,wake=[],None
        for arm in arms:
            key=keys[arm]
            last=state["arms"].get(arm)
            changed=last is None or last["config"]!=key["config"]
            if not key["visual"]:
                reason="no_visual_evidence"
            elif changed or key["visual"]-last["visual"]:
                # A ledger clock stepped back (now before the last offer) counts
                # as elapsed: a wall-clock correction never starves an arm.
                if last is not None and 0<=now-last["at"]<interval:
                    reason="rate_limited"
                    at=last["at"]+interval
                    wake=at if wake is None else min(wake,at)
                else:
                    due.append(arm)
                    continue
            elif key["context"]-last["context"]:
                reason="context_only"
            else:
                reason="unchanged_evidence"
            counters["skipped"][reason][arm]+=1
        state["wake_at"]=wake
        return due

    def _offered(self, sid, arms, keys, now, facts=None, reasons=None):
        state=self.reasoning_schedule.get(sid)
        if state is None:
            return
        for arm in arms:
            state["arms"][arm]={**keys[arm],"at":now,"key":((facts or {}).get(arm) or {}).get("key")}
            state["counters"]["offered"][arm]+=1
            state["counters"]["offer_reasons"][(reasons or {}).get(arm,"new_evidence")][arm]+=1

    def reasoning_scheduler_status(self, sid):
        """Content-free counters: offers made and offer opportunities skipped."""
        state=self.reasoning_schedule.get(sid)
        if state is None:
            return None
        counters=state["counters"]
        facts=counters["facts_ticks"]>0
        occupancy=state.get("occupancy")
        return {"basis":"facts_key_cadence" if facts else "new_admitted_camera_evidence",
                "min_offer_interval_s":self.reasoning_min_offer_interval_s,
                "ticks":counters["ticks"],"offered":dict(counters["offered"]),
                "skipped":{reason:dict(value) for reason,value in counters["skipped"].items()},
                "cadence":{**self.reasoning_cadence,"facts_ticks":counters["facts_ticks"],
                           "occupancy_gate":"provisioned" if occupancy and occupancy.get("gate") is not None else "not_provisioned"},
                "offer_reasons":{reason:dict(value) for reason,value in counters["offer_reasons"].items()},
                "local_abstains":dict(counters["local_abstains"])}

    def _reasoning_summary(self, sid):
        """One bounded, content-free receipt of the scheduler counters per session."""
        state=self.reasoning_schedule.get(sid)
        if state is None or state["summary_written"]:
            return
        state["summary_written"]=True
        self.evidence_caches.pop(sid,None)
        try:
            self.store.append_event(sid,"reasoning.scheduler",{**self.reasoning_scheduler_status(sid),"available_at":self.store.clock()})
        except Exception:
            pass
        finally:
            self.reasoning_schedule.pop(sid,None)

    def start_session(self, session):
        with self.lock:
            if self.closed:
                return {"state":"blocked","reason":"runtime_shutting_down"}
            recorder=self.recorders.get(session["camera_id"])
            if recorder is None:
                return {"state":"blocked","reason":"recording_source_not_provisioned"}
            viewing=session.get('experiment_id')=='apartment-viewing'
            if viewing and session['camera_id'] not in self.apartment_viewing_sources:
                return {'state':'blocked','reason':'apartment_viewing_not_provisioned'}
            outcome=recorder.start(session)
            if outcome.get("state") != "recording":
                return outcome
            sid=session["id"]
            fetch=self.preview_clients.get(session["camera_id"])
            if fetch is not None and session["camera_id"] in self.preview_sources and sid not in self.preview_pumps:
                if viewing:
                    self.apartment_viewing_sessions[sid]=self.apartment_viewing_sources[session['camera_id']].new_session(session)
                if session['camera_id'] in self.bundled_rgb_sources:
                    self.bundled_rgb_sessions[sid]=self.bundled_rgb_sources[session['camera_id']].new_session(session)
                # A 2 Hz grid (pump-grid-1): one quick retry when a capture
                # fails early in its slot, so one failure need not cost a slot.
                tap=self.preview_taps.get(session["camera_id"])
                consume=((lambda data,header:self.ingest_apartment_viewing(sid,data,header)) if viewing else
                    (lambda data,header:self.ingest_bundled_rgb(sid,data,header))
                    if session['camera_id'] in self.bundled_rgb_sources else
                    (lambda data,header:self.ingest_paired_preview(sid,data,header,fetch.take_pair(data,header)))
                    if session['camera_id'] in self.paired_preview_sources else
                    (lambda data,header:self.ingest_preview(sid,data,header)) if tap is None
                    else (lambda data,header:self._ingest_and_tap(sid,data,header,tap)))
                metric_owner=self.apartment_viewing_sessions.get(sid) if viewing else None
                idle_drain=(lambda:metric_owner.metric.finalize()) if metric_owner is not None and metric_owner.metric is not None else None
                pump=PreviewPump(fetch,consume,
                    lambda:not self.closed and self.store.get_session(sid)["state"]=="recording",retry_after=PREVIEW_RETRY_S,idle_drain=idle_drain)
                self.preview_pumps[sid]=pump
                pump.start()
            bundle_fetch=self.bundle_clients.get(session["camera_id"])
            if (bundle_fetch is not None and session["camera_id"] in self.bundle_sources and sid not in self.bundle_pumps
                    and session['camera_id'] not in self.paired_preview_sources):
                pump=PreviewPump(bundle_fetch,lambda data,calibration:self.ingest_bundle(sid,data,calibration),
                    lambda:not self.closed and self.store.get_session(sid)["state"]=="recording")
                self.bundle_pumps[sid]=pump
                pump.start()
            room=getattr(self,"room_facts",None)
            if room is not None and session["camera_id"]==room.camera_id and sid not in self.bundle_pumps:
                # Both arms' facts from one 2 Hz depth sample; the stage reads
                # the active pair's generation, None before activation.
                pump=room.pump(session,active=lambda:not self.closed and self.store.get_session(sid)["state"]=="recording",
                               generation=lambda:getattr(self.runners.get(sid),"generation",None))
                self.bundle_pumps[sid]=pump
                pump.start()
            ha_fetch=self.ha_clients.get(session["camera_id"])
            if ha_fetch is not None and sid not in self.ha_pumps:
                pump=PreviewPump(ha_fetch,lambda snapshot,_:self.ingest_ha(sid,**snapshot),
                    lambda:not self.closed and self.store.get_session(sid)["state"]=="recording",interval=1)
                self.ha_pumps[sid]=pump
                pump.start()
            caption=self.caption_sources.get(session["camera_id"])
            if caption is not None and sid not in self.caption_pumps:
                # Reads the already stored caption only; never a refresh request.
                pump=PreviewPump(lambda source=caption:(source.read(),None),lambda value,_:self.ingest_caption(sid,value),
                    lambda:not self.closed and self.store.get_session(sid)["state"]=="recording",interval=1)
                self.caption_pumps[sid]=pump
                pump.start()
            self._start_qwen_live(session)
            # Capture remains usable even if optional specialist admission fails.
            try:
                prepared=self._prepare(session,session["experiment_id"])
                if prepared["state"]=="ready":
                    generation=self._activate(session,session["experiment_id"],prepared["profiles"],prepared.get("reasoning_windows"),prepared.get("sampling_hz"),prepared.get("spatial_policy"),prepared.get("ha_evidence"),caption_evidence=prepared.get("caption_evidence"),**_lever_args(prepared))
                    # Only now, with the paired detector admission ready and
                    # activated, may the V-JEPA pump (and its LA lease) start.
                    self._start_vjepa_live(session)
                    return {"state":"recording","inference":"ready","experiment_generation":generation,
                            **({'viewing_only':True,'encoded_recording':False,'recording_artifact':'unavailable'} if viewing else {})}
                return {"state":"recording","inference":"unavailable","reason":prepared["reason"]}
            except Exception:
                pass
            return {"state":"recording","inference":"unavailable","reason":"paired_admission_unavailable"}

    def _start_qwen_live(self, session):
        """Recording-owned Qwen perceiver (caller holds the runtime lock). It
        sends only while this recording records, reasoning is enabled, the
        provisioned kill switch is on and the camera-derived person-occupancy
        gate says occupied; a failed construction leaves the recording and
        every other source untouched."""
        self._start_live_pump(session,self.qwen_live,self.qwen_live_pumps,self.qwen_live_errors,"qwen_live_unavailable")

    def _start_vjepa_live(self, session):
        """Recording-owned V-JEPA clip pump, gated like the perceiver (caller
        holds the runtime lock). It starts only after the paired detector
        admission is ready and activated (start_session, switch), and only for
        a recipe granting temporal_encoder to an arm; it then sends, and its
        pump leases the LA worker, only while that stays true. The pump thread
        releases the LA lease when it stops; a failed construction leaves the
        recording and every other source untouched."""
        sid=session["id"]
        if self.vjepa_live is None or sid in self.vjepa_live_pumps:
            return
        if not self._vjepa_arms(self.runners.get(sid)):
            self.vjepa_live_errors[sid]="temporal_encoder_not_in_recipe" if sid in self.runners else "detector_admission_not_ready"
            return
        self.vjepa_live_errors.pop(sid,None)
        self._start_live_pump(session,self.vjepa_live,self.vjepa_live_pumps,self.vjepa_live_errors,"vjepa_live_unavailable",
                              ready=lambda:bool(self._vjepa_arms(self.runners.get(sid))))

    @staticmethod
    def _vjepa_arms(runner):
        """Arms whose active, open pair grants the temporal_encoder family."""
        from evidence_policy import source_policy
        if runner is None or getattr(runner,"closed",True):
            return set()
        try:
            grants=source_policy(getattr(runner,"source_policy",None),ha=runner.ha_evidence,caption=getattr(runner,"caption_evidence",None))
        except (ValueError,AttributeError):
            return set()
        return {arm for arm in getattr(runner, 'execution_arms', ('a', 'b')) if grants["temporal_encoder"][arm]=="granted"}

    def _start_live_pump(self, session, factory, pumps, errors, unavailable, ready=None):
        sid=session["id"]
        if factory is None or sid in pumps:
            return
        # Owner requirement: the expensive model stack runs only while the
        # cheap Frigate person-occupancy sensor says someone is there. Without
        # a provisioned HA source and bound occupancy entity the pump refuses
        # to run; it is never started ungated.
        occupied=self._occupancy_gate(session,getattr(factory,"occupancy",None),getattr(factory,"occupancy_explicit",None))
        if occupied is None:
            errors[sid]="occupancy_gate_not_provisioned"
            return
        kill_switch=getattr(factory,"kill_switch",None) or (lambda:True)
        active=lambda:(not self.closed and self.store.get_session(sid)["state"]=="recording"
                       and (ready is None or ready() is True))
        enabled=lambda:kill_switch() is True and self.store.get_session(sid).get("reasoning_enabled",False) is True
        try:
            pump=factory(self.store,session,active=active,enabled=enabled,occupied=occupied)
            if getattr(pump,'person_mode',False):
                pump.evidence_ready=self.reasoning_ready
            pump.start()
        except Exception:
            errors[sid]=unavailable
            return
        pumps[sid]=pump

    def _occupancy_gate(self, session, settings=None, explicit=None):
        """The camera's provisioned in-memory occupancy reader (started here,
        shared by the pumps), else a fresh per-pump gate over this recording's
        own ha.observation rows; None when neither, or no bound camera-derived
        occupancy entity, is provisioned. The ledger gate makes no HA call.
        explicit: the settings the pump block names itself (a reader is used
        only when they equal its own).

        A reader whose entity is not the gate's bound camera-derived occupancy
        entity, or that cannot start, is passed over for the ledger gate, as
        when none is provisioned (and as for an unreadable reader trust file):
        adding a reader never ungates a pump or the JEV reasoning cadence."""
        from occupancy_gate import OccupancyGate, gate_config, reader_entity, reader_mismatch, resolve_entity
        camera=session["camera_id"]
        source=self.ha_sources.get(camera)
        reader=self.occupancy_readers.get(camera)
        if reader is not None and source is not None:
            try:
                merged={**gate_config(),**(settings or {})}
                # As the ledger gate: only with an HA source whose
                # camera-derived occupancy entity (person_present binding
                # included) is the reader's.
                bound=reader_entity(getattr(source,"camera_derived_entities",()),reader.entity,
                                    merged["entity"],getattr(self.reasoning_factory,"ha_bindings",None))
            except Exception:
                bound=None
            if bound is None:
                self._reader_passed_over(camera,reader)
            else:
                # Never with a pump-level hold or grace other than the
                # reader's own (validate_config refuses that at startup).
                if reader_mismatch({"entity":reader.entity,"hold_seconds":reader.hold,"grace_seconds":reader.grace},
                                   explicit):
                    return None
                try:
                    reader.start()
                    return reader
                except Exception:
                    self._reader_passed_over(camera,reader,"cannot start")
        if source is None or camera not in self.ha_clients:
            return None
        try:
            settings={**gate_config(),**(settings or {})}
            entity=resolve_entity(getattr(source,"camera_derived_entities",()),settings["entity"],
                                  getattr(self.reasoning_factory,"ha_bindings",None))
            if entity is None:
                return None
            return OccupancyGate(self.store,session,entity,hold_seconds=settings["hold_seconds"],
                                 grace_seconds=settings["grace_seconds"],clock=self.store.clock)
        except Exception:
            return None

    def _reader_passed_over(self, camera, reader, why="is not the bound camera-derived occupancy entity"):
        """One WARNING line per camera and runtime (the controller logs its
        operational lines at warning; nothing configures INFO)."""
        if camera in self.occupancy_readers_passed_over:
            return
        self.occupancy_readers_passed_over.add(camera)
        _occupancy_log.warning("occupancy reader passed over: camera=%s entity=%s %s; falling back to the ledger gate",
                               camera,getattr(reader,"entity",None),why)

    def _qwen_live_status(self, sid):
        return self._live_pump_status(sid,self.qwen_live,self.qwen_live_pumps,self.qwen_live_errors)

    def _vjepa_live_status(self, sid):
        return self._live_pump_status(sid,self.vjepa_live,self.vjepa_live_pumps,self.vjepa_live_errors)

    @staticmethod
    def _live_pump_status(sid, factory, pumps, errors):
        pump=pumps.get(sid)
        if pump is not None:
            return pump.snapshot()
        return {"status":errors.get(sid) or ("not_started" if factory is not None else "not_provisioned")}

    def _vjepa_context(self, session, runner, cutoff, generation):
        """(per-arm shared V-JEPA scene records, coverage status) for one tick.

        The same vjepa_scene record for every arm whose recipe grants the
        temporal_encoder family, only while the pump runs for this recording
        and the household grants vjepa-den-1; otherwise no record and an
        explicit status (a withheld arm's coverage comes from its grant)."""
        if self.vjepa_live is None or session["id"] not in self.vjepa_live_pumps:
            return {},None
        arms=self._vjepa_arms(runner)
        if not arms:
            return {},None
        check=getattr(self.reasoning_factory,"vjepa_granted",None)
        try:
            granted=callable(check) and check() is True
        except Exception:
            granted=False
        if not granted:
            return {},"not_granted"
        import vjepa_live
        rows=vjepa_live.scene_context(self.store,session,cutoff,generation=generation,arms=arms)
        record=next((r[0] for r in rows.values() if r),None)
        return rows,(record["vjepa_scene"]["status"] if record else "stale")

    def _room_facts_context(self, session, cutoff, generation):
        """{arm: [room.facts record]} for one tick: the newest valid record per
        arm while room facts are provisioned for this camera and the live wire
        reads them (jev_wire.ROOM_WIRES); else {}."""
        room=getattr(self,"room_facts",None)
        if room is None or session["camera_id"]!=room.camera_id:
            return {}
        from jev_wire import ROOM_WIRES
        if getattr(self.reasoning_factory,"wire",None) not in ROOM_WIRES:
            return {}
        from room_facts import facts_context
        return facts_context(self.store,session,cutoff,generation=generation)

    def _qwen_context(self, session, runner, cutoff, generation):
        """(per-arm shared Qwen scene records, coverage status) for one tick.

        The same record for every arm whose recipe grants the qwen_scene
        family, only while the perceiver is provisioned and the household
        grants its egress schema; otherwise no record and an explicit status."""
        from evidence_policy import source_policy
        pumped=self.qwen_live is not None and session["id"] in self.qwen_live_pumps
        if not pumped and self.la_qwen is None:
            return {},None  # no perceiver runs for this recording
        grants=source_policy(getattr(runner,"source_policy",None),ha=runner.ha_evidence,caption=getattr(runner,"caption_evidence",None))
        arms={arm for arm in getattr(runner, 'execution_arms', ('a', 'b')) if grants["qwen_scene"][arm]=="granted"}
        if not arms:
            return {},None
        check=getattr(self.reasoning_factory,"qwen_granted",None)
        try:
            granted=callable(check) and check() is True
        except Exception:
            granted=False
        if not granted:
            return {},"not_granted"
        if pumped:
            import qwen_live
            rows=qwen_live.scene_context(self.store,session,cutoff,generation=generation,arms=arms,
                horizon_seconds=getattr(self.qwen_live,"horizon_seconds",120))
        else:
            rows=self.la_qwen.context(session,cutoff,generation=generation,arms=arms)
        record=next((r[0] for r in rows.values() if r),None)
        return rows,(record["qwen_scene"]["status"] if record else "stale")

    def offer(self, session_id, frame):
        with self.lock:
            if self.closed:
                raise ValueError("runtime is shutting down")
            if self.store.get_session(session_id)["state"] != "recording":
                raise ValueError("session is not recording")
            runner=self.runners.get(session_id)
            if runner is None:
                return {"state":"unavailable","reason":"paired_admission_unavailable"}
            runner.offer(frame)
            return {"state":"offered","experiment_generation":runner.generation}

    def _ingest_and_tap(self, session_id, jpeg, metadata_header, tap):
        """The recording's preview pump with a tap: ingest first (a failure
        stays the pump's), then offer the same capture to the tap, whose own
        failure is ignored."""
        result=self.ingest_preview(session_id,jpeg,metadata_header)
        try:
            tap(jpeg,metadata_header)
        except Exception:
            pass
        return result

    def ingest_preview(self, session_id, jpeg, metadata_header):
        """Trusted source-client entrypoint; no browser-supplied source metadata."""
        with self.lock:
            if self.closed:
                raise ValueError("runtime is shutting down")
            session=self.store.get_session(session_id)
            source=self.preview_sources.get(session["camera_id"])
            if source is None:
                raise ValueError("preview source not provisioned")
            runner=self.runners.get(session_id)
            # One exact scan admits this preview object and its paired offer
            # writes. Runner lock first: the same order reconcile takes them.
            with (runner.lock if runner is not None else nullcontext()), self.store.capacity_batch(
                    (len(jpeg) if isinstance(jpeg,bytes) else 0)+65536+6*(65536+1024)):
                frame=source.ingest(session_id,jpeg,metadata_header)
                result=self.offer(session_id,frame)
            return {"media_ids":frame["media_ids"],"inference":result}

    def ingest_bundle(self, session_id, data, calibration):
        with self.lock:
            if self.closed: raise ValueError("runtime is shutting down")
            session=self.store.get_session(session_id)
            source=self.bundle_sources.get(session["camera_id"])
            if source is None: raise ValueError("numerical source not provisioned")
        # Archive decode and lossless view encoding run outside the runtime lock:
        # the ingestor re-checks the recording under the store's own lock, and
        # close()/delete_session wait for this pump thread before the store goes.
        return source.ingest(session_id,data,calibration)

    def ingest_bundled_rgb(self, session_id, jpeg, metadata_header):
        """One existing pump; all originals durable before the selected PNG offer."""
        with self.lock:
            session=self.store.get_session(session_id)
            source=self.bundled_rgb_sessions.get(session_id)
            runner=self.runners.get(session_id)
            if self.closed or source is None or runner is None or runner.closed:
                raise ValueError('bundled input is not current')
            generation=runner.generation
        try:
            # Do not hold runtime/Store locks during HTTP, NumPy or PNG work.
            frame=source.ingest(session_id,jpeg,metadata_header)
            if frame is None:return {'state':'refused','reason':source.status()['reason']}
            with self.lock:
                source._current(expiry=frame['valid_until'])
                if self.runners.get(session_id) is not runner or runner.generation!=generation:
                    raise ValueError('bundled input generation changed')
                result=self.offer(session_id,frame)
            return {'media_ids':frame['media_ids'],'inference':result,'original_family':'existing-api-bundled-rgb-1'}
        finally:
            if source.capture_count>=source.policy['max_captures'] or source.incomplete:
                pump=self.preview_pumps.get(session_id)
                if pump is not None:pump.stop()

    def ingest_apartment_viewing(self,session_id,jpeg,metadata_header):
        """Same preview owner and paired workers, exact pixels in bounded RAM."""
        source=self.apartment_viewing_sessions.get(session_id)
        if source is not None and source.metric is not None:
            # The existing pump serializes this optional capture. No runtime
            # lock spans camera HTTP, qualified proof checks or projection.
            frame=source.ingest(session_id,jpeg,metadata_header)
            with self.lock:
                source.current()
                if frame is None:return {'state':'awaiting_metric_frame','reason':source.metric.reason,'viewing_only':True,'physical_permission':False}
                result=self.offer(session_id,frame)
                source.metric.after_offer(frame)
            # This branch owns capture slots too: share the accepted original
            # with the always-on Qwen ring after releasing the runtime lock.
            tap=self.preview_taps.get(source.camera_id)
            if tap is not None:
                try:tap(jpeg,metadata_header)
                except Exception:pass
            return {'media_ids':frame['media_ids'],'inference':result,'viewing_only':True,'physical_permission':False}
        with self.lock:
            source=self.apartment_viewing_sessions.get(session_id);runner=self.runners.get(session_id)
            if self.closed or source is None or runner is None or runner.closed:
                raise ValueError('viewing source is not current')
            frame=source.ingest(session_id,jpeg,metadata_header)
            result=self.offer(session_id,frame)
        tap=self.preview_taps.get(source.camera_id)
        if tap is not None:
            try:tap(jpeg,metadata_header)
            except Exception:pass
        return {'media_ids':frame['media_ids'],'inference':result,'viewing_only':True,'physical_permission':False}

    def ingest_paired_preview(self, session_id, jpeg, metadata_header, pair):
        """Retain this accepted pair in the same Store before original RGB offer.

        Only the provisioned preview pump calls this internal port. No camera
        fetch occurs here. Stop/switch may proceed during slow retention; all
        admission state is checked again after the Store batch is released.
        """
        from copy import deepcopy
        from paired_preview_client import checked_pair, encoded, sha, sync_directory
        from reasoning import canonical, finite
        if (not isinstance(pair,dict) or set(pair)!={'jpeg','header','bundle','calibration','received_at'}
                or pair['jpeg']!=jpeg or pair['header']!=metadata_header):
            raise ValueError('one accepted original pair required')
        checked=checked_pair(jpeg,metadata_header,pair['bundle'],pair['calibration'])
        identity,fingerprint=checked['identity'],checked['fingerprint']
        with self.lock:
            session=self.store.get_session(session_id)
            camera=session['camera_id']
            runner=self.runners.get(session_id)
            source=self.paired_preview_sources.get(camera)
            if (self.closed or session['state']!='recording' or source is None
                    or runner is None or runner.closed):
                raise ValueError('paired preview admission unavailable')
            state=self.paired_preview_state.setdefault(session_id,{'identities':{},'inflight':False})
            if state['inflight']:
                raise ValueError('paired preview retention busy')
            entry=state['identities'].get(identity)
            if entry is not None and entry['fingerprint']!=fingerprint:
                raise ValueError('paired preview original identity conflict')
            if entry is None:
                if len(state['identities'])>=self.paired_preview_policy['max_pairs']:
                    raise ValueError('paired preview session budget exhausted')
                entry={'fingerprint':fingerprint,'original':None,'original_body':None,'generation':runner.generation,
                       'session_generation':session['generation'],'first_pair_received_at':pair['received_at']}
                state['identities'][identity]=entry
            if entry['generation']!=runner.generation or entry['session_generation']!=session['generation']:
                raise ValueError('paired preview generation changed')
            generation,session_generation=runner.generation,session['generation']
            state['inflight']=True
        try:
            now=self.store.clock()
            if (not finite(pair['received_at']) or not checked['observed_at']<=pair['received_at']<=now
                    or now+checked['uncertainty_seconds']>=checked['observed_at']+3):
                raise ValueError('paired preview original expired or future')
            if entry['original'] is None:
                # NPZ + RGB + depth_u16 + two worst-case lossless PNG views +
                # original JSON, with every media row's existing metadata budget.
                reserve=(len(jpeg)+len(pair['bundle'])+640*400*2+2*(640*400+65536)
                         +256*1024+6*65536)
                with self.store.capacity_batch(reserve):
                    frame=self.preview_sources[camera].ingest(session_id,jpeg,metadata_header)
                    bundle=source.ingest(session_id,pair['bundle'],pair['calibration'])
                    mapping=None
                    clock=self.paired_depth_clocks.get(camera)
                    if clock is not None:
                        from capture_timing import map_depth_time
                        mapping=map_depth_time({'source_timing_receipt':checked['receipt'],
                            'observed_at':checked['observed_at'],
                            'uncertainty_seconds':checked['uncertainty_seconds']},
                            checked['source_metadata']['frames']['depth'],clock)
                    original={'schema':'home-lab-paired-preview-original-1','version':1,
                        'session_id':session_id,'site_id':session['site_id'],'camera_id':camera,
                        'experiment_generation':generation,'store_generation':session_generation,
                        'capture_epoch':checked['receipt']['session'],'rgb_frame_id':frame['frame_id'],
                        'rgb_media_id':frame['media_ids'][0],'rgb_sha256':sha(jpeg),
                        'rgb_receipt':deepcopy(checked['receipt']),'rgb_valid_until':frame['valid_until'],
                        'first_pair_received_at':entry['first_pair_received_at'],'retained_at':self.store.clock(),
                        'bundle_media_id':bundle['id'],'bundle_sha256':bundle['sha256'],
                        'calibration':deepcopy(pair['calibration']),
                        'calibration_sha256':sha(encoded(pair['calibration'])),
                        'source_metadata':deepcopy(checked['source_metadata']),
                        'depth_clock_policy':deepcopy(clock),'depth_clock_mapping':mapping,
                        'depth_clock_status':'commissioned_mapping' if mapping is not None else 'unavailable',
                        'geometric_validity':'unvalidated','operational_geometry':None}
                    if entry['original_body'] is None:
                        entry['original_body']=deepcopy(original)
                    original=entry['original_body']
                    raw=encoded(original)
                    if len(raw)>256*1024:
                        raise ValueError('paired preview original exceeds bound')
                    receipt=self.store.add_media(session_id,raw,'application/json',{
                        'stream':'paired_preview_original','format':'home-lab-paired-preview-original-1',
                        'capture_epoch':original['capture_epoch'],'frame_id':frame['frame_id'],
                        'rgb_media_id':original['rgb_media_id'],'bundle_media_id':bundle['id'],
                        'geometric_validity':'unvalidated'},canonical(['paired-preview-v1',identity]))
                    directory=self.store.media_path(session_id,receipt['id']).parent
                    sync_directory(directory)
                    sync_directory(directory.parent)
                    entry['original']=receipt
            # Never hold Store's capacity/ledger lock while acquiring runtime:
            # Stop takes runtime first, then ledger while it drains.
            with self.lock:
                current=self.store.get_session(session_id)
                now=self.store.clock()
                if (self.closed or current['state']!='recording' or current['generation']!=session_generation
                        or self.runners.get(session_id) is not runner or runner.closed or runner.generation!=generation
                        or now+checked['uncertainty_seconds']>=checked['observed_at']+3):
                    raise ValueError('paired preview stopped switched or expired during retention')
                result=self.ingest_preview(session_id,jpeg,metadata_header)
            tap=self.preview_taps.get(camera)
            if tap is not None:
                try:tap(jpeg,metadata_header)
                except Exception:pass
            return {**result,'paired_original_media_id':entry['original']['id'],
                    'paired_original_sha256':entry['original']['sha256'],'geometric_validity':'unvalidated'}
        finally:
            with self.lock:
                state['inflight']=False
                if len(state['identities'])>=self.paired_preview_policy['max_pairs']:
                    state.update(status='exhausted',pairs_consumed=len(state['identities']),
                                 max_pairs=self.paired_preview_policy['max_pairs'])
                    # The final offer has returned, or its bounded original
                    # attempt failed. Stop only this existing preview pump;
                    # workers keep settling and the recording stays available.
                    pump=self.preview_pumps.get(session_id)
                    if pump is not None:pump.stop()

    def switch(self, session, request):
        with self.lock:
            if self.closed:
                return {"state":"blocked","reason":"runtime_shutting_down"}
            if session.get('experiment_id')=='apartment-viewing' or request.get('experiment_id')=='apartment-viewing':
                return {'state':'blocked','reason':'viewing_recipe_requires_new_session'}
            if set(request) != {"experiment_id","request_id"}:
                return {"state":"blocked","reason":"atomic_pair_switch_required"}
            old=self.runners.get(session["id"])
            if old and any(old.snapshot()["active"].values()):
                return {"state":"blocked","reason":"previous_pair_not_terminal"}
            try:
                prepared=self._prepare(session,request["experiment_id"])
                if prepared["state"]!="ready":
                    return prepared
                generation=self._activate(session,request["experiment_id"],prepared["profiles"],prepared.get("reasoning_windows"),prepared.get("sampling_hz"),prepared.get("spatial_policy"),prepared.get("ha_evidence"),caption_evidence=prepared.get("caption_evidence"),**_lever_args(prepared))
            except Exception:
                return {"state":"blocked","reason":"replacement_pair_preparation_failed"}
            self._start_vjepa_live(session)
            return {"state":"applied","experiment_id":request["experiment_id"],"experiment_generation":generation}

    def offer_reasoning(self, session_id, records, *, generation, cutoff, only_new=False):
        """Server-side ingestion of normalized, time-mapped observations only.

        No browser API accepts evidence or credentials for this operation. The
        source adapter must supply actual observation and availability times.
        A direct call offers both arms. The recording-owned scheduler passes
        only_new=True: an arm is offered only for new admitted camera evidence,
        and unchanged histories are counted instead of requested.

        The runtime lock covers only the runtime state checks: evidence
        preparation and admission run outside it. LiveReasoning.offer re-checks
        the recording, generation and closed state inside its own transaction.
        """
        from evidence_policy import filter_ha, caption_policy, ha_policy
        from caption_history import caption_context
        with self.lock:
            if self.closed:
                return {"state":"unavailable","reason":"runtime_shutting_down"}
            if self.reasoning_factory is None:
                return {"state":"unavailable","reason":"reasoning_not_provisioned"}
            session=self.store.get_session(session_id)
            if session["state"] != "recording":
                raise ValueError("session is not recording")
            runner=self.runners.get(session_id)
            if runner is None or runner.generation != generation:
                raise ValueError('reasoning requires the active experiment generation')
            if not only_new and session_id not in self.reasoners:
                self.reasoners[session_id] = self._new_reasoner(session_id)
            ha,caption,windows=runner.ha_evidence,getattr(runner,"caption_evidence",None),runner.reasoning_windows
        if getattr(self.reasoning_factory,'person_mode',False):
            return self._offer_person_reasoning(session_id,runner,generation,cutoff,only_new)
        shared_rows=records
        records=filter_ha(records,ha)
        # The same latest caption for every granted arm, whatever its window.
        captions=caption_context(self.store,session,cutoff,generation=generation,policy=caption)
        # The same continuous Qwen scene record for every granted arm. It is
        # shared context only: never a scheduling key, so a new Qwen receipt
        # alone never makes an arm due for a JEV call.
        qwen,qwen_status=self._qwen_context(session,runner,cutoff,generation)
        # Likewise the V-JEPA scene record (temporal_encoder): shared context
        # for the granted arms only, never visual and never a scheduling key.
        vjepa,vjepa_status=self._vjepa_context(session,runner,cutoff,generation)
        # The latest room facts record per arm, only for a wire that reads it
        # (den-facts-8): every other wire's requests stay exactly as before.
        room=self._room_facts_context(session,cutoff,generation)
        shared={arm:list(captions.get(arm,[]))+list(qwen.get(arm,[]))+list(vjepa.get(arm,[]))+list(room.get(arm,[]))
                for arm in ("a","b") if captions.get(arm) or qwen.get(arm) or vjepa.get(arm) or room.get(arm)}
        # One shared context per tick from the same (unfiltered) rows for both
        # arms: equal digests unless a family is this experiment's lever.
        context=self._shared_context(session,runner,shared_rows,cutoff,captions,qwen=qwen_status,vjepa=vjepa_status)
        arms,keys,facts,reasons=getattr(runner, 'execution_arms', ('a', 'b')),None,None,None
        if only_new:
            keys=self._evidence_keys(session,records,captions,generation=generation,cutoff=cutoff,windows=windows,
                                     ha=ha_policy(ha),caption=caption_policy(caption))
            # Facts-key cadence when the reasoner computes facts keys (v6 wire);
            # else the new-admitted-evidence rule.
            facts=self._facts(session_id,records,shared,context,generation=generation,cutoff=cutoff,windows=windows)
            if facts is not None:
                arms,reasons=self._facts_arms(session_id,session,keys,facts,generation=generation,now=cutoff)
            else:
                arms=tuple(self._new_evidence_arms(session_id,arms,keys,cutoff))
            if not arms:
                return {"state":"skipped","reason":"no_new_evidence","request_ids":[]}
        with self.lock:
            # Re-checked: a stop, disable or switch may have landed while the
            # request was prepared. set_reasoning and stop_session take this
            # lock, so after it no reasoner is created and nothing is offered;
            # a reasoner closed after this block refuses the offer itself.
            if self.closed:
                return {"state":"unavailable","reason":"runtime_shutting_down"}
            if self.runners.get(session_id) is not runner:
                raise ValueError('reasoning requires the active experiment generation')
            current=self.store.get_session(session_id)
            reasoner=self.reasoners.get(session_id)
            if current["state"]!="recording" or runner.snapshot()["closed"] or getattr(reasoner,"closed",False):
                return {"state":"unavailable","reason":"session_not_recording","request_ids":[]}
            if only_new and not current.get("reasoning_enabled",False):
                return {"state":"unavailable","reason":"reasoning_disabled","request_ids":[]}
            if reasoner is None:
                reasoner=self.reasoners[session_id]=self._new_reasoner(session_id)
        if only_new and facts is None:
            # A reasoner created by this offer: bind its first answers' facts keys.
            facts=self._facts(session_id,records,shared,context,generation=generation,cutoff=cutoff,windows=windows,reasoner=reasoner)
        extra={"facts_keys":{arm:(facts.get(arm) or {}).get("key") for arm in arms}} if facts else {}
        ids = reasoner.offer(records, generation=generation, cutoff=cutoff,windows=windows,shared_context=shared,arms=arms,context_snapshot=context,**extra)
        if keys is not None:
            self._offered(session_id,arms,keys,cutoff,facts,reasons)
        return {"state":"offered","request_ids":ids}

    def _offer_person_reasoning(self, sid, runner, generation, cutoff, only_new):
        """One batch on the existing B queue, keyed by new frozen person facts.

        A Qwen receipt, not another camera frame, makes this source due. The
        incumbent occupancy, interval, recording and stop checks still apply.
        """
        with self.lock:
            session=self.store.get_session(sid)
            if (self.closed or self.runners.get(sid) is not runner or runner.closed
                    or session['state']!='recording' or not session.get('reasoning_enabled',False)):
                return {'state':'unavailable','reason':'person_reasoning_not_active','request_ids':[]}
            reasoner=self.reasoners.get(sid)
            if reasoner is None:
                reasoner=self.reasoners[sid]=self._new_reasoner(sid)
            if reasoner.closed:
                return {'state':'unavailable','reason':'person_reasoning_not_active','request_ids':[]}
            state=self._schedule(sid)
            state['person_mode']=True
        facts=reasoner.facts_keys([],generation=generation,cutoff=cutoff,arms=('b',))
        info=(facts or {}).get('b') or {}
        key=info.get('key')
        if not isinstance(key,str) or not isinstance(info.get('person_context'),dict):
            return {'state':'skipped','reason':'no_current_person_facts','request_ids':[]}
        with self.store.lock:
            bridge = getattr(self.store, '_qualified_desk_person_bridges', {}).get(sid)
        if bridge is not None:
            from lab_desk_person_bridge import DeskPersonBridgeSource
            if type(bridge) is not DeskPersonBridgeSource or bridge.store is not self.store or bridge.scope['session_id'] != sid:
                raise ValueError('same_fixed_qualified_desk_person_bridge_required')
            before = info['person_context']
            # This is local preparation from already ACKed desk originals.
            # Its own actual retention time must precede the request cutoff;
            # neither the original image lease nor its timestamps are renewed.
            prepared = bridge.prepare_context(before)
            cutoff = self.store.clock()
            refreshed = (reasoner.facts_keys([],generation=generation,cutoff=cutoff,arms=('b',)) or {}).get('b') or {}
            if refreshed.get('person_context') != before:
                return {'state':'skipped','reason':'person_context_changed_during_qualification','request_ids':[]}
            info, key = refreshed, refreshed.get('key')
            state['person_bridge'] = {'status': prepared.get('status'), 'reason': prepared.get('reason')}
        with self.lock:
            if self.closed or self.runners.get(sid) is not runner or self.store.get_session(sid)['state']!='recording':
                return {'state':'unavailable','reason':'person_reasoning_not_active','request_ids':[]}
            state['counters']['facts_ticks']+=1
            gated,vacant,_occupied=self._occupancy(session,state,cutoff)
            if gated:
                last_gate=state['gates'].get('b')
                if last_gate is None or not 0<=cutoff-last_gate<self.reasoning_cadence['gate_keepalive_s']:
                    reasoner.local_abstain('b',generation=generation,now=cutoff,
                        reason='occupancy_vacant' if vacant else 'occupancy_unknown')
                    state['gates']['b']=cutoff
                    state['counters']['local_abstains']['b']+=1
                return {'state':'skipped','reason':'occupancy_gate','request_ids':[]}
            last=state.get('person_last')
            if last is not None:
                if last['key']==key:
                    return {'state':'skipped','reason':'unchanged_person_facts','request_ids':[]}
                if 0<=cutoff-last['at']<self.reasoning_min_offer_interval_s:
                    state['wake_at']=last['at']+self.reasoning_min_offer_interval_s
                    return {'state':'skipped','reason':'rate_limited','request_ids':[]}
        ids=reasoner.offer([],generation=generation,cutoff=cutoff,arms=('b',),facts_keys={'b':key})
        if ids:
            with self.lock:
                state['person_last']={'key':key,'at':cutoff}
                state['counters']['offered']['b']+=1
                state['counters']['offer_reasons']['facts_changed']['b']+=1
        return {'state':'offered' if ids else 'skipped','request_ids':ids}

    def _new_reasoner(self, sid):
        reasoner=self.reasoning_factory(self.store, sid)
        source = getattr(self, 'la_qwen', None)
        bind = getattr(reasoner, 'bind_reading_qwen', None)
        if source is not None and callable(bind):
            bind(source)
        if hasattr(reasoner,"hold_max_s"):
            reasoner.hold_max_s=self.reasoning_cadence["hold_max_s"]
        return reasoner

    def _facts(self, sid, records, shared, context, *, generation, cutoff, windows, reasoner=None):
        """Per-arm facts ({'key','people',...}) from the session's reasoner, or
        None: cadence mode new_evidence, no reasoner yet, or no facts key (the
        v5 wire). Local only; nothing is admitted or sent."""
        if self.reasoning_cadence["mode"]!="facts_key":
            return None
        reasoner=reasoner if reasoner is not None else self.reasoners.get(sid)
        compute=getattr(reasoner,"facts_keys",None)
        if reasoner is None or getattr(reasoner,"closed",False) or not callable(compute):
            return None
        try:
            facts=compute(records,generation=generation,cutoff=cutoff,windows=windows,shared_context=shared,context_snapshot=context)
        except Exception:
            return None
        if not isinstance(facts,dict) or not any(isinstance(v,dict) and isinstance(v.get("key"),str) for v in facts.values()):
            return None
        return facts

    def _occupancy(self, session, state, now):
        """(gated, vacant, occupied) for this tick; occupied is None when no
        occupancy gate is provisioned (then nothing is gated on occupancy)."""
        occupancy=state.get("occupancy")
        if occupancy is None:
            occupancy=state["occupancy"]={"gate":self._occupancy_gate(session,getattr(self.qwen_live,"occupancy",None)),"track":{}}
        gate=occupancy["gate"]
        if gate is None:
            return False,False,None
        from occupancy_gate import check
        _,reading=check(gate)
        return occupancy_step(occupancy["track"],reading["state"],now,getattr(gate,"hold",REASONING_CADENCE_DEFAULTS["keepalive_s"]))

    def _facts_arms(self, sid, session, keys, facts, *, generation, now):
        """Arms due under the facts-key cadence (cadence_decision). Each tick
        also closes delivered answers whose facts changed (observe_facts) and,
        while the occupancy gate holds, answers locally instead of requesting."""
        state=self.reasoning_schedule.get(sid)
        if state is None:
            return (),{}  # stopped since this tick began: its state is final
        counters,settings=state["counters"],self.reasoning_cadence
        counters["facts_ticks"]+=1
        gated,vacant,occupied=self._occupancy(session,state,now)
        reasoner=self.reasoners.get(sid)
        due,reasons,wake=[],{},None
        for arm in ("a","b"):
            info=facts.get(arm) if isinstance(facts.get(arm),dict) else {}
            runner = self.runners.get(sid)
            if runner is not None and arm not in getattr(runner, 'execution_arms', ('a', 'b')):
                continue
            key=info.get("key") if isinstance(info.get("key"),str) else None
            observe=getattr(reasoner,"observe_facts",None)
            if callable(observe):
                try:
                    observe(arm,generation=generation,key=key,now=now,vacant=vacant)
                except Exception:
                    pass
            present=occupied if occupied is not None else info.get("people") in ("one","multiple")
            hold=None
            if info.get("camera")=="expired_historical":
                expired_at,deadline=self._inflight_frame(sid,arm,generation,now)
                hold=inflight_hold(camera=info.get("camera"),now=now,expired_at=expired_at,inflight_deadline=deadline,
                                   debounce_s=settings["debounce_s"])
            action,reason,at=cadence_decision(state["arms"].get(arm),now=now,key=key,visual=bool(keys[arm]["visual"]),
                config=keys[arm]["config"],gated=gated,occupied=present,interval=self.reasoning_min_offer_interval_s,settings=settings,
                hold_until=hold)
            if action=="offer":
                due.append(arm)
                reasons[arm]=reason
                state["gates"].pop(arm,None)
                continue
            counters["skipped"][reason][arm]+=1
            if at is not None:
                wake=at if wake is None else min(wake,at)
            if action!="gate":
                state["gates"].pop(arm,None)
                continue
            last=state["gates"].get(arm)
            abstain=getattr(reasoner,"local_abstain",None)
            if callable(abstain) and (last is None or not 0<=now-last<settings["gate_keepalive_s"]):
                try:
                    abstain(arm,generation=generation,reason="occupancy_vacant" if vacant else "occupancy_unknown",now=now)
                    state["gates"][arm]=now
                    counters["local_abstains"][arm]+=1
                except Exception:
                    pass
        state["wake_at"]=wake
        return tuple(due),reasons

    def _inflight_frame(self, sid, arm, generation, now):
        """(expired_at, inflight_deadline) for an arm: the validity of its newest
        delivered frame, and the latest deadline (runtime.arm_valid_until) of a
        dispatched, unfinished request for a newer frame that has not passed,
        else None. Read from the paired ledger only; a read failure holds nothing."""
        from runtime import arm_valid_until
        try:
            with self.store.lock:
                rows=self.store.db.execute("""SELECT state,json_extract(payload,'$.frame.observed_at'),
                    coalesce(json_extract(payload,'$.preview_timing.original_valid_until'),json_extract(payload,'$.frame.valid_until')),json_extract(payload,'$.sampling_hz') FROM paired_requests
                    WHERE session_id=? AND arm=? AND generation=? AND (state IN ('dispatching','running')
                    OR id=(SELECT id FROM paired_requests WHERE session_id=? AND arm=? AND generation=? AND state='completed'
                           AND updated_at<=? ORDER BY updated_at DESC LIMIT 1))""",
                    (sid,arm,generation,sid,arm,generation,now)).fetchall()
            valid=lambda observed,until,hz:arm_valid_until({"observed_at":observed,"valid_until":until},hz)
            done=[(observed,valid(observed,until,hz)) for state,observed,until,hz in rows if state=="completed"]
            if not done:
                return None,None
            observed,expired_at=done[0]
            pending=[valid(o,until,hz) for state,o,until,hz in rows if state!="completed" and o is not None and o>observed]
        except Exception:
            return None,None
        return expired_at,max((d for d in pending if d>now),default=None)

    def _shared_context(self, session, runner, records, cutoff, captions, qwen=None, vjepa=None):
        from ambient_context import shared_context, zone
        from evidence_policy import source_policy
        factory=self.reasoning_factory

        def read(name, default):
            # Per-tick reads of the household policy; a failure is the safe default.
            check=getattr(factory,name,None)
            try:
                return check() if callable(check) else default
            except Exception:
                return default
        # Ambient needs the operator zone and the household schema grant.
        provisioned=read("ambient_provisioned",False) is True
        state=read("ambient_state",None)
        fact_grants=read("fact_grants",{})
        grants=source_policy(getattr(runner,"source_policy",None),ha=runner.ha_evidence,caption=getattr(runner,"caption_evidence",None))
        return shared_context(records,cutoff,grants=grants,lever=getattr(runner,"lever_families",None) or (),
            tz=zone(getattr(factory,"site_timezone",None)) if provisioned else None,provisioned=provisioned,
            bindings=getattr(factory,"ha_bindings",None),ha_provisioned=session["camera_id"] in getattr(self,"ha_clients",{}),
            captions={arm:bool(rows) for arm,rows in (captions or {}).items()},
            fact_grants=fact_grants if isinstance(fact_grants,dict) else {},qwen=qwen,vjepa=vjepa,
            ambient_state=state if state in ("granted","not_granted","not_provisioned") else None)

    def stop_session(self, session):
        with self.lock:
            viewing=self.apartment_viewing_sessions.get(session['id'])
            if viewing is not None:viewing.stop()
            source=self.bundled_rgb_sessions.get(session['id'])
            if source is not None:source.stop()
            pump=self.preview_pumps.get(session["id"])
            if pump: pump.stop()
            pump=self.bundle_pumps.get(session["id"])
            if pump: pump.stop()
            pump=self.ha_pumps.get(session["id"])
            if pump: pump.stop()
            pump=self.caption_pumps.get(session["id"])
            if pump: pump.stop()
            # The perceiver stops with the other readers, before the reasoner
            # closes and the recorder drains; an in-flight call ends within
            # its own bound and is reconciled by identity, never resent.
            pump=self.qwen_live_pumps.get(session["id"])
            if pump: pump.stop()
            pump=self.vjepa_live_pumps.get(session["id"])
            if pump: pump.stop()
            spatial=self.spatial_state.get(session["id"])
            if spatial and spatial["state"]=="active":
                spatial["state"]="stopped"
            # Reasoning and specialist dispatch end before the recorder drains:
            # no new request is prepared or admitted while it finalizes.
            reasoner=self.reasoners.get(session["id"])
            if reasoner:
                _close(reasoner,"session_stopped")
            runner=self.runners.get(session["id"])
            if runner:
                runner.close()  # active work keeps its durable reservation
            recorder=self.recorders.get(session["camera_id"])
            if recorder is None:
                outcome={"state":"indeterminate","reason":"recorder_not_reconciled"}
            else:
                outcome=recorder.stop(session)
            # After the drain: the counters receipt never delays finalization.
            self._reasoning_summary(session["id"])
            return outcome

    def _delivery(self, sid, arm, generation, latest, now):
        """Per-arm cadence from indexed ledger rows. A completion counts as
        fresh when it arrived by its arm validity (runtime.arm_valid_until:
        the source valid_until, extended only for an arm sampled below the
        source rate), the validity its output carries."""
        from runtime import arm_valid_until
        with self.store.lock:
            # Completion times come from the covering (session,arm,generation,
            # state,updated_at) index; request payloads are read only for the
            # last 30 s that the freshness counts use.
            times=[row[0] for row in self.store.db.execute("""SELECT updated_at FROM paired_requests
                WHERE session_id=? AND arm=? AND generation=? AND state='completed' ORDER BY updated_at""",(sid,arm,generation))]
            done=self.store.db.execute("""SELECT updated_at,coalesce(json_extract(payload,'$.preview_timing.original_valid_until'),json_extract(payload,'$.frame.valid_until')),
                json_extract(payload,'$.frame.observed_at'),json_extract(payload,'$.sampling_hz') FROM paired_requests
                WHERE session_id=? AND arm=? AND generation=? AND state='completed' AND updated_at>=? ORDER BY updated_at""",
                (sid,arm,generation,now-30)).fetchall()
            dispatched=self.store.db.execute("SELECT max(dispatched_at) FROM paired_dispatch WHERE session_id=? AND arm=? AND generation=?",
                (sid,arm,generation)).fetchone()[0]
            offered=self.store.db.execute("""SELECT created_at FROM events WHERE session_id=? AND kind='perception.offered'
                AND json_extract(payload,'$.arm')=? AND json_extract(payload,'$.generation')=? ORDER BY id DESC LIMIT 1""",(sid,arm,generation)).fetchone()
        def valid(until, observed, hz):
            if type(until) not in (int,float) or type(observed) not in (int,float) or type(hz) not in (int,float) or hz<=0:
                return until
            return arm_valid_until({"observed_at":observed,"valid_until":until},hz)
        done=[(at,valid(until,observed,hz)) for at,until,observed,hz in done]
        def count(window,fresh):
            return sum(1 for at,valid in done if at>=now-window and (not fresh or (valid is not None and at<=valid)))
        def age(key):
            value=latest.get(key)
            return now-value if type(value) in (int,float) else None
        return {"source_age_s":age("observed_at"),"output_age_s":age("available_at"),
                "fresh_10s":count(10,True),"fresh_30s":count(30,True),"completed_30s":count(30,False),
                "max_gap_s":max((b-a for a,b in zip(times,times[1:])),default=None),
                "last_offered_at":offered[0] if offered else None,"last_dispatched_at":dispatched,
                "last_completed_at":times[-1] if times else None}

    def _latest_live_payload(self, sid, kind, arm, generation):
        """Parsed payload of the arm's newest `kind` event, or None.

        The same indexed row is selected every poll; ledger events are
        immutable, so each one is parsed once. Callers must not mutate it.
        """
        with self.store.lock:
            row=self.store.db.execute("SELECT id FROM events WHERE session_id=? AND kind=? AND json_extract(payload,'$.arm')=? AND json_extract(payload,'$.generation')=? ORDER BY id DESC LIMIT 1",(sid,kind,arm,generation)).fetchone()
            if row is None:
                return None
            value=self._live_payloads.get(row[0])
            if value is None:
                value=json.loads(self.store.db.execute("SELECT payload FROM events WHERE id=?",(row[0],)).fetchone()[0])
                self._live_payloads[row[0]]=value
                while len(self._live_payloads)>32:
                    self._live_payloads.pop(next(iter(self._live_payloads)))
        return value

    def live(self, session):
        now=self.store.clock()
        pump=self.preview_pumps.get(session["id"])
        acquisition=pump.counters() if pump is not None else None
        runner=self.runners.get(session["id"])
        if runner is None:
            return {"state":"unavailable","arms":{},"reason":"recording_only_commissioning" if self.recording_only else "paired_admission_unavailable",
                    "server_time":now,"acquisition":acquisition,
                    "preview_status":self.preview_pumps[session["id"]].status if session["id"] in self.preview_pumps else "not_provisioned",
                    "ha_status":self.ha_pumps[session["id"]].status if session["id"] in self.ha_pumps else "not_provisioned",
                    "numerical_status":self.bundle_pumps[session["id"]].status if session["id"] in self.bundle_pumps else "not_provisioned",
                    "spatial_status":self._spatial_status(session["id"]),
                    "qwen_live_status":self._qwen_live_status(session["id"]),
                    "vjepa_live_status":self._vjepa_live_status(session["id"]),
                    **self._caption_status(session["id"]),**self._room_facts_status(session["id"])}
        snapshot=runner.snapshot()
        arms={}
        for arm in ("a","b"):
            if arm not in runner.execution_arms:
                arms[arm.upper()]={"id":arm.upper(),"status":"disabled","reason":"not_in_execution_scope",
                    "active_request":None,"pending_request":None,
                    "reasoning_status":{"state":"off","current":False}}
                continue
            value=self._latest_live_payload(session["id"],"perception.output",arm,runner.generation) or {}
            rendered=value.get("arm_snapshot",{"id":arm.upper(),"status":"unavailable"})
            if isinstance(rendered,dict):
                rendered=dict(rendered)  # the parsed payload is shared between polls
            if value and (snapshot["closed"] or value.get("valid_until",0)<self.store.clock()):
                rendered["status"]="stale"
            arms[arm.upper()]={**rendered,"active_request":snapshot["active"][arm],"pending_request":snapshot["pending"][arm],
                "delivery":self._delivery(session["id"],arm,runner.generation,value,now)}
            if value:
                # Exact clocks and identity of this same rendered output. A
                # separately read graph or delivery aggregate may advance.
                observation={"schema":"home-lab-arm-observation-1","session_id":session["id"],
                    **{key:value.get(key) for key in ("request_id","arm","site_id","camera_id","frame_id",
                        "media_ids","capture_epoch","generation","run_id","model_version","artifact_sha256",
                        "engine_epoch","observed_at","available_at","valid_until")}}
                if isinstance(observation["media_ids"],list):observation["media_ids"]=list(observation["media_ids"])
                arms[arm.upper()]["observation"]=observation
            viewing=self.apartment_viewing_sessions.get(session['id'])
            if arm=='b' and viewing is not None and viewing.metric is not None:
                approximate=viewing.metric.snapshot()
                if approximate is not None:arms[arm.upper()]['approximate_room_pose']=approximate
                virtual_preview=viewing.metric.preview_snapshot(arms[arm.upper()].get('observation'),session_state=session['state'])
                if virtual_preview is not None:arms[arm.upper()]['virtual_target_preview']=virtual_preview
            judgment=self._latest_live_payload(session["id"],"reasoning.output",arm,runner.generation)
            if judgment is not None:
                if self.store.get_session(session["id"]).get("reasoning_enabled") and not snapshot["closed"]:
                    arms[arm.upper()].update({"judgment":judgment["judgment"],"recommendation":judgment["recommendation"],
                        "reasoning_fresh":judgment["valid_until"]>=self.store.clock(),
                        "reasoning_source_end_s":judgment["source_end_s"],"reasoning_available_at_s":judgment["available_at_s"]})
            with self.store.lock:
                delivery=self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.delivery' AND json_extract(payload,'$.arm')=? AND json_extract(payload,'$.generation')=? ORDER BY id DESC LIMIT 1",(session["id"],arm,runner.generation)).fetchone()
            enabled=self.store.get_session(session["id"]).get("reasoning_enabled",False)
            status=json.loads(delivery[0]) if delivery else {}
            arms[arm.upper()]["reasoning_status"]={"state":("off" if not enabled else status.get("status","awaiting_evidence")),
                "available_at":status.get("available_at"),"current":bool(status.get("current")) and enabled}
            if enabled and arms[arm.upper()].get("reasoning_fresh") is False:
                arms[arm.upper()]["reasoning_status"]["state"]="stale — historical judgment only"
        return {"state":"stopped" if snapshot["closed"] else "ready","arms":arms,"experiment_generation":runner.generation,
                "execution_arms":list(runner.execution_arms),"comparison":len(runner.execution_arms)==2,
                **({'apartment_viewing':self.apartment_viewing_sessions[session['id']].status()}
                   if session['id'] in self.apartment_viewing_sessions else {}),
                "server_time":now,"acquisition":acquisition,
                "reasoning_ingestion_error":self.reasoning_errors.get(session["id"],self.reasoning_errors.get("controller")),
                "reasoning_scheduler":self.reasoning_scheduler_status(session["id"]),
                "preview_status":self.preview_pumps[session["id"]].status if session["id"] in self.preview_pumps else "not_provisioned",
                "ha_status":self.ha_pumps[session["id"]].status if session["id"] in self.ha_pumps else "not_provisioned",
                    "numerical_status":self.bundle_pumps[session["id"]].status if session["id"] in self.bundle_pumps else "not_provisioned",
                    "spatial_status":self._spatial_status(session["id"]),
                    "qwen_live_status":self._qwen_live_status(session["id"]),
                    "vjepa_live_status":self._vjepa_live_status(session["id"]),
                    **self._caption_status(session["id"]),**self._room_facts_status(session["id"])}

    def _caption_status(self, sid):
        # Reader health plus the last poll outcome (available, expired, missing,
        # out_of_order, expired_on_receipt, identity_conflict, ...); no content.
        pump=self.caption_pumps.get(sid)
        return {"caption_status":pump.status if pump is not None else "not_provisioned",
                "caption_poll_state":self.caption_states.get(sid)}

    def _room_facts_status(self, sid):
        # Only while room facts are provisioned: counts, timings and the
        # stage's state (active, mount_unverified, or why it stopped).
        room=getattr(self,"room_facts",None)
        return {} if room is None else {"room_facts_status":room.status(sid)}

    def reap_finished_sessions(self):
        """Stop optional dispatch after recorder-owned termination, without UI."""
        with self.lock:
            stopped=[]
            session_ids=set(self.runners)|set(self.reasoners)|set(self.preview_pumps)|set(self.bundle_pumps)|set(self.ha_pumps)|set(self.caption_pumps)|set(self.qwen_live_pumps)|set(self.vjepa_live_pumps)
            for sid in session_ids:
                if self.store.get_session(sid)["state"] in {"starting","recording"}:
                    continue
                for pumps in (self.preview_pumps,self.bundle_pumps,self.ha_pumps,self.caption_pumps,self.qwen_live_pumps,self.vjepa_live_pumps):
                    if sid in pumps:pumps[sid].stop()
                if self.spatial_state.get(sid,{}).get("state")=="active":
                    self.spatial_state[sid]["state"]="stopped"
                for workers in (self.runners,self.reasoners):
                    if sid in workers:_close(workers[sid],"session_ended")
                self._reasoning_summary(sid)
                stopped.append(sid)
            return {"state":"reconciled","closed_sessions":stopped}

    def close(self, timeout=5):
        """Close dispatch, then drain local users of SQLite before store closure.

        A finished local Future does not release an indeterminate remote job.
        Those reservations stay durable across shutdown. If local work is still
        executing, the application must retain the store and OS ownership lock.
        """
        deadline=time.monotonic()+max(0,min(timeout,30))
        with self.lock:
            self.closed=True
            for source in self.apartment_viewing_sessions.values():source.stop()
            for source in self.bundled_rgb_sessions.values():source.stop()
            self.reasoning_stop.set()
            self.reasoning_ready.set()
            self.spatial_stop.set()
            self.spatial_ready.set()
            for state in self.spatial_state.values():
                if state["state"]=="active":state["state"]="stopped"
            for pump in self.preview_pumps.values(): pump.stop()
            for pump in self.bundle_pumps.values(): pump.stop()
            for pump in self.ha_pumps.values(): pump.stop()
            for pump in self.caption_pumps.values(): pump.stop()
            for pump in self.qwen_live_pumps.values(): pump.stop()
            for pump in self.vjepa_live_pumps.values(): pump.stop()
            for reader in self.occupancy_readers.values(): reader.stop()
            if self.la_qwen is not None: self.la_qwen.close()
            workers=list(self.runners.values())+list(self.reasoners.values())
            jobs=[j for j in (self.analysis_jobs,self.preparation_jobs) if j is not None]
            futures=set()
            for worker in workers:
                _close(worker,"runtime_shutdown")
                with worker.lock:
                    futures.update(worker.futures)
            for sid in list(self.reasoning_schedule):
                self._reasoning_summary(sid)
            for worker in jobs:
                worker.close()
                with worker.lock:
                    futures.update(f for f,_ in worker.work.values())
            threads=[r.thread for r in self.recorders.values() if getattr(r,"thread",None) is not None]
            if self.reasoning_thread is not None:
                threads.append(self.reasoning_thread)
            if self.spatial_thread is not None:
                threads.append(self.spatial_thread)
            threads.extend(pump.thread for pump in self.preview_pumps.values())
            threads.extend(pump.thread for pump in self.bundle_pumps.values())
            threads.extend(pump.thread for pump in self.ha_pumps.values())
            threads.extend(pump.thread for pump in self.caption_pumps.values())
            threads.extend(pump.thread for pump in self.qwen_live_pumps.values())
            threads.extend(pump.thread for pump in self.vjepa_live_pumps.values())
            # Expiry dispatch never joins while runtime/runner locks are held.
            # It must still drain before the caller can close the Store.
            threads.extend(source.expiry_dispatch for source in self.apartment_viewing_sessions.values()
                           if isinstance(source.expiry_dispatch,threading.Thread) and source.expiry_dispatch.is_alive())
            threads.extend(r.thread for r in self.occupancy_readers.values()
                           if isinstance(getattr(r,"thread",None),threading.Thread) and r.thread.is_alive())
        if futures:
            wait(futures,timeout=max(0,deadline-time.monotonic()))
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=max(0,deadline-time.monotonic()))
        drained=all(f.done() for f in futures) and all(not t.is_alive() for t in threads)
        drained=drained and all(getattr(r,"process",None) is None or r.process.poll() is not None for r in self.recorders.values())
        return {"drained":drained,"state":"closed" if drained else "waiting_for_local_workers"}
