// W2 aim wiring on the mounted Home shell, with authored Victoria fixtures:
// HANDOFF "Direct selection" (APT-R01), and a native, ledger-witnessed fixture-body
// drag that selects without aiming or orbiting (APT-A01).
// Zero non-GET requests, WebSocket sends and service calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createDirectionalNativeMouse, assertDirectionalNativeMouseLedger } from './fixtures/apartment-directional-native-mouse.mjs';
import { aimWiringProof, FILE_TIMEOUT_MS } from './fixtures/apartment-aim-wiring-harness.mjs';

// Hosted CI spends tens of seconds per check, so each W2 suite keeps two checks
// inside its own fixed 145 s deadline.
test('aim wiring: direct selection and a native body drag through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, () => aimWiringProof('selection-body-drag', async ({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector,
  selectedTab, title, overlayState, until, canvasPoint, surfaceHit, near, settle, reticleDrag, save, receipt }) => {
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
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('Spot tool and Aim tab', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
    await until('reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
    const original = await descriptor(id);

    await check('a body drag selects its light but neither moves the aim nor orbits', async () => {
      const other = fixtures[3].fixture_id, otherBefore = await descriptor(other);
      const start = await canvasPoint('fixture', other), end = await canvasPoint('surface', null, start); assert.ok(start && end);
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
        await mouse.move(end.x, end.y, { steps: 12 }); await mouse.up();
        witnessed = await page.evaluate(() => { const value = window.__aimBodyWitness; value.cleanup(); window.__aimBodyWitness = null;
          return { observed: value.observed, dragstarts: value.dragstarts }; });
      } finally { await session.detach().catch(() => {}); }
      const record = mouse.readback();
      assert.deepEqual(record.operations.map(row => row.type), ['move', 'move', 'down', 'move', 'up']);
      assert.equal(record.expected.length, 16);
      const proof = assertDirectionalNativeMouseLedger({ ledger: record, observed: witnessed.observed, dragstarts: witnessed.dragstarts });
      assert.equal(proof.samples, 16);
      await settle();
      assert.deepEqual(await descriptor(other), otherBefore, 'the dragged light keeps its exact aim');
      assert.deepEqual(await descriptor(id), original, 'the other light keeps its exact aim');
      assert.deepEqual(await pose(), poseBefore, 'the body drag did not orbit');
      assert.equal((await overlayState()).bar, null, 'a body drag proposes nothing');
      await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await until('back', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[0].label);
      return { fixture_id: other, pose: poseBefore, native_samples: proof.samples };
    });
}));
