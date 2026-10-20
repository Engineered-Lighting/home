// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// the Spot pending dot (APT-A03) and aiming with Chat open through the room bar (APT-A02, A04).
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: the Spot pending dot and Chat-kept aiming through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('pending-chat', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
  selectedTab, title, overlayState, until, canvasPoint, surfaceHit, near, settle, reticleDrag, save, receipt }) => {
    const id = fixtures[0].fixture_id;
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('Spot tool and Aim tab', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
    await until('reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
    const original = await descriptor(id);

    await check('A03: the Spot pending dot shows only while a proposal waits and Spot is not the tool', async () => {
      const end = await canvasPoint('surface', null, (await overlayState()).reticle);
      await reticleDrag(end); await until('bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      const spot = page.locator('[data-apt-directional-mode="spot"]');
      assert.equal(await spot.locator('[data-apt-aim-pending]').count(), 0, 'no dot while Spot is the tool');
      await page.locator('[data-apt-directional-mode="zones"]').click();
      await spot.locator('[data-apt-aim-pending]').waitFor();
      assert.equal(await page.getByRole('button', { name: 'Spot (aim pending)', exact: true }).count(), 1, 'the Spot name gains "(aim pending)"');
      await spot.click(); await settle();
      assert.equal(await spot.locator('[data-apt-aim-pending]').count(), 0);
      await page.locator('[data-apt-aim-bar] [data-apt-aim-action="discard"]').click();
      await until('discarded', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { fixture_id: id };
    });

    await check('A02, A04: with Chat open a drag keeps Chat, and the room bar sets the aim', async () => {
      await page.getByRole('tab', { name: 'Chat', exact: true }).click();
      const end = await canvasPoint('surface', null, (await overlayState()).reticle);
      await reticleDrag(end);
      await until('bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true', 'Chat stays open');
      await page.locator('[data-apt-aim-bar] [data-apt-aim-action="set"]').click();
      await until('bar set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true');
      await blur(); await page.keyboard.press('Control+z');
      await until('chat undo', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original);
      await page.keyboard.press('Escape'); await until('discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { chat_kept: true };
    });
}));
