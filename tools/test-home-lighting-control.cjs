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


test("Serve runs the inline card without Agent sign-in and recovers by lookup after reload", async () => {
  const storage = new Map(), calls = [];
  const id = "11111111-1111-4111-8111-111111111111";
  const statuses = [{site_id:"echo",name:"Kitchen",operation:"off",brightness:null,status:"succeeded"},
    {site_id:"victoria",name:"Kitchen",operation:"off",brightness:null,status:"succeeded"}];
  const flush = () => new Promise(resolve => setImmediate(resolve));
  function browser() {
    function element(tag) {
      assert.notEqual(tag, "iframe");
      return {
        tag, children: [], click() { this.handler(); }, setAttribute() {},
        append(...children) { this.children.push(...children); },
        addEventListener(_name, handler) { this.handler = handler; },
      };
    }
    const window = {
      HomeServe: { active: true }, HG_WEB_MODE: true, addEventListener() {},
      document: { createElement: element }, crypto: { randomUUID: () => id },
      sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
      HomeLightingIntent: { parse: () => ({ request: { version: 1, sites: ["echo","victoria"],
        targets: ["kitchen"], operation: "off", brightness: null } }) },
      fetch: async (url, init) => {
        calls.push({ url, init });
        assert.equal(init.credentials, "omit");
        assert.equal(init.headers["X-Home-Lighting-CSRF"], "1");
        assert.equal(JSON.parse(init.body).operation_id, id);
        if (url.endsWith("/action")) {
          assert.match(storage.get("home.serveLighting.pending.v1"), new RegExp(id));
          throw new Error("lost reply");
        }
        return { ok: true, json: async () => ({ version:1, result:{version:1, operation_id:id, status:"done", results:statuses} }) };
      }
    };
    vm.runInNewContext(source, { window, AbortController, setTimeout, clearTimeout });
    const events = [];
    const addEvent = event => events.push(event);
    return { api: window.HomeLightingControl, addEvent, events, element };
  }
  const first = browser();
  assert.equal(first.api.run("Turn off the kitchen in both homes", { addEvent:first.addEvent, isCurrent: () => true }), true);
  await flush();
  const second = browser();
  second.api.restorePending(second.addEvent);
  second.api.restorePending(second.addEvent);
  assert.equal(second.events.length, 1);
  const slot = second.element("div");
  second.events[0].mountReview({ appendChild: card => slot.append(card) });
  const [message, check] = slot.children[0].children;
  assert.equal(check.hidden, false);
  check.click();
  await flush();
  assert.match(message.textContent, /Done/);
  assert.equal(check.hidden, true);
  assert.deepEqual(calls.map(call => call.url), ["/api/home/lighting/action", "/api/home/lighting/outcome"]);
  assert.equal(storage.get("home.serveLighting.pending.v1"), "[]");
});
