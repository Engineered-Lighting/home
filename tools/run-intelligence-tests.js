#!/usr/bin/env node
/* Pure local tests for app/src/home-intelligence.jsx helper behavior. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-intelligence.jsx");
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
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function approx(actual, expected, eps = 0.001) {
  return Math.abs(actual - expected) <= eps;
}

function makeLocalStorage(initial) {
  const store = new Map(Object.entries(initial || {}).map(([k, v]) => [String(k), String(v)]));
  return {
    getItem(k) { return store.has(String(k)) ? store.get(String(k)) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(String(k)); },
    clear() { store.clear(); },
  };
}

function loadHelpers(opts) {
  opts = opts || {};
  const start = source.indexOf("  const { useCallback");
  const end = source.indexOf("  function Chip", start);
  if (start < 0 || end < 0) throw new Error("home-intelligence helper block not found");
  const script = source.slice(start, end) + `
Object.assign(window, {
  intelligenceBaseFrom,
  fetchJson,
  fmtPct,
  fmtKelvin,
  friendlyEntityName,
  lightValueText,
  transitionBrightnessPct,
  transitionKelvin,
  transitionValueText,
  captureChangeSummary,
  frameEmptyState,
  fmtTime,
  fmtRatio,
  fmtBytes,
});`;
  const calls = [];
  const window = {};
  if (opts.defaultBase) window.HG_DEFAULT_INTELLIGENCE_BASE = opts.defaultBase;
  if (opts.tauriFetch) window.tauriFetch = opts.tauriFetch;
  const sandbox = {
    window,
    localStorage: opts.localStorage || makeLocalStorage(),
    fetch: opts.fetch || (async (url, init) => {
      calls.push({ url, init, via: "fetch" });
      return {
        ok: true,
        status: 200,
        async json() { return { ok: true, url }; },
      };
    }),
    React: {
      useCallback() {},
      useEffect() {},
      useMemo() {},
      useState() {},
    },
    URL,
    Date,
    JSON,
    Number,
    String,
    Math,
    isFinite,
  };
  vm.runInNewContext(script, sandbox, { filename: "home-intelligence.helpers.js" });
  return { H: sandbox.window, calls, localStorage: sandbox.localStorage };
}

async function expectReject(name, promise, pattern) {
  try {
    await promise;
    assert(name, false, "expected rejection");
  } catch (err) {
    assert(name, pattern.test(err && err.message || String(err)), err && err.message);
  }
}

async function main() {
  process.stdout.write("\nintelligence_base_and_fetch_test\n");
  const base = loadHelpers();
  const H = base.H;
  assert("helpers exported", typeof H.intelligenceBaseFrom === "function" && typeof H.captureChangeSummary === "function");
  assert("default intelligence base is stable", H.intelligenceBaseFrom("", "") === "http://192.168.0.100:8095", H.intelligenceBaseFrom("", ""));
  assert("metrics base maps to intelligence port", H.intelligenceBaseFrom("http://ai-box.local:8092/path", "") === "http://ai-box.local:8095", H.intelligenceBaseFrom("http://ai-box.local:8092/path", ""));
  assert("invalid metrics base falls back to default", H.intelligenceBaseFrom("not a url", "") === "http://192.168.0.100:8095", H.intelligenceBaseFrom("not a url", ""));
  const saved = loadHelpers({ localStorage: makeLocalStorage({ "hg-intelligence-base": "http://saved.local:8095///" }) });
  assert("saved intelligence base wins and trims slashes", saved.H.intelligenceBaseFrom("http://ignored:8092", "") === "http://saved.local:8095", saved.H.intelligenceBaseFrom("http://ignored:8092", ""));
  const def = loadHelpers({ defaultBase: "http://default.local:8095///" });
  assert("window default base is used when no saved base", def.H.intelligenceBaseFrom("", "") === "http://default.local:8095", def.H.intelligenceBaseFrom("", ""));

  const fetchCalls = [];
  const fetchLoad = loadHelpers({
    fetch: async (url, init) => {
      fetchCalls.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ url, method: init.method || "GET" }) };
    },
  });
  const fetched = await fetchLoad.H.fetchJson("http://intel.local/api", { method: "POST", body: "{}" });
  assert("fetchJson returns parsed body", fetched.method === "POST", fetched);
  assert("fetchJson injects no-store cache", fetchCalls[0].init.cache === "no-store", fetchCalls[0]);
  assert("fetchJson preserves caller init", fetchCalls[0].init.method === "POST" && fetchCalls[0].init.body === "{}", fetchCalls[0]);
  const tauriCalls = [];
  const tauriLoad = loadHelpers({
    tauriFetch: async (url, init) => {
      tauriCalls.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ via: "tauri" }) };
    },
    fetch: async () => {
      throw new Error("browser fetch should not run");
    },
  });
  assert("fetchJson prefers tauriFetch", (await tauriLoad.H.fetchJson("http://intel.local/api")).via === "tauri" && tauriCalls.length === 1, tauriCalls);
  const errLoad = loadHelpers({ fetch: async () => ({ ok: false, status: 503, json: async () => ({}) }) });
  await expectReject("fetchJson throws HTTP status", errLoad.H.fetchJson("http://intel.local/down"), /HTTP 503/);

  process.stdout.write("\nintelligence_light_formatting_test\n");
  assert("fmtPct rounds finite values", H.fmtPct(42.4) === "42%" && H.fmtPct(42.6) === "43%");
  assert("fmtPct handles empty values", H.fmtPct(null) === "-" && H.fmtPct(Number.NaN) === "-");
  assert("fmtKelvin rounds finite values", H.fmtKelvin(2700.6) === "2701K", H.fmtKelvin(2700.6));
  assert("fmtKelvin returns null for empty values", H.fmtKelvin(null) === null && H.fmtKelvin(Number.NaN) === null);
  assert("friendlyEntityName appends light when needed", H.friendlyEntityName("light.kitchen_sink") === "kitchen sink light", H.friendlyEntityName("light.kitchen_sink"));
  assert("friendlyEntityName avoids duplicate light suffix", H.friendlyEntityName("light.desk_light") === "desk light", H.friendlyEntityName("light.desk_light"));
  assert("friendlyEntityName handles non-light domains", H.friendlyEntityName("sensor.front_door") === "front door", H.friendlyEntityName("sensor.front_door"));
  assert("friendlyEntityName handles blank fallback", H.friendlyEntityName("", "zone light") === "zone light");
  assert("lightValueText returns off state", H.lightValueText({ lightState: "off", brightnessPct: 90, colorTempKelvin: 2700 }) === "off");
  assert("lightValueText combines kelvin and brightness", H.lightValueText({ brightnessPct: 42, colorTempKelvin: 2700 }) === "2700K 42%");
  assert("lightValueText returns empty text when unknown", H.lightValueText({ empty: "observed" }) === "observed");
  assert("transitionBrightnessPct uses explicit pct", H.transitionBrightnessPct({ brightness_pct: "66" }) === 66);
  assert("transitionBrightnessPct maps off to zero", H.transitionBrightnessPct({ state: "off" }) === 0);
  assert("transitionBrightnessPct converts raw HA brightness", approx(H.transitionBrightnessPct({ brightness: 128 }), 50.196), H.transitionBrightnessPct({ brightness: 128 }));
  assert("transitionKelvin uses explicit kelvin", H.transitionKelvin({ color_temp_kelvin: "3000" }) === 3000);
  assert("transitionKelvin converts mireds", approx(H.transitionKelvin({ color_temp: 400 }), 2500), H.transitionKelvin({ color_temp: 400 }));
  assert("transitionValueText formats converted state", H.transitionValueText({ brightness: 128, color_temp: 400 }) === "2500K 50%", H.transitionValueText({ brightness: 128, color_temp: 400 }));

  process.stdout.write("\nintelligence_capture_summary_test\n");
  const pending = H.captureChangeSummary({
    event_type: "pending_preference",
    zone: "kitchen_sink",
  }, {
    light_entity: "light.kitchen_sink",
    actual_brightness_pct: 78,
    actual_color_temp_kelvin: 3100,
    predicted_brightness_pct: 15,
    predicted_color_temp_kelvin: 2200,
    capture_provenance: { capture_trigger: "vacancy_settle" },
    profile: "evening",
    context: { tv_playing: true, asleep: true },
    shadow_mode: true,
  });
  assert("pending summary titles captured preference", pending.title === "kitchen sink light preference captured", pending);
  assert("pending summary compares automation target to capture", pending.beforeLabel === "automation would have done" && pending.before === "2200K 15%" && pending.after === "3100K 78%", pending);
  assert("pending summary explains vacancy-settle durability", /durable preference evidence/.test(pending.note), pending.note);
  assert("pending summary includes context bits", pending.context === "evening / TV on / asleep / shadow", pending.context);

  const override = H.captureChangeSummary({
    event_type: "override_event",
    zone: "office",
  }, {
    light_entity: "light.office_ceiling",
    predicted_brightness_pct: 30,
    predicted_color_temp_kelvin: 2400,
    actual_brightness_pct: 100,
    actual_color_temp_kelvin: 4000,
    light_transition: {
      from: { state: "on", brightness_pct: 30, color_temp_kelvin: 2400 },
      to: { state: "on", brightness_pct: 100, color_temp_kelvin: 4000 },
    },
  });
  assert("override summary uses actual transition", override.eyebrow === "actual transition" && override.before === "2400K 30%" && override.after === "4000K 100%", override);
  assert("override summary mentions system target", /System target was 2400K 30%/.test(override.note), override.note);

  const overrideNoTransition = H.captureChangeSummary({
    event_type: "override_event",
    zone: "living_room",
  }, {
    light_entity: "light.living_room",
    predicted_brightness_pct: 3,
    actual_brightness_pct: 80,
  });
  assert("override summary falls back without transition", overrideNoTransition.beforeLabel === "system target" && overrideNoTransition.afterLabel === "observed light", overrideNoTransition);

  const generic = H.captureChangeSummary({
    event_type: "observation_packet",
    zone: "front_door",
  }, {
    actual_brightness_pct: 12,
  });
  assert("generic summary handles observations", generic.title === "front door observation captured" && generic.before === "observation packet" && generic.after === "12%", generic);

  process.stdout.write("\nintelligence_empty_frames_and_misc_test\n");
  assert("deleted packet empty state is explicit", H.frameEmptyState({ status: "frames_deleted" }).title === "Frames deleted");
  const missing = H.frameEmptyState({ status: "frames_missing", error: "camera offline" });
  assert("missing frames surface packet error", missing.title === "Frames unavailable" && missing.detail === "camera offline", missing);
  const backfilled = H.frameEmptyState({ frame_count: 0, trigger: "api_backfill" }, { source: "none" });
  assert("backfilled no-frame state explains existing evidence", /existing HA evidence/.test(backfilled.detail), backfilled);
  const noFrames = H.frameEmptyState({ frame_count: 0, trigger: "live" }, { source: "none" });
  assert("live no-frame state explains metadata-only observation", /metadata/.test(noFrames.detail), noFrames);
  assert("default empty-frame state treats missing count as zero frames", H.frameEmptyState({}).title === "No frames captured");
  assert("fmtTime handles empty and invalid timestamps", H.fmtTime(null) === "-" && H.fmtTime("not-a-date-value").startsWith("not-a-date"));
  assert("fmtRatio rounds fractions", H.fmtRatio(0.456) === "46%" && H.fmtRatio(null) === "-");
  assert("fmtBytes formats bytes, KB, and MB", H.fmtBytes(42) === "42 B" && H.fmtBytes(2048) === "2.0 KB" && H.fmtBytes(3 * 1024 * 1024) === "3.0 MB");

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
