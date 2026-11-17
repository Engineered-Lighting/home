"""Fixed LA camera-pilot service. No model load, restart, or client migration."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time

from . import backend_timing
from .camera_pilot import CameraPilot, BeforeDispatchRejected, REPLY_WAIT_SECONDS
from .gateway import CredentialBinding
from .ingress import IngressPolicy, PrivateTLSIngress
from .model_pin import PIN_NAME, EngineIdentityMismatch, load_engine, load_read_only_engine
from .model_worker import ModelWorker, WorkerNotSent
from .openai_backend import PROMPT, _constant, _object
from .pilot_health import HealthCache
from .pilot_runtime import RUNTIME_NAME, RuntimeOptions, load_runtime_options
from .service_config import protected_file

ROOT = Path('/home/marcelo-lima/home-camera-pilot')
# Frozen ledger label, not the engine identity. It is part of the pilot_binding
# identity (camera_pilot.py:50-54) and of every camera observation, so changing
# it closes admission until the ledger is reconciled: never edit it on a vLLM
# restart or re-pin. The served incarnation (container, image digest,
# StartedAt) lives only in private/model-pin.json (model_pin.py).
MODEL_REVISION = 'qwen3-vl-30b-f579e204-20260920'
# The model_pin.PinnedEngine main() loads from private/model-pin.json.
ENGINE = None
# Warm path (speed plan S1.1), installed by main() from the optional
# private/pilot-runtime.json. Without HEALTH and CAMERA_WORKER (a bare import)
# every call checks health afresh and execs a fresh interpreter, as before;
# the kernel-scan interval and journal mode always follow OPTIONS.
OPTIONS = RuntimeOptions()
HEALTH = None          # pilot_health.HealthCache over _check_health
CAMERA_WORKER = None   # model_worker.ModelWorker for camera chat ("long_lived")
# :9446 ingress (speed plan S1.5). Victoria's clients reconnect after 15 s idle.
INGRESS_CONNECTIONS = 8
INGRESS_IDLE_SECONDS = 20
BINDING = CredentialBinding('victoria','den-camera-pilot','victoria-camera-pilot',
                            {'den_oak':'den'},'camera-pilot-v1',True)
INFER = '''
import sys,json,http.client
value=json.load(sys.stdin)
connection=http.client.HTTPConnection('127.0.0.1',8000,timeout=value['timeout'])
try:
 connection.request('POST','/v1/chat/completions',body=json.dumps(value['body']),headers={'Content-Type':'application/json'})
 response=connection.getresponse()
 raw=response.read(65537)
 if response.status!=200 or len(raw)>65536:raise ValueError('model response unavailable')
 result=json.loads(raw)
 print(json.dumps(result))
finally:connection.close()
'''


# The kernel-fault scan (_kernel_scan) starts at the latest of three points:
# - _KERNEL_BASELINE, the floor when there is no usable checkpoint. It is one
#   second past the owner-inspected 2026-11-11 21:37:41 UTC memcg OOM. That OOM
#   was this service's own 256 MiB cgroup, filled with orphaned root
#   `journalctl` scans that timed out under `sudo -n`. Host release
#   release-health-2ddb518730876293-kb20261111 carries the same value. It no
#   longer needs to move with a release.
# - KERNEL_CHECKPOINT, the end of the last clean scan, less a 5 s overlap. Only
#   a clean scan writes it, so a restart or a long idle resumes there.
# - KERNEL_RECEIPT plus one second. This is the owner's record of inspected
#   faults; the pilot only reads it:
#       {"version": 1, "inspected_through": "2026-11-11 21:37:41 UTC",
#        "note": "what was inspected and why it is not a host fault"}
#   It is read on every scan, so no release or restart is needed. A later fault
#   still refuses. A receipt that is invalid, or later than now, refuses too.
_KERNEL_BASELINE = '2026-11-11 21:37:42 UTC'
_KERNEL_FAULTS = 'invoked oom-killer|Out of memory: Killed|kernel panic|soft lockup|hard LOCKUP|Hardware Error|I/O error|segfault|Machine check|thermal throttling'
KERNEL_STATE = ROOT/'private'
KERNEL_CHECKPOINT = 'kernel-fault-checkpoint.json'
KERNEL_RECEIPT = 'kernel-fault-receipt.json'
_KERNEL_TIME = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC')
_KERNEL_OVERLAP = 5
_KERNEL_FILE_BYTES = 4096
# until: the start of the last clean scan in this process (the 30 s rule).
# retry_at: monotonic time before which no scan starts after a failed one.
# session: a timed-out scan whose session was not yet seen empty.
_KERNEL_CLEAN = {'until': None, 'retry_at': None, 'session': None}
_KERNEL_LOCK = threading.Lock()
# Each journalctl reads at most _KERNEL_CHUNK_SECONDS of journal, and a clean
# chunk is recorded at once. A stale window therefore converges over a few
# scans instead of timing out forever. On 2026-11-11 an idle process's window
# reached back 17 days and never fit the 15 s bound. A scan may run chunks for
# up to 180 s if it is the first in its process (main() runs it before
# serving), and for up to 15 s after that.
_KERNEL_BASELINE_TIMEOUT = 180
_KERNEL_SCAN_TIMEOUT = 15
_KERNEL_CHUNK_SECONDS = 6*3600
# After a failed scan (a fault, a timeout or missing evidence), no scan starts
# for this long. Before, every request relaunched one at once.
_KERNEL_RETRY_SECONDS = 30
# How long to wait after SIGTERM, and again after SIGKILL, for a timed-out scan.
_KERNEL_KILL_GRACE = 2
HWMON = Path('/sys/class/hwmon')
# Exact LA system units outside the Den inference path. Keep in sync with
# den_ops.monitor; neither classification clears failures or grants HA actions.
DEN_NON_DEPENDENCY_UNITS = {
    "ha-health.service": "LA HAOS availability; Den uses Victoria occupancy and direct model transport",
    "ha-reachable.service": "LA HAOS availability; no Den model or camera dependency",
    "haos-precious-sync.service": "LA HAOS backup; no Den execution dependency",
    "home-agent-bff-egress-verify.service": "Home BFF OAuth boundary; Den model transport does not traverse BFF",
    "home-agent-lighting-egress-verify.service": "Core-to-HA physical lighting boundary; Den publishes virtual intent only",
    "fwupd-refresh.service": "Firmware metadata refresh; hardware faults remain guarded by kernel and sensors"
}
_NON_DEPENDENCY_FAILED = ()

def _engine():
    if ENGINE is None:
        raise ValueError('model pin not loaded')
    return ENGINE


def _checked_engine():
    """The engine a health check inspects: main()'s ENGINE, or, without it (a
    bare import: a deploy or recovery helper's standalone model_health()
    preflight), private/model-pin.json read afresh and read-only. Its re-pin
    rule is off, so a changed incarnation fails the preflight as it did
    before model-pin.json, and the file is never rewritten; only the running
    pilot re-pins."""
    if ENGINE is not None:
        return ENGINE
    return load_read_only_engine(protected_file(ROOT/'private'/PIN_NAME,enforce_linux=True))


def model_health():
    """The fail-closed preflight. Under main(), a passing verdict at most
    OPTIONS.health_max_age_seconds old, refreshed by the request that finds
    it older (concurrent requests share that one refresh); otherwise a fresh
    check on every call. Without main() it is also the standalone LA
    preflight, against model-pin.json read-only (_checked_engine)."""
    if HEALTH is None:
        _check_health({})
    else:
        HEALTH.require()


def fresh_health():
    """A check begun after this call (shared with concurrent callers), never a
    cached one: run before a model worker starts, so a worker restarted after
    a vLLM restart never execs into an incarnation no check has seen."""
    if HEALTH is None:
        _check_health({})
    else:
        HEALTH.require(fresh=True)


def health_verdict():
    """The cached verdict for the live consumer's thermal ladder (S1.4):
    refreshed like model_health when older than its bound, then returned
    passed or failed (the readings a failed check took still count). None
    without the cache. Camera chat never reads it; its > 80 C guard is
    _host_health's, unchanged."""
    if HEALTH is None:
        return None
    try:
        HEALTH.require()
    except Exception:
        pass
    return HEALTH.latest()


def _check_health(readings):
    # By name: a recreated container is then seen (and refused, or re-pinned
    # under the owner's rule) rather than failing as an unknown id.
    engine = _checked_engine()
    result = subprocess.run(['docker','inspect','--type','container',engine.container_name],
                            capture_output=True,text=True,timeout=3,check=True)
    engine.check(json.loads(result.stdout)[0], lambda: _host_health(readings))


def _host_health(readings=None):
    """Every check besides the pinned incarnation; a re-pin needs all of them.
    readings receives gpu_c and, when the host exposes it, tctl_c."""
    readings = {} if readings is None else readings
    failed = subprocess.run(['systemctl','list-units','--failed','--plain','--no-legend'],
                            capture_output=True,text=True,timeout=2,check=True).stdout
    global _NON_DEPENDENCY_FAILED
    names = tuple(sorted(line.split()[0] for line in failed.splitlines() if line.strip()))
    unrelated = tuple(name for name in names if name in DEN_NON_DEPENDENCY_UNITS)
    if unrelated != _NON_DEPENDENCY_FAILED:
        print('Den inference non-dependency failures: '+(', '.join(unrelated) or 'recovered'),
              file=sys.stderr, flush=True)
        _NON_DEPENDENCY_FAILED = unrelated
    if any(name not in DEN_NON_DEPENDENCY_UNITS and name not in {'observer-health.service','observer-recover.service','home-agent-preference-offhost-20260928.service','home-agent-preference-offhost-20260928-r2.service'}
           for name in names):
        raise ValueError('new failed system unit')
    gpu = subprocess.run(['nvidia-smi','--query-gpu=memory.free,temperature.gpu','--format=csv,noheader,nounits'],
                         capture_output=True,text=True,timeout=2,check=True).stdout
    free, temperature = map(int,gpu.strip().split(','))
    readings['gpu_c'] = temperature
    tctl = _tctl()
    if tctl is not None:
        readings['tctl_c'] = tctl
    memory = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    if (free < 16384 or temperature > 80 or shutil.disk_usage(ROOT).free < 50*1024**3
            or int(memory['MemAvailable'].split()[0])*1024 < 8*1024**3):
        raise ValueError('pilot resource guard closed')
    _kernel_scan()


def _tctl():
    """AMD k10temp Tctl in degrees C from sysfs, or None. A reading for the
    thermal ladder only, never a check: absent or unreadable is not a fault."""
    try:
        for sensor in sorted(HWMON.iterdir()):
            if (sensor/'name').read_text().strip() != 'k10temp':
                continue
            for label in sorted(sensor.glob('temp*_label')):
                if label.read_text().strip() == 'Tctl':
                    return int((sensor/label.name.replace('_label','_input')).read_text().strip())/1000
    except (OSError, ValueError):
        return None
    return None


def _kernel_scan():
    """Refuse unless the kernel journal is clean from the window start
    (_window_start) to now. A fault anywhere in the scanned range closes
    admission and is never recorded as clean. A clean scan that began less
    than OPTIONS.kernel_scan_seconds ago stands (S1.1: about every 30 s while
    requests flow, not once per request). After a failed scan, checks refuse
    without scanning for _KERNEL_RETRY_SECONDS. While a timed-out scan's
    session still has a process, no new scan starts."""
    with _KERNEL_LOCK:
        started = time.time()
        last = _KERNEL_CLEAN['until']
        if last is not None and 0 <= started - last < OPTIONS.kernel_scan_seconds:
            return
        retry_at = _KERNEL_CLEAN['retry_at']
        if retry_at is not None and time.monotonic() < retry_at:
            raise ValueError('kernel fault scan failed; retry pending')
        try:
            if _KERNEL_CLEAN['session'] is not None:
                if _session_alive(_KERNEL_CLEAN['session']):
                    raise ValueError('timed-out kernel fault scan still running')
                _KERNEL_CLEAN['session'] = None
            since = _window_start(started)
            clean, chunks = _scan_window(since, _KERNEL_BASELINE_TIMEOUT if last is None else _KERNEL_SCAN_TIMEOUT)
        except Exception:
            _KERNEL_CLEAN['retry_at'] = time.monotonic() + _KERNEL_RETRY_SECONDS
            raise
        _KERNEL_CLEAN['retry_at'] = None
        if last is None or chunks > 1:
            print('camera pilot kernel scan since=@%d chunks=%d seconds=%.1f' % (since, chunks, time.time()-started),
                  file=sys.stderr, flush=True)
        _KERNEL_CLEAN['until'] = clean


def _scan_window(since, budget):
    """Scan the journal from since to now in chunks of at most
    _KERNEL_CHUNK_SECONDS, recording each clean chunk as soon as it is clean.
    Returns (clean_until, chunks) and raises on the first fault, on missing
    evidence, or when budget seconds run out."""
    deadline = time.monotonic() + budget
    adm = OPTIONS.kernel_journal == 'adm'
    chunks = 0
    while True:
        mark = time.time()
        if since > mark + 1:
            raise ValueError('kernel fault scan window starts in the future')
        until = since + _KERNEL_CHUNK_SECONDS if mark - since > _KERNEL_CHUNK_SECONDS else None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('kernel fault scan incomplete')
        command = ['journalctl','-k','--since','@%d' % since]
        if until is not None:
            command += ['--until','@%d' % until]
        command += ['--no-pager','--grep',_KERNEL_FAULTS]
        code, stdout, stderr = _journal(command if adm else ['sudo','-n']+command, remaining)
        chunks += 1
        # "adm" reads the kernel journal as the service user through group adm
        # (as vjepa_launcher does); then anything on stderr, such as a missing
        # group grant, is no proof of a clean journal. Under sudo, sudo's own
        # refusal ("sudo: a password is required", exit 1, nothing on stdout)
        # is no proof either. Its one warning that still runs the command is
        # an /etc/hosts gap.
        if (code != 1 or stdout.strip() not in ('','-- No entries --')
                or (adm and stderr.strip())
                or any(line.startswith('sudo:') and not line.startswith('sudo: unable to resolve host')
                       for line in stderr.splitlines())):
            raise ValueError('new kernel fault or health evidence unavailable')
        clean = mark if until is None else until
        _record_clean(clean)
        if until is None:
            return clean, chunks
        since = until - _KERNEL_OVERLAP


def _journal(command, timeout):
    """(returncode, stdout, stderr) of one journalctl run in a session of its
    own. If it runs past timeout, the whole session is ended and
    TimeoutExpired is raised."""
    scan = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = scan.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if not _end_session(scan):
            _KERNEL_CLEAN['session'] = scan.pid
        raise
    return scan.returncode, stdout, stderr


def _end_session(scan):
    """End a timed-out scan and its session. Returns True when no process of
    the session is left.

    Under `sudo -n`, this user cannot signal the root journalctl. Python's own
    timeout SIGKILLs sudo alone, and SIGKILL cannot be relayed. On 2026-11-11
    that orphaned 76 root scans in this unit's 256 MiB cgroup. sudo does relay
    SIGTERM sent from outside its process group, so SIGTERM goes first. Then
    SIGKILL ends whatever this user owns."""
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(scan.pid, number)
        except OSError:
            pass
        try:
            scan.communicate(timeout=_KERNEL_KILL_GRACE)
            break
        except subprocess.TimeoutExpired:
            continue
    else:
        # sudo is gone, but a process it could not end still holds the pipes.
        for stream in (scan.stdout, scan.stderr):
            stream.close()
        try:
            scan.wait(timeout=_KERNEL_KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass
    return not _session_alive(scan.pid)


def _session_alive(session):
    """Whether any process is still running in session (from /proc). If /proc
    cannot be read, the session counts as alive. A zombie doesn't count: it
    holds no memory and only waits for its new parent to reap it."""
    try:
        names = os.listdir('/proc')
    except OSError:
        return True
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % name) as source:
                fields = source.read().rsplit(')', 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > 3 and fields[3] == str(session) and fields[0] not in ('Z','X'):   # state ppid pgrp session
            return True
    return False


def _utc(text):
    """Epoch seconds of journalctl's own 'YYYY-MM-DD HH:MM:SS UTC' form."""
    if not isinstance(text, str) or not _KERNEL_TIME.fullmatch(text):
        raise ValueError('expected YYYY-MM-DD HH:MM:SS UTC')
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc).timestamp()


def _kernel_file(name):
    """The exact JSON object in KERNEL_STATE/name, or None when absent."""
    path = KERNEL_STATE/name
    if not os.path.lexists(path):
        return None
    with protected_file(path, enforce_linux=True).open('rb') as source:
        raw = source.read(_KERNEL_FILE_BYTES + 1)
    if len(raw) > _KERNEL_FILE_BYTES:
        raise ValueError('kernel fault file exceeds limit')
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('unsupported kernel fault file')
    return value


def _window_start(now):
    """Where a scan starts: the latest of the floor, one second past the
    owner's receipt, and the checkpoint less the overlap. Without a usable
    checkpoint the scan starts earlier, never later."""
    start = _utc(_KERNEL_BASELINE)
    try:
        receipt = _kernel_file(KERNEL_RECEIPT)
        if receipt is not None:
            if (set(receipt) != {'version','inspected_through','note'} or not isinstance(receipt['note'], str)
                    or not receipt['note'].strip() or len(receipt['note']) > 1000):
                raise ValueError('kernel fault receipt fields')
            through = _utc(receipt['inspected_through'])
            if through > now:
                raise ValueError('kernel fault receipt is later than now')
            start = max(start, through + 1)
    except Exception as error:
        raise ValueError('kernel fault receipt invalid') from error
    try:
        checkpoint = _kernel_file(KERNEL_CHECKPOINT)
        if checkpoint is not None:
            until = checkpoint['clean_until']
            if (set(checkpoint) != {'version','clean_until'} or type(until) not in (int, float)
                    or not math.isfinite(until) or until > now):
                raise ValueError('kernel fault checkpoint fields')
            start = max(start, until - _KERNEL_OVERLAP)
    except Exception as error:
        print('camera pilot kernel checkpoint unusable: '+type(error).__name__, file=sys.stderr, flush=True)
    return int(start)


def _record_clean(until):
    """Atomically record the end of a clean scan. Only a clean scan calls it."""
    path = KERNEL_STATE/KERNEL_CHECKPOINT
    temporary = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump({'version': 1, 'clean_until': until}, target)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def infer(request):
    # This exception boundary ends BEFORE starting Docker/model transport.
    # Failures after that boundary remain indeterminate, never auto-retried.
    try:
        with backend_timing.stage('health'):
            model_health()
        # Read after the health check: an accepted re-pin moves camera chat
        # to the new container.
        container = _engine().container_id
        remaining = request.request_deadline - time.time()
        if remaining <= .5:
            raise ValueError('request deadline exhausted')
    except Exception as error:
        print('camera pilot preflight rejected: '+type(error).__name__, file=sys.stderr, flush=True)
        raise BeforeDispatchRejected('preflight_rejected') from error
    body = dict(model='qwen3-vl-30b',max_tokens=request.max_output_tokens,stream=False,temperature=0,
        messages=[dict(role='user',content=[dict(type='text',text=PROMPT),
        dict(type='image_url',image_url={'url':'data:image/jpeg;base64,'+base64.b64encode(request.image).decode()})])])
    # Same fixed container path as the supervised canary; no published model port,
    # shell, dynamic command, retry, image persistence or model lifecycle action.
    with backend_timing.stage('exec'):
        if CAMERA_WORKER is None:
            output = subprocess.run(['docker','exec','-i',container,'python3','-I','-S','-c',INFER],
                # The observation deadline does not release execution accounting. Keep
                # one charged request awaiting a bounded completion after UI expiry.
                input=json.dumps({'body':body,'timeout':60}),capture_output=True,text=True,
                timeout=65,check=True).stdout
        else:
            output = _worker_infer(container, body)
    if len(output.encode()) > 65536:
        raise ValueError('model result too large')
    value = json.loads(output)
    choices, usage = value.get('choices'), value.get('usage')
    if (not isinstance(choices,list) or len(choices)!=1 or choices[0].get('finish_reason') not in ('stop','length')
            or not isinstance(usage,dict) or type(usage.get('completion_tokens')) is not int
            or not 0 <= usage['completion_tokens'] <= request.max_output_tokens
            or value.get('model') != 'qwen3-vl-30b'):
        raise ValueError('unproven model completion')
    return choices[0]['message']['content']


def _worker_infer(container, body):
    """The same request through the long-lived camera worker (model_worker.py):
    the same body, 60 s model timeout and 65 s bound. Only a frame proven never
    written is a rejection before dispatch; every failure after it stays
    indeterminate, as a failed or timed-out `docker exec` was."""
    try:
        return CAMERA_WORKER.chat(container, json.dumps(body), timeout=60, limit=65536, seconds=65,
                                  before_start=fresh_health)
    except WorkerNotSent as error:
        print('camera pilot preflight rejected: '+type(error).__name__, file=sys.stderr, flush=True)
        raise BeforeDispatchRejected('preflight_rejected') from error


def _refuse_start(reason):
    print('camera pilot refusing to start: '+reason, file=sys.stderr, flush=True)


def main():
    global ENGINE, OPTIONS, HEALTH, CAMERA_WORKER
    if socket.gethostname() != 'EngineeredLightingServer1':
        raise ValueError('pinned LA host required')
    os.umask(0o077)
    private = ROOT/'private'
    def file(name, secret=False):
        return str(protected_file(private/name,secret=secret,enforce_linux=True))
    try:
        ENGINE = load_engine(file(PIN_NAME))
    except Exception as error:
        _refuse_start(PIN_NAME+' unavailable: '+type(error).__name__)
        raise
    # Optional: absent means the S1.1 defaults; present but invalid refuses.
    try:
        OPTIONS = (load_runtime_options(file(RUNTIME_NAME)) if os.path.lexists(private/RUNTIME_NAME)
                   else RuntimeOptions())
    except Exception as error:
        _refuse_start(RUNTIME_NAME+' unavailable: '+type(error).__name__)
        raise
    HEALTH = HealthCache(_check_health, max_age_seconds=OPTIONS.health_max_age_seconds)
    long_lived = OPTIONS.model_worker == 'long_lived'
    # Started by the first request that needs one, then kept; separate per
    # consumer so a live hang never blocks camera chat.
    CAMERA_WORKER = ModelWorker('camera') if long_lived else None
    live_worker = ModelWorker('live') if long_lived else None
    # A different StartedAt or container fails here unless the pin's re-pin
    # rule accepts it; the live identity cross-check below uses the pin as
    # provisioned (with the epochs earlier re-pins replaced), so an accepted
    # re-pin keeps camera chat and closes live, now and after later restarts,
    # until live-scene.json names the new epoch.
    model_health()
    certificate = Path(file('client.pem')).read_text()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    pilot = CameraPilot(private/'admission.sqlite',binding=BINDING,backend=infer,model_revision=MODEL_REVISION,
                        reply_wait_seconds=REPLY_WAIT_SECONDS)
    identities = {fingerprint:BINDING}
    live = None
    # Optional Lab live-scene consumer. Without private/live-scene.json nothing
    # below is imported; absent, disabled ("enabled": false) or invalid
    # provisioning composes camera chat exactly as before. Enabled provisioning
    # that names another engine than model-pin.json (another artifact, or,
    # while the re-pin lineage is not full, an epoch no accepted re-pin
    # replaced) refuses the start.
    if os.path.lexists(private/'live-scene.json'):
        from .lab_live_pilot import compose_pilot_live, timed_backend
        try:
            live = compose_pilot_live(private,pilot,identities,container=ENGINE.container_id,
                                      model_health=model_health,engine=ENGINE,worker=live_worker,
                                      fresh_health=fresh_health,runtime=OPTIONS,verdict=health_verdict)
        except EngineIdentityMismatch as error:
            _refuse_start(str(error))
            pilot.close()
            raise
        if live is not None:
            pilot.backend = timed_backend(pilot.backend,'camera pilot')
    # Speed plan S1.5: 8 connection slots (inside the S1.1 task budget), each
    # a kept-alive HTTP/1.1 connection of at most 100 requests and 20 s idle;
    # ingress_requests_per_connection 1 in pilot-runtime.json is the rollback.
    policy = IngressPolicy(INGRESS_CONNECTIONS,2,3,2,max_requests_per_connection=OPTIONS.ingress_requests_per_connection,
                           idle_timeout_seconds=INGRESS_IDLE_SECONDS)
    options = dict(certificate=file('server.pem'),private_key=file('server.key',True),client_ca=file('ca.pem'),
                   identities=identities,policy=policy)
    if live is None:
        server = PrivateTLSIngress(('192.168.0.100',9446),pilot,**options)
    else:
        from .lab_live_pilot import PilotLiveIngress
        server = PilotLiveIngress(('192.168.0.100',9446),pilot,live,**options)
    def stop(*_):
        pilot.close_admission()
        if live is not None:
            live.close_admission()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        server.wait_handlers(5)
        if live is not None:
            live.close()
        pilot.close()
        for worker in (CAMERA_WORKER, live_worker):
            if worker is not None:
                worker.close()


if __name__ == '__main__':
    main()
