"""Atomically maintain the offline native-installation allowlist.

The registry contains public keys and exact Home Assistant user identifiers,
not bearer credentials.  It is still identity-sensitive deployment policy and
therefore remains root-owned on the encrypted Home Agent secrets volume.

The operation is read from bounded JSON on stdin so identity values never
enter the process list.  Existing key/user bindings cannot be replaced or
reactivated: revoke the old installation and enroll a new UUID/key instead.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - the operator tool is POSIX-only
    fcntl = None  # type: ignore[assignment]


MAX_INPUT_BYTES = 8 * 1024
SCHEMA_VERSION = 1
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
B64URL_32_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

# SEC 2 secp256r1 / NIST P-256 field and curve constant.  Checking the curve
# equation makes the offline writer reject coordinates Node/OpenSSL would not
# accept as a prime256v1 public key.
P256_P = int("FFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF", 16)
P256_B = int("5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B", 16)


class RegistryError(RuntimeError):
    """Content-free operator error."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError("duplicate JSON key")
        result[key] = value
    return result


def _exact_keys(value: dict[str, Any], expected: set[str], kind: str) -> None:
    if set(value) != expected:
        raise RegistryError(f"invalid {kind} fields")


def _uuid4(value: Any, kind: str) -> str:
    if not isinstance(value, str) or UUID4_RE.fullmatch(value) is None:
        raise RegistryError(f"invalid {kind}")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise RegistryError(f"invalid {kind}") from error
    if parsed.version != 4 or str(parsed) != value:
        raise RegistryError(f"invalid {kind}")
    return value


def _coordinate(value: Any) -> int:
    if not isinstance(value, str) or B64URL_32_RE.fullmatch(value) is None:
        raise RegistryError("invalid P-256 coordinate")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise RegistryError("invalid P-256 coordinate") from error
    if len(decoded) != 32:
        raise RegistryError("invalid P-256 coordinate")
    return int.from_bytes(decoded, "big")


def _validate_installation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("installation must be an object")
    _exact_keys(
        value,
        {"installation_id", "ha_user_id", "status", "public_key_jwk"},
        "installation",
    )
    installation_id = _uuid4(value["installation_id"], "installation UUID")
    ha_user_id = value["ha_user_id"]
    if (
        not isinstance(ha_user_id, str)
        or not 1 <= len(ha_user_id) <= 64
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in ha_user_id)
    ):
        raise RegistryError("invalid Home Assistant user identifier")
    if value["status"] not in {"active", "revoked"}:
        raise RegistryError("invalid installation status")

    jwk = value["public_key_jwk"]
    if not isinstance(jwk, dict):
        raise RegistryError("public key must be a JWK object")
    _exact_keys(jwk, {"kty", "crv", "x", "y", "kid"}, "public JWK")
    if jwk["kty"] != "EC" or jwk["crv"] != "P-256":
        raise RegistryError("public key must use P-256")
    if jwk["kid"] != installation_id:
        raise RegistryError("public key identifier does not match installation")
    x = _coordinate(jwk["x"])
    y = _coordinate(jwk["y"])
    if x >= P256_P or y >= P256_P:
        raise RegistryError("invalid P-256 public point")
    if (pow(y, 2, P256_P) - (pow(x, 3, P256_P) - 3 * x + P256_B)) % P256_P:
        raise RegistryError("invalid P-256 public point")

    return {
        "installation_id": installation_id,
        "ha_user_id": ha_user_id,
        "status": value["status"],
        "public_key_jwk": {
            "kty": "EC",
            "crv": "P-256",
            "x": jwk["x"],
            "y": jwk["y"],
            "kid": installation_id,
        },
    }


def _validate_registry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("registry must be an object")
    _exact_keys(value, {"schema_version", "installations"}, "registry")
    if value["schema_version"] != SCHEMA_VERSION:
        raise RegistryError("unsupported registry schema")
    if not isinstance(value["installations"], list):
        raise RegistryError("registry installations must be an array")
    installations = [_validate_installation(item) for item in value["installations"]]
    identifiers = [item["installation_id"] for item in installations]
    if len(identifiers) != len(set(identifiers)):
        raise RegistryError("duplicate installation UUID")
    public_keys = [
        (item["public_key_jwk"]["x"], item["public_key_jwk"]["y"])
        for item in installations
    ]
    if len(public_keys) != len(set(public_keys)):
        raise RegistryError("public key is bound to multiple installations")
    installations.sort(key=lambda item: item["installation_id"])
    return {"schema_version": SCHEMA_VERSION, "installations": installations}


def _load_json(raw: bytes, kind: str) -> Any:
    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_strict_object)
    except UnicodeDecodeError as error:
        raise RegistryError(f"{kind} is not UTF-8") from error
    except json.JSONDecodeError as error:
        raise RegistryError(f"{kind} is not valid JSON") from error


def _read_operation() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise RegistryError("operation input is empty or oversized")
    value = _load_json(raw, "operation")
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        raise RegistryError("operation must be an object with an action")
    return value


def _require_encrypted_root_path(path: Path) -> None:
    findmnt = shutil.which("findmnt")
    if findmnt is None:
        raise RegistryError("findmnt is required")
    probe = subprocess.run(
        [findmnt, "-rn", "-o", "SOURCE", "-T", str(path.parent)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0 or not probe.stdout.strip().startswith("/dev/mapper/"):
        raise RegistryError("registry must be on an encrypted mapper")


def _require_safe_parent(path: Path) -> None:
    if not path.is_absolute() or path.name != "native_installations.json":
        raise RegistryError("registry path must be absolute and use the fixed filename")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.resolve() != parent:
        raise RegistryError("registry parent is unsafe")
    metadata = parent.stat()
    if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RegistryError("registry parent must be root-owned mode 0700")
    _require_encrypted_root_path(path)


def _load_registry(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RegistryError("registry is absent or unsafe")
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RegistryError("registry must be root-owned mode 0600")
    if metadata.st_size > 64 * 1024:
        raise RegistryError("registry is oversized")
    return _validate_registry(_load_json(path.read_bytes(), "registry"))


def _apply(registry: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    action = operation["action"]
    if action == "initialize":
        _exact_keys(operation, {"action"}, "initialize operation")
        return registry

    installations = {
        item["installation_id"]: item for item in registry["installations"]
    }
    if action == "enroll":
        _exact_keys(operation, {"action", "installation"}, "enroll operation")
        candidate = _validate_installation(operation["installation"])
        if candidate["status"] != "active":
            raise RegistryError("new installation must be active")
        existing = installations.get(candidate["installation_id"])
        if existing is not None and existing != candidate:
            raise RegistryError("installation UUID is already bound")
        installations[candidate["installation_id"]] = candidate
    elif action == "revoke":
        _exact_keys(operation, {"action", "installation_id"}, "revoke operation")
        installation_id = _uuid4(operation["installation_id"], "installation UUID")
        existing = installations.get(installation_id)
        if existing is None:
            raise RegistryError("installation UUID is not enrolled")
        installations[installation_id] = {**existing, "status": "revoked"}
    else:
        raise RegistryError("unsupported registry action")
    return {
        "schema_version": SCHEMA_VERSION,
        "installations": sorted(
            installations.values(), key=lambda item: item["installation_id"]
        ),
    }


def _write_atomic(path: Path, registry: dict[str, Any]) -> None:
    encoded = (
        json.dumps(
            registry,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _acquire_lock(path: Path) -> int:
    if fcntl is None:
        raise RegistryError("POSIX file locking is required")
    lock_path = path.with_name(".native-installations.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
        ):
            raise RegistryError("registry lock is unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError as error:
        os.close(descriptor)
        raise RegistryError("another registry operation is running") from error
    except BaseException:
        os.close(descriptor)
        raise


def main() -> int:
    if os.name != "posix" or os.geteuid() != 0:
        raise RegistryError("native installation registry maintenance requires root on POSIX")
    if len(sys.argv) != 2:
        raise RegistryError("usage: manage_native_installations.py <absolute-registry-path>")
    path = Path(sys.argv[1])
    _require_safe_parent(path)
    operation = _read_operation()
    lock = _acquire_lock(path)
    try:
        if operation["action"] == "validate":
            _exact_keys(operation, {"action"}, "validate operation")
            _load_registry(path)
            print("native installation registry valid")
            return 0
        if operation["action"] == "initialize":
            if path.exists() or path.is_symlink():
                raise RegistryError("registry already exists")
            registry = {"schema_version": SCHEMA_VERSION, "installations": []}
            _exact_keys(operation, {"action"}, "initialize operation")
        else:
            registry = _apply(_load_registry(path), operation)
        _write_atomic(path, _validate_registry(registry))
    finally:
        os.close(lock)
    print("native installation registry updated")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RegistryError as error:
        print(f"native installation registry: {error}", file=sys.stderr)
        raise SystemExit(78) from error
