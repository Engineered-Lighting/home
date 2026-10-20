// Authored L3 sidecars only: no household data, credentials or live services.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { installPack } from './fixtures/apartment-home-shell-fixtures.mjs';
import { readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { createLightingLayerHandler } from '../web-gateway/lighting-layer.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const FIELDS = { albedo: 3, 'normal-oct': 2, 'basis-u8': 29, 'basis-scale': 2, 'bounce-tint': 3, flags: 1 };

async function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'home-lighting-layer-test-'));
  const packs = path.join(root, 'packs'), pack = installPack(packs, 'victoria');
  const directory = path.join(packs, pack.site, pack.revision);
  pack.files['apartment.mobile.ply'] = pack.files['apartment.ply'];
  pack.manifest.assets['apartment.mobile.ply'] = { path: 'apartment.mobile.ply', bytes: pack.files['apartment.ply'].length,
    sha256: hash(pack.files['apartment.ply']) };
  fs.writeFileSync(path.join(directory, 'apartment.mobile.ply'), pack.files['apartment.mobile.ply']);
  fs.writeFileSync(path.join(directory, 'content-pack.json'), JSON.stringify(pack.manifest));
  const index = { schemaVersion: 1, packs: [{ manifest: pack.manifest, active: true }] };
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify(index));
  const layer = directory + '.lighting'; fs.mkdirSync(layer);
  const manifest = { schema: 'home-lighting-layer-1', version: 1, site_id: 'victoria', binding: readActiveBinding(packs),
    provenance: { status: 'modeled', measured: false, photometry: 'assumed' },
    pack_assets: Object.fromEntries(['apartment.ply', 'apartment.mobile.ply', 'points.ply', 'mesh.glb'].map(name =>
      [name, { bytes: pack.manifest.assets[name].bytes, sha256: pack.manifest.assets[name].sha256 }])),
    columns: Array.from({ length: 29 }, (_, i) => ({ index: i, kind: i === 28 ? 'window' : i < 4 ? 'downward' : 'radial' })),
    receivers: {}, files: {} };
  const expected = {};
  for (const [set, source] of Object.entries({ splat: 'apartment.ply', 'splat-mobile': 'apartment.mobile.ply', points: 'points.ply' })) {
    const count = Number(pack.files[source].subarray(0, 100).toString().match(/element vertex (\d+)/)[1]), names = [];
    for (const [field, width] of Object.entries(FIELDS)) {
      const name = set + '-' + field + '.bin', bytes = Buffer.alloc(count * width, field === 'flags' ? 0 : 127);
      names.push(name); expected[name] = bytes;
      fs.writeFileSync(path.join(layer, name), bytes);
      manifest.files[name] = { bytes: bytes.length, sha256: hash(bytes), keyed_by: 'vertex index in the source PLY' };
    }
    manifest.receivers[set] = { source, source_sha256: pack.manifest.assets[source].sha256, count,
      column_scale: Array(29).fill(1), files: names };
  }
  // L3 also writes mesh arrays; this receiver endpoint must not expose them.
  fs.writeFileSync(path.join(layer, 'mesh-basis-u8.bin'), 'mesh-not-served');
  manifest.files['mesh-basis-u8.bin'] = { bytes: 15, sha256: hash('mesh-not-served') };
  const save = () => fs.writeFileSync(path.join(layer, 'lighting-manifest.json'), JSON.stringify(manifest)); save();
  const handler = createLightingLayerHandler({ contentPacksDir: packs, authenticate: req => req.headers.cookie === 'fixture-owner=1' });
  const base = '/content-packs/victoria/' + pack.revision + '.lighting/';
  // Call the actual HTTP handler without a socket; this runs in network-denied
  // workstations too. Files, manifest binding and SHA checks remain real.
  const request = (name, { method = 'GET', authenticated = true, headers = {}, routeHandler = handler } = {}) => new Promise(resolve => {
    const req = { url: name.startsWith('/') ? name : base + name, method, aborted: false, resume() {},
      headers: { ...(authenticated ? { cookie: 'fixture-owner=1' } : {}), ...headers } };
    const response = { status: null, headers: {}, body: null };
    const res = { destroyed: false, writableEnded: false,
      writeHead(status, headers = {}) {
        response.status = status;
        response.headers = Object.fromEntries(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]));
      },
      end(body) { this.writableEnded = true; response.body = Buffer.from(body || ''); resolve(response); },
    };
    if (!routeHandler(req, res)) { res.writeHead(404); res.end(); }
  });
  t.after(() => {
    assert.equal(path.dirname(root), fs.realpathSync(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('home-lighting-layer-test-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { root, packs, pack, directory, layer, manifest, expected, save, base, request, handler };
}

test('serves the bound L3 manifest and all receiver fields with private GET/HEAD responses', async t => {
  const f = await fixture(t);
  const before = Object.fromEntries(fs.readdirSync(f.directory).map(name => [name, hash(fs.readFileSync(path.join(f.directory, name)))]));
  const result = await f.request('lighting-manifest.json');
  assert.equal(result.status, 200);
  assert.deepEqual(JSON.parse(result.body), f.manifest);
  assert.match(result.headers['cache-control'], /private.*no-store/);
  assert.equal(result.headers['cross-origin-resource-policy'], 'same-origin');
  assert.equal(result.headers['x-content-type-options'], 'nosniff');
  for (const [name, bytes] of Object.entries(f.expected)) {
    const get = await f.request(name), head = await f.request(name, { method: 'HEAD' });
    assert.equal(get.status, 200, name); assert.deepEqual(get.body, bytes);
    assert.equal(get.headers.etag, '"' + hash(bytes) + '"');
    assert.equal(head.status, 200, name); assert.equal(head.body.length, 0);
    assert.equal(Number(head.headers['content-length']), bytes.length);
  }
  for (const [name, digest] of Object.entries(before)) assert.equal(hash(fs.readFileSync(path.join(f.directory, name))), digest);
});

test('requires explicit authentication, refuses writes and ignores unrelated routes', async t => {
  const f = await fixture(t);
  assert.equal((await f.request('lighting-manifest.json', { authenticated: false })).status, 401);
  const deny = createLightingLayerHandler({ contentPacksDir: f.packs });
  const reply = { writeHead(status) { this.status = status; }, end() {} };
  assert.equal(deny({ url: f.base + 'lighting-manifest.json', method: 'GET', headers: {} }, reply), true);
  assert.equal(reply.status, 401);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
    const result = await f.request('points-albedo.bin', { method });
    assert.equal(result.status, 405, method); assert.equal(result.headers.allow, 'GET, HEAD');
  }
  assert.equal((await f.request('points-albedo.bin', { headers: { 'content-length': '1' } })).status, 400);
  assert.equal(f.handler({ url: '/content-packs/index.json' }, {}), false);
  assert.equal(f.handler({ url: '/content-packs/victoria/' + f.pack.revision + '/points.ply' }, {}), false);
});

test('refuses non-active packs, arbitrary files and encoded path escapes', async t => {
  const f = await fixture(t);
  fs.writeFileSync(path.join(f.layer, 'secret.bin'), 'not-served');
  for (const url of [f.base + 'secret.bin', f.base + 'mesh-basis-u8.bin', f.base + 'points-albedo.bin/extra',
    f.base + '%2e%2e/secret.bin', f.base + 'points%2falbedo.bin', f.base + 'points%5calbedo.bin', f.base + 'points-albedo.bin:stream',
    f.base + '%70oints-albedo.bin', f.base + 'lighting-manifest.json?file=secret.bin',
    f.base.replace('/victoria/', '/echo/') + 'lighting-manifest.json',
    f.base.replace(f.pack.revision, 'retired-pack') + 'lighting-manifest.json']) {
    assert.equal((await f.request(url)).status, 404, url);
  }
  fs.writeFileSync(path.join(f.packs, 'victoria', 'ACTIVE'), 'another-pack\n');
  assert.equal((await f.request('points-albedo.bin')).status, 404);
});

test('refuses changed binding, source hashes, schema, declarations and file bytes', async t => {
  const f = await fixture(t), baseline = structuredClone(f.manifest);
  for (const edit of [
    m => { m.binding.frame_digest = '0'.repeat(64); },
    m => { m.pack_assets['points.ply'].sha256 = '0'.repeat(64); },
    m => { m.receivers.points.source_sha256 = '0'.repeat(64); },
    m => { m.schema = 'unknown'; }, m => { m.provenance.measured = true; },
    m => { m.receivers.points.files = []; }, m => { delete m.files['points-albedo.bin']; },
    m => { m.files['points-albedo.bin'].bytes++; }, m => { m.receivers.points.count = 16 * 1024 * 1024 + 1; },
  ]) {
    Object.assign(f.manifest, structuredClone(baseline)); edit(f.manifest); f.save();
    assert.equal((await f.request('points-albedo.bin')).status, 503);
  }
  Object.assign(f.manifest, baseline); f.save();
  const filename = path.join(f.layer, 'points-albedo.bin'), changed = Buffer.from(f.expected['points-albedo.bin']); changed[0] ^= 1;
  fs.writeFileSync(filename, changed);
  const result = await f.request('points-albedo.bin');
  assert.equal(result.status, 503); assert.ok(!result.body.includes(changed));
  assert.equal((await f.request('points-albedo.bin', { method: 'HEAD' })).status, 503);
  fs.writeFileSync(filename, f.expected['points-albedo.bin']);
  fs.writeFileSync(filename, f.expected['points-albedo.bin'].subarray(1));
  assert.equal((await f.request('points-albedo.bin')).status, 503);
});

test('bounds manifest reads without revealing filesystem paths', async t => {
  const f = await fixture(t), manifestFile = path.join(f.layer, 'lighting-manifest.json');
  fs.writeFileSync(manifestFile, Buffer.alloc(4 * 1024 * 1024 + 1, 32));
  const result = await f.request('lighting-manifest.json');
  assert.equal(result.status, 503); assert.ok(!result.body.toString().includes(f.root));
  f.save();
  f.manifest.receivers.points.count = 3 * 1024 * 1024;
  for (const [field, width] of Object.entries(FIELDS)) f.manifest.files['points-' + field + '.bin'].bytes = 3 * 1024 * 1024 * width;
  f.save();
  assert.equal((await f.request('lighting-manifest.json')).status, 503, 'a field exceeding the buffered-response bound is refused');
});

test('refuses a hard-linked receiver file', async t => {
  const f = await fixture(t);
  const filename = path.join(f.layer, 'points-albedo.bin'), link = path.join(f.root, 'same-file.bin');
  try { fs.linkSync(filename, link); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('filesystem does not permit creating test hard links'); throw error; }
  assert.equal((await f.request('points-albedo.bin')).status, 503); fs.unlinkSync(link);
});

test('refuses a linked layer directory', async t => {
  const f = await fixture(t);
  const moved = f.layer + '-target'; fs.mkdirSync(moved);
  assert.equal(path.dirname(f.layer), path.join(f.root, 'packs', 'victoria'));
  fs.rmSync(f.layer, { recursive: true, force: true });
  try { fs.symlinkSync(moved, f.layer, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('filesystem does not permit creating test directory links'); throw error; }
  assert.equal((await f.request('lighting-manifest.json')).status, 503);
});

test('refuses a linked receiver file', async t => {
  const f = await fixture(t), filename = path.join(f.layer, 'points-albedo.bin');
  fs.unlinkSync(filename);
  try { fs.symlinkSync(path.join(f.layer, 'splat-albedo.bin'), filename, 'file'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('filesystem does not permit creating test file links'); throw error; }
  assert.equal((await f.request('points-albedo.bin')).status, 503);
});

test('bounds concurrent reads and checks authorization again before returning private bytes', async t => {
  const f = await fixture(t);
  const requests = await Promise.all([f.request('points-albedo.bin'), f.request('splat-albedo.bin'), f.request('splat-mobile-albedo.bin')]);
  assert.deepEqual(requests.map(result => result.status), [200, 200, 429]);
  let authorizations = 0;
  const revoked = createLightingLayerHandler({ contentPacksDir: f.packs, authenticate: () => ++authorizations === 1 });
  const result = await f.request('points-albedo.bin', { routeHandler: revoked });
  assert.equal(result.status, 401); assert.equal(authorizations, 2);
  assert.ok(!result.body.equals(f.expected['points-albedo.bin']));
});
