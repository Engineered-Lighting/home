"""Private common deployment identity and local read-only provenance checks."""
from __future__ import annotations
import hashlib
import json
import os
import re

SCHEMA = "den-ops-deployment-1"
HOSTS = {"la-host", "victoria-jetson"}
EXPECTED_UNITS = {
    "la-host": {("lighting-brain.service", "system"), ("home-web-gateway.service", "system")},
    "victoria-jetson": {("victoria-ha-bridge.service", "user"), ("perception-lab-managed.service", "user"), ("lab-jetson-detector-trial.service", "user")},
}
COMPONENTS = {"la-host": {"brain", "gateway", "ops"}, "victoria-jetson": {"bridge", "lab", "detector", "ops"}}


def execution_identity(value):
    """Exclude systemd's observed PID/timestamps, retaining its exact command.

    A recording roll changes execution metadata even when the executable and
    arguments are unchanged. Unknown serialized command formats fail closed.
    """
    if not isinstance(value, str) or not value:
        return None
    if not value.startswith('{'):
        return value
    if re.search(r'\}\s*;?\s*\{', value):
        return None
    match = re.fullmatch(
        r'\{\s*(path=.+? ; argv\[\]=.+? ; ignore_errors=(?:yes|no))'
        r' ; start_time=[^;]* ; stop_time=[^;]* ; pid=\d+ ; code=[^;]* ; status=[^;]*?\s*\}',
        value, flags=re.DOTALL)
    return match.group(1) if match else None


def digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load(path):
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    validate(doc)
    return doc


def validate(doc):
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(doc.get("deployment_id", ""))):
        raise ValueError("invalid deployment manifest")
    if set(doc.get("hosts", {})) != HOSTS:
        raise ValueError("deployment needs exactly LA and Jetson")
    for name, spec in doc["hosts"].items():
        if not isinstance(spec.get("components"), dict) or not spec["components"] or any(not isinstance(v, str) or not v for v in spec["components"].values()):
            raise ValueError("actual component release identities required")
        if not COMPONENTS[name] <= set(spec["components"]):
            raise ValueError("required component identities missing")
        for key in ("monitor_config_sha256", "soak_config_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(spec.get(key, ""))):
                raise ValueError("missing config digest: " + key)
        artifacts = spec.get("artifacts")
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 4096:
            raise ValueError("deployment needs a bounded source/config artifact inventory")
        paths = []
        for artifact in artifacts:
            if not os.path.isabs(artifact.get("path", "")) or not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256", ""))):
                raise ValueError("artifact needs an absolute deployed path and SHA256")
            if artifact.get("component") not in spec["components"]:
                raise ValueError("artifact component binding missing")
            paths.append(artifact["path"])
        if not set(spec["components"]) <= {a["component"] for a in artifacts}:
            raise ValueError("every component needs deployed artifact hashes")
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate artifact")
        units = spec.get("units")
        if not isinstance(units, list) or not units:
            raise ValueError("deployment unit inventory missing")
        if len({(u.get("name"), u.get("scope")) for u in units}) != len(units):
            raise ValueError("duplicate deployment unit")
        if {(u.get("name"), u.get("scope")) for u in units} != EXPECTED_UNITS[name]:
            raise ValueError("required unit inventory/scope differs")
        for unit in units:
            if unit.get("scope") not in ("user", "system") or not str(unit.get("name", "")).endswith(".service") or not os.path.isabs(unit.get("working_directory", "")) or not unit.get("exec_start"):
                raise ValueError("unit scope and actual execution identity required")


def verify(doc, cfg, monitor_cfg, host):
    """Verify local artifacts and configurations; unit execution identity is sampled separately."""
    validate(doc)
    spec = doc["hosts"][cfg["host"]]
    errors = []
    if digest(cfg) != spec["soak_config_sha256"] or digest(monitor_cfg) != spec["monitor_config_sha256"]:
        errors.append("configuration digest mismatch")
    actual_units = {(u["name"], u.get("scope", "system")) for u in cfg.get("units", [])}
    if actual_units != {(u["name"], u["scope"]) for u in spec["units"]}:
        errors.append("configured unit scope/inventory mismatch")
    for artifact in spec["artifacts"]:
        try:
            observed = host.file_sha256(artifact["path"])
        except (OSError, ValueError):
            observed = None
        if observed != artifact["sha256"]:
            errors.append("artifact missing/changed: " + artifact["path"])
    return {"ok": not errors, "errors": errors, "manifest_sha256": digest(doc), "deployment_id": doc["deployment_id"]}
