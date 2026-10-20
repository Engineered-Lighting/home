import test from 'node:test';
import assert from 'node:assert/strict';
import { runFullUiShell } from './fixtures/home-full-ui-harness.mjs';

test('full Home: real Intelligence reads packets, exposes packet failure and recovers on desktop and phone', { timeout: 145000 }, async () => {
  await runFullUiShell('home-full-ui-intelligence-browser', async ({ page, openShell, menu, state, calls, checks, viewports, inViewport }) => {
    await openShell();
    const atlas = page.locator('[aria-label="Intelligence Atlas"]');
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      state.intelligence = 'populated';
      await menu('Intelligence');
      const packet = atlas.locator('button.intel-atlas-event').filter({ hasText: 'authored study' });
      await packet.waitFor();
      assert.match(await packet.innerText(), /reading/);
      await inViewport(atlas.getByRole('button', { name: 'close / esc', exact: true }));
      state.detailError = true;
      await packet.click();
      await atlas.getByRole('status').filter({ hasText: /503/ }).waitFor();
      state.detailError = false;
      await packet.click();
      await atlas.getByRole('status').filter({ hasText: /503/ }).waitFor({ state: 'hidden' });
      assert.ok(calls.some(row => row.path.endsWith('/observation-packets/authored-full-ui-packet')), 'packet selection reads the service');
      state.intelligence = 'empty';
      await atlas.getByRole('button', { name: 'refresh', exact: true }).click();
      await atlas.getByText('no visual packets yet', { exact: true }).waitFor();
      state.intelligence = 'populated';
      await atlas.getByRole('button', { name: 'refresh', exact: true }).click();
      await packet.waitFor();
      state.intelligence = 'error';
      await atlas.getByRole('button', { name: 'refresh', exact: true }).click();
      const serviceError = atlas.getByRole('status').filter({ hasText: /unavailable|failed|503/i });
      await serviceError.waitFor();
      state.intelligence = 'populated';
      await atlas.getByRole('button', { name: 'refresh', exact: true }).click();
      await serviceError.waitFor({ state: 'hidden' });
      await packet.waitFor();
      await atlas.getByRole('button', { name: 'close / esc', exact: true }).click();
      await atlas.waitFor({ state: 'hidden' });
      checks.push({ stage: 'intelligence_packet_error_retry_empty_populated', viewport });
    }
  });
});
