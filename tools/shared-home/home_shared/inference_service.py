"""Single-use listener/pump ownership for an explicitly provisioned gateway.

No production credentials, authority providers, commissioning, or ledger closure.
Transport shutdown never establishes that independent engine execution ended.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from .dispatch_service import DispatchPump, _seconds
from .ingress import PrivateTLSIngress


@dataclass(frozen=True)
class ServiceDrain:
    listener_stopped: bool
    dispatcher_stopped: bool
    handlers_drained: bool

    @property
    def drained(self) -> bool:
        """Only local service ownership drained; not engine completion."""
        return self.listener_stopped and self.dispatcher_stopped and self.handlers_drained


class InferenceService:
    """Own a fresh TLS ingress and one pump, retaining both after stop/failure.

    Construct before starting either component. Start always leaves admission
    closed. The separate commissioning owner may enable the controller only
    after its existing receipt and outstanding-execution checks pass.
    """

    def __init__(self, ingress: PrivateTLSIngress, *, poll_seconds: float):
        poll = _seconds(poll_seconds, .01, 5)
        self.ingress = ingress
        self.gateway = ingress.gateway
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failed = False
        self._started = False
        with self.gateway._lock:
            if getattr(self.gateway, '_inference_service', None) is not None:
                raise RuntimeError('gateway already has a service owner')
            self.pump = DispatchPump(self.gateway, poll_seconds=poll)
            self.gateway._inference_service = self
            self.gateway.controller.close_admission('inference service not commissioned')
        # A bounded handle_request loop avoids HTTPServer.shutdown's unbounded
        # wait and its deadlock when serve_forever has never started.
        self.ingress.timeout = poll

    @property
    def failed(self) -> bool:
        return self._failed or self.pump.failed

    def start(self) -> None:
        with self._lock:
            if self._started or self._stop.is_set():
                raise RuntimeError('inference service cannot be restarted')
            self._started = True
            self.gateway.controller.close_admission('inference service not commissioned')
            self._thread = threading.Thread(target=self._serve,
                                            name='shared-home-ingress', daemon=True)
            try:
                self.pump.start()
                self._thread.start()
            except Exception:
                self._failed = True
                self._stop.set()
                self.ingress.server_close()
                self.pump.stop(timeout_seconds=0)
                raise RuntimeError('inference service failed to start') from None

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                if self.pump.failed:
                    raise RuntimeError('dispatch failed')
                self.ingress.handle_request()
        except Exception:
            if not self._stop.is_set():
                self._failed = True
        finally:
            self._stop.set()
            self.ingress.server_close()
            self.pump.stop(timeout_seconds=0)

    def stop(self, *, timeout_seconds: float) -> ServiceDrain:
        deadline = time.monotonic() + _seconds(timeout_seconds, 0, 60)
        with self._lock:
            self.gateway.controller.close_admission('inference service stopping')
            self._stop.set()
            self.ingress.server_close()
            worker = self._thread
        remaining = lambda: max(0., deadline - time.monotonic())
        dispatch_stopped = self.pump.stop(timeout_seconds=remaining())
        if worker is not None and worker.ident is not None and worker is not threading.current_thread():
            worker.join(remaining())
        listener_stopped = worker is None or not worker.is_alive()
        handlers_drained = self.ingress.wait_handlers(remaining())
        return ServiceDrain(listener_stopped, dispatch_stopped, handlers_drained)
