"""Arm one coordinated bounded trial; never retry or stop an unrelated unit."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time

BASE = Path('/home/marcelo-lima/home-lab-yolo26-trial')
UNIT = 'lab-la-detector-trial.service'


def unit():
    raw = subprocess.check_output(['systemctl','--user','show',UNIT,'-p','ActiveState','-p','InvocationID'],
                                  text=True, timeout=10)
    return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--starts-at',type=float,required=True)
    parser.add_argument('--epoch',required=True)
    parser.add_argument('--firewall-approval-reference',required=True)
    args=parser.parse_args()
    if socket.gethostname()!='EngineeredLightingServer1':
        raise ValueError('LA host required')
    if not math.isfinite(args.starts_at) or not 15 <= args.starts_at-time.time() <= 600:
        raise ValueError('future start must be between 15 and 600 seconds away')
    if re.fullmatch(r'la-live-[A-Za-z0-9_-]{1,80}',args.epoch) is None:
        raise ValueError('invalid trial epoch')
    os.umask(0o077)
    with (BASE/'scheduled-live.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if unit().get('ActiveState') not in ('inactive','failed'):
            raise RuntimeError('existing trial unit is active')
        receipt=BASE/('scheduled-'+args.epoch+'.jsonl')
        with receipt.open('x') as out:
            def record(**values):
                out.write(json.dumps(dict(at=time.time(),**values))+'\n')
                out.flush();os.fsync(out.fileno())
            expires=args.starts_at+115
            record(state='armed',starts_at=args.starts_at,expires_at=expires,epoch=args.epoch)
            print(json.dumps(dict(state='armed',starts_at=args.starts_at,expires_at=expires)),flush=True)
            end_wait=time.monotonic()+610
            while time.time()<args.starts_at:
                if time.monotonic()>end_wait:
                    raise RuntimeError('clock discontinuity during scheduled wait')
                time.sleep(min(1,max(.01,args.starts_at-time.time())))
            if time.time()>args.starts_at+5 or unit().get('ActiveState') not in ('inactive','failed'):
                record(state='missed_or_busy');return 1
            launched=subprocess.run(['python3','-I',str(BASE/'start_live.py'),'--epoch',args.epoch,
                '--expires-at',str(expires),'--firewall-approval-reference',args.firewall_approval_reference],
                capture_output=True,text=True,timeout=45)
            if launched.returncode:
                record(state='launch_failed',returncode=launched.returncode);return 1
            owned=unit().get('InvocationID')
            if not owned:
                record(state='launch_identity_unavailable');return 1
            record(state='launched',invocation_id=owned)
            try:
                monotonic_end=time.monotonic()+120
                while time.time()<expires and time.monotonic()<monotonic_end:
                    current=unit()
                    if current.get('InvocationID')!=owned or current.get('ActiveState') not in ('active','activating'):
                        record(state='unit_ended',unit_state=current);break
                    time.sleep(1)
            finally:
                current=unit()
                if current.get('InvocationID')==owned:
                    subprocess.run(['systemctl','--user','stop',UNIT],check=True,timeout=15)
                record(state='finished',unit_state=unit())
    return 0


if __name__=='__main__':
    raise SystemExit(main())
