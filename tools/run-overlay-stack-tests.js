#!/usr/bin/env node
/* ============================================================================
 * run-overlay-stack-tests.js — behavioral tests for window.HomeOverlay
 * (app/src/home-overlay.js), the shared overlay layer stack.
 *
 * The core is framework-free; this harness vm-loads it with a stubbed
 * window/document and exercises the Escape dispatch, the scoped input
 * guard, stack ordering, passive layers, and the focus-restore chain.
 * ========================================================================== */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC_FILE = path.join(REPO, "app", "src", "home-overlay.js");

let fails = 0;
const failures = [];
function assert(name, ok, detail) {
  if (ok) {
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails += 1;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name + "\n");
    if (detail !== undefined) {
      process.stdout.write("        " + JSON.stringify(detail) + "\n");
    }
  }
}

/* ── stub DOM ─────────────────────────────────────────────────────────── */
function stubEl(name) {
  return {
    name,
    tagName: "DIV",
    isConnected: true,
    isContentEditable: false,
    focusedCount: 0,
    children: [],
    focus() { this.focusedCount += 1; ctx.window.__lastFocused = this; },
    contains(x) {
      if (x === this) return true;
      return this.children.indexOf(x) >= 0;
    },
    getClientRects() { return [{}]; },
    querySelectorAll() { return this.children.filter((c) => !c.disabled); },
    hasAttribute() { return true; },
    setAttribute() {},
  };
}

function makeSandbox() {
  const listeners = {};
  const win = {
    __lastFocused: null,
    addEventListener(type, fn) { listeners[type] = listeners[type] || []; listeners[type].push(fn); },
    removeEventListener() {},
    requestAnimationFrame(fn) { fn(); },   // synchronous for determinism
    matchMedia() { return { matches: false }; },
    __listeners: listeners,
  };
  const doc = {
    activeElement: null,
    querySelector() { return null; },
  };
  win.document = doc;
  const sandbox = { window: win, document: doc, setTimeout, clearTimeout, module: undefined, console };
  vm.createContext(sandbox);
  return sandbox;
}

const source = fs.readFileSync(SRC_FILE, "utf8");
const ctx = makeSandbox();
vm.runInContext(source, ctx, { filename: "home-overlay.js" });
const O = ctx.window.HomeOverlay;

function escEvent(target) {
  return {
    key: "Escape",
    target: target || null,
    defaultPrevented: false,
    stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopImmediatePropagation() { this.stopped = true; },
  };
}

process.stdout.write("\noverlay_stack_basics_test\n");
assert("HomeOverlay is exported", O && typeof O.push === "function");
assert("capture keydown listener installed at load",
  ctx.window.__listeners.keydown && ctx.window.__listeners.keydown.length === 1);
assert("empty stack: topKey null, no blocking layer",
  O.topKey() === null && O.hasBlockingLayer() === false);

process.stdout.write("\noverlay_escape_dispatch_test\n");
{
  const calls = [];
  const rootA = stubEl("rootA");
  const rootB = stubEl("rootB");
  const hA = O.push({ key: "A", onEscape: () => { calls.push("A"); }, getRoot: () => rootA });
  const hB = O.push({ key: "B", onEscape: () => { calls.push("B"); }, getRoot: () => rootB });
  assert("topKey is the last pushed", O.topKey() === "B" && O.isTopmost("B") && !O.isTopmost("A"));
  const e1 = escEvent();
  O._handleKeydown(e1);
  assert("Escape goes to the topmost layer only",
    calls.length === 1 && calls[0] === "B", calls);
  assert("claimed Escape is consumed (preventDefault + stopImmediatePropagation)",
    e1.defaultPrevented && e1.stopped);
  hB.pop();
  const e2 = escEvent();
  O._handleKeydown(e2);
  assert("after pop, next layer receives Escape", calls.length === 2 && calls[1] === "A", calls);
  hA.pop();
  assert("stack drains to empty", O._stackSize() === 0);
}

process.stdout.write("\noverlay_claim_or_pass_test\n");
{
  let passCalls = 0;
  const h = O.push({ key: "passer", onEscape: () => { passCalls += 1; return false; }, passive: true });
  const e = escEvent();
  O._handleKeydown(e);
  assert("pass (return false) leaves the event unconsumed",
    passCalls === 1 && !e.defaultPrevented && !e.stopped);
  assert("passive layer does not count as blocking", O.hasBlockingLayer() === false);
  h.pop();
}

process.stdout.write("\noverlay_input_guard_test\n");
{
  let calls = 0;
  const root = stubEl("root");
  const insideInput = stubEl("insideInput");
  insideInput.tagName = "INPUT";
  root.children.push(insideInput);
  const outsideInput = stubEl("outsideInput");
  outsideInput.tagName = "TEXTAREA";
  const h = O.push({ key: "layer", onEscape: () => { calls += 1; }, getRoot: () => root });
  const eInside = escEvent(insideInput);
  O._handleKeydown(eInside);
  assert("editable INSIDE the top layer keeps its own Escape (yielded)",
    calls === 0 && !eInside.defaultPrevented);
  const eOutside = escEvent(outsideInput);
  O._handleKeydown(eOutside);
  assert("editable OUTSIDE the layer does not swallow Escape (layer closes)",
    calls === 1 && eOutside.defaultPrevented, { calls });
  h.pop();
}

process.stdout.write("\noverlay_out_of_order_pop_test\n");
{
  const hA = O.push({ key: "A", onEscape: () => {} });
  const hB = O.push({ key: "B", onEscape: () => {} });
  const hC = O.push({ key: "C", onEscape: () => {} });
  hB.pop();               // middle pop
  assert("middle pop keeps order", O.topKey() === "C" && O._stackSize() === 2);
  hB.pop();               // double pop is a no-op
  assert("double pop is safe", O._stackSize() === 2);
  hC.pop(); hA.pop();
  assert("drained", O._stackSize() === 0);
}

process.stdout.write("\noverlay_focus_restore_test\n");
{
  // opener connected → restored
  const opener = stubEl("opener");
  ctx.document.activeElement = opener;
  const h1 = O.push({ key: "f1", onEscape: () => {}, initialFocus: "none" });
  h1.pop();
  assert("focus returns to the recorded opener", opener.focusedCount === 1);

  // opener unmounted → restoreTo
  const gone = stubEl("gone");
  gone.isConnected = false;
  const alt = stubEl("alt");
  ctx.document.activeElement = gone;
  const h2 = O.push({ key: "f2", onEscape: () => {}, initialFocus: "none", restoreTo: () => alt });
  h2.pop();
  assert("disconnected opener falls back to restoreTo", alt.focusedCount === 1);

  // neither → app fallback
  const fb = stubEl("fallback");
  O.setFallbackFocus(() => fb);
  ctx.document.activeElement = gone;
  const h3 = O.push({ key: "f3", onEscape: () => {}, initialFocus: "none" });
  h3.pop();
  assert("last resort is the app fallback target", fb.focusedCount === 1);
}

process.stdout.write("\noverlay_focus_in_test\n");
{
  const root = stubEl("root");
  const btn = stubEl("btn");
  btn.tagName = "BUTTON";
  root.children.push(btn);
  ctx.document.activeElement = null;
  const h = O.push({ key: "fin", onEscape: () => {}, getRoot: () => root, initialFocus: "first" });
  assert("initialFocus 'first' focuses the first focusable in the root", btn.focusedCount === 1);
  h.pop();
}

process.stdout.write("\noverlay_subscribe_test\n");
{
  const seen = [];
  const un = O.subscribe((size, key) => seen.push([size, key]));
  const h = O.push({ key: "sub", onEscape: () => {} });
  h.pop();
  un();
  assert("subscribers see push and pop", seen.length === 2 && seen[0][1] === "sub" && seen[1][0] === 0, seen);
}

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name);
}
console.log("\n" + (fails === 0 ? "all green" : fails + " fail(s)"));
process.exit(fails ? 1 : 0);
