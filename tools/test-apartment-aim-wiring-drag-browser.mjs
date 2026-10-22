// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// HANDOFF "Drag aim" without snap (APT-A01, A04, A05) and "Escape order" (APT-F04).
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { aimWiringProof, FILE_TIMEOUT_MS, OPTICAL, TRAVEL } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: drag aim, Set, Undo and the Escape order through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('drag-escape', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
  selectedTab, title, overlayState, until, canvasPoint, surfaceHit, near, settle, reticleDrag, save, receipt }) => {
    const id = fixtures[0].fixture_id;
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('Spot tool and Aim tab', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
    await until('reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
    const original = await descriptor(id);

    await check('Drag aim (no snap): chip, cyan proposal while the current aim stays, Set aim, exact Undo and Redo', async () => {
      const reticleBefore = (await overlayState()).reticle;
      assert.equal(reticleBefore.width >= 44 && reticleBefore.height >= 44, true, 'the reticle hit area is 44 px');
      assert.match(reticleBefore.label, /arrow keys/);
      const end = await canvasPoint('surface', null, reticleBefore); assert.ok(end);
      const poseBefore = await pose();
      const drag = await reticleDrag(end);
      assert.equal(drag.during.chip.state, 'valid');
      assert.match(drag.during.chip.text, /\d+\.\d\d m · \d+° tilt/, 'the chip names distance and tilt');
      assert.deepEqual(drag.during.chip.disclosures, [['optical-origin', OPTICAL], ['travel-limits', TRAVEL]]);
      assert.equal(await selectedTab(), 'Aim', 'the Aim tab opens at drag start');
      assert.deepEqual(await pose(), poseBefore, 'a reticle drag never orbits');
      assert.ok(drag.released?.hit, 'the release has a visible canonical surface hit');
      await until('bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      const state = await overlayState();
      assert.equal(state.bar.state, 'valid'); assert.match(state.bar.text, /Set aim/); assert.match(state.bar.text, /Discard/);
      assert.equal(state.reticle.state, 'proposed'); assert.equal(state.proposal_marker, true);
      assert.deepEqual(await descriptor(id), original, 'the current aim does not move before Set aim');
      assert.equal(await page.evaluate(() => document.querySelector('[data-apt-aim-row="proposed"]')?.getAttribute('data-state')), 'valid');
      assert.equal(await page.locator('[data-apt-aim-tab-disclosure]').textContent(), OPTICAL + ' · ' + TRAVEL);
      assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-apt-aim-reticle')), true);
      await page.keyboard.press('Enter');
      await until('set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      const aimed = await descriptor(id);
      assert.ok(near(aimed.target.point, drag.released.hit.point), 'Set aim lands within 3 cm of the release hit');
      assert.deepEqual(aimed.display_anchor_m, original.display_anchor_m, 'the socket stays fixed');
      assert.equal(aimed.physical_commands, false);
      assert.match(await page.locator('body').innerText(), /no hardware command sent/);
      assert.equal(await fixtureInspector().locator('.apt-ui-badge[data-state="modeled"]').textContent(), 'Modeled fixture');
      assert.equal(await fixtureInspector().getByText('Not commissioned', {exact:true}).count(), 1, 'the design badge keeps commissioning status explicit');
      assert.equal((await overlayState()).bar, null, 'Set retires the proposal');
      await blur(); await page.keyboard.press('Control+z');
      await until('undo', ({ id, pan }) => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.pan_deg === pan, { id, pan: original.state.pan_deg });
      assert.deepEqual(await descriptor(id), original, 'Ctrl+Z restores the exact aim');
      await until('proposal back', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.keyboard.press('Control+Shift+z');
      await until('redo', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), aimed, 'Shift+Ctrl+Z re-applies the same aim');
      await page.keyboard.press('Control+z');
      await until('undo again', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original);
      return { fixture_id: id, release: drag.released, aimed_target: aimed.target, chip: drag.during.chip.text };
    });

    await check('Escape order: drag, then proposal (Ctrl+Z restores it), then selection, then nothing', async () => {
      // Undo left the earlier proposal pending; Escape discards it first.
      let state = await overlayState();
      assert.ok(state.bar, 'a proposal is pending');
      const pendingText = state.bar.text;
      const end = await canvasPoint('surface', null, state.reticle);
      await reticleDrag(end, { release: false });
      await page.keyboard.press('Escape');
      await until('drag canceled', () => document.querySelector('[data-apt-aim-chip]')?.hidden === true);
      await page.mouse.up(); await settle();
      state = await overlayState();
      assert.equal(state.bar?.text, pendingText, 'Escape during a drag cancels the drag only');
      assert.deepEqual(await descriptor(id), original);
      await page.keyboard.press('Escape');
      await until('discarded', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      assert.equal(await title(), fixtures[0].label, 'the selection survives the discard');
      await page.keyboard.press('Control+z');
      await until('restored', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      assert.equal((await overlayState()).bar.text, pendingText, 'Ctrl+Z restores the discarded proposal');
      await page.keyboard.press('Escape'); await until('discarded again', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      await page.keyboard.press('Escape'); await settle();
      assert.equal(await page.locator('.apt-home-shell[data-home-layout="unified"]').count(), 1, 'the last Escape does nothing');
      assert.deepEqual(await descriptor(id), original);
      return { fixture_id: id };
    });
}));
