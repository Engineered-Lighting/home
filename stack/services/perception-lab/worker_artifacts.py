"""Bounded verification for immutable provisioned local worker artifacts."""
import hashlib
from pathlib import Path


def verified_artifact(path, sha256, suffix, maximum_bytes=2*1024**3):
    target=Path(path)
    if not target.is_absolute() or not target.is_file() or target.suffix != suffix:
        raise ValueError("existing provisioned artifact path required")
    if any(p.is_symlink() or getattr(p,"is_junction",lambda:False)() for p in (target,*target.parents)):
        raise ValueError("artifact bindings cannot traverse links")
    before=target.stat()
    if not 0<before.st_size<=maximum_bytes:
        raise ValueError("artifact exceeds worker allowance")
    checksum=hashlib.sha256(); consumed=0
    with target.open("rb") as source:
        for block in iter(lambda:source.read(1024*1024),b""):
            consumed+=len(block)
            if consumed>before.st_size:
                raise ValueError("artifact grew during verification")
            checksum.update(block)
    after=target.stat()
    if ((before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino)
            or consumed!=before.st_size or checksum.hexdigest()!=sha256):
        raise ValueError("artifact changed or checksum mismatch")
    return target
