"""Private JSON config. Secrets live in separate files or environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .core import BridgeConfig, Timing
from .ha_seed import default_entities

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


@dataclass
class MqttSettings:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str = "victoria-ha-bridge"
    keepalive_s: int = 30


@dataclass
class HaSeedSettings:
    url: str
    token: str
    entities: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Settings:
    bridge: BridgeConfig
    events_url: str
    brain_token: str
    brain_timeout_s: float
    brain_ca_file: str | None
    mqtt: MqttSettings
    ha_seed: HaSeedSettings | None


def _secret(section: dict[str, Any], key: str, env: str, *, required: bool, base: Path) -> str | None:
    """`<key>_file` (path, relative to the config) or an environment variable."""
    value = os.environ.get(env, "").strip()
    path = section.get(f"{key}_file")
    if not value and path:
        value = (base / path).read_text(encoding="utf-8").strip()
    if required and not value:
        raise ValueError(f"missing secret: set {env} or {key}_file")
    return value or None


def _events_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK):
        raise ValueError("brain.events_url must be https (plain http only on loopback)")
    if not parsed.path.endswith("/events"):
        raise ValueError("brain.events_url must be the contract's .../homes/{home}/events route")
    return url


def parse(raw: dict[str, Any], *, base: Path = Path("."), dry_run: bool = False) -> Settings:
    frigate = raw.get("frigate", {})
    timing = Timing(**raw.get("timing", {}))
    bridge = BridgeConfig(
        home=raw.get("home", "victoria"),
        room=raw.get("room", "den"),
        source=raw.get("source", "victoria-ha-bridge"),
        topic_prefix=frigate.get("topic_prefix", "frigate"),
        camera=frigate.get("camera", "den_oak"),
        object_label=frigate.get("object", "person"),
        zones=dict(frigate.get("zones", {})),
        silence_timeout_s=float(frigate.get("silence_timeout_s", 150)),
        timing=timing,
    )

    brain = raw["brain"]
    events_url = _events_url(brain["events_url"])
    if f"/homes/{bridge.home}/" not in events_url:
        raise ValueError(f"brain.events_url must name home {bridge.home!r}")

    mqtt_raw = raw["mqtt"]
    mqtt = MqttSettings(
        host=mqtt_raw["host"],
        port=int(mqtt_raw.get("port", 1883)),
        username=mqtt_raw.get("username"),
        password=_secret(mqtt_raw, "password", "BRIDGE_MQTT_PASSWORD", required=False, base=base),
        client_id=mqtt_raw.get("client_id", "victoria-ha-bridge"),
        keepalive_s=int(mqtt_raw.get("keepalive_s", 30)),
    )

    ha_seed = None
    ha_raw = raw.get("ha_seed")
    if ha_raw:
        entities = default_entities(bridge.camera, bridge.zones, bridge.object_label)
        entities.update({k: list(v) for k, v in ha_raw.get("entities", {}).items()})
        ha_seed = HaSeedSettings(
            url=ha_raw["url"],
            token=_secret(ha_raw, "token", "BRIDGE_HA_TOKEN", required=True, base=base),
            entities=entities,
        )

    return Settings(
        bridge=bridge,
        events_url=events_url,
        brain_token=_secret(brain, "token", "LIGHTING_BRAIN_PRODUCER_TOKEN", required=not dry_run, base=base) or "",
        brain_timeout_s=float(brain.get("timeout_s", 5)),
        brain_ca_file=brain.get("ca_file"),
        mqtt=mqtt,
        ha_seed=ha_seed,
    )


def load(path: str | Path, *, dry_run: bool = False) -> Settings:
    path = Path(path)
    return parse(json.loads(path.read_text(encoding="utf-8")), base=path.parent, dry_run=dry_run)
