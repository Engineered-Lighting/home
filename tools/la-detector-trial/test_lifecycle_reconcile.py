import contextlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from lifecycle import Lifecycle,LifecycleError
import lifecycle_reconcile

RID='12345678-1234-1234-1234-123456789abc'
OTHER='22345678-1234-1234-1234-123456789abc'


class Launcher:
    """Fixed-unit double; reconcile may only read absence evidence."""
    def __init__(self):self.fail=True;self.absent=True;self.config=False;self.calls=[]
    def start(self,epoch,expires,variant='detector'):
        self.calls.append('start')
        if self.fail:raise TimeoutError()
        return 'owned',{'engine_epoch':epoch}
    def inspect(self,invocation,epoch):return 'ready'
    def stop(self,invocation,epoch):self.calls.append('stop')
    def absence(self,epoch):
        self.calls.append('absence')
        return {'unit':'lab-la-detector-trial.service','active_state':'inactive' if self.absent else 'active',
                'main_pid':'0' if self.absent else '9','invocation_id':None,'worker_absent':self.absent,'config_present':self.config}


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.launcher=Launcher();self.now=1000
        self.subject=Lifecycle(Path(self.temp.name)/'ledger.sqlite',self.launcher,clock=lambda:self.now)
        self.subject.start(RID,'a'*32);self.subject.thread.join(2)

    def tearDown(self):
        if self.subject.thread:self.subject.thread.join(2)
        with contextlib.suppress(Exception):self.subject.db.close()
        self.subject.owner_file.close();self.temp.cleanup()

    def lease(self):return self.subject.db.execute('SELECT state,reason FROM leases WHERE request_id=?',(RID,)).fetchone()

    def test_unproven_launch_is_failed_only_after_expiry_with_recorded_absence(self):
        self.assertEqual(tuple(self.lease()),('indeterminate','launch_unproven'))
        self.subject.tick()  # Monitoring alone never clears an unlaunched lease.
        with self.assertRaises(LifecycleError) as caught:
            self.subject.reconcile_unlaunched(RID,self.launcher.absence)
        self.assertEqual(caught.exception.reason,'lease_not_expired')
        self.now=1115
        receipt=self.subject.reconcile_unlaunched(RID,self.launcher.absence)
        self.assertEqual((receipt['state'],receipt['reason']),('failed','reconciled_worker_absent'))
        row=self.subject.db.execute('SELECT checked_at,evidence FROM lease_reconciliations WHERE request_id=?',(RID,)).fetchone()
        evidence=json.loads(row['evidence'])
        self.assertEqual((row['checked_at'],evidence['prior_reason'],evidence['worker_absent']),(1115,'launch_unproven',True))
        self.assertEqual(evidence['epoch'],receipt['engine_epoch'])
        self.assertNotIn('stop',self.launcher.calls)
        self.launcher.fail=False
        self.subject.start(OTHER,'b'*32);self.subject.thread.join(2)
        self.assertEqual(self.launcher.calls.count('start'),2)

    def test_reason_claims_only_present_absence_after_a_possible_launch(self):
        # start_live wrote this epoch's config, so systemd-run may have run a
        # unit that has since exited. Absence now is not proof it never ran.
        self.now=1115;self.launcher.config=True
        receipt=self.subject.reconcile_unlaunched(RID,self.launcher.absence)
        self.assertEqual((receipt['state'],receipt['reason']),('failed','reconciled_worker_absent'))
        evidence=json.loads(self.subject.db.execute('SELECT evidence FROM lease_reconciliations').fetchone()[0])
        self.assertEqual((evidence['config_present'],evidence['prior_reason']),(True,'launch_unproven'))
        self.assertNotIn('stop',self.launcher.calls)

    def test_present_or_unreadable_worker_keeps_lease_blocking(self):
        self.now=1115;self.launcher.absent=False
        with self.assertRaises(LifecycleError) as caught:
            self.subject.reconcile_unlaunched(RID,self.launcher.absence)
        self.assertEqual(caught.exception.reason,'worker_absence_unproven')
        def unreadable(epoch):raise OSError('systemctl unavailable')
        with self.assertRaises(OSError):self.subject.reconcile_unlaunched(RID,unreadable)
        self.assertEqual(tuple(self.lease()),('indeterminate','launch_unproven'))
        self.assertIsNone(self.subject.db.execute('SELECT 1 FROM lease_reconciliations').fetchone())
        with self.assertRaises(LifecycleError):self.subject.start(OTHER,'b'*32)
        self.assertEqual(self.launcher.calls.count('stop'),0)

    def test_restarted_launch_without_invocation_is_reconcilable_but_owned_units_are_not(self):
        self.subject.db.execute("UPDATE leases SET reason='supervisor_restarted' WHERE request_id=?",(RID,))
        self.now=1115
        self.assertEqual(self.subject.reconcile_unlaunched(RID,self.launcher.absence)['state'],'failed')
        self.launcher.fail=False;self.now=2000
        self.subject.start(OTHER,'b'*32);self.subject.thread.join(2)
        self.subject.db.execute("UPDATE leases SET state='indeterminate' WHERE request_id=?",(OTHER,))
        self.now=3000
        with self.assertRaises(LifecycleError) as caught:
            self.subject.reconcile_unlaunched(OTHER,self.launcher.absence)
        self.assertEqual(caught.exception.reason,'reconcile_not_applicable')

    def test_cli_prints_receipt_or_reason_and_releases_only_the_ledger(self):
        self.now=1115;out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            self.launcher.absent=False
            self.assertEqual(lifecycle_reconcile.main([RID],opener=lambda:(self.subject,self.launcher)),1)
        self.assertEqual(json.loads(err.getvalue()),{'reason':'worker_absence_unproven'})
        self.assertNotIn('stop',self.launcher.calls)
        second=Lifecycle(Path(self.temp.name)/'ledger.sqlite',self.launcher,clock=lambda:self.now)
        self.launcher.absent=True
        with contextlib.redirect_stdout(out):
            self.assertEqual(lifecycle_reconcile.main([RID],opener=lambda:(second,self.launcher)),0)
        self.assertEqual(json.loads(out.getvalue())['reason'],'reconciled_worker_absent')


if __name__=='__main__':unittest.main()
