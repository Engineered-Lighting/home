"use strict";
// Actual leaf JSX, evaluated without a Home controller, events or browser APIs.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "app/src/home-chat-header-actions.jsx"), "utf8");
const babel = require(path.join(root, "app/src/vendor/babel-7.29.0/babel.min.js"));
const compiled = babel.transform(source, { presets: ["react"], sourceType: "script" }).code;
const React = { createElement(type, input, ...children) {
  const { key, ...props } = input || {};
  if (children.length) props.children = children.length === 1 ? children[0] : children;
  return { type, key: key == null ? null : String(key), props };
} };
function load() {
  const window = {};
  Object.defineProperties(window, Object.fromEntries(["events", "document", "navigator", "localStorage", "fetch"]
    .map(name => [name, { get() { throw new Error("The header leaf must not access " + name); } }])));
  vm.runInNewContext(compiled, { React, window }, { timeout: 1000 });
  return { render: window.HomeChatHeaderActions, transcript: window.HomeChatTranscript };
}
function buttons(tree) { return Array.from(tree.props.children.flat()).filter(node => node.type === "button"); }

test("the actual leaf presents native labeled buttons without controller or transcript access", () => {
  const { render } = load(), tree = render({});
  assert.equal(tree.props.role, "group");
  assert.equal(tree.props["aria-label"], "Conversation actions");
  const controls = buttons(tree);
  assert.deepEqual(controls.map(button => button.props["aria-label"]),
    ["Explain the last answer", "Copy transcript", "New conversation"]);
  assert.ok(tree.props.children.flat().some(node => node.type === "span" && node.props.children === "Conversation text only"),
    "the local copy scope is visible conversation UI");
  for (const button of controls) {
    assert.equal(button.type, "button"); assert.equal(button.props.type, "button");
    assert.equal(button.props.disabled, true);
    assert.equal(button.props.onClick, undefined, "an unavailable action has no executable handler");
    assert.ok(button.props["aria-description"]);
  }
});

test("actions delegate only to the supplied callbacks and retain the latest controller binding", () => {
  const { render } = load(), called = [];
  const props = { onExplain: () => called.push("explain"), onCopy: () => called.push("copy"),
    onNewConversation: () => called.push("guarded-new") };
  let controls = buttons(render(props));
  for (const [index, callback] of Object.values(props).entries()) {
    assert.equal(controls[index].props.onClick, callback, "the leaf preserves the owning operation");
    assert.equal(controls[index].props.disabled, false);
    assert.equal(controls[index].props["aria-description"], undefined);
  }
  controls[0].props.onClick(); controls[2].props.onClick();
  assert.deepEqual(called, ["explain", "guarded-new"], "other actions do not trigger copying");
  const replacement = () => called.push("current-copy");
  controls = buttons(render({ ...props, onCopy: replacement }));
  assert.equal(controls[1].props.onClick, replacement);
  controls[1].props.onClick();
  assert.deepEqual(called, ["explain", "guarded-new", "current-copy"]);
});

test("controller refusal removes its handler without disabling unrelated actions", () => {
  const { render } = load(), call = () => { throw new Error("Must not run"); };
  const controls = buttons(render({ onExplain: call, onCopy: call, onNewConversation: call,
    copyDisabledReason: "Transcript copying is unavailable.", newConversationDisabledReason: "Finish the current request first." }));
  assert.equal(controls[0].props.disabled, false);
  for (const index of [1, 2]) {
    assert.equal(controls[index].props.disabled, true);
    assert.equal(controls[index].props.onClick, undefined);
    assert.equal(controls[index].props.title, controls[index].props["aria-description"]);
  }
  assert.equal(controls[2].props["aria-description"], "Finish the current request first.");
});

test("a missing Copy callback stays unavailable while Explain and guarded New can be supplied", () => {
  const { render } = load(), call = () => {};
  const controls = buttons(render({ onExplain: call, onNewConversation: call }));
  assert.equal(controls[0].props.disabled, false);
  assert.equal(controls[1].props.disabled, true);
  assert.equal(controls[1].props.onClick, undefined);
  assert.equal(controls[1].props["aria-description"], "Transcript copying is unavailable.");
  assert.equal(controls[2].props.onClick, call);
});

test("transcript projection includes only current conversation text and never reads structured fields", () => {
  const { transcript } = load();
  const allowed = ["user", "voice", "home", "assistant", "external"].map((kind, index) => ({ id: "spoken-" + index, kind,
    text: "Text " + index, time: "private-time", convId: "private-conversation", snapshotUrl: "private-image" }));
  Object.defineProperty(allowed[0], "args", { get() { throw new Error("Structured fields must not be read"); } });
  const excluded = ["personal-memory-review", "tool", "action", "perception", "system", "diag", "help", "thinking", "proactive", "constructor"]
    .map(kind => ({ kind, text: "Must not copy " + kind }));
  const events = [...allowed, ...excluded,
    { kind: "user", text: "Onboarding", onboarding: "help-hint" },
    { kind: "home", text: "Private camera", privateCameraContext: true },
    { kind: "home", text: "Background", backgroundPerception: true },
    { kind: "external", text: "Partial", streaming: true },
    { kind: "voice", id: "unclaimed-voice", text: "listening…" },
    { kind: "home", text: { structured: "No serialization" } }, { kind: "user", text: "  " }, null];
  assert.equal(transcript.buildText(events, { excludedIds: ["unclaimed-voice"] }),
    "You: Text 0\nYou: Text 1\nHome: Text 2\nHome: Text 3\nExternal: Text 4");
  assert.equal(transcript.buildText([{ kind: "user", text: "Local literal https://authored.invalid/ remains text\nSecond line" }]),
    "You: Local literal https://authored.invalid/ remains text\nSecond line", "the projection does not redact or reinterpret user prose");
});

test("projection is immutable and does not retain a previous feed between calls", () => {
  const { transcript } = load();
  const event = Object.freeze({ id: "one", kind: "user", text: "First" });
  const events = Object.freeze([event]);
  const excludedIds = Object.freeze(["other"]);
  assert.equal(transcript.buildText(events, { excludedIds }), "You: First");
  assert.equal(events[0], event); assert.equal(event.text, "First");
  assert.equal(transcript.buildText([{ kind: "home", text: "Current feed" }]), "Home: Current feed");
  assert.equal(transcript.buildText([]), ""); assert.equal(transcript.buildText(null), "");
});

test("copy waits for the local writer and snapshots only the supplied current text", async () => {
  const { transcript } = load(), events = [{ kind: "user", text: "Copy now" }], written = [];
  let resolveWrite, settled = false;
  const pending = transcript.copy({ events, writeText: text => {
    written.push(text); return new Promise(resolve => { resolveWrite = resolve; });
  } }).then(result => { settled = true; return result; });
  events.push({ kind: "home", text: "Arrived after the copy gesture" });
  await Promise.resolve();
  assert.equal(settled, false, "success cannot precede writer completion");
  assert.deepEqual(written, ["You: Copy now"]);
  resolveWrite();
  const result = await pending; assert.equal(result.ok, true); assert.equal(result.reason, undefined);
});

test("empty, unavailable, denied, throwing and explicit-failure writers never report copied", async () => {
  const { transcript } = load(), events = [{ kind: "home", text: "Current reply" }];
  let calls = 0;
  const empty = await transcript.copy({ events: [{ kind: "personal-memory-review", text: "Private" }], writeText: () => { calls++; } });
  assert.equal(empty.ok, false); assert.equal(empty.reason, "empty"); assert.equal(calls, 0);
  const unavailable = await transcript.copy({ events });
  assert.equal(unavailable.ok, false); assert.equal(unavailable.reason, "unavailable");
  for (const writeText of [() => Promise.reject(new Error("Denied")), () => { throw new Error("Denied"); }, () => false]) {
    const result = await transcript.copy({ events, writeText });
    assert.equal(result.ok, false); assert.equal(result.reason, "failed");
    assert.equal(Object.keys(result).length, 2, "private error details and transcript text are not returned or logged");
  }
});
