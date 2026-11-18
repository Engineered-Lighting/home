"""Driver observations and cooperative execution leases, with no torch import."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

from .contracts import ContractError, canonical, strict_json


def current_boot_id():
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ContractError("local_boot_identity_unavailable") from None


def lease_path_digest(path):
    if path is None or not Path(path).is_absolute():
        raise ContractError("execution_broker_path_unconfigured")
    return hashlib.sha256(canonical(Path(path).resolve().as_posix()).encode()).hexdigest()


def parent_permissions(uid, gid, mode, *, current_uid, shared_gid=None, trusted_owner_uids=(),
                       allow_shared_parent_write=False, protected_parent=False):
    allowed_owners = {0, current_uid, *trusted_owner_uids}
    scoped_shared = (allow_shared_parent_write and uid == 0 and shared_gid is not None
                     and gid == shared_gid and mode == 0o2770)
    protected = protected_parent and uid in allowed_owners and not mode & 0o022
    private = (uid in allowed_owners and not mode & 0o027
               and (not mode & 0o050 or shared_gid is not None and gid == shared_gid))
    return bool(scoped_shared or protected or private)


def file_permissions(uid, gid, mode, *, current_uid, shared_gid=None, trusted_owner_uids=(), allow_public_file=False):
    allowed = mode == 0o600 or (mode == 0o640 and shared_gid is not None and gid == shared_gid)
    if allow_public_file:
        allowed = not mode & 0o022
    return uid in {0, current_uid, *trusted_owner_uids} and bool(allowed)


def private_parent(path, *, shared_gid=None, trusted_owner_uids=(), allow_shared_parent_write=False, protected_parent=False):
    path = Path(path)
    parent = path.parent
    if (not path.is_absolute() or not parent.is_dir() or parent.is_symlink()
            or parent.resolve() != parent.absolute() or path.is_symlink()):
        raise ContractError("private_local_path_required")
    info = parent.stat()
    if os.name == "posix":
        mode = stat.S_IMODE(info.st_mode)
        if not parent_permissions(info.st_uid, info.st_gid, mode, current_uid=os.getuid(), shared_gid=shared_gid,
                                  trusted_owner_uids=trusted_owner_uids, allow_shared_parent_write=allow_shared_parent_write,
                                  protected_parent=protected_parent):
            raise ContractError("private_local_directory_required")


def private_json(path, *, max_bytes=16 * 1024, shared_gid=None, trusted_owner_uids=(),
                 allow_shared_parent_write=False, protected_parent=False, allow_public_file=False):
    if path is None:
        raise ContractError("local_source_unconfigured")
    path = Path(path)
    private_parent(path, shared_gid=shared_gid, trusted_owner_uids=trusted_owner_uids,
                   allow_shared_parent_write=allow_shared_parent_write, protected_parent=protected_parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise ContractError("private_local_file_required")
            if os.name == "posix":
                mode = stat.S_IMODE(info.st_mode)
                if not file_permissions(info.st_uid, info.st_gid, mode, current_uid=os.getuid(), shared_gid=shared_gid,
                                        trusted_owner_uids=trusted_owner_uids, allow_public_file=allow_public_file):
                    raise ContractError("private_local_file_required")
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ContractError("local_source_oversize")
        return strict_json(raw)
    except (OSError, ValueError, UnicodeError):
        raise ContractError("private_local_source_unavailable") from None


class NvidiaProbe:
    def __init__(self, selector):
        if not re.fullmatch(r"(?:[0-9]+|GPU-[0-9a-fA-F-]{16,64})", selector):
            raise ContractError("invalid_gpu_selector")
        self.selector = selector

    def free_bytes(self):
        try:
            run = subprocess.run(["nvidia-smi", "-i", self.selector, "--query-gpu=memory.free",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                 timeout=3, check=True)
            lines = run.stdout.strip().splitlines()
            if len(lines) != 1 or not lines[0].strip().isdigit():
                raise ValueError()
            return int(lines[0].strip()) * 1024**2
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ContractError("gpu_driver_observation_unavailable") from None


class HostProbe:
    def available_bytes(self):
        try:
            rows = Path("/proc/meminfo").read_text().splitlines()
            line = next(row for row in rows if row.startswith("MemAvailable:"))
            fields = line.split()
            if len(fields) != 3 or fields[2] != "kB":
                raise ValueError()
            return int(fields[1]) * 1024
        except (OSError, ValueError, StopIteration):
            raise ContractError("host_memory_observation_unavailable") from None


def process_start_identity(pid):
    if os.name != "posix":
        return "nonlinux-" + str(pid)
    # /proc stat comm may contain spaces or parentheses; fields start after
    # the final closing parenthesis. Field22 is process start ticks.
    stat = Path(f"/proc/{pid}/stat").read_text()
    start = stat.rsplit(")", 1)[1].split()[19]
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return boot + ":" + start


class FileExecutionLease:
    """Cooperative O_EXCL broker. Existing callers must explicitly join it.

    A stale file is never automatically stolen. PID reuse is not proof that
    the old native work finished, and this cannot enforce other applications.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.token = None

    def try_acquire(self, request_id):
        token = secrets.token_hex(32)
        record = {"schema": "den.gpu-execution-lease/v1", "token": token, "pid": os.getpid(),
                  "process_start_identity": process_start_identity(os.getpid()),
                  "request_digest": hashlib.sha256(request_id.encode()).hexdigest(),
                  "created_at_utc": time.time()}
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as destination:
                destination.write(canonical(record))
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            # If creation partially fails, retain the conservative lock for
            # operator inspection rather than pretend ownership was absent.
            raise
        self.token = token
        return token

    def release(self, token, proof):
        if proof not in ("cuda_synchronized", "no_native_context", "process_reaped") or not token or token != self.token:
            return False
        try:
            record = strict_json(self.path.read_text())
            if record.get("token") != token or record.get("pid") != os.getpid():
                return False
            self.path.unlink()
        except (OSError, ValueError):
            return False
        self.token = None
        return True


class ControlState:
    """Attested live producer receipt; manual healthy JSON cannot admit work."""
    def __init__(self, path, max_age=2.0, *, expected_lease_file=None, shared_gid=None, identity=None, boot=None, wall=None,
                 queue_receipt_file=None, queue_receipt_gid=None, expected_qwen_config_sha256=None, expected_qwen_policy_sha256=None):
        self.path, self.max_age = Path(path), max_age
        self.shared_gid, self.identity = shared_gid, identity or process_start_identity
        self.boot, self.wall = boot or current_boot_id, wall or time.time
        self.producer_digest = hashlib.sha256(Path(__file__).with_name("control_producer.py").read_bytes()).hexdigest()
        self.expected_lease_digest = lease_path_digest(expected_lease_file) if expected_lease_file is not None else None
        from .qwen_queue import QueueReader
        self.queue_reader = QueueReader(queue_receipt_file, shared_gid=queue_receipt_gid,
                                        expected_config_sha256=expected_qwen_config_sha256,
                                        expected_policy_sha256=expected_qwen_policy_sha256)
        self.expected_queue_path = lease_path_digest(queue_receipt_file) if queue_receipt_file is not None else None

    def read(self, now):
        try:
            state = private_json(self.path, max_bytes=16 * 1024, shared_gid=self.shared_gid)
            expected = {"schema", "qwen_busy", "observed_at", "unallocated_reserve_bytes", "dependencies",
                        "admission_blocked", "blocked_reasons", "producer", "sources"}
            if (not isinstance(state, dict) or set(state) != expected
                    or state.get("schema") != "den.model-control/v2" or type(state.get("qwen_busy")) is not bool
                    or type(state.get("observed_at")) not in (int, float)
                    or not 0 <= now - state["observed_at"] <= self.max_age
                    or type(state.get("unallocated_reserve_bytes")) is not int
                    or not 0 <= state["unallocated_reserve_bytes"] <= 1024**4
                    or not isinstance(state.get("dependencies"), list) or len(state["dependencies"]) > 64):
                raise ContractError("control_state_stale_or_invalid")
            producer = state["producer"]
            if (not isinstance(producer, dict) or set(producer) != {"schema", "pid", "process_start_identity",
                    "boot_id", "clock_domain", "code_sha256", "broker_scope", "execution_lease_sha256",
                    "queue_receipt_path_sha256", "qwen_observer_config_sha256", "qwen_binding_policy_sha256"}
                    or producer["schema"] != "den.model-control-producer/v2"
                    or producer["clock_domain"] != "la_host_monotonic/v1"
                    or producer["broker_scope"] != "single_optional_execution"
                    or self.expected_lease_digest is None
                    or producer["execution_lease_sha256"] != self.expected_lease_digest
                    or self.expected_queue_path is None or producer["queue_receipt_path_sha256"] != self.expected_queue_path
                    or producer["qwen_observer_config_sha256"] != self.queue_reader.expected_config_sha256
                    or producer["qwen_binding_policy_sha256"] != self.queue_reader.expected_policy_sha256
                    or producer["code_sha256"] != self.producer_digest
                    or producer["boot_id"] != self.boot() or type(producer["pid"]) is not int or producer["pid"] <= 0
                    or self.identity(producer["pid"]) != producer["process_start_identity"]):
                raise ContractError("control_producer_attestation_invalid")
            if os.name == "posix" and Path(f"/proc/{producer['pid']}").stat().st_uid != self.path.stat().st_uid:
                raise ContractError("control_producer_owner_mismatch")
            if (type(state["admission_blocked"]) is not bool or not isinstance(state["blocked_reasons"], list)
                    or len(state["blocked_reasons"]) > 32
                    or not all(isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,80}", reason)
                               for reason in state["blocked_reasons"])):
                raise ContractError("control_admission_barrier_invalid")
            if state["admission_blocked"] or state["blocked_reasons"]:
                raise ContractError("control_admission_blocked")
            sources = state["sources"]
            if not isinstance(sources, dict) or set(sources) != {"qwen", "den_ops", "dependencies", "gpu", "host", "broker"}:
                raise ContractError("control_source_evidence_missing")
            for source in sources.values():
                if (not isinstance(source, dict) or source.get("ok") is not True
                        or type(source.get("observed_at")) not in (int, float)
                        or not 0 <= now - source["observed_at"] <= self.max_age):
                    raise ContractError("control_source_evidence_stale")
            ops, deps, qwen, broker = (sources[name] for name in ("den_ops", "dependencies", "qwen", "broker"))
            if (type(ops.get("level")) is not int or ops["level"] != 0 or ops.get("latched") is not False or ops.get("step") != "normal"
                    or ops.get("fresh") is not True or ops.get("evidence_ok") is not True
                    or type(ops.get("checked_at_utc")) not in (int, float)
                    or not 0 <= self.wall() - ops["checked_at_utc"] <= 90):
                raise ContractError("control_ops_backoff_or_unknown")
            if (type(deps.get("source_observed_at")) not in (int, float)
                    or type(deps.get("valid_until")) not in (int, float)
                    or not deps["source_observed_at"] <= now < deps["valid_until"] <= deps["source_observed_at"] + 2
                    or deps.get("dependencies") != state["dependencies"]):
                raise ContractError("control_dependencies_stale_or_changed")
            if (any(type(qwen.get(name)) is not int or qwen[name] < 0 for name in ("running", "waiting"))
                    or type(qwen.get("busy")) is not bool
                    or qwen["busy"] != (qwen["running"] > 0 or qwen["waiting"] > 0)
                    or state["qwen_busy"] != qwen["busy"]
                    or broker.get("scope") != "single_optional_execution" or broker.get("state") not in ("free", "held")
                    or broker.get("global_enforcement") is not False
                    or broker.get("unallocated_reserve_bytes") != 0 or state["unallocated_reserve_bytes"] != 0):
                raise ContractError("control_broker_or_queue_evidence_invalid")
            if any(type(sources[name].get(field)) is not int or sources[name][field] < 0
                   for name, field in (("gpu", "free_bytes"), ("host", "available_bytes"))):
                raise ContractError("control_resource_observation_invalid")
            # The actual ROOT authority file is read independently. Copying a
            # root-looking JSON object into a model-UID control file is not proof
            # of root ownership. A newer same-binding sample may safely add busy
            # without treating normal publisher polling races as job loss.
            from .qwen_queue import SEMANTICS, digest, source_from_receipt
            current = self.queue_reader.read(now)
            copied = qwen.get("root_receipt")
            self.queue_reader.validate(copied, now)
            if (qwen.get("semantics") != SEMANTICS or qwen.get("root_receipt_sha256") != digest(copied)
                    or any(qwen.get(key) != value for key, value in source_from_receipt(copied).items())
                    or copied["binding_sha256"] != current["binding_sha256"]
                    or copied["observer"] != current["observer"] or copied["sequence"] > current["sequence"]):
                raise ContractError("control_root_queue_binding_changed_or_unproven")
            result = strict_json(canonical(state))
            result["qwen_busy"] = state["qwen_busy"] or bool(current["gauges"]["running"] or current["gauges"]["waiting"])
            result["sources"]["qwen"]["current_verified_observation"] = source_from_receipt(current)
            return result
        except (OSError, ValueError):
            raise ContractError("control_state_unavailable") from None
