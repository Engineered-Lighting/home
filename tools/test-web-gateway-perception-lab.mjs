// Authored fake endpoints only. No household camera, credential, TLS trust or
// live-host acceptance is established by this local HTTP test transport.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createPerceptionLabReadHandler, describeLabRead, PERCEPTION_LAB_PREFIX as P } from '../web-gateway/perception-lab-route.mjs';

async function listen(server) { server.listen(0, '127.0.0.1'); await once(server, 'listening'); return server.address().port; }
async function until(predicate) {
  const deadline = Date.now() + 1000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'authored request did not arrive inside its fixture budget');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
const send = (res, value = { ok: true }) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
async function fixture(t, serve = (_req, res) => send(res), options = {}) {
  const calls = [], upstream = http.createServer(serve), upstreamPort = await listen(upstream);
  let authenticated = true;
  const handler = createPerceptionLabReadHandler({ origin: 'https://lab.fixture:8789', ca: 'authored-test-ca',
    credential: () => 'fixture-read-grant-'.padEnd(40, 'x'),
    authenticate: req => authenticated && req.headers.cookie === 'fixture-owner=1',
    request(args, callback) {
      calls.push(args);
      return http.request({ host: '127.0.0.1', port: upstreamPort, method: args.method, path: args.path, headers: args.headers }, callback);
    }, ...options });
  const gateway = http.createServer((req, res) => { if (!handler(req, res)) { res.writeHead(404); res.end(); } });
  const port = await listen(gateway), origin = 'http://127.0.0.1:' + port;
  t.after(async () => {
    for (const server of [gateway, upstream]) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  });
  return { calls, origin, revoke() { authenticated = false; },
    get: (suffix, extra = {}) => fetch(origin + P + suffix, { signal: AbortSignal.timeout(3000), ...extra,
      headers: { Cookie: 'fixture-owner=1', 'X-Home-Lab-Site': 'victoria', ...(extra.headers || {}) } }) };
}

test('allowlist accepts only bounded current-view reads with exact query fields', () => {
  for (const suffix of ['/capabilities', '/activity-loop', '/sessions', '/cameras/den/preview', '/cameras/den/context',
    '/cameras/den/structured-context', '/cameras/den/video?after_sequence=0&generation=' + 'a'.repeat(32),
    '/sessions/s/live', '/sessions/s?at_ms=0', '/sessions/s/graph', '/sessions/s/events?after_id=0&limit=200',
    '/sessions/s/findings', '/sessions/s/timeline?from_ms=0&to_ms=1800000', '/sessions/s/replay?at_ms=0',
    '/sessions/s/media?kind=video&encoded_at_ms=100&radius_ms=1000', '/sessions/s/media/m/descriptor?generation=0']) {
    assert.ok(describeLabRead(P + suffix), suffix);
  }
  assert.ok(describeLabRead(P + '/sessions/s/media/m?generation=0', 'bytes=0-4194303'));
  for (const suffix of ['/activity-loop/resume', '/activity-loop/cadence', '/sessions/s/stop', '/sessions/s/switch',
    '/jobs', '/sessions/s/export', '/cameras/other/preview', '/sessions/../capabilities', '/sessions/%73/live',
    '/capabilities?origin=https://foreign.test', '/sessions/s/live?unknown=1', '/sessions/s?at_ms=0&at_ms=1',
    '/sessions/s?at_ms=1800001', '/sessions/s/media?at_ms=0&encoded_at_ms=0', '/sessions/s/media?encoded_at_ms=0',
    '/sessions/s/findings?unknown=1', '/sessions/s/replay', '/sessions/s/replay?at_ms=0&at_ms=1',
    '/sessions/s/timeline?from_ms=2&to_ms=1', '/sessions/s/timeline?to_ms=1800001',
    '/sessions/s/events?limit=201', '/sessions/s/events?after_id=9007199254740992',
    '/sessions/s/media/m/descriptor', '/sessions/s/media/m?generation=-1', '/capabilities#fragment', '/capabilities??']) {
    assert.equal(describeLabRead(P + suffix), null, suffix);
  }
  for (const range of [undefined, 'bytes=0-', 'bytes=0-4194304', 'bytes=5-4', 'bytes=0-1,3-4', 'bytes=9007199254740992-9007199254740993']) {
    assert.equal(describeLabRead(P + '/sessions/s/media/m?generation=0', range), null, range);
  }
  assert.equal(describeLabRead(P + '/capabilities', 'bytes=0-1'), null);
});

test('saved-session findings, timeline and replay use bounded existing read contracts', async t => {
  const f = await fixture(t);
  for (const suffix of ['/sessions/s/findings', '/sessions/s/timeline?from_ms=0&to_ms=1000', '/sessions/s/replay?at_ms=0']) {
    const route = describeLabRead(P + suffix); assert.equal(route.maximum, 1024 * 1024);
    const response = await f.get(suffix); assert.equal(response.status, 200); await response.arrayBuffer();
    assert.equal(f.calls.at(-1).path, P + suffix); assert.equal(f.calls.at(-1).method, 'GET');
  }
});

test('real gateway harness uses pinned TLS settings and strips caller credentials/headers', async t => {
  const f = await fixture(t, (req, res) => {
    assert.equal(req.headers.authorization, 'Bearer ' + 'fixture-read-grant-'.padEnd(40, 'x'));
    assert.equal(req.headers['x-home-lab-site'], 'victoria');
    assert.equal(req.headers.cookie, undefined); assert.equal(req.headers['x-forwarded-host'], undefined);
    send(res, { state: 'running', deep_stack: { session_id: 's', producer_epoch: 'epoch' } });
  });
  const result = await f.get('/activity-loop', { headers: { Authorization: 'Basic caller-must-not-forward', 'X-Forwarded-Host': 'foreign.test' } });
  assert.equal(result.status, 200); assert.equal((await result.json()).deep_stack.session_id, 's');
  assert.equal(result.headers.get('x-home-lab-access'), 'read-only');
  assert.match(result.headers.get('cache-control'), /no-store/); assert.equal(result.headers.get('set-cookie'), null);
  const request = f.calls[0]; assert.equal(request.hostname, 'lab.fixture'); assert.equal(request.protocol, 'https:');
  assert.equal(request.rejectUnauthorized, true); assert.equal(request.ca, 'authored-test-ca'); assert.equal(request.agent, false);
});

test('unauthenticated/wrong-site requests and every write refuse before upstream execution', async t => {
  const f = await fixture(t);
  assert.equal((await f.get('/capabilities', { headers: { Cookie: '' } })).status, 401);
  assert.equal((await f.get('/capabilities', { headers: { 'X-Home-Lab-Site': 'echo' } })).status, 403);
  for (const method of ['POST', 'PUT', 'DELETE', 'PATCH', 'HEAD']) {
    const refusal = await f.get('/sessions', { method });
    assert.equal(refusal.status, 405);
    assert.equal(refusal.headers.get('x-home-lab-access'), 'read-only');
  }
  assert.equal((await f.get('/activity-loop/resume')).status, 404);
  assert.equal(f.calls.length, 0);
});

test('GET bodies are refused without forwarding any bytes upstream', async t => {
  const f = await fixture(t);
  const result = await new Promise((resolve, reject) => {
    const req = http.request(f.origin + P + '/capabilities', { method: 'GET', headers: {
      Cookie: 'fixture-owner=1', 'X-Home-Lab-Site': 'victoria', 'Content-Length': '1',
    } }, res => { res.resume(); res.once('end', () => resolve(res.statusCode)); });
    req.once('error', reject); req.end('x');
  });
  assert.equal(result, 400); assert.equal(f.calls.length, 0);
});

test('unconfigured or invalid server origin, CA and read credential never open upstream', async t => {
  for (const options of [{ origin: undefined }, { origin: 'http://lab.fixture' }, { origin: 'https://secret@lab.fixture' },
    { origin: 'https://lab.fixture/path' }, { origin: 'https://lab.fixture/?url=foreign' }, { ca: '' },
    { credential: () => 'bad\r\nsecret' }, { credential: () => { throw Error('private-path-must-not-appear'); } }]) {
    const f = await fixture(t, undefined, options), result = await f.get('/capabilities');
    assert.equal(result.status, 503); assert.equal(f.calls.length, 0);
    assert.doesNotMatch(await result.text(), /secret|private-path/);
  }
  assert.throws(() => createPerceptionLabReadHandler({ maxConcurrent: 5 }), /limits/);
  assert.throws(() => createPerceptionLabReadHandler({ timeoutMs: 5001 }), /limits/);
});

test('upstream HTML, redirects, compression and authorization errors cannot become a working Lab route', async t => {
  for (const [status, headers, body, expected] of [
    [200, { 'Content-Type': 'text/html' }, '<html>fallback</html>', 502],
    [302, { Location: 'https://foreign.test' }, '', 502],
    [200, { 'Content-Type': 'application/json', 'Content-Encoding': 'gzip' }, '{}', 502],
    [200, { 'Content-Type': 'application/json' }, 'not-json', 502],
    [200, { 'Content-Type': 'application/json' }, '[]', 502],
    [401, { 'Content-Type': 'application/json' }, '{"error":"upstream-grant"}', 503],
  ]) {
    const f = await fixture(t, (_req, res) => { res.writeHead(status, headers); res.end(body); });
    const result = await f.get('/capabilities'); assert.equal(result.status, expected);
    assert.equal(f.calls.length, 1); assert.doesNotMatch(await result.text(), /foreign|upstream-grant|fallback/);
  }
});

test('upstream unavailable/refusal JSON is retained without response header tunnels', async t => {
  const f = await fixture(t, (_req, res) => {
    res.writeHead(503, { 'Content-Type': 'application/json', 'Set-Cookie': 'must-not-forward', 'Access-Control-Allow-Origin': '*' });
    res.end('{"error":{"code":"preview_not_configured"}}');
  });
  const result = await f.get('/cameras/den/preview'); assert.equal(result.status, 503);
  assert.equal((await result.json()).error.code, 'preview_not_configured');
  assert.equal(result.headers.get('set-cookie'), null); assert.equal(result.headers.get('access-control-allow-origin'), null);
});

test('media reads require a bounded matching range and preserve successful bytes', async t => {
  const f = await fixture(t, (req, res) => {
    assert.equal(req.headers.range, 'bytes=0-3');
    res.writeHead(206, { 'Content-Type': 'image/jpeg', 'Content-Range': 'bytes 0-3/100', 'Content-Length': 4 }); res.end('jpeg');
  });
  const result = await f.get('/sessions/s/media/m?generation=0', { headers: { Range: 'bytes=0-3' } });
  assert.equal(result.status, 206); assert.equal(await result.text(), 'jpeg'); assert.equal(result.headers.get('content-range'), 'bytes 0-3/100');
  for (const [status, range, type] of [[200, 'bytes 0-3/100', 'image/jpeg'], [206, 'bytes 1-4/100', 'image/jpeg'],
    [206, 'bytes 0-4/100', 'image/jpeg'], [206, 'bytes 0-3/3', 'image/jpeg'], [206, 'bytes 0-3/100', 'text/html']]) {
    const bad = await fixture(t, (_req, res) => { res.writeHead(status, { 'Content-Type': type, 'Content-Range': range }); res.end('jpeg'); });
    assert.equal((await bad.get('/sessions/s/media/m?generation=0', { headers: { Range: 'bytes=0-3' } })).status, 502);
  }
});

test('declared and streaming graph response limits prevent large buffered replies', async t => {
  for (const declared of [true, false]) {
    const f = await fixture(t, (_req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json', ...(declared ? { 'Content-Length': 1024 * 1024 + 1 } : {}) });
      res.end(Buffer.alloc(1024 * 1024 + 1, 32));
    });
    const result = await f.get('/sessions/s/graph'); assert.equal(result.status, 502);
    assert.equal((await result.json()).error.code, 'lab_upstream_response_too_large');
  }
});

test('revoked browser authorization withdraws an already completed upstream result', async t => {
  let release; const ready = new Promise(resolve => { release = resolve; });
  const f = await fixture(t, async (_req, res) => { await ready; send(res, { private: 'never-return' }); });
  const pending = f.get('/sessions/s/live');
  await until(() => f.calls.length);
  f.revoke(); release(); const result = await pending;
  assert.equal(result.status, 401); assert.doesNotMatch(await result.text(), /never-return/);
});

test('concurrency and deadlines are bounded and timed-out reservations release', async t => {
  const f = await fixture(t, () => {}, { timeoutMs: 100, maxConcurrent: 1 });
  const first = f.get('/sessions/s/live');
  await until(() => f.calls.length);
  assert.equal((await f.get('/sessions/s/graph')).status, 429);
  assert.equal((await first).status, 504);
  assert.equal((await f.get('/sessions/s/live')).status, 504);
  assert.equal(f.calls.length, 2);
});

test('client disconnect retires the reservation and permits a later observation', async t => {
  let seen = 0;
  const f = await fixture(t, (_req, res) => { if (++seen > 1) send(res); }, { maxConcurrent: 1 });
  const controller = new AbortController(), pending = f.get('/sessions/s/live', { signal: controller.signal }).catch(error => error);
  await until(() => f.calls.length);
  controller.abort(); await pending;
  let response;
  for (let i = 0; i < 30; i++) {
    response = await f.get('/capabilities'); if (response.status !== 429) break;
    await response.arrayBuffer(); await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(response.status, 200); await response.arrayBuffer();
});

test('interrupted upstream bodies cannot become partial successful JSON', async t => {
  const f = await fixture(t, (_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': 100 });
    res.write('{"private":"unfinished'); setImmediate(() => res.destroy());
  });
  const result = await f.get('/sessions/s/live'); assert.equal(result.status, 502);
  assert.doesNotMatch(await result.text(), /unfinished/);
});
