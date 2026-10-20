// Authored Home shell server for read-only audits. It serves this worktree's
// public app sources and two authored packs; it never reads household assets,
// starts a session, opens a camera or forwards a command anywhere.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';

function staticSources(root) {
  const sources = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs', 'precompiled']);
  let entries = 0;
  (function walk(directory, depth) {
    assert.ok(depth <= 12, 'static source depth is bounded');
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      assert.ok(++entries <= 10000, 'static source inventory is bounded');
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/');
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/')) continue;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { walk(file, depth + 1); continue; }
      const trusted = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|png|svg|ico|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !trusted && !rootAsset) continue;
      sources.set('/' + relative, file);
    }
  })(root, 0);
  return sources;
}

const TYPES = [[/\.(?:js|mjs|jsx)$/, 'text/javascript'], [/\.html$/, 'text/html'], [/\.css$/, 'text/css'],
  [/\.(?:json|webmanifest)$/, 'application/json'], [/\.svg$/, 'image/svg+xml'], [/\.woff2$/, 'font/woff2']];

export async function serveAuthoredShell(run, srcRoot) {
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const sources = staticSources(srcRoot), requests = [], forbidden = [], missing = [];
  let handler;
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      requests.push({ path: url.pathname, method: req.method });
      if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
        forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
      }
      if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
      let body = null, type = 'application/octet-stream';
      if (url.pathname.startsWith('/api/perception-lab/v1/')) {
        const route = url.pathname.slice('/api/perception-lab/v1/'.length);
        const value = route === 'capabilities' ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {},
          recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
          : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
        if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
      }
      if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
      else if (!body) {
        const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
        if (pack) { body = pack.files[url.pathname.slice(pack.base.length)] || null; if (url.pathname.endsWith('.json')) type = 'application/json'; }
      }
      if (!body && url.pathname.startsWith('/proxy/')) {
        const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
          : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
        body = Buffer.from(JSON.stringify(value)); type = 'application/json';
      }
      const file = !body && sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
      if (file) { body = fs.readFileSync(file); type = TYPES.find(([pattern]) => pattern.test(file))?.[1] || type; }
      if (!body) { missing.push({ path: url.pathname, method: req.method }); res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' });
      res.end(req.method === 'HEAD' ? undefined : body);
    } catch (error) { res.writeHead(500); res.end(); throw error; }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
    HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
    authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
  return { server, origin, requests, forbidden, missing };
}
