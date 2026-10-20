// Actual gateway process, authored private content, and loopback requests only.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { initRegistration, readActiveBinding, REGISTRATION_ROUTE } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

test('real gateway authenticates installed content and exposes registration without enabling writes', { timeout: 45000 }, async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'home-installed-content-'));
  const packs = path.join(root, 'packs'), privateRoot = path.join(root, 'private'), pack = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [{ manifest: pack.manifest, active: true }] }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const layer = path.join(packs, 'victoria', pack.revision + '.lighting'); fs.mkdirSync(layer);
  const hash = bytes => createHash('sha256').update(bytes).digest('hex');
  const count = Number(pack.files['points.ply'].toString().match(/element vertex (\d+)/)[1]);
  const files = {}, fields = { albedo: 3, 'normal-oct': 2, 'basis-u8': 1, 'basis-scale': 2, 'bounce-tint': 3, flags: 1 };
  for (const [field, width] of Object.entries(fields)) {
    const name = 'points-' + field + '.bin', bytes = Buffer.alloc(count * width, 1);
    fs.writeFileSync(path.join(layer, name), bytes); files[name] = { bytes: bytes.length, sha256: hash(bytes) };
  }
  fs.writeFileSync(path.join(layer, 'lighting-manifest.json'), JSON.stringify({ schema: 'home-lighting-layer-1', version: 1,
    site_id: 'victoria', binding: readActiveBinding(packs), provenance: { status: 'modeled', measured: false },
    pack_assets: { 'points.ply': pack.manifest.assets['points.ply'] }, columns: [{ index: 0, kind: 'window' }], files,
    receivers: { points: { source: 'points.ply', source_sha256: pack.manifest.assets['points.ply'].sha256,
      count, column_scale: [1], files: Object.keys(files) } } }));
  const reservation = http.createServer(); reservation.listen(0, '127.0.0.1'); await once(reservation, 'listening');
  const port = reservation.address().port; await new Promise(resolve => reservation.close(resolve));
  const origin = `http://127.0.0.1:${port}`, repo = fileURLToPath(new URL('../', import.meta.url));
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('HOME_WEB_') && key !== 'HOME_CONTENT_PACKS_DIR'));
  const child = spawn(process.execPath, ['web-gateway/server.mjs'], { cwd: repo, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: {
    ...env, NODE_ENV: 'test', HOME_WEB_HOST: '127.0.0.1', HOME_WEB_PORT: String(port),
    HOME_WEB_BASIC_AUTH: 'fixture-owner:fixture-password', HOME_WEB_AUTH_REQUIRED: '1',
    HOME_WEB_AUTH_FILE: path.join(root, 'absent-auth.json'), HOME_WEB_HA_TOKEN_FILE: path.join(root, 'absent-ha-token'),
    HOME_WEB_STACK_TOKEN_FILE: path.join(root, 'absent-stack-token'), HOME_CONTENT_PACKS_DIR: packs,
    HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1', HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin,
    HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot,
    HOME_WEB_AGENT_ORIGINS: 'http://agent.fixture', HOME_WEB_ALLOW_INSECURE_TEST_AGENT_ORIGINS: '1',
  } });
  let diagnostics = ''; const collect = chunk => { diagnostics = (diagnostics + chunk.toString()).slice(-8000); };
  child.stdout.on('data', collect); child.stderr.on('data', collect);
  t.after(async () => {
    child.kill(); await Promise.race([once(child, 'exit').catch(() => {}), new Promise(resolve => setTimeout(resolve, 2000))]);
    fs.rmSync(root, { recursive: true, force: true });
  });
  const startupDeadline = Date.now() + 15000;
  for (;;) {
    try { const health = await fetch(origin + '/healthz', { signal: AbortSignal.timeout(1000) }); if (health.ok) break; } catch {}
    if (Date.now() >= startupDeadline || child.exitCode !== null) assert.fail('authored gateway failed to start: ' + diagnostics);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const auth = { Authorization: 'Basic ' + Buffer.from('fixture-owner:fixture-password').toString('base64') };
  const request = (url, options = {}) => fetch(origin + url, { signal: AbortSignal.timeout(3000), ...options });
  assert.equal((await request('/content-packs/index.json')).status, 401);
  const index = await request('/content-packs/index.json', { headers: auth });
  assert.equal(index.status, 200); assert.equal((await index.json()).packs[0].manifest.revision, pack.revision);
  const asset = `/content-packs/victoria/${pack.revision}/points.ply`;
  assert.deepEqual(Buffer.from(await (await request(asset, { headers: auth })).arrayBuffer()), pack.files['points.ply']);
  assert.equal((await request(asset, { method: 'HEAD', headers: auth })).status, 200);
  assert.equal((await request('/content-packs/index.json', { method: 'POST', headers: auth })).status, 405);
  const registration = await request(REGISTRATION_ROUTE, { headers: auth });
  assert.equal(registration.status, 200); assert.deepEqual((await registration.json()).capabilities, { read: true, write: false });
  assert.equal((await request(REGISTRATION_ROUTE, { method: 'PUT', headers: { ...auth, Origin: origin, 'x-home-equipment-csrf': '1' } })).status, 405);
  assert.equal((await request(REGISTRATION_ROUTE, { headers: { ...auth, Origin: 'http://foreign.test' } })).status, 403);
  const layerBase = `/content-packs/victoria/${pack.revision}.lighting/`;
  assert.equal((await request(layerBase + 'lighting-manifest.json')).status, 401);
  const layerManifest = await request(layerBase + 'lighting-manifest.json', { headers: auth });
  assert.equal(layerManifest.status, 200); assert.match(layerManifest.headers.get('cache-control'), /no-store/);
  const binary = await request(layerBase + 'points-basis-u8.bin', { headers: auth });
  assert.equal(binary.status, 200); assert.equal((await binary.arrayBuffer()).byteLength, count);
  const binaryHead = await request(layerBase + 'points-basis-u8.bin', { method: 'HEAD', headers: auth });
  assert.equal(binaryHead.status, 200); assert.equal((await binaryHead.arrayBuffer()).byteLength, 0);
  assert.equal((await request(layerBase + 'points-basis-u8.bin', { method: 'POST', headers: auth })).status, 405);
  // fetch owns its Host header; use a real loopback HTTP request to exercise
  // the gateway's dedicated-origin branch rather than a silently ignored header.
  const forbiddenHost = await new Promise((resolve, reject) => {
    const probe = http.get(origin + '/content-packs/index.json', { headers: { ...auth, Host: 'agent.fixture' } }, response => {
      const chunks = []; response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks)) }));
    });
    probe.setTimeout(3000, () => probe.destroy(new Error('fixture Host probe timeout'))); probe.on('error', reject);
  });
  assert.equal(forbiddenHost.status, 404); assert.equal(forbiddenHost.body.error, 'agent_origin_route_denied');
  fs.writeFileSync(path.join(layer, 'points-basis-u8.bin'), Buffer.alloc(count, 2));
  assert.equal((await request(layerBase + 'points-basis-u8.bin', { headers: auth })).status, 503);
});
