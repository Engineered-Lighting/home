// Loopback-only design review: real pinned renderer, authored room/registration,
// explicit candidate UI files, and the unchanged supplied reference prototype.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const options = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  assert.ok(process.argv[index].startsWith('--') && process.argv[index + 1], 'options require --name value');
  options.set(process.argv[index].slice(2), process.argv[index + 1]);
}
const option = (name, fallback) => {
  const value = options.get(name) || fallback;
  assert.ok(value, 'required --' + name); return value;
};
const frozenRoot = path.resolve(option('frozen-root'));
const baselineFile = path.resolve(option('baseline'));
const referenceRoot = path.resolve(option('reference-root'));
const uiRoot = path.resolve(option('ui-root', ROOT));
const acceptedRoot = path.resolve(option('accepted-source-root', path.join(path.dirname(baselineFile), 'live-source')));
const uiNames = new Set(['app/src/home-app.jsx', 'app/src/home-apartment.jsx', 'app/src/home-tokens.css']);
const run = path.join(ROOT, '.tmp/apartment-design-preview', 'run-' + Date.now());
fs.mkdirSync(run, { recursive: true });
const evidenceRoot = path.join(run, 'evidence');
fs.mkdirSync(evidenceRoot);
const baseline = JSON.parse(fs.readFileSync(baselineFile, 'utf8'));
const frozenManifestFile = path.join(frozenRoot, 'capture-manifest.json');
const frozen = JSON.parse(fs.readFileSync(frozenManifestFile, 'utf8'));
const sources = new Map(), references = new Map();
const receipt = { schema: 'home-apartment-design-preview-1', created_at: new Date().toISOString(),
  authored_room: true, actual_renderer: true, live_camera: false, physical_commands: 0, session_starts: 0,
  equipment_saves: 0, source_pins: {}, served: {}, missing: [], blocked: [], errors: [], reference_sources: {},
  baseline: { file: baselineFile, sha256: hash(fs.readFileSync(baselineFile)), asset_version: baseline.assetVersion },
  frozen_manifest: { file: frozenManifestFile, sha256: hash(fs.readFileSync(frozenManifestFile)) },
  candidate_ui_root: uiRoot, accepted_source_root: acceptedRoot, reference_root: referenceRoot,
  fixture_bootstrap: { service_worker: 'disabled', background_warmup: 'off', default_home: 'victoria', explicit_home_query: true },
  evidence_root: evidenceRoot, run };
function checked(file, expected) {
  const stat = fs.lstatSync(file);
  assert.ok(stat.isFile() && !stat.isSymbolicLink(), 'source must be an ordinary file: ' + file);
  const bytes = fs.readFileSync(file);
  assert.equal(hash(bytes), expected, 'source pin changed: ' + file);
  return bytes;
}
for (const [name, pin] of Object.entries(frozen.files)) {
  if (!name.startsWith('app/src/')) continue;
  const file = path.join(frozenRoot, name);
  checked(file, pin.sha256);
  sources.set('/' + name.slice(8), { file, expected: pin.sha256, kind: 'frozen-public-source' });
}
// Accepted fresh renderer changes are never replaced by the older public tree.
// Read accepted files only when their bytes match the fresh live receipt, then
// snapshot them inside this preview's isolated source directory.
for (const [name, pin] of Object.entries(baseline.files)) {
  if (!name.startsWith('app/src/') || uiNames.has(name)) continue;
  const route = '/' + name.slice(8), previous = sources.get(route);
  if (previous?.expected === pin.sha256) continue;
  const source = path.join(acceptedRoot, name), bytes = checked(source, pin.sha256);
  const file = path.join(run, 'accepted-source', name);
  fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, bytes);
  sources.set(route, { file, expected: pin.sha256, kind: 'accepted-live-snapshot', original: source });
}
for (const name of uiNames) {
  const file = path.join(uiRoot, name), bytes = fs.readFileSync(file);
  assert.ok(fs.lstatSync(file).isFile() && !fs.lstatSync(file).isSymbolicLink());
  sources.set('/' + name.slice(8), { file, kind: 'explicit-candidate-ui', initial: hash(bytes) });
}
for (const [route, source] of sources) receipt.source_pins[route] = { ...source, sha256: source.expected || source.initial };
// Tool-side schema/store modules must match the same frozen production facts.
assert.equal(hash(fs.readFileSync(path.join(ROOT, 'app/src/home-equipment-registration.js'))),
  frozen.files['app/src/home-equipment-registration.js'].sha256, 'fixture schema matches frozen runtime schema');
assert.equal(hash(fs.readFileSync(path.join(ROOT, 'web-gateway/equipment-registration.mjs'))),
  '9e8adf869a5c2bfcf7066ebc5c7a53c0a045117f0676bf7baed5d3caf1336a99', 'fixture gateway matches accepted frozen helper');
receipt.fixture_modules = Object.fromEntries(['app/src/home-equipment-registration.js', 'web-gateway/equipment-registration.mjs',
  'tools/fixtures/apartment-home-shell-fixtures.mjs'].map(name => [name, hash(fs.readFileSync(path.join(ROOT, name)))]));
let referenceCount = 0;
function referenceInventory(directory, depth = 0) {
  assert.ok(depth < 8);
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    assert.ok(++referenceCount < 2000);
    const file = path.join(directory, item.name), stat = fs.lstatSync(file);
    assert.ok(!stat.isSymbolicLink(), 'reference links are forbidden');
    if (stat.isDirectory()) { referenceInventory(file, depth + 1); continue; }
    if (!stat.isFile() || !/\.(?:html|css|jsx?|woff2?|png|jpe?g|svg|webp)$/.test(item.name)) continue;
    assert.ok(stat.size < 16 * 1024 * 1024);
    const relative = path.relative(referenceRoot, file).split(path.sep).join('/'), sha256 = hash(fs.readFileSync(file));
    references.set('/reference/' + relative, { file, expected: sha256, kind: 'unchanged-reference' });
    receipt.reference_sources[relative] = sha256;
  }
}
referenceInventory(referenceRoot);

const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
const packIndex = Buffer.from(JSON.stringify({ schemaVersion: 1,
  packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
fs.writeFileSync(path.join(packs, 'index.json'), packIndex);
receipt.registration = initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs }).digest;
const mime = file => ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.jsx': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2': 'font/woff2', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml', '.wasm': 'application/wasm' }[path.extname(file)] || 'application/octet-stream');
// The pinned renderer fetches embedded WASM/textures from data/blob URLs.
// Permit those local bytes while keeping external household endpoints excluded.
const baseCsp = "default-src 'self' data: blob:; connect-src 'self' data: blob:; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; frame-src 'none'; object-src 'none'; ";
const referenceScripts = ['https://unpkg.com/three@0.149.0/build/three.min.js',
  'https://unpkg.com/react@18.3.1/umd/react.development.js', 'https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js',
  'https://unpkg.com/@babel/standalone@7.29.0/babel.min.js'];
receipt.reference_cdn_allowlist = referenceScripts;
let origin, registrationHandler, flushTimer;
function flush() {
  clearTimeout(flushTimer);
  fs.writeFileSync(path.join(run, 'preview-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
}
function changed() { clearTimeout(flushTimer); flushTimer = setTimeout(flush, 100); }
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Security-Policy', baseCsp + "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
      + (url.pathname.startsWith('/reference/') ? ' ' + referenceScripts.join(' ') : '') + ';');
    if (!['GET', 'HEAD'].includes(req.method)) {
      receipt.blocked.push({ method: req.method, path: url.pathname }); changed(); res.writeHead(403); res.end('Read-only authored preview'); return;
    }
    if (url.pathname === '/preview/receipt') {
      flush(); res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(receipt)); return;
    }
    // Test the raw prefix as URL parsing normalizes dot segments before routing.
    if (req.url.split('?')[0].startsWith('/review/')) {
      const name = decodeURIComponent(req.url.split('?')[0].slice('/review/'.length));
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:jpg|jpeg|html|json)$/i.test(name) || name.includes('..')) {
        res.writeHead(400); res.end('Invalid review filename'); return;
      }
      const file = path.join(evidenceRoot, name);
      if (!fs.existsSync(file)) { res.writeHead(404); res.end('Review file has not been written'); return; }
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.isSymbolicLink() || path.dirname(fs.realpathSync(file)) !== fs.realpathSync(evidenceRoot)
          || stat.size > 32 * 1024 * 1024) { res.writeHead(403); res.end('Review file refused'); return; }
      const body = fs.readFileSync(file);
      res.writeHead(200, { 'Content-Type': mime(file), 'Content-Length': body.length });
      res.end(req.method === 'HEAD' ? undefined : body); return;
    }
    if (url.pathname === '/healthz' || url.pathname === '/auth/status') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(url.pathname === '/auth/status' ? { authenticated: true, username: 'authored-owner' } : { ok: true, preview: true })); return;
    }
    if (url.pathname.startsWith('/api/apartment/v1/')) return registrationHandler(req, res);
    let bytes, type = 'application/json';
    if (url.pathname === '/content-packs/index.json') bytes = packIndex;
    const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
    if (pack) { bytes = pack.files[url.pathname.slice(pack.base.length)]; type = mime(url.pathname); }
    if (url.pathname.startsWith('/api/perception-lab/v1/')) {
      const suffix = url.pathname.slice('/api/perception-lab/v1/'.length);
      const payload = suffix === 'capabilities' ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {},
        recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
        : suffix === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : suffix === 'recordings' ? { items: [] } : null;
      if (payload) bytes = Buffer.from(JSON.stringify(payload));
    }
    if (url.pathname.startsWith('/proxy/')) {
      const payload = /\/(?:model|apartment_model)$/.test(url.pathname) ? echo.seed
        : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
      bytes = Buffer.from(JSON.stringify(payload));
    }
    const route = url.pathname === '/' ? '/index.html' : url.pathname === '/reference/' ? '/reference/index.html' : url.pathname;
    const source = sources.get(route) || references.get(route);
    if (source) {
      bytes = source.expected ? checked(source.file, source.expected) : fs.readFileSync(source.file);
      const sha256 = hash(bytes);
      const record = { sha256, kind: source.kind, served_at: new Date().toISOString() };
      (receipt.served[route] ||= []).push(record); changed();
      type = mime(source.file);
      if (route === '/index.html') {
        const selected = url.searchParams.get('home'), site = selected === 'echo' ? 'echo' : 'victoria';
        const init = `<script>window.HG_DISABLE_SERVICE_WORKER=true;localStorage.setItem('home.perf.backgroundWarmup','off');if(${selected === 'echo' || selected === 'victoria'}||!localStorage.getItem('apartment3d.site'))localStorage.setItem('apartment3d.site',${JSON.stringify(site)});</script>`;
        bytes = Buffer.from(bytes.toString().replace('<head>', '<head>' + init));
        record.fixture_bootstrap_injected = true; record.wire_sha256 = hash(bytes);
        res.setHeader('Set-Cookie', 'authored_owner_session=1; Path=/; SameSite=Strict; HttpOnly');
      }
    }
    if (!bytes) {
      receipt.missing.push({ method: req.method, path: url.pathname }); changed(); res.writeHead(404); res.end('Not in pinned preview inventory'); return;
    }
    res.writeHead(200, { 'Content-Type': type, 'Content-Length': bytes.length }); res.end(req.method === 'HEAD' ? undefined : bytes);
  } catch (error) {
    receipt.errors.push({ path: req.url, error: error.message }); changed();
    res.writeHead(500, { 'Content-Type': 'text/plain' }); res.end(error.message);
  }
});
server.on('upgrade', (req, socket) => {
  socket.on('error', () => {}); receipt.blocked.push({ method: 'UPGRADE', path: req.url }); changed(); socket.destroy();
});
server.on('clientError', (_error, socket) => socket.destroy());
await new Promise(resolve => server.listen(Number(option('port', '0')), '127.0.0.1', resolve));
origin = 'http://127.0.0.1:' + server.address().port;
registrationHandler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
  HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
  authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
receipt.urls = { implementation: origin + '/?apartmentRedesign=1&worldZoom=off',
  reference: origin + '/reference/index.html?home=victoria&theme=dark&vic=none&dock=default&detail=simple&sheet=half',
  review: origin + '/review/', receipt: origin + '/preview/receipt' };
flush(); console.log(JSON.stringify({ ...receipt.urls, receipt_file: path.join(run, 'preview-receipt.json') }, null, 2));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { flush(); server.closeAllConnections(); server.close(() => process.exit()); });
