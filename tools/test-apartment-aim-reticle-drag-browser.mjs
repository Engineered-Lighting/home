// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// the held reticle stays under the pointer at every mid-drag frame (Home's
// press-feedback scale once parked it at the top-left until release), and
// grabbing the reticle while Zones is showing switches to Spot and aims in
// the same gesture. Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: the held reticle tracks the pointer, and grabbing it from Zones aims as Spot', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('reticle-drag', async ({ page, check, fixtures, descriptor, blur, fixtureInspector,
  selectedTab, overlayState, until, canvasPoint, near, settle }) => {
    const id = fixtures[0].fixture_id;
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    const reticleRect = () => page.evaluate(() => {
      const node = document.querySelector('[data-apt-aim-reticle]'), rect = node.getBoundingClientRect();
      return { hidden: node.hidden, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, state: node.getAttribute('data-state') };
    });
    const mode = () => page.evaluate(() => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true' ? 'spot'
      : document.querySelector('[data-apt-directional-mode="zones"]')?.getAttribute('aria-pressed') === 'true' ? 'zones' : null);

    await check('A01: a grab on the reticle while Zones shows switches to Spot and Aim, and the same drag proposes', async () => {
      // The first selection opens Light with Zones; the reticle shows anyway.
      await until('zones', () => document.querySelector('[data-apt-directional-mode="zones"]')?.getAttribute('aria-pressed') === 'true');
      assert.equal(await selectedTab(), 'Light');
      await until('reticle on Zones', () => document.querySelector('[data-apt-aim-reticle]')?.hidden === false);
      const original = await descriptor(id), at = await reticleRect(), end = await canvasPoint('surface', null, at);
      assert.ok(end, 'a registered surface point clear of the fixtures');
      await page.mouse.move(at.x, at.y); await page.mouse.down();
      await until('spot on press', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
      assert.equal(await selectedTab(), 'Aim', 'the press itself brings Aim forward');
      await page.evaluate(() => { window.__aimRelease = null; window.addEventListener('pointerup', event => {
        const hit = window.__APT_ENGINE.displaySurfaceHit(event.clientX, event.clientY);
        window.__aimRelease = hit && { point: [...hit.point] };
      }, { capture: true, once: true }); });
      await page.mouse.move(end.x, end.y, { steps: 10 }); await page.mouse.up();
      await until('bar', () => document.querySelector('[data-apt-aim-bar]')?.hidden === false);
      const release = await page.evaluate(() => window.__aimRelease);
      assert.equal(await mode(), 'spot');
      assert.deepEqual(await descriptor(id), original, 'the drop only proposes');
      assert.equal((await overlayState()).bar.state, 'valid');
      await page.keyboard.press('Enter');
      await until('set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.ok(near((await descriptor(id)).target.point, release.point), 'Set lands within 3 cm of the release');
      assert.match(await page.locator('body').innerText(), /no hardware command sent/);
      await blur(); await page.keyboard.press('Control+z');
      await until('undo', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original, 'Undo restores the exact aim');
      await page.keyboard.press('Escape'); await until('discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { from: 'zones', to: 'spot', release };
    });

    await check('the held reticle stays under the pointer at every mid-drag frame and is never parked at 0,0', async () => {
      const at = await reticleRect(), end = await canvasPoint('surface', null, at); assert.ok(end);
      await page.mouse.move(at.x, at.y); await page.mouse.down();
      const samples = [];
      for (let step = 1; step <= 8; step++) {
        const x = at.x + (end.x - at.x) * step / 8, y = at.y + (end.y - at.y) * step / 8;
        await page.mouse.move(x, y); await settle();
        const rect = await reticleRect();
        samples.push({ pointer: [x, y], reticle: [rect.x, rect.y], hidden: rect.hidden });
        assert.equal(rect.hidden, false, 'the held reticle stays visible');
        assert.ok(Math.hypot(rect.x - x, rect.y - y) <= 2, `the reticle follows the pointer (step ${step}: ${rect.x},${rect.y} vs ${x},${y})`);
        assert.ok(rect.x > 30 || rect.y > 30, 'never parked at the top-left');
      }
      await page.mouse.up();
      await until('bar after drag', () => document.querySelector('[data-apt-aim-bar]')?.hidden === false);
      await page.keyboard.press('Escape'); await until('discard after drag', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { samples };
    });
  }));
