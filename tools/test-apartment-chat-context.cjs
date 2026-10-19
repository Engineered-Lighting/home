"use strict";
// The Chat room-context module with authored rooms and a fake bridge only. No
// Home app, room assets, model, socket or service is loaded.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SOURCE = fs.readFileSync(path.resolve(__dirname, "../app/src/home-apartment-chat-context.js"), "utf8");
function load() {
  // Same realm, so returned objects compare structurally with test literals.
  const window = {};
  new Function("window", SOURCE)(window);
  return window.HomeApartmentChatContext;
}
const api = load();
const SCHEMA = "home-apartment-chat-room-1";
const victoria = (selection = { kind: "light", id: "vic-s4" }) => api.normalizeRoom({ schema: SCHEMA, siteId: "victoria", key: "victoria:r7:e3:d9",
  lights: [{ id: "vic-s4", label: "Socket 4" }, { id: "vic-s2", label: "Socket 2" }, { id: "vic-s4-spot", label: "Socket 4 spot" }],
  namedAims: [], selection });
const echo = (selection = null, key = "echo:r3") => api.normalizeRoom({ schema: SCHEMA, siteId: "echo", key,
  lights: [{ id: "kitchen", label: "Kitchen light", entityId: "light.kitchen" }, { id: "desk", label: "Desk lamp", entityId: "light.desk_lamp" }],
  namedAims: [{ id: "painting", label: "Painting" }], selection });

function fakeBridge(state = {}, { reject = false, throwOn = "" } = {}) {
  const calls = { read: [], apply: [] };
  const fixture = { state: { spotlight: { on: true, brightness: 0.5, color_temp_kelvin: 3000, ...state.spotlight },
    downward: { on: true, brightness: 0.75, color_temp_kelvin: 3000, ...state.downward }, radials: [] },
  color_temp_ranges: { spotlight: [2700, 6500], downward: [2200, 6500], radials: [1800, 6500] } };
  return { calls, fixture,
    readModeledLight(ref) { calls.read.push(ref); if (throwOn === "read") throw Error("authored"); return fixture; },
    applyModeledPatch(ref, patch) {
      calls.apply.push([ref, patch]);
      if (throwOn === "apply") throw Error("authored");
      if (reject) return null;
      for (const [channel, update] of Object.entries(patch)) Object.assign(fixture.state[channel], update);
      return { ok: true, state: fixture.state };
    } };
}
const about = (room, extra = {}) => ({ ...room.selection, siteId: room.siteId, key: room.key, ...extra });

test("only a well-formed current-home snapshot becomes a room; shared names never become chips", () => {
  assert.equal(api.normalizeRoom(null), null);
  assert.equal(api.normalizeRoom({ schema: "other", siteId: "victoria", key: "k" }), null);
  assert.equal(api.normalizeRoom({ schema: SCHEMA, siteId: "elsewhere", key: "k" }), null);
  assert.equal(api.normalizeRoom({ schema: SCHEMA, siteId: "victoria", key: " " }), null);
  const room = api.normalizeRoom({ schema: SCHEMA, siteId: "echo", key: "echo:1",
    lights: [{ id: "a", label: "Lamp" }, { id: "b", label: "lamp" }, { id: "c", label: "Floor", entityId: "switch.no" }, { id: "", label: "x" }],
    namedAims: [], selection: { kind: "light", id: "missing" } });
  assert.deepEqual(room.lights.map(item => [item.id, item.ambiguous, item.entityId]), [["a", true, null], ["b", true, null], ["c", false, null]]);
  assert.equal(room.selection, null, "a selection outside the inventory is not offered");
  assert.deepEqual(api.findReferences("Turn on the lamp and the floor", room).map(ref => ref.id), ["c"]);
  assert.ok(Object.isFrozen(room) && Object.isFrozen(room.lights[0]));
});

test("replies name current-home lights and named aims in order, longest name first, once each", () => {
  const room = victoria();
  const refs = api.findReferences("I set Socket 4 spot to 55%. Socket 2 and socket 4 spot stay; Socket 40 is unknown.", room);
  assert.deepEqual(refs.map(ref => [ref.kind, ref.id, ref.siteId, ref.key]), [
    ["light", "vic-s4-spot", "victoria", "victoria:r7:e3:d9"], ["light", "vic-s2", "victoria", "victoria:r7:e3:d9"]]);
  const lit = api.findReferences("Desk lamp (light.desk_lamp) lights the Painting.", echo());
  assert.deepEqual(lit.map(ref => ref.id), ["desk", "painting"]);
  assert.deepEqual(api.findReferences("Nothing here", echo()), []);
  assert.deepEqual(api.findReferences("Socket 4", null), []);
});

test("an action card names one light by entity or exact label", () => {
  assert.equal(api.resolveActionTarget("light.kitchen", echo()).id, "kitchen");
  assert.equal(api.resolveActionTarget("kitchen light", echo()).id, "kitchen");
  assert.equal(api.resolveActionTarget("light.unknown", echo()), null);
  assert.equal(api.resolveActionTarget("Painting", echo()), null, "a named aim is not an action-card light");
});

test("a reply stays bound to the home and room it completed in", () => {
  const bound = api.bindEvent({ kind: "home", text: "Kitchen light is on." }, echo());
  assert.equal(bound.refs[0].id, "kitchen");
  assert.equal(api.availability(bound.refs[0], bound, echo()), "");
  assert.match(api.availability(bound.refs[0], bound, victoria()), /about Los Angeles; you're viewing Victoria/);
  assert.match(api.availability(bound.refs[0], bound, echo(null, "echo:r4")), /room changed/);
  const shrunk = api.normalizeRoom({ schema: SCHEMA, siteId: "echo", key: "echo:r3", lights: [], namedAims: [] });
  assert.match(api.availability(bound.refs[0], bound, shrunk), /no longer in the room/);
  assert.match(api.availability(bound.refs[0], null, echo()), /before this room view/);
  assert.match(api.availability(bound.refs[0], bound, null), /isn't open/);
  assert.equal(api.bindEvent({ kind: "home", text: "Kitchen light" }, null), null, "a reply seen without a room stays unbound");
  assert.equal(api.bindEvent({ kind: "action", target: "light.kitchen" }, echo()).action.id, "kitchen");
});

test("brighter, dimmer, warmer, cooler and N% parse; anything else asks", () => {
  const ok = (text, expected) => assert.deepEqual(api.parseIntent(text, "Socket 4"), { ok: true, channel: "spotlight", brightness: null, level: null, kelvin: null, ...expected }, text);
  ok("brighter", { brightness: "up" });
  ok("Make Socket 4 a little brighter, please", { brightness: "up" });
  ok("dim it", { brightness: "down" });
  ok("warmer", { kelvin: "warmer" });
  ok("cooler", { kelvin: "cooler" });
  ok("50%", { brightness: "set", level: 50 });
  ok("set it to 30 percent", { brightness: "set", level: 30 });
  ok("down warmer", { channel: "downward", kelvin: "warmer" });
  ok("brighter and warmer", { brightness: "up", kelvin: "warmer" });
  for (const text of ["", "turn on the kitchen", "off", "brighter dimmer", "50% brighter", "0%", "150%", "10% 20%", "spot down brighter", "radial brighter", "aim at the desk"]) {
    assert.equal(api.parseIntent(text, "Socket 4").ok, false, text);
  }
});

test("a selected Den light changes in the room model only, through the bridge", () => {
  for (const [text, channel, expected, reply] of [
    ["brighter", "spotlight", { brightness: 0.7 }, /spot is now 70% · 3000 K in the room model\. Nothing was sent to a light\./],
    ["warmer", "spotlight", { color_temp_kelvin: 2700 }, /spot is now 50% · 2700 K/],
    ["50%", "spotlight", null, /spot is already 50% · 3000 K, so nothing changed/],
    ["30%", "spotlight", { brightness: 0.3 }, /spot is now 30% · 3000 K/],
    ["down dimmer", "downward", { brightness: 0.55 }, /down is now 55% · 3000 K/],
  ]) {
    const room = victoria(), bridge = fakeBridge();
    const result = api.resolveAbout(text, about(room), room, bridge);
    assert.match(result.text, reply, text);
    if (expected) {
      assert.equal(result.outcome, "modeled", text);
      assert.equal(bridge.calls.apply.length, 1);
      assert.deepEqual(bridge.calls.apply[0][1], { [channel]: expected });
      assert.equal(bridge.calls.apply[0][0].id, "vic-s4");
    } else {
      assert.equal(result.outcome, "unchanged");
      assert.equal(bridge.calls.apply.length, 0);
    }
  }
  const off = fakeBridge({ spotlight: { on: false, brightness: 0.4 } });
  assert.deepEqual(api.resolveAbout("brighter", about(victoria()), victoria(), off).outcome, "modeled");
  assert.deepEqual(off.calls.apply[0][1], { spotlight: { on: true, brightness: 0.2 } }, "an off spot comes on from zero");
  const warmest = fakeBridge({ spotlight: { color_temp_kelvin: 2700 } });
  assert.equal(api.resolveAbout("warmer", about(victoria()), victoria(), warmest).outcome, "unchanged");
  assert.equal(api.resolveAbout("dimmer", about(victoria()), victoria(), fakeBridge({ spotlight: { on: false } })).outcome, "unchanged");
});

test("everything else is refused or asked, and the bridge is never asked to change", () => {
  const cases = [
    ["Los Angeles is refused", "brighter", about(echo({ kind: "light", id: "kitchen" })), echo({ kind: "light", id: "kitchen" }), "refused", /in Los Angeles yet, so nothing was sent/],
    ["a Los Angeles named aim is refused", "brighter", about(echo({ kind: "aim", id: "painting" })), echo({ kind: "aim", id: "painting" }), "refused", /in Los Angeles yet/],
    ["unsupported words ask", "turn on the kitchen", about(victoria()), victoria(), "clarify", /brighter, dimmer, warmer or cooler/],
    ["a changed selection is refused", "brighter", about(victoria(), { id: "vic-s2" }), victoria(), "refused", /selection changed/],
    ["another room revision is refused", "brighter", about(victoria(), { key: "victoria:old" }), victoria(), "refused", /selection changed/],
    ["a cleared selection is refused", "brighter", about(victoria()), victoria(null), "refused", /selection changed/],
    ["no room is refused", "brighter", about(victoria()), null, "refused", /isn't ready/],
  ];
  for (const [name, text, chip, room, outcome, message] of cases) {
    const bridge = fakeBridge(), result = api.resolveAbout(text, chip, room, bridge);
    assert.equal(result.outcome, outcome, name);
    assert.match(result.text, message, name);
    assert.equal(bridge.calls.apply.length, 0, name);
  }
  const aimRoom = api.normalizeRoom({ schema: SCHEMA, siteId: "victoria", key: "v", lights: [], namedAims: [{ id: "desk", label: "Desk" }],
    selection: { kind: "aim", id: "desk" } });
  assert.equal(api.resolveAbout("brighter", about(aimRoom), aimRoom, fakeBridge()).outcome, "clarify");
  const rejected = fakeBridge({}, { reject: true });
  assert.equal(api.resolveAbout("brighter", about(victoria()), victoria(), rejected).outcome, "refused");
  for (const throwOn of ["read", "apply"]) {
    assert.equal(api.resolveAbout("brighter", about(victoria()), victoria(), fakeBridge({}, { throwOn })).outcome, "refused", throwOn);
  }
  assert.equal(api.resolveAbout("brighter", about(victoria()), victoria(), null).outcome, "refused");
  const lying = { readModeledLight: () => fakeBridge().fixture, applyModeledPatch: () => ({ ok: true, state: { spotlight: { on: true, brightness: 0.9, color_temp_kelvin: 3000 } } }) };
  assert.equal(api.resolveAbout("brighter", about(victoria()), victoria(), lying).outcome, "refused", "a readback that differs from the change is not claimed");
});

test("the module itself has no transport", () => {
  for (const word of ["fetch(", "WebSocket", "XMLHttpRequest", "callService", "sendToHA", "runPipeline", "localStorage", "postMessage"]) {
    assert.equal(SOURCE.includes(word), false, word);
  }
});
