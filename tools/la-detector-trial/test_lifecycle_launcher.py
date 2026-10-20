import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import lifecycle_launcher
import start_live
from lifecycle import Lifecycle,PreflightRejected
from lifecycle_launcher import Launcher,READY,declared_readiness

RID='12345678-1234-1234-1234-123456789abc'
REPO_WORKER=Path(__file__).resolve().parents[2]/'stack'/'services'/'perception-lab'
LEGACY="async def reply():\n    return {'error':'request_unavailable_or_indeterminate'}\n"


class Worker:
    """RemoteHTTP double: one ingress slot shared by execute and lookup."""
    def __init__(self):self.busy=False;self.lookups=0;self.reply=(409,json.dumps(READY).encode())
    def forward(self,path,body):
        if path=='/v1/specialist/lookup':self.lookups+=1
        if self.busy:return 429,b'{"error":"ingress_busy"}'
        return self.reply


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        patcher=patch.object(lifecycle_launcher,'BASE',self.base);patcher.start();self.addCleanup(patcher.stop)
        self.worker=Worker();self.stops=[];self.now=1000
        self.unit={'ActiveState':'inactive','MainPID':'0','InvocationID':'','ControlGroup':''}
        launcher=Launcher.__new__(Launcher)  # Host binding and TLS context are not under test.
        launcher.unit=lambda:dict(self.unit)
        launcher.forward=self.worker.forward
        def start(epoch,expires,variant='detector'):
            manifest={'sha256':'a'*64,'engine_epoch':epoch}
            (self.base/('live-config-'+epoch+'.json')).write_text(json.dumps({'manifest':manifest}))
            self.unit={'ActiveState':'active','MainPID':'42','InvocationID':'owned','ControlGroup':''}
            return 'owned',{**manifest,'artifact_sha256':'a'*64}
        launcher.start=start
        launcher.stop=lambda invocation,epoch:self.stops.append(invocation)
        self.launcher=launcher
        self.subject=Lifecycle(self.base/'ledger.sqlite',launcher,clock=lambda:self.now)

    def tearDown(self):
        if self.subject.thread:self.subject.thread.join(2)
        self.subject.db.close();self.subject.owner_file.close();self.temp.cleanup()

    def start(self):
        self.subject.start(RID,'a'*32);self.subject.thread.join(2)
        return self.subject.status(RID)

    def test_ready_lease_never_probes_the_busy_worker_slot(self):
        self.assertEqual(self.start()['state'],'ready')
        probes=self.worker.lookups
        self.worker.busy=True  # An admitted execute holds the single ingress slot.
        for _ in range(3):self.subject.tick()
        self.assertEqual(self.subject.status(RID)['state'],'ready')
        self.assertEqual(self.worker.lookups,probes)
        self.unit['InvocationID']='another'
        self.assertEqual(self.subject.status(RID)['state'],'indeterminate')
        self.assertEqual(self.stops,[])

    def test_ready_lease_ends_or_expires_through_unit_identity(self):
        self.start();self.unit.update(ActiveState='inactive',MainPID='0')
        self.assertEqual(self.subject.status(RID)['state'],'stopped')
        self.subject.start('22345678-1234-1234-1234-123456789abc','b'*32);self.subject.thread.join(2)
        self.now=1115;self.subject.tick()
        self.assertEqual(self.stops,['owned'])

    def test_readiness_requires_the_exact_profile_admission_reply(self):
        (self.base/'live-config-la-live-x.json').write_text(json.dumps({'manifest':{'sha256':'a'*64,'engine_epoch':'la-live-x'}}))
        self.unit={'ActiveState':'active','MainPID':'42','InvocationID':'owned','ControlGroup':''}
        # Any 409 once meant ready, including a resident-profile mismatch.
        for reply in ((409,b'{"error":"request_unavailable_or_indeterminate"}'),(429,b'{"error":"ingress_busy"}'),
                      (409,b'not json'),(409,json.dumps({**READY,'profile_admitted':1}).encode()),
                      (200,json.dumps(READY).encode()),(503,b'{"error":"admission_closed"}')):
            self.worker.reply=reply
            with self.subTest(reply=reply):self.assertEqual(self.launcher.inspect('owned','la-live-x'),'starting')
        self.worker.reply=(409,json.dumps(READY).encode())
        self.assertEqual(self.launcher.inspect('owned','la-live-x'),'ready')
        self.assertEqual(self.launcher.inspect('other','la-live-x'),'unrelated')

    def test_mismatched_or_busy_probe_keeps_a_starting_lease_unready(self):
        self.worker.reply=(409,b'{"error":"request_unavailable_or_indeterminate"}')
        self.assertEqual(self.start()['state'],'starting')
        self.worker.reply=(409,json.dumps(READY).encode());self.worker.busy=True
        self.assertEqual(self.subject.status(RID)['state'],'starting')
        self.worker.busy=False
        self.assertEqual(self.subject.status(RID)['state'],'ready')

    def test_known_preflight_gate_reaches_lease_reason_without_threshold_detail(self):
        self.launcher.start=Launcher.start.__get__(self.launcher)
        for error,reason in ((ValueError('host memory headroom failed'),'preflight_memory'),
                             (ValueError('gpu memory headroom failed'),'preflight_vram'),
                             (ValueError('gpu thermal headroom failed'),'preflight_thermal'),
                             (ValueError('something else'),'preflight_rejected'),
                             (OSError('host memory headroom failed'),'preflight_rejected')):
            def fail():raise error
            with self.subTest(reason=reason),patch.object(lifecycle_launcher,'preflight',fail):
                with self.assertRaises(PreflightRejected) as caught:self.launcher.start('la-live-x',0)
                self.assertEqual(caught.exception.reason,reason)
        self.unit={'ActiveState':'active','MainPID':'7','InvocationID':'other','ControlGroup':''}
        with self.assertRaises(PreflightRejected) as caught:self.launcher.start('la-live-x',0)
        self.assertEqual(caught.exception.reason,'preflight_worker_active')

    def test_lifecycle_records_allowlisted_preflight_reason_only(self):
        reasons=iter(['preflight_memory','memory below 3GiB: 2.4'])
        def reject(epoch,expires):raise PreflightRejected('gate',reason=next(reasons))
        self.launcher.start=reject
        self.assertEqual(self.start()['state'],'failed')
        self.assertEqual(self.subject.status(RID)['reason'],'preflight_memory')
        other='22345678-1234-1234-1234-123456789abc'
        self.subject.start(other,'b'*32);self.subject.thread.join(2)
        self.assertEqual(self.subject.status(other)['reason'],'preflight_rejected')

    def stage(self,source,directory='release/app'):
        (self.base/directory).mkdir(parents=True,exist_ok=True)
        if source is not None:(self.base/directory/'remote_http.py').write_text(source)

    def tree(self):return patch.multiple(start_live,BASE=self.base,RELEASE=self.base/'release')

    def test_worker_root_follows_the_launch_selection(self):
        with self.tree():
            self.assertEqual(start_live.worker_root('detector'),self.base/'release'/'app')
            (self.base/'pose-source.json').write_text(json.dumps({'directory':'pose-release/service'}))
            self.assertEqual(start_live.worker_root('pose'),self.base/'pose-release'/'service')
            self.assertEqual(start_live.worker_root('detector'),self.base/'release'/'app')
            (self.base/'managed-source.json').write_text(json.dumps({'directory':'managed-release/service'}))
            for variant in ('detector','pose'):
                self.assertEqual(start_live.worker_root(variant),self.base/'managed-release'/'service')

    def test_staged_worker_without_the_readiness_reply_is_refused_before_launch(self):
        self.launcher.start=Launcher.start.__get__(self.launcher)
        launches=[]
        class Launched(Exception):pass
        def run(*args,**kwargs):launches.append(args);raise Launched()
        (self.base/'pose-source.json').write_text(json.dumps({'directory':'pose-release/service'}))
        with self.tree(),patch.object(lifecycle_launcher,'preflight',lambda:None),patch.object(lifecycle_launcher.subprocess,'run',run):
            # An older release answered every lookup miss alike and never proves ready.
            for source,variant,directory in ((LEGACY,'detector','release/app'),
                    ('READINESS_REPLY='+repr({**READY,'profile_admitted':1}),'detector','release/app'),
                    ('READINESS_REPLY=dict(error=1)','detector','release/app'),
                    (None,'pose','pose-release/service')):
                self.stage(source,directory)
                with self.subTest(source=source,variant=variant):
                    with self.assertRaises(PreflightRejected) as caught:self.launcher.start('la-live-x',0,variant=variant)
                    self.assertEqual(caught.exception.reason,'preflight_worker_readiness')
            self.assertEqual(launches,[])
            self.stage((REPO_WORKER/'remote_http.py').read_text(encoding='utf-8'))
            with self.assertRaises(Launched):self.launcher.start('la-live-x',0)
            self.assertEqual(len(launches),1)

    def test_lease_fails_with_the_readiness_reason_instead_of_waiting(self):
        self.launcher.start=Launcher.start.__get__(self.launcher)
        self.stage(LEGACY)
        with self.tree(),patch.object(lifecycle_launcher,'preflight',lambda:None):
            self.assertEqual(self.start()['state'],'failed')
        self.assertEqual(self.subject.status(RID)['reason'],'preflight_worker_readiness')
        self.assertEqual(self.worker.lookups,0)

    def test_this_repository_worker_declares_the_required_reply(self):
        self.assertEqual(declared_readiness(REPO_WORKER),READY)

    def test_absence_evidence_reads_unit_state_only(self):
        self.assertEqual(self.launcher.absence('la-live-x'),{'unit':lifecycle_launcher.UNIT,'active_state':'inactive',
            'main_pid':'0','invocation_id':None,'worker_absent':True,'config_present':False})
        self.unit={'ActiveState':'active','MainPID':'9','InvocationID':'x','ControlGroup':''}
        self.assertFalse(self.launcher.absence('la-live-x')['worker_absent'])
        self.assertEqual(self.stops,[])


if __name__=='__main__':unittest.main()
