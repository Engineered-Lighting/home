/* Home web service worker.
 *
 * Scope: speed up heavy static assets while keeping executable app code fresh.
 * Never cache proxy/API/live-camera traffic; those routes represent current
 * home state and must stay network-bound.
 *
 * Cache versioning is automatic. home-web-runtime.js registers this worker as
 * `home-service-worker.js?v=<asset-version>`, where the asset version is the
 * gateway's `${commit}-${buildStartedAt}` string. We read that `v` back off our
 * own script URL, so every deploy produces a fresh CACHE_NAME and the previous
 * cache is deleted on activate. If the param is absent, we fall back to "dev".
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
const SAME_ORIGIN_STATIC = /\.(?:js|jsx|css|png|jpg|jpeg|webp|svg|ico|webmanifest|wasm)$/i;
const APP_CODE_ASSET = /\.(?:js|jsx)$/i;
const HEAVY_APARTMENT_ASSET = /^\/assets\/apartment\/.*\.(?:ply|spz|glb|wasm|jpg|jpeg|png|webp)$/i;
const NEVER_CACHE_PREFIXES = [
  "/proxy/",
  "/api/",
  "/auth/",
  "/healthz",
];

// Tiny non-code shell assets only. Do not precache "/" or app JS/JSX modules:
// mobile browsers can keep an old worker across tab/app restarts, and falling
// back to cached executable code after a deploy is worse than a slower load.
const PRECACHE_URLS = [
  "home-tokens.css",
  "favicon.svg",
  "favicon-32.png",
  "apple-touch-icon.png",
  "site.webmanifest",
];

async function precacheShell() {
  // Best-effort: one unreachable file must not reject install, or the worker
  // would never take over.
  try {
    const cache = await caches.open(CACHE_NAME);
    await cache.addAll(PRECACHE_URLS);
    console.info(`[home-sw] ${SW_VERSION} precached ${PRECACHE_URLS.length} static file(s)`);
  } catch (err) {
    console.warn(`[home-sw] ${SW_VERSION} precache skipped - ${(err && err.message) || err}`);
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
    // reload after the current boot settles. Forcing navigation here can abort
    // in-flight boot-file XHRs on mobile Safari.
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

function isAppCodeModule(url) {
  return APP_CODE_ASSET.test(url.pathname) && !url.pathname.startsWith("/vendor/");
}

function isCacheableStatic(url) {
  if (HEAVY_APARTMENT_ASSET.test(url.pathname)) return true;
  if (isAppCodeModule(url)) return false;
  if (url.searchParams.has("v") && SAME_ORIGIN_STATIC.test(url.pathname)) return true;
  if (url.pathname.startsWith("/vendor/")) return true;
  return false;
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

  if (isAppShell(request, url) || isAppCodeModule(url)) {
    // Let the browser hit the gateway directly. The gateway marks the shell
    // no-store and app modules use fresh ?v= URLs, so this avoids stale code on
    // mobile while preserving browser-level validation.
    return;
  }

  if (HEAVY_APARTMENT_ASSET.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  if (isCacheableStatic(url)) {
    event.respondWith(cacheFirst(request));
  }
});
