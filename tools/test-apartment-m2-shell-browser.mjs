// Authored-room browser acceptance for the M2 shell pieces in the actual Home
// shell: the HANDOFF "Photo missing" example with its loading percentage,
// Mesh and saved-view recovery, camera views as room poses with Return to
// room, and a reachability walk over every screen the old Home menu reached.
import test from 'node:test';
import assert from 'node:assert/strict';
import { runM2Shell, waitForDirectionalReadiness, FORMER_SCREENS } from './fixtures/apartment-m2-shell-harness.mjs';

test('M2 shell: Photo missing, mode recovery, camera views and every former screen in the actual Home shell', { timeout: 180000 }, async () => {
  await runM2Shell('apartment-m2-shell-browser', async shell => {
    const { page, faults, stalled, requests, checks, receipt, echo, assetPath, waitSite, homes, selectedHome, toolbar, modeButton, notice, engineMode, openShell, tab, menu } = shell;
    receipt.former_screens = FORMER_SCREENS;
    // Opt-in stills for visual review; CI stays functional-only.
    const still = async name => {
      if (process.env.M2_SCREENSHOTS !== '1') return;
      await page.evaluate(() => window.__APT_ENGINE?.setRunning?.(false));
      try { await page.screenshot({ path: receipt.run + '/' + name + '.png' }); }
      finally { await page.evaluate(() => window.__APT_ENGINE?.setRunning?.(true)); }
    };
    // Every Photo candidate stalls from the first page load, so the first one the
    // device's asset plan asks for (desktop or mobile tier) shows a real partial
    // percentage and nothing reaches the in-memory asset cache beforehand.
    const splatNames = ['apartment.mobile.ply', 'apartment.ply', 'apartment.spz'];
    for (const name of splatNames) faults.set(assetPath(echo, name), 'stall');
    await openShell(); await waitSite('echo');
    assert.equal(await selectedHome(), 'Echo Park');

    // HANDOFF › Acceptance examples › "Photo missing": loading with its
    // percentage, then "Photo isn't available here" with Retry and Back to
    // Cloud, still Echo Park.
    await modeButton('Photo').click();
    await notice().getByRole('heading', { name: /^Loading Photo · \d+%$/ }).waitFor().catch(async error => {
      // The touched-harness runner keeps only the last lines, which hold the stack.
      const diagnostics = JSON.stringify({ notice: await notice().allInnerTexts().catch(() => null),
        requests: requests.filter(row => /\/apartment\./.test(row.path)).map(row => row.path),
        modes: await page.evaluate(() => { const d = window.__APT_ENGINE?.modes?.debugInfo?.(); return d && { status: d.status, loadStatus: d.loadStatus, last: d.lastLoadError }; }).catch(() => null) });
      error.message += ' ' + diagnostics; error.stack = 'Photo loading diagnostics: ' + diagnostics + String.fromCharCode(10) + error.stack;
      throw error;
    });
    const loading = await notice().evaluate(node => ({ title: node.querySelector('h3').textContent,
      value: Number(node.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')) }));
    assert.ok(loading.value > 0 && loading.value < 100, 'a real partial percentage is shown: ' + loading.title);
    await still('photo-loading');
    assert.equal(await engineMode(), 'points', 'the current view stays as it is while Photo loads');
    for (const name of splatNames) faults.set(assetPath(echo, name), 'fail');
    for (const res of stalled.splice(0)) res.destroy();
    await notice().getByRole('heading', { name: "Photo isn't available here", exact: true }).waitFor();
    const recovery = page.getByRole('group', { name: 'Photo recovery', exact: true });
    assert.equal(await recovery.getByRole('button', { name: 'Retry Photo', exact: true }).isEnabled(), true);
    assert.equal(await recovery.getByRole('button', { name: 'Back to Cloud', exact: true }).isEnabled(), true);
    assert.match(await notice().innerText(), /still looking at Echo Park/);
    await still('photo-missing');
    assert.equal(await selectedHome(), 'Echo Park', 'a missing Photo never switches homes');
    assert.equal(await engineMode(), 'points');
    const splatRequests = () => requests.filter(row => splatNames.some(name => row.path === assetPath(echo, name))).length;
    const before = splatRequests();
    await recovery.getByRole('button', { name: 'Retry Photo', exact: true }).click();
    await waitForDirectionalReadiness(page, () => !!document.querySelector('[aria-label="Photo recovery"]'));
    assert.ok(splatRequests() > before, 'Retry asks for the Photo asset again');
    await page.getByRole('group', { name: 'Photo recovery', exact: true }).getByRole('button', { name: 'Back to Cloud', exact: true }).click();
    await waitForDirectionalReadiness(page, () => !document.querySelector('.apt-ui-toolbar-notice'));
    assert.equal(await modeButton('Cloud').getAttribute('aria-pressed'), 'true');
    assert.equal(await selectedHome(), 'Echo Park');
    checks.push({ stage: 'handoff_photo_missing_in_real_shell', loading });
    for (const name of splatNames) faults.delete(assetPath(echo, name));

    // Camera view: a room pose inside the unified chrome with Return to room.
    const hint = page.locator('[data-apt-hint="camera-view"]');
    const enterCamera = async () => {
      assert.equal(await page.evaluate(() => window.__havApartmentDebug.flyFirstCamera()), true);
      await hint.waitFor();
      await waitForDirectionalReadiness(page, () => window.__APT_ENGINE.rig.cameraPoseSettled());
    };
    await enterCamera();
    assert.match(await hint.innerText(), /Viewing from Authored corner camera/);
    await still('camera-view');
    assert.equal(await hint.getByRole('button', { name: /^Return to room/ }).count(), 1);
    assert.equal(await page.locator('[data-apt-legacy-toolbar="1"]').count(), 0, 'no old camera HUD or second home picker');
    assert.equal(await toolbar().isVisible(), true, 'the room toolbar stays in place during a camera view');
    for (const name of ['Orbit left', 'Orbit right', 'Fit room']) {
      assert.equal(await toolbar().getByRole('button', { name, exact: true }).isDisabled(), true, name + ' is inert in a camera view');
    }
    assert.equal(await page.locator('.apt-room-guide-legend').count(), 0, 'the legend gives the top of the room to the hint bar');
    const pose = () => page.evaluate(() => window.__APT_ENGINE.camera.position.toArray().map(value => Math.round(value * 1000)));
    const held = await pose();
    const box = await page.locator('.apt-home-stage canvas').first().boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.wheel(0, -600);
    await page.keyboard.press('ArrowLeft');
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 300)));
    assert.deepEqual(await pose(), held, 'wheel and orbit keys leave the camera pose where it is');
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.rig.inCameraPose()), true);
    await page.keyboard.press('Escape');
    await hint.waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.rig.inCameraPose()), false, 'Escape returns to the room');
    await enterCamera();
    await hint.getByRole('button', { name: /^Return to room/ }).click();
    await hint.waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.rig.inCameraPose()), false, 'Return to room leaves the camera view');
    assert.equal(await toolbar().getByRole('button', { name: 'Orbit left', exact: true }).isEnabled(), true);
    checks.push({ stage: 'echo_camera_view_returns_to_room' });

    // Victoria offers no camera view and says why.
    await homes().getByRole('button', { name: 'Victoria', exact: true }).click(); await waitSite('victoria');
    assert.equal(await page.evaluate(() => window.__havApartmentDebug.flyFirstCamera()), false);
    await toolbar().getByRole('button', { name: 'Gestures and keys', exact: true }).click();
    await page.getByRole('button', { name: 'About this home', exact: true }).click();
    const about = page.locator('[data-apt-about="camera-views"]');
    assert.match(await about.innerText(), /Den-calibrated camera pose/);
    assert.equal(await about.evaluate(node => node.closest('[data-apt-about-group]').dataset.aptAboutGroup), 'unavailable');
    await page.getByRole('button', { name: 'Close About', exact: true }).click();
    checks.push({ stage: 'victoria_has_no_camera_view_and_says_why' });
    await homes().getByRole('button', { name: 'Echo Park', exact: true }).click(); await waitSite('echo');

    // Reachability: every former screen from the unified shell.
    const reached = {};
    await menu('Perception Lab');
    const lab = page.getByRole('region', { name: 'Perception Lab', exact: true }).or(page.locator('section[aria-label="Perception Lab"]'));
    await lab.first().waitFor({ timeout: 30000 });
    await page.locator('section[aria-label="Perception Lab"]').getByRole('button', { name: 'Back', exact: true }).first().click();
    await page.locator('section[aria-label="Perception Lab"]').waitFor({ state: 'detached' });
    reached['perception-lab'] = 'Back';
    await menu('Metrics');
    const metrics = page.getByRole('dialog', { name: 'Home metrics', exact: true });
    await metrics.waitFor(); await metrics.getByRole('button', { name: 'Back', exact: true }).click(); await metrics.waitFor({ state: 'hidden' });
    reached.metrics = 'Back';
    for (const [id, label, locator] of [
      ['people', 'People', () => page.locator('[data-home-people-root], .hp-root, [aria-label*="People" i]').first()],
      ['intelligence', 'Intelligence', () => page.locator('[aria-label="Intelligence Atlas"]').first()],
      ['video-labeler', 'Video labeler', () => page.locator('[aria-label^="Video labeler"]').first()],
    ]) {
      await menu(label);
      await locator().waitFor({ timeout: 30000 });
      reached[id] = await locator().evaluate(node => node.getAttribute('aria-label') || node.className);
      await page.keyboard.press('Escape');
      await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 150)));
      if (await locator().isVisible().catch(() => false)) {
        const close = page.getByRole('button', { name: /^(Close|Back|close|back|×|←)/ }).last();
        if (await close.count()) await close.click();
      }
    }
    for (const [id, label, locator] of [
      ['lights', 'Lights / Travel Mode', () => page.locator('[aria-label="Living Lights tuning"]').first()],
      ['preferences', 'Preferences / connection', () => page.getByRole('dialog', { name: 'Remote profile', exact: true })],
    ]) {
      await menu(label); await locator().waitFor({ timeout: 30000 });
      reached[id] = await locator().evaluate(node => node.getAttribute('aria-label'));
      await page.keyboard.press('Escape'); await locator().waitFor({ state: 'hidden' });
    }
    assert.equal(await page.getByRole('menu', { name: 'Home features', exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Home menu', exact: true }).click();
    const items = await page.getByRole('menu', { name: 'Home features', exact: true }).getByRole('menuitem').allInnerTexts();
    await page.keyboard.press('Escape');
    for (const label of ['Perception Lab', 'Metrics', 'People', 'Intelligence', 'Video labeler', 'Lights / Travel Mode',
      'Preferences / connection', 'Previous Home']) assert.ok(items.includes(label), 'Home menu lists ' + label);
    assert.equal(items.includes('Simulation'), false, 'Simulation controls are listed only while simulation runs');
    reached.simulation = '/simulation and ?simulation=1; Menu › Simulation while it runs';
    await tab('Controls');
    reached['previous-home'] = 'Menu item; exercised by test-apartment-home-shell-browser.mjs';
    await tab('Chat');
    const bench = page.locator('.apt-ui-chat-header').getByRole('button', { name: 'Bench', exact: true });
    await bench.waitFor({ timeout: 20000 }); await bench.click();
    assert.deepEqual(await page.evaluate(() => window.__benchOpened), ['http://127.0.0.1:8765/']);
    reached.bench = 'chat header';
    await tab('Controls');
    checks.push({ stage: 'former_screens_reachable', reached });

    // Saved-view failure: the restored view's mode is missing after a reload.
    await modeButton('Mesh').click();
    await waitForDirectionalReadiness(page, () => window.__APT_ENGINE.modes.mode === 'mesh');
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 600)));
    for (const name of ['mesh.glb', 'collision.glb']) faults.set(assetPath(echo, name), 'fail');
    await openShell(); await waitSite('echo');
    await notice().getByRole('heading', { name: "Your saved view didn't load", exact: true }).waitFor({ timeout: 30000 });
    assert.match(await notice().innerText(), /stays on Cloud/);
    await page.getByRole('group', { name: 'Mesh recovery', exact: true }).getByRole('button', { name: 'Stay on Cloud', exact: true }).click();
    await waitForDirectionalReadiness(page, () => !document.querySelector('.apt-ui-toolbar-notice'));
    assert.equal(await selectedHome(), 'Echo Park');
    checks.push({ stage: 'saved_view_failure_offers_recovery' });

    // Mesh failure explains itself and recovers in place.
    await modeButton('Mesh').click();
    await notice().getByRole('heading', { name: "Mesh isn't available here", exact: true }).waitFor();
    const meshRecovery = page.getByRole('group', { name: 'Mesh recovery', exact: true });
    assert.equal(await meshRecovery.getByRole('button', { name: 'Retry Mesh', exact: true }).isEnabled(), true);
    assert.equal(await meshRecovery.getByRole('button', { name: 'Back to Cloud', exact: true }).isEnabled(), true);
    assert.equal(await selectedHome(), 'Echo Park');
    for (const name of ['mesh.glb', 'collision.glb']) faults.delete(assetPath(echo, name));
    await meshRecovery.getByRole('button', { name: 'Retry Mesh', exact: true }).click();
    await waitForDirectionalReadiness(page, () => window.__APT_ENGINE.modes.mode === 'mesh' && !document.querySelector('.apt-ui-toolbar-notice'));
    assert.equal(await modeButton('Mesh').getAttribute('aria-pressed'), 'true');
    checks.push({ stage: 'mesh_failure_recovers_in_place' });
    await modeButton('Cloud').click();
    await waitForDirectionalReadiness(page, () => window.__APT_ENGINE.modes.mode === 'points');
    assert.deepEqual(Object.keys(reached).sort(), FORMER_SCREENS.map(row => row.id).filter(id => id !== 'apartment').sort());
  });
});
