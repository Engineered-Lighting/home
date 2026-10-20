import tempfile
from pathlib import Path
import unittest
from lifecycle import Lifecycle,LifecycleError,PreflightRejected

RID='12345678-1234-1234-1234-123456789abc'


class Launcher:
    def __init__(self): self.calls=[];self.state='ready';self.stops=[];self.fail=False
    def start(self,epoch,expires,variant='detector'):
        self.variant=variant
        self.calls.append((epoch,expires))
        if self.fail: raise TimeoutError()
        return 'owned-invocation',{'engine_epoch':epoch}
    def inspect(self,invocation,epoch): return self.state
    def stop(self,invocation,epoch): self.stops.append((invocation,epoch));self.state='ended'


class LifecycleTests(unittest.TestCase):
    def test_variant_is_durable_and_cannot_change_on_retry(self):
        self.subject.start(RID,'a'*32,variant='pose');self.subject.thread.join(2)
        self.assertEqual(self.subject.status(RID)['variant'],'pose')
        self.assertEqual(self.launcher.variant,'pose')
        with self.assertRaises(LifecycleError):self.subject.start(RID,'a'*32,variant='detector')
        self.assertEqual(len(self.launcher.calls),1)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.launcher=Launcher();self.now=1000
        self.subject=Lifecycle(Path(self.temp.name)/'ledger.sqlite',self.launcher,clock=lambda:self.now)
    def tearDown(self):
        if self.subject.thread:self.subject.thread.join(2)
        self.launcher.state='ended'
        if not self.subject.close():
            self.subject.db.close();self.subject.owner_file.close()
        self.temp.cleanup()
    def start(self):
        self.subject.start(RID,'a'*32);self.subject.thread.join(2)
        return self.subject.status(RID)
    def test_idempotent_start_and_frozen_expiry(self):
        first=self.start();self.now+=5
        second=self.subject.start(RID,'a'*32)
        self.assertEqual(first['expires_at'],1115)
        self.assertEqual(first['engine_epoch'],second['engine_epoch'])
        self.assertEqual(len(self.launcher.calls),1)
        with self.assertRaises(LifecycleError):self.subject.start(RID,'b'*32)
    def test_single_worker_and_epoch_bound_stop(self):
        first=self.start()
        with self.assertRaises(LifecycleError):self.subject.start('22345678-1234-1234-1234-123456789abc','b'*32)
        with self.assertRaises(LifecycleError):self.subject.stop(RID,'wrong')
        self.assertEqual(self.launcher.stops,[])
        stopped=self.subject.stop(RID,first['engine_epoch'])
        self.assertEqual(stopped['state'],'stopped')
        self.subject.start(RID,'a'*32)
        self.assertEqual(len(self.launcher.calls),1)
    def test_expiry_stops_only_owned_invocation(self):
        first=self.start();self.now=1115;self.subject.tick()
        self.assertEqual(self.subject.status(RID)['state'],'stopped')
        self.assertEqual(self.launcher.stops,[('owned-invocation',first['engine_epoch'])])
    def test_unknown_launch_blocks_new_request(self):
        self.launcher.fail=True
        self.assertEqual(self.start()['state'],'indeterminate')
        with self.assertRaises(LifecycleError):self.subject.start('22345678-1234-1234-1234-123456789abc','b'*32)
        self.assertEqual(len(self.launcher.calls),1)
    def test_changed_unit_identity_never_stops_another_worker(self):
        self.start();self.launcher.state='unrelated';self.now=1200
        self.assertEqual(self.subject.status(RID)['state'],'indeterminate')
        self.assertEqual(self.launcher.stops,[])
    def test_proven_preflight_refusal_does_not_hold_unknown_execution(self):
        def reject(epoch,expires):raise PreflightRejected()
        self.launcher.start=reject
        self.assertEqual(self.start()['state'],'failed')
        self.subject.start('22345678-1234-1234-1234-123456789abc','b'*32)
        self.subject.thread.join(2)


if __name__=='__main__':unittest.main()
