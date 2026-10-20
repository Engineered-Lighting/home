"""Restore the always-on LA perceiver's Qwen facts to live JEV.

Since 2026-10-09 the always-on la-live-perceiver has been the only live Qwen
sender (LA allows one outstanding live request), so no recording runs its own
qwen_live pump and ServiceRuntime._qwen_context sent no Qwen record. This keeps
a bounded window of the perceiver's accepted FactLog receipts (closed facts
only, delivered by its in-memory observer; it never opens the private log or
calls LA) and builds the same shared qwen_scene record a per-recording pump
would, scoped to the requesting recording. The existing recipe grant and the
household policy's structured-source grant still decide whether it is sent.
"""
from collections import OrderedDict
import threading

import qwen_scene as scene

CAPACITY = 256


class LaQwenReceipts:
    def __init__(self, fact_log, *, capacity=CAPACITY):
        self.lock = threading.Lock()
        self.receipts = OrderedDict()
        self.capacity = capacity
        self.fact_log = fact_log
        self.closed = False
        fact_log.subscribe_receipts(self.observe)

    def observe(self, metadata, receipt):
        """FactLog observer: keep the first copy of each accepted receipt."""
        if not isinstance(receipt, dict) or not isinstance(receipt.get('request_id'), str):
            return
        with self.lock:
            if self.closed or receipt['request_id'] in self.receipts:
                return
            self.receipts[receipt['request_id']] = receipt
            while len(self.receipts) > self.capacity:
                self.receipts.popitem(last=False)

    def context(self, session, cutoff, *, generation, arms, horizon_seconds=120):
        """Per-arm shared records like qwen_live.scene_context, from the perceiver."""
        with self.lock:
            receipts = [r for r in self.receipts.values() if r.get('observed_at', 0) >= cutoff - horizon_seconds - 60]
        projected = scene.projection(receipts, cutoff, horizon_seconds=horizon_seconds)
        record = scene.scene_record(projected, receipts, cutoff, site_id=session['site_id'],
                                    camera_id=session['camera_id'], session_id=session['id'])
        return scene.shared_records(record, arms=arms, generation=generation)

    def close(self):
        with self.lock:
            self.closed = True
            self.receipts.clear()
        try:
            self.fact_log.unsubscribe_receipts(self.observe)
        except Exception:
            pass
