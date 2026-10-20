"""Fresh causal JPEGs through the commissioned camera/Chrony acceptance path.

No second camera owner, NTP probe, persistent image or fallback timestamp. The
original schema-3 header is returned unchanged after full pilot validation.
"""
import inspect
from pathlib import Path
import re
import sys
import threading
import time
from host_home import JETSON_HOME

RUNTIME=Path(JETSON_HOME + '/victoria-camera-pilot/clock-runtime')
_CAPTURE_LOCK = threading.Lock()


class _PreviewUnavailable(ValueError):
    pass


def commissioned_runtime():
    if str(RUNTIME) not in sys.path:
        sys.path.insert(0,str(RUNTIME))
    from home_shared import pilot_snapshot
    from home_shared.chrony_clock import ChronyClockConfig
    from home_shared.chrony_publisher import ChronyPublisher
    for module in (pilot_snapshot,sys.modules[ChronyClockConfig.__module__],
                   sys.modules[ChronyPublisher.__module__]):
        if not Path(module.__file__).resolve().is_relative_to(RUNTIME):
            raise ValueError('Commissioned clock runtime required')
    return pilot_snapshot,ChronyClockConfig,ChronyPublisher


class _RecordingPublisher:
    """The commissioned publisher, unchanged; remembers the receipt it published
    so a capture hold can stay inside that receipt's validity."""
    def __init__(self, inner, receipts):
        self.inner, self.receipts = inner, receipts

    def publish_once(self):
        evidence = self.inner.publish_once()
        self.receipts['evidence'] = evidence
        return evidence

    def close(self):
        return self.inner.close()


class ChronyPreviewClient:
    last_phases_ms = None
    # Capture alignment (pump-grid-1). The recording pump starts a call
    # capture_lead_s before its 0.5 s slot and sets capture_at (time.monotonic)
    # to the slot: the variable preparation (camera health, clock publisher
    # open and publication) then runs ahead, and the JPEG request, the causal
    # challenge the camera answers with its next frame, is held until
    # capture_at. The hold is at most MAX_HOLD_S and only while the published
    # clock receipt stays valid for HOLD_MARGIN_S more; otherwise the request
    # goes at once, exactly as before. Acceptance and validation are unchanged.
    capture_lead_s = .25
    capture_at = None
    MAX_HOLD_S = .25
    HOLD_MARGIN_S = .5

    def fetch(self, pilot, path, token):
        """Fixed trusted transport seam; ordinary commissioned reads unchanged."""
        return pilot.fetch(path, token)

    def runtime(self):
        return commissioned_runtime()

    def _hold_s(self, evidence):
        target = self.capture_at
        if type(target) not in (int, float) or not isinstance(evidence, dict):
            return 0.0
        expires = evidence.get('valid_until_monotonic_ns')
        if type(expires) is not int:
            return 0.0
        now = time.monotonic()
        return max(0.0, min(target-now, self.MAX_HOLD_S, expires/1e9-self.HOLD_MARGIN_S-now))

    def __call__(self):
        # Lab and explicit chat share one short-lived clock publisher. Wait only
        # for a bounded local capture turn; never queue or retry model execution.
        if not _CAPTURE_LOCK.acquire(timeout=1):
            raise ValueError('Commissioned camera capture busy')
        try:
            started=time.monotonic()
            for attempt in range(3):
                try:
                    return self._capture()
                except _PreviewUnavailable:
                    if attempt == 2 or time.monotonic()-started >= 2:
                        raise
                    time.sleep(.1)
        finally:
            _CAPTURE_LOCK.release()

    def _capture(self):
        pilot,Policy,Publisher=self.runtime()
        config=pilot.load_config(pilot.CONFIG)
        clock=config.ntp
        policy=Policy(clock.server_ip,4096,clock.local_clock_quantization_ns,
            clock.drift_slew_ppm,clock.timeout_seconds,clock.valid_for_seconds,
            clock.max_uncertainty_seconds)
        captured={}
        receipts={}
        held=[]
        def transport(path,token):
            if path=='/frame.jpg':
                hold=self._hold_s(receipts.get('evidence'))
                if hold>0:
                    time.sleep(hold)
                    held.append(hold)
            try:
                headers,data=self.fetch(pilot,path,token)
            except ValueError as error:
                # Fixed local transport rejection only. Clock, metadata,
                # continuity and decode failures are not retried. The same
                # fixed credential and strict response checks apply each time.
                # Each retry re-runs full acceptance with a new clock receipt.
                if path == '/frame.jpg' and str(error) == 'camera response rejected':
                    raise _PreviewUnavailable('Fresh preview temporarily unavailable') from None
                raise
            if path=='/frame.jpg':
                captured['jpeg']=data
                captured['header']=headers['x-victoria-frame-metadata']
            return headers,data
        # Timing only: phase names are recorded, never frame or clock content.
        # An older commissioned runtime without the hook is called unchanged.
        marks=[]
        def on_phase(phase):
            if isinstance(phase,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,39}',phase) and len(marks)<16:
                marks.append((phase,time.monotonic()))
        try:
            hooked='on_phase' in inspect.signature(pilot.accept_once).parameters
        except (TypeError,ValueError):
            hooked=False
        try:
            result=pilot.accept_once(config,pilot.read_token(),transport=transport,
                publisher_factory=lambda value:_RecordingPublisher(Publisher(value,policy),receipts),
                **({'on_phase':on_phase} if hooked else {}))
        finally:
            ended=time.monotonic()
            self.last_phases_ms={phase:(end-start)*1000 for (phase,start),end in
                zip(marks,[m[1] for m in marks[1:]]+[ended])} if hooked else None
            if held and self.last_phases_ms is not None:
                # Part of jpeg_request: how long the challenge waited for its slot.
                self.last_phases_ms['capture_hold']=sum(held)*1000
        if result.get('status')!='accepted':
            raise ValueError('Commissioned JPEG acceptance failed')
        return captured['jpeg'],captured['header']
