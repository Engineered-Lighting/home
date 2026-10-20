"""Explicit, bounded LA detector worker using the existing worker protocol.

Not used by the recording controller. Launching this factory is model execution
and requires an admitted trial; importing it or browsing Home does nothing.
"""
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import time

CHECKPOINT='646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b'
UNIT='lab-la-detector-trial.service'
PREPROCESSING=dict(imgsz=[640,640],confidence=.25,iou=.7,max_det=128,retina_masks=False)


def validate_limits(values):
    if (values['memory.max'].strip()!=str(4*1024**3)
            or values['memory.swap.max'].strip()!='0' or values['pids.max'].strip()!='64'):
        raise ValueError('Dedicated trial RAM/swap/task limits required')
    quota,period=values['cpu.max'].split()
    if not quota.isdecimal() or not period.isdecimal() or not 0<int(quota)<=int(period):
        raise ValueError('At most one CPU quota required')


def validate_config(config, now):
    if set(config)-{'pose','runtime_seconds'}!={'schema_version','checkpoint_path','ledger_root','manifest','grants','expires_at','maximum_requests','gate_references'} or config['schema_version']!=1:
        raise ValueError('Exact trial configuration required')
    from session_budget import worker_budget
    limits=worker_budget(config)
    expiry=config['expires_at']
    if type(expiry) not in (int,float) or not now<expiry<=now+limits['runtime_seconds']:
        raise ValueError('Fresh trial expiry within provisioned runtime required')
    if type(config['maximum_requests']) is not int or not 1<=config['maximum_requests']<=limits['maximum_requests']:
        raise ValueError('Bounded request count required')
    gates=config['gate_references']
    if set(gates)!={'technical','health_resource','workload_authorization'} or any(not isinstance(v,str) or not 1<=len(v)<=256 for v in gates.values()):
        raise ValueError('Three independently reviewed gate references required')
    m=config['manifest']
    if (m.get('device_id')!='echo-la-rtx-pro-6000' or m.get('device')!='cuda:0'
            or m.get('checkpoint')!='yolo26s' or m.get('sha256')!=CHECKPOINT
            or m.get('runtime_version')!='8.4.0' or m.get('precision')!='fp32'
            or m.get('format')!='pt' or m.get('task')!='detect' or m.get('preprocessing')!=PREPROCESSING
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',m.get('engine_epoch',''))):
        raise ValueError('Exact pinned FP32 LA detector required')
    if len(config['grants'])!=1 or config['grants'][0].get('site_id')!='victoria' or config['grants'][0].get('cameras')!=['den']:
        raise ValueError('Victoria den grant only')
    base=PurePosixPath('/home/marcelo-lima/home-lab-yolo26-trial')
    for key,parent in [('checkpoint_path',base),('ledger_root',base/'live-ledgers')]:
        path=PurePosixPath(config[key])
        if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(parent):
            raise ValueError('Dedicated local worker paths required')
    if config['checkpoint_path'] != str(base/'yolo26s.pt'):
        raise ValueError('Only the previously verified LA checkpoint is admitted')
    if 'pose' in config:
        from pose_composition import validate_pose
        validate_pose(config['pose'])
    return deepcopy(config)


def create_la_detector_app():
    if sys.platform!='linux' or socket.gethostname()!='EngineeredLightingServer1':
        raise ValueError('Explicit LA host required')
    groups=[line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::/')]
    if len(groups)!=1 or not groups[0].endswith('/'+UNIT):
        raise ValueError('Dedicated bounded detector trial unit required')
    cgroup=Path('/sys/fs/cgroup')/groups[0].lstrip('/')
    validate_limits({k:(cgroup/k).read_text() for k in ('memory.max','memory.swap.max','pids.max','cpu.max')})
    properties=subprocess.run(['systemctl','--user','show',UNIT,'-p','RuntimeMaxUSec','-p','Restart'],
        capture_output=True,text=True,check=True,timeout=3).stdout
    properties=dict(line.split('=',1) for line in properties.splitlines() if '=' in line)
    path=Path(os.environ['PERCEPTION_LAB_LA_WORKER_CONFIG'])
    if path.is_symlink() or path.stat().st_uid!=os.geteuid() or path.stat().st_mode&0o077:
        raise ValueError('Private operator provisioning required')
    with path.open('rb') as source:raw=source.read(16385)
    if len(raw)>16384:raise ValueError('Configuration too large')
    config=validate_config(json.loads(raw),time.time())
    from session_budget import worker_budget, verify_process_lifetime
    limits=worker_budget(config)
    verify_process_lifetime(properties,limits)
    base=Path('/home/marcelo-lima/home-lab-yolo26-trial')
    for key,parent in [('checkpoint_path',base),('ledger_root',base/'live-ledgers')]:
        if not Path(config[key]).resolve().is_relative_to(parent):
            raise ValueError('Worker path traverses outside its private storage')
    from config import Settings
    from store import Store
    from remote_receiver import RemoteReceiver
    from remote_http import RemoteHTTP
    from ultralytics_worker import UltralyticsWorker
    ledger=Store(Settings(Path(config['ledger_root']),quota_bytes=128*1024**2))
    pending=set(); count=0; manifest=config['manifest']
    started=time.monotonic()
    def fresh():return time.time()<config['expires_at'] and time.monotonic()-started<limits['runtime_seconds']
    def worker_admit(phase, proposed, request_id):
        if proposed!=manifest or not fresh():return False
        if phase=='load':return request_id is None
        if phase in {'infer','infer_with_warmup'} and request_id in pending:
            pending.remove(request_id);return True
        return False
    profile={**manifest,'artifact_sha256':manifest['sha256']}
    if 'pose' in config:
        from pose_composition import profile as pose_profile
        profile=pose_profile(manifest,config['pose'])
    def admit(phase,reference):
        nonlocal count
        if reference.get('site_id')!='victoria' or reference.get('camera_id')!='den' or reference.get('profile')!=profile:return False
        # Lookup remains possible after lease expiry; expiry cannot erase proof.
        if phase=='source':return True
        if phase!='execute' or not fresh() or count>=config['maximum_requests']:return False
        with ledger.lock:
            if ledger.db.execute("SELECT 1 FROM remote_execution WHERE state!='terminal' LIMIT 1").fetchone():return False
        count+=1;pending.add(reference['request_id']);return True
    try:
        detector=UltralyticsWorker(manifest,worker_admit)
        worker=detector
        if 'pose' in config:
            from pose_composition import compose
            worker=compose(ledger,detector,config['pose'],fresh,pending)
        receiver=RemoteReceiver(ledger,worker,profile,admit)
        app=RemoteHTTP(receiver,config['grants'],owns_store=True)
        with ledger.lock:
            if ledger.db.execute("SELECT 1 FROM remote_execution WHERE state!='terminal' LIMIT 1").fetchone():
                raise ValueError('Unreconciled prior execution blocks startup')
        with ledger.transaction() as db:
            key='detector-trial-epoch:'+manifest['engine_epoch']
            if db.execute('SELECT 1 FROM configuration WHERE key=?',(key,)).fetchone():
                raise ValueError('This worker trial epoch was already attempted')
            db.execute('INSERT INTO configuration VALUES(?,?)',(key,'attempted'))
        import importlib.metadata as metadata
        for name, expected in [('torch','2.10.0+cu128'),('torchvision','0.25.0+cu128'),('ultralytics','8.4.0')]:
            if metadata.version(name)!=expected:
                raise ValueError('Runtime differs from the successful saved trial')
        free=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],
            capture_output=True,text=True,check=True,timeout=5).stdout.strip().splitlines()
        if len(free)!=1 or not free[0].isdigit() or int(free[0])<32768:
            raise ValueError('At least 32GiB GPU headroom required before loading')
        import torch
        torch.set_num_threads(1)
        # Torch allocator bound only; does not isolate whole-process CUDA memory.
        torch.cuda.set_per_process_memory_fraction(512*1024**2/torch.cuda.get_device_properties(0).total_memory,0)
        detector.load(Path(config['checkpoint_path']))
        return app
    except BaseException:
        ledger.close();raise
