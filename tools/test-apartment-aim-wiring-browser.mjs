// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// Native selection and direct, ledger-witnessed fixture aiming.
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createDirectionalNativeMouse, assertDirectionalNativeMouseLedger } from './fixtures/apartment-directional-native-mouse.mjs';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: direct selection and a native body drag through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('selection-body-drag', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
  selectedTab, title, overlayState, until, canvasPoint, surfaceHit, near, settle }) => {
    await check('Direct selection: label, cylinder and list row open the same Light inspector; hover changes nothing', async () => {
      const target = fixtures[1];
      const initial = await overlayState();
      assert.deepEqual(Object.values(initial.labels), ['idle', 'idle', 'idle', 'idle'], 'four or fewer lights: every label shows');
      await page.locator(`[data-apt-fixture-label="${target.fixture_id}"]`).click();
      await fixtureInspector().waitFor();
      assert.equal(await title(), target.label); assert.equal(await selectedTab(), 'Light', 'the first selection opens Light');
      await until('selected label and ring', id => document.querySelector(`[data-apt-fixture-label="${id}"]`)?.getAttribute('data-state') === 'selected'
        && document.querySelector(`[data-apt-fixture-ring="${id}"]`)?.getAttribute('data-state') === 'selected', target.fixture_id);
      const other = fixtures[2], before = { title: await title(), tab: await selectedTab() };
      await page.locator(`[data-apt-fixture-label="${other.fixture_id}"]`).hover();
      await until('hover ring', id => document.querySelector(`[data-apt-fixture-ring="${id}"]`)?.getAttribute('data-state') === 'hover', other.fixture_id);
      assert.deepEqual({ title: await title(), tab: await selectedTab() }, before, 'hover never changes the inspector');
      await blur(); await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      const body = await canvasPoint('fixture', target.fixture_id); assert.ok(body, 'an unobscured cylinder point');
      await page.mouse.click(body.x, body.y); await fixtureInspector().waitFor();
      assert.equal(await title(), target.label); assert.equal(await selectedTab(), 'Light');
      await blur(); await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      await page.getByRole('button', { name: 'Select ' + target.label, exact: true }).click(); await fixtureInspector().waitFor();
      assert.equal(await title(), target.label); assert.equal(await selectedTab(), 'Light');
      await blur(); await page.keyboard.press('n'); await until('N', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[2].label);
      await page.keyboard.press('p'); await until('P', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, target.label);
      await blur(); await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      // Focus restoration runs on the next frame.
      await until('focus returns after Escape', () => !!document.activeElement && document.activeElement !== document.body);
      await page.keyboard.press('Escape'); await settle();
      assert.equal(await page.locator('.apt-home-shell[data-home-layout="unified"]').count(), 1, 'idle Escape closes nothing');
      return { fixture_id: target.fixture_id, paths: ['label', 'cylinder', 'list'], hover_ring: other.fixture_id };
    });

    const id = fixtures[0].fixture_id;
    const original = await descriptor(id);

    await check('a body drag from the unselected room aims its light and leaves the other fixtures and camera unchanged', async () => {
      const other = fixtures[3].fixture_id, otherBefore = await descriptor(other);
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      assert.equal((await overlayState()).reticle, null, 'the drag starts without opening the fixture or selecting Spot');
      const start = await canvasPoint('fixture', other), end = await canvasPoint('surface', null, start); assert.ok(start && end);
      const expected = await surfaceHit(end); assert.ok(expected);
      const poseBefore = await pose();
      // A native, witnessed mouse: the same 16-sample ledger as the Home shell drags.
      const session = await context.newCDPSession(page);
      const mouse = createDirectionalNativeMouse({ send: (method, packet) => session.send(method, packet), onRecord: () => {} });
      let witnessed;
      try {
        await page.evaluate(() => {
          const observed = [], dragstarts = [], types = ['pointermove', 'pointerdown', 'pointerup', 'dragstart'];
          const witness = event => {
            if (event.type !== 'dragstart' && event.pointerType !== 'mouse') return;
            (event.type === 'dragstart' ? dragstarts : observed).push({ sequence: (event.type === 'dragstart' ? dragstarts : observed).length + 1,
              type: event.type, isTrusted: event.isTrusted, x: event.clientX, y: event.clientY, pointerType: event.pointerType,
              pointerId: event.pointerId, button: event.button, buttons: event.buttons, pressure: event.pressure,
              modifiers: { alt: event.altKey, control: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey } });
          };
          for (const type of types) window.addEventListener(type, witness, { capture: true });
          window.__aimBodyWitness = { observed, dragstarts, cleanup: () => { for (const type of types) window.removeEventListener(type, witness, { capture: true }); } };
        });
        await mouse.move(start.x, start.y); await mouse.move(start.x, start.y + 1);
        await mouse.down();
        await until('pointer-down selects', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[3].label);
        await mouse.move(end.x, end.y, { steps: 12 });
        await until('live aim chip', () => !document.querySelector('[data-apt-aim-chip]')?.hidden);
        assert.deepEqual(await descriptor(other), otherBefore, 'the live preview leaves the current aim unchanged until release');
        const captured = await page.evaluate(() => {
          const pointer = window.__aimBodyWitness.observed.find(row => row.type === 'pointerdown');
          return window.__APT_ENGINE.renderer.domElement.hasPointerCapture(pointer.pointerId);
        });
        assert.equal(captured, true, 'the spotlight drag owns pointer capture');
        await mouse.up();
        witnessed = await page.evaluate(() => { const value = window.__aimBodyWitness; value.cleanup(); window.__aimBodyWitness = null;
          return { observed: value.observed, dragstarts: value.dragstarts }; });
      } finally { await session.detach().catch(() => {}); }
      const record = mouse.readback();
      assert.deepEqual(record.operations.map(row => row.type), ['move', 'move', 'down', 'move', 'up']);
      assert.equal(record.expected.length, 16);
      const proof = assertDirectionalNativeMouseLedger({ ledger: record, observed: witnessed.observed, dragstarts: witnessed.dragstarts });
      assert.equal(proof.samples, 16);
      await settle();
      const after = await descriptor(other);
      assert.ok(after.target && near(after.target.point, expected.point), 'release applies the selected surface directly');
      assert.deepEqual(after.display_anchor_m, otherBefore.display_anchor_m, 'the fixture mount stays fixed');
      for (const channel of ['spotlight', 'downward', 'radials']) assert.deepEqual(after.state[channel], otherBefore.state[channel]);
      assert.deepEqual(await descriptor(id), original, 'the other light keeps its exact aim');
      assert.deepEqual(await pose(), poseBefore, 'the body drag did not orbit');
      assert.equal((await overlayState()).bar, null, 'the drop needs no proposal confirmation');
      await page.getByRole('button', { name: 'Undo aim', exact: true }).click();
      assert.deepEqual(await descriptor(other), otherBefore, 'Undo restores the aim and channels');
      await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await until('back', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[0].label);
      return { fixture_id: other, pose: poseBefore, native_samples: proof.samples };
    });
}));
