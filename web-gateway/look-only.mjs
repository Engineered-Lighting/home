// Look-only: a device on the owner's tailnet with no Home session may read the den's live view, and nothing else.
// It's off unless HOME_WEB_LOOK_ONLY=1.
//
// A request is look-only when all of these hold:
// - the feature is on (HOME_WEB_LOOK_ONLY=1, with HOME_WEB_SERVE_PORT set);
// - it has no valid session. A signed-in device always gets full Home;
// - it came in on the Serve listener (HOME_WEB_SERVE_PORT), which takes loopback connections that start with a PROXY
//   line only (home-location.mjs, { required: true }). Serve forwards there with --proxy-protocol=1, so that line is
//   always Serve's own and names the real client. The gateway's ordinary port, where a client's own first line can't
//   be told from Serve's, never gives look-only;
// - that client is a tailnet address: 100.64.0.0/10 or fd7a:115c:a1e0::/48;
// - it's a GET or HEAD, with no body, of a path on the list in lookOnlyKind.
// Any other request from that device is answered as any request without a session is today. That includes every
// write, every other site's data, /api/world/v1/config and WebSocket upgrades. Producers posting with their own
// tokens are untouched, because no POST is ever look-only.
//
// What a look-only device can read: the den's view and the app's own code. One code file, the site registry, holds
// both homes' addresses and coordinates; it's served redacted (REDACTED below), and not at all if the redaction
// can't be checked. Code folders the view never loads (the agent, the spatial spike, tests) aren't served.
//
// A process on this host could connect to the Serve listener with a PROXY line of its own. That's the owner's own host.
import net from "node:net";
import { CLIENT_ADDRESS, VIA_SERVE } from "./home-location.mjs";
import { REGISTRATION_ROUTE } from "./equipment-registration.mjs";
import { DEN_LIVE_PREFIX, DEN_SITE, denLiveRouteKind } from "./den-live-route.mjs";
import { LIGHTING_BRAIN_PREFIX } from "./lighting-brain-route.mjs";

const TAILNET = new net.BlockList();
TAILNET.addSubnet("100.64.0.0", 10, "ipv4");
TAILNET.addSubnet("fd7a:115c:a1e0::", 48, "ipv6");

export function lookOnlyEnabled(env = process.env) {
  return ["1", "true", "yes", "on"].includes(String(env.HOME_WEB_LOOK_ONLY || "").trim().toLowerCase());
}

export function isTailnetAddress(address) {
  let text = String(address || "");
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/i.exec(text);
  if (mapped) text = mapped[1];
  const family = net.isIP(text);
  return family !== 0 && TAILNET.check(text, family === 6 ? "ipv6" : "ipv4");
}

// The client Serve named on the Serve listener, when it's a tailnet one: that address, or null.
export function lookOnlyClient(req) {
  const socket = req?.socket;
  return socket?.[VIA_SERVE] === true && isTailnetAddress(socket[CLIENT_ADDRESS]) ? String(socket[CLIENT_ADDRESS]) : null;
}

// A look-only request carries no body: a GET body would reach a proxied service unframed.
export function hasBody(req) {
  const length = req?.headers?.["content-length"];
  return req?.headers?.["transfer-encoding"] !== undefined || (length !== undefined && length !== "0");
}

// A path segment never starts with a dot, so ".." can't appear.
const SEGMENT = "[A-Za-z0-9_][A-Za-z0-9_.-]*";
const CODE = new RegExp(`^/(?:${SEGMENT}/)*${SEGMENT}\\.(?:js|mjs|jsx|css)$`);
const TRUSTED = new RegExp(`^/(?:vendor|fonts|icons)/(?:${SEGMENT}/)*${SEGMENT}\\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$`);
const ROOT_FILES = new Set(["/favicon.svg", "/favicon.ico", "/favicon-32.png", "/apple-touch-icon.png", "/site.webmanifest"]);
const NOT_CODE = /^\/(?:home-agent|spatial-spike|assets|data|img|artifacts|private|recordings|logs|node_modules|tests?|fixtures)(?:\/|$)|\.test\.|\/tests?\//;
const REVISION = /^\/revisions\/(?:0|[1-9][0-9]{0,14})$/;
const BRAIN = new RegExp(`^${LIGHTING_BRAIN_PREFIX}/v1/homes/${DEN_SITE}/(?:state|stream)$`);
// A cache-buster only: Home's ?v= is the asset version plus a release suffix, about 140 characters, URL-encoded.
const cacheQuery = query => !query || query.split("&").every(part => /^(?:cb|v|fresh)=[A-Za-z0-9._~%-]{1,200}$/.test(part));

// What a look-only request may read: "index" | "static" | "auth-status" | "pack-index" | "pack" | "registration" |
// "den-live" | "lighting-brain", or null.
// - The page shell and the app's code, styles, fonts and icons. No scan, data or other asset folder.
// - The content packs' index, which the gateway filters to the den's packs, and the den's pack and lighting files.
// - The den's registration, read only.
// - den-live's state and stream (its markers), never its body path.
// - The lighting brain's den state and stream, which the den view's spot follows.
// - /auth/status, which says "look-only".
export function lookOnlyKind(method, rawUrl) {
  if (method !== "GET" && method !== "HEAD") return null;
  const raw = String(rawUrl || "");
  if (!raw.startsWith("/") || raw.includes("#") || raw.includes("\\")) return null;
  const at = raw.indexOf("?");
  const pathname = at < 0 ? raw : raw.slice(0, at), query = at < 0 ? "" : raw.slice(at + 1);
  if (pathname.includes("%") || pathname.includes("//")) return null;
  if (pathname === "/" || pathname === "/index.html") return "index";
  if (CODE.test(pathname) || TRUSTED.test(pathname) || ROOT_FILES.has(pathname) || pathname === "/precompiled/manifest.json") {
    return !NOT_CODE.test(pathname) && cacheQuery(query) ? "static" : null;
  }
  // Pack files take a cache-buster too, as Home fetches them; nothing else takes a query.
  if (pathname === "/content-packs/index.json") return cacheQuery(query) ? "pack-index" : null;
  if (pathname.startsWith(`/content-packs/${DEN_SITE}/`)) return cacheQuery(query) ? "pack" : null;
  if (query) return null;
  if (pathname === "/auth/status") return "auth-status";
  if (pathname === REGISTRATION_ROUTE || pathname.startsWith(REGISTRATION_ROUTE + "/") && REVISION.test(pathname.slice(REGISTRATION_ROUTE.length))) {
    return method === "GET" ? "registration" : null;
  }
  // The markers only: the live body stays with signed-in Home.
  if (pathname.startsWith(DEN_LIVE_PREFIX + "/") && denLiveRouteKind(method, pathname.slice(DEN_LIVE_PREFIX.length)) === "read") return "den-live";
  if (method === "GET" && BRAIN.test(pathname)) return "lighting-brain";
  return null;
}

// Code files that hold private data, served to a look-only device only through their redaction. Each redaction
// returns null when anything private could be left (the file's shape changed), and then the file isn't served.
// The site registry: both homes' street addresses and their coordinates. Only the world view uses them, and it's off
// in look-only; NaN makes every anchor check read "no anchor".
export function redactSiteRegistry(text) {
  const out = String(text)
    .replace(/(\b(?:address|note):\s*)"[^"\n]*"/g, '$1""')
    .replace(/(\b(?:latitude|longitude|altitudeMeters|altitude_m|altitude)\s*:\s*)-?\d+(?:\.\d+)?(?:e-?\d+)?/gi, "$1NaN")
    // Comments quote measured figures too.
    .replace(/^(\s*(?:\*|\/\/|\/\*).*)$/gm, line => line.replace(/-?\d{1,3}\.\d{4,}/g, "…"));
  const leftover = /\baddress:\s*"[^"]/.test(out) || /\b(?:latitude|longitude|lat|lng|lon)\s*:\s*-?\d/i.test(out)
    || /-?\d{1,3}\.\d{5,}/.test(out);
  return leftover ? null : out;
}
export const REDACTED = new Map([["/home-site-registry.js", redactSiteRegistry]]);

// The packs index, with only the den's packs: a look-only device learns nothing about any other site.
export function lookOnlyPackIndex(index) {
  return { ...index, packs: (Array.isArray(index?.packs) ? index.packs : []).filter(item => item?.manifest?.siteId === DEN_SITE) };
}

// The page shell's line telling Home it's look-only. It also keeps the service worker off on that device.
export function lookOnlyRuntime() {
  return `window.HomeLookOnly=Object.freeze({active:true,site:${JSON.stringify(DEN_SITE)}});window.HG_DISABLE_SERVICE_WORKER=true;`;
}
