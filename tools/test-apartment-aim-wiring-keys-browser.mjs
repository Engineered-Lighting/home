// APT-A02, A03, A04 and A08 (W2) on the mounted Home shell: arrow-key nudges
// and Tap to aim as proposals, the Spot pending dot, aiming with Chat open,
// the phone sheet at half and slider power-on, on authored Victoria fixtures.
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

test('aim wiring: keys, tap, pending dot, Chat, phone sheet and sliders through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('keys-tap-chat-sheet', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
  selectedTab, title, overlayState, until, canvasPoint, surfaceHit, near, settle, reticleDrag, save, receipt }) => {
    const id = fixtures[0].fixture_id;
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('Spot tool and Aim tab', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
    await until('reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
    const original = await descriptor(id);
    await check('A08: arrow keys nudge the focused reticle 10 px, or 40 px with Shift, as proposals', async () => {
      await page.locator('[data-apt-aim-reticle]').focus();
      await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Light', exact: true }).click();
      await blur(); await page.keyboard.press('a');
      await page.locator('[data-apt-aim-reticle]').focus();
      const start = (await overlayState()).reticle;
      await page.keyboard.press('ArrowRight');
      await until('nudged', () => document.querySelector('[data-apt-aim-reticle]')?.getAttribute('data-state') !== 'current');
      await settle();
      const one = (await overlayState()).reticle;
      await page.keyboard.press('Shift+ArrowRight'); await settle(); await settle();
      const two = (await overlayState()).reticle;
      assert.ok(Math.abs(one.x - start.x - 10) <= 2.5 && Math.abs(one.y - start.y) <= 2.5, 'ArrowRight moves the proposal 10 px');
      assert.ok(Math.abs(two.x - one.x - 40) <= 4 && Math.abs(two.y - one.y) <= 4, 'Shift+ArrowRight moves it 40 px');
      assert.equal(await selectedTab(), 'Aim', 'a key nudge brings Aim forward');
      assert.deepEqual(await descriptor(id), original, 'nudges only propose');
      await page.keyboard.press('Escape'); await until('discard nudge', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { start, one, two };
    });

    await check('A08: Tap to aim proposes from one tap; Escape leaves the mode', async () => {
      const toggle = fixtureInspector().getByRole('button', { name: 'Tap to aim', exact: true });
      await toggle.click();
      await page.locator('[data-apt-tap-aim-hint]').waitFor();
      await blur(); await page.keyboard.press('Escape');
      await page.locator('[data-apt-tap-aim-hint]').waitFor({ state: 'detached' });
      assert.equal(await title(), fixtures[0].label, 'leaving tap mode keeps the selection');
      await toggle.click(); await page.locator('[data-apt-tap-aim-hint]').waitFor();
      const point = await canvasPoint('surface', null, (await overlayState()).reticle), hit = await surfaceHit(point);
      const poseBefore = await pose();
      await page.mouse.click(point.x, point.y);
      await until('tap proposal', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.locator('[data-apt-tap-aim-hint]').waitFor({ state: 'detached' });
      assert.equal(await title(), fixtures[0].label, 'the tap neither deselects nor selects');
      assert.deepEqual(await pose(), poseBefore, 'the tap does not orbit');
      assert.deepEqual(await descriptor(id), original);
      const proposed = await page.evaluate(() => document.querySelector('[data-apt-aim-row="proposed"]')?.getAttribute('data-state'));
      assert.equal(proposed, 'valid');
      await fixtureInspector().locator('[data-apt-aim-tab-action="set"]').click();
      await until('tap set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.ok(near((await descriptor(id)).target.point, hit.point), 'the tapped spot is set within 3 cm');
      await fixtureInspector().getByRole('button', { name: 'Undo aim', exact: true }).click();
      await until('tap undo', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original);
      await fixtureInspector().locator('[data-apt-aim-tab-action="discard"]').click();
      await until('tap discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { point, hit };
    });

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

    await check('A02: on a phone, aiming raises the sheet from peek to half and keeps Chat', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      const grip = page.getByRole('button', { name: 'Resize Home sheet', exact: true });
      for (let tries = 0; tries < 3 && await page.locator('.apt-home-shell').getAttribute('data-sheet') !== 'peek'; tries++) await grip.click();
      assert.equal(await page.locator('.apt-home-shell').getAttribute('data-sheet'), 'peek');
      await until('phone reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
      await page.locator('[data-apt-aim-reticle]').focus(); await page.keyboard.press('ArrowLeft');
      await until('half', () => document.querySelector('.apt-home-shell')?.getAttribute('data-sheet') === 'half');
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true');
      await until('phone bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.keyboard.press('Escape'); await until('phone discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      await page.setViewportSize({ width: 1366, height: 768 });
      await page.getByRole('tab', { name: 'Controls', exact: true }).click();
      return { sheet: 'half' };
    });

    await check('a Spot or Down brightness slider powers its channel on', async () => {
      await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Light', exact: true }).click();
      const power = fixtureInspector().getByRole('checkbox', { name: 'Spot power', exact: true });
      if (await power.isChecked()) await power.uncheck();
      await until('spot off', id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.spotlight.on === false, id);
      const slider = fixtureInspector().getByRole('slider', { name: 'Brightness', exact: true });
      await slider.focus(); await page.keyboard.press('Home'); await page.keyboard.press('ArrowRight');
      await until('spot on', id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.spotlight.on === true, id);
      const down = fixtureInspector().getByRole('checkbox', { name: 'Down power', exact: true });
      if (await down.isChecked()) await down.uncheck();
      const downSlider = fixtureInspector().getByRole('slider', { name: 'Down brightness', exact: true });
      await downSlider.focus(); await page.keyboard.press('ArrowRight');
      await until('down on', id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.downward.on === true, id);
      return { spot: true, down: true };
    });

}));
