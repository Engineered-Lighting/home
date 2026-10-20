"""Local camera-owner client and bounded recording-owned preview polling."""
import http.client
import json
import re
import threading
import time


class LocalPreviewClient:
    def __init__(self, credential, *, connection_factory=http.client.HTTPConnection):
        self.credential,self.connection_factory=credential,connection_factory

    def __call__(self):
        return self._read("/frame.jpg")

    def _read(self,path):
        routes={"/frame.jpg":("image/jpeg",256*1024),
            "/frame.npz":("application/octet-stream",4*1024**2),"/calibration":("application/json",24576)}
        if path not in routes: raise ValueError("unregistered camera route")
        mime,maximum=routes[path]
        conn=None
        try:
            token=self.credential()
            if not isinstance(token,str) or not token or any(c in token for c in "\r\n"):
                raise ValueError()
            # Victoria's Lab controller and camera owner share the Jetson.
            # No caller-selected origin, redirect, proxy or secondary camera owner.
            conn=self.connection_factory("127.0.0.1",8090,timeout=3)
            deadline=time.monotonic()+3
            conn.request("GET",path,headers={"Authorization":"Bearer "+token,
                "Accept":mime,"Accept-Encoding":"identity"})
            response=conn.getresponse()
            meta=response.getheader("X-Victoria-Frame-Metadata","")
            if (response.status!=200 or response.getheader("Content-Type","").split(";")[0]!=mime
                    or response.getheader("Content-Encoding","identity")!="identity"
                    or (path=="/frame.jpg" and not 0<len(meta)<=16384)):
                raise ValueError()
            size=int(response.getheader("Content-Length","-1"))
            if not 0<size<=maximum:
                raise ValueError()
            data=bytearray()
            while len(data)<size:
                left=deadline-time.monotonic()
                if left<=0: raise TimeoutError()
                if conn.sock is not None: conn.sock.settimeout(left)
                chunk=response.read1(min(8192,size-len(data)))
                if not chunk or len(chunk)>size-len(data): raise ValueError()
                data.extend(chunk)
            return bytes(data),meta
        except Exception:
            raise ValueError("camera_preview_unavailable") from None
        finally:
            if conn is not None: conn.close()


class LocalBundleClient(LocalPreviewClient):
    def __call__(self):
        try:
            bundle,_=self._read("/frame.npz")
            raw,_=self._read("/calibration")
            calibration=json.loads(raw)
            if not isinstance(calibration,dict): raise ValueError()
            # BundleIngestor checks the epoch. A restart between the two reads
            # rejects this sample; no retry or calibration substitution occurs.
            return bundle,calibration
        except Exception:
            raise ValueError("camera_bundle_unavailable") from None


def _category(stage,error):
    # Content-free label: an explicit category or code, else a short fixed
    # message of this service's own ValueErrors, else only the type name.
    for value in (getattr(error,"category",None),getattr(error,"code",None)):
        if isinstance(value,str) and re.fullmatch(r"[a-z][a-z0-9_]{0,47}",value):
            return stage+":"+value
    text=str(error)
    if isinstance(error,ValueError) and re.fullmatch(r"[A-Za-z][A-Za-z ,;_-]{0,63}",text):
        return stage+":"+re.sub(r"[^a-z]+","_",text.lower()).strip("_")
    return stage+":"+type(error).__name__


class PreviewPump:
    """Recording-owned polling on a fixed grid (rule pump-grid-1).

    Slot k is due at first + k*interval on the monotonic clock, so wake-up,
    fetch and ingest latency never accumulate into drift (the old pump slept
    for what remained of the interval and restarted at once after an overrun,
    so every overrun shifted all later polls). One fetch at a time and at most
    one regular fetch per slot: a fetch that overruns its slot starts the next
    slot at once only while that is still in the slot's first half
    (LATE_START_FRACTION); otherwise that slot is skipped (counted), never
    queued, and the grid phase is kept. With retry_after, a failed fetch or
    ingest is retried once in its own slot, only while the retry still starts
    in the slot's first half: one failure need not cost a whole slot, and a
    pump that never fails makes exactly one request per slot.

    Capture alignment: a fetch that exposes capture_lead_s (at most half the
    interval) and a settable capture_at is started that much before its slot,
    with capture_at set to the slot, so its variable preparation (the causal
    client's health check and clock publication) runs ahead and the capture
    itself can be held to the slot. Any other fetch starts at the slot.
    """
    LATE_START_FRACTION = .5
    def __init__(self, fetch, consume, active, *, interval=.5, retry_after=None, clock=time.monotonic, idle_drain=None):
        if not .5<=interval<=10: raise ValueError("bounded preview cadence required")
        if retry_after is not None and (type(retry_after) not in (int,float) or not 0<retry_after<interval/2):
            raise ValueError("bounded retry delay required")
        if idle_drain is not None and not callable(idle_drain): raise ValueError("bounded idle drain required")
        self.idle_drain=idle_drain
        self.wake_event=threading.Event() if idle_drain is not None else None
        self.fetch,self.consume,self.active=fetch,consume,active
        self.interval=interval
        self.retry_after=retry_after
        self.clock=clock
        self.stopped=threading.Event()
        self.status="idle"
        self.counts_lock=threading.Lock()
        self.attempts,self.ok,self.failures,self.last_fetch_ms=0,0,{},None
        self.retries,self.skipped_slots=0,0
        self.thread=threading.Thread(target=self._run,name="lab-preview",daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.wake_event is not None:self.wake_event.set()

    def counters(self):
        with self.counts_lock:
            result={"attempts":self.attempts,"ok":self.ok,"failures":dict(self.failures),"last_fetch_ms":self.last_fetch_ms,
                    "retries":self.retries,"skipped_slots":self.skipped_slots}
        phases=getattr(self.fetch,"last_phases_ms",None)
        if isinstance(phases,dict):
            result["last_phases_ms"]=dict(phases)
        return result

    def _fail(self,category):
        with self.counts_lock:
            if category in self.failures or len(self.failures)<24:
                self.failures[category]=self.failures.get(category,0)+1
            else:
                self.failures["other"]=self.failures.get("other",0)+1

    def _wait_until(self, deadline):
        """True once stopped; optional drains run ONLY on this owned pump."""
        if self.wake_event is None:
            return self.stopped.wait(max(0.0,deadline-self.clock()))
        while not self.stopped.is_set():
            remaining=max(0.0,deadline-self.clock())
            if not self.wake_event.wait(remaining):return self.stopped.is_set()
            self.wake_event.clear()
            if self.stopped.is_set():return True
            if self.active():
                try:self.idle_drain()
                except Exception as error:self._fail(_category('idle',error))
        return True

    def _attempt(self):
        """One fetch and ingest: True ingested, False failed, None inactive."""
        stage="fetch"
        try:
            if not self.active(): return None
            self.status="fetching"
            started=self.clock()
            with self.counts_lock:self.attempts+=1
            try:
                data,header=self.fetch()
            finally:
                with self.counts_lock:self.last_fetch_ms=(self.clock()-started)*1000
            if self.stopped.is_set() or not self.active(): return None
            stage="ingest"
            self.consume(data,header)
            with self.counts_lock:self.ok+=1
            self.status="current"
            return True
        except Exception as error:
            self._fail(_category(stage,error))
            self.status="unavailable"
            return False

    def _run(self):
        # See the class docstring (pump-grid-1). Delayed polls are skipped,
        # never accumulated; an in-flight fetch is never interrupted.
        late=self.interval*self.LATE_START_FRACTION
        slot=self.clock()+self.interval
        while not self._wait_until(slot-self._lead()):
            # Optional idle work may consume a slot. Keep the same grid and
            # skip it, rather than introducing a late additional capture.
            if self.wake_event is not None and self.clock()>slot+late:
                slot+=self.interval
                with self.counts_lock:self.skipped_slots+=1
                now=self.clock()
                while now>slot+late:
                    slot+=self.interval
                    with self.counts_lock:self.skipped_slots+=1
                continue
            self._target(slot)
            outcome=self._attempt()
            if outcome is False and self.retry_after is not None:
                retry=self.clock()+self.retry_after
                if retry<=slot+late:
                    if self._wait_until(retry): break
                    if self.wake_event is None or self.clock()<=slot+late:
                        with self.counts_lock:self.retries+=1
                        self._target(slot)
                        self._attempt()
            slot+=self.interval
            now=self.clock()
            while now>slot+late:
                slot+=self.interval
                with self.counts_lock:self.skipped_slots+=1
        self.status="stopped"

    def _lead(self):
        """The fetch's own capture lead, when it can hold its capture to a slot."""
        lead=getattr(self.fetch,"capture_lead_s",None)
        if (type(lead) not in (int,float) or not 0<lead<=self.interval/2
                or not hasattr(self.fetch,"capture_at")):
            return 0.0
        return float(lead)

    def _target(self, slot):
        if self._lead()>0:
            try:
                self.fetch.capture_at=slot
            except Exception:
                pass
