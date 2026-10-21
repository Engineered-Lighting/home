"""Fixed LA camera-pilot service. No model load, restart, or client migration."""
import base64
import hashlib
import json
import os
from pathlib import Path
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
from .openai_backend import PROMPT
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


# Moved past the inspected 2026-10-20 01:35 UTC download-container OOM (the
# Qwen3.6 model fetch, not a host fault); host release
# release-speed-82aef18471685737-kb20261020 carries the same value.
_KERNEL_BASELINE = '2026-10-20 01:36:00 UTC'
_KERNEL_FAULTS = 'invoked oom-killer|Out of memory: Killed|kernel panic|soft lockup|hard LOCKUP|Hardware Error|I/O error|segfault|Machine check|thermal throttling'
_KERNEL_CLEAN = {'until': None}
_KERNEL_LOCK = threading.Lock()
# The baseline scan reads the whole window once per process (main() runs it
# before serving); the journal only grows, and it took 36.5 s on 2026-10-09.
# Incremental scans read seconds of journal and keep the short bound.
_KERNEL_BASELINE_TIMEOUT = 180
_KERNEL_SCAN_TIMEOUT = 15
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
    # Incremental: the full window from the fixed baseline once per process,
    # then only since the last clean scan (5 s overlap). A fault anywhere in
    # the scanned range still closes admission; the journal only grows. A
    # clean scan that began less than OPTIONS.kernel_scan_seconds ago stands
    # (S1.1: about every 30 s while requests flow, not once per request); a
    # fault is never cached as clean, so every later check rescans.
    with _KERNEL_LOCK:
        started = time.time()
        last = _KERNEL_CLEAN['until']
        if last is not None and 0 <= started - last < OPTIONS.kernel_scan_seconds:
            return
        since = _KERNEL_BASELINE if last is None else '@%d' % int(last - 5)
        command = ['journalctl','-k','--since',since,'--no-pager','--grep',_KERNEL_FAULTS]
        adm = OPTIONS.kernel_journal == 'adm'
        # "adm" reads the kernel journal as the service user through group adm
        # (as vjepa_launcher does); then anything on stderr, such as a missing
        # group grant, is no proof of a clean journal.
        faults = subprocess.run(command if adm else ['sudo','-n']+command,
            capture_output=True,text=True,
            timeout=_KERNEL_BASELINE_TIMEOUT if last is None else _KERNEL_SCAN_TIMEOUT)
        if (faults.returncode != 1 or faults.stdout.strip() not in ('','-- No entries --')
                or (adm and faults.stderr.strip())):
            raise ValueError('new kernel fault or health evidence unavailable')
        if last is None:
            print('camera pilot kernel baseline scan seconds=%.1f' % (time.time()-started),
                file=sys.stderr, flush=True)
        _KERNEL_CLEAN['until'] = started


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
