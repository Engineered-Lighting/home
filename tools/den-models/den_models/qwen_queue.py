"""Strict reported-queue evidence from a separately privileged root observer.

This module never contacts Docker or a model endpoint. Production reads require
an actual root-owned, protected file and a live root observer. The pure validators
also support CPU-only protocol tests; there is no configuration bypass.
"""
import hashlib
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import time

from .contracts import ContractError, HEX64, canonical, finite, strict_json

CLOCK_DOMAIN = "la_host_monotonic/v1"
SCHEMA = "den.qwen-queue-observation/v1"
OBSERVER_SCHEMA = "den.root-qwen-observer/v1"
SEMANTICS = "reported_scheduler_gauges_not_atomic_admission"
MAX_RECEIPT_BYTES = 16 * 1024
MAX_METRICS_BYTES = 1024 * 1024
MAX_AGE_SECONDS = 1.5
POLICY_KEYS = {"daemon_id", "container_id", "image_id", "compose_project", "compose_service", "started_at",
               "restart_count", "init_pid", "init_process_start_identity", "network_name", "network_id",
               "endpoint_id", "expected_series"}
BINDING_KEYS = (POLICY_KEYS - {"expected_series"}) | {"ip_address", "subnet", "network_driver", "network_scope", "health"}
GAUGES = ("vllm:num_requests_running", "vllm:num_requests_waiting")
LABEL = re.compile(r'([A-Za-z_][A-Za-z_0-9]*)="((?:\\[\\n"]|[^"\\\n])*)"')
_SOURCE_DIGESTS_AT_IMPORT = {name: hashlib.sha256((Path(__file__).resolve().parent / name).read_bytes()).hexdigest()
                             for name in ("qwen_queue.py", "qwen_observer.py")}


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def observer_code_digest():
    # Immutable release code must be restarted to change this attestation.
    # Never bless already-loaded old code with a later on-disk digest.
    return digest(_SOURCE_DIGESTS_AT_IMPORT)


def text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum and all(32 <= ord(c) <= 126 for c in value)


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ContractError("qwen_policy_unconfigured")
    for name in ("container_id", "network_id", "endpoint_id"):
        if not isinstance(policy[name], str) or not HEX64.fullmatch(policy[name]):
            raise ContractError("qwen_policy_identity_invalid")
    if (not isinstance(policy["image_id"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", policy["image_id"])
            or not text(policy["daemon_id"], 128) or not text(policy["compose_project"], 128)
            or policy["compose_service"] != "vllm"
            or not isinstance(policy["network_name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", policy["network_name"])
            or not isinstance(policy["started_at"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z", policy["started_at"])
            or type(policy["restart_count"]) is not int or policy["restart_count"] < 0
            or type(policy["init_pid"]) is not int or policy["init_pid"] <= 0
            or not text(policy["init_process_start_identity"], 128)):
        raise ContractError("qwen_policy_identity_invalid")
    series = policy["expected_series"]
    if not isinstance(series, list) or not 1 <= len(series) <= 16:
        raise ContractError("qwen_policy_series_unconfigured")
    seen = set()
    for labels in series:
        if (not isinstance(labels, dict) or set(labels) != {"model_name", "engine"}
                or not all(text(v, 256) for v in labels.values())
                or canonical(labels) in seen):
            raise ContractError("qwen_policy_series_invalid")
        seen.add(canonical(labels))
    return policy


def validate_binding(binding, policy):
    validate_policy(policy)
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ContractError("qwen_binding_invalid")
    if any(binding[name] != policy[name] for name in POLICY_KEYS - {"expected_series"}):
        raise ContractError("qwen_binding_not_approved")
    try:
        address = ipaddress.IPv4Address(binding["ip_address"])
        subnet = ipaddress.IPv4Network(binding["subnet"], strict=True)
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise ContractError("qwen_bridge_address_invalid") from None
    if (not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
            or address not in subnet or address in (subnet.network_address, subnet.broadcast_address)
            or not subnet.is_private or binding["network_driver"] != "bridge"
            or binding["network_scope"] != "local" or binding["health"] != "healthy"):
        raise ContractError("qwen_bridge_or_health_unqualified")
    return binding


def parse_labels(raw):
    if not raw or not raw.startswith("{") or not raw.endswith("}"):
        raise ContractError("qwen_gauge_labels_required")
    body, offset, result = raw[1:-1], 0, {}
    while offset < len(body):
        match = LABEL.match(body, offset)
        if match is None or match.group(1) in result:
            raise ContractError("qwen_gauge_labels_invalid")
        result[match.group(1)] = json.loads('"' + match.group(2) + '"')
        offset = match.end()
        if offset == len(body):
            break
        if body[offset] != ",":
            raise ContractError("qwen_gauge_labels_invalid")
        offset += 1
        while offset < len(body) and body[offset].isspace():
            offset += 1
        if offset == len(body):
            raise ContractError("qwen_gauge_labels_invalid")
    return result


def parse_reported_gauges(raw, expected_series):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_METRICS_BYTES:
        raise ContractError("qwen_metrics_size_invalid")
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        raise ContractError("qwen_metrics_encoding_invalid") from None
    wanted = {canonical(labels) for labels in expected_series}
    values = {name: {} for name in GAUGES}
    pattern = re.compile(r"^(vllm:num_requests_running|vllm:num_requests_waiting)(\{.*\})?\s+([^\s]+)\s*$")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not any(re.match(re.escape(name) + r"(?:\{|\s|$)", line) for name in GAUGES):
            continue
        match = pattern.fullmatch(line)
        if match is None:
            # Includes explicit sample timestamps and exemplars: no silently
            # re-stamping old samples as a new observation.
            raise ContractError("qwen_metrics_format_or_timestamp_unqualified")
        name, label_text, number_text = match.groups()
        key = canonical(parse_labels(label_text))
        try:
            if len(number_text) > 64:
                raise ValueError()
            number = Decimal(number_text)
            valid = number.is_finite() and 0 <= number <= 1000000 and number == number.to_integral_value()
        except (ValueError, InvalidOperation):
            raise ContractError("qwen_gauge_count_invalid") from None
        if key not in wanted or key in values[name]:
            raise ContractError("qwen_gauge_series_unapproved_or_duplicate")
        if not valid:
            raise ContractError("qwen_gauge_count_invalid")
        values[name][key] = int(number)
    if any(set(found) != wanted for found in values.values()):
        raise ContractError("qwen_matching_gauge_series_missing")
    return {"running": sum(values[GAUGES[0]].values()), "waiting": sum(values[GAUGES[1]].values()),
            "series": sorted(expected_series, key=canonical), "semantics": SEMANTICS}


def validate_receipt(receipt, *, now, boot_id, expected_config_sha256, expected_policy_sha256,
                     observer_identity, observer_uid, expected_code_sha256=None):
    if (not isinstance(expected_config_sha256, str) or not HEX64.fullmatch(expected_config_sha256)
            or not isinstance(expected_policy_sha256, str) or not HEX64.fullmatch(expected_policy_sha256)):
        raise ContractError("qwen_observer_approval_unconfigured")
    keys = {"schema", "status", "sequence", "host_boot_id", "observed_at", "valid_until", "request_started_at",
            "postcheck_at", "published_at", "policy", "policy_sha256", "binding", "binding_sha256",
            "gauges", "response_sha256", "response_bytes", "observer"}
    if not isinstance(receipt, dict) or set(receipt) != keys or receipt.get("schema") != SCHEMA or receipt.get("status") != "observed":
        raise ContractError("qwen_observation_unknown")
    times = [receipt[name] for name in ("request_started_at", "observed_at", "postcheck_at", "published_at", "valid_until")]
    if (not finite(now) or not all(finite(t) for t in times)
            or not times[0] <= times[1] <= times[2] <= times[3] <= now < times[4] <= times[1] + MAX_AGE_SECONDS
            or times[2] - times[0] > MAX_AGE_SECONDS or now - times[0] > MAX_AGE_SECONDS or receipt["host_boot_id"] != boot_id
            or type(receipt["sequence"]) is not int or receipt["sequence"] < 1):
        raise ContractError("qwen_observation_stale_or_other_clock")
    policy, binding = receipt["policy"], receipt["binding"]
    validate_binding(binding, policy)
    if receipt["policy_sha256"] != digest(policy) or receipt["policy_sha256"] != expected_policy_sha256:
        raise ContractError("qwen_policy_digest_mismatch")
    if receipt["binding_sha256"] != digest(binding):
        raise ContractError("qwen_binding_digest_mismatch")
    observer = receipt["observer"]
    expected_observer = {"schema", "uid", "pid", "process_start_identity", "boot_id", "clock_domain",
                         "code_sha256", "config_sha256"}
    if (not isinstance(observer, dict) or set(observer) != expected_observer or observer["schema"] != OBSERVER_SCHEMA
            or observer["uid"] != 0 or type(observer["pid"]) is not int or observer["pid"] <= 0
            or observer["clock_domain"] != CLOCK_DOMAIN or observer["boot_id"] != boot_id
            or observer["config_sha256"] != expected_config_sha256
            or observer["code_sha256"] != (expected_code_sha256 or observer_code_digest())
            or observer_uid(observer["pid"]) != 0
            or observer_identity(observer["pid"]) != observer["process_start_identity"]):
        raise ContractError("qwen_root_observer_attestation_invalid")
    gauges = receipt["gauges"]
    if (not isinstance(gauges, dict) or set(gauges) != {"running", "waiting", "series", "semantics"}
            or gauges["semantics"] != SEMANTICS
            or gauges["series"] != sorted(policy["expected_series"], key=canonical)
            or any(type(gauges[name]) is not int or not 0 <= gauges[name] <= 16000000 for name in ("running", "waiting"))
            or type(receipt["response_bytes"]) is not int or not 0 < receipt["response_bytes"] <= MAX_METRICS_BYTES
            or not isinstance(receipt["response_sha256"], str) or not HEX64.fullmatch(receipt["response_sha256"])):
        raise ContractError("qwen_reported_gauges_invalid")
    return receipt


def root_owned_json(path, *, shared_gid, max_bytes=MAX_RECEIPT_BYTES):
    """Authority files are root-owned with no group/world write at any level."""
    path = Path(path)
    if os.name != "posix" or not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ContractError("root_queue_file_unavailable")
    for parent in path.parents:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ContractError("root_queue_parent_unprotected")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_size > max_bytes
                or not (mode == 0o600 or mode == 0o640 and type(shared_gid) is int and info.st_gid == shared_gid)):
            raise ContractError("root_queue_file_unprotected")
        raw = source.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ContractError("root_queue_receipt_oversize")
    return strict_json(raw)


def process_uid(pid):
    return Path(f"/proc/{pid}").stat().st_uid


class QueueReader:
    def __init__(self, path, *, shared_gid, expected_config_sha256, expected_policy_sha256):
        self.path, self.shared_gid = path, shared_gid
        self.expected_config_sha256, self.expected_policy_sha256 = expected_config_sha256, expected_policy_sha256
        self.code_sha256 = observer_code_digest()

    def read(self, now):
        if self.path is None:
            raise ContractError("qwen_queue_source_unconfigured")
        try:
            receipt = root_owned_json(self.path, shared_gid=self.shared_gid)
            self.validate(receipt, now)
        except (OSError, ValueError, TypeError):
            raise ContractError("qwen_root_receipt_unavailable_or_unqualified") from None
        return receipt

    def validate(self, receipt, now):
        from .resources import current_boot_id, process_start_identity
        return validate_receipt(receipt, now=now, boot_id=current_boot_id(),
                                expected_config_sha256=self.expected_config_sha256,
                                expected_policy_sha256=self.expected_policy_sha256,
                                observer_identity=process_start_identity, observer_uid=process_uid,
                                expected_code_sha256=self.code_sha256)


def source_from_receipt(receipt):
    gauges = receipt["gauges"]
    return {"running": gauges["running"], "waiting": gauges["waiting"],
            "busy": gauges["running"] > 0 or gauges["waiting"] > 0,
            "observed_at": receipt["observed_at"], "started_at": receipt["request_started_at"],
            "valid_until": receipt["valid_until"], "semantics": SEMANTICS,
            "root_receipt_sha256": digest(receipt), "root_receipt": receipt}
