import test from 'node:test';
import assert from 'node:assert/strict';
import { runFullUiShell } from './fixtures/home-full-ui-harness.mjs';

test('full Home: Metrics reads populated service data while the room stays mounted, on desktop and phone', { timeout: 145000 }, async () => {
  await runFullUiShell('home-full-ui-metrics-browser', async ({ page, openShell, menu, state, calls, sockets, checks, viewports, inViewport }) => {
    await openShell();
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await menu('Metrics');
      const metrics = page.getByRole('dialog', { name: 'Home metrics', exact: true });
      await metrics.waitFor();
      await page.waitForFunction(() => window.__hav_metricsLatest?.model === 'Authored full UI model', null, { timeout: 15000 });
      assert.ok(sockets.some(row => row.type === 'auth'), 'real HA client authenticates against the authored socket');
      assert.ok(calls.some(row => row.path === '/proxy/metrics/metrics'), 'the actual shell fetches metrics while its room remains mounted');
      const summary = metrics.locator('button.hg-focusable').first();
      await summary.click();
      await metrics.getByText('Authored full UI model', { exact: true }).first().waitFor();
      state.metrics = 'error';
      await page.waitForFunction(() => window.__hav_metricsLatest === null, null, { timeout: 15000 });
      await metrics.getByText('Authored full UI model', { exact: true }).waitFor({ state: 'hidden' });
      state.metrics = 'populated';
      await metrics.getByText('Authored full UI model', { exact: true }).first().waitFor({ timeout: 15000 });
      await inViewport(metrics.getByRole('button', { name: 'Back', exact: true }));
      await metrics.getByRole('button', { name: 'Back', exact: true }).click();
      await metrics.waitFor({ state: 'hidden' });
      assert.equal(await page.locator('.apt-home-stage canvas').first().isVisible(), true, 'closing Metrics leaves the same room available');
      checks.push({ stage: 'metrics_populated_error_recovery_without_leaving_room', viewport });
    }
  });
});
