#!/usr/bin/env node
/* Deterministic Apartment shell routing tests. Execute the actual resolver from
 * home-app.jsx without mounting Home, opening sockets, or contacting services.
 * Browser/renderer composition remains a separate authored-fixture acceptance.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const baselineSourceRoot = path.resolve(__dirname, "../app/src");
const candidateRoot = process.env.HOME_APARTMENT_CANDIDATE_ROOT
  ? path.resolve(process.env.HOME_APARTMENT_CANDIDATE_ROOT) : null;
const sourceRoot = candidateRoot
  ? fs.existsSync(path.join(candidateRoot, "app/src/home-app.jsx"))
    ? path.join(candidateRoot, "app/src") : candidateRoot
  : baselineSourceRoot;
const source = fs.readFileSync(path.join(sourceRoot, "home-app.jsx"), "utf8");
const babel = require(path.join(baselineSourceRoot, "vendor/babel-7.29.0/babel.min.js"));
const parsed = babel.transform(source, {
  parserOpts: { plugins: ["jsx"] },
  ast: true,
  code: false,
});
const declaration = parsed.ast.program.body.find((node) =>
  node.type === "FunctionDeclaration" && node.id?.name === "resolveApartmentHomeLayout");
assert.ok(declaration, "home-app.jsx must declare the actual Apartment layout resolver");

// Evaluating only the resolver avoids every app, simulation, recording, camera,
// service and household-storage initialization effect in the surrounding module.
const resolveLayout = vm.runInNewContext(
  "(" + source.slice(declaration.start, declaration.end) + ")",
  { URLSearchParams },
  { filename: "home-app.apartment-shell-resolver.js", timeout: 1000 },
);
const preferenceKey = "home.apartmentRedesign.v1";

function storage(values = {}, { blocked = false } = {}) {
  const reads = [], writes = [];
  return {
    reads,
    writes,
    getItem(key) {
      reads.push(key);
      if (blocked) throw new Error("authored blocked storage");
      return Object.hasOwn(values, key) ? values[key] : null;
    },
    setItem(...args) {
      writes.push(["setItem", ...args]);
      throw new Error("layout resolution must not save preferences");
    },
    removeItem(...args) {
      writes.push(["removeItem", ...args]);
      throw new Error("layout resolution must not remove preferences");
    },
    clear() {
      writes.push(["clear"]);
      throw new Error("layout resolution must not clear household storage");
    },
  };
}

test("Apartment is the default and invalid saved preferences do not strand startup", () => {
  for (const value of [null, "", "unified", "spatial", "true", "false", "{broken}"]) {
    assert.equal(resolveLayout("", storage({ [preferenceKey]: value })), "unified", String(value));
  }
  assert.equal(resolveLayout("?unrelated=1", storage()), "unified");
  assert.equal(resolveLayout("", storage({}, { blocked: true })), "unified");
});

test("explicit redesign on and off outrank legacy links and either saved choice", () => {
  for (const preference of [null, "0", "1"]) {
    for (const legacy of ["layout=classic", "view=spatial", "home2", "spatial2", "spatial-home"]) {
      const saved = { [preferenceKey]: preference, "hg-layout-v2": "spatial" };
      assert.equal(resolveLayout("?apartmentRedesign=1&" + legacy, storage(saved)), "unified", legacy);
      assert.equal(resolveLayout("?apartmentRedesign=0&" + legacy, storage(saved)), "classic", legacy);
    }
  }
});

test("every recognized explicit legacy layout value survives the default change", () => {
  for (const key of ["layout", "view", "home2"]) {
    for (const value of ["spatial", "home2", "2"]) {
      for (const preference of ["0", "1"]) {
        assert.equal(resolveLayout("?" + key + "=" + value, storage({ [preferenceKey]: preference })),
          "spatial", key + "=" + value);
      }
    }
    for (const value of ["classic", "chat", "off", "1"]) {
      for (const preference of ["0", "1"]) {
        assert.equal(resolveLayout("?" + key + "=" + value, storage({ [preferenceKey]: preference })),
          "classic", key + "=" + value);
      }
    }
  }
  assert.equal(resolveLayout("?layout=SPATIAL", storage()), "spatial");
  assert.equal(resolveLayout("?view=CLASSIC", storage()), "classic");
});

test("legacy bare spatial switches and the empty home2 shorthand stay usable", () => {
  for (const search of ["?spatial2", "?spatial-home", "?home2", "?home2="]) {
    assert.equal(resolveLayout(search, storage({ [preferenceKey]: "0" })), "spatial", search);
  }
});

test("the namespaced saved choice outranks the new default without reviving legacy storage", () => {
  for (const legacy of [null, "classic", "spatial"]) {
    assert.equal(resolveLayout("", storage({ [preferenceKey]: "1", "hg-layout-v2": legacy })), "unified");
    assert.equal(resolveLayout("", storage({ [preferenceKey]: "0", "hg-layout-v2": legacy })), "classic");
    assert.equal(resolveLayout("", storage({ "hg-layout-v2": legacy })), "unified");
  }
});

test("unknown redesign values fall through to a recognized legacy link or saved choice", () => {
  for (const value of ["", "true", "false", "unified", "01", "invalid"]) {
    assert.equal(resolveLayout("?apartmentRedesign=" + value + "&layout=spatial", storage({ [preferenceKey]: "0" })), "spatial");
    assert.equal(resolveLayout("?apartmentRedesign=" + value, storage({ [preferenceKey]: "0" })), "classic");
    assert.equal(resolveLayout("?apartmentRedesign=" + value, storage()), "unified");
  }
});

test("unknown legacy values preserve the saved redesign choice and safe default", () => {
  for (const search of ["?layout=unknown", "?view=unknown", "?home2=unknown", "?layout=", "?view="]) {
    assert.equal(resolveLayout(search, storage({ [preferenceKey]: "0" })), "classic", search);
    assert.equal(resolveLayout(search, storage({ [preferenceKey]: "1" })), "unified", search);
    assert.equal(resolveLayout(search, storage()), "unified", search);
  }
});

test("explicit routes remain usable with blocked storage and do not consult household preferences", () => {
  for (const [search, expected] of [
    ["?apartmentRedesign=1", "unified"], ["?apartmentRedesign=0", "classic"],
    ["?layout=spatial", "spatial"], ["?layout=classic", "classic"], ["?home2", "spatial"],
  ]) {
    const saved = storage({}, { blocked: true });
    assert.equal(resolveLayout(search, saved), expected, search);
    assert.deepEqual(saved.reads, [], "explicit routing must not require browser storage");
    assert.deepEqual(saved.writes, []);
  }
});

test("layout resolution reads only its namespaced preference and never mutates storage", () => {
  const saved = storage({ [preferenceKey]: "0", "hg-prefs": "PRIVATE", "hg-layout-v2": "spatial" });
  assert.equal(resolveLayout("?layout=unknown", saved), "classic");
  assert.deepEqual([...new Set(saved.reads)], [preferenceKey]);
  assert.deepEqual(saved.writes, []);
  assert.equal(resolveLayout("?apartmentRedesign=1", saved), "unified");
  assert.equal(resolveLayout("?apartmentRedesign=0", saved), "classic");
  assert.deepEqual(saved.writes, []);
});

function findLightsAskExternal() {
  const callbacks = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (node.type === "JSXOpeningElement" && node.name?.type === "JSXMemberExpression"
        && node.name.object?.name === "window" && node.name.property?.name === "HomeLightsDrawer") {
      const attribute = node.attributes.find(value => value.type === "JSXAttribute" && value.name?.name === "askExternal");
      if (attribute?.value?.type === "JSXExpressionContainer") callbacks.push(attribute.value.expression);
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") visit(value);
    }
  }
  visit(parsed.ast.program);
  assert.equal(callbacks.length, 1, "the actual HomeLightsDrawer must receive exactly one askExternal callback");
  return source.slice(callbacks[0].start, callbacks[0].end);
}

for (const unifiedHome of [true, false]) {
  test("Lights askExternal prepares a reviewable /ask draft" + (unifiedHome ? " and reveals/focuses unified Chat" : " in classic Home"), () => {
    const drafts = [], tabs = [], sends = [];
    let focusToken = 7;
    const forbiddenSend = (...args) => { sends.push(args); throw new Error("draft preparation must never send"); };
    const callback = vm.runInNewContext("(" + findLightsAskExternal() + ")", {
      unifiedHome,
      setInput: value => drafts.push(value),
      showShellTab: value => tabs.push(value),
      setFocusToken: update => { focusToken = typeof update === "function" ? update(focusToken) : update; },
      sendInput: forbiddenSend,
      sendToHA: forbiddenSend,
      dispatchExternal: forbiddenSend,
      handleCommand: forbiddenSend,
      fetch: forbiddenSend,
    }, { filename: "home-app.lights-ask-external-callback.js", timeout: 1000 });
    callback({ title: "Authored dimming layer", intro: "Authored context for review", fileLocation: "authored/lighting.py",
      whyFrozen: "Needs an explicit owner choice", userIntent: "Make the transition slower",
      knobs: [{ name: "transition_seconds", current: 0, desc: "Authored current value" }] });
    assert.equal(drafts.length, 1);
    assert.match(drafts[0], /^\/ask /);
    for (const expected of ["Authored dimming layer", "Authored context for review", "authored/lighting.py",
      "Needs an explicit owner choice", "Make the transition slower", "transition_seconds = 0", "Authored current value"]) {
      assert.ok(drafts[0].includes(expected), "reviewable draft retains " + expected);
    }
    assert.deepEqual(tabs, unifiedHome ? ["chat"] : []);
    assert.equal(focusToken, unifiedHome ? 8 : 7);
    assert.deepEqual(sends, [], "the user must explicitly send the prepared draft");
  });
}
