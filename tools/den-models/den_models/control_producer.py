"""Trusted LA control observations; no synthetic idle/healthy fallback.

Only configured local files and approved ROOT queue observations are read.
No Docker access, endpoint HTTP, model libraries, media or environment secrets
are used. A missing source produces a fresh *blocked* diagnostic receipt.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from den_models.contracts import (ContractError, DEPENDENCIES, HEX64, binding_values_valid, canonical, finite, ident,
                                  strict_json)
from den_models.resources import (HostProbe, NvidiaProbe, current_boot_id, private_json,
                                  process_start_identity, lease_path_digest)
from den_models.qwen_queue import QueueReader, source_from_receipt

SCHEMA = "den.model-control/v2"
CLOCK_DOMAIN = "la_host_monotonic/v1"
PRODUCER_SCHEMA = "den.model-control-producer/v2"
BROKER_SCOPE = "single_optional_execution"
STEPS = ("normal", "cadence_reduced", "deep_stack_paused", "loop_stopped")
REASON_CODE = re.compile(r"[a-z_]{1,80}\Z")
CONFIG_KEYS = {"schema", "vllm_port", "ops_status_file", "ops_host", "dependencies_file", "control_file",
               "execution_lease_file", "gpu_selector", "shared_gid", "poll_seconds",
               "ops_max_age_seconds", "trusted_source_uids", "ops_owner_uid", "queue_receipt_file",
               "queue_receipt_gid", "qwen_observer_config_sha256", "qwen_binding_policy_sha256"}


def code_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def validate_dependencies(doc, now):
    expected = {"schema", "observed_at", "valid_until", "dependencies"}
    if (not isinstance(doc, dict) or set(doc) != expected or doc["schema"] != "den.intent-source/v1"
            or not all(finite(doc[name]) for name in ("observed_at", "valid_until"))
            or not doc["observed_at"] <= now < doc["valid_until"] <= doc["observed_at"] + 2
            or not isinstance(doc["dependencies"], list) or len(doc["dependencies"]) > 1):
        raise ContractError("dependencies_stale_or_invalid")
    seen = set()
    for deps in doc["dependencies"]:
        if (not isinstance(deps, dict) or set(deps) != set(DEPENDENCIES)
                or not binding_values_valid(deps)
                or not re.fullmatch(r"0|[1-9][0-9]*", deps["camera_epoch"])
                or not re.fullmatch(r"0|[1-9][0-9]*", deps["association_revision"])
                or not HEX64.fullmatch(deps["calibration_revision"])
                or deps["source_generation"] != hashlib.sha256(canonical(
                    [deps["service_run_id"], int(deps["camera_epoch"])]).encode()).hexdigest()):
            raise ContractError("dependencies_lineage_invalid")
        key = canonical(deps)
        if key in seen:
            raise ContractError("dependencies_duplicate_binding")
        seen.add(key)
    return {"source_observed_at": doc["observed_at"], "valid_until": doc["valid_until"],
            "dependencies": doc["dependencies"]}


def parse_ops(doc, *, wall, expected_host, max_age):
    if not isinstance(doc, dict) or doc.get("schema") != "den-ops-status-1":
        raise ContractError("ops_status_invalid")
    # Retain the monitor's actual state even when freshness/evidence fail.
    level, latched = doc.get("level"), doc.get("latched")
    if (type(level) is not int or not 0 <= level <= 3 or type(latched) is not bool
            or doc.get("step") != STEPS[level] or doc.get("host") != expected_host
            or doc.get("synthetic") is True):
        raise ContractError("ops_status_invalid")
    checked = doc.get("checked_at")
    age = wall - checked if finite(checked) else None
    report = doc.get("report")
    evidence = report.get("evidence") if isinstance(report, dict) else None
    evidence_ok = evidence.get("ok") if isinstance(evidence, dict) else None
    fresh = age is not None and 0 <= age <= max_age
    return {"host": expected_host, "level": level, "latched": latched, "step": STEPS[level],
            "checked_at_utc": checked if finite(checked) else None, "age_seconds": age,
            "fresh": fresh, "evidence_ok": evidence_ok is True,
            "ok": fresh and evidence_ok is True and not (latched and level != 3)}


def broker_observation(path, *, identity=process_start_identity, shared_gid=None):
    if path is None:
        raise ContractError("broker_unconfigured")
    path = Path(path)
    # A private, configured single-execution scope makes an absent lease an
    # observed empty broker, not an assertion that unrelated GPU users are idle.
    from den_models.resources import private_parent
    private_parent(path, shared_gid=shared_gid)
    try:
        path.lstat()
    except FileNotFoundError:
        return {"scope": BROKER_SCOPE, "state": "free", "unallocated_reserve_bytes": 0,
                "global_enforcement": False}
    doc = private_json(path, max_bytes=4096, shared_gid=shared_gid)
    if (not isinstance(doc, dict) or doc.get("schema") != "den.gpu-execution-lease/v1"
            or type(doc.get("pid")) is not int or doc["pid"] <= 0
            or not isinstance(doc.get("token"), str) or not HEX64.fullmatch(doc["token"])
            or not isinstance(doc.get("process_start_identity"), str)
            or identity(doc["pid"]) != doc["process_start_identity"]):
        raise ContractError("broker_owner_unproven")
    return {"scope": BROKER_SCOPE, "state": "held", "owner_pid": doc["pid"],
            "owner_start_identity": doc["process_start_identity"], "unallocated_reserve_bytes": 0,
            "global_enforcement": False}


def load_config(path):
    cfg = private_json(path, max_bytes=16 * 1024, protected_parent=True)
    if not isinstance(cfg, dict) or set(cfg) != CONFIG_KEYS or cfg["schema"] != "den.model-control-config/v2":
        raise ContractError("invalid_control_config")
    for name in ("ops_status_file", "dependencies_file", "control_file", "execution_lease_file", "queue_receipt_file"):
        value = cfg[name]
        if value is None and name != "control_file":
            continue
        if not isinstance(value, str) or not Path(value).is_absolute() or "://" in value:
            raise ContractError("control_paths_must_be_local_absolute")
    paths = [Path(value).resolve() for name, value in cfg.items()
             if name in ("ops_status_file", "dependencies_file", "control_file", "execution_lease_file", "queue_receipt_file") and value is not None]
    paths.extend((Path(path).resolve(), Path(str(cfg["control_file"]) + ".producer.lock").resolve()))
    if len(paths) != len(set(paths)):
        raise ContractError("control_source_and_output_paths_must_be_distinct")
    if cfg["vllm_port"] is not None:
        raise ContractError("legacy_loopback_queue_source_forbidden")
    for name in ("qwen_observer_config_sha256", "qwen_binding_policy_sha256"):
        if cfg[name] is not None and (not isinstance(cfg[name], str) or not HEX64.fullmatch(cfg[name])):
            raise ContractError("qwen_observer_approval_invalid")
    if cfg["queue_receipt_gid"] is not None and (type(cfg["queue_receipt_gid"]) is not int or cfg["queue_receipt_gid"] < 0):
        raise ContractError("queue_receipt_gid_invalid")
    if cfg["shared_gid"] is not None and (type(cfg["shared_gid"]) is not int or cfg["shared_gid"] < 0):
        raise ContractError("invalid_shared_gid")
    if (cfg["trusted_source_uids"] is not None and
            (not isinstance(cfg["trusted_source_uids"], list) or len(cfg["trusted_source_uids"]) > 16
             or any(type(uid) is not int or uid < 0 for uid in cfg["trusted_source_uids"])
             or len(cfg["trusted_source_uids"]) != len(set(cfg["trusted_source_uids"])))):
        raise ContractError("invalid_trusted_source_uids")
    if cfg["ops_owner_uid"] is not None and (type(cfg["ops_owner_uid"]) is not int or cfg["ops_owner_uid"] < 0):
        raise ContractError("invalid_ops_owner_uid")
    if (not ident(cfg["ops_host"]) or not finite(cfg["poll_seconds"]) or not .2 <= cfg["poll_seconds"] <= 1
            or not finite(cfg["ops_max_age_seconds"]) or not 1 <= cfg["ops_max_age_seconds"] <= 90):
        raise ContractError("invalid_control_poll_limits")
    if cfg["gpu_selector"] is not None:
        NvidiaProbe(cfg["gpu_selector"])
    return cfg


class Producer:
    def __init__(self, config, *, clock=time.monotonic, wall=time.time,
                 read_json=private_json, gpu=None, host=None, identity=process_start_identity,
                 boot=current_boot_id, broker=broker_observation):
        if config.get("schema") != "den.model-control-config/v2" or config.get("vllm_port") is not None:
            raise ContractError("legacy_queue_config_not_admitted")
        self.config, self.clock, self.wall = config, clock, wall
        self.read_json, self.identity, self.broker = read_json, identity, broker
        self.queue_reader = QueueReader(config["queue_receipt_file"], shared_gid=config["queue_receipt_gid"],
                                        expected_config_sha256=config["qwen_observer_config_sha256"],
                                        expected_policy_sha256=config["qwen_binding_policy_sha256"])
        self.gpu = gpu or (NvidiaProbe(config["gpu_selector"]) if config["gpu_selector"] is not None else None)
        self.host = host or HostProbe()
        self.boot_id = boot()
        self.last_ops = None

    def _observe(self, name, call):
        started = self.clock()
        try:
            values = call()
            return {"ok": True, "started_at": started, "observed_at": self.clock(), **values}
        except Exception as exc:
            # Fixed reason only: never copy arbitrary HTTP/file contents. A
            # ContractError carries a fixed code (e.g. dependencies_lineage_invalid).
            code = str(exc) if isinstance(exc, ContractError) else ""
            return {"ok": False, "started_at": started, "observed_at": self.clock(),
                    "reason": code if REASON_CODE.fullmatch(code) else name + "_unavailable"}

    def sample(self):
        cfg = self.config
        def qwen_read():
            return source_from_receipt(self.queue_reader.read(self.clock()))
        qwen = self._observe("qwen", qwen_read)
        def ops_read():
            if cfg["ops_status_file"] is None:
                raise ContractError("ops_unconfigured")
            owners = () if cfg["ops_owner_uid"] is None else (cfg["ops_owner_uid"],)
            return parse_ops(self.read_json(cfg["ops_status_file"], max_bytes=64 * 1024, trusted_owner_uids=owners,
                                           protected_parent=True, allow_public_file=True),
                             wall=self.wall(), expected_host=cfg["ops_host"], max_age=cfg["ops_max_age_seconds"])
        ops = self._observe("den_ops", ops_read)
        if "level" in ops:
            self.last_ops = {key: ops[key] for key in ("level", "latched", "step", "checked_at_utc")}
        elif self.last_ops is not None:
            ops["last_known"] = self.last_ops
        def deps_read():
            if cfg["dependencies_file"] is None:
                raise ContractError("dependencies_unconfigured")
            doc = self.read_json(cfg["dependencies_file"], max_bytes=32 * 1024, shared_gid=cfg["shared_gid"],
                                 trusted_owner_uids=tuple(cfg["trusted_source_uids"] or ()), allow_shared_parent_write=True)
            return validate_dependencies(doc, self.clock())
        deps = self._observe("dependencies", deps_read)
        gpu = self._observe("gpu", lambda: {"free_bytes": self.gpu.free_bytes()} if self.gpu is not None and cfg["gpu_selector"] is not None
                            else (_ for _ in ()).throw(ContractError("gpu_unconfigured")))
        host = self._observe("host", lambda: {"available_bytes": self.host.available_bytes()})
        def broker_read():
            if cfg["execution_lease_file"] is None:
                raise ContractError("broker_unconfigured")
            return self.broker(cfg["execution_lease_file"], identity=self.identity, shared_gid=cfg["shared_gid"])
        broker = self._observe("broker", broker_read)
        now = self.clock()
        sources = {"qwen": qwen, "den_ops": ops, "dependencies": deps, "gpu": gpu, "host": host, "broker": broker}
        reasons = []
        for name, source in sources.items():
            if not source["ok"]:
                reasons.append(name + "_unavailable")
            if not 0 <= now - source["observed_at"] <= 1.5 or not 0 <= now - source["started_at"] <= 1.5:
                source["ok"] = False
                reasons.append(name + "_observation_stale")
        if deps["ok"] and not now < deps["valid_until"]:
            deps["ok"] = False
            reasons.append("dependencies_expired_during_observation")
        if qwen["ok"] and not now < qwen["valid_until"]:
            qwen["ok"] = False
            reasons.append("qwen_receipt_expired_during_observation")
        if ops.get("level", 0) > 0 or ops.get("latched") is True:
            reasons.append("den_ops_backoff")
        for name, field in (("gpu", "free_bytes"), ("host", "available_bytes")):
            if sources[name]["ok"] and (type(sources[name].get(field)) is not int or sources[name][field] < 0):
                sources[name]["ok"] = False
                reasons.append(name + "_observation_invalid")
        return {"schema": SCHEMA, "observed_at": now, "qwen_busy": qwen.get("busy", True) if qwen["ok"] else True,
                "unallocated_reserve_bytes": 0 if broker["ok"] else 1024**4,
                "dependencies": deps["dependencies"] if deps["ok"] else [],
                "admission_blocked": bool(reasons), "blocked_reasons": sorted(set(reasons)), "sources": sources,
                "producer": {"schema": PRODUCER_SCHEMA, "pid": os.getpid(), "process_start_identity": self.identity(os.getpid()),
                             "boot_id": self.boot_id, "clock_domain": CLOCK_DOMAIN, "code_sha256": code_digest(),
                             "execution_lease_sha256": lease_path_digest(cfg["execution_lease_file"])
                             if cfg["execution_lease_file"] is not None else None,
                             "queue_receipt_path_sha256": lease_path_digest(cfg["queue_receipt_file"])
                             if cfg["queue_receipt_file"] is not None else None,
                             "qwen_observer_config_sha256": cfg["qwen_observer_config_sha256"],
                             "qwen_binding_policy_sha256": cfg["qwen_binding_policy_sha256"],
                             "broker_scope": BROKER_SCOPE}}


def write_atomic(path, value):
    from den_models.resources import private_parent
    path = Path(path)
    private_parent(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(canonical(value))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ProducerLock:
    """Kernel-released singleton lock; unrelated to a GPU execution permit."""
    def __init__(self, control_file):
        self.path = Path(str(control_file) + ".producer.lock")
        self.descriptor = None

    def __enter__(self):
        import fcntl
        from den_models.resources import private_parent
        private_parent(self.path)
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(self.descriptor)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ContractError("private_producer_lock_required")
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self.descriptor, 0)
            record = {"schema": "den.control-producer-lock/v1", "pid": os.getpid(),
                      "process_start_identity": process_start_identity(os.getpid()), "code_sha256": code_digest()}
            os.write(self.descriptor, canonical(record).encode())
            os.fsync(self.descriptor)
        except BaseException:
            os.close(self.descriptor)
            self.descriptor = None
            raise ContractError("control_producer_already_running_or_lock_unavailable") from None
        return self

    def __exit__(self, *_):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="Publish one observed receipt, blocked on missing sources")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        raise ContractError("control_producer_requires_la_linux_clock")
    config = load_config(args.config)
    with ProducerLock(config["control_file"]):
        producer = Producer(config)
        stopped = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda _sig, _frame: stopped.set())
        while True:
            started = time.monotonic()
            value = producer.sample()
            write_atomic(config["control_file"], value)
            if args.once:
                return 2 if value["admission_blocked"] or value["qwen_busy"] else 0
            if stopped.wait(max(.01, config["poll_seconds"] - (time.monotonic() - started))):
                return 0


if __name__ == "__main__":
    raise SystemExit(main())
