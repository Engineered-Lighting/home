"""Publish an already-admitted runtime observation for the existing Home reader."""
import json
import math
import os
from pathlib import Path
import stat
import tempfile


class CaptionPublication:
    def __init__(self, path, *, model_revision):
        self.path = Path(path)
        if not model_revision:
            raise ValueError('pinned model revision required')
        self.model_revision = model_revision

    def __call__(self, value, *, generated_at):
        if (value.get('site_id') != 'victoria' or value.get('camera_id') != 'den_oak'
                or value.get('room_id') != 'den' or value.get('model_revision') != self.model_revision):
            raise ValueError('caption source binding mismatch')
        observed, until, deadline = (value.get(k) for k in ('observed_at', 'valid_until', 'request_deadline'))
        uncertainty = value.get('uncertainty_seconds')
        if (any(type(x) not in (int, float) or not math.isfinite(x)
                for x in (observed, until, deadline, generated_at, uncertainty))
                or not 0 <= uncertainty <= 1 or not 0 < observed <= generated_at < until
                or generated_at >= deadline or not 0 < until - observed <= 180):
            raise ValueError('caption freshness invalid')
        for key, limit in (('description', 4000), ('request_id', 128), ('frame_id', 128)):
            if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > limit:
                raise ValueError('caption field invalid')
        result = dict(schema_version=1, site_id='victoria', camera_id='den', processing_site_id='echo',
                      model='qwen3-vl-30b', request_id=value['request_id'], frame_id=value['frame_id'],
                      text=value['description'], observed_at=observed, valid_until=until,
                      generated_at=generated_at, uncertainty_seconds=uncertainty,
                      timestamp_semantics='Validated source observation time with bounded clock uncertainty.')
        encoded = json.dumps(result).encode()
        if len(encoded) > 16384:
            raise ValueError('caption exceeds reader bound')
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (os.name == 'posix' and
                (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise ValueError('private caption directory required')
        descriptor, name = tempfile.mkstemp(prefix='.caption-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
