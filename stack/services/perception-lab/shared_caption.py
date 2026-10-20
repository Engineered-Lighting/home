"""Read a provisioned, private cross-home caption; never initiate inference."""
import json
import math
import os
import stat
import time


def read_caption(path, site_id, camera_id, now=None):
    empty = {"status": "invalid", "site_id": site_id, "camera_id": camera_id}
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 16384:
            return empty
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)
                    or info.st_size > 16384):
                return empty
            if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                return empty
            data = stream.read(16385)
        if len(data) > 16384:
            return empty
        value = json.loads(data)
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            return empty
        if (value.get("site_id") != site_id or value.get("camera_id") != camera_id
                or value.get("processing_site_id") != "echo"
                or value.get("model") != "qwen3-vl-30b"):
            return empty
        for key, bound in (("text", 4000), ("request_id", 128), ("frame_id", 128), ("timestamp_semantics", 500)):
            if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > bound:
                return empty
        for key in ("observed_at", "valid_until", "generated_at"):
            if type(value.get(key)) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0:
                return empty
        current = time.time() if now is None else now
        uncertainty = value.get("uncertainty_seconds", 0)
        if type(uncertainty) not in (int, float) or not math.isfinite(uncertainty) or not 0 <= uncertainty <= 1:
            return empty
        if (not value["observed_at"] <= value["generated_at"] <= current + uncertainty
                or not 0 < value["valid_until"] - value["observed_at"] <= 180):
            return empty
        allowed = ("schema_version", "site_id", "camera_id", "processing_site_id", "model", "request_id",
                   "frame_id", "text", "observed_at", "valid_until", "generated_at", "timestamp_semantics", "uncertainty_seconds")
        result = {key: value[key] for key in allowed if key in value}
        result["status"] = "current" if current < value["valid_until"] else "stale"
        return result
    except FileNotFoundError:
        return {**empty, "status": "missing"}
    except (OSError, ValueError, TypeError, OverflowError):
        return empty
