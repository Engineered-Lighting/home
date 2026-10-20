"use strict";
// Dispatch safety for Chat bound to the room. Runs the actual sendInput and
// stopStreaming callbacks from home-app.jsx against fake transports, authored
// rooms and a fake room bridge. No Home app, socket, model or service runs.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const babel = require(path.resolve(__dirname, "../app/src/vendor/babel-7.29.0/babel.min.js"));

const SRC = path.resolve(__dirname, "../app/src");
const source = fs.readFileSync(path.join(SRC, "home-app.jsx"), "utf8");
const moduleSource = fs.readFileSync(path.join(SRC, "home-apartment-chat-context.js"), "utf8");
const ast = babel.transform(source, { parserOpts: { plugins: ["jsx"] }, ast: true, code: false }).ast.program;
const text = node => source.slice(node.start, node.end);
function walk(node, visit, ancestors = []) {
  if (!node || typeof node !== "object") return;
  if (node.type) visit(node, ancestors);
  for (const [key, value] of Object.entries(node)) {
    if (["loc", "extra", "leadingComments", "trailingComments", "innerComments"].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => walk(child, visit, [...ancestors, node]));
    else if (value && typeof value === "object") walk(value, visit, [...ancestors, node]);
  }
}
const declaration = name => {
  const found = ast.body.filter(node => node.type === "FunctionDeclaration" && node.id?.name === name);
  assert.equal(found.length, 1, "one declaration for " + name); return text(found[0]);
};
const normal = ast.body.find(node => node.type === "FunctionDeclaration" && node.id?.name === "HomeNormalApp");
function callbackOf(name) {
  const found = [];
  walk(normal, node => {
    if (node.type === "VariableDeclarator" && node.id?.name === name && node.init?.callee?.name === "useCallback") found.push(node.init.arguments[0]);
  });
  assert.equal(found.length, 1, "one actual " + name); return text(found[0]);
}
const sendInputSource = callbackOf("sendInput"), stopSource = callbackOf("stopStreaming");
const instantiate = (code, bindings) => new Function(...Object.keys(bindings), "return (" + code + ");")(...Object.values(bindings));
const loadApi = () => { const window = {}; new Function("window", moduleSource)(window); return window.HomeApartmentChatContext; };

const SCHEMA = "home-apartment-chat-room-1";
function bridgeFor(siteId, { throwOnRead = false } = {}) {
  const calls = [];
  const fixture = { state: { spotlight: { on: true, brightness: 0.5, color_temp_kelvin: 3000 }, downward: { on: true, brightness: 0.75, color_temp_kelvin: 3000 } },
    color_temp_ranges: { spotlight: [2700, 6500], downward: [2200, 6500] } };
  const lights = siteId === "victoria" ? [{ id: "vic-s4", label: "Socket 4" }] : [{ id: "kitchen", label: "Kitchen light", entityId: "light.kitchen" }];
  return { calls, schema: SCHEMA, siteId, key: siteId + ":authored", lights, namedAims: [], selection: { kind: "light", id: lights[0].id },
    hover: ref => { calls.push(["hover", ref]); return false; }, select: ref => { calls.push(["select", ref]); return true; },
    adjust: ref => { calls.push(["adjust", ref]); return true; },
    readModeledLight: ref => { calls.push(["read", ref.id]); if (throwOnRead) throw Error("authored"); return fixture; },
    applyModeledPatch: (ref, patch) => {
      calls.push(["apply", ref.id, patch]);
      for (const [channel, update] of Object.entries(patch)) Object.assign(fixture.state[channel], update);
      return { ok: true, state: fixture.state };
    } };
}

function harness({ input, siteId = "victoria", chip = true, api = loadApi(), bridgeOptions } = {}) {
  const log = [], events = [], transport = { callService: [], runPipeline: [], sendMessage: [], websockets: [], fetches: [] };
  const spy = (name, result = false) => (...args) => { log.push(name); return result; };
  const bridge = siteId ? bridgeFor(siteId, bridgeOptions) : null;
  const room = bridge && api ? api.normalizeRoom(bridge) : null;
  const about = chip && room?.selection ? { ...room.selection, siteId: room.siteId, key: room.key } : null;
  const window = {
    HomeApartmentChatContext: api,
    HomePersonalMemory: { run: spy("memory") }, HomeLightingControl: { run: spy("lighting") },
    HomeCameraQuery: { run: spy("camera"), reset: () => log.push("camera.reset") },
    classifyIntent: () => { log.push("classify"); return "external"; }, isExternalConfigured: () => true,
  };
  const client = { callService: (...args) => transport.callService.push(args), runPipeline: (...args) => transport.runPipeline.push(args),
    sendMessage: (...args) => transport.sendMessage.push(args) };
  const reply = instantiate(declaration("apartmentChatAboutReply"), { window });
  const sendInput = instantiate(sendInputSource, {
    input, canonicalChatText: value => value.toLowerCase(), lastSubmittedTextRef: { current: { text: "", ts: 0 } }, window,
    handleCommand: spy("slash"), setInput: () => {}, chatAboutRef: { current: about }, addEvent: event => events.push(event),
    apartmentChatAboutReply: reply, chatRoomRef: { current: { room, bridge } }, haClientRef: { current: client },
    cameraQueryGenerationRef: { current: 1 }, homeChoiceRef: { current: "auto" }, viewSiteRef: { current: siteId || null }, sim: { active: false }, clearPrivateCameraContext: () => {},
    answerDirectLightStateQuestion: spy("direct"), runNaturalDeepLook: spy("natural"),
    localStorage: { getItem: () => "on" }, dispatchExternal: spy("external"), sendToHA: spy("sendToHA"),
  });
  return { log, events, transport, bridge, run: async () => {
    const saved = { WebSocket: globalThis.WebSocket, fetch: globalThis.fetch };
    globalThis.WebSocket = function AuthoredSocket(url) { transport.websockets.push(url); };
    globalThis.fetch = (...args) => { transport.fetches.push(args); return Promise.reject(Error("authored")); };
    try { await sendInput(); } finally { Object.assign(globalThis, saved); }
  } };
}
const ROUTES = ["memory", "lighting", "camera", "direct", "natural", "classify", "external", "sendToHA", "slash"];
function assertNoDispatch(h, name) {
  assert.deepEqual(h.log.filter(entry => ROUTES.includes(entry)), [], name + ": no route after the resolver");
  for (const [kind, calls] of Object.entries(h.transport)) assert.equal(calls.length, 0, `${name}: zero ${kind}`);
}

test("a chip-bearing message is claimed before memory and lighting control and never dispatched", async () => {
  for (const [input, patch, reply] of [
    ["brighter", { spotlight: { brightness: 0.7 } }, /Socket 4 spot is now 70% · 3000 K in the room model\. Nothing was sent to a light\./],
    ["warmer", { spotlight: { color_temp_kelvin: 2700 } }, /2700 K in the room model/],
    ["50%", null, /already 50% · 3000 K, so nothing changed/],
    ["down 30%", { downward: { brightness: 0.3 } }, /Socket 4 down is now 30%/],
  ]) {
    const h = harness({ input });
    await h.run();
    assertNoDispatch(h, input);
    assert.deepEqual(h.events[0], { kind: "user", text: input, about: "Socket 4" });
    assert.equal(h.events.length, 2);
    assert.match(h.events[1].text, reply, input);
    assert.equal(h.events[1].kind, "assistant");
    const applies = h.bridge.calls.filter(call => call[0] === "apply");
    assert.deepEqual(applies, patch ? [["apply", "vic-s4", patch]] : [], input);
  }
});

test("unresolvable, foreign and failing chip messages are refused or clarified, never routed", async () => {
  for (const [name, options, kind, message] of [
    ["Los Angeles", { input: "brighter", siteId: "echo" }, "system", /Chat can't change Kitchen light in Los Angeles yet, so nothing was sent/],
    ["a lighting command", { input: "turn on the kitchen lights in victoria" }, "assistant", /brighter, dimmer, warmer or cooler/],
    ["a camera question", { input: "who is in the den?" }, "assistant", /brighter, dimmer, warmer or cooler/],
    ["a failing bridge", { input: "brighter", bridgeOptions: { throwOnRead: true } }, "system", /couldn't be resolved/],
  ]) {
    const h = harness(options);
    await h.run();
    assertNoDispatch(h, name);
    assert.equal(h.events.length, 2, name);
    assert.equal(h.events[1].kind, kind, name);
    assert.match(h.events[1].text, message, name);
    assert.equal(h.bridge.calls.filter(call => call[0] === "apply").length, 0, name);
  }
});

test("a chip offered while the module was present is refused if the module is gone at send time", async () => {
  const h = harness({ input: "brighter" });
  const reply = instantiate(declaration("apartmentChatAboutReply"), { window: {} });
  assert.deepEqual(reply("brighter", { kind: "light", id: "vic-s4", label: "Socket 4", siteId: "victoria", key: "victoria:authored" }, {}),
    { kind: "system", tone: "warn", text: "The selection couldn't be used, so nothing changed.", chatContext: "refused" });
  await h.run();
  assertNoDispatch(h, "module present");
});

test("without the chip the existing routes run in their existing order", async () => {
  const h = harness({ input: "turn on the kitchen", chip: false });
  await h.run();
  assert.deepEqual(h.log.filter(entry => ROUTES.includes(entry)), ["memory", "lighting", "camera", "direct", "natural", "classify", "external"]);
  assert.equal(h.events.length, 0);
  const slash = harness({ input: "/look desk" });
  await slash.run();
  assert.deepEqual(slash.log.filter(entry => ROUTES.includes(entry)), ["slash"], "slash commands keep their existing handler");
  const order = ["chatAboutRef.current", "window.HomePersonalMemory?.run", "window.HomeLightingControl?.run", "sendToHA(text)"]
    .map(needle => sendInputSource.indexOf(needle));
  assert.ok(order.every(index => index > 0) && order.every((index, i) => !i || index > order[i - 1]), "the resolver comes first");
});

test("Stop says whether a tool step had started, and cuts a streaming reply short", () => {
  const run = (events, { active = true } = {}) => {
    let state = events, cancels = 0, cleared = 0, n = 0;
    const activeRunRef = { current: active ? { cancel: () => cancels++ } : null };
    const streamingIds = { current: new Set(events.filter(e => e.kind === "home" && e.streaming).map(e => e.id)) };
    const stop = instantiate(stopSource, { activeRunRef, timers: { current: [setTimeout(() => {}, 0)] }, streamingIds,
      setEvents: update => { state = typeof update === "function" ? update(state) : update; }, nextId: () => "authored-" + ++n, fmtTime: () => "12:00:00" });
    const realClear = globalThis.clearTimeout;
    globalThis.clearTimeout = id => { cleared++; realClear(id); };
    try { stop(); } finally { globalThis.clearTimeout = realClear; }
    return { state, cancels, cleared, activeRunRef };
  };
  const user = { id: "u", kind: "user", text: "Authored question" };
  const before = run([user, { id: "t", kind: "thinking", text: "calling assistant…", streaming: true, awaitingReply: true, turnKey: "k" }]);
  assert.equal(before.cancels, 1); assert.equal(before.activeRunRef.current, null);
  assert.deepEqual(before.state.map(e => [e.kind, e.text]), [["user", "Authored question"], ["system", "Stopped before Home reported any tool step."]]);
  assert.equal(before.state[1].turnKey, "k");
  const after = run([user, { id: "t", kind: "thinking", text: "working · looking (desk)…", streaming: true, awaitingReply: true, progressStep: "looking (desk)" }]);
  assert.equal(after.state[1].text, "Stopped after looking (desk) started; no reply will be shown.");
  const streaming = run([user, { id: "s", kind: "home", text: "Partial", streaming: true }]);
  assert.deepEqual(streaming.state[1], { id: "s", kind: "home", text: "Partial", streaming: false, stopped: true });
  assert.equal(streaming.state.length, 2);
  const external = run([{ id: "x", kind: "thinking", text: "asking external model…" }], { active: false });
  assert.deepEqual(external.state, [{ id: "x", kind: "thinking", text: "asking external model…" }], "other stubs keep their own owner");
});

test("the Home Assistant stub waits with Stop and shows the first tool step", () => {
  const start = source.indexOf("const sendToHA = useCallback"), end = source.indexOf("const stopStreaming = useCallback", start);
  const block = source.slice(start, end);
  assert.match(block, /kind: "thinking",[\s\S]{0,120}text: "calling assistant…",\s*turnKey,\s*streaming: true,\s*awaitingReply: true,/);
  assert.match(block, /e\.id === thinkingId && !e\.progressStep\s*\? \{ \.\.\.e, progressStep: step, text: `working · \$\{step\}…` \}/);
});

test("the header line names the model and what Chat draws on in each home", () => {
  let attr;
  walk(normal, node => {
    if (node.type === "JSXOpeningElement" && node.name?.name === "HomeHeader") attr = node.attributes.find(item => item.name?.name === "chatSourceLine");
  });
  assert.ok(attr, "the actual header receives the source line");
  const hasLiveModelName = instantiate(declaration("hasLiveModelName"), {});
  const line = bindings => instantiate(text(attr.value.expression), { hasLiveModelName, unifiedHome: true, model: "authored-model", sim: { active: false }, shellState: null, ...bindings });
  assert.equal(line({ shellState: { siteId: "victoria" } }), "authored-model · Victoria · modeled room");
  assert.equal(line({ shellState: { siteId: "echo" } }), "authored-model · Los Angeles · Home Assistant");
  assert.equal(line({}), "authored-model · Home Assistant");
  assert.equal(line({ sim: { active: true }, shellState: { siteId: "echo" } }), "authored-model · Simulation");
  assert.equal(line({ model: "—" }), "Model not chosen · Home Assistant");
  assert.equal(line({ unifiedHome: false }), "");
  assert.match(declaration("HomeHeader"), /\{chatSourceLine && <span className="apt-ui-chat-model" title=\{chatSourceLine\}>\{chatSourceLine\}<\/span>\}/);
});

test("the strip and the About chip mount only in the Apartment Home view", () => {
  let strip, chip;
  walk(normal, (node, ancestors) => {
    if (node.type === "JSXElement" && node.openingElement.name?.name === "ApartmentChatRoomRefs") strip = { node, parent: ancestors[ancestors.length - 1], ancestors };
    if (node.type === "JSXElement" && node.openingElement.name?.name === "ApartmentChatContextChip") chip = { node, parent: ancestors[ancestors.length - 1] };
  });
  assert.ok(strip && chip);
  assert.equal(text(strip.parent), "unifiedHome && " + text(strip.node));
  const row = [...strip.ancestors].reverse().find(node => node.type === "JSXElement");
  const turns = row.children.filter(child => child.type === "JSXElement").map(child => child.openingElement.name.name);
  assert.deepEqual(turns, ["ApartmentChatTurn"], "the strip is a sibling of the unchanged presenter");
  assert.equal(text(chip.parent), "chatAbout && " + text(chip.node));
  assert.match(source, /const chatRoomBridge = unifiedHome \? shellState\?\.chatRoom \|\| null : null;/);
});
