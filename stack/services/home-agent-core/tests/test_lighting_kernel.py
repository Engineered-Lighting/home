import asyncio
import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.lighting_contract import Clarification, Inventory, LightingProposalRequest, LightOperation
from app.lighting_edge_client import (EXECUTE_PATH, INVENTORY_PATH, OUTCOME_PATH, SIGNATURE_HEADER,
                                      EdgeLightingClient, LightingRefused, OutcomeUnknown, sign)
from app.lighting_resolution import light_key, resolve

SECRET = b"s" * 32
REVISION = "a" * 64
NOW = 1_800_000_000_000


def request(**values):
    return LightingProposalRequest(**{"operation_id": uuid.uuid4(), "sites": ("victoria",), "targets": "all",
                                      "operation": "off", **values})


def inventory(site="victoria", lights=None):
    return Inventory(site_id=site, revision=REVISION, lights=tuple(lights if lights is not None else [
        {"entity_id": "light.kitchen", "name": "Kitchen", "state": "on", "brightness_pct": 50, "dimmable": True},
        {"entity_id": "light.porch", "name": "Front Porch", "state": "off", "dimmable": False},
        {"entity_id": "light.den", "name": "Den", "state": "unsupported", "dimmable": False},
        {"entity_id": "light.garage", "name": "Garage", "state": "unavailable", "dimmable": True},
    ]))


# Contract -----------------------------------------------------------------

@pytest.mark.parametrize("values", [
    {"sites": ()}, {"sites": ("victoria", "victoria")}, {"sites": ("paris",)},
    {"operation": "brightness"}, {"operation": "on", "brightness": 50},
    {"operation": "brightness", "brightness": 0}, {"operation": "brightness", "brightness": 101},
    {"operation": "toggle"}, {"targets": ()}, {"targets": ("Kitchen",)}, {"targets": ("kitchen", "kitchen")},
    {"targets": ("scene.evening",)}, {"targets": tuple(f"l{i}" for i in range(9))},
])
def test_proposal_requests_are_exact(values):
    with pytest.raises(ValidationError):
        request(**values)


def test_contract_rejects_non_light_entities_and_extra_fields():
    with pytest.raises(ValidationError):
        LightOperation(site_id="echo", entity_id="lock.front", name="Front", operation="off")
    with pytest.raises(ValidationError):
        LightOperation(site_id="echo", entity_id="light.a", name="A", operation="off", scene="x")
    with pytest.raises(ValidationError):
        inventory(lights=[{"entity_id": "light.a", "name": "A", "state": "on", "dimmable": True}] * 2)


# Resolution ---------------------------------------------------------------

@pytest.mark.parametrize("text,key", [
    ("The Kitchen Lights", "kitchen"), ("light.front_porch", "front porch"), ("front-porch lamp", "front porch"),
    ("  Den   light ", "den"),
])
def test_light_keys_normalize_names(text, key):
    assert light_key(text) == key


def test_all_lights_skips_groups_and_unavailable_lights():
    result = resolve(request(), {"victoria": inventory()})
    assert [op.entity_id for op in result] == ["light.kitchen", "light.porch"]
    assert all(op.operation == "off" and op.site_id == "victoria" for op in result)


def test_all_lights_brightness_uses_only_dimmable_lights():
    result = resolve(request(operation="brightness", brightness=30), {"victoria": inventory()})
    assert [(op.entity_id, op.brightness) for op in result] == [("light.kitchen", 30)]


def test_named_lights_resolve_by_name_or_entity():
    result = resolve(request(targets=("front porch", "kitchen light")), {"victoria": inventory()})
    assert [op.entity_id for op in result] == ["light.porch", "light.kitchen"]


def test_both_homes_produce_separate_operations_per_home():
    echo = inventory("echo", [{"entity_id": "light.kitchen", "name": "Kitchen", "state": "off", "dimmable": True}])
    result = resolve(request(sites=("echo", "victoria"), targets=("kitchen",), operation="on"),
                     {"echo": echo, "victoria": inventory()})
    assert [(op.site_id, op.entity_id) for op in result] == [("echo", "light.kitchen"), ("victoria", "light.kitchen")]


@pytest.mark.parametrize("values,reason", [
    ({"targets": ("attic",)}, "unknown_light"),
    ({"targets": ("den",)}, "unknown_light"),          # groups are never matched
    ({"targets": ("garage",)}, "light_unavailable"),
    ({"targets": ("front porch",), "operation": "brightness", "brightness": 20}, "not_dimmable"),
])
def test_uncertain_requests_become_clarifications(values, reason):
    result = resolve(request(**values), {"victoria": inventory()})
    assert type(result) is Clarification and result.reason == reason and result.site_id == "victoria"


def test_ambiguous_names_list_candidates():
    lights = [{"entity_id": "light.lamp_one", "name": "Lamp", "state": "on", "dimmable": True},
              {"entity_id": "light.lamp_two", "name": "Lamp", "state": "off", "dimmable": True}]
    result = resolve(request(targets=("lamp",)), {"victoria": inventory(lights=lights)})
    assert result.reason == "ambiguous_light" and result.candidates == ("Lamp", "Lamp")


def test_no_eligible_lights_and_missing_inventories():
    assert resolve(request(), {"victoria": inventory(lights=[])}).reason == "no_eligible_lights"
    with pytest.raises(ValueError):
        resolve(request(), {"echo": inventory("echo")})


def test_more_than_sixteen_operations_is_refused():
    lights = [{"entity_id": f"light.l{i}", "name": f"L{i}", "state": "on", "dimmable": True} for i in range(9)]
    result = resolve(request(sites=("echo", "victoria")),
                     {"echo": inventory("echo", lights), "victoria": inventory(lights=lights)})
    assert result.reason == "too_many_lights"


# Signed client --------------------------------------------------------------

class Endpoint:
    """Records requests, checks signatures, and returns scripted replies."""

    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def __call__(self, http_request: httpx.Request) -> httpx.Response:
        body = http_request.content
        expected = hmac.new(SECRET, b"home-agent-lighting:v1\n" + http_request.url.path.encode() + b"\n" + body,
                            hashlib.sha256).hexdigest()
        assert http_request.headers[SIGNATURE_HEADER] == expected
        value = json.loads(body)
        self.seen.append((http_request.url.path, value))
        return self.reply(http_request.url.path, value)


def client(reply):
    endpoint = Endpoint(reply)
    http = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    return EdgeLightingClient(site_id="victoria", base_url="https://home-app.example.ts.net:10001",
                              secret=SECRET, client=http, now_ms=lambda: NOW), endpoint


OPERATION = LightOperation(site_id="victoria", entity_id="light.kitchen", name="Kitchen", operation="off")


def execute(edge, **kwargs):
    return asyncio.run(edge.execute(OPERATION, request_id=uuid.UUID(int=1), index=0, revision=REVISION,
                                    expires_at_ms=NOW + 60_000, **kwargs))


def test_client_requires_https_origin_and_dedicated_secret():
    http = httpx.AsyncClient()
    for url in ("http://ha.example", "https://user:pw@ha.example", "https://ha.example/api", "https://ha.example?x=1"):
        with pytest.raises(ValueError):
            EdgeLightingClient(site_id="victoria", base_url=url, secret=SECRET, client=http)
    with pytest.raises(ValueError):
        EdgeLightingClient(site_id="victoria", base_url="https://ha.example", secret=b"short", client=http)
    assert "sss" not in repr(EdgeLightingClient(site_id="echo", base_url="https://ha.example", secret=SECRET, client=http))


def test_execute_sends_the_frozen_operation_signed():
    edge, endpoint = client(lambda path, v: httpx.Response(200, json={
        "version": 1, "request_id": v["request_id"], "operation_index": v["operation_index"], "status": "succeeded"}))
    assert execute(edge) == "succeeded"
    path, value = endpoint.seen[0]
    assert path == EXECUTE_PATH
    assert value == {"version": 1, "site_id": "victoria", "issued_at": NOW, "revision": REVISION,
                     "request_id": str(uuid.UUID(int=1)), "operation_index": 0, "entity_id": "light.kitchen",
                     "operation": "off", "brightness": None, "expires_at": NOW + 60_000}


@pytest.mark.parametrize("reply", [
    lambda path, v: httpx.Response(503, json={"error": "lighting_unavailable"}),
    lambda path, v: httpx.Response(409, json={"error": "replayed_request"}),
    lambda path, v: httpx.Response(200, json={"version": 1, "request_id": str(uuid.UUID(int=2)),
                                               "operation_index": 0, "status": "succeeded"}),
    lambda path, v: httpx.Response(200, text="not json"),
])
def test_uncertain_execute_replies_are_outcome_unknown(reply):
    edge, _ = client(reply)
    with pytest.raises(OutcomeUnknown):
        execute(edge)


def test_network_failure_during_execute_is_outcome_unknown():
    def boom(path, value):
        raise httpx.ConnectError("reset")
    edge, _ = client(boom)
    with pytest.raises(OutcomeUnknown):
        execute(edge)


@pytest.mark.parametrize("status,code", [(400, "operation_not_allowed"), (409, "allowlist_changed"),
                                         (409, "request_withdrawn"), (401, "signature_invalid")])
def test_definite_refusals_are_reported_as_refused(status, code):
    edge, _ = client(lambda path, v: httpx.Response(status, json={"error": code}))
    with pytest.raises(LightingRefused) as refused:
        execute(edge)
    assert refused.value.code == code


def test_execute_rejects_operations_for_another_home():
    edge, endpoint = client(lambda path, v: httpx.Response(500))
    other = LightOperation(site_id="echo", entity_id="light.kitchen", name="Kitchen", operation="off")
    with pytest.raises(TypeError):
        asyncio.run(edge.execute(other, request_id=uuid.UUID(int=1), index=0, revision=REVISION,
                                 expires_at_ms=NOW + 60_000))
    assert endpoint.seen == []


def test_outcome_and_inventory_are_validated():
    def reply(path, value):
        if path == OUTCOME_PATH:
            return httpx.Response(200, json={"version": 1, "request_id": value["request_id"],
                                             "operation_index": value["operation_index"], "status": "absent"})
        return httpx.Response(200, json=inventory().model_dump())
    edge, endpoint = client(reply)
    assert asyncio.run(edge.outcome(request_id=uuid.UUID(int=1), index=0)) == "absent"
    assert asyncio.run(edge.inventory()).revision == REVISION
    assert [path for path, _ in endpoint.seen] == [OUTCOME_PATH, INVENTORY_PATH]
    wrong_home, _ = client(lambda path, v: httpx.Response(200, json=inventory("echo").model_dump()))
    with pytest.raises(ValueError):
        asyncio.run(wrong_home.inventory())


def test_signature_matches_the_home_assistant_endpoint_vector():
    # The same vector is asserted in ha-config/home_agent_edge/test_lighting.py.
    assert EXECUTE_PATH == "/api/home_agent_edge/lighting/v1/execute"
    assert sign(bytes.fromhex("ab" * 32), EXECUTE_PATH, b'{"version":1}') == "032530b2b4433cff16b0a80d9716dca6291fc15ccfbd40fb5467f296b021147b"
