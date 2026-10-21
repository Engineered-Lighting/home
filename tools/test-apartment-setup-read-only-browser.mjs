// Direct Setup and the retained Home menu entry share one editor. On a home
// whose equipment registration is read-only,
// Setup opens the rail read-only and says why; where Setup cannot open at
// all, pressing it says why instead of doing nothing. Echo is unchanged.
// Also: Escape closes the Home menu opened with a mouse click and returns
// focus to Menu, and the menu's click-away layer covers the viewport with no
// border, so a click below the bar closes it.
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

test('Setup opens read-only on a read-only home, explains itself where it cannot open, and Escape closes the Home menu', { timeout: 145000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-setup-read-only-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const rooms = installSetupRooms(run), registration = hash(fs.readFileSync(rooms.registrationFile));
  const receipt = { schema: 'home-apartment-setup-read-only-browser-1', authored_room: true, household_data: false, screenshots: false,
    saves: 0, physical_commands: 0, checks: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let server, browser, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('145s read-only Setup deadline at: ' + receipt.current_stage)); }, 145000); });
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
      assert.equal(await shell.setupState(), 'off');
      const direct = page.getByRole('button', { name: 'Setup', exact: true });
      assert.equal(await direct.getAttribute('aria-pressed'), 'false');
      assert.equal(await direct.getAttribute('aria-disabled'), null, 'read-only Setup remains a working review control');
      await direct.click(); await shell.inspector().waitFor(); await shell.waitSetupState('on');
      assert.equal(await direct.getAttribute('aria-pressed'), 'true');
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
      assert.equal(await (await shell.openMenu()).getAttribute('aria-checked'), 'true');
      await shell.pressSetup(); await shell.rail().waitFor({ state: 'detached' }); await shell.waitSetupState('off');
      assert.equal(await direct.getAttribute('aria-pressed'), 'false', 'the retained menu entry closes the same Setup state');
      return { opened: true, read_only_note: note };
    });
    await check('read-only Victoria: the light Setup tab offers Review fixture placement, next to the registered desk target', async () => {
      const inspector = page.locator('[data-victoria-virtual-fixtures="1"]');
      await page.getByRole('button', { name: /^Select / }).first().click();
      await inspector.getByRole('tab', { name: 'Setup', exact: true }).click();
      const review = inspector.getByRole('button', { name: 'Review fixture placement', exact: true });
      assert.equal(await review.isDisabled(), false);
      await review.click(); await shell.inspector().locator('[data-setup-read-only]').waitFor();
      await shell.pressSetup(); await shell.rail().waitFor({ state: 'detached' });
      return { review: true };
    });
    await check('Escape closes the Home menu opened with a mouse click, and arrows walk it from Setup', async () => {
      const menu = page.getByRole('button', { name: 'Home menu', exact: true });
      await menu.click(); await shell.homeMenu().waitFor();
      assert.equal(await menu.getAttribute('aria-expanded'), 'true');
      await page.waitForFunction(() => document.activeElement?.matches('.apt-home-menu [role="menuitemcheckbox"]'));
      await page.keyboard.press('ArrowDown');
      assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Perception Lab');
      await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowUp');
      assert.equal(await page.evaluate(() => document.activeElement?.textContent), 'Previous Home', 'ArrowUp wraps from Setup to the last item');
      await page.keyboard.press('Escape');
      await shell.homeMenu().waitFor({ state: 'detached', timeout: 3000 });
      assert.equal(await menu.getAttribute('aria-expanded'), 'false');
      assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Home menu', 'focus returns to Menu');
      return { closed: true, focus: 'Home menu' };
    });
    await check('the open menu\'s click-away layer covers the viewport with no border, and a click below the bar closes it', async () => {
      const menu = page.getByRole('button', { name: 'Home menu', exact: true });
      await menu.click(); await shell.homeMenu().waitFor();
      const layer = await page.locator('.apt-home-menu-dismiss').evaluate(node => {
        const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, viewport: [innerWidth, innerHeight], position: style.position,
          borders: ['Top', 'Right', 'Bottom', 'Left'].map(side => style['border' + side + 'Width']) };
      });
      assert.deepEqual({ x: layer.x, y: layer.y, width: layer.width, height: layer.height, position: layer.position },
        { x: 0, y: 0, width: layer.viewport[0], height: layer.viewport[1], position: 'fixed' }, 'the click-away layer covers the viewport');
      assert.deepEqual(layer.borders, ['0px', '0px', '0px', '0px'], 'no line across the top bar');
      const below = { x: Math.round(layer.viewport[0] * .3), y: Math.round(layer.viewport[1] * .6) };
      assert.equal(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.className, below), 'apt-home-menu-dismiss', 'a click below the bar lands on the click-away layer');
      await page.mouse.click(below.x, below.y);
      await shell.homeMenu().waitFor({ state: 'detached', timeout: 3000 });
      assert.equal(await menu.getAttribute('aria-expanded'), 'false');
      return { layer, closed_by: 'click below the bar' };
    });
    await ro.context.close();

    // 2. Victoria's registration is unavailable: Setup can't open, and says why.
    const off = await openSetupContext(browser, server.origin, server.log, { site: 'victoria', mobile: true, viewport: { width: 390, height: 460 } });
    await off.context.route(REGISTRATION, route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"ok":false,"error":"equipment_unconfigured"}' }));
    const offShell = homeShell(off.page, server.origin);
    await offShell.boot('1', 'victoria');
    await check('registration unavailable: Setup reads unavailable and a press shows the reason, never a silent no-op', async () => {
      await offShell.waitSetupState('unavailable');
      const item = await offShell.openMenu();
      assert.equal(await item.getAttribute('aria-disabled'), 'true');
      await offShell.pressSetup({ force: true }); // aria-disabled still takes a press
      const why = off.page.locator('[data-apt-setup-unavailable]');
      await why.waitFor();
      assert.match(await why.innerText(), /Victoria's equipment registration isn't available here/);
      assert.equal(await item.getAttribute('aria-describedby'), 'apt-home-setup-why');
      assert.equal(await offShell.homeMenu().count(), 1, 'the menu stays open to show why');
      assert.equal(await offShell.rail().count(), 0);
      const reason = await why.innerText();
      await off.page.keyboard.press('Escape'); await offShell.homeMenu().waitFor({ state: 'detached' });
      return { reason };
    });
    await check('short coarse phone: direct unavailable Setup explains itself, menu scrolls, and Escape restores its opener', async () => {
      const direct = off.page.getByRole('button', { name: 'Setup', exact: true });
      assert.equal(await direct.getAttribute('aria-disabled'), 'true');
      const box = await direct.boundingBox();
      assert.ok(box.width >= 44 && box.height >= 44 && box.x >= 0 && box.x + box.width <= 390, 'direct Setup retains an unclipped touch target');
      await direct.click({ force: true });
      const why = off.page.locator('[data-apt-setup-unavailable]');await why.waitFor();
      assert.equal(await direct.getAttribute('aria-describedby'), 'apt-home-setup-why');
      assert.match(await why.innerText(), /Victoria's equipment registration isn't available here/);
      const menuBox = await offShell.homeMenu().boundingBox();
      assert.ok(menuBox.y >= 0 && menuBox.y + menuBox.height <= 460, 'the menu stays inside the short viewport');
      await off.page.waitForFunction(() => document.activeElement?.matches('.apt-home-menu [role="menuitemcheckbox"]'));
      await off.page.keyboard.press('End');
      await off.page.waitForFunction(() => document.activeElement?.textContent === 'Previous Home');
      const last = await offShell.homeMenu().getByRole('menuitem', { name: 'Previous Home', exact: true }).boundingBox();
      assert.ok(last.y >= menuBox.y && last.y + last.height <= menuBox.y + menuBox.height + 1, 'keyboard focus scrolls the final action into view');
      await off.page.keyboard.press('Escape');await offShell.homeMenu().waitFor({ state: 'detached' });
      assert.equal(await direct.evaluate(element => document.activeElement === element), true, 'Escape returns to direct Setup');
      assert.equal(await offShell.rail().count(), 0, 'an unavailable press grants no editor access');
      return { viewport: [390, 460], touch_target: [box.width, box.height], menu_scroll_and_focus: true };
    });
    await check('Echo is unchanged: Setup opens the editable rail', async () => {
      await offShell.selectHome('echo');
      await offShell.waitSetupState('off');
      assert.equal(await (await offShell.openMenu()).getAttribute('aria-disabled'), null);
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
