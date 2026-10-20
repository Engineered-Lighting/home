"""Strict static service provisioning; credentials are fixed local files.

Configuration is JSON with exactly the fields described by load_config. No
URLs, arbitrary module names, inline credentials or admission override exist.
Secrets live beside it in credentials/{camera,frigate,gateway,mqtt-username,
mqtt-password}.txt; trust files are named files beneath trust/. This loader
performs no network operation and never creates state stores.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from .gateway_client import GatewayClientConfig
from .health import HealthProbeConfig
from .mqtt_transport import MqttConfig
from .observations import SourceBinding
from .runtime import RuntimeSettings
from .timing_client import TimingClientConfig


@dataclass(frozen=True)
class ServiceConfig:
    expected_hostname: str
    state_directory: str
    gateway: GatewayClientConfig
    mqtt: MqttConfig = field(repr=False)
    health: HealthProbeConfig
    timing: TimingClientConfig
    binding: SourceBinding
    runtime: RuntimeSettings
    journal_max_records: int
    free_space_floor_bytes: int


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError("provisioning fields do not match the versioned schema")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provisioning field")
        result[key] = value
    return result


def protected_file(path, *, secret=False, enforce_linux=False):
    """Inspect original path components before resolving any symlink."""
    path = Path(path).absolute()
    if enforce_linux and sys.platform != "linux":
        raise ValueError("production file ownership checks require Linux")
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("provisioning symlinks are not permitted")
        if enforce_linux:
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                raise ValueError("provisioning path is not protected")
            if item == path and secret and info.st_mode & 0o077:
                raise ValueError("credential file must be owner-only")
    if not path.is_file():
        raise ValueError("provisioning must be a regular file")
    return path


def load_config(path, *, enforce_linux=False):
    path = protected_file(path, enforce_linux=enforce_linux)
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("provisioning exceeds limit")
    value = json.loads(raw, object_pairs_hook=_pairs)
    _keys(value, "version expected_hostname state_directory owner_id observation_source_instance device_id "
                 "configuration_revision max_output_tokens gateway mqtt health timing journal")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported service configuration")
    hostname = value["expected_hostname"]
    if (not isinstance(hostname, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", hostname) is None
            or hostname.lower() in ("engineeredlightingserver1", "home-app")):
        raise ValueError("explicit non-quarantined Victoria hostname required")
    state = value["state_directory"]
    if (not isinstance(state, str) or not PurePosixPath(state).is_absolute()
            or ".." in PurePosixPath(state).parts or "\\" in state or state in ("/", "/tmp", "/run")):
        raise ValueError("dedicated absolute Linux state directory required")
    base = path.parent
    def credential(name):
        filename = protected_file(base / "credentials" / (name + ".txt"), secret=True,
                                  enforce_linux=enforce_linux)
        with filename.open("rb") as source:
            data = source.read(4099)
        if len(data) > 4098:
            raise ValueError("credential exceeds limit")
        return data.decode("utf-8").rstrip("\r\n")
    def trust(name, *, secret=False):
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None:
            raise ValueError("trust files must be names within the provisioned trust directory")
        return protected_file(base / "trust" / name, secret=secret, enforce_linux=enforce_linux)
    gateway, mqtt, health, timing, journal = (value[key] for key in ("gateway", "mqtt", "health", "timing", "journal"))
    _keys(gateway, "hostname port ca_file client_certificate client_key")
    _keys(mqtt, "hostname ca_file client_id event_capacity")
    _keys(health, "frigate_hostname frigate_port ca_file source_instance detector_id max_stats_age_seconds "
                 "max_stats_collection_lag_seconds policy_revision camera_host_identity camera_boot_id "
                 "pi_host_identity pi_boot_id la_host_identity la_boot_id")
    _keys(timing, "authority_id uid gid")
    _keys(journal, "max_records free_space_floor_bytes")
    if (type(journal["max_records"]) is not int or not 1 <= journal["max_records"] <= 1_000_000
            or type(journal["free_space_floor_bytes"]) is not int or journal["free_space_floor_bytes"] < 0):
        raise ValueError("explicit journal capacity and storage floor required")
    runtime = RuntimeSettings(value["device_id"], value["configuration_revision"], value["max_output_tokens"])
    registry = {"den_oak": "den"}
    gateway_config = GatewayClientConfig(gateway["hostname"], gateway["port"], trust(gateway["ca_file"]),
                                        credential("gateway"), runtime.device_id, registry, runtime.configuration_revision,
                                        trust(gateway["client_certificate"]) if gateway["client_certificate"] else None,
                                        trust(gateway["client_key"], secret=True) if gateway["client_key"] else None)
    mqtt_config = MqttConfig(mqtt["hostname"], str(trust(mqtt["ca_file"])), credential("mqtt-username"),
                             credential("mqtt-password"), mqtt["client_id"], mqtt["event_capacity"])
    health_config = HealthProbeConfig(**dict(health, ca_file=trust(health["ca_file"]),
                                            camera_bearer=credential("camera"), frigate_bearer=credential("frigate")))
    binding = SourceBinding(value["owner_id"], value["observation_source_instance"], registry)
    return ServiceConfig(hostname, state, gateway_config, mqtt_config, health_config, TimingClientConfig(**timing),
                         binding, runtime, journal["max_records"], journal["free_space_floor_bytes"])
