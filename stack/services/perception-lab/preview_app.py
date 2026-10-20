"""Explicit Victoria review + live-preview factory; never enables recording."""
import os
from pathlib import Path
import stat
from app import create_app
from config import Settings
from live_preview import LivePreview
from preview_client import LocalPreviewClient


def camera_token(path):
    path=Path(path)
    for target in (path.parent,path):
        info=target.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077 or info.st_uid!=os.getuid():
            raise ValueError('Private camera credential required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o077 or info.st_uid!=os.getuid():
            raise ValueError('Private camera credential required')
        raw=source.read(8193)
    if len(raw)>8192:raise ValueError('Invalid camera credential')
    values=[line.split('=',1)[1].strip() for line in raw.decode().splitlines() if line.startswith('VICTORIA_CAMERA_TOKEN=')]
    if len(values)!=1:raise ValueError('Invalid camera credential')
    token=values[0]
    if len(token)>1 and token[0]==token[-1] and token[0] in "\"'":token=token[1:-1]
    if not 24<=len(token)<=1024 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('Invalid camera credential')
    return token


def create_preview_app():
    settings=Settings.from_file(os.environ['PERCEPTION_LAB_CONFIG'])
    if settings.site_id!='victoria' or len(settings.cameras)!=1 or settings.developer_fixture_mode:
        raise ValueError('One registered Victoria camera required')
    path=os.environ['PERCEPTION_LAB_CAMERA_ENV']
    camera_token(path)  # Check provisioning; no network activity at startup.
    camera=settings.cameras[0]['id']
    preview=LivePreview(LocalPreviewClient(lambda:camera_token(path)),site_id=settings.site_id,camera_id=camera)
    return create_app(settings,previews={camera:preview})
