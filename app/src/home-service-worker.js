/* Home web service worker.
 *
 * Scope: speed up the browser/Tailscale web app shell and heavy static assets.
 * Never cache proxy/API/live-camera traffic; those routes represent current
 * home state and must stay network-bound.
 */

const CACHE_NAME = "home-web-static-v2";
const SAME_ORIGIN_STATIC = /\.(?:js|jsx|css|png|jpg|jpeg|webp|svg|ico|webmanifest|wasm)$/i;
const HEAVY_APARTMENT_ASSET = /^\/assets\/apartment\/.*\.(?:ply|spz|glb|wasm|jpg|jpeg|png|webp)$/i;
const NEVER_CACHE_PREFIXES = [
  "/proxy/",
  "/api/",
  "/auth/",
  "/healthz",
];

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name)));
    // Claiming lets the page's controllerchange handler perform one guarded
    // reload after the current boot settles. Forcing page navigation here can
    // abort in-flight boot-file XHRs on mobile Safari and leave a red boot
    // overlay even though the app files are available.
    await self.clients.claim();
  })());
});

function shouldNeverCache(url) {
  return NEVER_CACHE_PREFIXES.some((prefix) => url.pathname === prefix || url.pathname.startsWith(prefix));
}

function isAppShell(request, url) {
  return request.mode === "navigate" || url.pathname === "/" || url.pathname === "/index.html";
}

function isCacheableStatic(url) {
  if (HEAVY_APARTMENT_ASSET.test(url.pathname)) return true;
  if (url.searchParams.has("v") && SAME_ORIGIN_STATIC.test(url.pathname)) return true;
  if (url.pathname.startsWith("/vendor/")) return true;
  return false;
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
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
    event.respondWith(cacheFirst(request));
  }
});
