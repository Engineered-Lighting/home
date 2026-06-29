#!/usr/bin/env node
/* Pure local tests for app/src/home-services.js. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-services.js");
const source = fs.readFileSync(SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function makeLocalStorage(initial) {
  const store = new Map(Object.entries(initial || {}).map(([k, v]) => [String(k), String(v)]));
  return {
    get length() { return store.size; },
    key(i) { return Array.from(store.keys())[i] || null; },
    getItem(k) { return store.has(String(k)) ? store.get(String(k)) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(String(k)); },
    clear() { store.clear(); },
    _store: store,
  };
}

function loadModule(opts = {}) {
  const localStorage = makeLocalStorage(opts.storage);
  const fetchCalls = [];
  const fetcher = opts.fetch || (async (url) => {
    fetchCalls.push(url);
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
      text: async () => "ok",
    };
  });
  const window = {
    HG_WEB_MODE: !!opts.webMode,
    localStorage,
    fetch: fetcher,
    location: {
      protocol: "https:",
      host: "formd-t1.taild52a15.ts.net",
      origin: "https://formd-t1.taild52a15.ts.net",
    },
    dispatchEvent: () => {},
  };
  const sandbox = {
    window,
    localStorage,
    console: { log: () => {}, warn: () => {}, error: () => {} },
    CustomEvent: function CustomEvent(type, init) { this.type = type; this.detail = init && init.detail; },
    Date,
    Error,
    JSON,
    Promise,
    setTimeout,
    URL,
  };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return { window, localStorage, fetchCalls };
}

async function main() {
  process.stdout.write("\nhome_services_lan_test\n");
  const lan = loadModule();
  assert("default profile is Home LAN", lan.window.HomeServices.getProfile().id === "home-lan");
  assert("LAN HA default is preserved", lan.window.HomeServices.get("ha") === "http://192.168.0.125:8123");
  assert("LAN metrics default is preserved", lan.window.HomeServices.get("metrics") === "http://192.168.0.100:8092");
  assert("runtime globals are refreshed", lan.window.HG_DEFAULT_S2S_BASE === "http://192.168.0.100:8094");

  process.stdout.write("\nhome_services_web_mode_test\n");
  const web = loadModule({
    webMode: true,
    storage: { "hg-services-v1": JSON.stringify({ profile: "tailscale" }) },
  });
  assert("browser mode reports web gateway profile", web.window.HomeServices.getProfile().id === "web");
  assert("browser mode HA uses same-origin proxy", web.window.HomeServices.get("ha") === "/proxy/ha");
  assert("browser mode metrics candidates stay same-origin", web.window.HomeServices.candidates("metrics")[0] === "/proxy/metrics");
  assert("browser mode apartment assets stay same-origin", web.window.HG_DEFAULT_APARTMENT_ASSET_BASE === "/assets/apartment");

  process.stdout.write("\nhome_services_tailscale_test\n");
  const tail = loadModule();
  tail.window.HomeServices.setProfile("tailscale");
  const haCandidates = tail.window.HomeServices.candidates("ha");
  assert("Tailscale profile is active", tail.window.HomeServices.getProfile().id === "tailscale");
  assert("Tailscale HA short MagicDNS is first", haCandidates[0] === "http://homeassistant:8123", haCandidates);
  assert("Tailscale HA full tailnet DNS is available", haCandidates.includes("http://homeassistant.taild52a15.ts.net:8123"), haCandidates);
  assert("Tailscale HA observed 100.x fallback is available", haCandidates.includes("http://100.116.3.41:8123"), haCandidates);
  assert("runtime globals move with profile", tail.window.HG_DEFAULT_VLLM_BASE === "http://engineeredlightingserver1:8000");

  process.stdout.write("\nhome_services_custom_and_legacy_test\n");
  const legacy = loadModule({
    storage: {
      "videoLabeler.base": "http://stale-video-labeler.invalid:9",
      "apartment3d.trackerBase": "http://stale-tracker.invalid:9",
      "hg-intelligence-base": "http://stale-intelligence.invalid:9",
    },
  });
  assert("legacy video labeler key does not override resolver", legacy.window.HomeServices.get("videoLabeler") === "http://192.168.0.100:8099");
  assert("legacy tracker key does not override resolver", legacy.window.HomeServices.get("tracker") === "http://192.168.0.100:8098");
  assert("legacy intelligence key does not override resolver", legacy.window.HomeServices.get("intelligence") === "http://192.168.0.100:8095");
  legacy.window.HomeServices.setOverride("metrics", "http://custom-ai:9002/");
  assert("per-service override switches to custom profile", legacy.window.HomeServices.getProfile().id === "custom");
  assert("per-service override is normalized", legacy.window.HomeServices.get("metrics") === "http://custom-ai:9002");
  assert("custom profile seeds other services from the prior profile", legacy.window.HomeServices.get("ha") === "http://192.168.0.125:8123");

  process.stdout.write("\nhome_services_probe_test\n");
  const probeCalls = [];
  const probe = loadModule({
    fetch: async (url) => {
      probeCalls.push(url);
      if (String(url).includes("engineeredlightingserver1:8092/healthz")) {
        return { ok: false, status: 502, json: async () => ({}), text: async () => "" };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => "ok" };
    },
  });
  probe.window.HomeServices.setProfile("tailscale");
  const results = await probe.window.HomeServices.probeAll();
  const metrics = results.find((r) => r.service === "metrics");
  assert("probeAll tests every service", results.length === probe.window.HomeServices.services.length, results.length);
  assert("probeAll records failed and successful attempts", metrics.attempts.length === 2 && metrics.ok, metrics);
  assert("probeAll selects the first healthy Tailscale fallback", probe.window.HomeServices.get("metrics") === "http://engineeredlightingserver1.taild52a15.ts.net:8092", metrics);
  assert("probeAll persisted selected URL into runtime globals", probe.window.HG_DEFAULT_METRICS_BASE === "http://engineeredlightingserver1.taild52a15.ts.net:8092");

  process.stdout.write(`\n${passes} passed, ${fails} failed\n`);
  if (fails) {
    process.stdout.write(JSON.stringify(failures, null, 2) + "\n");
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
