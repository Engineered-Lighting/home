"""Explicitly provisioned refresh composition preserving the capture application.

No provisioned gateway means no inference. Configuration cannot choose Python
modules, camera URLs, model URLs or execute commands.
"""
import json
import os
from pathlib import Path
import re
import stat


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate refresh configuration field')
        value[key] = item
    return value


def create_refresh_app(*, capture_factory=None):
    from chrony_preview_client import ChronyPreviewClient, commissioned_runtime
    commissioned_runtime()
    from home_shared.service_config import protected_file
    from home_shared.gateway_client import GatewayClient, GatewayClientConfig
    from home_shared.submissions import SubmissionJournal
    from home_shared.caption_publication import CaptionPublication
    from home_shared.on_demand import OnDemandRefresh
    from refresh_lifecycle import RefreshLifecycle
    from capture_app import create_capture_app

    path = protected_file(os.environ['PERCEPTION_LAB_REFRESH_CONFIG'], enforce_linux=True)
    raw = path.read_bytes()
    if len(raw) > 16384:
        raise ValueError('refresh configuration too large')
    config = json.loads(raw, object_pairs_hook=_pairs)
    fields = {'version','gateway_hostname','gateway_port','device_id','configuration_revision',
              'model_revision','gateway_subject','state_directory','max_records','free_space_floor_bytes'}
    if not isinstance(config, dict) or set(config) != fields or type(config['version']) is not int or config['version'] != 1:
        raise ValueError('unsupported refresh configuration')
    for key in ('device_id','configuration_revision','model_revision','gateway_subject'):
        if not isinstance(config[key], str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}', config[key]) is None:
            raise ValueError('invalid refresh binding')
    if (type(config['max_records']) is not int or not 1 <= config['max_records'] <= 1000000
            or type(config['free_space_floor_bytes']) is not int or config['free_space_floor_bytes'] < 0):
        raise ValueError('explicit refresh storage limits required')
    state = Path(config['state_directory'])
    if not state.is_absolute() or '..' in state.parts:
        raise ValueError('absolute refresh state directory required')
    for parent in (*reversed(state.parents), state):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022):
            raise ValueError('protected refresh state directory required')
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise ValueError('owner-only refresh state required')
    for suffix in ('','-wal','-shm','.lock'):
        item=state/('refresh-submissions.sqlite'+suffix)
        if item.exists() or item.is_symlink():
            info=item.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('protected refresh journal required')
    def file(name, secret=False):
        return protected_file(path.parent/name, secret=secret, enforce_linux=True)
    token = file('gateway-token.txt', True).read_bytes()
    if len(token) > 4098:
        raise ValueError('gateway credential too large')
    gateway_config = GatewayClientConfig(hostname=config['gateway_hostname'],port=config['gateway_port'],
        ca_file=file('gateway-ca.pem'),bearer_token=token.decode().rstrip('\r\n'),device_id=config['device_id'],
        cameras={'den_oak':'den'},configuration_revision=config['configuration_revision'],
        client_certificate=file('gateway-client.pem'),client_key=file('gateway-client.key',True),allow_interactive=True)
    caption = state/'shared-caption.json'
    if Path(os.environ.get('PERCEPTION_LAB_SHARED_CAPTION_FILE','')) != caption:
        raise ValueError('Home reader and refresh publication paths must match')

    def factory():
        gateway = GatewayClient(gateway_config)
        journal = SubmissionJournal(state/'refresh-submissions.sqlite',device_id=config['device_id'],
            gateway_identity=gateway.gateway_identity,max_records=config['max_records'],
            free_space_floor_bytes=config['free_space_floor_bytes'])
        try:
            return OnDemandRefresh(camera=ChronyPreviewClient(),gateway=gateway,journal=journal,
                caption_writer=CaptionPublication(caption,model_revision=config['model_revision']),
                model_revision=config['model_revision'],configuration_revision=config['configuration_revision'])
        except Exception:
            journal.close()
            raise
    # Only a trusted Python composition root may select the application factory.
    return (capture_factory or create_capture_app)(refresh_backend=RefreshLifecycle(factory),refresh_gateway_subject=config['gateway_subject'])
