"""Explicit, single-lifetime dispatch pump for a provisioned Gateway.

This is not a commissioning path: it cannot enable admission, replace an
engine, close its ledger, or prove that an outstanding execution has ended.
"""

from __future__ import annotations

import math
import threading

from .gateway import Gateway


def _seconds(value: float, minimum: float, maximum: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise ValueError("lifecycle timeout outside supported finite bounds")
    return float(value)


class DispatchPump:
    """One explicit start, one owner per gateway, and bounded stop joins.

    Registration survives stop and failure. A timed-out stop must be joined
    again; it is never permission to construct a replacement pump or release
    ledger resources. Even a successful stop says only that this pump ended,
    not that the gateway's independent upstream workers ended.
    """

    def __init__(self, gateway: Gateway, *, poll_seconds: float):
        self.gateway = gateway
        self.poll_seconds = _seconds(poll_seconds, 0.01, 5.0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failed = False
        with gateway._lock:
            if getattr(gateway, "_dispatch_pump", None) is not None:
                raise RuntimeError("gateway already has a dispatch pump owner")
            gateway._dispatch_pump = self

    @property
    def failed(self) -> bool:
        return self._failed

    def start(self) -> None:
        with self.gateway._lock:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("dispatch pump cannot be restarted")
            self._thread = threading.Thread(
                target=self._run, name="shared-home-dispatch", daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._failed = True
                self._stop.set()
                self.gateway.controller.close_admission("dispatch pump failed")
                raise RuntimeError("dispatch pump failed to start") from None

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                # Late affirmative evidence remains useful with admission closed.
                self.gateway.reconcile_late_completions()
                if not self._stop.is_set() and self.gateway.readiness()["ready"]:
                    self.gateway.dispatch_once()
                # Bound idle/closed/error-free polling without creating a queue
                # or using an uninterruptible sleep.
                self._stop.wait(self.poll_seconds)
        except Exception:
            self._failed = True
            self.gateway.controller.close_admission("dispatch pump failed")
            self._stop.set()

    def stop(self, *, timeout_seconds: float) -> bool:
        timeout = _seconds(timeout_seconds, 0.0, 60.0)
        with self.gateway._lock:
            self.gateway.controller.close_admission("dispatch pump stopping")
            self._stop.set()
            worker = self._thread
        if worker is None:
            return True
        if worker is threading.current_thread():
            return False
        # Thread.start may fail, leaving no joinable thread.
        if worker.ident is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()
