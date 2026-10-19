// Authored-room browser proof for Tier-0 Lit (APT-V01, SPEC Lit view) on the
// real renderer: relit mesh, disclosure, orbs untouched, exact restore.
// Real renderer and shell over the authored fixture packs; no household pack,
// live camera, model session, physical command, screenshot or deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// Software GL compiles the 32-light lit shader slowly; these waits cover that.
const LIT_WAIT_MS = 45000;
// The touched-harness runner keeps only the last lines of output, so a
// failure is repeated after the test summary.
let failureSummary = null;
process.on('exit', () => { if (failureSummary) console.log('Lit browser failure: ' + failureSummary); });

function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  assert.ok(fs.lstatSync(root).isDirectory() && !fs.lstatSync(root).isSymbolicLink());
  let count = 0, bytes = 0;
  (function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      assert.ok(++count <= 10000);
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/'), stat = fs.lstatSync(file);
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/') || stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { visit(file, depth + 1); continue; }
      if (!stat.isFile()) continue;
      const vendor = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !vendor && !rootAsset) continue;
      bytes += stat.size; assert.ok(stat.size <= 32 * 1024 * 1024 && bytes <= 256 * 1024 * 1024);
      result.set('/' + relative, { file, relative });
    }
  })(root);
  return result;
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

test('Lit relights the mesh with assumed output, keeps the orbs legible, discloses it and restores exactly', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'this proof uses only current worktree source');
  const run = path.join(ROOT, '.tmp/apartment-lit-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-lit-authored-browser-1', authored_room: true, real_renderer: true,
    household_data: false, physical_commands: 0, screenshots: false, checks: [], forbidden: [], missing_code: [], errors: [], served: {} };
  const check = (stage, detail = {}) => receipt.checks.push({ stage, ...detail });
  let server, browser, page, origin, handler, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('120s lit browser deadline'));
  }, 120000); });

  async function proof() {
    server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost');
        if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
          receipt.forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
        }
        if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
        let body, type = 'application/octet-stream';
        if (url.pathname.startsWith('/api/perception-lab/v1/')) {
          const route = url.pathname.slice('/api/perception-lab/v1/'.length), value = route === 'capabilities'
            ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
            : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
          if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
        }
        if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
        else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base)); if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; } }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry) {
          const relative = path.relative(fs.realpathSync(SRC), fs.realpathSync(entry.file)).split(path.sep).join('/');
          assert.equal(relative, entry.relative); assert.ok(!relative.startsWith('../') && !path.isAbsolute(relative));
          body = fs.readFileSync(entry.file); receipt.served[relative] = hash(body);
          type = /\.(?:js|mjs|jsx)$/.test(entry.file) ? 'text/javascript' : entry.file.endsWith('.html') ? 'text/html' : entry.file.endsWith('.css') ? 'text/css'
            : /\.(?:json|webmanifest)$/.test(entry.file) ? 'application/json' : entry.file.endsWith('.svg') ? 'image/svg+xml' : type;
        }
        if (!body) { if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) receipt.missing_code.push(url.pathname); res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) { receipt.errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
    handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
      authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
    browser = await chromium.launch(browserOptions());
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'victoria'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        const probe = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && route.request().method() === 'GET' && url.pathname === '/healthz';
        if (!probe) receipt.forbidden.push({ origin: url.origin, path: url.pathname, external: true });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => receipt.errors.push(error.message));

    const frames = (n = 3) => page.evaluate(n => new Promise(resolve => { let k = 0; const step = () => (++k >= n ? resolve() : requestAnimationFrame(step)); requestAnimationFrame(step); }), n);
    const meanLuma = () => page.evaluate(async () => {
      const frame = await window.__APT_ENGINE.captureFrame({ scale: .25 });
      if (!frame) return null;
      let sum = 0; const n = frame.width * frame.height;
      for (let i = 0; i < n; i++) sum += .2126 * frame.pixels[i * 4] + .7152 * frame.pixels[i * 4 + 1] + .0722 * frame.pixels[i * 4 + 2];
      return sum / n;
    });
    // CPU submission time of renderer.render and rAF intervals (descriptive only).
    const timing = () => page.evaluate(() => new Promise(resolve => {
      const engine = window.__APT_ENGINE, renderer = engine.renderer, original = renderer.render, renders = [], intervals = [];
      renderer.render = function (...args) { const t0 = performance.now(); try { return original.apply(this, args); } finally { renders.push(performance.now() - t0); } };
      let last = null, count = 0;
      const step = now => {
        if (last != null) intervals.push(now - last); last = now;
        if (++count < 121) requestAnimationFrame(step);
        else { renderer.render = original; const p = (a, q) => [...a].sort((x, y) => x - y)[Math.floor(a.length * q)];
          resolve({ frames: intervals.length, frame_p50_ms: p(intervals, .5), frame_p95_ms: p(intervals, .95), render_p50_ms: p(renders, .5), render_p95_ms: p(renders, .95) }); }
      };
      requestAnimationFrame(step);
    }));

    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    await page.waitForFunction(() => window.__APT_ENGINE?.siteId === 'victoria'
      && window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.length === 4, null, { timeout: 45000 });
    const litButton = page.getByRole('button', { name: 'Lit view', exact: true });
    await litButton.waitFor();
    assert.equal(await litButton.isDisabled(), false, 'Lit is offered in a home with modeled fixtures');
    assert.equal(await litButton.getAttribute('aria-pressed'), 'false', 'Lit is off by default');

    // Mesh unlit: the baseline.
    await page.getByRole('button', { name: 'Mesh view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'mesh' && !!window.__APT_ENGINE.modes.getMesh(), null, { timeout: LIT_WAIT_MS });
    await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await frames(6);
    const before = await page.evaluate(() => {
      const engine = window.__APT_ENGINE, rows = [];
      engine.modes.getMesh().traverse(node => { if (node.isMesh) rows.push([node.material.uuid, Object.keys(node.geometry.attributes).sort().join(',')]); });
      const orbs = []; engine.overlay.virtualFixturesGroup.traverse(node => { if (node.material) orbs.push([node.name, node.material.uuid, node.material.toneMapped]); });
      return { meshMaterials: rows, orbs, toneMapping: engine.renderer.toneMapping, shadows: engine.renderer.shadowMap.enabled };
    });
    const unlitLuma = await meanLuma(), unlitTiming = await timing();

    // Lit on.
    await litButton.click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState?.()?.active === true, null, { timeout: LIT_WAIT_MS });
    await frames(6);
    assert.equal(await litButton.getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByRole('button', { name: 'Mesh view', exact: true }).getAttribute('aria-pressed'), 'false', 'Lit, not Mesh, reads as the current view');
    const note = page.locator('[data-apt-lit-disclosure]');
    assert.equal((await note.textContent()).trim(), 'Simulated · not photometric · assumed output');
    assert.match(await note.getAttribute('title'), /Assumed output, not measured: spot 450 lm, down 350 lm, each radial zone 120 lm/);
    const lit = await page.evaluate(() => {
      const engine = window.__APT_ENGINE, state = engine.litState(), lights = [];
      engine.scene.traverse(node => { if (node.isSpotLight && node.userData.lit_source) lights.push({ kind: node.userData.lit_source, intensity: node.intensity,
        castShadow: node.castShadow, radius: node.shadow.radius, measured: node.userData.measured }); });
      const mesh = []; engine.modes.getMesh().traverse(node => { if (node.isMesh) mesh.push({ standard: node.material.isMeshStandardMaterial, cast: node.castShadow, receive: node.receiveShadow }); });
      const orbs = []; engine.overlay.virtualFixturesGroup.traverse(node => { if (node.material) orbs.push([node.name, node.material.uuid, node.material.toneMapped]); });
      return { state: { ...state }, lights, mesh, orbs, toneMapping: engine.renderer.toneMapping, shadows: engine.renderer.shadowMap.enabled,
        shadowMaps: lights.length && [...engine.scene.children].length };
    });
    assert.equal(lit.state.active, true); assert.equal(lit.state.measured, false); assert.equal(lit.state.lights, 32); assert.equal(lit.state.shadowLights, 8);
    assert.deepEqual(lit.state.assumedLumens, { spot: 450, down: 350, zone: 120 });
    assert.ok(lit.mesh.length > 0 && lit.mesh.every(row => row.standard && row.cast && row.receive), 'the scan mesh is relit');
    assert.ok(lit.lights.filter(light => light.kind === 'spot').every(light => light.castShadow && light.radius === 2.5 && light.intensity > 0));
    assert.ok(lit.lights.filter(light => light.kind === 'down').every(light => light.castShadow && light.radius === 6 && light.intensity > 0));
    assert.ok(lit.lights.filter(light => light.kind === 'zone').every(light => !light.castShadow));
    assert.equal(lit.toneMapping, before.toneMapping, 'renderer tone mapping untouched'); assert.equal(lit.shadows, true);
    assert.deepEqual(lit.orbs, before.orbs, 'orbs and beams keep the same untone-mapped materials, so they stay legible in Lit');
    const litLuma = await meanLuma();
    assert.ok(Number.isFinite(litLuma) && Math.abs(litLuma - unlitLuma) > 1, 'Lit renders differently from the unlit mesh: ' + unlitLuma + ' vs ' + litLuma);

    // Light follows the channels: all spots, downs and zones off darkens the room.
    const ids = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.map(item => item.fixture_id));
    await page.getByRole('button', { name: 'All off', exact: true }).click().catch(() => {});
    await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.every(item => !item.state.spotlight.on && !item.state.downward.on), null, { timeout: LIT_WAIT_MS });
    await frames(6);
    const darkLuma = await meanLuma();
    const darkLights = await page.evaluate(() => { let sum = 0; window.__APT_ENGINE.scene.traverse(node => { if (node.isSpotLight && node.userData.lit_source) sum += node.intensity; }); return sum; });
    assert.equal(darkLights, 0, 'every simulated light follows its channel off');
    assert.ok(darkLuma < litLuma - 1, 'turning the fixtures off darkens the Lit room: ' + litLuma + ' -> ' + darkLuma);
    await page.getByRole('button', { name: 'All on', exact: true }).click().catch(() => {});
    await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.every(item => item.state.spotlight.on), null, { timeout: LIT_WAIT_MS });
    await frames(6);
    const litTiming = await timing();
    // Opt-in stills for visual review; never part of the pass criteria.
    if (process.env.HOME_APARTMENT_LIT_SCREENSHOTS === '1') {
      await page.screenshot({ path: path.join(run, 'lit-1366.png') });
      await page.getByRole('button', { name: 'Mesh view', exact: true }).click();
      await page.waitForFunction(() => window.__APT_ENGINE.litState().active === false); await frames(6);
      await page.screenshot({ path: path.join(run, 'mesh-1366.png') });
      await litButton.click(); await page.waitForFunction(() => window.__APT_ENGINE.litState().active === true, null, { timeout: LIT_WAIT_MS }); await frames(6);
    }
    check('lit_on_relights_mesh', { lights: lit.state.lights, shadow_lights: lit.state.shadowLights, luma: { unlit: unlitLuma, lit: litLuma, all_off: darkLuma }, fixtures: ids.length });
    receipt.performance = { note: 'descriptive only; headless browser on this machine is not the owner laptop', unlit_mesh: unlitTiming, lit: litTiming,
      quality: await page.evaluate(() => window.__APT_ENGINE.litState().quality) };

    // Mesh while Lit returns to the unlit mesh; Lit then Cloud restores everything.
    await page.getByRole('button', { name: 'Mesh view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState().active === false);
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.modes.mode), 'mesh');
    assert.equal(await note.count(), 0, 'the disclosure leaves with Lit');
    await litButton.click(); await page.waitForFunction(() => window.__APT_ENGINE.litState().active === true, null, { timeout: LIT_WAIT_MS });
    await page.getByRole('button', { name: 'Cloud view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'points' && window.__APT_ENGINE.litState().active === false);
    const after = await page.evaluate(() => {
      const engine = window.__APT_ENGINE, rows = [];
      engine.modes.getMesh().traverse(node => { if (node.isMesh) rows.push([node.material.uuid, Object.keys(node.geometry.attributes).sort().join(',')]); });
      let litLights = 0; engine.scene.traverse(node => { if (node.userData?.lit_source) litLights++; });
      return { meshMaterials: rows, toneMapping: engine.renderer.toneMapping, shadows: engine.renderer.shadowMap.enabled, litLights };
    });
    assert.deepEqual(after.meshMaterials, before.meshMaterials, 'leaving Lit restores the exact mesh materials and geometry attributes');
    assert.equal(after.shadows, before.shadows); assert.equal(after.toneMapping, before.toneMapping); assert.equal(after.litLights, 0);
    assert.equal(await litButton.getAttribute('aria-pressed'), 'false');
    check('leaving_lit_restores_mesh_and_renderer', after);

    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes, 'authored pack bytes unchanged');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.missing_code, []);
    for (const [relative, digest] of Object.entries(receipt.served)) assert.equal(digest, hash(fs.readFileSync(path.join(SRC, relative))), 'served code stayed stable: ' + relative);
    receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored Lit browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) {
    failureSummary = String(error.message).split(String.fromCharCode(10)).slice(0, 3).join(' | ').slice(0, 600);
    receipt.failure = error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nLit failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
});
