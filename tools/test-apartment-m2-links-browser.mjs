// Authored-room browser acceptance for old links and renderer recovery in the
// actual Home shell: the bare URL lands in the room, the Lab demo and
// simulation links still open what they did, and a room that fails to load
// offers Retry and the other home without switching on its own.
import test from 'node:test';
import assert from 'node:assert/strict';
import { runM2Shell, waitForDirectionalReadiness } from './fixtures/apartment-m2-shell-harness.mjs';

test('M2 shell: old deep links keep working and a failed room recovers in place', { timeout: 180000 }, async () => {
  await runM2Shell('apartment-m2-links-browser', async shell => {
    const { page, faults, checks, echo, assetPath, waitSite, homes, selectedHome, toolbar, openShell, tab, menu } = shell;
    // Deep links: the bare URL lands in the room; old links keep working.
    await openShell(''); await waitSite('echo');
    checks.push({ stage: 'bare_url_lands_in_unified_room' });
    await openShell('?apartmentRedesign=1&worldZoom=off&perceptionLabDemo=1');
    await page.locator('section[aria-label="Perception Lab"]').first().waitFor({ timeout: 30000 });
    checks.push({ stage: 'perception_lab_demo_link_opens_lab' });
    await openShell('?apartmentRedesign=1&worldZoom=off&simulation=1');
    await menu('Simulation');
    const simulation = page.getByRole('dialog', { name: 'Simulation controls', exact: true });
    await simulation.waitFor({ timeout: 30000 }); await page.keyboard.press('Escape'); await simulation.waitFor({ state: 'hidden' });
    checks.push({ stage: 'simulation_link_and_menu_open_simulation_controls' });

    // Renderer failure: recovery stays in the chrome and never switches homes.
    faults.set(assetPath(echo, 'points.ply'), 'fail');
    await openShell();
    const room = page.getByRole('group', { name: 'Room recovery', exact: true });
    await room.waitFor({ timeout: 30000 });
    assert.match(await room.innerText(), /Echo Park's room didn't load/);
    assert.equal(await room.getByRole('button', { name: 'Retry room', exact: true }).isEnabled(), true);
    assert.equal(await room.getByRole('button', { name: 'Open Victoria', exact: true }).isEnabled(), true);
    assert.equal(await selectedHome(), 'Echo Park', 'a failed room never switches homes on its own');
    faults.delete(assetPath(echo, 'points.ply'));
    await room.getByRole('button', { name: 'Retry room', exact: true }).click();
    await waitSite('echo'); await room.waitFor({ state: 'hidden' });
    checks.push({ stage: 'renderer_failure_retry_recovers_same_home' });
  });
});
