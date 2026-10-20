"""Read a protected, explicitly provisioned engine launch descriptor.

This loader never creates keys, ledgers, epochs or markers and never starts an
engine. Linux ownership checks are mandatory; no caller can disable them.
The startup composition independently validates the preexisting ledger binding.
"""
import json
import math
import re
from pathlib import Path

from .admission import ResourceManifest
from .service_config import protected_file
from .vllm_startup import StartupConfig

MAX_DESCRIPTOR_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
_FIELDS = frozenset({"version", "manifest_file", "key_file", "ledger_file", "engine_epoch",
                     "rpc_timeout_seconds", "max_database_bytes"})
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class LaunchConfigError(ValueError):
    """Protected launch provisioning is unavailable or invalid."""


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LaunchConfigError("duplicate launch provisioning field")
        value[key] = item
    return value


def _constant(_value):
    raise LaunchConfigError("nonfinite launch provisioning value")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise LaunchConfigError("nonfinite launch provisioning value")
    return result


def _read(path, limit):
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if not raw or len(raw) > limit:
        raise LaunchConfigError("launch provisioning size rejected")
    return raw


def _json(path, limit):
    return json.loads(_read(path, limit).decode("utf-8"), object_pairs_hook=_pairs,
                      parse_constant=_constant, parse_float=_float)


def load_launch_config(path):
    """Load fixed sibling files; all paths are trusted operator configuration."""
    try:
        if not Path(path).is_absolute():
            raise LaunchConfigError('absolute launch descriptor required')
        descriptor = protected_file(path, secret=False, enforce_linux=True)
        value = _json(descriptor, MAX_DESCRIPTOR_BYTES)
        if (type(value) is not dict or set(value) != _FIELDS
                or type(value["version"]) is not int or value["version"] != 1):
            raise LaunchConfigError("unsupported launch descriptor")
        names = [value[key] for key in ("manifest_file", "key_file", "ledger_file")]
        if (any(type(name) is not str or _FILENAME.fullmatch(name) is None for name in names)
                or len(set(names)) != 3 or descriptor.name in names):
            raise LaunchConfigError("distinct fixed sibling filenames required")
        manifest_path = protected_file(descriptor.parent / names[0], secret=False, enforce_linux=True)
        key_path = protected_file(descriptor.parent / names[1], secret=True, enforce_linux=True)
        ledger_path = protected_file(descriptor.parent / names[2], secret=True, enforce_linux=True)
        manifest = ResourceManifest.from_dict(_json(manifest_path, MAX_MANIFEST_BYTES))
        key = _read(key_path, 4096)
        if len(key) < 32:
            raise LaunchConfigError("signing key size rejected")
        return StartupConfig(manifest=manifest, engine_epoch=value["engine_epoch"],
                             ledger_path=str(ledger_path), signer_key=key,
                             rpc_timeout_seconds=value["rpc_timeout_seconds"],
                             max_database_bytes=value["max_database_bytes"])
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        # Neither parser excerpts nor inline unknown-field values reach logs.
        raise LaunchConfigError("protected engine launch provisioning rejected") from None
