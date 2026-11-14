"""A den recording can no longer be left indeterminate for good.

Two ways it was (2026-11-11 and 11-12): a cleanup gave up after its fixed stop
wait while LA's preflight still held its lease, and nothing retried it; and a
Lab restart dropped the in-memory worker item, so nothing ever settled the row.
Either way the Store refused every later den recording ('conflict').

A real Store ledger and Controller; the supervisors are fakes whose durable
receipts outlive a Lab process. No worker, camera or network."""
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from config import Settings
from controller import Controller
from managed_pair import ABSENT_GRACE_SECONDS, CLEANUP_RETRY_SECONDS, RECONCILED_REASON, ManagedPair
from store import LabError, Store
from worker_lifecycle_client import RequestNotFound


class Supervisor:
    """One lifecycle supervisor: durable leases by request id (a Jetson-local one)."""
    def __init__(self, clock):
        self.clock = clock
        self.leases = {}
        self.calls = []
        self.start_state = 'ready'
        self.stop_to = 'stopped'      # where a stop request takes a live lease at once
        self.unreadable = False

    def start(self, request_id, session_id, variant='detector'):
        self.calls.append(('start', request_id))
        self.leases.setdefault(request_id, {'session_id': session_id, 'state': self.start_state, 'reason': None})
        return self.status(request_id)

    def status(self, request_id):
        self.calls.append(('status', request_id))
        if self.unreadable:
            raise TimeoutError()
        if request_id not in self.leases:
            raise RequestNotFound('request_not_found')
        lease = self.leases[request_id]
        return {'version': 1, 'request_id': request_id, 'session_id': lease['session_id'], 'state': lease['state'],
                'reason': lease['reason'], 'engine_epoch': 'epoch-' + request_id[:8], 'expires_at': self.clock() + 1000,
                'profile': {}}

    def stop(self, request_id, epoch):
        self.calls.append(('stop', request_id))
        lease = self.leases[request_id]
        if lease['state'] not in ('stopped', 'failed'):
            lease['state'] = self.stop_to
        return self.status(request_id)

    def requests(self, kind):
        return [rid for call, rid in self.calls if call == kind]


class RemoteSupervisor(Supervisor):
    """The LA supervisor's client: it must be bound to a request before reading it."""
    def __init__(self, clock):
        super().__init__(clock)
        self.adopted = []

    def adopt(self, request_id, session_id, variant='detector'):
        self.adopted.append((request_id, session_id, variant))


class Inner:
    def start_session(self, session):
        return {'state': 'recording', 'inference': 'ready'}

    def stop_session(self, session):
        return {'state': 'stopped'}

    def close(self, timeout=5):
        return {'drained': True}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = [1800000000.]
        self.clients = {'a': Supervisor(self.clock), 'b': RemoteSupervisor(self.clock)}
        self.store = self.runtime = None
        self.body = dict(camera_id='den', experiment_id='placement-same', name='den', request_id='den-1')

    def tearDown(self):
        if self.runtime is not None:
            for client in self.clients.values():
                client.unreadable, client.stop_to = False, 'stopped'
            self.runtime.close()
        if self.store is not None:
            self.store.close()

    def clock(self):
        return self.now[0]

    def open(self):
        """One Lab process: the ledger plus a fresh ManagedPair (no worker items)."""
        self.store = Store(Settings(self.root, max_session_seconds=60, cameras=({'id': 'den'},)), clock=self.clock)
        self.runtime = ManagedPair(self.store, self.clients, lambda *_, **__: Inner(),
                                   [{'id': 'placement-same'}, {'id': 'apartment-viewing'}],
                                   clock=self.clock, prepare_seconds=5, stop_wait_seconds=.1)
        self.controller = Controller(self.store, self.runtime)

    def settle(self, sid, state):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if self.store.get_session(sid)['state'] == state:
                return self.store.get_session(sid)
            time.sleep(.01)
        self.fail(self.store.get_session(sid)['state'])

    def idle(self):
        """Wait for the item's own cleanup thread, if any."""
        until = time.monotonic() + 5
        while time.monotonic() < until:
            with self.runtime.lock:
                item = self.runtime.current
                if item is None or not item['thread'].is_alive():
                    return item
            time.sleep(.01)
        self.fail('cleanup still running')

    def events(self, sid, kind):
        return [e['payload'] for e in self.store.list_events(sid) if e['kind'] == kind]

    def assert_admission_refused(self):
        with self.assertRaises(LabError) as refused:
            self.store.create_session('den', 'placement-same', 'den', 'probe-' + uuid.uuid4().hex[:8])
        self.assertEqual(refused.exception.code, 'conflict')

    def start_and_stop_another(self):
        for client in self.clients.values():
            client.start_state, client.stop_to = 'ready', 'stopped'
        session = self.controller.start({**self.body, 'request_id': 'den-next-' + uuid.uuid4().hex[:8]})
        self.settle(session['id'], 'recording')
        self.controller.stop(session['id'])
        self.settle(session['id'], 'stopped')


class SlowArmCleanupTests(Base):
    """Cleanup retries until both receipts are terminal; it never strands the row."""

    def preparation_fails_while_la_is_still_in_preflight(self):
        # Arm A's worker exits on its own; LA's lease is still 'starting' behind
        # its kernel-log scan, so a stop request leaves it 'stopping'.
        self.clients['a'].start_state = 'stopped'
        self.clients['b'].start_state, self.clients['b'].stop_to = 'starting', 'stopping'
        session = self.controller.start(self.body)
        return self.settle(session['id'], 'indeterminate')

    def test_a_slow_arm_settles_the_row_once_its_stop_completes(self):
        self.open()
        session = self.preparation_fails_while_la_is_still_in_preflight()
        item = self.idle()
        rid = item['request_id']
        self.assertEqual(item['phase'], 'indeterminate')
        self.assertEqual(item['retry_at'], self.now[0] + CLEANUP_RETRY_SECONDS)
        self.assert_admission_refused()
        self.assertIn({'id': 'workers', 'state': 'blocked', 'reason': 'worker_cleanup_pending'},
                      self.controller.capabilities()['gates'])
        # A row this process still owns is left to its own cleanup.
        self.assertEqual(self.runtime.reconcile_orphans(), [])

        # Before the retry is due, reap does not touch the workers.
        reads = len(self.clients['b'].calls)
        self.now[0] += CLEANUP_RETRY_SECONDS - 1
        self.controller.expire()
        self.runtime.orphan_thread.join(5)
        self.assertEqual(len(self.clients['b'].calls), reads)

        # Due, but LA is still stopping: the retry backs off (doubling).
        self.now[0] += 1
        self.controller.expire()
        item = self.idle()
        self.assertGreater(len(self.clients['b'].calls), reads)
        self.assertEqual(self.store.get_session(session['id'])['state'], 'indeterminate')
        self.assertEqual(item['retry_at'], self.now[0] + 2 * CLEANUP_RETRY_SECONDS)

        # LA's preflight ends (it rejected the lease); the next retry proves both
        # receipts terminal and the row stops with the preparation's reason.
        self.clients['b'].leases[rid].update(state='failed', reason='preflight_rejected')
        self.now[0] += 2 * CLEANUP_RETRY_SECONDS
        self.controller.expire()
        stopped = self.settle(session['id'], 'stopped')
        self.assertEqual(stopped['reason'], 'worker_preparation_failed')
        self.assertIsNone(self.idle())
        cleanup = self.events(session['id'], 'workers.cleanup')
        self.assertEqual([c['state'] for c in cleanup], ['indeterminate', 'indeterminate', 'stopped'])
        self.assertEqual(cleanup[-1]['workers'], {'a': {'state': 'stopped', 'reason': None},
                                                  'b': {'state': 'failed', 'reason': 'preflight_rejected'}})
        self.assertTrue(self.controller.capabilities()['recording_allowed'])
        self.start_and_stop_another()

    def test_an_unreadable_supervisor_keeps_the_row_until_it_answers(self):
        self.open()
        session = self.controller.start(self.body)
        self.settle(session['id'], 'recording')
        self.clients['b'].unreadable = True
        self.controller.stop(session['id'])
        self.settle(session['id'], 'indeterminate')
        self.idle()
        self.now[0] += CLEANUP_RETRY_SECONDS
        self.controller.expire()
        self.idle()
        self.assertEqual(self.store.get_session(session['id'])['state'], 'indeterminate')
        self.assert_admission_refused()
        self.clients['b'].unreadable = False
        self.now[0] += 2 * CLEANUP_RETRY_SECONDS
        self.controller.expire()
        self.settle(session['id'], 'stopped')
        self.assertEqual(self.events(session['id'], 'workers.cleanup')[-1]['workers']['b'], {'state': 'stopped', 'reason': None})
        self.start_and_stop_another()

    def test_a_start_that_never_reached_the_supervisor_is_released_after_the_grace(self):
        self.open()

        def lost(request_id, session_id, variant='detector'):
            raise TimeoutError()  # the start may or may not have arrived
        self.clients['b'].start = lost
        session = self.controller.start(self.body)
        self.settle(session['id'], 'indeterminate')
        item = self.idle()
        self.assertEqual(item['receipts']['b'], {'state': 'unknown', 'reason': 'request_not_found'})
        self.now[0] += ABSENT_GRACE_SECONDS
        self.controller.expire()
        self.assertEqual(self.settle(session['id'], 'stopped')['reason'], 'worker_preparation_uncertain')
        self.assertEqual(self.events(session['id'], 'workers.cleanup')[-1]['workers']['b'],
                         {'state': 'absent', 'reason': 'request_not_found'})


class RestartReconcileTests(Base):
    """After a restart, a row is settled only from its supervisors' own receipts."""

    def crashed(self, *, arms=('a', 'b'), experiment='placement-same', preparing=True, leases=None):
        """A previous Lab process recorded a den recording, then died mid-run."""
        self.open()
        session, _ = self.store.create_session('den', experiment, 'den', 'activity-loop-' + uuid.uuid4().hex)
        sid, rid = session['id'], str(uuid.uuid4())
        self.store.transition(sid, 'starting')
        if preparing:
            self.store.append_event(sid, 'workers.preparing', {'request_id': rid, 'execution_arms': list(arms),
                                                               'comparison': len(arms) == 2})
        for arm, (state, reason) in (leases or {}).items():
            self.clients[arm].leases[rid] = {'session_id': sid, 'state': state, 'reason': reason}
        self.store.transition(sid, 'recording')
        # The process is gone: its worker item with it. The ledger survives.
        self.runtime.closed = True
        self.store.close()
        self.open()
        session = self.store.get_session(sid)
        self.assertEqual((session['state'], session['reason']), ('indeterminate', 'controller_restart_execution_unknown'))
        self.assert_admission_refused()
        return sid, rid

    def test_startup_pass_stops_the_orphaned_lease_and_settles_the_row(self):
        sid, rid = self.crashed(leases={'a': ('stopped', None), 'b': ('ready', None)})
        before = {e['id'] for e in self.store.list_events(sid)}
        # The first reap after startup runs the pass.
        self.controller.expire()
        self.runtime.orphan_thread.join(5)
        session = self.store.get_session(sid)
        self.assertEqual((session['state'], session['reason']), ('stopped', RECONCILED_REASON))
        self.assertEqual(self.clients['b'].adopted, [(rid, sid, 'detector')])
        self.assertEqual(self.clients['b'].requests('stop'), [rid])
        self.assertEqual(self.clients['a'].requests('stop'), [])   # already terminal
        evidence = self.events(sid, 'workers.reconciled')
        self.assertEqual(evidence, [{'request_id': rid,
                                     'workers': {'a': {'state': 'stopped', 'reason': None},
                                                 'b': {'state': 'stopped', 'reason': None}},
                                     'prior': {'state': 'indeterminate', 'reason': 'controller_restart_execution_unknown',
                                               'revision': evidence[0]['prior']['revision']}}])
        # Records are kept: every earlier event is still there.
        self.assertLessEqual(before, {e['id'] for e in self.store.list_events(sid)})
        # Then periodically, not every reap.
        self.assertEqual(self.runtime.orphan_check_at, self.now[0] + 60)
        thread = self.runtime.orphan_thread
        self.controller.expire()
        self.assertIs(self.runtime.orphan_thread, thread)
        self.start_and_stop_another()

    def test_any_doubt_keeps_the_row_for_a_later_pass(self):
        sid, rid = self.crashed(leases={'a': ('stopped', None), 'b': ('ready', None)})
        self.clients['b'].unreadable = True
        self.assertEqual(self.runtime.reconcile_orphans(), [])
        self.clients['b'].unreadable, self.clients['b'].stop_to = False, 'stopping'
        self.assertEqual(self.runtime.reconcile_orphans(), [])      # asked to stop, not yet stopped
        self.assertEqual(self.store.get_session(sid)['state'], 'indeterminate')
        self.assert_admission_refused()
        self.assertEqual(self.events(sid, 'workers.reconciled'), [])
        self.clients['b'].leases[rid]['state'] = 'stopped'
        self.assertEqual(self.runtime.reconcile_orphans(), [sid])
        self.assertEqual(self.runtime.reconcile_orphans(), [])      # settles once

    def test_a_receipt_bound_to_another_session_is_never_stopped_or_trusted(self):
        sid, rid = self.crashed(leases={'a': ('stopped', None), 'b': ('ready', None)})
        self.clients['b'].leases[rid]['session_id'] = 'f' * 32
        self.assertEqual(self.runtime.reconcile_orphans(), [])
        self.assertEqual(self.clients['b'].requests('stop'), [])
        self.assertEqual(self.store.get_session(sid)['state'], 'indeterminate')

    def test_a_request_the_supervisor_never_reserved_settles_only_after_the_grace(self):
        sid, rid = self.crashed(leases={'a': ('stopped', None)})   # LA never got the start
        self.assertEqual(self.runtime.reconcile_orphans(), [])
        self.now[0] += ABSENT_GRACE_SECONDS
        self.assertEqual(self.runtime.reconcile_orphans(), [sid])
        self.assertEqual(self.events(sid, 'workers.reconciled')[0]['workers']['b'],
                         {'state': 'absent', 'reason': 'request_not_found'})

    def test_a_row_that_never_prepared_workers_settles_without_contacting_them(self):
        sid, _ = self.crashed(preparing=False)
        self.assertEqual(self.runtime.reconcile_orphans(), [sid])
        self.assertEqual([c.calls for c in self.clients.values()], [[], []])
        self.assertEqual(self.events(sid, 'workers.reconciled')[0]['workers'], {})

    def test_an_la_only_loop_recording_consults_only_the_la_supervisor(self):
        sid, rid = self.crashed(arms=('b',), experiment='apartment-viewing', leases={'b': ('failed', 'preflight_rejected')})
        self.assertEqual(self.runtime.reconcile_orphans(), [sid])
        self.assertEqual(self.clients['a'].calls, [])
        self.assertEqual(self.clients['b'].adopted, [(rid, sid, 'pose')])
        self.assertEqual(self.events(sid, 'workers.reconciled')[0]['workers'],
                         {'b': {'state': 'failed', 'reason': 'preflight_rejected'}})

    def test_a_closed_runtime_writes_nothing(self):
        sid, _ = self.crashed(leases={'a': ('stopped', None), 'b': ('stopped', None)})
        self.runtime.close()
        self.assertEqual(self.runtime.reconcile_orphans(), [])
        self.assertEqual(self.store.get_session(sid)['state'], 'indeterminate')

    def test_a_changed_row_is_not_overwritten(self):
        sid, _ = self.crashed(leases={'a': ('stopped', None), 'b': ('stopped', None)})
        stale = self.store.get_session(sid)
        self.controller.stop(sid)                                    # an operator's Stop wins
        self.assertEqual(self.store.get_session(sid)['reason'], 'user_stop')
        self.assertFalse(self.store.settle_indeterminate(sid, stale['revision'], RECONCILED_REASON, {}))
        self.assertEqual(self.store.get_session(sid)['reason'], 'user_stop')


if __name__ == '__main__':
    unittest.main()
