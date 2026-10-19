"""Opt-in recording-only ASGI factory. Importing this module performs no I/O.

Provisioning files are operator-owned inputs, never browser-selected paths.
References document reviewed gates; their presence does not prove admission.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def _json(path):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate configuration field')
            result[key]=value
        return result
    try:
        with Path(path).open('rb') as source:raw=source.read(65537)
        if len(raw)>65536:raise ValueError()
        return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError,ValueError,TypeError):
        raise ValueError('capture configuration unreadable or invalid') from None


def load_provisioning(path):
    """Validate without opening a store, contacting a camera or loading a model."""
    from config import Settings
    from recorder import StreamSource
    from auth import Authorizer
    from reasoning import finite
    raw=_json(path)
    encoded_only=isinstance(raw,dict) and raw.get('capture_mode')=='encoded_only'
    fields={'schema_version','settings_file','source','gate_references'}
    fields |= {'capture_mode'} if encoded_only else {'camera_credential_file','receiver_clock'}
    if not isinstance(raw,dict) or set(raw)!=fields or type(raw['schema_version']) is not int or raw['schema_version']!=1:
        raise ValueError('capture provisioning schema 1 required')
    for key in (('settings_file',) if encoded_only else ('settings_file','camera_credential_file')):
        if not isinstance(raw[key],str) or not Path(raw[key]).is_absolute():
            raise ValueError('absolute provisioned file paths required')
    settings_raw=_json(raw['settings_file'])
    if not isinstance(settings_raw,dict):raise ValueError('settings object required')
    settings=Settings(**settings_raw)
    if settings.site_id!='victoria' or settings.developer_fixture_mode or not Path(settings.storage_root).is_absolute():
        raise ValueError('Victoria non-fixture absolute storage required')
    if len(settings.cameras)!=1 or not settings.token_grants:
        raise ValueError('one camera and authenticated grants required')
    Authorizer(settings.site_id,settings.token_grants)
    gates=raw['gate_references']
    if not isinstance(gates,dict) or set(gates)!={'technical','health_resource','workload_authorization'} or any(
            not isinstance(v,str) or not 1<=len(v)<=256 or any(c in v for c in '\r\n\0') for v in gates.values()):
        raise ValueError('three independent reviewed gate references required')
    value=raw['source']
    if not isinstance(value,dict) or set(value)-{'id','camera_id','url','codec','codec_validation_id','segment_seconds','staging_reserve_bytes'}:
        raise ValueError('fixed encoded source fields required')
    try:
        url=urlsplit(value['url'])
        if url.scheme!='rtsp' or url.hostname!='127.0.0.1' or url.port!=8554 or not url.path or url.query or url.fragment:
            raise ValueError()
        source=StreamSource(**value,enabled=True,admitted=True,ffmpeg='/usr/bin/ffmpeg')
    except (ValueError,KeyError,TypeError):
        raise ValueError('registered loopback RTSP source required') from None
    if source.camera_id!=settings.cameras[0].get('id') or not source.codec or not source.codec_validation_id:
        raise ValueError('camera binding and validated codec required')
    if encoded_only:
        if settings.cameras[0].get('modalities', ['rgb']) != ['rgb']:
            raise ValueError('encoded-only camera exposes RGB recording only')
        return settings,source,None,None,dict(gates)
    clock=raw['receiver_clock']
    if not isinstance(clock,dict) or set(clock)!={'validation_id','uncertainty_seconds'} or not isinstance(clock['validation_id'],str) or not 1<=len(clock['validation_id'])<=256 or not finite(clock['uncertainty_seconds']) or not 0<=clock['uncertainty_seconds']<=1:
        raise ValueError('validated receiver clock required')
    credential_path=Path(raw['camera_credential_file'])
    def credential():
        try:
            with credential_path.open('rb') as source:token=source.read(514).decode('ascii').rstrip('\n')
            if not 32<=len(token)<=512 or any(c in token for c in '\r\n\0'):raise ValueError()
            return token
        except (OSError,ValueError):raise ValueError('camera credential unavailable') from None
    credential()  # Check availability, never log or include it in capabilities.
    return settings,source,clock,credential,dict(gates)


def create_capture_app(*, refresh_backend=None, refresh_gateway_subject=None):
    path=os.environ.get('PERCEPTION_LAB_CAPTURE_CONFIG')
    if not path:raise ValueError('PERCEPTION_LAB_CAPTURE_CONFIG required')
    settings,source,clock,credential,_gates=load_provisioning(path)
    from composition import compose_runtime
    from app import create_app
    previews={}
    planes={}
    videos={}
    preview_env=os.environ.get('PERCEPTION_LAB_CAMERA_ENV')
    if preview_env:
        from preview_app import camera_token
        from preview_client import LocalPreviewClient
        from live_planes import LivePlanes
        planes[source.camera_id]=LivePlanes(LocalPreviewClient(lambda:camera_token(preview_env)),settings.site_id,source.camera_id)
    if os.environ.get('PERCEPTION_LAB_DISPLAY_RTSP')=='1':
        from rtsp_preview import RTSPPreview
        from live_video import LiveVideo
        previews[source.camera_id]=RTSPPreview(source,settings.site_id)
        videos[source.camera_id]=LiveVideo(source,settings.site_id)
    elif preview_env:
        from preview_app import camera_token
        from preview_client import LocalPreviewClient
        from live_preview import LivePreview
        camera_token(preview_env)
        previews[source.camera_id]=LivePreview(LocalPreviewClient(lambda:camera_token(preview_env)),
            site_id=settings.site_id,camera_id=source.camera_id)
    return create_app(settings,runtime_factory=lambda store:compose_runtime(store,
        sources=[source],camera_credential=credential,receiver_clock=clock,recording_only=True,
        auxiliary_capture=clock is not None),previews=previews,live_planes=planes,live_videos=videos,
        refresh_backend=refresh_backend,refresh_gateway_subject=refresh_gateway_subject)
