import asyncio
import threading
import unittest
from types import SimpleNamespace
from home_shared.refresh_mailbox import RefreshMailbox


class RefreshMailboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        owner = threading.get_ident()
        def check():
            if threading.get_ident() != owner:
                raise RuntimeError('wrong thread')
        def accept(namespace, request_id):
            check()
            self.calls.append((namespace, request_id))
            return {'request_id': request_id}
        self.box = RefreshMailbox(SimpleNamespace(_check_owner=check, accept_refresh=accept))

    async def test_accept_runs_only_when_owner_drains(self):
        task = asyncio.create_task(self.box.accept('owner', 'request'))
        await asyncio.sleep(0)
        self.assertEqual(self.calls, [])
        self.box.drain()
        self.assertEqual(await task, {'request_id': 'request'})
        self.assertEqual(self.calls, [('owner', 'request')])

    async def test_cancel_before_dequeue_never_dispatches(self):
        task = asyncio.create_task(self.box.accept('owner', 'request'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.box.drain()
        self.assertEqual(self.calls, [])

    async def test_close_rejects_pending_and_future_requests(self):
        task = asyncio.create_task(self.box.accept('owner', 'request'))
        await asyncio.sleep(0)
        self.box.close()
        with self.assertRaises(RuntimeError):
            await task
        with self.assertRaises(RuntimeError):
            await self.box.accept('owner', 'another')
        self.assertEqual(self.calls, [])
