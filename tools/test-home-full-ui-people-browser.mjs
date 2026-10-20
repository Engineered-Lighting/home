import test from 'node:test';
import assert from 'node:assert/strict';
import { runFullUiShell } from './fixtures/home-full-ui-harness.mjs';

test('full Home: real People loads the household, distinguishes empty/refused and recovers on desktop and phone', { timeout: 180000 }, async () => {
  await runFullUiShell('home-full-ui-people-browser', async ({ page, openShell, menu, state, calls, checks, viewports, inViewport }) => {
    await openShell();
    const people = page.locator('[aria-label="People — relationships and identities"]');
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      state.people = 'populated';
      await menu('People');
      await people.getByRole('button', { name: 'list', exact: true }).click();
      await people.getByText('QA Ada', { exact: true }).first().waitFor();
      assert.match(await people.innerText(), /QA Owner/);
      assert.ok(calls.some(row => row.path === '/api/agent/v1/household'), 'the mounted People component reads the household service');
      await people.getByText('QA Ada', { exact: true }).first().click();
      const detail = page.getByRole('dialog', { name: 'Identity detail', exact: true });
      await detail.getByText('QA Ada', { exact: true }).first().waitFor();
      await inViewport(detail.getByRole('button', { name: 'Close panel', exact: true }));
      await detail.getByRole('button', { name: 'Close panel', exact: true }).click();
      await detail.waitFor({ state: 'hidden' });
      await inViewport(people.getByRole('button', { name: 'Close', exact: true }));
      state.people = 'error';
      await people.getByRole('button', { name: 'refresh', exact: true }).click();
      await people.getByText('People service unavailable', { exact: true }).waitFor();
      assert.equal(await people.getByText('no identities yet', { exact: true }).count(), 0, 'failure never pretends the household was empty');
      state.people = 'populated';
      await people.getByRole('button', { name: 'Retry People', exact: true }).click();
      await people.getByText('QA Ada', { exact: true }).first().waitFor();
      state.people = 'empty';
      await people.getByRole('button', { name: 'refresh', exact: true }).click();
      await people.getByText('no identities yet', { exact: true }).waitFor();
      assert.equal(await people.getByText('QA Ada', { exact: true }).count(), 0);
      await people.getByRole('button', { name: 'Close', exact: true }).click();
      await people.waitFor({ state: 'hidden' });
      await inViewport(page.getByRole('button', { name: 'Home menu', exact: true }));
      checks.push({ stage: 'people_populated_refused_retry_empty', viewport });
    }
  });
});
