"""Bounded activation through explicit CLI or the authenticated supervisor.

Requires the separately approved private firewall rule and both trial operators
ready. Does not modify networking, restart existing services or retry a trial.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

BASE = Path('/home/marcelo-lima/home-lab-yolo26-trial')
RELEASE = BASE/'live-stage-0c41bff61ca9'
TLS = BASE/'live-tls-20260926'


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=15).stdout


# Diagnosed failures kept as incident history (never reset for green). Exact names only:
# observer units per docs/UBUNTU-AI-HOST-STABILITY-REVIEW-2026-08-13.md; the two superseded
# off-host backup attempts per docs/SHARED-PREFERENCES-RELEASE-2026-09-28.md (W1 section).
DOCUMENTED_FAILED_UNITS=frozenset({'observer-health.service','observer-recover.service',
    'home-agent-preference-offhost-20260928.service','home-agent-preference-offhost-20260928-r2.service'})


def preflight():
    if socket.gethostname()!='EngineeredLightingServer1':raise ValueError('LA host required')
    source=json.loads((RELEASE/'source.json').read_text())
    for name,digest in source['files'].items():
        if hashlib.sha256((RELEASE/'app'/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('staged source changed')
    for line in run(['systemctl','list-units','--failed','--plain','--no-legend']).splitlines():
        if line.split()[0] not in DOCUMENTED_FAILED_UNITS:
            raise ValueError('new failed service')
    model=json.loads(run(['docker','inspect','hav-vllm']))[0]['State']
    if not model['Running'] or model['Health']['Status']!='healthy':raise ValueError('model unhealthy')
    free,temp=map(int,run(['nvidia-smi','--query-gpu=memory.free,temperature.gpu','--format=csv,noheader,nounits']).strip().split(','))
    # Owner 2026-09-29: 16 GiB free (was 32) so the ~3 GiB V-JEPA worker can
    # coexist; separate refusals only name the gate that failed.
    if free<16384:raise ValueError('gpu memory headroom failed')
    if temp>80:raise ValueError('gpu thermal headroom failed')
    if shutil.disk_usage(BASE).free<50*1024**3:raise ValueError('disk headroom failed')
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(memory['MemAvailable'].split()[0])*1024<8*1024**3:raise ValueError('host memory headroom failed')
    # Plain journalctl (the service user is in adm): under NoNewPrivileges a
    # `sudo -n` refusal exited 1 with empty stdout and read as "no faults".
    # Incremental across leases: a clean scan records its start time; the next
    # lease scans only from there (5 s overlap). The first full scan from the
    # fixed baseline is slow at this unit's 50% CPU quota (~15 s, live 2026-09-30).
    checkpoint=BASE/'kernel-fault-checkpoint.json'
    try:since='@%d'%int(float(json.loads(checkpoint.read_text())['clean_until'])-5)
    except Exception:since='2026-09-26 23:07:00 UTC'
    started=time.time()
    fault=subprocess.run(['journalctl','-k','--since',since,'--no-pager','--grep',
        'invoked oom-killer|Out of memory: Killed|kernel panic|soft lockup|hard LOCKUP|Hardware Error|I/O error|segfault'],
        capture_output=True,text=True,timeout=60)
    if fault.returncode!=1 or fault.stderr.strip() or fault.stdout.strip() not in {'','-- No entries --'}:
        raise ValueError('new fault or unavailable fault check')
    temporary=checkpoint.with_suffix('.tmp');temporary.write_text(json.dumps({'clean_until':started}));temporary.replace(checkpoint)
    return source,free,temp


def worker_root(variant):
    """The staged worker tree main() launches for variant; main() verifies its hashes."""
    managed=BASE/'managed-source.json'
    if managed.exists():return BASE/json.loads(managed.read_text())['directory']
    if variant=='pose':return BASE/json.loads((BASE/'pose-source.json').read_text())['directory']
    return RELEASE/'app'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--epoch',required=True)
    parser.add_argument('--expires-at',required=True,type=float)
    parser.add_argument('--firewall-approval-reference',required=True)
    parser.add_argument('--supervised-loopback',action='store_true')
    parser.add_argument('--variant',choices=('detector','pose'),default='detector')
    parser.add_argument('--runtime-seconds',type=int,choices=(120,180,360),default=120)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',args.epoch):raise ValueError('bounded epoch required')
    if not time.time()<args.expires_at<=time.time()+args.runtime_seconds:raise ValueError('coordinated bounded expiry required')
    if not 1<=len(args.firewall_approval_reference)<=256:raise ValueError('exact-rule approval reference required')
    os.umask(0o077)
    source,free,temp=preflight()
    worker_release=RELEASE/'app'
    managed=BASE/'managed-source.json'
    if managed.exists():
        configured=json.loads(managed.read_text())
        worker_release=BASE/configured['directory']
        if not worker_release.resolve().is_relative_to(BASE):raise ValueError('Invalid managed source root')
        for name,sha in configured['files'].items():
            if hashlib.sha256((worker_release/name).read_bytes()).hexdigest()!=sha:raise ValueError('Managed source changed')
        source={'runtime_revision':configured['runtime_revision']}
    elif args.runtime_seconds!=120:
        raise ValueError('Longer trial needs commissioned source release')
    binding=json.loads((BASE/'live-binding.template.json').read_text())
    manifest={k:v for k,v in binding['profile'].items() if k!='artifact_sha256'}
    manifest['engine_epoch']=args.epoch
    if manifest['runtime_revision']!=source['runtime_revision']:raise ValueError('runtime revision differs')
    (BASE/'settings').mkdir(mode=0o700,exist_ok=True)
    path=BASE/('live-config-'+args.epoch+'.json')
    receipt=BASE/('live-health-'+args.epoch+'.json')
    with receipt.open('x') as out:
        json.dump({'checked_at':time.time(),'gpu_free_mib':free,'gpu_temp_c':temp,
                   'faults_since':'2026-09-26 23:07:00 UTC','new_matching_faults':False,
                   'firewall_approval_reference':args.firewall_approval_reference},out)
        out.flush();os.fsync(out.fileno())
    token=(TLS/'client.token').read_text()
    config=dict(schema_version=1,checkpoint_path=str(BASE/'yolo26s.pt'),
        ledger_root=str(BASE/'live-ledgers'/args.epoch),manifest=manifest,
        grants=[dict(site_id='victoria',cameras=['den'],token_sha256=hashlib.sha256(token.encode()).hexdigest())],
        expires_at=args.expires_at,maximum_requests=2*args.runtime_seconds,runtime_seconds=args.runtime_seconds,
        gate_references=dict(technical=str(RELEASE/'source.json'),health_resource=str(receipt),
                             workload_authorization='AGENTS.md owner authorization 2026-09-26'))
    if args.variant=='pose':
        config['pose']=json.loads((BASE/'pose.template.json').read_text())
        config['pose']['manifest']['engine_epoch']=args.epoch
        # Fixed, hash-verified source selected by local provisioning, never a
        # path or command accepted from the lifecycle API caller.
        if not managed.exists():
            pose_source=json.loads((BASE/'pose-source.json').read_text())
            worker_release=BASE/pose_source['directory']
            if not worker_release.resolve().is_relative_to(BASE):raise ValueError('Invalid pose source root')
            for name,sha in pose_source['files'].items():
                if hashlib.sha256((worker_release/name).read_bytes()).hexdigest()!=sha:raise ValueError('Pose source changed')
    with path.open('x') as out:
        json.dump(config,out);out.flush();os.fsync(out.fileno())
    command=['systemd-run','--user','--unit=lab-la-detector-trial','--collect',
        '--property=MemoryMax=4G','--property=MemorySwapMax=0','--property=CPUQuota=100%',
        '--property=TasksMax=64','--property=RuntimeMaxSec='+str(args.runtime_seconds),'--property=Restart=no',
        '--property=TimeoutStopSec=5','--property=KillMode=control-group','--property=UMask=0077',
        '--setenv=PERCEPTION_LAB_LA_WORKER_CONFIG='+str(path),'--setenv=YOLO_AUTOINSTALL=false',
        '--setenv=YOLO_OFFLINE=true','--setenv=YOLO_CONFIG_DIR='+str(BASE/'settings'),
        '--setenv=OMP_NUM_THREADS=1','--setenv=OPENBLAS_NUM_THREADS=1','--setenv=MKL_NUM_THREADS=1',
        '/home/marcelo-lima/.venvs/home-lab-yolo26s-trial/bin/python','-m','uvicorn',
        'la_detector_app:create_la_detector_app','--factory','--app-dir',str(worker_release),
        '--host','127.0.0.1' if args.supervised_loopback else '192.168.0.100',
        '--port','9445' if args.supervised_loopback else '9443','--ssl-keyfile',str(TLS/'server.key'),
        '--ssl-certfile',str(TLS/'server.pem'),'--no-access-log','--log-level','warning',
        '--timeout-keep-alive','2','--timeout-graceful-shutdown','5','--limit-concurrency','2','--backlog','4']
    print(run(command))


if __name__=='__main__':main()
