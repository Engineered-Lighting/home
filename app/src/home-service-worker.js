/* Home web service worker.
 *
 * Scope: speed up the browser/Tailscale web app shell and heavy static assets.
 * Never cache proxy/API/live-camera traffic; those routes represent current
 * home state and must stay network-bound.
 *
 * Cache versioning is automatic — no manual v2 -> v3 bumps. home-web-runtime.js
 * registers this worker as `home-service-worker.js?v=<asset-version>`, where the
 * asset version is the gateway's `${commit}-${buildStartedAt}` string
 * (web-gateway/server.mjs -> window.__HOME_ASSET_VERSION). We read that `v` back
 * off our own script URL, so every deploy produces a fresh CACHE_NAME and the
 * previous cache is deleted on activate. If the param is ever absent (e.g. the
 * dev `serve` harness, which injects no version), we fall back to "dev": nothing
 * to bump, it just means one shared dev cache.
 */

function readSwVersion() {
  try {
    const v = new URL(self.location.href).searchParams.get("v");
    return v && v.trim() ? v.trim() : "dev";
  } catch (_) {
    return "dev";
  }
}

const SW_VERSION = readSwVersion();
const CACHE_NAME = `home-web-static-${SW_VERSION}`;
const SW_BORN_AT = Date.now();
// Once the worker has been alive this long, prefer the network for the app
// shell and JSX modules so a fresh deploy is never masked by a warm cache. Set
// well above a Tailscale re-handshake so ordinary blips don't force cold loads.
const NETWORK_PREFERRED_AFTER_MS = 10 * 60 * 1000;
// Generous network ceiling: long enough to ride out a Tailscale flip, short
// enough that a truly dead box falls back to cache (or lets the boot loader
// retry) instead of hanging the tab forever. Deliberately NOT aggressive — a
// too-short timeout would strand users during normal flaky-link recovery.
const NETWORK_TIMEOUT_MS = 8000;

const SAME_ORIGIN_STATIC = /\.(?:js|jsx|css|png|jpg|jpeg|webp|svg|ico|webmanifest|wasm)$/i;
const HEAVY_APARTMENT_ASSET = /^\/assets\/apartment\/.*\.(?:ply|spz|glb|wasm|jpg|jpeg|png|webp)$/i;
const NEVER_CACHE_PREFIXES = [
  "/proxy/",
  "/api/",
  "/auth/",
  "/healthz",
];

// Small always-loaded shell + top-of-chain modules. These are the files that
// fail first when the box goes down mid-navigation, so seeding them at install
// lets networkFirst fall back to a warm copy during the reboot window. The app
// shell ("/") is fetched as a navigation and matches directly; the versioned
// JSX/JS requests carry a ?v= query, so their precache value is mainly the
// shell — precache stays strictly best-effort. Deliberately excludes the SW
// itself and every heavy apartment asset (font/image/model bytes).
const PRECACHE_URLS = [
  "/",
  "home-web-runtime.js",
  "home-services.js",
  "home-perf.js",
  "home-feature-loader.js",
  "home-tokens.css",
  "home-tauri.jsx",
  "home-app.jsx",
  "home-events.jsx",
  "home-people.jsx",
];

async function precacheShell() {
  // Best-effort: a single unreachable file (flaky first-load) must NOT reject
  // install, or the worker would never take over. addAll is atomic-all-or-
  // nothing, so its rejection is swallowed here rather than propagated.
  try {
    const cache = await caches.open(CACHE_NAME);
    await cache.addAll(PRECACHE_URLS);
    console.info(`[home-sw] ${SW_VERSION} precached ${PRECACHE_URLS.length} shell file(s)`);
  } catch (err) {
    console.warn(`[home-sw] ${SW_VERSION} precache skipped — ${(err && err.message) || err}`);
  }
}

self.addEventListener("install", (event) => {
  console.info(`[home-sw] ${SW_VERSION} installing (cache ${CACHE_NAME})`);
  self.skipWaiting();
  event.waitUntil(precacheShell());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    const stale = names.filter((name) => name !== CACHE_NAME);
    await Promise.all(stale.map((name) => caches.delete(name)));
    // Claiming lets the page's controllerchange handler perform one guarded
    // reload after the current boot settles. Forcing page navigation here can
    // abort in-flight boot-file XHRs on mobile Safari and leave a red boot
    // overlay even though the app files are available.
    await self.clients.claim();
    console.info(`[home-sw] ${SW_VERSION} active; purged ${stale.length} stale cache(s)`);
  })());
});

function shouldNeverCache(url) {
  return NEVER_CACHE_PREFIXES.some((prefix) => url.pathname === prefix || url.pathname.startsWith(prefix));
}

function isAppShell(request, url) {
  return request.mode === "navigate" || url.pathname === "/" || url.pathname === "/index.html";
}

function isJsxModule(url) {
  return /\.jsx$/i.test(url.pathname);
}

function isCacheableStatic(url) {
  if (HEAVY_APARTMENT_ASSET.test(url.pathname)) return true;
  if (url.searchParams.has("v") && SAME_ORIGIN_STATIC.test(url.pathname)) return true;
  if (url.pathname.startsWith("/vendor/")) return true;
  return false;
}

function swAliveMs() {
  return Date.now() - SW_BORN_AT;
}

function preferNetwork() {
  return swAliveMs() > NETWORK_PREFERRED_AFTER_MS;
}

function fetchWithTimeout(request, timeoutMs) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timer = setTimeout(() => {
    if (controller) {
      try { controller.abort(); } catch (_) {}
    }
  }, timeoutMs);
  const init = controller ? { signal: controller.signal } : undefined;
  return Promise.resolve()
    .then(() => fetch(request, init))
    .finally(() => clearTimeout(timer));
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetchWithTimeout(request, NETWORK_TIMEOUT_MS);
    if (response && response.ok) await cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) await cache.put(request, response.clone());
  return response;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const refreshed = fetch(request).then((response) => {
    if (response && response.ok) cache.put(request, response.clone());
    return response;
  });
  return cached || refreshed;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (shouldNeverCache(url)) return;

  if (isAppShell(request, url)) {
    event.respondWith(networkFirst(request));
    return;
  }

  if (HEAVY_APARTMENT_ASSET.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  if (isCacheableStatic(url)) {
    // JSX app modules go network-first once the worker is warm, so an updated
    // deploy can't be shadowed by a cached module. Other versioned static
    // assets are immutable per ?v= and stay cache-first for speed. networkFirst
    // still falls back to cache on failure, so this never blocks an offline boot.
    if (isJsxModule(url) && preferNetwork()) {
      event.respondWith(networkFirst(request));
    } else {
      event.respondWith(cacheFirst(request));
    }
  }
});
