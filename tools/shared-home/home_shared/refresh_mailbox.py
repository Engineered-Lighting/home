"""Bounded async ingress to the existing runtime owner thread.

Cancellation before dequeue prevents work. Cancellation after durable acceptance
does not undo it; callers must look up the same request ID, never resubmit a new ID.
"""
import asyncio
from concurrent.futures import Future
import queue
import threading


class RefreshMailbox:
    def __init__(self, runtime):
        self.runtime = runtime
        self._queue = queue.Queue(maxsize=2)
        self._lock = threading.Lock()
        self._closed = False

    async def _call(self, method, namespace, request_id):
        future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError('refresh unavailable')
            self._queue.put_nowait((future, method, namespace, request_id))
        return await asyncio.wrap_future(future)

    async def accept(self, namespace, request_id):
        return await self._call('accept', namespace, request_id)

    async def status(self, namespace, request_id):
        return await self._call('status', namespace, request_id)

    def drain(self):
        self.runtime._check_owner()
        for _ in range(2):
            try:
                future, method, namespace, request_id = self._queue.get_nowait()
            except queue.Empty:
                break
            if not future.set_running_or_notify_cancel():
                continue
            try:
                if method == 'accept':
                    result = self.runtime.accept_refresh(namespace, request_id)
                else:
                    self.runtime.journal.expire_refreshes(now=self.runtime.clock())
                    result = self.runtime.journal.refresh_status(namespace, request_id)
                future.set_result(result)
            except Exception as error:
                future.set_exception(error)

    def close(self):
        self.runtime._check_owner()
        with self._lock:
            self._closed = True
            while True:
                try:
                    future, *_ = self._queue.get_nowait()
                except queue.Empty:
                    break
                if future.set_running_or_notify_cancel():
                    future.set_exception(RuntimeError('refresh unavailable'))
