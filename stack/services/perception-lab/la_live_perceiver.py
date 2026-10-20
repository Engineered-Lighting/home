"""Always-on LA live perceiver (Qwen) inside the Lab controller (speed plan S1.3).

Plan S1.3 with section 13 #1 and #14 and section 15 #11: the den gets LA Qwen
facts whenever it is occupied, not only inside 60-300 s Lab recordings. This
is a component of the existing Lab controller process, not a separate systemd
unit, so the controller stays the single owner of the camera's /frame.jpg
captures (frame_ring). It composes qwen_live.QwenLivePump through its
injection points:
- frames: a frame_ring.FrameRing (RAM only, about 10 s), filled through the
  commissioned client's acceptance path and its process-local lock, one
  attempt per capture and an idle gap of one interval after each (under 2 Hz),
  yielding to camera chat (frame_ring.RingCapture, RingPreviewClient). Frames
  are taken only while the gate is open, the switch is on, the fact log can
  record answers and the pump could send them soon (PerceiverPump.wants_frames:
  not while paused or awaiting an unproven outcome, not before LA's engine
  epoch is known, not while a send backoff or the rate cap has more than one
  window left). Each capture also runs the commissioned clock publication
  (subprocesses and an fsync of the host clock receipt), so idle captures are
  not free;
- occupied: the controller's own occupancy_reader.OccupancyReader (plan S1.2),
  whose on-edge wakes the pump and the ring capture at once. Its entity must
  be the HA source's camera-derived person-occupancy entity
  (occupancy_gate.reader_entity, checked at build);
- enabled: the household policy lists la-live-inference-1 (ENTRY), checked per
  tick (cached for at most a second), and the live config's
  'la_live_perceiver' block is enabled (else nothing is built). This is a
  dedicated kill switch for LA inference, not the JEV egress grant
  lab-live-scene-1 (red-team #14). No Lab session and no per-session
  reasoning_enabled opt-in are involved (section 15 #11);
- session: a synthetic id per UTC day (day_session), so request ids are
  digests of that day's id, the frames and the body; it moves to the next day
  only between requests;
- ledger: la_fact_log.FactLog (la-fact-log-1, words only, 14 days). Its
  directory must be writable at start (check_writable); while it cannot record
  answers (failed writes, a full day) the pump sends nothing
  (fact_log_unwritable) and the ring takes no frames. The first record_failed
  of each UTC day logs one line.

Failure semantics, for this component only: when LA reports the outstanding
request indeterminate, the pump pauses (never resubmits) and looks the request
up by its identity once every indeterminate_reprobe_seconds (default 60 s),
whatever each lookup returns; the recording pump halts instead, as before. An
unknown_resolved outcome from LA's reconcile (plan S1.8), or any other
terminal proof, ends the pause and the pump resumes; until LA can reconcile,
the pause lasts until a manual reconcile. Pause and resume each log one line,
and the ring takes no frames while paused. When a send finds LA unreachable
(transport_not_connected), the pump learns LA's engine epoch again with the
identity-only probe before it reads frames, so an LA outage costs one probe
per tick and no captures.

Mutual exclusion: LA allows one outstanding live request, so this perceiver
and the recording pump ('qwen_live') would refuse each other. start() refuses
while the live config's qwen_live block is enabled, and build_runtime refuses
the recording pump while a perceiver runs; each logs one WARNING line.

Logging: logger 'perception_lab.la_live' at WARNING (the controller configures
no logging, so only WARNING reaches its journal): start, refusal, stop, pause,
resume and kill-switch transitions only.
"""
import logging
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re
import threading
import time

import frame_ring
import la_fact_log
import occupancy_gate
import qwen_live
import qwen_scene as scene
from structured_camera_context import StructuredCameraContext

_log = logging.getLogger("perception_lab.la_live")

# The household kill switch: an entry of the household policy's
# structured_sources list, read per tick like the JEV grants.
ENTRY = "la-live-inference-1"
# The private live config's 'la_live_perceiver' block.
REQUIRED = frozenset({"enabled", "transport_file", "artifact_sha256", "runtime_revision", "fact_log_directory"})
PUMP_KEYS = frozenset({"interval_seconds", "frames", "span_seconds", "max_per_minute", "timezone"})
FACT_LOG_KEYS = frozenset(la_fact_log.DEFAULTS)
OWN_KEYS = frozenset({"indeterminate_reprobe_seconds", "capture_interval_seconds"})
OPTIONAL = PUMP_KEYS | FACT_LOG_KEYS | OWN_KEYS
DEFAULTS = {"indeterminate_reprobe_seconds": 60.0, "capture_interval_seconds": frame_ring.CAPTURE_INTERVAL}
BOUNDS = {"indeterminate_reprobe_seconds": (30, 600), "capture_interval_seconds": frame_ring.CAPTURE_BOUNDS}
GRANT_CACHE_SECONDS = 1.0
# Tick outcomes that show LA unreachable: the engine epoch is probed again
# (identity only, no frames) before the next request.
UNREACHABLE = frozenset({"transport_not_connected", "engine_epoch_unavailable"})
UNWRITABLE = "fact_log_unwritable"


def _finite(value):
    return scene.finite(value)


def check_config(value):
    """Exact 'la_live_perceiver' block (no I/O); returns its settings:
    {'pump', 'fact_log', 'indeterminate_reprobe_seconds', 'capture_interval_seconds'}."""
    if not isinstance(value, dict) or set(value) - OPTIONAL != REQUIRED:
        raise ValueError("Exact LA live perceiver provisioning required")
    if type(value["enabled"]) is not bool:
        raise ValueError("Explicit LA live perceiver switch required")
    for key in ("transport_file", "fact_log_directory"):
        if not isinstance(value[key], str) or not PurePosixPath(value[key]).is_absolute():
            raise ValueError("Absolute LA live perceiver paths required")
    if not isinstance(value["artifact_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["artifact_sha256"]):
        raise ValueError("Pinned Qwen artifact digest required")
    if (not isinstance(value["runtime_revision"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}", value["runtime_revision"])):
        raise ValueError("Pinned Qwen runtime revision required")
    from ambient_context import ZONE
    if "timezone" in value and (not isinstance(value["timezone"], str) or not ZONE.fullmatch(value["timezone"])):
        raise ValueError("IANA time zone required")
    settings = {"pump": qwen_live.pump_config({k: value[k] for k in PUMP_KEYS if k in value}),
                "fact_log": la_fact_log.settings({k: value[k] for k in FACT_LOG_KEYS if k in value})}
    for key, default in DEFAULTS.items():
        number = value.get(key, default)
        low, high = BOUNDS[key]
        if not _finite(number) or not low <= number <= high:
            raise ValueError(f"LA live perceiver {key} outside its bound")
        settings[key] = number
    return settings


def day_session(site_id, camera_id, now):
    """The synthetic session id of now's UTC day (32 hex, as LA requires)."""
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    return scene.digest({"la_live_perceiver_session": [site_id, camera_id, day]})[:32]


def household_granted(read_policy, entry=ENTRY):
    """Per tick: the household policy lists entry in structured_sources
    (managed_jev.qwen_granted's rule for another entry). Fail closed."""
    def granted():
        try:
            from jev_policy import structured_sources
            policy = read_policy()
            return isinstance(policy, dict) and entry in structured_sources(policy)
        except Exception:
            return False
    return granted


class PerceiverPump(qwen_live.QwenLivePump):
    """QwenLivePump with the synthetic day session, the fact log's upkeep
    after every tick, nothing sent while the fact log cannot record answers, a
    fresh engine-epoch probe after LA was unreachable, wants_frames() for the
    ring, and one log line per pause, resume, switch change and first
    record_failed of a UTC day."""

    def __init__(self, *, site_id, camera_id, fact_log, clock=time.time, poke=None, **kwargs):
        session = {"id": day_session(site_id, camera_id, clock()), "site_id": site_id, "camera_id": camera_id}
        super().__init__(None, session, ledger=fact_log, clock=clock, **kwargs)
        self.fact_log = fact_log
        self.thread.name = "la-live-perceiver"
        self.logged_halt, self.logged_disabled = None, None
        self.poke, self.wanted = poke, False
        self.record_failures, self.record_failed_day = 0, None

    def tick(self):
        if self.outstanding is None:
            # Between requests only: an outstanding request keeps its day.
            self.session["id"] = day_session(self.session["site_id"], self.session["camera_id"], self.clock())
        try:
            if not self.stopped.is_set() and not self.fact_log.writable():
                # No LA inference whose answer could not be recorded.
                result = self._note(UNWRITABLE, "skipping")
            else:
                result = super().tick()
        finally:
            self.fact_log.maintain()
        if isinstance(result, dict) and result.get("reason") in UNREACHABLE:
            # LA did not answer: learn its engine epoch again (identity-only
            # probe) before frames are read or captured for another request.
            self.profile = None
        self._transitions(result)
        wanted = self.wants_frames()
        if wanted and not self.wanted and callable(self.poke):
            self.poke()
        self.wanted = wanted
        return result

    def wants_frames(self):
        """Whether the ring should take frames now: only while a send could
        use them soon. Not while paused on an indeterminate row or awaiting an
        unproven outcome, not before LA's engine epoch is known (at start and
        after LA was unreachable the identity-only probe comes first), and
        not while a send backoff or the rate cap has more than one window
        (span_seconds plus one frame step) left. Read from the ring's thread;
        never raises."""
        try:
            if (self.stopped.is_set() or self.halted or self.outstanding is not None or self.profile is None
                    or self.local_time is None):
                return False
            span = self.config["span_seconds"]
            lead = span + span / (self.config["frames"] - 1)
            mono = self.monotonic()
            if self.backoff_until is not None and self.backoff_until - mono > lead:
                return False
            sent = list(self.sent)
            if len(sent) >= self.config["max_per_minute"] and sent[0] + 60 - mono > lead:
                return False
            return True
        except Exception:
            return True  # only decides whether frames are taken; the tick still checks everything

    def _transitions(self, result):
        if self.halted != self.logged_halt:
            if self.halted:
                _log.warning("la-live-perceiver paused: LA reports the outstanding request indeterminate; "
                             "identity lookup every %ds, never resubmitted", self.indeterminate_seconds)
            else:
                _log.warning("la-live-perceiver resumed: LA ended the indeterminate request (%s)", self.last_reason)
            self.logged_halt = self.halted
        disabled = isinstance(result, dict) and result.get("reason") == "disabled"
        if isinstance(result, dict) and result.get("reason") != "stopped" and disabled != self.logged_disabled:
            if disabled:
                _log.warning("la-live-perceiver: household kill switch %s is off; nothing is captured or sent", ENTRY)
            elif self.logged_disabled:
                _log.warning("la-live-perceiver: household kill switch %s is on", ENTRY)
            self.logged_disabled = disabled
        failures = self.counts.get("record_failed", 0)
        if failures > self.record_failures:
            self.record_failures = failures
            day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
            if day != self.record_failed_day:
                self.record_failed_day = day
                _log.warning("la-live-perceiver: an LA outcome could not be written to la-fact-log-1 (%s); after %d "
                             "failed writes in a row nothing is captured or sent for %ds; further failures today "
                             "are counted only", self.fact_log.snapshot().get("last_failure") or "record_failed",
                             la_fact_log.MAX_WRITE_FAILURES, la_fact_log.RETRY_SECONDS)


class LaLivePerceiver:
    """The controller-lifetime component: occupancy reader, ring capture,
    pump and fact log, started and stopped together."""

    def __init__(self, *, transport, profile, reader, fact_log, granted, fetch, settings, site_id, camera_id,
                 lab_qwen_enabled=False, clock=time.time, monotonic=time.monotonic, local_time=None, ring=None,
                 turns=None):
        self.settings = settings
        self.granted, self.lab_qwen_enabled = granted, lab_qwen_enabled
        self.monotonic = monotonic
        self.lock = threading.Lock()
        self.state = "idle"
        self.grant = (None, False)
        self.reader, self.fact_log = reader, fact_log
        self.ring = ring or frame_ring.FrameRing(clock=clock)
        self.pump = None
        self.capture = frame_ring.RingCapture(self.ring, fetch, reader, enabled=self._capture_wanted, turns=turns,
                                              interval=settings["capture_interval_seconds"], monotonic=monotonic)
        # Local acquisition shares controller arbitration, independently of
        # whether LA can currently run or record inference.
        self.capture_scope = frame_ring.register_controller_capture_scope(self.capture)
        self.pump = PerceiverPump(transport=transport, profile=profile, frames=self.ring, fact_log=fact_log,
                                  config=settings["pump"], clock=clock, monotonic=monotonic, local_time=local_time,
                                  enabled=self.enabled, active=self.running, occupied=reader,
                                  indeterminate_seconds=settings["indeterminate_reprobe_seconds"],
                                  site_id=site_id, camera_id=camera_id, poke=self.capture.poke)
        self.context_readback = StructuredCameraContext(site_id=site_id, camera_id=camera_id, clock=clock)
        self.fact_log.subscribe_receipts(self.context_readback.observe)

    def start(self):
        """Start once; refuses (one log line) while the recording pump's
        qwen_live block is enabled. True when running."""
        with self.lock:
            if self.state != "idle":
                return self.state == "running"
            if self.lab_qwen_enabled:
                self.state = "refused"
                refused = True
            else:
                self.state, refused = "running", False
        if refused:
            _log.warning("la-live-perceiver refused: the Lab qwen_live block is enabled and LA allows one "
                         "outstanding live request; turn qwen_live off and restart the controller to run it")
            return False
        self.reader.start()
        self.capture.start()
        self.pump.start()
        _log.warning("la-live-perceiver started: interval=%.1fs capture=%.1fs indeterminate_reprobe=%ds",
                     self.pump.config["interval_seconds"], self.capture.interval, self.pump.indeterminate_seconds)
        return True

    def running(self):
        with self.lock:
            return self.state == "running"

    def stop(self, timeout=5):
        with self.lock:
            was, self.state = self.state, "stopped"
        self.fact_log.unsubscribe_receipts(self.context_readback.observe)
        self.context_readback.close()
        if self.capture_scope is not None:
            self.capture_scope.close()
        self.pump.stop(timeout)
        self.capture.stop(timeout)
        self.reader.stop(timeout)
        self.fact_log.close()
        self.ring.clear()
        if was == "running":
            _log.warning("la-live-perceiver stopped")

    def _capture_wanted(self):
        """Frames are taken only while the switch is on, the pump could send
        them soon (PerceiverPump.wants_frames) and the fact log can record
        the answers."""
        return (self.enabled() and self.pump is not None and self.pump.wants_frames()
                and self.fact_log.writable())

    def enabled(self):
        """The household kill switch, read at most once a second."""
        now = self.monotonic()
        with self.lock:
            checked, value = self.grant
            if checked is not None and 0 <= now - checked < GRANT_CACHE_SECONDS:
                return value
        try:
            value = self.granted() is True
        except Exception:
            value = False
        with self.lock:
            self.grant = (now, value)
        return value

    def offer_preview(self, data, header):
        """A recording's accepted preview capture, offered to the ring while
        the gate is open (that recording owns every capture slot meanwhile).
        Never raises into the recording."""
        try:
            if not self.capture.is_open():
                return False
            return self.ring.offer(data, header)
        except Exception:
            return False

    def snapshot(self):
        with self.lock:
            state = self.state
        return {"state": state, "pump": self.pump.snapshot(), "capture": self.capture.snapshot(),
                "ring": self.ring.snapshot(), "fact_log": self.fact_log.snapshot(),
                "occupancy": self.reader.snapshot() if callable(getattr(self.reader, "snapshot", None)) else None}

    def structured_context(self):
        """Read the original daily-scoped facts; never capture or call LA."""
        with self.lock:
            state = self.state
        pump = self.pump.snapshot()
        return self.context_readback.snapshot(dict(state=state, pump_status=pump['status'],
            last_reason=pump['last_reason'], outstanding=pump['outstanding'], halted=pump['halted'] is not None))


def camera_derived_entities(config, read):
    """The HA source file's camera-derived entities (that file is validated at
    controller start, before the perceiver is built)."""
    path = config.get("ha_source_file")
    if not isinstance(path, str) or not path:
        raise ValueError("The LA live perceiver requires the HA source file (its camera-derived occupancy entity)")
    value = read(path)
    derived = value.get("camera_derived_entities") if isinstance(value, dict) else None
    return tuple(derived) if isinstance(derived, list) else ()


def build(config, *, site_id, camera_id, policy_file, ha_bindings=None, transport_factory=None, fetch=None,
          private=True, clock=time.time):
    """The controller's perceiver from a validated live config, or None when
    the block is absent or disabled. Reads the private transport file, checks
    that the occupancy_reader entity is the HA source's camera-derived
    occupancy entity (with the JEV ha_bindings) and that the fact log
    directory is writable (opening today's file); opens no connection and
    starts nothing."""
    block = config.get("la_live_perceiver")
    if block is None or block.get("enabled") is not True:
        return None
    settings = check_config(block)
    if config.get("occupancy_reader") is None:
        raise ValueError("The LA live perceiver requires the occupancy_reader block")
    if not isinstance(policy_file, str) or not PurePosixPath(policy_file).is_absolute():
        raise ValueError("The LA live perceiver requires the household policy file")
    from live_detector_app import private_json
    from occupancy_reader import from_config, settings as reader_settings
    from qwen_transport import load_config
    entity = reader_settings(config["occupancy_reader"])["entity"]
    if occupancy_gate.reader_entity(camera_derived_entities(config, private_json), entity, None, ha_bindings) is None:
        raise ValueError("The LA live perceiver's occupancy_reader entity must be the HA source's camera-derived "
                         "person-occupancy entity")
    transport_config = load_config(private_json(block["transport_file"]), check_files=transport_factory is None)
    if not 5 <= transport_config["deadline_seconds"] <= qwen_live.MAX_DEADLINE_SECONDS:
        raise ValueError("LA live perceiver deadline must be 5-8 seconds")
    fact_log = la_fact_log.FactLog(block["fact_log_directory"], **settings["fact_log"], clock=clock, private=private)
    fact_log.check_writable()
    profile = {"model_version": scene.MODEL, "served_model": scene.SERVED_MODEL, "artifact_sha256": block["artifact_sha256"],
               "engine_epoch": "unprobed", "runtime_revision": block["runtime_revision"],
               "adapter_revision": scene.PROMPT_VERSION}
    if fetch is None:
        # One attempt per ring capture: camera chat never waits behind the
        # commissioned client's in-lock retries (frame_ring docstring).
        fetch = frame_ring.RingPreviewClient()
    return LaLivePerceiver(transport=(transport_factory or qwen_live.LiveSceneTransport)(transport_config),
                           profile=profile, reader=from_config(config["occupancy_reader"], clock=clock),
                           fact_log=fact_log, granted=household_granted(lambda: private_json(policy_file)),
                           fetch=fetch, settings=settings, site_id=site_id, camera_id=camera_id,
                           lab_qwen_enabled=(config.get("qwen_live") or {}).get("enabled") is True, clock=clock)


def attach(runtime, perceiver):
    """Start the perceiver with the controller's runtime, and stop it first
    whenever that runtime closes (shutdown or a failed composition)."""
    close = runtime.close

    def closing(*args, **kwargs):
        perceiver.stop(timeout=5)
        return close(*args, **kwargs)
    runtime.close = closing
    perceiver.start()
    return runtime
