// One label per Den fixture: while the in-room HTML labels are mounted, the
// canvas equipment tag of each labelled mount stays hidden; equipment without
// an HTML label keeps its tag. Authored packs only: no household data,
// session, camera, command or deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { serveAuthoredShell } from './fixtures/apartment-shell-audit-server.mjs';
import { waitForDirectionalReadiness } from './fixtures/apartment-directional-readiness.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Fixed before the first run (2026-10-20); never raised to make a run pass.
const DEADLINE_MS = 90000;

function launchOptions(platform = process.platform) {
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || platform === 'win32', 'the pinned Playwright Chromium browser must be available on this platform');
  return { headless: true, args, ...(platform === 'linux' ? { channel: 'chromium' } : !installed && platform === 'win32' ? { channel: 'msedge' } : {}) };
}

test('each Den fixture shows one label: its HTML label, never also its canvas equipment tag', { timeout: DEADLINE_MS + 15000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-one-label-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-one-label-1', authored_room: true, real_household_data: false, physical_commands: 0, views: [], errors: [] };
  let served, browser, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(new Error(DEADLINE_MS + ' ms one-label deadline')); }, DEADLINE_MS); });
  async function exercise() {
    served = await serveAuthoredShell(run, path.join(ROOT, 'app/src'));
    browser = await chromium.launch(launchOptions());
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: served.origin }]);
    await context.addInitScript(() => { localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true; });
    await context.route('**/*', route => { const url = new URL(route.request().url());
      return ['data:', 'blob:'].includes(url.protocol) || url.origin === served.origin ? route.continue() : route.abort(); });
    await context.routeWebSocket('**/*', socket => socket.close());
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => receipt.errors.push(error.message));
    await page.goto(served.origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    await waitForDirectionalReadiness(page, () => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'echo');
    await page.evaluate(() => document.fonts.ready.then(() => true));
    const home = page.getByRole('group', { name: 'Home', exact: true }).getByRole('button', { name: 'Victoria', exact: true });
    if (await home.getAttribute('aria-pressed') !== 'true') await home.click();
    await waitForDirectionalReadiness(page, () => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'victoria'
      && document.querySelectorAll('[data-apt-fixture-label]:not([hidden])').length > 0);
    const read = () => page.evaluate(() => {
      const overlay = window.__APT_ENGINE.overlay, fixtures = overlay.virtualFixturesReadback?.().fixtures || [];
      const html = [...document.querySelectorAll('[data-apt-fixture-label]')].filter(node => !node.hidden).map(node => node.getAttribute('data-apt-fixture-label'));
      const tags = [...overlay.markersById.values()].map(marker => {
        const tag = marker.group.getObjectByName('equipment-tag');
        let shown = !!tag; for (let node = tag; node && shown; node = node.parent) shown = node.visible;
        return { id: marker.device.id, kind: marker.device.equipment_record?.kind || null, tag: !!tag, shown };
      });
      return { html, fixtures: fixtures.map(item => ({ fixture_id: item.fixture_id, mount_id: item.mount_id })), tags };
    });
    for (const [label, turns] of [['default view', 0], ['two detents round', 2]]) {
      for (let i = 0; i < turns; i++) { await page.getByRole('button', { name: 'Orbit right', exact: true }).click(); await page.waitForTimeout(400); }
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const state = await read(); receipt.views.push({ label, ...state });
      assert.ok(state.fixtures.length >= 4 && state.html.length === state.fixtures.length, label + ': every Den fixture has its one HTML label');
      assert.equal(new Set(state.html).size, state.html.length, label + ': no fixture has two HTML labels');
      for (const fixture of state.fixtures) {
        const marker = state.tags.find(row => row.id === fixture.mount_id);
        assert.ok(marker?.tag, label + ': the mount of ' + fixture.fixture_id + ' has an equipment tag to hide');
        assert.equal(marker.shown, false, label + ': no canvas tag doubles the HTML label of ' + fixture.fixture_id);
      }
      const labelled = new Set(state.fixtures.map(item => item.mount_id));
      for (const row of state.tags.filter(item => item.tag && !labelled.has(item.id)))
        assert.equal(row.shown, true, label + ': equipment without an HTML label keeps its tag (' + row.id + ')');
    }
    // Pausing retires the owned HTML layer synchronously. The fallback canvas
    // labels must return in that same operation, before any React effect or
    // asynchronous Spark sorting can change the capture's visible state.
    const paused = await page.evaluate(() => {
      const engine=window.__APT_ENGINE;engine.setRunning(false);
      return {html:document.querySelectorAll('[data-apt-fixture-label]:not([hidden])').length,
        tags:[...engine.overlay.markersById.values()].filter(marker=>marker.tag).map(marker=>({id:marker.device.id,shown:marker.tag.visible}))};
    });
    assert.equal(paused.html,0,'paused presentation removes its HTML labels synchronously');
    assert.ok(paused.tags.every(tag=>tag.shown),'canvas fallback returns synchronously with HTML retirement');
    receipt.paused=paused;
    await page.evaluate(()=>window.__APT_ENGINE.setRunning(true));
    await page.locator('[data-apt-fixture-label]').first().waitFor();
    // Without the HTML layer (the legacy path) every equipment tag is drawn.
    const legacy = await page.evaluate(() => { window.__APT_ENGINE.overlay.setRoomLabelledEquipment([]);
      return [...window.__APT_ENGINE.overlay.markersById.values()].filter(marker => marker.tag).every(marker => marker.tag.visible); });
    assert.equal(legacy, true, 'with no HTML labels every equipment tag shows');
    assert.deepEqual(served.forbidden, [], 'no command or session request');
    assert.deepEqual(receipt.errors, [], 'no page errors');
  }
  try { await Promise.race([exercise(), deadline]); }
  finally {
    clearTimeout(timer); fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    await browser?.close().catch(() => {}); served?.server.closeAllConnections(); served?.server.close();
  }
});
