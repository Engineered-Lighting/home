#!/usr/bin/env node
/* Pure local tests for app/src/index.html bootstrap and mount contracts. */
"use strict";

const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const SRC_DIR = path.join(REPO, "app", "src");
const INDEX = path.join(SRC_DIR, "index.html");
const MOUNT = path.join(SRC_DIR, "home-mount.jsx");
const HOME_APP = path.join(SRC_DIR, "home-app.jsx");
const AUDIT = path.join(REPO, "tools", "qa-registry-audit.py");

const indexSource = fs.readFileSync(INDEX, "utf8");
const runtimeSource = fs.readFileSync(path.join(SRC_DIR, "home-web-runtime.js"), "utf8");
const serviceWorkerSource = fs.readFileSync(path.join(SRC_DIR, "home-service-worker.js"), "utf8");
const mountSource = fs.readFileSync(MOUNT, "utf8");
const appSource = fs.readFileSync(HOME_APP, "utf8");
const iconsSource = fs.readFileSync(path.join(SRC_DIR, "home-icons.jsx"), "utf8");
const auditSource = fs.readFileSync(AUDIT, "utf8");
const fetchRetrySource = fs.readFileSync(path.join(SRC_DIR, "home-fetch-with-retry.js"), "utf8");
const peopleSource = fs.readFileSync(path.join(SRC_DIR, "home-people.jsx"), "utf8");
const peoplePrewarmSource = fs.readFileSync(path.join(SRC_DIR, "home-people-prewarm.js"), "utf8");
const worldstateSource = fs.readFileSync(path.join(SRC_DIR, "home-worldstate.jsx"), "utf8");
const explainSource = fs.readFileSync(path.join(SRC_DIR, "home-explain.jsx"), "utf8");

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

function bootEntries() {
  const match = indexSource.match(/var\s+files\s*=\s*\[([\s\S]*?)\];/);
  assert("boot loader file array exists", Boolean(match));
  if (!match) return [];
  const entries = [];
  const re = /\[\s*['"]([^'"]+)['"]\s*,\s*(true|false)\s*\]/g;
  let item;
  while ((item = re.exec(match[1]))) {
    entries.push({ name: item[1], babel: item[2] === "true" });
  }
  return entries;
}

function unique(values) {
  return Array.from(new Set(values));
}

const entries = bootEntries();
const names = entries.map((entry) => entry.name);
const positions = new Map(names.map((name, index) => [name, index]));

function before(left, right, label) {
  assert(label || `${left} loads before ${right}`,
    positions.has(left) && positions.has(right) && positions.get(left) < positions.get(right),
    { left: positions.get(left), right: positions.get(right) });
}

function present(name) {
  assert(`${name} is in boot chain`, positions.has(name));
}

function sliceBetween(source, startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  if (start < 0) return "";
  const end = endNeedle ? source.indexOf(endNeedle, start + startNeedle.length) : -1;
  return end >= 0 ? source.slice(start, end) : source.slice(start);
}

process.stdout.write("\nbootstrap_inventory_contract_test\n");
assert("boot chain has the expected module scale", entries.length >= 40, entries.length);
assert("boot chain has no duplicate files", unique(names).length === names.length,
  names.filter((name, index) => names.indexOf(name) !== index));
assert("every boot-chain file exists", entries.every((entry) => fs.existsSync(path.join(SRC_DIR, entry.name))),
  entries.filter((entry) => !fs.existsSync(path.join(SRC_DIR, entry.name))).map((entry) => entry.name));

const sourceFiles = fs.readdirSync(SRC_DIR)
  .filter((name) => (name.endsWith(".jsx") || name.endsWith(".js")) &&
    !name.endsWith(".test.js") &&
    name !== "babel-config.js")
  .sort();
const staticScripts = Array.from(indexSource.matchAll(/<script\s+src=["']\.\/([^"']+)["']/g))
  .map((match) => match[1]);
const registeredStaticFiles = ["home-service-worker.js"];
const missingSourceFiles = sourceFiles.filter((name) => !positions.has(name) &&
  !staticScripts.includes(name) &&
  !registeredStaticFiles.includes(name));
assert("every top-level app/src JS/JSX file is in the boot chain or static pre-runtime",
  missingSourceFiles.length === 0,
  missingSourceFiles);
assert("web runtime and service resolver load before ordered boot chain",
  staticScripts.includes("home-web-runtime.js") &&
    staticScripts.includes("home-services.js") &&
    indexSource.indexOf("./home-web-runtime.js") < indexSource.indexOf("./home-services.js") &&
    indexSource.indexOf("./home-services.js") < indexSource.indexOf("var files ="),
  staticScripts);
assert("React and Babel runtime libraries are served locally for travel startup",
  indexSource.includes("./vendor/react-18.3.1/react.production.min.js") &&
    indexSource.includes("./vendor/react-18.3.1/react-dom.production.min.js") &&
    indexSource.includes("./vendor/babel-7.29.0/babel.min.js") &&
    !indexSource.includes("https://unpkg.com/react@") &&
    !indexSource.includes("https://unpkg.com/@babel/standalone@"),
  staticScripts);
assert("all JSX boot entries run through Babel",
  entries.filter((entry) => entry.name.endsWith(".jsx")).every((entry) => entry.babel),
  entries.filter((entry) => entry.name.endsWith(".jsx") && !entry.babel));
assert("plain JS boot entries bypass Babel",
  entries.filter((entry) => entry.name.endsWith(".js")).every((entry) => !entry.babel),
  entries.filter((entry) => entry.name.endsWith(".js") && entry.babel));

process.stdout.write("\nbootstrap_loader_failure_contract_test\n");
assert("loader uses launch-time cache buster",
  indexSource.includes("cb=' + Date.now()") || indexSource.includes('cb=" + Date.now()'));
assert("no static text/babel script tags remain",
  !/<script\s+type=["']text\/babel["']\s+src=/.test(indexSource));
assert("ordered loader fetches each file with three attempts",
  indexSource.includes("fetchWithRetry(name + cb, 3)") &&
    indexSource.includes("fetchBootText(i).then(function (text)"));
assert("boot loader prefetches a bounded window while preserving ordered execution",
  indexSource.includes("var PREFETCH_WINDOW = 6") &&
    indexSource.includes("function primeWindow(start)") &&
    indexSource.includes("primeWindow(0);") &&
    /execute\(name,\s*code\);[\s\S]*boot\.loaded\+\+;[\s\S]*primeWindow\(i \+ 1\);[\s\S]*step\(i \+ 1\);/.test(indexSource));
assert("status 0 empty loads are rejected",
  indexSource.includes("xhr.status === 0 && xhr.responseText.length > 0"));
assert("XHR timeout becomes a hard boot failure",
  indexSource.includes("xhr.ontimeout = function () { finish(reject, new Error('timeout after '"));
assert("network error becomes a hard boot failure",
  indexSource.includes("xhr.onerror = function () { finish(reject, new Error('network error (status 0)')); }"));
assert("manual boot-file timeout aborts stuck browser requests",
  indexSource.includes("manual timeout after ") &&
    indexSource.includes("try { xhr.abort(); } catch (_) {}") &&
    indexSource.includes("xhr.onabort = function () { finish(reject, new Error('request aborted')); }"));
assert("boot-file XHRs preserve gateway auth cookies",
  indexSource.includes("xhr.withCredentials = true"));
assert("Babel transform failures stop the boot chain",
  indexSource.includes("Babel transform failed in") && indexSource.includes("boot.failed = name"));
assert("files execute before the chain advances",
  /execute\(name,\s*code\);[\s\S]*boot\.loaded\+\+;[\s\S]*step\(i \+ 1\);/.test(indexSource));
assert("visible overlay is used for loader failures",
  indexSource.includes("function overlay(title, detail)") &&
    indexSource.includes("Failed to load ") &&
    indexSource.includes("boot chain stopped"));
assert("desktop glue is optional only in web mode",
  indexSource.includes("var webOptionalFiles") &&
    indexSource.includes("'home-tauri.jsx': true") &&
    indexSource.includes("function skipOptionalWebFile") &&
    indexSource.includes("!window.HG_WEB_MODE || !webOptionalFiles[name]") &&
    indexSource.includes("skipped optional web boot file"));
assert("web runtime provides browser-safe Tauri fallbacks",
  runtimeSource.includes("IS_TAURI: false") &&
    runtimeSource.includes("tauriFetch: browserTauriFetch") &&
    runtimeSource.includes("loadPrefs: browserLoadPrefs") &&
    runtimeSource.includes("saveEvents: browserSaveEvents"));
assert("service worker activation does not forcibly navigate booting clients",
  serviceWorkerSource.includes("self.clients.claim()") &&
    !serviceWorkerSource.includes("client.navigate("));
assert("service worker derives its cache name from the per-deploy ?v= version",
  serviceWorkerSource.includes("self.location.href") &&
    serviceWorkerSource.includes('searchParams.get("v")') &&
    /CACHE_NAME\s*=\s*`home-web-static-\$\{SW_VERSION\}`/.test(serviceWorkerSource) &&
    !serviceWorkerSource.includes('CACHE_NAME = "home-web-static-v2"'));
assert("service worker install skips waiting and activate claims clients",
  serviceWorkerSource.includes("self.skipWaiting()") &&
    serviceWorkerSource.includes("self.clients.claim()"));
assert("service worker bypasses app shell and app code modules",
  serviceWorkerSource.includes("function isAppCodeModule") &&
    serviceWorkerSource.includes("isAppShell(request, url) || isAppCodeModule(url)") &&
    serviceWorkerSource.includes("avoids stale code on"));
assert("service worker logs its version on install and activate",
  serviceWorkerSource.includes("[home-sw]") &&
    serviceWorkerSource.includes("installing") &&
    serviceWorkerSource.includes("active"));
assert("service worker install precaches non-code shell assets best-effort",
  serviceWorkerSource.includes("PRECACHE_URLS") &&
    /caches\.open\(CACHE_NAME\)[\s\S]*addAll\(PRECACHE_URLS\)/.test(serviceWorkerSource) &&
    serviceWorkerSource.includes("event.waitUntil(precacheShell())") &&
    /async function precacheShell\(\)\s*\{[\s\S]*try\s*\{[\s\S]*addAll\(PRECACHE_URLS\)[\s\S]*\}\s*catch/.test(serviceWorkerSource) &&
    serviceWorkerSource.includes("self.skipWaiting()"));
assert("service worker precache excludes app shell and app modules",
  !/PRECACHE_URLS\s*=\s*\[[\s\S]*["']\/["'][\s\S]*\]/.test(serviceWorkerSource) &&
    !/PRECACHE_URLS\s*=\s*\[[\s\S]*home-app\.jsx[\s\S]*\]/.test(serviceWorkerSource) &&
    !/PRECACHE_URLS\s*=\s*\[[\s\S]*home-web-runtime\.js[\s\S]*\]/.test(serviceWorkerSource));
assert("service worker precache never caches the worker itself",
  serviceWorkerSource.includes("PRECACHE_URLS") &&
    !/PRECACHE_URLS\s*=\s*\[[\s\S]*home-service-worker\.js[\s\S]*\]/.test(serviceWorkerSource));

process.stdout.write("\nbootstrap_recoverable_boot_contract_test\n");
assert("inline boot shell renders before app scripts",
  indexSource.includes('id="hg-boot-shell"') &&
    indexSource.includes("hg-boot-ascii") &&
    indexSource.includes("hg-boot-bar") &&
    indexSource.indexOf('id="hg-boot-shell"') < indexSource.indexOf('id="root"'),
  "boot shell must be body markup, not React-rendered UI");
assert("boot shell has a troubleshooting escape hatch",
  indexSource.includes("readFlag('bootShell', 'home.perf.bootShell')") &&
    indexSource.includes("data-disabled"));
assert("boot shell tracks loader phases",
  indexSource.includes("function updateBootShell") &&
    indexSource.includes("updateBootShell('fetching'") &&
    indexSource.includes("updateBootShell('transforming'") &&
    indexSource.includes("updateBootShell('executing'") &&
    indexSource.includes("updateBootShell('reconnecting'") &&
    indexSource.includes("updateBootShell('complete'") &&
    indexSource.includes("updateBootShell('failed'"));
assert("home mount removes the static boot shell after React render",
  mountSource.includes("window.__homeRemoveBootShell") &&
    mountSource.includes("requestAnimationFrame") &&
    mountSource.indexOf("window.__homeRoot.render(<HomeApp />)") < mountSource.indexOf("window.__homeRemoveBootShell"));
assert("boot fetch failures reconnect with backoff instead of a dead end",
  indexSource.includes("function scheduleBootRetry") &&
    indexSource.includes("function backoffDelay") &&
    /scheduleBootRetry\(i, err\);\s*\n\s*\}\);/.test(indexSource));
assert("boot reconnecting overlay is shown instead of the terminal error",
  indexSource.includes("function reconnectingOverlay") &&
    indexSource.includes("Reconnecting to home"));
assert("reconnecting overlay offers a reload that unregisters the service worker",
  indexSource.includes("function unregisterSwAndReload") &&
    indexSource.includes("getRegistrations") &&
    indexSource.includes("r.unregister()") &&
    indexSource.includes("Reload now"));
assert("terminal Babel failure is bounded and clears the reconnecting state",
  indexSource.includes("babelFailures[name] <= 3") &&
    indexSource.includes("boot.reconnecting = false"));
assert("mount watchdog stays quiet while the boot loader is reconnecting",
  indexSource.includes("if (boot.reconnecting) { setTimeout(watchdog, 2000); return; }"));
assert("boot outcome and service worker version are logged",
  indexSource.includes("function swVersionLabel") &&
    indexSource.includes("[boot] chain complete") &&
    indexSource.includes("[boot] starting chain"));

process.stdout.write("\nbackground_warmup_contract_test\n");
assert("background warmup has a URL/localStorage kill switch",
  appSource.includes('get("warmup")') &&
    appSource.includes("home.perf.backgroundWarmup") &&
    appSource.includes("state: \"disabled\""));
assert("people data warmup waits for boot and saved credentials outside React",
  peoplePrewarmSource.includes("__bootState?.done") &&
    peoplePrewarmSource.includes("loadPrefs") &&
    peoplePrewarmSource.includes("api/extended_openai_conversation/identities") &&
    peoplePrewarmSource.includes("Authorization: `Bearer ${token}`"));
assert("background warmup pauses during interaction and focused input",
  appSource.includes("recent interaction") &&
    appSource.includes("input focused") &&
    appSource.includes("waitForQuiet"));
assert("background warmup keeps apartment prewarm conservative on constrained links",
  appSource.includes("saveData") &&
    appSource.includes("slowLink") &&
    appSource.includes("cellular") &&
    appSource.includes("background-warmup-conservative"));
assert("background warmup controls apartment auto-prewarm",
  appSource.includes("__HOME_BACKGROUND_WARMUP_CONTROLS_APARTMENT") &&
    fs.readFileSync(path.join(SRC_DIR, "home-apartment-prewarm.js"), "utf8").includes("__HOME_BACKGROUND_WARMUP_CONTROLS_APARTMENT"));
assert("perf snapshot exposes boot shell and warmup state",
  fs.readFileSync(path.join(SRC_DIR, "home-perf.js"), "utf8").includes("window.__homeBootShell") &&
    fs.readFileSync(path.join(SRC_DIR, "home-perf.js"), "utf8").includes("window.__HOME_BACKGROUND_WARMUP"));
assert("people prewarm exposes a quiet data warmer",
  peopleSource.includes("window.HomePeoplePrewarm") &&
    peopleSource.includes("prewarmPeopleData") &&
    peopleSource.includes("cache: \"no-store\"") &&
    peopleSource.includes("cache: \"force-cache\""));

process.stdout.write("\nbootstrap_order_contract_test\n");
[
  "home-tauri.jsx",
  "home-ha.jsx",
  "home-icons.jsx",
  "home-events.jsx",
  "home-metrics.jsx",
  "home-sse-fetch.js",
  "home-stack-actions.jsx",
  "home-ai-stack.jsx",
  "home-capabilities.js",
  "home-metrics-lab.jsx",
  "home-lights.jsx",
  "simulation.jsx",
  "home-apartment.jsx",
  "home-video-labeler.jsx",
  "home-app.jsx",
  "home-mount.jsx",
].forEach(present);

before("home-metrics.jsx", "home-ai-stack.jsx", "metrics surface loads before AI stack card uses it");
before("home-sse-fetch.js", "home-ai-stack.jsx", "SSE fetch helper loads before AI stack streams");
before("home-stack-actions.jsx", "home-ai-stack.jsx", "stack actions load before AI stack UI");
before("home-stack-actions.jsx", "home-metrics-lab.jsx", "stack actions load before Lab stack controls");
before("home-capabilities.js", "home-app.jsx", "capability registry loads before HomeApp");
before("home-metrics-lab-helpers.js", "home-metrics-lab.jsx");
before("home-people-helpers.js", "home-people.jsx");
before("home-explain-helpers.js", "home-explain.jsx");
before("home-worldstate-helpers.js", "home-worldstate.jsx");
before("home-spatial-helpers.js", "home-spatial.jsx");
before("home-lighting-events.jsx", "home-lights.jsx");
before("home-lights.jsx", "home-app.jsx");
before("home-intelligence.jsx", "home-app.jsx");
before("simulation-cameras.jsx", "simulation.jsx");
before("simulation-controls.jsx", "simulation.jsx");
before("simulation-timeline.jsx", "simulation.jsx");
before("simulation-data.jsx", "simulation.jsx");
before("simulation.jsx", "home-app.jsx");
before("home-apartment-data.js", "home-apartment.jsx");
before("home-apartment-sim.js", "home-apartment.jsx");
before("home-apartment-cards.jsx", "home-apartment.jsx");
before("home-apartment-calibrate.jsx", "home-apartment.jsx");
before("home-apartment-edit.jsx", "home-apartment.jsx");
before("home-video-labeler-data.js", "home-video-labeler.jsx");
before("home-video-labeler-timeline.jsx", "home-video-labeler.jsx");
before("home-app.jsx", "home-mount.jsx", "HomeApp loads before mount");
assert("home-mount.jsx is the final boot file", names[names.length - 1] === "home-mount.jsx",
  names.slice(-5));

present("home-fetch-with-retry.js");
before("home-fetch-with-retry.js", "home-people.jsx", "fetch-with-retry loads before People");
before("home-fetch-with-retry.js", "home-worldstate.jsx", "fetch-with-retry loads before Worldstate");
before("home-fetch-with-retry.js", "home-explain.jsx", "fetch-with-retry loads before Explain");

process.stdout.write("\nresilience_fetch_with_retry_contract_test\n");
assert("fetch-with-retry exposes a global fetchWithRetry",
  /(?:glob|window|globalThis)\.fetchWithRetry\s*=/.test(fetchRetrySource) &&
    fetchRetrySource.includes("function fetchWithRetry"));
assert("fetch-with-retry only retries transient statuses",
  /RETRYABLE_STATUS\s*=\s*\{[^}]*408[^}]*429[^}]*502[^}]*503[^}]*504/.test(fetchRetrySource) &&
    /NON_RETRYABLE_STATUS\s*=\s*\{[^}]*400[^}]*401[^}]*403[^}]*404[^}]*409/.test(fetchRetrySource));
assert("fetch-with-retry aborts each attempt via AbortController and honors Retry-After",
  fetchRetrySource.includes("new AbortController()") &&
    fetchRetrySource.includes("parseRetryAfter") &&
    fetchRetrySource.includes("RETRY_AFTER_CAP_MS"));
assert("fetch-with-retry throws with attempts/lastStatus/reason metadata",
  fetchRetrySource.includes("wrapped.reason") &&
    fetchRetrySource.includes("wrapped.attempts") &&
    fetchRetrySource.includes("wrapped.lastStatus"));
assert("People identity load uses fetchWithRetry with a reconnecting banner",
  peopleSource.includes("window.fetchWithRetry(") &&
    peopleSource.includes("setReconnecting") &&
    /reconnecting to identity store/i.test(peopleSource) &&
    peopleSource.includes(">retry now</button>"));
assert("Worldstate load uses fetchWithRetry with a reconnecting banner",
  worldstateSource.includes("window.fetchWithRetry(") &&
    worldstateSource.includes("setReconnecting") &&
    /reconnecting to world state/i.test(worldstateSource) &&
    worldstateSource.includes(">retry now</button>"));
assert("Explain load uses fetchWithRetry with a reconnecting banner",
  explainSource.includes("window.fetchWithRetry(") &&
    explainSource.includes("setReconnecting") &&
    /reconnecting to routing log/i.test(explainSource) &&
    explainSource.includes(">retry now</button>"));

process.stdout.write("\nbootstrap_mount_watchdog_contract_test\n");
assert("watchdog lives in index.html",
  indexSource.includes("function watchdog()") && indexSource.includes("Mount watchdog"));
assert("watchdog waits for boot completion before mount retry",
  indexSource.includes("if (!boot.done)") && indexSource.includes("typeof window.__homeMount === 'function'"));
assert("watchdog retries mount exactly three times",
  indexSource.includes("mountRetries < 3") && indexSource.includes("mount retries: "));
assert("watchdog reports HomeApp and mount function types",
  indexSource.includes("typeof HomeApp: ") && indexSource.includes("typeof __homeMount: "));
assert("home-mount exposes retryable mount function",
  mountSource.includes("window.__homeMount = function ()"));
assert("home-mount no-ops when root is already populated",
  mountSource.includes("if (el.children.length > 0) return true"));
assert("home-mount creates a reusable React root",
  mountSource.includes("window.__homeRoot") && mountSource.includes("ReactDOM.createRoot(el)"));
assert("home-mount renders HomeApp",
  mountSource.includes("window.__homeRoot.render(<HomeApp />)"));
assert("home-mount invokes initial mount immediately",
  /window\.__homeMount\(\);\s*$/.test(mountSource.trim()));

process.stdout.write("\nheader_navigation_contract_test\n");
assert("header exposes a dedicated apartment action",
  appSource.includes("onOpenApartment") &&
    appSource.includes("aria-label=\"Open apartment view\"") &&
    appSource.includes("openApartmentFromHeader"));
assert("mobile header exposes people and apartment as direct actions",
  appSource.includes("{onOpenPeople && mobile && (") &&
    appSource.includes("aria-label=\"Open people view\"") &&
    appSource.includes("aria-label=\"Open apartment view\""));
assert("mobile header does not render the desktop actions menu",
  appSource.includes("{!mobile && actionMenu}") &&
    appSource.includes("serviceProfile && onOpenRemoteProfile && !mobile") &&
    appSource.includes("{onToggleTheme && !mobile"));
assert("video labeler is hidden on mobile",
  appSource.includes("const videoLabelerAvailable = !mobile") &&
    appSource.includes("onOpenVideoLabeler={isSpatialWide || !videoLabelerAvailable ? null : openVideoLabelerFeature}") &&
    appSource.includes("video labeler is hidden on mobile"));
assert("mobile slash/help hide desktop-only labeler command",
  appSource.includes("function isMobileHiddenCommand(cmd)") &&
    appSource.includes("function availableSlashCommands") &&
    appSource.includes("const slashCommands = availableSlashCommands({ mobile });") &&
    appSource.includes("const visibleCommands = availableSlashCommands({ mobile });"));
assert("header connection status is anchored with the brand",
  appSource.includes("const connectionStatusNode") &&
    appSource.includes("{mobile && (") &&
    appSource.includes("{!mobile && connectionStatusNode}"));
assert("apartment header action is text-only",
  !appSource.includes("IconApartment") &&
    !iconsSource.includes("IconApartment"));

process.stdout.write("\nregistry_boot_inventory_contract_test\n");
assert("registry audit extracts the boot-loader file array",
  auditSource.includes("extract_boot_loader_scripts") &&
    auditSource.includes("var\\s+files\\s*=\\s*\\[") &&
    auditSource.includes("dict.fromkeys"));
assert("registry frontend inventory includes static and boot-loader scripts",
  auditSource.includes("static_scripts") &&
    auditSource.includes("boot_scripts") &&
    auditSource.includes("static_scripts + boot_scripts"));

process.stdout.write("\nfirst_run_s88_connect_contract_test\n");
const firstRunBlock = sliceBetween(appSource, "function FirstRun", "/* Boot wordmark");
const firstRunGateBlock = sliceBetween(appSource, "function isFirstRunVisible", "function BootBanner");
const prefsBlock = sliceBetween(appSource, "/* Persist prefs whenever they change", "/* Persist events on change");
const haLifecycleBlock = sliceBetween(appSource, "/* \u2500\u2500 HA client lifecycle", "/* Probe for the sidecar");
const connectBlock = sliceBetween(appSource, "const connectTo = useCallback", "const confirmModel = useCallback");
const confirmModelBlock = sliceBetween(appSource, "const confirmModel = useCallback", "/* Auto-reconnect on launch");
const reconnectBlock = sliceBetween(appSource, "/* Auto-reconnect on launch", "/* Sim Mode transitions");
const focusBlock = sliceBetween(appSource, "/* Input focus", "/* \u2500\u2500 HA client lifecycle");
const renderGateBlock = sliceBetween(appSource, "{isFirstRunVisible(connection, events) ? (", "<MetricsStrip");
const spatialRailRenderBlock = sliceBetween(appSource, "<SpatialModeRail", "{spatialOpsDockOpen && (");
const inputRowBlock = sliceBetween(appSource, "function InputRow", "/* VoiceModeButton");
assert("FirstRun accepts S88 connection props",
  /function FirstRun\(\{[\s\S]*connection,[\s\S]*endpoint,[\s\S]*token,[\s\S]*onConnect,[\s\S]*availableModels,[\s\S]*onPickModel,[\s\S]*onSimulation\s*=\s*null,[\s\S]*compact\s*=\s*false,[\s\S]*serviceProfile\s*=\s*null,[\s\S]*serviceProbeRunning\s*=\s*false,[\s\S]*\}\)/.test(firstRunBlock),
  firstRunBlock.slice(0, 520));
assert("FirstRun seeds endpoint and token fields from persisted props",
  /useState\(endpoint \|\| webDefaultBase\("HG_DEFAULT_HA_BASE"\) \|\| "http:\/\/192\.168\.0\.125:8123"\)/.test(firstRunBlock) &&
    /connection === "offline" \|\| connection === "auth_invalid"[\s\S]*\? "" : \(token \|\| ""\)/.test(firstRunBlock),
  firstRunBlock.slice(0, 700));
assert("FirstRun submit prevents default and calls onConnect with typed url and token",
  /<form onSubmit=\{\(e\) => \{ e\.preventDefault\(\); onConnect\(url, tok\); \}\}/.test(firstRunBlock),
  firstRunBlock.slice(2500, 3900));
assert("FirstRun Connect button is disabled while connecting or missing credentials",
  /type="submit" disabled=\{isConnecting \|\| !url \|\| !tok\}/.test(firstRunBlock),
  firstRunBlock.slice(3600, 5100));
assert("FirstRun model picker renders every discovered model and selects by name",
  /availableModels\.map\(\(m\) => \(/.test(firstRunBlock) &&
    /onClick=\{\(\) => onPickModel\(m\.name\)\}/.test(firstRunBlock) &&
    /\{m\.name\}/.test(firstRunBlock) &&
    /\{m\.ctx\}/.test(firstRunBlock),
  firstRunBlock.slice(900, 2600));
assert("FirstRun empty model-list fallback can continue without a model name",
  /onClick=\{\(\) => onPickModel\(""\)\}/.test(firstRunBlock),
  firstRunBlock.slice(1800, 3200));
assert("FirstRun visibility gate keeps picker and auth errors on the form surface",
  /return connection !== "online" && connection !== "reconnecting";/.test(firstRunGateBlock),
  firstRunGateBlock);
assert("HomeApp render gate mounts FirstRun with live connect and picker props",
  /<FirstRun[\s\S]*connection=\{connection\}[\s\S]*endpoint=\{endpoint\}[\s\S]*token=\{token\}[\s\S]*onConnect=\{connectTo\}[\s\S]*availableModels=\{availableModels\}[\s\S]*onPickModel=\{confirmModel\}[\s\S]*onSimulation=\{\(\) => \{/.test(renderGateBlock),
  renderGateBlock.slice(0, 900));
assert("Spatial ops rail clears tool surfaces before toggling the ops dock",
  /onOps=\{\(\) => \{[\s\S]*closeSpatialToolSurfaces\(\);[\s\S]*setSpatialOpsDockOpen\(\(open\) => !open\);[\s\S]*\}\}/.test(spatialRailRenderBlock),
  spatialRailRenderBlock);
assert("stored events do not make an uncredentialed launch look online",
  /const hasStoredHaCredentials = !!\(initialPrefs\.endpoint && initialPrefs\.token\);[\s\S]*const \[connection, setConnection\] = useState\(\s*hasStoredHaCredentials \? "reconnecting" : "disconnected"\s*\);/.test(appSource) &&
    !/initialEvents \? "online"/.test(appSource),
  appSource.slice(appSource.indexOf("const hasStoredHaCredentials") - 300, appSource.indexOf("const hasStoredHaCredentials") + 500));
assert("prefs persistence includes endpoint token and selected model",
  /savePrefs\(\{\s*endpoint,\s*token,\s*model,/.test(prefsBlock) &&
    /\[endpoint,\s*token,\s*model,/.test(prefsBlock),
  prefsBlock);
assert("connectTo persists typed endpoint/token before attempting HA connect",
  /setEndpoint\(haUrl\);[\s\S]*setToken\(accessToken\);[\s\S]*setAvailableModels\(null\);[\s\S]*setConnection\("connecting"\);/.test(connectBlock) &&
    /haClientRef\.current\.connect\(haUrl, accessToken\)/.test(connectBlock),
  connectBlock.slice(0, 900));
assert("connectTo discovers vLLM models from the AI host and maps id plus context",
  /let vllmBase = webDefaultBase\("HG_DEFAULT_VLLM_BASE"\)/.test(connectBlock) &&
    /const aiHost = new URL\(mBase\)\.hostname/.test(connectBlock) &&
    /vllmBase = `http:\/\/\$\{aiHost\}:8000`/.test(connectBlock) &&
    /tauriFetch\(`\$\{vllmBase\}\/v1\/models`\)/.test(connectBlock) &&
    /name:\s*m\.id/.test(connectBlock) &&
    /ctx:\s*m\.max_model_len/.test(connectBlock),
  connectBlock.slice(1600, 3100));
assert("connectTo auto-selects a single model or the previously saved model",
  /const autoPick = models\.length === 1[\s\S]*\? models\[0\]\.name[\s\S]*model && models\.some\(\(m\) => m\.name === model\) \? model : null/.test(connectBlock) &&
    /if \(autoPick\) \{[\s\S]*setModel\(autoPick\);[\s\S]*setConnection\("online"\)/.test(connectBlock),
  connectBlock.slice(2500, 4100));
assert("connectTo enters model-picking mode for a genuine multi-model choice",
  /setAvailableModels\(models\);[\s\S]*setConnection\("picking-model"\);[\s\S]*choose one/.test(connectBlock),
  connectBlock.slice(3300, 4700));
assert("HA online event cannot collapse an active model picker",
  /state === "online"[\s\S]*setConnection\(\(c\) => c === "picking-model" \? c : "online"\)/.test(haLifecycleBlock),
  haLifecycleBlock.slice(0, 900));
assert("confirmModel stores the chosen model, clears picker data, and releases chat",
  /if \(name\) setModel\(name\);[\s\S]*setConnection\("online"\);[\s\S]*setAvailableModels\(null\);/.test(confirmModelBlock),
  confirmModelBlock);
assert("credentialed launch reconnects through the same connectTo path",
  /connection === "reconnecting" && endpoint && token[\s\S]*connectTo\(endpoint, token\)/.test(reconnectBlock),
  reconnectBlock);
assert("boot focus release waits until FirstRun is no longer visible",
  /if \(isFirstRunVisible\(connection, events\)\) return;[\s\S]*setFocusToken\(\(t\) => t \+ 1\)/.test(focusBlock),
  focusBlock);
assert("InputRow mount still ignores the initial zero focus token",
  /useEffect\(\(\) => \{ if \(focusToken\) inputRef\.current\?\.focus\(\); \}, \[focusToken\]\)/.test(inputRowBlock),
  inputRowBlock.slice(0, 700));

process.stdout.write("\n");
process.stdout.write(`${passes} pass . ${fails} fail\n`);
if (failures.length) {
  for (const failure of failures) {
    process.stdout.write(`  FAIL: ${failure.name}\n`);
  }
  process.exit(1);
}
