"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "app/src/home-app.jsx"), "utf8");
const babel = require(path.join(root, "app/src/vendor/babel-7.29.0/babel.min.js"));
const ast = babel.transform(source, { parserOpts: { plugins: ["jsx"] }, ast: true, code: false }).ast;
const declaration = ast.program.body.find(n => n.type === "FunctionDeclaration" && n.id?.name === "createApartmentChatUnreadTracker");
assert.ok(declaration, "the actual shell must declare its unread tracker");
const createTracker = vm.runInNewContext("(" + source.slice(declaration.start, declaration.end) + ")", {}, { timeout: 1000 });
const reply = (id, text = "A reply", extra = {}) => ({ id, kind: "home", text, ...extra });
function setup(initial = [], conversationId = null) {
  const tracker = createTracker(), scope = {};
  const state = { events: initial, scope, conversationId, visible: false };
  const sync = patch => tracker.sync(Object.assign(state, patch));
  assert.equal(sync({}), false, "history is the initial read baseline");
  return { sync, scope };
}

test("new hidden assistant replies set one dot; opening Chat clears without replay", () => {
  const { sync } = setup();
  const events = [reply("e-1")];
  assert.equal(sync({ events }), true);
  assert.equal(sync({ events, visible: true }), false);
  assert.equal(sync({ events, visible: false }), false);
  assert.equal(sync({ events: [...events, reply("e-2")] }), true);
});

test("hydration, history prepend, rerender and same-id stream patches do not manufacture unread", () => {
  const existing = reply("e-2"), { sync } = setup([existing]);
  assert.equal(sync({ events: [reply("e-1"), existing] }), false);
  assert.equal(sync({ events: [reply("e-1"), { ...existing, text: "Updated streamed reply" }] }), false);
  assert.equal(sync({ events: [reply("e-1"), existing] }), false);
});

test("an empty streaming placeholder alerts only on its first content", () => {
  const { sync } = setup();
  assert.equal(sync({ events: [reply("stream", "", { streaming: true })] }), false);
  assert.equal(sync({ events: [reply("stream", "First", { streaming: true })] }), true);
  assert.equal(sync({ visible: true }), false);
  assert.equal(sync({ visible: false, events: [reply("stream", "First plus more", { streaming: true })] }), false);
  assert.equal(sync({ events: [reply("stream", "Final", { streaming: false })] }), false);
});

test("user/system/background/onboarding/empty/missing-id events do not alert", () => {
  const { sync } = setup();
  assert.equal(sync({ events: [
    reply("user", "Input", { kind: "user" }), reply("system", "Notice", { kind: "system" }),
    reply("bg", "Background", { backgroundPerception: true }), reply("intro", "Intro", { onboarding: true }),
    reply("empty", "   "), { kind: "home", text: "No stable id" }
  ] }), false);
});

test("endpoint/credential scope and explicit conversation switches reset the baseline", () => {
  const first = reply("e-1"), { sync } = setup();
  assert.equal(sync({ events: [first] }), true);
  assert.equal(sync({ scope: {}, events: [first, reply("other-endpoint")] }), false);
  assert.equal(sync({ events: [first, reply("other-endpoint"), reply("new")] }), true);
  assert.equal(sync({ conversationId: "new-conversation" }), true, "first server id adopts the current conversation");
  assert.equal(sync({ conversationId: "different-conversation" }), false);
  assert.equal(sync({ conversationId: null }), false);
});

test("first reply arriving with the first server conversation id is not lost", () => {
  const { sync } = setup([{ id: "user", kind: "user", text: "Question" }]);
  assert.equal(sync({ conversationId: "server-id", events: [
    { id: "user", kind: "user", text: "Question" }, reply("first", "Answer", { convId: "server-id" })
  ] }), true);
});

test("clear/restore feed replacement is a read baseline, not new assistant output", () => {
  const old = reply("old"), { sync } = setup([old], "old-conversation");
  assert.equal(sync({ conversationId: null, events: [{ id: "cleared", kind: "system", text: "Cleared" }] }), false);
  assert.equal(sync({ events: [old, { id: "restored", kind: "system", text: "Restored" }] }), false);
  assert.equal(sync({ events: [old, { id: "restored", kind: "system", text: "Restored" }, reply("fresh")] }), true);
});

test("bounded feed eviction retains new-tail detection and foreign conversation replies do not alert", () => {
  const events = Array.from({ length: 600 }, (_, i) => reply("e-" + i));
  const { sync } = setup(events, "current");
  const next = [...events.slice(1), reply("new", "Foreign", { convId: "other" })];
  assert.equal(sync({ events: next }), false);
  assert.equal(sync({ events: [...next.slice(1), reply("current-new", "Current", { convId: "current" })] }), true);
});

test("retired unread events clear the dot without discarding other unread replies", () => {
  const read = reply("read"), one = reply("private-one", "Private", { privateCameraContext: true });
  const two = reply("other-unread");
  const { sync } = setup([read]);
  assert.equal(sync({ events: [read, one] }), true);
  assert.equal(sync({ events: [read] }), false, "retired private content is no longer unread");
  assert.equal(sync({ events: [read, one, two] }), true);
  assert.equal(sync({ events: [read, two] }), true, "another unread reply remains");
  assert.equal(sync({ events: [read] }), false);
});
