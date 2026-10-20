"""Explicit production ASGI factory. No model starts at import or startup."""
import os
from pathlib import Path
import stat
from lifecycle import Lifecycle
from lifecycle_launcher import Launcher
from lifecycle_http import LifecycleHTTP
from start_live import BASE,TLS


def private(path, *, directory=False):
    info=path.lstat()
    correct=stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('private lifecycle provisioning required')
    return path


def create_app():
    os.umask(0o077)
    root=private(BASE/'lifecycle-private',directory=True)
    for name in ('lifecycle.sqlite','lifecycle.sqlite.owner','lifecycle.sqlite-journal'):
        path=root/name
        if path.exists() or path.is_symlink():private(path)
    def token(path):
        data=private(path).read_bytes()
        if len(data)>512:raise ValueError('credential too large')
        return data.decode('ascii').rstrip('\r\n')
    controller=token(root/'controller.token')
    execution=token(TLS/'client.token')
    launcher=Launcher()
    from session_budget import provisioned_budget
    lifecycle=Lifecycle(root/'lifecycle.sqlite',launcher,lease_seconds=provisioned_budget()['lease_seconds'])
    return LifecycleHTTP(lifecycle,controller_token=controller,execution_token=execution)
