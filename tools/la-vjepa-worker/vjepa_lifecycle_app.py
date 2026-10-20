"""ASGI factory for lab-vjepa-lifecycle.service (192.168.0.100:9447, TLS).

The reviewed lifecycle.Lifecycle and lifecycle_http.LifecycleHTTP, byte-identical
copies of tools/la-detector-trial, run unchanged here with their own ledger,
their own controller/execution tokens and vjepa_launcher.Launcher. No model
starts at import or startup; a lease start is the only launch path.
"""
import os
from pathlib import Path
import stat

from lifecycle import Lifecycle
from lifecycle_http import LifecycleHTTP
from vjepa_launcher import BASE, PRIVATE, Launcher


def private(path, *, directory=False):
    info = path.lstat()
    correct = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("private lifecycle provisioning required")
    return path


def token(path):
    data = private(path).read_bytes()
    if len(data) > 512:
        raise ValueError("credential too large")
    return data.decode("ascii").rstrip("\r\n")


def create_app(*, base=BASE, launcher_factory=Launcher, environ=os.environ):
    os.umask(0o077)
    root = private(Path(base) / "lifecycle-private", directory=True)
    private(Path(base) / PRIVATE.name, directory=True)
    for name in ("lifecycle.sqlite", "lifecycle.sqlite.owner", "lifecycle.sqlite-journal"):
        path = root / name
        if path.exists() or path.is_symlink():
            private(path)
    controller = token(root / "controller.token")
    execution = token(Path(base) / PRIVATE.name / "execution.token")
    if controller == execution:
        raise ValueError("controller and execution credentials must differ")
    launcher = launcher_factory(environ=environ)
    lifecycle = Lifecycle(root / "lifecycle.sqlite", launcher, lease_seconds=launcher.lease_seconds)
    http = LifecycleHTTP(lifecycle, controller_token=controller, execution_token=execution)
    # The full kernel-journal fault scan now, at supervisor start, instead of
    # inside the first lease's preflight (no model, no GPU).
    launcher.warm_start()
    return http
