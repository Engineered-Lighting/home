// Setup is never a dead control. On a home whose equipment registration is
// read-only, Setup opens the rail read-only and says why; where Setup cannot
// open at all, pressing it says why instead of doing nothing. Echo is
// unchanged. Also: Escape closes the Home menu opened with a mouse click.
// Authored rooms only; the registration is made read-only or unavailable by
// rewriting the authored gateway's response in the browser. No writes.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { installSetupRooms, startSetupServer, openSetupContext, browserOptions, homeShell, hash } from './fixtures/apartment-setup-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REGISTRATION = '**/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration';

test('Setup opens read-only on a read-only home, explains itself where it cannot open, and Escape closes the Home menu', { timeout: 200000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-setup-read-only-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const rooms = installSetupRooms(run), registration = hash(fs.readFileSync(rooms.registrationFile));
  const receipt = { schema: 'home-apartment-setup-read-only-browser-1', authored_room: true, household_data: false, screenshots: false,
    saves: 0, physical_commands: 0, checks: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let server, browser, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('170s read-only Setup deadline at: ' + receipt.current_stage)); }, 170000); });
  const check = async (name, operation) => { receipt.current_stage = name; save(); receipt.checks.push({ name, ...(await operation()) }); save(); };
  async function proof() {
    server = await startSetupServer(rooms);
    browser = await chromium.launch(receipt.browser);

    // 1. Victoria's registration loads, but this owner may not write it.
    const ro = await openSetupContext(browser, server.origin, server.log, { site: 'victoria' });
    await ro.context.route(REGISTRATION, async route => {
      const response = await route.fetch(), body = await response.json();
      if (body?.capabilities) body.capabilities.write = false;
      await route.fulfill({ response, json: body });
    });
    const shell = homeShell(ro.page, server.origin), page = ro.page;
    await shell.boot('1', 'victoria');
    await check('read-only Victoria: Setup opens the rail read-only, with the reason, and nothing can be changed', async () => {
      await page.waitForFunction(() => window.__havApartmentDebug?.modelSnapshot()?.source_detail?.read_only === true);
      assert.equal(await shell.setupButton().getAttribute('aria-disabled'), null, 'Setup is a working control here');
      await shell.setupButton().click(); await shell.inspector().waitFor();
      const note = await shell.inspector().locator('[data-setup-read-only]').innerText();
      assert.match(note, /Victoria's room layout is read-only here, so placement can't be changed/);
      assert.match(note, /desk and sofa targets are marked from a light's Setup tab/);
      assert.equal(await shell.inspector().locator('fieldset[data-equipment-registration="inspector"] input').first().isDisabled(), true, 'equipment fields are read-only');
      assert.equal(await shell.inspector().getByRole('button', { name: 'Add socket', exact: true }).isDisabled(), true);
      assert.equal(await shell.inspector().locator('button[title="Save equipment positions for this Victoria Den scan revision"]').isDisabled(), true);
      for (const tool of ['position', 'cameras']) {
        await shell.tool(tool);
        assert.equal(await shell.inspector().locator('fieldset[data-equipment-registration="inspector"] input').first().isDisabled(), true, tool + ' is read-only');
      }
      await shell.setupButton().click(); await shell.rail().waitFor({ state: 'detached' });
      return { opened: true, read_only_note: note };
    });
    await check('read-only Victoria: the light Setup tab offers Review fixture placement, next to the registered desk target', async () => {
      const inspector = page.locator('[data-victoria-virtual-fixtures="1"]');
      await page.getByRole('button', { name: /^Select / }).first().click();
      await inspector.getByRole('tab', { name: 'Setup', exact: true }).click();
      const review = inspector.getByRole('button', { name: 'Review fixture placement', exact: true });
      assert.equal(await review.isDisabled(), false);
      await review.click(); await shell.inspector().locator('[data-setup-read-only]').waitFor();
      await shell.setupButton().click(); await shell.rail().waitFor({ state: 'detached' });
      return { review: true };
    });
    await check('Escape closes the Home menu opened with a mouse click', async () => {
      const menu = page.getByRole('button', { name: 'Home menu', exact: true });
      await menu.click(); await page.getByRole('menu', { name: 'Home features' }).waitFor();
      assert.equal(await menu.getAttribute('aria-expanded'), 'true');
      await page.keyboard.press('Escape');
      await page.getByRole('menu', { name: 'Home features' }).waitFor({ state: 'detached', timeout: 3000 });
      assert.equal(await menu.getAttribute('aria-expanded'), 'false');
      return { closed: true };
    });
    await ro.context.close();

    // 2. Victoria's registration is unavailable: Setup can't open, and says why.
    const off = await openSetupContext(browser, server.origin, server.log, { site: 'victoria' });
    await off.context.route(REGISTRATION, route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"ok":false,"error":"equipment_unconfigured"}' }));
    const offShell = homeShell(off.page, server.origin);
    await offShell.boot('1', 'victoria');
    await check('registration unavailable: Setup reads unavailable and a press shows the reason, never a silent no-op', async () => {
      await off.page.waitForFunction(() => document.querySelector('.apt-home-topbar .apt-home-setup')?.getAttribute('aria-disabled') === 'true');
      await offShell.setupButton().click({ force: true }); // aria-disabled still takes a press
      const why = off.page.locator('[data-apt-setup-unavailable]');
      await why.waitFor();
      assert.match(await why.innerText(), /Victoria's equipment registration isn't available here/);
      assert.equal(await offShell.setupButton().getAttribute('aria-describedby'), 'apt-home-setup-why');
      assert.equal(await offShell.rail().count(), 0);
      return { reason: await why.innerText() };
    });
    await check('Echo is unchanged: Setup opens the editable rail', async () => {
      await offShell.selectHome('echo');
      await off.page.waitForFunction(() => document.querySelector('.apt-home-topbar .apt-home-setup')?.getAttribute('aria-disabled') === null);
      assert.equal(await off.page.locator('[data-apt-setup-unavailable]').count(), 0);
      await offShell.openSetup();
      assert.equal(await offShell.inspector().locator('[data-setup-read-only]').count(), 0);
      assert.equal(await offShell.header().innerText(), 'Devices and sockets');
      return { echo: 'editable' };
    });
    await off.context.close();

    assert.equal(hash(fs.readFileSync(rooms.registrationFile)), registration, 'registration bytes unchanged');
    assert.deepEqual(server.log.forbidden, []); assert.deepEqual(server.log.missing, []); assert.deepEqual(server.log.errors, []);
    assert.equal(server.log.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false, 'no write of any kind');
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); save(); throw error; }
  finally {
    clearTimeout(timer);
    for (const close of [() => browser?.close(), () => server?.close()]) { try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push(error.message); } }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
