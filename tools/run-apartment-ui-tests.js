#!/usr/bin/env node
/* Execute the shared presentation module with a deterministic React-effect
 * harness. No Home controller, renderer, storage, service or room data loads. */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const sourceRoot = path.resolve(__dirname, "../app/src");
const babel = require(path.join(sourceRoot, "vendor/babel-7.29.0/babel.min.js"));
const compiled = babel.transform(fs.readFileSync(path.join(sourceRoot, "home-apartment-ui.jsx"), "utf8"), {
  presets: ["react"], sourceType: "script",
}).code;

function fixture() {
  let now = 0, nextTimer = 0, cursor = 0;
  const timers = new Map(), hooks = [], effects = [];
  const React = {
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
    useRef(value) {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = { current: value };
      return hooks[index];
    },
    useEffect(callback, deps) {
      const index = cursor++, previous = hooks[index];
      if (previous && deps.every((value, i) => value === previous.deps[i])) return;
      effects.push(() => {
        previous?.cleanup?.();
        hooks[index] = { deps, cleanup: callback() };
      });
    },
  };
  const context = {
    React, window: {}, Date: { now: () => now },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(compiled, context, { filename: "home-apartment-ui.jsx", timeout: 1000 });
  const ui = context.window.HomeApartmentUI;
  return {
    ui,
    render(props) {
      cursor = 0;
      const element = ui.Toast(props);
      for (const effect of effects.splice(0)) effect();
      return element;
    },
    unmount() { for (const hook of hooks) hook?.cleanup?.(); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].callback();
      }
      now = end;
    },
    pending: () => timers.size,
  };
}

function children(element) { return element.props.children.filter(Boolean); }
function button(element, name) {
  return children(element).find((child) => child.type === "button" &&
    (child.props["aria-label"] === name || child.props.children[0] === name));
}

test("shared vocabulary includes text and a distinct decorative shape for every agreed status", () => {
  const { ui } = fixture();
  const expected = {
    live: ["Live", "live", "ok"], held: ["Held", "diamond", "attn"],
    stale: ["Stale", "diamond", "attn"], recorded: ["Recorded", "ring", "neutral"],
    authored: ["Authored", "diamond", "neutral"], proposed: ["Proposed", "ring", "proposed"],
    saved: ["Saved", "dot", "ok"], blocked: ["Blocked", "bar", "blocked"],
    reported: ["Reported", "dot", "ok"],
  };
  for (const [state, [label, shape, tone]] of Object.entries(expected)) {
    const badge = ui.Badge({ state });
    assert.equal(badge.props["data-state"], state);
    assert.equal(badge.props["data-tone"], tone);
    assert.equal(badge.props.children[0].props["data-shape"], shape);
    assert.equal(badge.props.children[0].props["aria-hidden"], "true");
    assert.equal(badge.props.children[1].props.children[0], label);
  }
  const custom = ui.Badge({ state: "current", children: "Analyzed 4 s ago" });
  assert.equal(custom.props.children[1].props.children[0], "Analyzed 4 s ago");
  const unknown = ui.Badge({ state: "unsupported-source-state" });
  assert.equal(unknown.props["data-state"], "unavailable");
  assert.equal(unknown.props.children[1].props.children[0], "Unavailable");
});

test("an action consumes itself before callback reentry and remains consumed after a thrown callback", () => {
  const { ui } = fixture();
  let calls = 0, action;
  action = ui.createToastAction(() => { calls++; assert.equal(action.run(), false); }, { isCurrent: () => true });
  assert.equal(action.run(), true);
  assert.equal(action.run(), false);
  assert.equal(action.cancel(), false);
  assert.equal(calls, 1);
  const failing = ui.createToastAction(() => { throw new Error("authored failure"); }, { isCurrent: () => true });
  assert.throws(() => failing.run(), /authored failure/);
  assert.equal(failing.run(), false);
});

test("cancelled or stale actions cannot run, and source currentness is checked at activation", () => {
  const { ui } = fixture();
  let calls = 0, current = true;
  const reasons = [];
  const action = ui.createToastAction(() => calls++, { isCurrent: () => current, onCancel: (reason) => reasons.push(reason) });
  current = false;
  assert.equal(action.run(), false);
  assert.equal(action.active, false);
  assert.deepEqual(reasons, ["stale"]);
  assert.equal(action.cancel("again"), false);
  const throwing = ui.createToastAction(() => calls++, { isCurrent: () => { throw new Error("unavailable"); } });
  assert.equal(throwing.run(), false);
  const unguarded = ui.createToastAction(() => calls++);
  assert.equal(unguarded.run(), false);
  const cancelled = ui.createToastAction(() => calls++, { isCurrent: () => true });
  assert.equal(cancelled.cancel("home changed"), true);
  assert.equal(cancelled.run(), false);
  assert.equal(calls, 0);
});

test("default expiry cancels the action and dismisses the original toast identity", () => {
  const f = fixture(), dismissed = [], reasons = [];
  const action = f.ui.createToastAction(() => assert.fail("expired Undo ran"), { isCurrent: () => true, onCancel: (reason) => reasons.push(reason) });
  const toast = { text: "Preview changed", action };
  f.render({ toast, onDismiss: (value) => dismissed.push(value) });
  f.advance(2799);
  assert.equal(dismissed.length, 0);
  f.advance(1);
  assert.deepEqual(dismissed, [toast]);
  assert.deepEqual(reasons, ["timeout"]);
  assert.equal(action.run(), false);
});

test("action and dismiss are native buttons; a repeated activation executes Undo once", () => {
  const f = fixture(), dismissed = [];
  let calls = 0;
  const toast = { text: "Aim changed", action: f.ui.createToastAction(() => calls++, { isCurrent: () => true }) };
  const element = f.render({ toast, onDismiss: (value) => dismissed.push(value) });
  const undo = button(element, "Undo"), dismiss = button(element, "Dismiss message");
  assert.equal(undo.props.type, "button");
  assert.equal(dismiss.props.type, "button");
  assert.equal(children(element)[0].props.role, "status");
  undo.props.onClick(); undo.props.onClick(); dismiss.props.onClick();
  f.advance(3000);
  assert.equal(calls, 1);
  assert.deepEqual(dismissed, [toast]);
});

test("focus and pointer independently pause the remaining time without restarting the full duration", () => {
  const f = fixture(), dismissed = [];
  const element = f.render({ toast: { text: "Changed", timeoutMs: 1000 }, onDismiss: (value) => dismissed.push(value) });
  f.advance(300);
  element.props.onFocus();
  f.advance(2000);
  element.props.onPointerEnter();
  element.props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null });
  f.advance(2000);
  assert.equal(dismissed.length, 0);
  element.props.onPointerLeave();
  f.advance(699);
  assert.equal(dismissed.length, 0);
  f.advance(1);
  assert.equal(dismissed.length, 1);
});

test("focus moving between toast buttons keeps expiry paused", () => {
  const f = fixture(), dismissed = [];
  const element = f.render({ toast: { text: "Changed", timeoutMs: 100 }, onDismiss: (value) => dismissed.push(value) });
  element.props.onFocus();
  element.props.onBlur({ currentTarget: { contains: () => true }, relatedTarget: {} });
  f.advance(1000);
  assert.equal(dismissed.length, 0);
});

test("replacement and unmount cancel old handles and queued old clicks do not dismiss the newer notice", () => {
  const f = fixture(), dismissed = [];
  let calls = 0;
  const first = { text: "First", action: f.ui.createToastAction(() => calls++, { isCurrent: () => true }), timeoutMs: 100 };
  const oldElement = f.render({ toast: first, onDismiss: (value) => dismissed.push(value) });
  f.advance(40);
  const second = { text: "Second", action: f.ui.createToastAction(() => calls++, { isCurrent: () => true }), timeoutMs: 200 };
  f.render({ toast: second, onDismiss: (value) => dismissed.push(value) });
  button(oldElement, "Undo").props.onClick();
  assert.equal(first.action.run(), false);
  f.advance(100);
  assert.equal(dismissed.length, 0);
  f.unmount();
  assert.equal(second.action.run(), false);
  assert.equal(f.pending(), 0);
  f.advance(1000);
  assert.equal(calls, 0);
  assert.equal(dismissed.length, 0);
});

test("sticky notices do not expire and explicit dismissal cancels the action", () => {
  const f = fixture(), dismissed = [];
  const action = f.ui.createToastAction(() => assert.fail("dismissed action ran"), { isCurrent: () => true });
  const toast = { text: "Review needed", action, sticky: true };
  const element = f.render({ toast, onDismiss: (value) => dismissed.push(value) });
  f.advance(100000);
  assert.equal(f.pending(), 0);
  assert.equal(dismissed.length, 0);
  button(element, "Dismiss message").props.onClick();
  assert.deepEqual(dismissed, [toast]);
  assert.equal(action.run(), false);
});

test("rerendering the same toast preserves its timeout and uses the latest dismissal callback", () => {
  const f = fixture(), dismissed = [];
  const toast = { text: "Changed", timeoutMs: 100 };
  f.render({ toast, onDismiss: () => assert.fail("old dismissal callback ran") });
  f.advance(75);
  f.render({ toast, onDismiss: (value) => dismissed.push(value) });
  f.advance(25);
  assert.deepEqual(dismissed, [toast]);
});

test("the mounted Apartment toast adapter preserves legacy notices and refuses an unguarded callback", () => {
  const f = fixture();
  const source = fs.readFileSync(path.join(sourceRoot, "home-apartment.jsx"), "utf8");
  const ast = babel.transform(source, { parserOpts: { plugins: ["jsx"] }, ast: true, code: false }).ast;
  const declarations = {};
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (node.type === "VariableDeclarator" && ["showToast", "dismissToast"].includes(node.id?.name)) declarations[node.id.name] = node;
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") visit(value);
    }
  }
  visit(ast.program);
  let notice;
  const context = {
    window: { HomeApartmentUI: f.ui },
    setToast: (next) => { notice = typeof next === "function" ? next(notice) : next; },
  };
  const load = (name) => {
    const callback = declarations[name]?.init.arguments[0];
    assert.ok(callback, `actual ${name} callback exists`);
    return vm.runInNewContext("(" + source.slice(callback.start, callback.end) + ")", context, { timeout: 1000 });
  };
  const showToast = load("showToast"), dismissToast = load("dismissToast");
  showToast("Legacy message");
  assert.equal(notice.text, "Legacy message");
  assert.equal(notice.sticky, false);
  assert.equal(notice.action, null);
  assert.equal(notice.timeoutMs, undefined, "the shared presenter owns the unchanged 2800ms default");
  showToast("Legacy sticky", { sticky: true });
  assert.equal(notice.sticky, true);
  showToast("Legacy error", { tone: "error" });
  assert.equal(notice.sticky, true);
  showToast("Refuse unsafe callback", { action: () => assert.fail("unguarded callback ran") });
  assert.equal(notice.action, null);
  let calls = 0, current = true;
  showToast("Guarded Undo", { action: () => calls++, isCurrent: () => current, actionLabel: "Undo", timeoutMs: 900 });
  const original = notice;
  assert.equal(notice.actionLabel, "Undo");
  assert.equal(notice.timeoutMs, 900);
  current = false;
  assert.equal(original.action.run(), false);
  assert.equal(calls, 0);
  showToast("Newer message");
  const newer = notice;
  dismissToast(original);
  assert.equal(notice, newer, "old timer callbacks cannot dismiss a newer notice");
  dismissToast(newer);
  assert.equal(notice, null);
});
