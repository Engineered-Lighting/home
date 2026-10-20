// Actual mounted Home + renderer + authored room. No private assets/upstreams.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { serveAuthoredShell } from './fixtures/apartment-shell-audit-server.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backends = (process.env.HOME_PHOTO_CROSSFADE_BACKENDS || 'webgl,webgpu-webgl').split(',');
assert.ok(backends.every(name => ['webgl', 'webgpu-webgl', 'webgpu'].includes(name)));

async function pixelsInPage() {
  const e = window.__APT_ENGINE;
  e.setRunning(false);
  const frame = async () => {
    await e.modes.tickSplat(e.camera, performance.now() / 1000);
    await e.modes.whenSparkIdle();
    return e.captureFrame({ scale: 1 });
  };
  await e.modes.setMode('splat', { duration: 0 });
  const reference = await frame();
  if (!reference) throw Error('Photo frame unavailable');
  const depths = () => {
    const rows = [e.pointsMaterial.depthWrite];
    for (const points of e.apartmentRoot.children.filter(node => node.isPoints)) points.traverse(node => {
      for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) rows.push(material.depthWrite);
    });
    return rows;
  };
  const compare = async () => {
    const sample = await frame();
    let covered = 0, darkened = 0;
    for (let i = 0; i < reference.pixels.length; i += 4) {
      const a = reference.pixels[i] + reference.pixels[i + 1] + reference.pixels[i + 2];
      if (reference.pixels[i + 3] < 100 || a < 120) continue;
      covered++;
      const b = sample.pixels[i] + sample.pixels[i + 1] + sample.pixels[i + 2];
      if (b < a * .75) darkened++;
    }
    return { covered, darkened, fraction: darkened / covered, depths: depths() };
  };
  await e.modes.setMode('points', { duration: 0 });
  await e.modes.setMode('splat', { duration: 600 });
  e.modes.update(.55);
  const intoPhoto = await compare();
  e.modes.update(.1);
  const settledPhoto = { depths: depths(), pointsVisible: e.apartmentRoot.children.find(node => node.isPoints)?.visible };
  await e.modes.setMode('points', { duration: 600 });
  e.modes.update(.05);
  const intoCloud = await compare();
  await e.modes.setMode('splat', { duration: 0 });
  const interrupted = { depths: depths(), mode: e.modes.mode };
  await e.modes.setMode('points', { duration: 0 });
  return { backend: e.rendererBackend, intoPhoto, intoCloud, settledPhoto, interrupted,
    finalCloud: { depths: depths(), pointsVisible: e.apartmentRoot.children.find(node => node.isPoints)?.visible } };
}

test('Cloud/Photo crossfades preserve Photo pixels and restore Cloud self-occlusion', { timeout: 145000 }, async t => {
  const run = path.join(ROOT, '.tmp/apartment-photo-crossfade-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const served = await serveAuthoredShell(run, path.join(ROOT, 'app/src'));
  const receipt = { authored_room: true, household_data: false, physical_commands: 0, results: [], external: [], errors: [] };
  const browsers = new Set();
  t.after(async () => { await Promise.allSettled([...browsers].map(browser => browser.close())); served.server.closeAllConnections(); served.server.close(); });
  try {
    for (const backend of backends) {
      const args = ['--disable-background-networking', '--renderer-process-limit=2'];
      if (backend === 'webgpu') args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
      if (process.platform === 'win32') args.push('--use-angle=d3d11');
      else args.push('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
      const channel = process.env.HOME_PHOTO_CROSSFADE_CHANNEL || (!fs.existsSync(chromium.executablePath()) && process.platform === 'win32' ? 'msedge' : undefined);
      const browser = await chromium.launch({ headless: true, args, ...(channel ? { channel } : {}) }); browsers.add(browser);
      try {
        const context = await browser.newContext({ viewport: { width: 960, height: 600 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
        await context.addCookies([{ name: 'authored_owner_session', value: '1', url: served.origin }]);
        await context.addInitScript(() => { localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true; });
        await context.route('**/*', route => {
          const url = new URL(route.request().url());
          if (url.origin === served.origin || ['data:', 'blob:'].includes(url.protocol)) return route.continue();
          if (!(['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && url.pathname === '/healthz' && route.request().method() === 'GET')) receipt.external.push(url.origin + url.pathname);
          return route.abort();
        });
        await context.routeWebSocket('**/*', socket => socket.close());
        const page = await context.newPage(); page.on('pageerror', error => receipt.errors.push(error.message));
        await page.goto(served.origin + '/?apartmentRedesign=1&worldZoom=off' + (backend === 'webgl' ? '' : '&renderer=' + backend));
        await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'echo', null, { timeout: 45000 });
        await page.getByRole('button', { name: 'Photo view', exact: true }).click();
        await page.waitForFunction(() => window.__APT_ENGINE?.modes.mode === 'splat');
        const result = await page.evaluate(pixelsInPage); receipt.results.push(result);
        console.log('Photo fade ' + JSON.stringify(result));
        assert.equal(result.backend, backend === 'webgpu-webgl' ? 'webgpu-webgl2' : backend);
        for (const [name, image] of [['into Photo', result.intoPhoto], ['into Cloud', result.intoCloud]]) {
          assert.ok(image.covered > 1000, name + ' exercises visible Photo pixels');
          assert.ok(image.fraction < .01, name + ': fading Cloud blacked out ' + (image.fraction * 100).toFixed(2) + '% of Photo');
          assert.ok(image.depths.every(value => value === false), name + ' disables depth on both point rendering paths');
        }
        for (const state of [result.settledPhoto, result.interrupted, result.finalCloud]) assert.ok(state.depths.every(value => value === true), 'settling/interruption restores Cloud depth');
        assert.equal(result.settledPhoto.pointsVisible, false); assert.equal(result.finalCloud.pointsVisible, true);
      } finally { await browser.close(); browsers.delete(browser); }
    }
    assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.external, []); assert.deepEqual(served.forbidden, []);
  } finally { fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2)); }
});
