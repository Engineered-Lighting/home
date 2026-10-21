"""State document, SSE framing, config loading and the loopback HTTP API."""
import http.client
import json
import threading

import pytest
from conftest import (EXAMPLE_GEOMETRY, F01, F02, F03, F04, NOON, ROOT, FakeClock, activity, batch, occupancy,
                      reading_scene, test_settings, zone)

from lighting_brain.brain import Brain, sse_frame, stream_frames
from lighting_brain.geometry import load_geometry, parse_geometry
from lighting_brain.server import authenticate, load_tokens, parse_tokens, serve
from lighting_brain.settings import load_settings

TOKEN_HA = "test-token-ha-bridge-000000000001"
TOKEN_JETSON = "test-token-jetson-loop-0000000002"
TOKENS = {"schema": "lighting-brain-producer-tokens-1", "producers": [
    {"producer": "ha", "token": TOKEN_HA, "homes": ["victoria"], "types": ["occupancy", "zone", "health"]},
    {"producer": "jetson", "token": TOKEN_JETSON},  # no home/type scoping
]}


# ------------------------------------------------------------ state document


def test_state_matches_the_contract_shape(brain, clock):
    brain.ingest("victoria", batch(*reading_scene(NOON)))
    clock.tick(4)
    brain.ingest("victoria", batch(activity(NOON + 4)))
    state = brain.state("victoria")
    assert set(state) == {"schema", "home", "room", "generated_at", "brain", "mode", "why", "occupancy", "activity",
                          "fixtures", "decisions", "cost", "inputs", "settings_version",
                          "state_revision", "decision_id", "reading_evidence"}
    assert state["schema"] == "activity-lighting-state-1"
    assert (state["home"], state["room"], state["brain"]) == ("victoria", "den", "online")
    assert state["generated_at"] == NOON + 4 and state["settings_version"] == "v1.1"
    assert state["mode"] == "reading" and state["why"] == "Reading at sofa · spotlight on"
    assert state["occupancy"] == {"present": True, "people_count": 1, "zones": {"sofa": False, "desk": False}}
    assert state["activity"] == {"label": "reading", "confidence": 0.8, "age_s": 0.0, "source": "jev"}
    assert [f["fixture_id"] for f in state["fixtures"]] == [F01, F02, F03, F04]
    for f in state["fixtures"]:
        assert set(f) == {"fixture_id", "ambient", "spot", "paused_until", "pause_reason"}
        assert set(f["ambient"]) == {"radial_level", "down_level", "cct_k", "fade_s"}
        assert set(f["spot"]) == {"on", "level", "cct_k", "target_m", "fade_s"}
    lit = state["fixtures"][0]["spot"]
    assert lit["on"] and len(lit["target_m"]) == 3
    off = state["fixtures"][2]
    assert off["ambient"]["radial_level"] == 0.1  # far from fixture 03: the proximity floor
    assert off["spot"]["on"] is False and off["spot"]["target_m"] is None
    assert state["cost"] == {"jev_calls_last_hour": 0, "est_usd_last_hour": None}
    assert state["inputs"] == {"occupancy_age_s": 4.0, "activity_age_s": 0.0, "pose_age_s": 4.0,
                               "position_age_s": 4.0,
                               "at_sofa": True,
                               "health": {"jev": True, "deep_stack": True, "frigate": True, "camera": True}}
    json.dumps(state, allow_nan=False)


def test_unknown_home_is_404(brain):
    from lighting_brain.inputs import EventError

    with pytest.raises(EventError) as info:
        brain.state("la")
    assert info.value.status == 404


def test_version_moves_on_change_not_on_age(brain, clock):
    v0 = brain.version
    clock.tick(5)
    brain.tick()
    assert brain.version == v0  # only generated_at / ages moved
    brain.ingest("victoria", batch(occupancy(clock()), zone(clock())))
    assert brain.version == v0 + 1


# ------------------------------------------------------------ SSE framing


def test_sse_frame_format():
    frame = sse_frame({"why": "Reading at sofa · spotlight on", "nested": {"a": [1, 2]}}, 7)
    text = frame.decode("utf-8")
    assert text.endswith("\n\n") and text.count("\n\n") == 1
    lines = text.rstrip("\n").split("\n")
    assert lines[0] == "id: 7" and lines[1] == "event: state"
    assert lines[2].startswith("data: ") and len(lines) == 3  # one data line, JSON has no raw newlines
    assert json.loads(lines[2][len("data: "):])["why"] == "Reading at sofa · spotlight on"


def test_sse_frame_escapes_newlines_inside_strings():
    frame = sse_frame({"why": "line one\nline two"}, 1).decode("utf-8")
    assert frame.count("\n") == 4  # id, event, data, blank


def test_stream_pushes_on_change_and_on_period(brain, clock):
    waits = []

    def fake_wait(version, timeout):  # no sleeping: record the wait, then return
        waits.append((version, timeout))
        if len(waits) == 1:
            clock.tick(1)  # past the push interval
            brain.ingest("victoria", batch(occupancy(clock()), zone(clock())))  # a change
        return True

    stop = threading.Event()
    frames = stream_frames(brain, 5.0, stop, wait=fake_wait)
    first = json.loads(next(frames).decode().split("data: ", 1)[1])
    second = json.loads(next(frames).decode().split("data: ", 1)[1])
    third = next(frames)
    assert first["mode"] == "off" and second["mode"] == "degraded_ambient"
    assert third.startswith(b"id: 3\n")
    assert [timeout for _, timeout in waits] == [5.0, 5.0]
    assert waits[1][0] == waits[0][0] + 1  # the second wait starts from the new version
    stop.set()
    with pytest.raises(StopIteration):
        next(frames)


def test_wait_for_change_returns_at_once_after_a_change(brain, clock):
    version = brain.version
    clock.tick(1)
    brain.ingest("victoria", batch(occupancy(clock()), zone(clock())))
    assert brain.wait_for_change(version, timeout=0) is True
    assert brain.wait_for_change(brain.version, timeout=0) is False


# ------------------------------------------------------------ config


def test_tokens(tmp_path):
    producers = parse_tokens(TOKENS)
    assert authenticate(f"Bearer {TOKEN_HA}", producers).name == "ha"
    assert authenticate(f"Bearer {TOKEN_JETSON}", producers).types is None
    assert authenticate("Bearer wrong-token-value-00000000000", producers) is None
    assert authenticate(TOKEN_HA, producers) is None  # scheme required
    assert authenticate(None, producers) is None
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(TOKENS))
    assert len(load_tokens(str(path))) == 2
    assert len(load_tokens(None, json.dumps(TOKENS))) == 2
    assert load_tokens() == []
    with pytest.raises(ValueError):
        parse_tokens({"schema": "lighting-brain-producer-tokens-1", "producers": [{"producer": "x", "token": "short"}]})
    with pytest.raises(ValueError):
        parse_tokens({"schema": "lighting-brain-producer-tokens-1",
                      "producers": [{"producer": "x", "token": TOKEN_HA, "types": ["weather"]}]})


def test_example_files_parse():
    assert load_geometry(EXAMPLE_GEOMETRY).synthetic is True
    assert parse_tokens(json.loads((ROOT / "config" / "tokens.example.json").read_text()))
    settings = load_settings(ROOT / "config" / "settings.example.json")
    assert settings.settings_version == "v1.1"


def test_settings_refuse_unknown_fields(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"ambient_levle": 0.4}))
    with pytest.raises(ValueError, match="unknown settings"):
        load_settings(path)
    with pytest.raises(ValueError):
        test_settings(occupied_level=1.5)
    with pytest.raises(ValueError):
        test_settings(prox_near_m=3.0, prox_far_m=3.0)
    with pytest.raises(ValueError):
        test_settings(night_start="25:00")


def test_geometry_validation():
    base = json.loads(EXAMPLE_GEOMETRY.read_text())
    missing = {**base, "fixtures": base["fixtures"][:3]}
    with pytest.raises(ValueError, match="missing contract fixtures"):
        parse_geometry(missing)
    bad_zone = {**base, "zones": {"kitchen": {"near_fixtures": []}}}
    with pytest.raises(ValueError, match="unknown zone"):
        parse_geometry(bad_zone)
    bad_ref = {**base, "zones": {"sofa": {"near_fixtures": ["nope"]}}}
    with pytest.raises(ValueError, match="unknown fixtures"):
        parse_geometry(bad_ref)


# ------------------------------------------------------------ HTTP (loopback)


@pytest.fixture
def server(geometry):
    clock = FakeClock(NOON)
    brain = Brain(geometry, test_settings(stream_period_s=60.0, tick_s=60.0), clock)
    srv = serve(brain, parse_tokens(TOKENS), "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv, clock
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read() or b"null")
    conn.close()
    return response.status, data


BASE = "/api/lighting-brain/v1/homes/victoria"


def test_http_events_auth_and_validation(server):
    srv, clock = server
    body = batch(occupancy(NOON), zone(NOON))
    assert call(srv, "POST", f"{BASE}/events", body)[0] == 401
    assert call(srv, "POST", f"{BASE}/events", body, token="not-a-real-token-000000000000")[0] == 401
    status, data = call(srv, "POST", f"{BASE}/events", body, token=TOKEN_HA)
    assert (status, data) == (200, {"ok": True, "accepted": 2, "ignored": []})
    status, data = call(srv, "POST", f"{BASE}/events", batch(activity(NOON)), token=TOKEN_HA)
    assert status == 403 and data["error"] == "producer_type_forbidden"
    unknown = batch({**occupancy(NOON), "type": "weather"})
    status, data = call(srv, "POST", f"{BASE}/events", unknown, token=TOKEN_JETSON)
    assert status == 422 and data["error"] == "unknown_event_type"
    status, data = call(srv, "POST", "/api/lighting-brain/v1/homes/la/events", body, token=TOKEN_HA)
    assert status == 403
    status, data = call(srv, "POST", "/api/lighting-brain/v1/homes/la/events", body, token=TOKEN_JETSON)
    assert status == 404
    expired = call(srv, "POST", f"{BASE}/events", batch(occupancy(NOON - 100)), token=TOKEN_HA)[1]
    assert expired["ignored"] == [{"index": 0, "type": "occupancy", "reason": "expired"}]


def test_http_state_overrides_and_health(server):
    srv, clock = server
    call(srv, "POST", f"{BASE}/events", batch(occupancy(NOON), zone(NOON)), token=TOKEN_HA)
    status, state = call(srv, "GET", f"{BASE}/state")
    assert status == 200 and state["schema"] == "activity-lighting-state-1"
    override = {"schema": "activity-lighting-override-1", "fixture_id": F02, "reason": "change", "at": NOON}
    status, data = call(srv, "POST", f"{BASE}/overrides", override)
    assert (status, data) == (200, {"ok": True, "fixture_id": F02, "paused_until": NOON + 1800})
    state = call(srv, "GET", f"{BASE}/state")[1]
    assert state["fixtures"][1]["paused_until"] == NOON + 1800
    assert call(srv, "POST", f"{BASE}/overrides", {**override, "reason": "because"})[0] == 422
    assert call(srv, "POST", f"{BASE}/overrides", {**override, "fixture_id": "nope"})[0] == 422
    assert call(srv, "DELETE", f"{BASE}/overrides/{F02}") == (200, {"ok": True, "fixture_id": F02, "cleared": True})
    assert call(srv, "DELETE", f"{BASE}/overrides/{F02}")[1]["cleared"] is False
    assert call(srv, "DELETE", f"{BASE}/overrides/nope")[0] == 404
    assert call(srv, "GET", f"{BASE}/nothing")[0] == 404
    assert call(srv, "GET", "/api/lighting-brain/v1/homes/la/state")[0] == 404
    status, health = call(srv, "GET", "/healthz")
    assert status == 200 and health["ok"] and health["synthetic_geometry"] is True


def test_http_bad_json(server):
    srv, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("POST", f"{BASE}/events", body=b"{not json", headers={"Authorization": f"Bearer {TOKEN_HA}"})
    response = conn.getresponse()
    assert response.status == 400 and json.loads(response.read())["error"] == "invalid_json"
    conn.close()


def read_frame(response):
    lines = []
    while True:
        line = response.fp.readline().decode("utf-8")
        if line in ("\n", ""):
            if lines:
                return lines
            continue
        lines.append(line.rstrip("\n"))


def test_http_stream_pushes_state_on_change(server):
    srv, clock = server
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("GET", f"{BASE}/stream")
    response = conn.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/event-stream")
    assert read_frame(response) == ["retry: 3000"]
    first = read_frame(response)
    assert first[0] == "id: 1" and first[1] == "event: state"
    assert json.loads(first[2][6:])["mode"] == "off"
    # A change wakes the stream at once (the period is 60 s in this test).
    clock.tick(1)  # past the 0.25 s push interval
    call(srv, "POST", f"{BASE}/events", batch(occupancy(NOON), zone(NOON)), token=TOKEN_HA)
    second = read_frame(response)
    assert second[0] == "id: 2" and json.loads(second[2][6:])["occupancy"]["present"] is True
    conn.close()


def test_refuses_non_loopback_bind(geometry):
    brain = Brain(geometry, test_settings(), FakeClock())
    with pytest.raises(SystemExit):
        serve(brain, [], "0.0.0.0", 0)
