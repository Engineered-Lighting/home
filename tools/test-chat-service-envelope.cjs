"use strict";
// Production refusal boundary. Authored transports are never contacted.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.resolve(__dirname, "../app/src/home-apartment-data.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));
function fixture() {
  const attempts = [];
  const window = { __HOME_TRAVEL_MODE_STATE: "off" };
  for (const name of ["fetch", "WebSocket", "localStorage", "HomeSpatialViewport", "__hav_haClient"]) Object.defineProperty(window, name, {
    get() { attempts.push(name); throw new Error("Chat refusal must not consult " + name); },
  });
  vm.runInNewContext(source, { window, console: { log() {}, warn() {} } }, { filename: "home-apartment-data.js", timeout: 1000 });
  return { window, attempts, api: window.HomeApartmentData, refuse: window.HomeApartmentData.refuseChatServiceEnvelope };
}
const envelope = (domain = "light", service = "turn_off") => ({ type: "call_service", domain, service,
  service_data: {}, target: { entity_id: ["light.authored"] } });

for (const [domain, service] of [
  ["light", "turn_on"], ["light", "toggle"], ["switch", "turn_on"], ["switch", "toggle"],
  ["homeassistant", "turn_on"], ["homeassistant", "toggle"], ["scene", "turn_on"], ["scene", "apply"],
  ["script", "turn_on"], ["script", "authored_lighting_script"], ["LIGHT", "TURN_ON"],
]) {
  test(domain + "." + service + " refuses before helper, viewport or transport access", async () => {
    const f = fixture();
    await assert.rejects(f.refuse(envelope(domain, service)), /Chat lighting controls are unavailable.*home authority cannot be verified/);
    assert.deepEqual(f.attempts, []);
  });
}

for (const [domain, service] of [["light", "turn_off"], ["switch", "turn_off"], ["homeassistant", "turn_off"],
  ["script", "turn_off"], ["media_player", "media_pause"], ["media_player", "volume_set"],
  ["fan", "turn_off"], ["cover", "close_cover"]]) {
  test(domain + "." + service + " refuses unresolved originating-card context", async () => {
    const f = fixture();
    await assert.rejects(f.refuse(envelope(domain, service)), /originating connection is unverified/);
    assert.deepEqual(f.attempts, []);
  });
}

test("later globals or extra authority/currentness arguments never activate dispatch", async () => {
  const f = fixture(), calls = [], client = { call: async value => { calls.push(value); } };
  f.window.HomeChatAuthority = { siteId: "echo", qualified: true, client };
  for (const request of [envelope("light", "turn_on"), envelope(), envelope("media_player", "media_pause")]) {
    await assert.rejects(f.refuse(request, { client, siteId: "echo", qualified: true, isCurrent: () => true }), /unavailable/);
  }
  assert.equal(f.api.callChatServiceEnvelope, undefined, "no dormant physical admission adapter is exported");
  assert.deepEqual(calls, []); assert.deepEqual(f.attempts, []);
});

test("refusal preserves entity, area, device selectors and separate service data without retaining or serializing them", async () => {
  const f = fixture(), request = envelope("media_player", "volume_set");
  request.target = { entity_id: ["media_player.authored_one", "media_player.authored_two"], area_id: ["authored_area"], device_id: ["authored_device"] };
  request.service_data = { volume_level: 0.2, entity_id: "literal_nested_value", nested: { retained: [1, 2] } };
  request.return_response = true;
  const before = copy(request);
  await assert.rejects(f.refuse(request), /originating connection is unverified/);
  assert.deepEqual(request, before);
  Object.defineProperty(request, "target", { get() { throw new Error("Selectors need not be read to refuse"); } });
  Object.defineProperty(request, "service_data", { get() { throw new Error("Private data need not be read to refuse"); } });
  await assert.rejects(f.refuse(request), /originating connection is unverified/);
  assert.deepEqual(f.attempts, []);
});

test("simulation and rehearsal cannot activate the physical refusal boundary", async () => {
  const f = fixture(); f.window.__SIM_ACTIVE = true; f.window.HomeLiveSceneShadow = { active: true };
  await assert.rejects(f.refuse(envelope("light", "turn_on")), /home authority cannot be verified/);
  await assert.rejects(f.refuse(envelope()), /originating connection is unverified/);
  assert.deepEqual(f.attempts, []);
});

test("malformed requests fail closed without consulting output state", async () => {
  const f = fixture();
  for (const request of [null, [], { ...envelope(), type: "get_states" }, { ...envelope(), domain: null }, { ...envelope(), service: {} }]) {
    await assert.rejects(f.refuse(request), /command is invalid/);
  }
  assert.deepEqual(f.attempts, []);
});
