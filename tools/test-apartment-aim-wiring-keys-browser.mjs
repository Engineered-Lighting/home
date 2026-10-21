// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// APT-A08 arrow-key nudges on the focused reticle and Tap to aim, as proposals.
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: arrow-key nudges and Tap to aim through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('keys-tap', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
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
      const fineTune = fixtureInspector().locator('[data-apt-aim-fine-tune]');
      assert.equal(await fineTune.evaluate(node => node.open), false, 'numeric fine tuning starts collapsed');
      assert.equal(await fixtureInspector().getByRole('slider', {name:'Pan',exact:true}).count(), 0, 'the primary Aim flow stays compact');
      await fineTune.locator('summary').press('Enter');
      const pan = fixtureInspector().getByRole('slider', {name:'Pan',exact:true});
      await pan.focus(); await page.keyboard.press('ArrowRight');
      await until('fine tune pan', ({id,pan})=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.pan_deg===pan+1,
        {id,pan:original.state.pan_deg});
      const fineTuned = await descriptor(id);
      assert.deepEqual({...fineTuned.state,pan_deg:original.state.pan_deg}, original.state, 'fine tuning preserves every other channel and tilt');
      await fixtureInspector().getByRole('button',{name:'Undo aim',exact:true}).press('Enter');
      await until('fine tune undo', ({id,pan})=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.pan_deg===pan,
        {id,pan:original.state.pan_deg});
      assert.deepEqual(await descriptor(id),original,'compact Undo restores the exact aim');
      await fixtureInspector().getByRole('button',{name:'Redo aim',exact:true}).press('Enter');
      await until('fine tune redo', ({id,pan})=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.pan_deg===pan,
        {id,pan:fineTuned.state.pan_deg});
      assert.deepEqual(await descriptor(id),fineTuned,'compact Redo restores the exact fine tune');
      await fixtureInspector().getByRole('button',{name:'Undo aim',exact:true}).press('Enter');
      await until('restore fine tune', ({id,pan})=>window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.pan_deg===pan,
        {id,pan:original.state.pan_deg});
      await fineTune.locator('summary').press('Enter');
      assert.equal(await fineTune.evaluate(node=>node.open),false,'keyboard users can close fine tuning again');
      return { point, hit };
    });
}));
