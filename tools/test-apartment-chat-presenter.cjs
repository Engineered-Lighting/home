"use strict";
// Execute the actual presentation seam without mounting Home, sockets or hooks.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const babel = require(path.resolve(__dirname, "../app/src/vendor/babel-7.29.0/babel.min.js"));
const source = fs.readFileSync(path.resolve(__dirname, "../app/src/home-app.jsx"), "utf8");
const ast = babel.transform(source, { parserOpts: { plugins: ["jsx"] }, ast: true, code: false }).ast.program;
function walk(node, visit, ancestors = []) {
  if (!node || typeof node !== "object") return;
  if (node.type) visit(node, ancestors);
  for (const [key, value] of Object.entries(node)) {
    if (["loc", "extra", "leadingComments", "trailingComments", "innerComments"].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => walk(child, visit, [...ancestors, node]));
    else if (value && typeof value === "object") walk(value, visit, [...ancestors, node]);
  }
}
const presenter = ast.body.find(node => node.type === "FunctionDeclaration" && node.id.name === "ApartmentChatTurn");
assert.ok(presenter, "the actual unified presenter exists");
const text = node => source.slice(node.start, node.end);
const presenterSource = text(presenter);
const React = { createElement(type, input, ...children) {
  const { key, ...props } = input || {};
  if (children.length) props.children = children.length === 1 ? children[0] : children;
  return { type, key: key == null ? null : String(key), props };
} };
function loadPresenter(code = presenterSource) {
  const EventContent = function AuthoredEventContent() {};
  const TurnHeader = function AuthoredTurnHeader() {}, TurnBody = function AuthoredTurnBody() {};
  const compiled = babel.transform(code, { presets: ["react"] }).code;
  const render = vm.runInNewContext(compiled + "\nApartmentChatTurn;", { React, TurnHeader, TurnBody, window: { EventContent } }, { timeout: 1000 });
  return { render, EventContent };
}
function descendants(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => descendants(child, predicate));
  if (!node || typeof node !== "object") return [];
  return [...(predicate(node) ? [node] : []), ...descendants(node.props?.children, predicate)];
}
const callbacks = Object.fromEntries(["onConfirmAction", "onCancelAction", "onUndoAction", "onControlAction", "onWhy"].map(key => [key, () => key]));
const reviewMount = () => {};
const events = [
  { id: "stream", kind: "home", text: "Partial", streaming: true },
  { id: "external", kind: "external", text: "Partial external", streaming: true },
  { id: "confirm", kind: "action", status: "pending", service: "authored.only" },
  { id: "control", kind: "action", controllable: true, actionCtx: { domain: "light" } },
  { id: "review", kind: "personal-memory-review", mountReview: reviewMount },
];
function assertForwarding(loaded) {
  const lifecycle = new Map(events.map((event, index) => [event.id, { authored_state: index }]));
  const tree = loaded.render({ group: { speaker: "home", time: "12:00:00", events }, unified: true, ...callbacks, controlLifecycles: lifecycle });
  const children = descendants(tree, node => node.type === loaded.EventContent);
  assert.equal(children.length, events.length);
  children.forEach((child, index) => {
    assert.deepEqual(Object.keys(child.props).sort(), ["e", "onConfirm", "onCancel", "onUndo", "onControlAction", "lifecycle", "onWhy"].sort());
    assert.equal(child.props.e, events[index], "streaming, action and review data retain their original event identity");
    for (const [prop, callback] of Object.entries({ onConfirm: "onConfirmAction", onCancel: "onCancelAction", onUndo: "onUndoAction", onControlAction: "onControlAction", onWhy: "onWhy" })) {
      assert.equal(child.props[prop], callbacks[callback]);
    }
    assert.equal(child.props.lifecycle, lifecycle.get(events[index].id));
  });
}

test("unified Chat delegates original events and every action, lifecycle and review callback", () => {
  const loaded = loadPresenter();
  assertForwarding(loaded);
  const tree = loaded.render({ group: { speaker: "system", time: "12:00:01", events: [events[0]] }, ...callbacks });
  assert.equal(descendants(tree, node => node.type === loaded.EventContent)[0].props.lifecycle, undefined);
});

test("stream updates and inserted siblings preserve keyed rows and the EventContent component", () => {
  const loaded = loadPresenter();
  const rows = (list, unified) => descendants(loaded.render({ group: { speaker: "home", events: list }, unified, ...callbacks }), node => node.props?.["data-kind"]);
  const first = rows(events, true), updated = rows([{ id: "new", kind: "system" }, ...events.map(event => ({ ...event, text: "Completed", streaming: false }))], false);
  assert.equal(new Set(first.map(row => row.key)).size, events.length);
  first.forEach((row, index) => {
    assert.equal(row.key, events[index].id);
    const next = updated.find(item => item.key === row.key);
    assert.ok(next, "a streamed event retains its reconciliation key after insertion");
    assert.equal(next.type, row.type, "layout changes preserve the keyed event wrapper type");
    assert.equal(row.props.children.type, loaded.EventContent);
    assert.equal(next.props.children.type, loaded.EventContent);
  });
});

test("actual feed callsite keeps controller callbacks, group identity and one stable presenter", () => {
  let callsite, outerKey;
  walk(ast, (node, ancestors) => {
    if (node.type === "JSXElement" && node.openingElement.name?.name === "ApartmentChatTurn") {
      assert.equal(callsite, undefined, "one actual feed callsite"); callsite = node;
      const owner = [...ancestors].reverse().find(parent => parent.type === "JSXElement" && parent.openingElement.attributes.some(attr => attr.name?.name === "key"));
      outerKey = owner?.openingElement.attributes.find(attr => attr.name?.name === "key").value.expression;
    }
  });
  assert.ok(callsite && outerKey);
  const loaded = loadPresenter();
  const group = { speaker: "home", events: [{ ...events[0], turnKey: "stable-turn" }] }, explained = [];
  const bindings = { React, ApartmentChatTurn: loaded.render, unifiedHome: true, g: group, isSpatialWide: true, density: "normal", confirmAction: callbacks.onConfirmAction, cancelAction: callbacks.onCancelAction,
    undoAction: callbacks.onUndoAction, onControlAction: callbacks.onControlAction, controlLifecycles: new Map(), openRightSlot: (slot, open) => { explained.push(slot); open(); }, setExplainConvId: id => explained.push(id),
    // The Apartment aim bridge Chat aim cards use (APT-C04).
    shellState: { aimBridge: { authored: "aim-bridge" } } };
  const call = vm.runInNewContext(babel.transform("(" + text(callsite) + ")", { presets: ["react"] }).code, bindings, { timeout: 1000 });
  const classic = vm.runInNewContext(babel.transform("(" + text(callsite) + ")", { presets: ["react"] }).code, { ...bindings, unifiedHome: false }, { timeout: 1000 });
  assert.equal(call.type, loaded.render); assert.equal(classic.type, call.type);
  assert.equal(call.props.unified, true); assert.equal(classic.props.unified, false);
  assert.equal(call.props.group, group); assert.equal(call.props.density, "compact");
  for (const [prop, callback] of Object.entries(callbacks)) if (prop !== "onWhy") assert.equal(call.props[prop], callback);
  assert.equal(call.props.controlLifecycles, bindings.controlLifecycles);
  assert.equal(call.props.aimBridge, bindings.shellState.aimBridge, "Chat aim cards receive the Apartment aim bridge");
  call.props.onWhy("authored-conversation"); assert.deepEqual(explained, ["explain", "authored-conversation"]);
  const key = (g, i) => vm.runInNewContext("(" + text(outerKey) + ")", { g, i }, { timeout: 1000 });
  assert.equal(key(group, 2), key({ ...group, events: [{ ...group.events[0], text: "stream completed", streaming: false }] }, 99));
  assert.equal(key({ speaker: "home", events: [events[0]] }, 2), key({ speaker: "home", events: [{ ...events[0], text: "stream completed" }] }, 99));
});

test("contracts reject losing Undo, cloning events or replacing event identity keys", () => {
  assert.throws(() => assertForwarding(loadPresenter(presenterSource.replace("onUndo={onUndoAction}", "onUndo={undefined}"))));
  assert.throws(() => assertForwarding(loadPresenter(presenterSource.replace("e={event}", "e={{ ...event }}"))));
  const broken = loadPresenter(presenterSource.replace("key={event.id}", "key={event.kind}"));
  const tree = broken.render({ group: { speaker: "home", events: [{ id: "first", kind: "home" }, { id: "second", kind: "home" }] } });
  const keys = descendants(tree, node => node.props?.["data-kind"]).map(row => row.key);
  assert.notEqual(new Set(keys).size, keys.length, "the authored duplicate-kind mutation is rejected by the stable identity assertion");
});
