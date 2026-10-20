"""OS lifetime ownership for a controller's local durable storage directory.

The lock file is never deleted/replaced: a stale filename is not a stale lock.
No PID inspection, process killing, timeout takeover, or remote-host operation.
"""
import os
from pathlib import Path


class OwnershipError(ValueError):
    pass


class StoreOwnership:
    def __init__(self, root):
        path = Path(root) / ".controller.lock"
        if path.is_symlink():
            raise OwnershipError("controller lock must not be a symbolic link")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
        self.fd = None
        try:
            os.set_inheritable(fd, False)
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"1")
                os.fsync(fd)
            self.fd = fd
        except Exception:
            os.close(fd)
            raise OwnershipError("Lab storage already owned or ownership unavailable") from None

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            # Closing releases the OS lock even after unexpected process death.
            os.close(fd)
