"""Fixed LA systemd launcher and verified loopback TLS transport."""
import ast
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
from start_live import BASE,TLS,preflight,worker_root
from lifecycle import PreflightRejected

UNIT='lab-la-detector-trial.service'
# Exact start_live.preflight refusals mapped to content-free lease reasons.
GATES={'host memory headroom failed':'preflight_memory','gpu memory headroom failed':'preflight_vram',
    'gpu thermal headroom failed':'preflight_thermal','disk headroom failed':'preflight_disk',
    'new failed service':'preflight_failed_unit','new fault or unavailable fault check':'preflight_kernel_fault',
    'model unhealthy':'preflight_model_health','staged source changed':'preflight_source'}
# The worker's lookup miss after its exact-profile admission; other 409s are not ready.
READY={'error':'request_unavailable_or_indeterminate','profile_admitted':True}


def declared_readiness(root):
    """The staged worker's declared readiness reply, parsed from source, never imported."""
    for node in ast.parse((root/'remote_http.py').read_bytes()).body:
        if isinstance(node,ast.Assign) and [getattr(t,'id',None) for t in node.targets]==['READINESS_REPLY']:
            return ast.literal_eval(node.value)


class Launcher:
    def __init__(self):
        if socket.gethostname()!='EngineeredLightingServer1':
            raise ValueError('LA host required')
        self.context=ssl.create_default_context(cafile=str(TLS/'ca.pem'))

    def unit(self):
        raw=subprocess.check_output(['systemctl','--user','show',UNIT,'-p','InvocationID','-p','ActiveState',
            '-p','MainPID','-p','ControlGroup'],text=True,timeout=5)
        return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)

    @staticmethod
    def ended(value):
        if value.get('ActiveState') not in ('inactive','failed') or value.get('MainPID')!='0':return False
        group=value.get('ControlGroup')
        if not group:return True
        events=Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events'
        if not events.exists():return True
        return 'populated 0' in events.read_text().splitlines()

    def start(self,epoch,expires,variant='detector'):
        if variant not in {'detector','pose'}:raise PreflightRejected('variant not provisioned')
        if not self.ended(self.unit()):raise PreflightRejected('worker already active',reason='preflight_worker_active')
        try:preflight()
        except Exception as error:
            gate=GATES.get(str(error)) if type(error) is ValueError else None
            raise PreflightRejected('health preflight rejected',reason=gate) from None
        # Supervisor and worker trees deploy separately. A worker older than this
        # readiness contract could never prove ready; refuse it before launch.
        try:declared=json.dumps(declared_readiness(worker_root(variant)),sort_keys=True)
        except Exception:declared=None
        if declared!=json.dumps(READY,sort_keys=True):
            raise PreflightRejected('staged worker readiness differs',reason='preflight_worker_readiness')
        from session_budget import provisioned_budget
        limits=provisioned_budget()
        subprocess.run(['/usr/bin/python3','-I',str(BASE/'start_live.py'),'--epoch',epoch,
            '--expires-at',str(expires),'--runtime-seconds',str(limits['runtime_seconds']),'--supervised-loopback','--variant',variant,
            '--firewall-approval-reference','user-turn-01a0e05d-6078-79a0-99c9-963719cb4eb1'],
            check=True,capture_output=True,timeout=45)
        value=self.unit()
        if value.get('ActiveState') not in ('active','activating') or not value.get('InvocationID'):
            raise RuntimeError('launch not proven')
        config=json.loads((BASE/('live-config-'+epoch+'.json')).read_text())
        profile=config['manifest']
        profile={**profile,'artifact_sha256':json.loads((BASE/'live-binding.template.json').read_text())['profile']['artifact_sha256']}
        if 'pose' in config:
            from pose_composition import profile as pose_profile
            profile=pose_profile(config['manifest'],config['pose'])
        return value['InvocationID'],profile

    def forward(self,path,body):
        if path not in ('/v1/specialist/execute','/v1/specialist/lookup') or len(body)>6*1024**2:
            raise ValueError('fixed worker routes only')
        client=http.client.HTTPSConnection('192.168.0.100',9445,context=self.context,timeout=10)
        # Connect only to loopback while verifying the provisioned certificate's
        # original IP SAN. No global trust change or forwarded caller URL.
        client._create_connection=lambda address,timeout,source_address=None:socket.create_connection(('127.0.0.1',9445),timeout)
        try:
            token=(TLS/'client.token').read_text()
            client.request('POST',path,body,{'Content-Type':'application/json','Authorization':'Bearer '+token})
            response=client.getresponse()
            data=response.read(1024*1024+1)
            if len(data)>1024*1024:raise ValueError('worker response too large')
            if response.getheader('Content-Type','').split(';')[0]!='application/json':
                raise ValueError('worker response type invalid')
            return response.status,data
        finally:client.close()

    def identity(self,invocation):
        value=self.unit()
        if self.ended(value):return 'ended'
        if value.get('InvocationID')!=invocation:return 'unrelated'
        return 'running' if value.get('ActiveState')=='active' else 'starting'

    def absence(self,epoch):
        """Operator reconcile evidence; inspects only, never starts or stops."""
        value=self.unit()
        return dict(unit=UNIT,active_state=value.get('ActiveState'),main_pid=value.get('MainPID'),
                    invocation_id=value.get('InvocationID') or None,worker_absent=self.ended(value),
                    config_present=(BASE/('live-config-'+epoch+'.json')).exists())

    def inspect(self,invocation,epoch):
        status=self.identity(invocation)
        if status in ('ended','unrelated'):return status
        try:
            config=json.loads((BASE/('live-config-'+epoch+'.json')).read_text())
            profile={**config['manifest'],'artifact_sha256':config['manifest']['sha256']}
            if 'pose' in config:
                from pose_composition import profile as pose_profile
                profile=pose_profile(config['manifest'],config['pose'])
            reference=dict(site_id='victoria',camera_id='den',session_id='readiness',request_id='readiness',
                           body_sha256='0'*64,profile=profile)
            status,data=self.forward('/v1/specialist/lookup',json.dumps(reference).encode())
            value=json.loads(data)
            return 'ready' if status==409 and value==READY and value['profile_admitted'] is True else 'starting'
        except (OSError,ValueError,http.client.HTTPException):return 'starting'

    def stop(self,invocation,epoch):
        value=self.unit()
        if self.ended(value):return
        if value.get('InvocationID')!=invocation:raise RuntimeError('refusing unrelated worker stop')
        subprocess.run(['systemctl','--user','stop',UNIT],check=True,capture_output=True,timeout=15)
