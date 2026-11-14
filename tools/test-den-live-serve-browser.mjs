import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import os from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import Core from '../app/src/home-equipment-registration.js';
import { initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { authoredDocument, installPack } from './fixtures/apartment-home-shell-fixtures.mjs';
import { measuredBodyEvent } from './fixtures/den-measured-body.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const certDir = path.join(root, 'tools/shared-home/tests/fixtures/gateway_client');
const ca = fs.readFileSync(path.join(certDir, 'ca.pem'));
const denBase = '/api/den-live/v1/homes/den';
const relayCredential = 'a'.repeat(64);
const haToken = 'authored-server-ha-credential';
const golden = JSON.parse(fs.readFileSync(new URL('./den-live/fixtures/preview-f3.json', import.meta.url), 'utf8'));
const listen = server => new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => resolve(server.address().port));
});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function clickCoordinator(page, button, command) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith(denBase + '/coordinator/' + command)
      && response.request().method() === 'POST'),
    button.click(),
  ]);
  assert.ok(response.ok(), `${command} returned ${response.status()}: ${await response.text()}`);
}

let folder, upstream, relay, gateway, browser, origin, den, scene, ordinaryPort, servePort;
let previewPort = null;
const relaySockets = new Set();
const fake = { streams: new Set(), bodies: new Set(), sockets: new Set(), actions: [], ws: [], labels: null,
  bodyAvailable: true, bodyStreaming: true, bodySeq: 0, haOffline: false,
  control: { on: false, stopped: false, stop_counter: 0 }, epoch: 'fixture-epoch', seq: 0 };

function json(res, value, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
}
function labelDocument() {
  return { schema: 'den-room-labels-1', home: 'den', room_id: 'den', content_revision: den.revision,
    revision: 1, saved_at: null, labels: [{ id: 'l-authored', name: 'Reading table', kind: 'table',
      box: { centre_m: [1.4, 1.5, 0.4], size_m: [0.8, 0.6, 0.8], yaw_deg: 0 },
      on_wall: false, made_by: 'owner', status: 'confirmed', updated_at: null }] };
}
function spotEvent() {
  const now = performance.now() / 1000;
  const on = fake.control.on && !fake.control.stopped;
  const row = on ? golden.snapshot.fixtures.F3 : golden.stopped_snapshot.fixtures.F3;
  const ids = scene.equipment.filter(item => item.kind === 'mount' && item.position_m != null).map(mount =>
    scene.equipment.find(item => item.kind === 'fixture' && item.mounted_at_ref === mount.id)?.id ?? `${mount.id}-virtual-fixture`);
  return { ...golden.snapshot, site: 'victoria', epoch: fake.epoch, seq: ++fake.seq,
    sampled_mono: now, valid_until_mono: now + 1.5, ...fake.control,
    binding: { transform_id: 'authored-transform', scan_revision: den.revision,
      equipment_revision: scene.revision, equipment_digest: scene.digest },
    fixtures: Object.fromEntries(ids.map(fixture => [fixture, { ...row,
      last_move_trace_id: `${fake.epoch}/${fixture}/trace-1` }])) };
}
function openEvents(res, clients, write, headers = {}, first = '') {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', ...headers });
  res.write('retry: 500\n\n');
  if (first) res.write(first);
  clients.add(res);
  const timer = setInterval(() => write(res), 200);
  res.on('close', () => { clearInterval(timer); clients.delete(res); });
}
async function upstreamRequest(req, res) {
  let text = '';
  for await (const part of req) text += part;
  const body = text ? JSON.parse(text) : null;
  const { pathname } = new URL(req.url, 'http://fixture.local');
  if (previewPort && (pathname === denBase + '/stream' || pathname.startsWith(denBase + '/coordinator/virtual'))) {
    if (req.method === 'POST') fake.actions.push({ path: pathname, body, headers: req.headers });
    const host = '127.0.0.1:' + previewPort;
    const request = http.request({ hostname: '127.0.0.1', port: previewPort, path: pathname, method: req.method,
      headers: { host, ...(text ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(text) } : {}) } },
    response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    request.on('error', () => res.destroy());
    res.on('close', () => request.destroy());
    request.end(text);
    return;
  }
  if (pathname === denBase + '/stream') return openEvents(res, fake.streams, out => {
    const now = Date.now() / 1000;
    const people = { schema: 'den-live-people-1', home: 'den', seq: ++fake.seq, transform_id: 'authored-transform',
      content_revision: den.revision, capture_ts: now, emitted_ts: now, mode: 'rgb', camera: { status: 'ok' },
      tracks: [{ track_id: 'authored-person', state: 'confirmed', xy_m: [1.2, 1.5], sigma_m: .1, age_s: 0 }] };
    out.write('event: people\ndata: ' + JSON.stringify(people) + '\n\n');
    if (fake.spotStreaming !== false) out.write('event: spot_snapshot\ndata: ' + JSON.stringify(spotEvent()) + '\n\n');
  }, {}, 'event: spot_clock\ndata: ' + JSON.stringify({ epoch: fake.epoch, server_mono: performance.now() / 1000 }) + '\n\n');
  if (pathname === denBase + '/body-v2' && fake.bodyAvailable) return openEvents(res, fake.bodies, out => {
    if (fake.bodyStreaming) emitBody(out);
  }, { 'X-Home-Den-Body-Schema': 'den-live-body-2' },
  'event: body_clock\ndata: ' + JSON.stringify({ server_ts: Date.now() / 1000 }) + '\n\n');
  if (pathname === denBase + '/body') return json(res, { schema: 'den-live-body-1', private_fixture: true });
  if (pathname === denBase + '/body-v2') return json(res, { error: 'not_served' }, 404);
  if (pathname === denBase + '/labels') {
    if (req.method === 'GET') return json(res, fake.labels);
    fake.actions.push({ path: pathname, body, headers: req.headers });
    if (body.revision !== fake.labels.revision) return json(res, fake.labels, 409);
    fake.labels = { ...body, revision: body.revision + 1, saved_at: new Date().toISOString() };
    return json(res, fake.labels);
  }
  const virtual = denBase + '/coordinator/virtual';
  if (pathname === virtual) return json(res, { ...fake.control, snapshot: spotEvent(), settings: {} });
  if (pathname.startsWith(virtual + '/')) {
    fake.actions.push({ path: pathname, body, headers: req.headers });
    const command = pathname.slice(virtual.length + 1);
    if (command === 'resume' && body.stop_counter !== fake.control.stop_counter) {
      return json(res, { error: 'stop_counter_changed' }, 409);
    }
    if (command === 'switch') fake.control.on = body.on;
    if (command === 'stop') { fake.control.stop_counter += 1; fake.control.stopped = true; }
    if (command === 'resume') fake.control.stopped = false;
    return json(res, { ok: true, ...fake.control });
  }
  if (pathname.startsWith('/internal/home/serve-lighting/')) {
    assert.equal(req.headers.authorization, 'Bearer ' + relayCredential);
    fake.actions.push({ path: pathname, body, headers: req.headers });
    if (pathname.endsWith('/action') && fake.dropLightingReply) return req.socket.destroy();
    return json(res, { version: 1, result: { version: 1, status: 'done', operation_id: body.operation_id,
      results: ['echo', 'victoria'].map(site_id => ({ site_id, name: 'Reading lamp', operation: 'on', brightness: null, status: 'succeeded' })) } });
  }
  if (pathname.startsWith('/api/services/')) fake.actions.push({ path: pathname, body, headers: req.headers });
  if (pathname === '/api/states') return json(res, []);
  return json(res, { error: 'fixture_unavailable' }, 404);
}

function emitBody(out, reason) {
  const frame = measuredBodyEvent({ revision: den.revision, captured: Date.now() / 1000 - 0.05,
    seq: ++fake.bodySeq, reason });
  out.write('event: body\ndata: ' + JSON.stringify(frame) + '\n\n');
}

function serverFrame(socket, data) {
  const payload = Buffer.from(JSON.stringify(data));
  assert.ok(payload.length < 126);
  socket.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]));
}
function upgrade(req, socket) {
  assert.equal(req.url, '/api/websocket');
  fake.sockets.add(socket);
  socket.on('close', () => fake.sockets.delete(socket));
  const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
  serverFrame(socket, { type: 'auth_required', ha_version: '2026.11' });
  let buffered = Buffer.alloc(0);
  socket.on('data', chunk => {
    buffered = Buffer.concat([buffered, chunk]);
    while (buffered.length >= 6) {
      if (buffered[0] !== 0x81) return socket.destroy();
      const length = buffered[1] & 127;
      const offset = length === 126 ? 4 : 2;
      if (length === 127 || buffered.length < offset + 4) return;
      const size = length === 126 ? buffered.readUInt16BE(2) : length;
      if (buffered.length < offset + 4 + size) return;
      const mask = buffered.subarray(offset, offset + 4);
      const bytes = buffered.subarray(offset + 4, offset + 4 + size);
      const msg = JSON.parse(Buffer.from(bytes.map((n, i) => n ^ mask[i % 4])).toString());
      buffered = buffered.subarray(offset + 4 + size);
      fake.ws.push(msg);
      if (msg.type === 'auth') serverFrame(socket, { type: !fake.haOffline && msg.access_token === haToken ? 'auth_ok' : 'auth_invalid', ha_version: '2026.11' });
      else if (msg.type === 'ping') serverFrame(socket, { type: 'pong', id: msg.id });
      else serverFrame(socket, { type: 'result', id: msg.id, success: true, result: [] });
    }
  });
}
function request(url) {
  return new Promise((resolve, reject) => {
    const transport = url.startsWith('https:') ? https : http;
    const req = transport.get(url, { ca, timeout: 1000 }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', part => { data += part; });
      res.on('end', () => resolve({ status: res.statusCode, data, headers: res.headers }));
    });
    req.on('timeout', () => req.destroy(new Error('readiness timeout')));
    req.on('error', reject);
  });
}
async function unusedPort() {
  const server = net.createServer();
  const port = await listen(server);
  await new Promise(resolve => server.close(resolve));
  return port;
}
before(async () => {
  assert.ok(fs.existsSync(chromium.executablePath()), 'install the pinned Playwright Chromium');
  folder = fs.mkdtempSync(path.join(os.tmpdir(), 'home-serve-browser-'));
  const packs = path.join(folder, 'packs'), registration = path.join(folder, 'registration');
  // The equipment store refuses a folder others can read (POSIX only), as on LA.
  fs.mkdirSync(packs); fs.mkdirSync(registration, { mode: 0o700 });
  den = installPack(packs, 'victoria');
  const other = installPack(packs, 'echo');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [den, other].map(row => ({ manifest: row.manifest, active: true, baseUrl: row.base })) }));
  scene = initRegistration(registration, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  fake.labels = labelDocument();
  upstream = http.createServer((req, res) => upstreamRequest(req, res).catch(error => {
    if (!res.headersSent) json(res, { error: error.message }, 500);
    else res.destroy();
  }));
  upstream.on('upgrade', upgrade);
  const target = 'http://127.0.0.1:' + await listen(upstream);
  ordinaryPort = await unusedPort();
  servePort = await unusedPort();
  relay = tls.createServer({ cert: fs.readFileSync(path.join(certDir, 'server.pem')),
    key: fs.readFileSync(path.join(certDir, 'server-test-only.key')) }, client => {
    const upstreamSocket = net.connect(servePort, '127.0.0.1', () => {
      upstreamSocket.write('PROXY TCP4 100.101.102.103 100.64.0.2 ' + client.remotePort + ' 443\r\n');
      client.pipe(upstreamSocket); upstreamSocket.pipe(client);
    });
    upstreamSocket.on('error', () => client.destroy());
    client.on('error', () => upstreamSocket.destroy());
    client.on('close', () => upstreamSocket.destroy());
  });
  relay.on('connection', socket => {
    relaySockets.add(socket);
    socket.on('close', () => relaySockets.delete(socket));
  });
  origin = 'https://localhost:' + await listen(relay);
  const haFile = path.join(folder, 'ha-token'), relayFile = path.join(folder, 'relay-token');
  fs.writeFileSync(haFile, haToken, { mode: 0o600 }); fs.writeFileSync(relayFile, relayCredential, { mode: 0o600 });
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(HOME_WEB_|HOME_CONTENT_|HOME_AGENT_|STACK_TOKEN$)/.test(key)));
  Object.assign(env, { NODE_ENV: 'test', HOME_WEB_HOST: '127.0.0.1', HOME_WEB_PORT: String(ordinaryPort),
    HOME_WEB_SERVE_ORIGIN: origin, HOME_WEB_SERVE_PORT: String(servePort), HOME_WEB_AUTH_REQUIRED: '1',
    HOME_WEB_AUTH_FILE: path.join(folder, 'missing-auth.json'), HOME_WEB_BASIC_AUTH: 'fixture:ordinary-auth-required',
    HOME_CONTENT_PACKS_DIR: packs, HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
    HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: registration, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin,
    HOME_WEB_DEN_LIVE: '1', HOME_WEB_DEN_BODY_V2: '1', HOME_WEB_DEN_VIRTUAL: '1',
    HOME_WEB_DEN_LIVE_TARGET: target + '/api/den-live', HOME_WEB_ENABLE_LEGACY_HA_PROXY: '1',
    HOME_WEB_HA_TARGET: target, HOME_WEB_HA_TOKEN_FILE: haFile,
    HOME_WEB_SERVE_LIGHTING_RELAY_CREDENTIAL_FILE: relayFile, HOME_WEB_AGENT_BFF_TARGET: target + '/api/agent' });
  const targets = ['METRICS', 'VLLM', 'VISION', 'INTELLIGENCE', 'SUPERVISOR', 'S2S', 'TRACKER', 'VIDEO_LABELER', 'FRIGATE', 'LIGHTING_BRAIN'];
  for (const name of targets) env['HOME_WEB_' + name + '_TARGET'] = target;
  gateway = spawn(process.execPath, [path.join(root, 'web-gateway/server.mjs')], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '', problem;
  for (const stream of [gateway.stdout, gateway.stderr]) stream.on('data', chunk => { logs = (logs + chunk).slice(-8000); });
  for (let i = 0; i < 55 && gateway.exitCode === null; i++) {
    try {
      const health = await request('http://127.0.0.1:' + ordinaryPort + '/healthz');
      const shell = await request(origin + '/');
      if (health.status === 200 && shell.status === 200 && shell.data.includes('window.HomeServe')) { problem = null; break; }
      problem = 'health=' + health.status + ' shell=' + shell.status;
    } catch (error) { problem = error.message; }
    await delay(100);
  }
  assert.ok(gateway.exitCode === null && problem === null, 'gateway/HTTPS relay readiness: ' + problem + '\n' + logs);
  browser = await chromium.launch({ channel: 'chromium', headless: true,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-background-networking'] });
});
after(async () => {
  await browser?.close();
  gateway?.kill();
  gateway?.stdout.destroy(); gateway?.stderr.destroy();
  for (const socket of relaySockets) socket.destroy();
  relay?.close();
  for (const socket of fake.sockets) socket.destroy();
  for (const res of [...fake.streams, ...fake.bodies]) res.destroy();
  upstream?.closeAllConnections();
  upstream?.close();
  if (folder) fs.rmSync(folder, { recursive: true, force: true });
});

async function openHome(mobile) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, hasTouch: mobile, isMobile: mobile,
    viewport: mobile ? { width: 390, height: 844 } : { width: 1366, height: 768 } });
  await context.route('**/*', route => {
    const url = route.request().url();
    return url.startsWith(origin + '/') || /^(data|blob):/.test(url) ? route.continue() : route.abort();
  });
  await context.addInitScript(() => {
    localStorage.setItem('home.perf.backgroundWarmup', 'off');
    localStorage.setItem('apartment3d.prewarm', 'off');
    localStorage.setItem('apartment3d.site', 'victoria');
  });
  const page = await context.newPage();
  await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.HomeServe?.active === true && !!window.HomeLightingControl);
  return { page, context };
}

async function startPreview(config) {
  const child = spawn(process.env.PYTHON || 'python', ['-B', 'tools/den-live/test_sofa_browser_backend.py'],
    { cwd: root, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-8000); });
  const lines = createInterface({ input: child.stdout });
  const command = value => child.stdin.write(JSON.stringify(value) + '\n');
  const closed = new Promise(resolve => child.once('close', resolve));
  child.stdin.on('error', () => {});
  command(config);
  try {
    for await (const line of lines) {
      if (!line.startsWith('{')) continue;
      const ready = JSON.parse(line);
      if (Number.isInteger(ready.port)) {
        return { port: ready.port, command, closed, stop: async () => {
          command({ command: 'close' });
          const timeout = setTimeout(() => child.kill(), 5000);
          try { await closed; } finally { clearTimeout(timeout); }
        } };
      }
    }
  } catch (error) { stderr += '\n' + error.message; }
  child.kill();
  throw Error('Python preview did not start\n' + stderr);
}

test('full Serve shell and managed HA WebSocket on a fresh desktop and phone', { timeout: 90000 }, async () => {
  for (const mobile of [false, true]) {
    const { page, context } = await openHome(mobile);
    try {
      assert.equal(await page.locator('[data-look-only], input[placeholder="paste token"], iframe[src*="lighting-review"]').count(), 0);
      const auth = await page.evaluate(() => fetch('/auth/status').then(response => response.json()));
      assert.equal(auth.access, 'serve');
      assert.deepEqual(await context.cookies(), []);
      const pong = await page.evaluate(() => new Promise((resolve, reject) => {
        const socket = new WebSocket('wss://' + location.host + '/proxy/ha/api/websocket');
        const timer = setTimeout(() => { socket.close(); reject(new Error('HA handshake timed out')); }, 5000);
        socket.onerror = () => { clearTimeout(timer); reject(new Error('HA WebSocket failed')); };
        socket.onmessage = ({ data }) => {
          const value = JSON.parse(data);
          if (value.type === 'auth_required') socket.send(JSON.stringify({ type: 'auth', access_token: window.__HOME_WEB_HA_TOKEN }));
          if (value.type === 'auth_ok') socket.send(JSON.stringify({ type: 'ping', id: 83 }));
          if (value.type === 'pong') { clearTimeout(timer); socket.close(); resolve(value.id); }
        };
      }));
      assert.equal(pong, 83);
      assert.ok(fake.ws.some(value => value.type === 'auth' && value.access_token === haToken));
    } finally { await context.close(); }
  }
  assert.equal((await request('http://127.0.0.1:' + ordinaryPort + '/')).status, 401);
});

test('label CAS, exact virtual controls and body rollback gate through the browser origin', { timeout: 90000 }, async () => {
  const initial = { ...fake.control };
  const { page, context } = await openHome(false);
  try {
    const results = await page.evaluate(async base => {
      const labelUrl = base + '/labels';
      const original = await fetch(labelUrl).then(r => r.json());
      const edit = { ...original, labels: original.labels.map(label => ({ ...label, name: 'Book table' })) };
      const put = () => fetch(labelUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Home-Den-Labels': '1' },
        body: JSON.stringify(edit) });
      const call = (command, body) => fetch(base + '/coordinator/' + command, { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Home-Den-Live-CSRF': '1' }, body: JSON.stringify(body) });
      const save = await put(), stale = await put();
      const on = await call('switch', { on: true });
      const stop = await call('stop', {});
      const stopped = await stop.json();
      const rejected = await call('resume', { stop_counter: stopped.stop_counter - 1 });
      const resumed = await call('resume', { stop_counter: stopped.stop_counter });
      const off = await call('switch', { on: false });
      return [save.status, stale.status, on.status, stop.status, rejected.status, resumed.status, off.status];
    }, denBase);
    assert.deepEqual(results, [200, 409, 200, 200, 409, 200, 200]);
    assert.ok(fake.actions.filter(row => row.path.startsWith(denBase)).every(row => !row.headers.authorization && !row.headers.cookie));
    fake.bodyAvailable = false;
    const downgraded = await page.evaluate(async path => { const r = await fetch(path + '/body'); return [r.status, await r.text()]; }, denBase);
    assert.equal(downgraded[0], 503);
    assert.ok(!downgraded[1].includes('den-live-body-1'));
  } finally { fake.bodyAvailable = true; fake.control = initial; await context.close(); }
});

test('cross-home card works during HA outage and recovers a lost reply on reload', { timeout: 90000 }, async () => {
  fake.haOffline = true;
  const { page, context } = await openHome(false);
  let phase = 'open chat';
  page.setDefaultTimeout(15_000);
  try {
    const command = 'Turn on the reading lamp in both homes';
    async function showChat() {
      await page.locator('#apt-home-tab-chat').click();
      await page.locator('[data-home-chat-surface]').waitFor();
    }
    await showChat();
    await page.evaluate(() => localStorage.setItem('hg-prefs', JSON.stringify({
      endpoint: 'https://stale.invalid/ha', token: 'authored-stale-token', s2sBase: 'wss://stale.invalid/bridge'
    })));
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!window.HomeLightingControl && window.HomeServe?.active);
    await showChat();
    phase = 'wait for HA outage status';
    await page.locator('[data-home-managed-ha="unavailable"]').waitFor();
    assert.equal(await page.locator('input[placeholder="paste token"]').count(), 0);
    assert.equal(await page.getByText(/use \/token|bad token/i).count(), 0);
    async function send() {
      const input = page.getByRole('combobox', { name: 'Command input' });
      await input.fill(command);
      await input.press('Enter');
    }
    phase = 'send lighting command';
    await send();
    phase = 'wait for lighting result';
    await page.getByText(/Done.*Reading lamp.*Los Angeles.*Reading lamp.*Victoria/).waitFor();
    assert.equal(await page.locator('iframe[src*="lighting-review"]').count(), 0);
    await delay(1250);
    phase = 'simulate a lost action response';
    fake.dropLightingReply = true;
    await send();
    await page.getByRole('button', { name: 'Check outcome' }).waitFor();
    const actions = fake.actions.filter(row => row.path.endsWith('/serve-lighting/action'));
    const lost = actions.at(-1).body.operation_id;
    phase = 'reload and look up the lost action';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await showChat();
    await page.getByRole('button', { name: 'Check outcome' }).click();
    await page.getByText(/Done.*Reading lamp.*Los Angeles.*Reading lamp.*Victoria/).waitFor();
    assert.equal(fake.actions.filter(row => row.path.endsWith('/serve-lighting/action')).length, actions.length);
    assert.equal(fake.actions.at(-1).body.operation_id, lost);
  } catch (error) {
    const chat = await page.locator('[data-home-chat-surface]').innerText({ timeout: 1500 }).catch(() => '');
    const diagnostic = phase + ': ' + error.message + '\nChat: ' + chat.slice(-1500) +
      '\nFake lighting calls: ' + fake.actions.filter(row => row.path.includes('/serve-lighting/')).length;
    assert.fail(diagnostic);
  } finally { fake.haOffline = false; fake.dropLightingReply = false; await context.close(); }
});

// SwiftShader on a busy runner can slow this case well past its usual minute. If it still times out, the abort puts the
// fake back and closes this case's browser at once, so the cases after it don't inherit paused streams or a Stop.
test('the mobile Den draws a face-free figure, and Stop holds the visible spot through replay, restart and reload', { timeout: 240000 }, async t => {
  const initial = { ...fake.control }, initialEpoch = fake.epoch;
  const firstAction = fake.actions.length;
  const restore = () => { fake.control = initial; fake.epoch = initialEpoch; fake.bodyStreaming = true; fake.spotStreaming = true; };
  const { page, context } = await openHome(true);
  t.signal.addEventListener('abort', () => { restore(); context.close().catch(() => {}); }, { once: true });
  const panel = page.locator('[data-den-coordinator]');
  const button = command => panel.locator(`[data-den-coordinator-${command}]`);
  const live = () => page.evaluate(() => window.HomeDenLiveView.status());
  const waitForSpot = (on, timeout = 15000) => page.waitForFunction(lit => {
    const engine = window.__APT_ENGINE, spots = engine?.overlay?.virtualFixturesReadback()?.fixtures || [];
    const virtual = window.HomeDenLiveView?.status()?.spot;
    return spots.length && spots.every(row => row.state.spotlight.on === lit)
      && virtual?.fixtures?.every(row => row.on === lit);
  }, on, { timeout });
  const readDrawn = () => page.evaluate(() => {
    const overlay = window.__APT_ENGINE?.overlay;
    const roots = overlay?.virtualFixturesGroup?.children || [];
    return roots.map(root => root.getObjectByName('robotic-spotlight')?.userData.channel?.on);
  });
  try {
    await page.waitForFunction(() => window.HomeDenLiveView?.status()?.body?.layer?.points > 0, null, { timeout: 60000 });
    assert.equal((await live()).body.layer.trackId, 'authored-track');
    assert.ok((await live()).body.last_paint_age_s > 0);
    assert.equal(fake.bodies.size, 1);
    assert.equal(fake.streams.size, 1);
    fake.bodyStreaming = false;
    for (const connection of fake.bodies) emitBody(connection, 'ambiguous');
    await page.waitForFunction(() => {
      const status = window.HomeDenLiveView?.status();
      return status?.body?.reason === 'ambiguous' && status.body.layer?.points === 0 && status.layer?.tracks === 1;
    });
    fake.bodyStreaming = true;
    await page.waitForFunction(() => window.HomeDenLiveView?.status()?.body?.layer?.points > 0);
    await page.waitForFunction(() => window.HomeDenLiveView?.status()?.coordinator?.kind === 'running');
    const dark = await readDrawn();
    assert.ok(dark.length && dark.every(on => on === false));
    await panel.locator('summary').click();
    await clickCoordinator(page, button('switch'), 'switch');
    await waitForSpot(true);
    assert.ok((await readDrawn()).every(on => on === true));
    assert.match(await panel.locator('[data-den-coordinator-why]').innerText(), /wrists.*trace-1/);
    const lit = (await live()).spot.fixtures[0];
    assert.deepEqual([lit.pan_deg, lit.tilt_deg, lit.level, lit.keep_lit_token, lit.move_decision_id],
      [-45, 30, 200 / 254, 7, golden.decision_id]);
    fake.spotStreaming = false;
    await page.waitForFunction(() => {
      const status = window.HomeDenLiveView?.status();
      const rows = window.__APT_ENGINE?.overlay?.virtualFixturesReadback()?.fixtures || [];
      return !status?.spot && status?.layer?.tracks === 1 && rows.length && rows.every(row => !row.state.spotlight.on);
    });
    fake.spotStreaming = true;
    await waitForSpot(true);
    const preStop = spotEvent();
    await clickCoordinator(page, button('stop'), 'stop');
    await waitForSpot(false);
    for (const stream of fake.streams) stream.write('event: spot_snapshot\ndata: ' + JSON.stringify({ ...preStop, seq: ++fake.seq }) + '\n\n');
    await delay(400);
    assert.ok((await readDrawn()).every(on => on === false));
    fake.epoch = 'fixture-restarted';
    for (const stream of fake.streams) stream.end();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.HomeDenLiveView?.status()?.spot?.epoch === 'fixture-restarted', null, { timeout: 60000 });
    assert.ok((await readDrawn()).every(on => on === false));
    const other = await context.newPage();
    try {
      await other.bringToFront();
      await other.goto(page.url(), { waitUntil: 'domcontentloaded' });
      await other.waitForFunction(() => !document.hidden && window.HomeDenLiveView?.status()?.spot?.stopped === true);
      const observed = await other.evaluate(() => window.HomeDenLiveView.status().spot.stop_counter);
      await page.bringToFront();
      await page.waitForFunction(() => !document.hidden && window.HomeDenLiveView?.status()?.chip);
      await panel.locator('summary').evaluate(summary => {
        if (!summary.parentElement.open) summary.click();
      });
      await clickCoordinator(page, button('stop'), 'stop');
      const staleResume = await other.evaluate(async ({ url, stop_counter }) => {
        const response = await fetch(url + '/resume', { method: 'POST', headers: {
          'Content-Type': 'application/json', 'X-Home-Den-Live-CSRF': '1',
        }, body: JSON.stringify({ stop_counter }) });
        return response.status;
      }, { url: denBase + '/coordinator', stop_counter: observed });
      assert.equal(staleResume, 409, 'a second tab cannot resume past a newer Stop');
      assert.ok((await readDrawn()).every(on => on === false));
    } catch (error) {
      const otherState = await other.evaluate(() => ({ hidden: document.hidden, den: window.HomeDenLiveView?.status() }));
      error.message += '\nOther tab: ' + JSON.stringify(otherState);
      throw error;
    } finally { await other.close(); }
    await clickCoordinator(page, button('resume'), 'resume');
    await waitForSpot(true);
    await clickCoordinator(page, button('switch'), 'switch');
    await waitForSpot(false);
    assert.ok((await readDrawn()).every(on => on === false));
    assert.equal(await panel.locator('[data-den-coordinator-desk]').count(), 0);
    assert.deepEqual(fake.actions.slice(firstAction).map(row => row.path), ['switch', 'stop', 'stop', 'resume', 'resume', 'switch']
      .map(operation => denBase + '/coordinator/virtual/' + operation));
  } catch (error) {
    error.message += '\nDen state: ' + JSON.stringify(await page.evaluate(() => window.HomeDenLiveView?.status() || null));
    throw error;
  } finally { restore(); await context.close(); }
});

test('the normal Den label editor saves a label and keeps a draft across a revision conflict', { timeout: 110000 }, async () => {
  const initial = fake.labels;
  const { page, context } = await openHome(false);
  const name = 'Table beside the sofa';
  try {
    await page.waitForFunction(() => window.HomeDenLabelsView?.status()?.load === 'ok', null, { timeout: 60000 });
    await page.getByRole('button', { name: 'Label objects in the den' }).click();
    await page.locator('.home-den-labels-list [data-act="select"]').first().click();
    await page.locator('[data-field="name"]').fill(name);
    await page.locator('[data-field="name"]').press('Tab');
    fake.labels = { ...fake.labels, revision: fake.labels.revision + 1 };
    await page.locator('.home-den-labels-panel [data-act="save"]').click();
    await page.getByRole('dialog', { name: 'Labels changed elsewhere' }).waitFor();
    assert.equal((await page.evaluate(() => window.HomeDenLabelsView.labels()))[0].name, name);
    await page.getByRole('button', { name: 'Keep mine' }).click();
    await page.waitForFunction(() => window.HomeDenLabelsView?.status()?.save?.state === 'saved');
    assert.equal(fake.labels.labels[0].name, name);
    assert.equal(fake.labels.revision, initial.revision + 2);
  } catch (error) {
    error.message += '\nLabel state: ' + JSON.stringify(await page.evaluate(() => window.HomeDenLabelsView?.status() || null));
    throw error;
  } finally { fake.labels = initial; await context.close(); }
});

// The real den-live preview lives in the producer's own lane until it lands on main. Without it this case can't run
// here; it is qualified against the installed producer instead.
const producer = fs.existsSync(path.join(root, 'tools/den-live/test_sofa_browser_backend.py'));
test('the real coordinator trace, virtual spot and Stop agree through Full Home Serve', { timeout: 150000,
  skip: producer ? false : 'needs den-live (tools/den-live), which is not in this checkout' }, async () => {
  const firstAction = fake.actions.length;
  const { page, context } = await openHome(false);
  let preview = null;
  const traceRoot = path.join(folder, 'actual-preview');
  const traceRows = () => {
    const days = path.join(traceRoot, 'days');
    return fs.existsSync(days) ? fs.readdirSync(days).flatMap(day =>
      fs.readFileSync(path.join(days, day, 'coordinator-fixture_calls.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse)
        // Trace v2 opens each day file a run writes to with a run header; the receipt counts events only.
        .filter(row => row.kind !== 'run_header')) : [];
  };
  const button = name => page.locator(`[data-den-coordinator-${name}]`);
  async function waitForDark() {
    await page.waitForFunction(() => {
      const rows = window.__APT_ENGINE?.overlay?.virtualFixturesReadback()?.fixtures || [];
      return rows.length > 0 && rows.every(row => !row.state.spotlight.on);
    });
  }
  async function waitForLit() {
    await page.waitForFunction(() => {
      const sample = window.HomeDenLiveView?.status()?.spot;
      return sample?.fixtures?.some(row => row.on && row.motion_phase === 'settled');
    });
    return page.evaluate(() => window.HomeDenLiveView.status().spot.fixtures.find(row => row.on));
  }
  try {
    await page.waitForFunction(() => !!window.__APT_ENGINE?.overlay?.coordinatorSpotScope(), null, { timeout: 60000 });
    const geometry = await page.evaluate(() => {
      const overlay = window.__APT_ENGINE.overlay, scope = overlay.coordinatorSpotScope();
      return Object.fromEntries(scope.fixture_ids.map(id => {
        const d = overlay.virtualFixtureInteractionDescriptor(id);
        return [id, { aim_origin_m: d.display_origin_m, horizontal_world_reference: d.horizontal_world_reference,
          initial_cdeg: [0, -4500] }];
      }));
    });
    const site = await page.evaluate(() => window.__APT_ENGINE.siteId);
    preview = await startPreview({ state_dir: traceRoot, fixtures: geometry, site,
      binding: { transform_id: 'authored-transform', scan_revision: den.revision,
        equipment_revision: scene.revision, equipment_digest: scene.digest },
      target_m: [1.2, 1.38, .65], facing: [0, 1] });
    previewPort = preview.port;
    for (const stream of fake.streams) stream.end();
    await page.waitForFunction(() => window.HomeDenLiveView?.markers()?.some(row => row.track_id === 'synthetic-owner')
      || window.HomeDenLiveView?.status()?.spot?.epoch?.length === 32, null, { timeout: 20000 });
    await waitForDark();
    preview.command({ scene: 'reading' });
    const row = await waitForLit();
    const stamps = await (await page.waitForFunction(() => {
      const epoch = window.HomeDenLiveView.status().spot?.epoch, stamps = window.HomeDenPaintStamps || [];
      return stamps.some(stamp => stamp.epoch === epoch && stamp.lit === true) && stamps;
    })).jsonValue();
    assert.ok(stamps.length <= 600 && stamps.every(stamp => Object.keys(stamp).sort().join() === 'epoch,lit,paint_unix_ms,seq'
      && typeof stamp.epoch === 'string' && Number.isSafeInteger(stamp.seq) && typeof stamp.lit === 'boolean'
      && Number.isSafeInteger(stamp.paint_unix_ms)), 'the painted spot left cue-to-light paint stamps of four fields');
    const beam = await page.evaluate(id => {
      const root = window.__APT_ENGINE.overlay.virtualFixturesGroup.children.find(node => node.userData.fixture_id === id);
      return root.getObjectByName('robotic-spotlight').userData;
    }, row.fixture_id);
    assert.ok(beam.beam_origin_m?.length === 3 && beam.direction_unit?.length === 3);
    const rayError = point => {
      const vector = point.map((n, i) => n - beam.beam_origin_m[i]);
      const depth = vector.reduce((sum, n, i) => sum + n * beam.direction_unit[i], 0);
      return Math.hypot(...vector.map((n, i) => n - depth * beam.direction_unit[i]));
    };
    const error = rayError(row.target_m);
    assert.ok(error < .005, `rendered ray should cross the task target, got ${error} m`);
    assert.ok(rayError([row.target_m[0] + .5, ...row.target_m.slice(1)]) > .3, 'a nearby offset target misses the rendered ray');
    assert.equal(row.keep_lit_token, row.move_sequence);
    preview.command({ scene: 'standing' });
    await waitForDark();
    preview.command({ scene: 'reading' });
    await waitForLit();
    await page.locator('[data-den-coordinator] summary').evaluate(summary => {
      if (!summary.parentElement.open) summary.click();
    });
    await clickCoordinator(page, button('stop'), 'stop');
    await waitForDark();
    await page.waitForFunction(() => window.HomeDenLiveView?.status()?.spot?.stopped === true);
    let receipt, traces;
    for (let attempt = 0; attempt < 30; attempt++) {
      receipt = JSON.parse((await request('http://127.0.0.1:' + preview.port + denBase + '/test/receipt')).data);
      traces = traceRows();
      if (receipt.trace_event_index === traces.length
          && traces.filter(trace => trace.action === 'stop' && trace.outcome === 'encoded_only').length === Object.keys(geometry).length) break;
      await delay(40);
    }
    assert.deepEqual(receipt.writes, []);
    assert.equal(receipt.stopped, true);
    assert.equal(receipt.trace_complete, true);
    assert.equal(receipt.trace_event_index, traces.length);
    assert.equal(traces.filter(trace => trace.action === 'stop' && trace.outcome === 'encoded_only').length, Object.keys(geometry).length);
    await clickCoordinator(page, button('resume'), 'resume');
    await waitForLit();
    await clickCoordinator(page, button('switch'), 'switch');
    await waitForDark();
    assert.deepEqual(fake.actions.slice(firstAction).map(action => action.path), ['stop', 'resume', 'switch']
      .map(action => denBase + '/coordinator/virtual/' + action));
    traces = traceRows();
    assert.ok(traces.some(trace => trace.trace_id === row.last_move_trace_id && trace.outcome === 'encoded_only'));
    assert.ok(traces.some(trace => trace.action === 'stop' && trace.outcome === 'encoded_only'));
    assert.ok(traces.some(trace => trace.action === 'resume' && trace.outcome === 'encoded_only'));
  } catch (error) {
    error.message += '\nDen state: ' + JSON.stringify(await page.evaluate(() => window.HomeDenLiveView?.status() || null));
    throw error;
  } finally {
    await context.close();
    previewPort = null;
    await preview?.stop();
  }
});
