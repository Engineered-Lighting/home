"use strict";
// Actual Chat callbacks with authored transports. No browser, HA connection or I/O.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "app/src/home-app.jsx"), "utf8");
const dataSource = fs.readFileSync(path.join(root, "app/src/home-apartment-data.js"), "utf8");
const babel = require(path.join(root, "app/src/vendor/babel-7.29.0/babel.min.js"));
const ast = babel.transform(source, { parserOpts: { plugins: ["jsx"] }, ast: true, code: false }).ast;
const callbacks = {};
function visit(node) {
  if (!node || typeof node !== "object") return;
  if (node.type === "VariableDeclarator" && ["undoAction", "onControlAction"].includes(node.id?.name)) {
    assert.equal(callbacks[node.id.name], undefined, "one actual " + node.id.name + " callback");
    callbacks[node.id.name] = source.slice(node.init.arguments[0].start, node.init.arguments[0].end);
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === "object") visit(value);
  }
}
visit(ast.program);

function loadCallbacks({ dataAvailable = true, guardOverride } = {}) {
  const calls = [], messages = [], envelopes = [];
  const client = { ws: { readyState: 1 }, url: "https://authored-ha.invalid", token: "authored-only",
    call: async envelope => { calls.push(envelope); return {}; } };
  const window = {};
  if (dataAvailable) {
    vm.runInNewContext(dataSource, { window }, { timeout: 1000 });
    const refuse = guardOverride || window.HomeApartmentData.refuseChatServiceEnvelope;
    window.HomeApartmentData.refuseChatServiceEnvelope = envelope => {
      envelopes.push(envelope); return refuse(envelope);
    };
  }
  const bindings = { haClientRef: { current: client }, addEvent: event => messages.push(event), window,
    console: { log() {}, warn() {} } };
  return { client, calls, messages, envelopes, window, bindings, ...Object.fromEntries(Object.entries(callbacks)
    .map(([name, code]) => [name, vm.runInNewContext("(" + code + ")", bindings, { timeout: 1000 })])) };
}

test("actual Chat Undo cannot energize lights without a qualified command-home authority", async () => {
  const proof = loadCallbacks();
  await assert.rejects(proof.undoAction({ id: "authored-action", originalService: "light.turn_off",
    inverseService: "light.turn_on", target: "entity.light.authored", attrs: {} }), /Chat.*unavailable|qualified.*home/i);
  assert.deepEqual(proof.calls, [], "missing authority refuses before helper reads or service calls");
});

test("actual Chat inline light control cannot energize without a qualified command-home authority", async () => {
  const proof = loadCallbacks();
  await assert.rejects(proof.onControlAction({ domain: "light", service: "turn_on",
    service_data: { brightness: 80 }, target: { entity_id: ["light.authored"] } }), /Chat.*unavailable|qualified.*home/i);
  assert.deepEqual(proof.calls, [], "missing authority refuses before helper reads or service calls");
});

test("actual unbound off/media and restored Undo requests refuse their unknown origin", async () => {
  const proof = loadCallbacks();
  for (const [domain, service] of [["light", "turn_off"], ["media_player", "media_pause"], ["media_player", "volume_set"]]) {
    await assert.rejects(proof.onControlAction({ domain, service, service_data: {}, target: { entity_id: [domain + ".authored"] } }), /originating connection is unverified/);
  }
  await assert.rejects(proof.undoAction({ id: "restored-action", originalService: "light.turn_on", inverseService: "light.turn_off",
    target: "entity.light.authored", attrs: {} }), /originating connection is unverified/);
  assert.deepEqual(proof.calls, []);
  assert.ok(proof.messages.every(event => event.tone === "error" && !/undone.*fired/.test(event.text)));
});

test("actual callbacks keep selectors and data separate while refusing", async () => {
  const proof = loadCallbacks(), target = { entity_id: ["light.authored_one"], area_id: ["authored_area"], device_id: ["authored_device"] };
  const service_data = { transition: 0.5, entity_id: "literal_nested_value" };
  await assert.rejects(proof.onControlAction({ domain: "light", service: "turn_off", service_data, target }), /originating connection is unverified/);
  assert.equal(proof.envelopes[0].target, target); assert.equal(proof.envelopes[0].service_data, service_data);
  const targets = ["light.authored_one", "light.authored_two"];
  await assert.rejects(proof.undoAction({ id: "authored-multi", originalService: "light.turn_on", inverseService: "light.turn_off",
    target: "2 targets", attrs: { targets } }), /originating connection is unverified/);
  assert.equal(proof.envelopes[1].target.entity_id, targets);
  assert.deepEqual(Object.keys(proof.envelopes[1].service_data), []);
  assert.deepEqual(proof.calls, []);
});

test("a new client, socket, endpoint, credential or epoch cannot qualify an old card or replay its refusal", async () => {
  const proof = loadCallbacks();
  const request = { domain: "light", service: "turn_off", service_data: {}, target: { entity_id: ["light.authored"] } };
  await assert.rejects(proof.onControlAction(request), /originating connection is unverified/);
  const replacement = { ws: { readyState: 1 }, url: "https://authored-new.invalid", token: "authored-new-only",
    epoch: 2, call: async envelope => { proof.calls.push(envelope); } };
  proof.bindings.haClientRef.current = replacement;
  proof.window.HomeChatAuthority = { client: replacement, epoch: 2, siteId: "echo", qualified: true };
  await assert.rejects(proof.onControlAction(request), /originating connection is unverified/);
  await assert.rejects(proof.undoAction({ id: "old-action", originalService: "light.turn_on", inverseService: "light.turn_off",
    target: "entity.light.authored", attrs: {} }), /originating connection is unverified/);
  await Promise.resolve(); assert.deepEqual(proof.calls, []);
});

for (const mode of ["missing", "unexpected-return"]) {
  test("an " + mode + " guard never becomes a raw fallback or successful Undo claim", async () => {
    const proof = loadCallbacks(mode === "missing" ? { dataAvailable: false } : { guardOverride: async () => ({ ok: true }) });
    await assert.rejects(proof.onControlAction({ domain: "media_player", service: "media_pause", service_data: {},
      target: { entity_id: ["media_player.authored"] } }), /originating connection is unverified/);
    await assert.rejects(proof.undoAction({ id: "authored-old", originalService: "light.turn_on", inverseService: "light.turn_off",
      target: "entity.light.authored", attrs: {} }), /originating connection is unverified/);
    assert.deepEqual(proof.calls, []);
    assert.equal(proof.messages.some(event => /undone.*fired/.test(event.text)), false);
  });
}
