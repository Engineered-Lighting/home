import test from 'node:test';
import assert from 'node:assert/strict';
import { runFullUiShell } from './fixtures/home-full-ui-harness.mjs';

test('full Home: real Lab lists saved sessions, refuses recording, distinguishes empty/unavailable and retries on desktop and phone', { timeout: 180000 }, async () => {
  await runFullUiShell('home-full-ui-lab-browser', async ({ page, openShell, menu, state, calls, checks, viewports, inViewport }) => {
    await openShell();
    const lab = page.locator('section[aria-label="Perception Lab"]');
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      state.lab = 'populated';
      await menu('Perception Lab');
      await lab.locator('[data-lab-browser-access="read-only"]').waitFor();
      await lab.getByRole('button', { name: 'Review & analyze', exact: true }).click();
      const sessions = lab.getByRole('region', { name: 'Recorded sessions', exact: true });
      await sessions.getByRole('button', { name: /Authored reading session/ }).waitFor();
      assert.ok(calls.some(row => row.path === '/api/perception-lab/v1/sessions'), 'real Lab transport reads the authored session service');
      await sessions.getByRole('button', { name: /Authored reading session/ }).click();
      const recorded = lab.getByRole('region', { name: 'Recorded session', exact: true });
      await recorded.getByRole('heading', { name: 'Authored reading session', exact: true }).waitFor();
      assert.equal(await recorded.getByRole('tab', { name: 'Label review', exact: true }).getAttribute('aria-selected'), 'true');
      assert.ok(calls.some(row => row.path.endsWith('/sessions/authored-full-ui-session/media')), 'opening a saved session reads its typed media manifest');
      await recorded.getByRole('button', { name: /Sessions$/ }).click();
      await sessions.waitFor();
      state.lab = 'error';
      await sessions.getByRole('button', { name: 'Refresh', exact: true }).click();
      await lab.getByText('Live experiment service unavailable', { exact: true }).waitFor();
      state.lab = 'populated';
      await lab.getByRole('button', { name: 'Retry', exact: true }).click();
      await lab.getByText('Live experiment service unavailable', { exact: true }).waitFor({ state: 'hidden' });
      await sessions.getByRole('button', { name: /Authored reading session/ }).waitFor();
      state.lab = 'empty';
      await sessions.getByRole('button', { name: 'Refresh', exact: true }).click();
      await sessions.getByText('No saved sessions', { exact: true }).waitFor();
      await lab.getByRole('button', { name: 'Live & record', exact: true }).click();
      assert.equal(await lab.getByRole('button', { name: 'Start & record session', exact: true }).isDisabled(), true,
        'a browsable service does not grant capture or model admission');
      assert.match(await lab.locator('#pl-start-reason').innerText(), /browser connection is read-only/i);
      const refused = await page.evaluate(async () => {
        try { await window.HomePerceptionLabController.action('sessions.start', {}); return 'unexpected_write'; }
        catch (error) { return error.message; }
      });
      assert.equal(refused, 'lab_browser_read_only', 'the real controller refuses before any fixture write is attempted');
      await inViewport(lab.getByRole('button', { name: 'Back', exact: true }));
      await lab.getByRole('button', { name: 'Back', exact: true }).click();
      await lab.waitFor({ state: 'hidden' });
      checks.push({ stage: 'lab_saved_session_error_retry_empty_no_capture', viewport });
    }
  });
});
