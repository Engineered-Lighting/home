"""HTTPS transport, HA seed, private config and the Frigate zone proposal."""

from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from helpers import FIXTURES
from victoria_ha_bridge import config as config_mod
from victoria_ha_bridge.ha_seed import default_entities, fetch_states, subject_values
from victoria_ha_bridge.transport import DryRunTransport, HttpTransport

SERVICE = Path(__file__).resolve().parents[1]
EXAMPLE = SERVICE / "bridge.example.json"
PROPOSAL = SERVICE / "frigate-zones-den.proposal.yaml"


class FakeResponse:
    def __init__(self, status: int = 202, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def recording_opener(result):
    calls = []

    def opener(request, timeout=None, context=None):
        calls.append({"request": request, "timeout": timeout, "context": context})
        if isinstance(result, Exception):
            raise result
        return result

    return opener, calls


# ── transport ──────────────────────────────────────────────────────────

def test_http_transport_posts_json_with_bearer_token():
    opener, calls = recording_opener(FakeResponse(202))
    url = "https://gateway.example.invalid/api/lighting-brain/v1/homes/victoria/events"
    transport = HttpTransport(url, "producer-token", timeout_s=4, opener=opener)
    body = {"schema": "activity-lighting-events-1", "events": []}

    result = transport.post(body)

    assert result.ok and result.status == 202
    request = calls[0]["request"]
    assert request.full_url == url and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer producer-token"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == body
    assert calls[0]["timeout"] == 4 and calls[0]["context"] is not None


@pytest.mark.parametrize("status,retryable", [(503, True), (429, True), (401, False), (422, False)])
def test_http_errors_split_into_retryable_and_rejected(status, retryable):
    error = urllib.error.HTTPError("https://x/events", status, "err", {}, io.BytesIO(b"detail"))
    opener, _calls = recording_opener(error)
    result = HttpTransport("https://x/events", "t", opener=opener).post({})
    assert (result.ok, result.status, result.retryable) == (False, status, retryable)


def test_unreachable_brain_is_retryable():
    opener, _calls = recording_opener(urllib.error.URLError(ConnectionRefusedError("refused")))
    result = HttpTransport("https://x/events", "t", opener=opener).post({})
    assert not result.ok and result.retryable and result.status is None


def test_dry_run_prints_one_line_per_batch():
    out = io.StringIO()
    assert DryRunTransport(out).post({"schema": "s", "events": [1]}).ok
    assert json.loads(out.getvalue()) == {"schema": "s", "events": [1]}


# ── HA seed ────────────────────────────────────────────────────────────

def test_default_entities_follow_frigate_integration_names():
    assert default_entities("den_oak", {"sofa": "den_sofa"}) == {
        "occupancy": ["sensor.den_oak_person_count", "binary_sensor.den_oak_person_occupancy"],
        "zone:sofa": ["sensor.den_sofa_person_count", "binary_sensor.den_sofa_person_occupancy"],
    }


def test_subject_values_prefers_counts_and_skips_unavailable():
    states = json.loads((FIXTURES / "ha" / "api_states_den.json").read_text(encoding="utf-8"))
    entities = default_entities("den_oak", {"desk": "den_desk", "sofa": "den_sofa"})
    assert subject_values(states, entities) == {
        "occupancy": (True, 1),
        "zone:sofa": (True, 1),
        "zone:desk": (False, 0),  # count sensor unavailable, binary sensor off
    }
    assert subject_values([{"entity_id": "sensor.den_oak_person_count", "state": "unknown"}], entities) == {}


def test_fetch_states_reads_api_states_and_fails_soft():
    payload = (FIXTURES / "ha" / "api_states_den.json").read_bytes()
    opener, calls = recording_opener(FakeResponse(200, payload))
    states = fetch_states("http://ha.example.invalid:8123/", "ha-token", opener=opener)
    assert len(states) == 8
    assert calls[0]["request"].full_url == "http://ha.example.invalid:8123/api/states"
    assert calls[0]["request"].get_header("Authorization") == "Bearer ha-token"

    opener, _calls = recording_opener(urllib.error.URLError("down"))
    assert fetch_states("http://ha.example.invalid:8123", "t", opener=opener) == []


# ── config ─────────────────────────────────────────────────────────────

@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setenv("LIGHTING_BRAIN_PRODUCER_TOKEN", "brain-token")
    monkeypatch.setenv("BRIDGE_MQTT_PASSWORD", "mqtt-password")
    monkeypatch.setenv("BRIDGE_HA_TOKEN", "ha-token")


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_config_parses_with_contract_defaults(secrets):
    settings = config_mod.parse(example())
    b = settings.bridge
    assert (b.home, b.room, b.camera, b.zones) == ("victoria", "den", "den_oak", {})
    assert (b.timing.state_heartbeat_s, b.timing.state_ttl_s) == (30, 90)
    assert (b.timing.health_heartbeat_s, b.timing.health_ttl_s) == (60, 150)
    assert settings.events_url.endswith("/api/lighting-brain/v1/homes/victoria/events")
    assert settings.brain_token == "brain-token" and settings.mqtt.password == "mqtt-password"
    assert settings.ha_seed.entities["occupancy"][0] == "sensor.den_oak_person_count"


def test_example_config_holds_no_secrets_or_private_addresses():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
    assert "ts.net" not in text
    raw = example()
    for section in ("brain", "mqtt", "ha_seed"):
        assert not {"token", "password"} & set(raw[section]), section


def test_tokens_can_come_from_files_next_to_the_config(tmp_path, monkeypatch):
    for env in ("LIGHTING_BRAIN_PRODUCER_TOKEN", "BRIDGE_MQTT_PASSWORD", "BRIDGE_HA_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    (tmp_path / "brain.token").write_text("from-file\n", encoding="utf-8")
    raw = example()
    raw["brain"]["token_file"] = "brain.token"
    raw.pop("ha_seed")
    raw["mqtt"].pop("password_file")
    settings = config_mod.parse(raw, base=tmp_path)
    assert settings.brain_token == "from-file" and settings.ha_seed is None


def test_missing_brain_token_fails_unless_dry_run(monkeypatch):
    monkeypatch.delenv("LIGHTING_BRAIN_PRODUCER_TOKEN", raising=False)
    raw = example()
    raw.pop("ha_seed")
    raw["brain"].pop("token_file")
    raw["mqtt"].pop("password_file")
    with pytest.raises(ValueError, match="LIGHTING_BRAIN_PRODUCER_TOKEN"):
        config_mod.parse(raw)
    assert config_mod.parse(raw, dry_run=True).brain_token == ""


@pytest.mark.parametrize("url,message", [
    ("http://gateway.example.invalid/api/lighting-brain/v1/homes/victoria/events", "https"),
    ("https://gateway.example.invalid/api/lighting-brain/v1/homes/victoria/state", "events"),
    ("https://gateway.example.invalid/api/lighting-brain/v1/homes/echo/events", "victoria"),
])
def test_events_url_is_checked(secrets, url, message):
    raw = example()
    raw["brain"]["events_url"] = url
    with pytest.raises(ValueError, match=message):
        config_mod.parse(raw)


def test_loopback_http_is_allowed_for_local_testing(secrets):
    raw = example()
    raw["brain"]["events_url"] = "http://127.0.0.1:8790/api/lighting-brain/v1/homes/victoria/events"
    assert config_mod.parse(raw).events_url.startswith("http://127.0.0.1")


# ── zone proposal and scope ────────────────────────────────────────────

def test_zone_proposal_matches_the_bridge_and_is_marked_placeholder():
    text = PROPOSAL.read_text(encoding="utf-8")
    assert "den_oak:" in text
    for zone in ("den_desk", "den_sofa"):
        assert re.search(rf"^\s+{zone}:\s*$", text, re.M), zone
    assert text.count("PLACEHOLDER") >= 2
    assert re.search(r"^\s+stats_interval:\s*60\s*$", text, re.M)
    coordinates = re.findall(r"coordinates:\s*([0-9.,]+)", text)
    assert len(coordinates) == 2
    for coords in coordinates:
        values = [float(v) for v in coords.split(",")]
        assert len(values) % 2 == 0 and len(values) >= 6
        assert all(0.0 <= v <= 1.0 for v in values)
    # The README tells the owner which bridge zones to switch on afterwards.
    assert '"desk": "den_desk"' in (SERVICE / "README.md").read_text(encoding="utf-8")


def test_bridge_never_calls_light_or_service_apis():
    source = "\n".join(p.read_text(encoding="utf-8") for p in (SERVICE / "victoria_ha_bridge").glob("*.py"))
    assert "/api/services" not in source
    assert not re.search(r"\blight\.", source)
    assert "/set" not in source  # no Frigate or MQTT command topics
    assert "publish(" not in source
