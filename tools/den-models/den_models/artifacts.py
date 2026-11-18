"""Offline admission for reviewed, fully materialized model snapshots."""
import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import re
import sys
import time

from .contracts import ContractError, HEX64, canonical, profile_digest, strict_json

PROFILE_ROOT = Path(__file__).resolve().parents[1] / "profiles"
PROFILE_IDS = ("vjepa-lm-qwen3-8b", "vjepa-lm-qwen3-4b")


def adapter_bundle_digest():
    root = Path(__file__).resolve().parents[1]
    names = ("__init__.py", "artifacts.py", "backend.py", "client.py", "contracts.py", "control_producer.py", "resources.py",
             "runtime.py", "server.py", "qwen_queue.py", "qwen_observer.py")
    files = [*(root / "den_models" / name for name in names), root / "serve.py", root / "run_bounded.py",
             root / "requirements.lock"]
    closure = {path.relative_to(root).as_posix(): digest_file(path) for path in files}
    return hashlib.sha256(canonical(closure).encode()).hexdigest()


def load_profile(profile_id):
    if profile_id not in PROFILE_IDS:
        raise ContractError("unsupported_profile")
    return strict_json((PROFILE_ROOT / (profile_id + ".json")).read_text(encoding="utf-8"))


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_path(root, profile):
    # Operator-selected root; neither requests nor upstream config choose it.
    return Path(root).resolve() / profile["id"] / profile["revision"]


def check_receipt(profile, receipt, *, wall_time=None):
    expected = {"schema", "profile_digest", "revision", "admission_scope", "expires_at_utc", "runtime_manifest_sha256",
                "code_reviewed", "research_noncommercial_use_accepted", "files"}
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise ContractError("missing_or_invalid_admission_receipt")
    if (receipt["schema"] != "den.model-admission/v1"
            or receipt["profile_digest"] != profile_digest(profile)
            or receipt["revision"] != profile["revision"]
            or receipt["admission_scope"] != "bounded_trial"
            or receipt["code_reviewed"] is not True
            or receipt["research_noncommercial_use_accepted"] is not True
            or type(receipt["expires_at_utc"]) not in (int, float)
            or not math.isfinite(receipt["expires_at_utc"])
            or not isinstance(receipt["runtime_manifest_sha256"], str)
            or not HEX64.fullmatch(receipt["runtime_manifest_sha256"])
            or receipt["expires_at_utc"] <= (time.time() if wall_time is None else wall_time)):
        raise ContractError("profile_not_admitted")
    files = receipt["files"]
    if not isinstance(files, dict) or set(files) != set(profile["files"]):
        raise ContractError("incomplete_snapshot_manifest")
    for name, recorded in files.items():
        if (not isinstance(recorded, str) or not HEX64.fullmatch(recorded)
                or (profile["files"][name] is not None and recorded != profile["files"][name])):
            raise ContractError("snapshot_pin_mismatch")
    return hashlib.sha256(canonical(receipt).encode()).hexdigest()


def read_receipt(profile, receipt_root):
    path = Path(receipt_root) / (profile["id"] + ".json")
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ContractError("profile_not_admitted")
    receipt = strict_json(path.read_text(encoding="utf-8"))
    digest = check_receipt(profile, receipt)
    if profile.get("adapter_bundle_sha256") != adapter_bundle_digest():
        raise ContractError("adapter_bundle_not_pinned")
    read_runtime_manifest(profile, receipt_root, receipt)
    return receipt, digest


def normalized_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def read_runtime_manifest(profile, receipt_root, receipt):
    path = Path(receipt_root) / (profile["id"] + ".runtime.json")
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 128 * 1024:
        raise ContractError("runtime_wheel_closure_not_admitted")
    manifest = strict_json(path.read_text(encoding="utf-8"))
    if hashlib.sha256(canonical(manifest).encode()).hexdigest() != receipt["runtime_manifest_sha256"]:
        raise ContractError("runtime_manifest_digest_mismatch")
    expected = {"schema", "profile_digest", "python", "cuda", "reviewed", "distributions", "wheel_sha256", "record_sha256"}
    if (not isinstance(manifest, dict) or set(manifest) != expected
            or manifest["schema"] != "den.model-runtime/v1" or manifest["reviewed"] is not True
            or manifest["profile_digest"] != profile_digest(profile)
            or manifest["python"] != profile["runtime"]["python"]
            or manifest["cuda"] != profile["runtime"]["cuda"]):
        raise ContractError("runtime_manifest_not_admitted")
    distributions, wheel_hashes, record_hashes = (manifest[name] for name in
                                                ("distributions", "wheel_sha256", "record_sha256"))
    if (not all(isinstance(item, dict) for item in (distributions, wheel_hashes, record_hashes))
            or not distributions or len(distributions) > 256
            or set(distributions) != set(wheel_hashes) or set(distributions) != set(record_hashes)):
        raise ContractError("incomplete_runtime_wheel_closure")
    for name, version in distributions.items():
        if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or not isinstance(version, str) or not 0 < len(version) <= 128
                or any(not isinstance(table[name], str) or not HEX64.fullmatch(table[name])
                       for table in (wheel_hashes, record_hashes))):
            raise ContractError("invalid_runtime_distribution_pin")
    for name, version in profile["runtime"].items():
        if name in ("python", "cuda"):
            continue
        if distributions.get(normalized_name(name), "").split("+", 1)[0] != version:
            raise ContractError("runtime_direct_pin_mismatch")
    return manifest


def verify_snapshot(profile, snapshot_root, receipt):
    """Hash every file before executing publisher code or opening weight shards.

    Unknown publisher hashes stay null in the shipped candidate. A separately
    reviewed provisioning receipt must pin every such file, not silently turn
    a repo name into admission. Materialized files only: HF cache symlinks are
    deliberately refused. No network or provisioning is performed here.
    """
    receipt_digest = check_receipt(profile, receipt)
    path = snapshot_path(snapshot_root, profile)
    if not path.is_dir() or path.is_symlink():
        raise ContractError("complete_local_snapshot_missing")
    for parent in (path.parent, path):
        if parent.is_symlink():
            raise ContractError("symlink_snapshot")
    actual = {str(p.relative_to(path)).replace("\\", "/") for p in path.rglob("*") if p.is_file()}
    if actual != set(profile["files"]):
        raise ContractError("snapshot_file_closure_mismatch")
    for name, digest in receipt["files"].items():
        item = path / name
        if item.is_symlink() or not item.is_file() or digest_file(item) != digest:
            raise ContractError("snapshot_file_digest_mismatch")
    config = strict_json((path / "config.json").read_text(encoding="utf-8"))
    expected_map = {"AutoModel": profile["auto_model"], "AutoConfig": profile["auto_config"]}
    if config.get("auto_map") != expected_map or config.get("model_type") != "jepa_llm":
        raise ContractError("unexpected_remote_code_entrypoint")
    index = strict_json((path / "model.safetensors.index.json").read_text(encoding="utf-8"))
    weights = {name for name in profile["files"] if name.endswith(".safetensors")}
    if set(index.get("weight_map", {}).values()) != weights:
        raise ContractError("weight_index_mismatch")
    return path, receipt_digest


def installed_runtime():
    versions, records = {}, {}
    for distribution in importlib.metadata.distributions():
        name = normalized_name(distribution.metadata["Name"])
        record = distribution.read_text("RECORD")
        if name in versions or record is None:
            raise ContractError("unrecorded_or_duplicate_runtime_distribution")
        versions[name] = distribution.version
        records[name] = hashlib.sha256(record.encode()).hexdigest()
    return versions, records


def check_runtime(profile, manifest):
    if sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix:
        raise ContractError("isolated_python312_environment_required")
    versions, records = installed_runtime()
    if versions != manifest["distributions"] or records != manifest["record_sha256"]:
        raise ContractError("installed_runtime_closure_mismatch")
    for name, expected in profile["runtime"].items():
        if name in ("python", "cuda"):
            continue
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ContractError("runtime_dependency_missing") from None
        if actual.split("+", 1)[0] != expected:
            raise ContractError("runtime_version_mismatch_" + name)


def cgroup_limits(profile, io_device):
    """Require inherited cgroup v2 caps before any heavy import on Linux.

    The run wrapper installs these. An explicit major:minor is required for
    the storage device; a made-up universal I/O cap would be misleading.
    """
    if sys.platform != "linux" or not re.fullmatch(r"[0-9]+:[0-9]+", io_device or ""):
        raise ContractError("bounded_linux_cgroup_required")
    group = None
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            group = line[3:]
    if group is None or ".." in Path(group).parts:
        raise ContractError("cgroup_v2_required")
    directory = Path("/sys/fs/cgroup") / group.lstrip("/")
    caps = profile["execution"]
    # Parent cgroups can impose tighter limits; requiring the worker's own
    # visible values as well makes the intended operational boundary explicit.
    try:
        for filename, maximum in (("memory.max", caps["memory_max_bytes"]),
                                  ("memory.swap.max", 0), ("pids.max", caps["tasks_max"])):
            raw = (directory / filename).read_text().strip()
            if raw == "max" or int(raw) > maximum:
                raise ContractError("resource_cap_missing_" + filename)
        quota, period = (directory / "cpu.max").read_text().split()
        if quota == "max" or int(quota) / int(period) > caps["cpu_threads"]:
            raise ContractError("resource_cap_missing_cpu")
        rows = [row.split() for row in (directory / "io.max").read_text().splitlines()]
        row = next((r for r in rows if r and r[0] == io_device), None)
        limits = dict(field.split("=", 1) for field in row[1:]) if row else {}
        if limits.get("rbps", "max") == "max" or int(limits["rbps"]) > caps["io_read_bytes_per_second"]:
            raise ContractError("resource_cap_missing_io")
    except (OSError, ValueError, StopIteration):
        raise ContractError("resource_caps_unreadable") from None
    return str(directory)
