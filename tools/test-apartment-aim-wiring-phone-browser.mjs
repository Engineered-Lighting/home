// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// the phone sheet rising to half while Chat stays (APT-A02), and slider power-on.
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: the phone sheet and slider power-on through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('phone-sliders', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
  selectedTab, title, overlayState, until, canvasPoint, surfaceHit, near, settle, reticleDrag, save, receipt }) => {
    const id = fixtures[0].fixture_id;
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('Spot tool and Aim tab', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
    await until('reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
    const original = await descriptor(id);

    await check('A02: on a phone, aiming raises the sheet from peek to half and keeps Chat', async () => {
      await page.getByRole('tab', { name: 'Chat', exact: true }).click();
      await page.setViewportSize({ width: 390, height: 844 });
      const grip = page.getByRole('button', { name: 'Resize Home sheet', exact: true });
      for (let tries = 0; tries < 3 && await page.locator('.apt-home-shell').getAttribute('data-sheet') !== 'peek'; tries++) await grip.click();
      assert.equal(await page.locator('.apt-home-shell').getAttribute('data-sheet'), 'peek');
      const reticle = page.locator('[data-apt-aim-reticle]');
      // Resize/peek can retire and recreate the room overlay. A false hidden
      // attribute alone does not establish that its button can receive focus.
      await reticle.waitFor({ state: 'visible' });
      await reticle.focus();
      await until('phone reticle focused', () => document.activeElement?.matches('[data-apt-aim-reticle]') === true);
      await page.keyboard.press('ArrowLeft');
      await until('half', () => document.querySelector('.apt-home-shell')?.getAttribute('data-sheet') === 'half');
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true');
      await until('phone bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.keyboard.press('Escape'); await until('phone discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      await page.getByRole('tab', { name: 'Controls', exact: true }).click();
      await page.locator('[data-apt-aim-reticle]').focus(); await page.keyboard.press('ArrowLeft');
      await until('phone proposal before full', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      for (let tries=0;tries<3&&await page.locator('.apt-home-shell').getAttribute('data-sheet')!=='full';tries++) await grip.click();
      await until('full sheet retires the drawing lease',()=>document.querySelector('.apt-home-shell')?.getAttribute('data-sheet')==='full'
        &&document.querySelectorAll('[data-apt-fixture-label]:not([hidden])').length===0);
      assert.equal(await fixtureInspector().locator('[data-apt-aim-row="current"]').isVisible(),true,'current aim remains in a full sheet');
      assert.equal(await fixtureInspector().locator('[data-apt-aim-row="proposed"]').isVisible(),true,'pending aim remains when drawing pauses');
      assert.deepEqual(await descriptor(id),original,'expanding the sheet cannot change the actual aim');
      await fixtureInspector().locator('[data-apt-aim-tab-action="set"]').click();
      await until('full sheet Set',id=>!!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target,id);
      await fixtureInspector().getByRole('button',{name:'Undo aim',exact:true}).click();
      await until('full sheet Undo',id=>!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target,id);
      assert.deepEqual(await descriptor(id),original,'Undo works while the room is paused');
      await fixtureInspector().locator('[data-apt-aim-tab-action="discard"]').click();
      assert.equal(await fixtureInspector().locator('[data-apt-aim-row="proposed"]').count(),0,'Discard works while drawing is paused');
      await fixtureInspector().getByRole('button',{name:'Tap to aim',exact:true}).click();
      await until('tap reveals the room',()=>document.querySelector('.apt-home-shell')?.getAttribute('data-sheet')==='half'
        &&!!document.querySelector('[data-apt-tap-aim-hint]'));
      await blur(); await page.keyboard.press('Escape');
      await page.locator('[data-apt-tap-aim-hint]').waitFor({state:'detached'});
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
