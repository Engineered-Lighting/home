// Real loopback TCP resets against the actual spawned gateway. No household
// credentials, camera, model, service, or non-loopback upstream is used.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVER = path.join(ROOT, 'web-gateway/server.mjs');
const PAIR = 'upgrade-test-owner:authored-loopback-password-only';
const BASIC = 'Basic ' + Buffer.from(PAIR).toString('base64');
const TOKEN = 'authored-loopback-ha-token-never-real';
const TOKEN_MARKER = '__home_web_gateway_ha_token__';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function until(check, description, ms = 2500) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (check()) return;
    await delay(10);
  }
  assert.ok(check(), description);
}

function request(port, urlPath, authorization = BASIC) {
  return new Promise((resolve, reject) => {
    const req = http.get({ hostname: '127.0.0.1', port, path: urlPath,
      headers: authorization ? { Authorization: authorization } : {}, agent: false }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { body += chunk; });
      res.once('end', () => resolve({ status: res.statusCode, body }));
      res.once('error', reject);
    });
    req.once('error', reject);
    req.setTimeout(1500, () => req.destroy(Error('bounded local HTTP request timeout')));
  });
}

async function freePort() {
  const probe = net.createServer();
  await new Promise((resolve, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', resolve); });
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  return port;
}

function frame(text) {
  const payload = Buffer.from(text);
  assert.ok(payload.length < 126);
  return Buffer.concat([Buffer.from([0x81, payload.length]), payload]);
}

function maskedFrame(text) {
  const payload = Buffer.from(text), mask = Buffer.from([0x17, 0x42, 0x93, 0xab]);
  assert.ok(payload.length < 126, 'authored browser frames remain small');
  return Buffer.concat([Buffer.from([0x81, 0x80 | payload.length]), mask,
    Buffer.from(payload.map((value, index) => value ^ mask[index % 4]))]);
}

async function startUpstream() {
  const sockets = new Set(), upgrades = [];
  const server = http.createServer((req, res) => { res.writeHead(200); res.end('{}'); });
  server.on('connection', socket => {
    sockets.add(socket); socket.on('error', () => {}); socket.once('close', () => sockets.delete(socket));
  });
  server.on('upgrade', (req, socket) => {
    const record = { url: req.url, socket, closed: false, ended: false, messages: [], errors: [], pending: Buffer.alloc(0) };
    upgrades.push(record); socket.once('close', () => { record.closed = true; });
    // Node's HTTP upgrade leaves the fixture socket half-open. Acknowledge only
    // a real peer FIN; otherwise a correctly destroyed gateway endpoint could
    // look leaked merely because this fake server kept its own write half open.
    socket.once('end', () => { record.ended = true; socket.end(); });
    const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key']
      + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    socket.write(frame(req.url === '/api/websocket' ? '{"type":"auth_required"}' : '{"type":"authored_tracker_ready"}'));
    socket.on('data', chunk => {
      record.pending = Buffer.concat([record.pending, chunk]);
      while (record.pending.length >= 6) {
        const bytes = record.pending, length = bytes[1] & 0x7f;
        if (!(bytes[1] & 0x80) || length >= 126) {
          record.errors.push('authored upstream requires small masked frames'); socket.destroy(); return;
        }
        if (bytes.length < 6 + length) return;
        const mask = bytes.subarray(2, 6), payload = Buffer.alloc(length);
        for (let index = 0; index < length; index++) payload[index] = bytes[6 + index] ^ mask[index % 4];
        const text = payload.toString(); record.messages.push(text); record.pending = bytes.subarray(6 + length);
        let message;
        try { message = JSON.parse(text); } catch { /* generic transparent payload */ }
        if (message?.type === 'auth') socket.write(frame(JSON.stringify({ type: message.access_token === TOKEN ? 'auth_ok' : 'auth_invalid' })));
        if (message?.type === 'ping') socket.write(frame(JSON.stringify({ type: 'pong', id: message.id })));
      }
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, sockets, upgrades, port: server.address().port };
}

async function startGateway(temp, upstream) {
  const port = await freePort();
  const tokenFile = path.join(temp, 'authored-ha-token');
  fs.writeFileSync(tokenFile, TOKEN + '\n', { mode: 0o600 });
  // Prevent inherited operator configuration from reaching another upstream.
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('HOME_WEB_')));
  Object.assign(env, { NODE_ENV: 'test', HOME_WEB_HOST: '127.0.0.1', HOME_WEB_PORT: String(port),
    HOME_WEB_AUTH_REQUIRED: '1', HOME_WEB_AUTH_FILE: path.join(temp, 'absent-auth.json'), HOME_WEB_BASIC_AUTH: PAIR,
    HOME_WEB_ENABLE_LEGACY_HA_PROXY: '1', HOME_WEB_ENABLE_LEGACY_TRACKER_PROXY: '1',
    HOME_WEB_HA_TARGET: 'http://127.0.0.1:' + upstream.port, HOME_WEB_TRACKER_TARGET: 'http://127.0.0.1:' + upstream.port,
    HOME_WEB_HA_TOKEN_FILE: tokenFile, HOME_WEB_STACK_TOKEN: '', STACK_TOKEN: '', HOME_WEB_STACK_TOKEN_FILE: path.join(temp, 'absent-stack-token'),
    HOME_WEB_AGENT_ORIGINS: 'https://agent-authored.example.invalid', HOME_WEB_NATIVE_AGENT_ORIGIN: 'https://native-authored.example.invalid',
    HOME_WEB_SITE_LANS: '', HOME_WEB_SITE_ZONES: '', HOME_WEB_LOCATION_PERSON: '', HOME_WEB_ASSET_VERSION: 'authored-upgrade-reset-test' });
  const child = spawn(process.execPath, [SERVER], { cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  for (const pipe of [child.stdout, child.stderr]) pipe.on('data', chunk => { output = (output + chunk.toString()).slice(-16000); });
  const gateway = { child, port, pid: child.pid, output: () => output };
  for (let attempt = 0; attempt < 100; attempt++) {
    assert.equal(child.exitCode, null, 'gateway stays alive during startup: ' + output);
    try { if ((await request(port, '/healthz')).status === 200) return gateway; } catch { /* bounded startup retry */ }
    await delay(20);
  }
  throw Error('gateway did not start: ' + output);
}

async function openUpgrade(gateway, urlPath = '/proxy/ha/api/websocket', authorization = BASIC, headers = {}) {
  const socket = net.connect(gateway.port, '127.0.0.1');
  const client = { socket, buffer: Buffer.alloc(0), header: null, closed: false };
  socket.on('error', () => {}); socket.once('close', () => { client.closed = true; });
  socket.on('data', chunk => {
    client.buffer = Buffer.concat([client.buffer, chunk]);
    if (client.header === null) {
      const end = client.buffer.indexOf('\r\n\r\n');
      if (end >= 0) { client.header = client.buffer.subarray(0, end).toString(); client.buffer = client.buffer.subarray(end + 4); }
    }
  });
  await new Promise((resolve, reject) => { socket.once('connect', resolve); socket.once('error', reject); });
  socket.write(['GET ' + urlPath + ' HTTP/1.1', 'Host: ' + (headers.Host || '127.0.0.1:' + gateway.port), 'Upgrade: websocket',
    'Connection: Upgrade', 'Sec-WebSocket-Version: 13', 'Sec-WebSocket-Key: ' + crypto.randomBytes(16).toString('base64'),
    ...(authorization ? ['Authorization: ' + authorization] : []), ...(headers.Origin ? ['Origin: ' + headers.Origin] : []), '', ''].join('\r\n'));
  await until(() => client.header !== null || client.closed, 'browser receives actual upgrade response');
  return client;
}

async function assertRecovery(gateway, upstreamRecord = null) {
  if (upstreamRecord) await until(() => upstreamRecord.closed || gateway.child.exitCode !== null, 'abandoned upstream closes');
  await delay(50);
  assert.equal(gateway.child.exitCode, null, 'same gateway process survives real browser TCP reset:\n' + gateway.output());
  assert.equal(gateway.child.pid, gateway.pid, 'gateway was not replaced or restarted');
  if (upstreamRecord) {
    assert.equal(upstreamRecord.closed, true, 'the abandoned upgraded upstream is closed');
    assert.deepEqual(upstreamRecord.errors, [], 'the real upstream saw valid authored protocol bytes');
  }
  assert.equal((await request(gateway.port, '/healthz')).status, 200);
  const auth = await request(gateway.port, '/auth/status');
  assert.equal(auth.status, 200); assert.equal(JSON.parse(auth.body).authenticated, true);
  assert.equal((await request(gateway.port, '/home-app.jsx')).status, 200, 'authenticated static requests still work');
}

test('real upgraded socket disconnects preserve the same gateway, auth fences and upstream lifetime', { timeout: 30000 }, async t => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'home-upgrade-reset-'));
  let upstream, gateway, client;
  try {
    upstream = await startUpstream(); gateway = await startGateway(temp, upstream);
    const fresh = async (urlPath = '/proxy/ha/api/websocket') => {
      client = await openUpgrade(gateway, urlPath);
      assert.match(client.header, /^HTTP\/1\.1 101 /);
      await until(() => client.buffer.includes(Buffer.from(urlPath.includes('/ha/') ? 'auth_required' : 'authored_tracker_ready')),
        'real upstream greeting reaches the browser through HTTP101');
      return upstream.upgrades.at(-1);
    };
    const browserReset = async record => { client.socket.resetAndDestroy(); await assertRecovery(gateway, record); };

    await t.test('HA browser TCP reset before auth', async () => {
      const record = await fresh(); assert.equal(record.url, '/api/websocket');
      await browserReset(record); assert.deepEqual(record.messages, []);
    });
    assert.equal(gateway.child.exitCode, null, 'stop immediately if the real gateway process crashed');

    await t.test('HA browser TCP reset with a partial first auth frame buffered', async () => {
      const record = await fresh(), auth = maskedFrame(JSON.stringify({ type: 'auth', access_token: TOKEN_MARKER }));
      await new Promise((resolve, reject) => client.socket.write(auth.subarray(0, 9), error => error ? reject(error) : resolve()));
      // A separate completed HTTP round trip lets the actual gateway service
      // its event loop after TCP delivery, without injecting any socket event.
      assert.equal((await request(gateway.port, '/healthz')).status, 200);
      await browserReset(record); assert.deepEqual(record.messages, [], 'an incomplete auth frame never reaches the upstream');
    });

    for (const partial of [false, true]) {
      await t.test('HA clean browser FIN before auth completes' + (partial ? ' with a partial frame buffered' : ''), async () => {
        const record = await fresh();
        if (partial) {
          const auth = maskedFrame(JSON.stringify({ type: 'auth', access_token: TOKEN_MARKER }));
          client.socket.write(auth.subarray(0, 9));
          assert.equal((await request(gateway.port, '/healthz')).status, 200);
        }
        client.socket.end();
        await assertRecovery(gateway, record);
        assert.equal(record.ended, true, 'the actual upstream observes gateway TCP EOF before acknowledging it');
        await until(() => client.closed, 'the half-closed browser socket also closes');
        assert.deepEqual(record.messages, [], 'clean departure cannot forward an unfinished auth frame');
      });
    }

    await t.test('HA split TCP auth rewrites the token, then browser reset after auth stays contained', async () => {
      const record = await fresh(), auth = maskedFrame(JSON.stringify({ type: 'auth', access_token: TOKEN_MARKER }));
      client.socket.write(auth.subarray(0, 3));
      assert.equal((await request(gateway.port, '/healthz')).status, 200);
      client.socket.write(auth.subarray(3));
      await until(() => client.buffer.includes(Buffer.from('auth_ok')), 'split first frame completes actual HA auth');
      assert.equal(JSON.parse(record.messages[0]).access_token, TOKEN, 'gateway rewrites only its authored marker to its private token');
      client.socket.write(maskedFrame('{"type":"ping","id":19}'));
      await until(() => client.buffer.includes(Buffer.from('pong')), 'authenticated frames continue through the real tunnel');
      await browserReset(record);
    });

    await t.test('generic tracker browser TCP reset tears down its paired upstream', async () => {
      const record = await fresh('/proxy/tracker/ws/tracks');
      client.socket.write(maskedFrame('{"type":"ping","id":23}'));
      await until(() => client.buffer.includes(Buffer.from('pong')), 'generic upgraded bytes flow transparently');
      await browserReset(record);
    });

    for (const [name, urlPath, authorization, status, headers] of [
      ['unauthenticated HA', '/proxy/ha/api/websocket', null, 401],
      ['route without websocket permission', '/proxy/metrics/healthz', BASIC, 403],
      ['HA path outside the route allowlist', '/proxy/ha/not-allowed', BASIC, 403],
      ['unknown route', '/proxy/not-a-route/ws', BASIC, 404],
      ['disabled legacy route', '/proxy/vllm/v1/models', BASIC, 404],
      ['dedicated browser Agent origin', '/proxy/tracker/ws/tracks', BASIC, 403,
        { Host: 'agent-authored.example.invalid', Origin: 'https://agent-authored.example.invalid' }],
      ['dedicated native Agent origin', '/proxy/ha/api/websocket', BASIC, 403,
        { Host: 'native-authored.example.invalid', Origin: 'https://native-authored.example.invalid' }],
    ]) {
      await t.test('denied upgrade remains denied: ' + name, async () => {
        const count = upstream.upgrades.length;
        client = await openUpgrade(gateway, urlPath, authorization, headers);
        assert.match(client.header, new RegExp('^HTTP/1\\.1 ' + status + ' '));
        await until(() => client.closed, 'denied upgrade socket closes');
        assert.equal(upstream.upgrades.length, count, 'denied request opens no upstream');
        await assertRecovery(gateway);
      });
    }

    for (const [name, urlPath] of [['HA before auth', '/proxy/ha/api/websocket'], ['generic tracker', '/proxy/tracker/ws/tracks']]) {
      await t.test('actual upstream TCP reset after HTTP101: ' + name, async () => {
        const record = await fresh(urlPath);
        record.socket.resetAndDestroy();
        await until(() => client.closed || gateway.child.exitCode !== null, 'upstream reset closes the browser tunnel');
        assert.equal(client.closed, true, 'upstream reset cannot strand the browser socket');
        assert.equal(client.buffer.includes(Buffer.from('HTTP/1.1 502')), false, 'an established websocket never receives a second HTTP response');
        await assertRecovery(gateway, record);
      });
    }
  } finally {
    client?.socket.destroy();
    if (gateway?.child.exitCode === null) gateway.child.kill();
    if (gateway) await until(() => gateway.child.exitCode !== null || gateway.child.signalCode !== null, 'gateway child terminates', 1500);
    if (upstream) {
      for (const socket of upstream.sockets) socket.destroy();
      upstream.server.closeAllConnections();
      await new Promise(resolve => upstream.server.close(resolve));
    }
    // This uniquely-created temporary root is the only recursive cleanup target.
    assert.equal(path.dirname(temp), os.tmpdir());
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
