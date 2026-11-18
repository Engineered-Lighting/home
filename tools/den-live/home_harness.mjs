// The loopback Home harness den-live's local pages share (the scan page, step 2; the live page, step 3). Modelled on the
// den simulation's run.mjs: Home's own app/src, unchanged, with your scan's content pack (or the authored room, for
// tests) through the gateway's own read-only routes, all loaded from the app root a page serves, so a page can serve
// another branch's Home. It binds 127.0.0.1 only and refuses any other Host. A page adds its own routes; besides
// those it answers GET only; anything it doesn't serve is a 503; WebSocket upgrades are destroyed.
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const HOST = '127.0.0.1';

export function option(name, fallback) {
  const at = process.argv.indexOf('--' + name);
  return at > 1 ? process.argv[at + 1] : fallback;
}
export function fail(text) { console.log(`refused: ${text}`); process.exit(2); }

/* The gateway and Home modules this harness uses, from appRoot. SITE is the den's site, as the gateway's own route names it. */
export async function loadHome(appRoot) {
  const at = relative => import(pathToFileURL(path.join(appRoot, relative)).href);
  const [registration, packRoute, lighting, fixtures, core] = await Promise.all([
    at('web-gateway/equipment-registration.mjs'), at('web-gateway/content-pack-route.mjs'), at('web-gateway/lighting-layer.mjs'),
    at('tools/fixtures/apartment-home-shell-fixtures.mjs'), at('app/src/home-equipment-registration.js')]);
  return { appRoot, src: path.join(appRoot, 'app/src'), site: /\/sites\/([^/]+)\//.exec(registration.REGISTRATION_ROUTE)[1],
    REGISTRATION_ROUTE: registration.REGISTRATION_ROUTE, readActiveBinding: registration.readActiveBinding,
    createContentPackHandler: packRoute.createContentPackHandler, createLightingLayerHandler: lighting.createLightingLayerHandler,
    installPack: fixtures.installPack, authoredDocument: fixtures.authoredDocument, Core: core.default };
}

/* Your scan's packs (refs.json's content_packs, as the den simulation reads them), or the authored room in a new folder under
 * tmp, removed when the process exits (tests give their own tmp: a terminated process skips 'exit'). */
export function packRoot(home, { room, refsRoot, packs, tmp, prefix }) {
  if (room === 'real') {
    const refs = JSON.parse(fs.readFileSync(path.join(refsRoot, 'tools', 'den-sim', 'refs.json'), 'utf8'));
    return path.resolve(packs || refs.content_packs);
  }
  const folder = fs.mkdtempSync(path.join(path.resolve(tmp || os.tmpdir()), prefix));
  process.on('exit', () => fs.rmSync(folder, { recursive: true, force: true }));
  const item = home.installPack(folder, home.site);
  fs.writeFileSync(path.join(folder, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [{ manifest: item.manifest, active: true, baseUrl: item.base }] }));
  return folder;
}

/* app/src as the den simulation serves it: scripts, styles, trusted vendor files and the root's own assets. */
export function staticSources(root) {
  const sources = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs', 'precompiled']);
  (function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), name = path.relative(root, file).split(path.sep).join('/');
      if (name === 'assets/apartment' || name === 'img/raw') continue;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { walk(file); continue; }
      const trusted = /^(?:vendor|fonts|icons)\//.test(name) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (/\.(?:js|mjs|jsx|css)$/.test(entry.name) || trusted || rootAsset) sources.set('/' + name, file);
    }
  })(root);
  return sources;
}

export const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.jsx': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf', '.wasm': 'application/wasm' };
const type = file => TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream';
const SECURITY = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self' data: blob:; connect-src 'self' data: blob:; img-src 'self' data: blob:; media-src 'self' blob:; "
    + "font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; "
    + "frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'self';" };

export function send(res, status, body, headers = {}) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': bytes.length, ...headers });
  res.end(bytes);
}
export const refuse = (res, status) => send(res, status, { ok: false, error: 'not_served', detail: 'Loopback page: nothing beyond this harness is reachable.' });
export function readBody(req, limit = 16384) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', chunk => { size += chunk.length; if (size > limit) { reject(Error('body_too_large')); req.destroy(); } else chunks.push(chunk); });
    req.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { reject(Error('json_invalid')); } });
    req.on('error', reject);
  });
}

/* The server. pageInit runs before Home's own scripts; panel is the page's own script, served at panelRoute and added
 * before </body>; routes(req, res, url) answers a page's own paths and returns true when it did. */
export function createHarness({ home, packs, port, pageInit, panel, panelRoute, panelModule = false, authoredRoom = false,
  routes = async () => false }) {
  const origin = `http://${HOST}:${port}`;
  let binding;
  try { binding = home.readActiveBinding(packs); } catch (error) { return { error: error.code || error.message }; }
  // The authored room's sockets, as the den simulation serves them. Your scan needs none here: no page reads sockets.
  const authored = authoredRoom ? home.Core.sealDocument(home.Core.normalizeDocument(home.authoredDocument(binding, home.Core))) : null;
  const sources = staticSources(home.src);
  const handleLighting = home.createLightingLayerHandler({ contentPacksDir: packs, authenticate: () => true });
  const handlePack = home.createContentPackHandler({ contentPacksDir: packs, authenticate: () => true });
  const lab = { site_id: home.site, cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } };
  const tag = `<script${panelModule ? ' type="module"' : ''} src="${panelRoute}"></script>`;
  const homePage = () => {
    const html = fs.readFileSync(path.join(home.src, 'index.html'), 'utf8');
    if (!html.includes('<head>') || !html.includes('</body>')) throw Error('index.html no longer has the expected shape');
    return html.replace('<head>', '<head>' + pageInit).replace('</body>', tag + '</body>');
  };
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, origin), { pathname } = url;
    for (const [name, value] of Object.entries(SECURITY)) res.setHeader(name, value);
    try {
      if (req.headers.host !== `${HOST}:${port}`) return refuse(res, 403);
      if (await routes(req, res, url)) return undefined;
      if (req.method !== 'GET') return refuse(res, 405);
      if (pathname === '/' && !url.search) { res.writeHead(302, { Location: '/?apartmentRedesign=1&worldZoom=off' }); return res.end(); }
      if (pathname === '/' || pathname === '/index.html') return send(res, 200, homePage(), { 'Content-Type': TYPES['.html'] });
      if (pathname === panelRoute) return send(res, 200, fs.readFileSync(panel), { 'Content-Type': TYPES['.js'] });
      if (pathname === '/auth/status') return send(res, 200, { authenticated: true, username: 'den-live' });
      if (pathname === '/api/perception-lab/v1/capabilities') return send(res, 200, lab, { 'X-Home-Lab-Access': 'read-only' });
      if (authored && (pathname === home.REGISTRATION_ROUTE || pathname === `${home.REGISTRATION_ROUTE}/revisions/${authored.revision}`)) {
        return send(res, 200, { ok: true, registration: authored, capabilities: { read: true, write: false } });
      }
      if (pathname.startsWith('/content-packs/')) {
        if (!handleLighting(req, res)) handlePack(req, res);
        return undefined;
      }
      const file = sources.get(pathname);
      if (file) return send(res, 200, fs.readFileSync(file), { 'Content-Type': type(file) });
      return refuse(res, 503);
    } catch (error) {
      if (!res.headersSent) send(res, 500, { ok: false, error: error.message }); else res.destroy();
      return undefined;
    }
  });
  server.on('upgrade', (_req, socket) => { socket.on('error', () => {}); socket.destroy(); });
  server.on('clientError', (_error, socket) => socket.destroy());
  return { server, binding, origin };
}
