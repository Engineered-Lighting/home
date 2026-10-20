import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Writable } from 'node:stream';
import Core from '../app/src/home-equipment-registration.js';
import { createContentPackHandler } from '../web-gateway/content-pack-route.mjs';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding, REGISTRATION_ROUTE } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

function capture(handler, url, { method = 'GET', authenticated = true, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const chunks = [], req = { url, method, aborted: false, resume() {},
      headers: { host: 'fixture.test', ...(authenticated ? { cookie: 'fixture-owner=1' } : {}), ...headers } };
    const res = new Writable({ write(chunk, _encoding, next) { chunks.push(Buffer.from(chunk)); next(); } });
    res.writeHead = (status, responseHeaders) => { res.statusCode = status; res.responseHeaders = responseHeaders; res.headersSent = true; };
    res.on('error', reject);
    res.on('finish', () => resolve({ status: res.statusCode, headers: res.responseHeaders, body: Buffer.concat(chunks) }));
    Promise.resolve(handler(req, res)).then(handled => { if (!handled) { res.writeHead(404, {}); res.end(); } }, reject);
  });
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'home-content-route-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const packs = path.join(root, 'packs'), pack = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [{ manifest: pack.manifest, active: true }] }));
  const authenticate = req => req.headers.cookie === 'fixture-owner=1';
  return { root, packs, pack, authenticate, handler: createContentPackHandler({ contentPacksDir: packs, authenticate }) };
}

test('authenticated index and declared assets support GET and HEAD; unrelated/private paths refuse', async t => {
  const f = fixture(t);
  const index = await capture(f.handler, '/content-packs/index.json');
  assert.equal(index.status, 200);
  assert.equal(JSON.parse(index.body).packs[0].manifest.revision, f.pack.revision);
  assert.match(index.headers['Cache-Control'], /private.*no-store/);
  const asset = `/content-packs/victoria/${f.pack.revision}/points.ply`;
  const get = await capture(f.handler, asset), head = await capture(f.handler, asset, { method: 'HEAD' });
  assert.equal(get.status, 200); assert.deepEqual(get.body, f.pack.files['points.ply']);
  assert.equal(head.status, 200); assert.equal(head.body.length, 0);
  assert.equal(head.headers['Content-Length'], get.body.length);
  for (const url of [asset.replace('points.ply', 'undeclared.json'), asset.replace('points.ply', '../index.json'),
    '/content-packs/%2e%2e/private.json', asset.replace('points.ply', '%5cprivate.json'), '/unrelated']) {
    assert.equal((await capture(f.handler, url)).status, 404, url);
  }
});

test('content reads require authentication, reject methods/bodies and preserve on-disk bytes', async t => {
  const f = fixture(t), file = path.join(f.packs, 'index.json'), before = fs.readFileSync(file);
  assert.equal((await capture(f.handler, '/content-packs/index.json', { authenticated: false })).status, 401);
  for (const method of ['POST', 'PUT', 'DELETE']) assert.equal((await capture(f.handler, '/content-packs/index.json', { method })).status, 405);
  assert.equal((await capture(f.handler, '/content-packs/index.json', { headers: { 'content-length': '12' } })).status, 400);
  assert.deepEqual(fs.readFileSync(file), before);
});

test('the deployed registration adapter advertises read-only and cannot save even with valid auth and CSRF', async t => {
  const f = fixture(t), privateRoot = path.join(f.root, 'private');
  initRegistration(privateRoot, authoredDocument(readActiveBinding(f.packs), Core), { contentPacksDir: f.packs });
  const handler = createEquipmentRegistrationGateway({ authenticate: f.authenticate, readOnly: true, env: {
    NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1', HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: 'http://fixture.test',
    HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_CONTENT_PACKS_DIR: f.packs,
  } });
  const current = await capture(handler, REGISTRATION_ROUTE), body = JSON.parse(current.body);
  assert.equal(current.status, 200); assert.deepEqual(body.capabilities, { read: true, write: false });
  assert.equal((await capture(handler, REGISTRATION_ROUTE, { method: 'PUT', headers: { origin: 'http://fixture.test',
    'x-home-equipment-csrf': '1', 'content-type': 'application/json' } })).status, 405);
  assert.equal((await capture(handler, REGISTRATION_ROUTE, { authenticated: false })).status, 401);
  assert.equal((await capture(handler, REGISTRATION_ROUTE, { headers: { origin: 'http://foreign.test' } })).status, 403);
  assert.deepEqual(JSON.parse((await capture(handler, REGISTRATION_ROUTE)).body).registration, body.registration);
});
