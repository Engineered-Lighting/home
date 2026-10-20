"use strict";
// Chat's "Auto" home (HomeLightingControl.resolveHome): a hand-picked home,
// then the home on screen (an attached chip's home, else the Apartment's),
// then the gateway's location, then the last home acted on, then Los Angeles.
// Synthetic data only; nothing here reaches a home.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "app/src/home-lighting-control.js"), "utf8");
const app = fs.readFileSync(path.join(root, "app/src/home-app.jsx"), "utf8");

function setup({ location = null, last = null } = {}) {
  const fetches = [], parsed = [], store = new Map(last ? [["home.lastActedSite", last]] : []);
  const window = {
    HG_WEB_MODE: true,
    addEventListener() {},
    localStorage: { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) },
    fetch: async url => { fetches.push(url); return { ok: !!location, json: async () => location }; },
    // Stands in for the parser: unnamed commands need a home; an LA default
    // leaves the command to the existing path (null), as the real parser does.
    HomeLightingIntent: { parse(text, home) { parsed.push(home); return home === null ? { needsHome: true } : null; } },
  };
  vm.runInNewContext(source, { window, AbortController, setTimeout, clearTimeout });
  return { api: window.HomeLightingControl, fetches, parsed, store };
}
const LA_PHONE = { version: 1, site: "echo", basis: "phone", label: "your phone is in Los Angeles" };
const VICTORIA_NETWORK = { version: 1, site: "victoria", basis: "network", label: "on the Victoria network" };
const plain = value => JSON.parse(JSON.stringify(value));

test("the viewed home wins over location and last-used", async () => {
  const f = setup({ location: LA_PHONE, last: "echo" });
  assert.deepEqual(plain(await f.api.resolveHome("auto", { viewSite: "victoria" })),
    { site: "victoria", basis: "view", label: "the home you're viewing" });
  const g = setup({ location: VICTORIA_NETWORK, last: "victoria" });
  assert.deepEqual(plain(await g.api.resolveHome("auto", { viewSite: "echo" })),
    { site: "echo", basis: "view", label: "the home you're viewing" });
  assert.deepEqual(f.fetches, []);
  assert.deepEqual(g.fetches, []);
});

test("a manual Echo or Victoria pick wins over the view", async () => {
  const f = setup({ location: LA_PHONE });
  assert.deepEqual(plain(await f.api.resolveHome("victoria", { viewSite: "echo" })), { site: "victoria", basis: "manual", label: "" });
  assert.deepEqual(plain(await f.api.resolveHome("echo", { viewSite: "victoria" })), { site: "echo", basis: "manual", label: "" });
  assert.deepEqual(f.fetches, []);
});

test("no view keeps today's order: location, then last home, then Los Angeles", async () => {
  for (const context of [undefined, {}, { viewSite: null }, { viewSite: "attic" }, { viewSite: ["victoria"] }, "victoria"]) {
    assert.equal((await setup({ location: VICTORIA_NETWORK }).api.resolveHome("auto", context)).basis, "network");
    assert.deepEqual(plain(await setup({ last: "victoria" }).api.resolveHome("auto", context)),
      { site: "victoria", basis: "last", label: "last home used" });
    assert.deepEqual(plain(await setup().api.resolveHome("auto", context)),
      { site: "echo", basis: "default", label: "location unknown" });
  }
});

test("run hands the viewed home to the parser instead of the location", async () => {
  const f = setup({ location: LA_PHONE });
  const options = { addEvent() {}, homeChoice: "auto", viewSite: "echo", isCurrent: () => true };
  assert.equal(await f.api.run("brighter", options), false);
  assert.deepEqual(f.parsed, [null, "echo"]);
  assert.deepEqual(f.fetches, []);
  assert.equal(f.store.get("home.lastActedSite"), "echo");
  const g = setup({ location: VICTORIA_NETWORK });
  assert.equal(await g.api.run("50%", { ...options, viewSite: undefined }), false);
  assert.deepEqual(g.parsed, [null, "victoria"]);
  assert.deepEqual(g.fetches, ["/api/home/location"]);
});

test("Home passes the chip's home, else the Apartment's, to lighting and the Auto label", () => {
  assert.match(app, /const viewSite = chatAbout\?\.siteId\s*\|\| \(unifiedHome && \["echo", "victoria"\]\.includes\(shellState\?\.siteId\) \? shellState\.siteId : null\);/);
  assert.match(app, /HomeLightingControl\?\.run\(text, \{addEvent, homeChoice: homeChoiceRef\.current, viewSite: viewSiteRef\.current,/);
  assert.match(app, /HomeLightingControl\.resolveHome\('auto', \{ viewSite: viewSiteRef\.current \}\)/);
  assert.match(app, /\}, \[homeChoice, viewSite\]\);/);
  // The About chip still claims its message before lighting control.
  assert.ok(app.indexOf("const chatAbout = chatAboutRef.current;") < app.indexOf("window.HomeLightingControl?.run(text"));
});
