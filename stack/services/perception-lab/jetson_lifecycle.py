"""Fixed local Jetson launcher for the owner's explicitly requested Lab session."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from host_home import JETSON_HOME

BASE = Path(JETSON_HOME + '/perception-lab')
UNIT = 'lab-jetson-detector-trial.service'
BENIGN_KERNEL = re.compile(r'blk_update_request: I/O error, dev mmcblk0, sector 0 op 0x1:\(WRITE\) flags 0x800 phys_seg 0 prio class 0$')
# Content-free gate identities. Thresholds live only in JetsonLauncher.
GATES = {'Unknown failed unit': 'preflight_failed_unit',
         'New kernel fault or unavailable health evidence': 'preflight_kernel_evidence',
         'Capture unavailable': 'preflight_capture', 'Worker already active': 'preflight_worker_active',
         'Storage headroom unavailable': 'preflight_storage', 'Memory headroom unavailable': 'preflight_memory',
         'Required temperature unavailable': 'preflight_thermal_read', 'Thermal headroom unavailable': 'preflight_thermal'}
GATE_CODES = frozenset(GATES.values()) | {'preflight_inspection_error'}


def command(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout).stdout.strip()


class JetsonLauncher:
    # Preflight headroom gates, enforced by health() and only reported by probe().
    MIN_STORAGE_BYTES, MIN_MEMORY_BYTES, MAX_CELSIUS = 20 * 1024**3, 3 * 1024**3, 80

    def __init__(self, release):
        if not re.fullmatch('[0-9a-f]{16}', release):
            raise ValueError('Immutable worker release required')
        from session_budget import provisioned_budget
        self.budget = provisioned_budget()
        self.release = release
        self.private = BASE / 'private/live-detector'
        self.gates = {}

    def unit(self):
        text = command(['systemctl', '--user', 'show', UNIT, '-p', 'InvocationID', '-p', 'MainPID',
                        '-p', 'ActiveState', '-p', 'LoadState', '-p', 'ControlGroup'])
        return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)

    @staticmethod
    def ended(unit):
        if unit.get('MainPID') != '0' or unit.get('ActiveState') not in {'inactive', 'failed'}:
            return False
        group = unit.get('ControlGroup')
        if not group:
            return True
        events = Path('/sys/fs/cgroup') / group.lstrip('/') / 'cgroup.events'
        return not events.exists() or 'populated 0' in events.read_text().splitlines()

    def health(self):
        if socket.gethostname() != 'victoria-den-jetson':
            raise ValueError('Jetson host required')
        for line in command(['systemctl', '--failed', '--no-legend', '--plain']).splitlines():
            # Same exact metadata-only non-dependency commissioned by Den ops.
            # Kernel faults, unknown failed units and all headroom gates below
            # still block every worker launch.
            if line.split()[0] not in {'apport-autoreport.service', 'jetson_metrics.service', 'fwupd-refresh.service'}:
                raise ValueError('Unknown failed unit')
        faults = subprocess.run(['journalctl', '-k', '-b', '--since', '20 minutes ago', '--no-pager',
                                 '--grep', 'MCE|hardware error|lockup|thermal.*critical|Out of memory|oom-kill|panic|I/O error|segfault'],
                                capture_output=True, text=True, timeout=10)
        # Exact known-benign line only: an empty flush to the idle SD/eMMC slot
        # (not the NVMe root) logged about every 80 min (7x on 2026-09-29).
        # Any other I/O error, including on nvme*, still blocks.
        lines = [line for line in faults.stdout.splitlines()
                 if line.strip() not in {'', '-- No entries --'} and not BENIGN_KERNEL.search(line)]
        if faults.returncode not in {0, 1} or lines:
            raise ValueError('New kernel fault or unavailable health evidence')
        for unit in ('oak-den.service', 'mediamtx.service'):
            if command(['systemctl', 'is-active', unit]) != 'active':
                raise ValueError('Capture unavailable')
        unit = self.unit()
        if not self.ended(unit):
            raise ValueError('Worker already active')
        failed = self.headroom_gates(self.headroom())
        if failed:
            raise ValueError(failed[0])

    @staticmethod
    def headroom():
        """Measured storage, memory and thermal headroom. Reads files only."""
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        temperatures, readable = [], True
        for file in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
            try:
                temperatures.append(int(file.read_text()) / 1000)
            except (OSError, ValueError, TypeError):
                if file.with_name('type').read_text().strip() not in {'cv0-thermal', 'cv1-thermal', 'cv2-thermal'}:
                    readable = False
        return {'storage_free_bytes': shutil.disk_usage(BASE).free,
                'memory_available_bytes': int(memory['MemAvailable'].split()[0]) * 1024,
                'max_temperature_c': max(temperatures) if temperatures else None, 'temperatures_readable': readable}

    @classmethod
    def headroom_gates(cls, measured):
        """Failed headroom gate messages, in health() order."""
        failed = []
        if measured['storage_free_bytes'] < cls.MIN_STORAGE_BYTES:
            failed.append('Storage headroom unavailable')
        if measured['memory_available_bytes'] < cls.MIN_MEMORY_BYTES:
            failed.append('Memory headroom unavailable')
        if not measured['temperatures_readable']:
            failed.append('Required temperature unavailable')
        elif measured['max_temperature_c'] is None or measured['max_temperature_c'] >= cls.MAX_CELSIUS:
            failed.append('Thermal headroom unavailable')
        return failed

    def probe(self):
        """Launch-free setup readiness read: headroom and the worker unit only.

        The same thresholds as health(), but it never starts, stops or contacts a
        worker and skips the failed-unit, kernel-journal and capture checks, so
        start() still runs the complete health() gate before any launch."""
        if socket.gethostname() != 'victoria-den-jetson':
            raise ValueError('Jetson host required')
        measured = self.headroom()
        failed = self.headroom_gates(measured)
        if not self.ended(self.unit()):
            failed.append('Worker already active')
        return {'gates': [GATES[message] for message in failed], 'measured': measured,
                'thresholds': {'storage_free_bytes': self.MIN_STORAGE_BYTES, 'memory_available_bytes': self.MIN_MEMORY_BYTES,
                               'max_temperature_c': self.MAX_CELSIUS}}

    def start(self, epoch, expires, variant='detector'):
        if variant not in {'detector','pose'}:raise ValueError('Unknown worker variant')
        # Nothing before the explicit systemd-run below starts a model.
        from lifecycle import PreflightRejected
        try:
            self.health()
        except Exception as error:
            # Preserve a content-free gate identity; the lifecycle's generic
            # rejection alone cannot distinguish a capacity issue from a read
            # failure. Never log arbitrary subprocess output or environment.
            gate = GATES.get(str(error), 'preflight_inspection_error')
            # In-memory per epoch: the client reports it on this lease's receipt.
            self.gates[epoch] = gate
            while len(self.gates) > 16:
                self.gates.pop(next(iter(self.gates)))
            import logging
            logging.getLogger(__name__).warning('Jetson preflight rejected: %s (%s)', gate, type(error).__name__)
            raise PreflightRejected('jetson_health_gate_failed') from None
        if not re.fullmatch(r'jetson-live-[a-f0-9]{32}', epoch) or not time.time() < expires <= time.time() + self.budget['runtime_seconds']:
            raise ValueError('Bounded epoch required')
        from live_detector_app import private_json
        binding = private_json(self.private / 'jetson-binding.template.json')
        profile = binding['profile']; profile['engine_epoch'] = epoch
        config = dict(schema_version=1, checkpoint_path=str(BASE/'model-cache/yolo26s/yolo26s.pt'),
                      ledger_root=str(BASE/'worker-ledgers/epochs'/epoch),
                      manifest={k: v for k, v in profile.items() if k != 'artifact_sha256'},
                      grants=[dict(site_id='victoria', cameras=['den'], token_sha256=hashlib.sha256(
                          Path(binding['token_file']).read_text().strip().encode()).hexdigest())],
                      expires_at=expires, maximum_requests=self.budget['maximum_requests'], runtime_seconds=self.budget['runtime_seconds'],
                      gate_references=dict(technical='same-checkpoint-live-pair6-20260926',
                          health_resource='fresh-managed-preflight-2GiB-swap0-1CPU-64tasks-'+str(self.budget['runtime_seconds'])+'s',
                          workload_authorization='owner-ready-to-use-Lab-20260927'))
        if variant=='pose':
            config['pose']=private_json(self.private/'pose.template.json')
            config['pose']['manifest']['engine_epoch']=epoch
            from pose_composition import profile as pose_profile
            profile=pose_profile(config['manifest'],config['pose'])
        path = self.private / (epoch + '.json')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(config, out); out.flush(); os.fsync(out.fileno())
        service = BASE/'releases'/self.release/'service'
        args = ['systemd-run', '--user', '--unit='+UNIT, '--collect', '--property=MemoryMax=2G',
                '--property=MemorySwapMax=0', '--property=CPUQuota=100%', '--property=TasksMax=64',
                '--property=RuntimeMaxSec='+str(self.budget['runtime_seconds']), '--property=Restart=no', '--property=KillMode=control-group',
                '--property=TimeoutStopSec=5', '--property=UMask=0077', '--property=NoNewPrivileges=yes',
                '--working-directory='+str(service), '--setenv=PERCEPTION_LAB_JETSON_WORKER_CONFIG='+str(path),
                '--setenv=YOLO_AUTOINSTALL=false', '--setenv=YOLO_CONFIG_DIR='+str(self.private/'yolo-config'),
                '--setenv=LD_LIBRARY_PATH='+str(BASE/'model-cache/yolo26s/native/usr/lib/aarch64-linux-gnu/libcudss/12'),
                '--setenv=PYTHONDONTWRITEBYTECODE=1', '--setenv=OMP_NUM_THREADS=1', '--setenv=OPENBLAS_NUM_THREADS=1',
                str(BASE/'yolo26-venv/bin/python'), '-m', 'uvicorn', 'jetson_detector_app:create_jetson_detector_app',
                '--factory', '--host', '192.168.1.168', '--port', '8791', '--workers', '1', '--no-access-log',
                '--ssl-keyfile', str(BASE/'private/review/server.key'), '--ssl-certfile', str(BASE/'private/review/server.pem')]
        command(args)
        invocation = self.unit().get('InvocationID')
        if not invocation:
            raise ValueError('Launch identity unavailable')
        return invocation, profile

    def inspect(self, invocation, epoch):
        unit = self.unit()
        if self.ended(unit):
            return 'ended'
        if unit.get('InvocationID') != invocation:
            return 'unrelated'
        from live_detector_app import private_json
        binding = private_json(self.private/'jetson-binding.template.json')
        config=private_json(self.private/(epoch+'.json'))
        profile={**config['manifest'],'artifact_sha256':config['manifest']['sha256']}
        if 'pose' in config:
            from pose_composition import profile as pose_profile
            profile=pose_profile(config['manifest'],config['pose'])
        reference = dict(site_id='victoria', camera_id='den', session_id='readiness', request_id='readiness',
                         body_sha256='0'*64, profile=profile)
        request = urllib.request.Request(binding['origin']+'/v1/specialist/lookup',
            data=json.dumps(reference).encode(), headers={'Authorization': 'Bearer '+Path(binding['token_file']).read_text().strip(),
                                                         'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=binding['ca_file']), timeout=2):
                return 'unrelated'
        except urllib.error.HTTPError as error:
            return 'ready' if error.code == 409 else 'unrelated'
        except (OSError, TimeoutError):
            return 'starting'

    def stop(self, invocation, epoch):
        unit = self.unit()
        if self.ended(unit):
            return
        if unit.get('InvocationID') != invocation:
            raise ValueError('Refusing to stop unrelated invocation')
        command(['systemctl', '--user', 'stop', UNIT])


class JetsonLifecycleClient:
    """Local equivalent of the remote lifecycle client; same durable receipts."""
    def __init__(self, release):
        from lifecycle import Lifecycle
        from session_budget import provisioned_budget
        self.lifecycle = Lifecycle(BASE/'private/live-detector/lifecycle.sqlite', JetsonLauncher(release),
                                   site_id='victoria', epoch_prefix='jetson-live-',lease_seconds=provisioned_budget()['lease_seconds'])

    def _gate(self, receipt):
        # The durable lease keeps 'preflight_rejected'; the receipt names the
        # exact gate this process observed for that epoch, when it knows it.
        gate = getattr(self.lifecycle.launcher, 'gates', {}).get(receipt.get('engine_epoch'))
        if receipt.get('state') == 'failed' and receipt.get('reason') == 'preflight_rejected' and gate in GATE_CODES:
            return {**receipt, 'reason': gate}
        return receipt

    def start(self, request_id, session_id, variant='detector'):
        from lifecycle import LifecycleError
        from worker_lifecycle_client import StartRejected
        self.lifecycle.tick()
        try:
            return self._gate(self.lifecycle.start(request_id, session_id,variant))
        except LifecycleError as error:
            if error.reason in {'worker_busy', 'ledger_capacity'}:
                raise StartRejected(error.reason) from None
            raise

    def status(self, request_id):
        return self._gate(self.lifecycle.status(request_id))

    def probe(self):
        """Readiness reasons from the launch-free host read; [] when every gate passes."""
        value = self.lifecycle.launcher.probe()
        measured, limits = value['measured'], value['thresholds']
        detail = {'preflight_memory': ('jetson_memory_below_gate', 'memory_available_bytes'),
                  'preflight_storage': ('jetson_storage_below_gate', 'storage_free_bytes'),
                  'preflight_thermal': ('jetson_thermal_above_gate', 'max_temperature_c'),
                  'preflight_thermal_read': ('jetson_thermal_unreadable', None),
                  'preflight_worker_active': ('jetson_worker_active', None)}
        reasons = []
        for gate in value['gates']:
            code, key = detail[gate]
            if key and measured[key] is None:
                code, key = 'jetson_thermal_unreadable', None
            reasons.append({'code': code, **({'measured': measured[key], 'threshold': limits[key]} if key else {})})
        return reasons

    def stop(self, request_id, epoch):
        return self._gate(self.lifecycle.stop(request_id, epoch))

    def close(self):
        return self.lifecycle.close()
