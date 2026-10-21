// APT-U01: the Setup rail in the real Home shell, on authored rooms only.
// Opens every tool in both homes, the pressed toggle, Escape, the dock
// across Setup, calibration from Cameras and sensors, phone and light theme.
// No household data, no saves, no session starts and no light commands.
// One page load for everything: each Home boot compiles the app in the
// browser, which dominates hosted-CI time.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { installSetupRooms, startSetupServer, openSetupContext, mockHomeAssistant, browserOptions, homeShell, hash } from './apartment-setup-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const TOOLS = [['sockets', 'Devices and sockets'], ['names', 'Names'], ['targets', 'Named aims'], ['zones', 'Zones'],
  ['position', 'Fixture position'], ['links', 'Home Assistant'], ['cameras', 'Cameras and sensors']];

export async function runSetupRailProof({ phase = 'rail' } = {}) {
  if (!['rail','dock','dock-collapsed','phone'].includes(phase)) throw Error('unknown Setup rail proof phase');
  const run = path.join(ROOT, '.tmp/apartment-setup-rail-browser', 'run-' + Date.now() + '-' + phase); fs.mkdirSync(run, { recursive: true });
  const rooms = installSetupRooms(run), packHashes = rooms.packHashes(), registration = hash(fs.readFileSync(rooms.registrationFile));
  const receipt = { schema: 'home-apartment-setup-rail-browser-1', authored_room: true, household_data: false, screenshots: false,
    saves: 0, physical_commands: 0, phase, checks: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let server, browser, timer;
  const started = Date.now();
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('140s Setup rail browser deadline at: '
    + receipt.current_stage + ' · ' + JSON.stringify(receipt.checks.map(item => item.seconds)))); }, 140000); });
  const check = async (name, operation) => {
    receipt.current_stage = name; save(); const begun = Date.now();
    receipt.checks.push({ name, ...(await operation()), seconds: Math.round((Date.now() - begun) / 100) / 10 }); save();
  };
  async function proof() {
    server = await startSetupServer(rooms);
    browser = await chromium.launch(receipt.browser);
    const site = phase === 'rail' ? 'echo' : 'victoria';
    const { page, context } = await openSetupContext(browser, server.origin, server.log, { site });
    // An authored Home Assistant that holds authentication until goOnline():
    // the first checks see it offline, the Flash check sees it connected.
    const lights = [{ entity_id: 'light.authored_ceiling_a', name: 'Ceiling A' }, { entity_id: 'light.authored_ceiling_b', name: 'Ceiling B' },
      { entity_id: 'light.authored_lamp', name: 'Lamp' }, { entity_id: 'light.authored_spare', name: 'Spare' }];
    const ha = await mockHomeAssistant(context, server.origin, { lights, online: false });
    await page.addInitScript(origin => localStorage.setItem('apartment3d.trackerBase', origin + '/proxy/tracker'), server.origin);
    const shell = homeShell(page, server.origin);
    await shell.boot('1', site);
    if (phase !== 'rail') ha.goOnline();
    const draft = () => page.evaluate(() => localStorage.getItem('apartment3d.modelDraft'));
    const originalDraft = await draft();
    const visible = selector => page.evaluate(selector => [...document.querySelectorAll(selector)]
      .some(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden'), selector);
    const saveButton = () => page.locator('[data-apt-edit-ui="toolbar"] button[title*="Home Assistant"]');

    if (phase === 'rail') {
    // Scene readiness and the shell's editor-capability callback commit
    // separately. The authored Echo editor must reach the shell before its
    // initial off state is asserted; loading is temporarily unavailable.
    await shell.waitSetupState('off');
    await check('Setup opens one rail and its inspector in the right column; tabs, chat and dock are hidden', async () => {
      assert.equal(await shell.setupState(), 'off');
      const setupToggle = page.getByRole('button', { name: 'Setup', exact: true });
      assert.equal(await setupToggle.getAttribute('aria-pressed'), 'false', 'the direct Setup toggle reads the shared off state');
      assert.equal(await (await shell.openMenu()).getAttribute('aria-checked'), 'false');
      await shell.openSetup(); await shell.waitSetupState('on');
      assert.equal(await setupToggle.getAttribute('aria-pressed'), 'true', 'the direct toggle reflects Setup opened through Menu');
      assert.equal(await shell.homeMenu().count(), 0, 'choosing Setup closes the menu');
      assert.equal(await (await shell.openMenu()).getAttribute('aria-checked'), 'true', 'Setup reads as on in the menu');
      await page.keyboard.press('Escape'); await shell.homeMenu().waitFor({ state: 'detached' });
      assert.equal(await shell.inspector().count(), 1, 'Escape closed only the menu');
      const labels = await shell.rail().locator('button').evaluateAll(nodes => nodes.map(node => [node.dataset.setupTool, node.querySelector('.apt-setup-rail-label').firstChild.textContent]));
      assert.deepEqual(labels, TOOLS);
      assert.equal(await shell.header().innerText(), 'Devices and sockets');
      assert.match(await shell.inspector().locator('.apt-ui-sub').innerText(), /^Setup · Echo Park · saved$/);
      assert.equal(await page.locator('.apt-home-controls [data-apt-setup-inspector]').count(), 1, 'the inspector is in the unified right column');
      for (const selector of ['.apt-home-tabs', '.apt-home-camera', '.apt-home-chat']) assert.equal(await visible(selector), false, selector + ' hidden in Setup');
      assert.equal(await page.locator('.apt-home-controls > :not([data-apt-setup-inspector])').evaluateAll(nodes => nodes.filter(node => node.getClientRects().length).length), 0);
      assert.equal(await page.getByRole('toolbar', { name: 'Room view', exact: true }).isVisible(), true);
      assert.equal(await page.getByRole('group', { name: 'Apartment edit orbit controls', exact: true }).count(), 0);
      assert.equal(await page.getByRole('button', { name: 'Cloud view', exact: true }).isDisabled(), true);
      assert.equal(await saveButton().isDisabled(), true); assert.equal(await draft(), originalDraft);
      return { tools: labels.map(([id]) => id), column: 'unified-controls', hidden: ['tabs', 'camera', 'chat'] };
    });

    await check('every Echo rail item opens its existing tool without a dirty draft', async () => {
      const seen = {};
      const expect = {
        sockets: /Select a marker or target directly in 3D/, names: /Renames stay in Apartment; Home Assistant keeps its own names/,
        targets: /Surfaces lights can be pointed at by name/, zones: /edit room zones/i, position: /Mapped fixtures · 2/,
        links: /Match each light in the model to the Home Assistant light that controls it/, cameras: /Placed by hand · not calibrated/,
      };
      for (const [id, label] of TOOLS) {
        await shell.tool(id); await page.waitForFunction(label => document.querySelector('[data-apt-setup-inspector] .apt-ui-title')?.textContent === label, label);
        const text = await shell.inspector().innerText();
        assert.match(text, expect[id], id); seen[id] = true;
        assert.equal(await shell.rail().locator(`[data-setup-tool="${id}"]`).getAttribute('aria-pressed'), 'true');
      }
      await shell.tool('names');
      assert.equal(await page.getByRole('textbox', { name: 'Name for Authored ceiling A', exact: true }).inputValue(), 'Authored ceiling A');
      await shell.tool('cameras');
      assert.equal(await page.getByRole('button', { name: 'Calibrate Authored camera', exact: true }).isDisabled(), false);
      await shell.tool('links');
      assert.deepEqual(await shell.inspector().locator('[data-setup-link]').evaluateAll(rows => rows.map(row => [row.dataset.setupLink, row.dataset.linkState])),
        [['authored-ceiling-a', 'linked'], ['authored-ceiling-b', 'linked'], ['authored-lamp', 'linked']]);
      assert.match(await shell.inspector().locator('[data-setup-links-note="offline"]').innerText(), /Home Assistant is offline. You can change matches; Flash needs a connection/);
      for (const name of ['Authored ceiling A', 'Authored ceiling B', 'Authored lamp']) assert.equal(await page.getByRole('button', { name: 'Flash ' + name, exact: true }).isDisabled(), true);
      assert.equal(await saveButton().isDisabled(), true); assert.equal(await draft(), originalDraft);
      return { opened: Object.keys(seen), dirty: false };
    });

    await check('Setup is a pressed toggle: pressing it again leaves like Done and brings the column back', async () => {
      await shell.pressSetup(); await shell.rail().waitFor({ state: 'detached' });
      await shell.waitSetupState('off');
      assert.equal(await shell.inspector().count(), 0);
      assert.equal(await visible('.apt-home-tabs'), true);
      await shell.openSetup(); await page.locator('[data-apt-setup-inspector] .apt-setup-done').click(); await shell.rail().waitFor({ state: 'detached' });
      assert.equal(await draft(), originalDraft);
      return { toggle_exits: true, done_exits: true };
    });

    await check('Escape leaves Fixture position first, then Setup', async () => {
      await shell.openSetup(); await shell.tool('position');
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-editor-tool');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('[data-apt-setup-inspector] .apt-ui-title')?.textContent === 'Devices and sockets');
      await page.keyboard.press('Escape'); await shell.rail().waitFor({ state: 'detached' });
      await shell.waitSetupState('off');
      assert.equal(await draft(), originalDraft);
      return { first: 'tool', second: 'setup' };
    });

    await check('Home Assistant connected: Flash sends exactly one guarded short flash, and is refused with zero dispatch under Travel Mode on or unknown', async () => {
      const flash = name => page.getByRole('button', { name: 'Flash ' + name, exact: true });
      ha.goOnline();
      await page.waitForFunction(() => window.__HOME_TRAVEL_MODE_STATE === 'off', null, { timeout: 30000 });
      await shell.openSetup(); await shell.tool('links');
      assert.equal(await flash('Authored ceiling A').isDisabled(), false);
      await flash('Authored ceiling A').click();
      await page.locator('[data-setup-link="authored-ceiling-a"] [data-flash-result="sent"]').waitFor();
      assert.deepEqual(ha.calls, [{ domain: 'light', service: 'turn_on', service_data: { flash: 'short' }, target: { entity_id: 'light.authored_ceiling_a' } }]);
      // Home Assistant turns Travel Mode on before the app hears about it:
      // the button is still offered, and the guard refuses at dispatch.
      ha.setTravel('on', { announce: false });
      await flash('Authored ceiling B').click();
      await page.locator('[data-setup-link="authored-ceiling-b"] [data-flash-result="refused"]').waitFor();
      assert.match(await page.locator('[data-setup-link="authored-ceiling-b"] [data-flash-result]').innerText(), /Travel Mode is on/);
      ha.setTravel('on');
      await page.waitForFunction(() => window.__HOME_TRAVEL_MODE_STATE === 'on');
      assert.equal(await flash('Authored ceiling B').isDisabled(), true);
      assert.match(await page.locator('[data-setup-links-note="travel"]').innerText(), /Travel Mode is on, so Flash is refused/);
      ha.setTravel(null);
      await page.waitForFunction(() => window.__HOME_TRAVEL_MODE_STATE === null);
      assert.equal(await flash('Authored lamp').isDisabled(), true);
      assert.match(await page.locator('[data-setup-links-note="travel"]').innerText(), /unknown, so Flash is refused/);
      assert.equal(ha.calls.length, 1, 'only the first Flash reached Home Assistant');
      // Re-matching from the registry changes only the link and sends nothing.
      const lamp = () => page.evaluate(() => window.__havApartmentDebug.modelSnapshot().devices.find(device => device.id === 'authored-lamp'));
      const before = await lamp();
      await page.getByRole('combobox', { name: 'Home Assistant light for Authored lamp', exact: true }).selectOption('light.authored_spare');
      await page.waitForFunction(() => window.__havApartmentDebug.modelSnapshot().devices.find(device => device.id === 'authored-lamp').ha_entity_id === 'light.authored_spare');
      const after = await lamp();
      assert.deepEqual(after.pos, before.pos); assert.deepEqual(after.fixture_calibration ?? null, before.fixture_calibration ?? null);
      const options = await page.getByRole('combobox', { name: 'Home Assistant light for Authored ceiling A', exact: true }).locator('option').evaluateAll(nodes => nodes.map(node => node.value));
      assert.deepEqual(options, ['', 'light.authored_ceiling_a', 'light.authored_lamp'], 'lights linked elsewhere are not offered: no new duplicates');
      assert.equal(ha.calls.length, 1);
      ha.setTravel('off'); await page.locator('[data-apt-setup-inspector] .apt-setup-done').click(); await shell.rail().waitFor({ state: 'detached' });
      return { flash_calls: ha.calls, refused: ['travel-on-at-dispatch', 'travel-on', 'travel-unknown'], rematch_geometry_unchanged: true };
    });

    await check('Calibrate in Cameras and sensors leaves Setup and opens the existing calibration for that camera', async () => {
      await shell.openSetup(); await shell.tool('cameras');
      await page.getByRole('button', { name: 'Calibrate Authored camera', exact: true }).click();
      await page.getByRole('button', { name: /^snapshot of authored_study/ }).waitFor({ timeout: 20000 });
      assert.equal(await shell.rail().count(), 0);
      assert.equal(server.log.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false, 'starting calibration writes nothing');
      return { camera: 'authored-camera', overlay: 'HomeApartmentCalibrate', writes: 0 };
    });

    await shell.selectHome('victoria');
    receipt.boot_and_echo_seconds = Math.round((Date.now() - started) / 100) / 10;
    await check('Victoria lists every tool, opens its registration editors and states why four are unavailable', async () => {
      await shell.openSetup();
      const reasons = {};
      for (const [id, label] of TOOLS) {
        await shell.tool(id); await page.waitForFunction(label => document.querySelector('[data-apt-setup-inspector] .apt-ui-title')?.textContent === label, label);
        const available = await shell.rail().locator(`[data-setup-tool="${id}"]`).getAttribute('data-available');
        if (['names', 'targets', 'zones'].includes(id)) {
          assert.equal(available, 'false', id);
          reasons[id] = await shell.inspector().locator('[data-setup-unavailable] .apt-ui-copy').innerText();
          assert.ok(reasons[id].length > 20, id + ' states a reason');
          assert.equal(await shell.inspector().locator('[data-apt-edit-ui="inspector"] fieldset').count(), 0);
        } else assert.equal(available, 'true', id);
      }
      await shell.tool('links');
      assert.match(await shell.inspector().locator('[data-setup-links-note="registration"]').innerText(), /Each is matched when its fixture is commissioned/);
      const rows = await shell.inspector().locator('[data-setup-link]').evaluateAll(nodes => nodes.map(node => node.dataset.linkState));
      assert.equal(rows.length, 6); assert.ok(rows.every(state => state === 'not_linked'), 'every Victoria socket and fixture reads Not linked');
      assert.equal(await shell.inspector().locator('[data-setup-link] select, [data-setup-link] button').count(), 0, 'no picker and no Flash');
      await shell.tool('position'); await shell.inspector().locator('[data-equipment-kinds="fixture"]').waitFor();
      await shell.tool('cameras'); await shell.inspector().locator('[data-equipment-kinds="camera"]').waitFor();
      assert.equal(await shell.inspector().locator('[data-equipment-selected]').getAttribute('data-equipment-selected'), 'victoria-den-oak-d-pro-01');
      await shell.tool('sockets'); await shell.inspector().getByRole('button', { name: 'Add socket', exact: true }).waitFor();
      await page.locator('[data-apt-setup-inspector] .apt-setup-done').click(); await shell.rail().waitFor({ state: 'detached' });
      assert.equal(hash(fs.readFileSync(rooms.registrationFile)), registration, 'opening Victoria tools changes no registration bytes');
      return { unavailable: Object.keys(reasons), available: ['sockets', 'position', 'links', 'cameras'], victoria_link_rows: 6 };
    });

    }
    if (phase === 'dock' || phase === 'dock-collapsed') {
    await check('the camera dock is hidden in Setup and returns exactly as it was in each presentation', async () => {
      const dockState = () => page.evaluate(() => ({ dock: document.querySelector('.apt-home-shell').dataset.dock,
        expanded: document.querySelector('.apt-ui-dock-header button[aria-pressed]')?.getAttribute('aria-pressed'),
        open: document.querySelector('.apt-ui-dock-header button[aria-expanded]')?.getAttribute('aria-expanded') }));
      const states = [];
      // Keep every original transition, but avoid five editor round trips in
      // one hosted-browser deadline. Each phase also proves the default state.
      const transitions = phase === 'dock'
        ? [[null, 'default'], ['Expand camera dock', 'expanded'], ['Restore camera dock size', 'default']]
        : [[null, 'default'], ['Collapse camera dock', 'collapsed'], ['Show camera dock', 'default']];
      for (const [action, expected] of transitions) {
        receipt.dock_transition = { action, expected }; save();
        if (action) await page.getByRole('button', { name: action, exact: true }).click();
        await page.waitForFunction(expected => document.querySelector('.apt-home-shell').dataset.dock === expected, expected);
        const before = await dockState(); assert.equal(await visible('.apt-home-camera'), true);
        await shell.openSetup();
        assert.equal(await visible('.apt-home-camera'), false); assert.deepEqual((await dockState()).dock, before.dock);
        await page.locator('[data-apt-setup-inspector] .apt-setup-done').click(); await shell.rail().waitFor({ state: 'detached' });
        await shell.waitSetupState('off');
        assert.deepEqual(await dockState(), before); assert.equal(await visible('.apt-home-camera'), true);
        states.push(before.dock);
      }
      assert.deepEqual([...new Set(states)].sort(), phase === 'dock' ? ['default', 'expanded'] : ['collapsed', 'default']);
      return { states };
    });

    }
    if (phase === 'phone') {
    // The Victoria room at phone size with a coarse pointer in Dark appearance.
    const phone = page, phoneShell = shell;
    await check('phone and Dark appearance: a narrow rail over the room, the inspector in the sheet, 44 px targets, readable text', async () => {
      assert.equal(await page.locator('.apt-home-shell').getAttribute('data-theme'), 'dark');
      await page.setViewportSize({ width: 390, height: 844 });
      await (await page.context().newCDPSession(page)).send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
      assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), true);
      await phoneShell.openSetup();
      const layout = await phone.evaluate(() => {
        const rail = document.querySelector('.apt-setup-rail').getBoundingClientRect(), stage = document.querySelector('.apt-home-stage').getBoundingClientRect();
        const buttons = [...document.querySelectorAll('.apt-setup-rail button, [data-apt-setup-inspector] .apt-setup-header button')].map(node => node.getBoundingClientRect().height);
        const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number).map(c => { c /= 255; return c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; });
        const lum = value => { const [r, g, b] = rgb(value); return .2126 * r + .7152 * g + .0722 * b; };
        const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + .05) / (y + .05); };
        const title = document.querySelector('[data-apt-setup-inspector] .apt-ui-title');
        let background = 'rgb(0, 0, 0)';
        for (let node = title; node; node = node.parentElement) { const color = getComputedStyle(node).backgroundColor; if (!/rgba\(.*, 0\)$|transparent/.test(color)) { background = color; break; } }
        return { railWidth: rail.width, railShare: rail.height / stage.height, minTarget: Math.min(...buttons),
          inSheet: !!document.querySelector('.apt-home-panel [data-apt-setup-inspector]'), overflowX: document.documentElement.scrollWidth - innerWidth,
          titleContrast: contrast(getComputedStyle(title).color, background),
          // The rail sits on the room's dark glass in both themes.
          railContrast: Math.min(...[...document.querySelectorAll('.apt-setup-rail button')].map(node => contrast(getComputedStyle(node).color, 'rgb(8, 9, 11)'))) };
      });
      assert.ok(layout.railWidth <= 181 && layout.railShare <= .465, JSON.stringify(layout));
      assert.ok(layout.minTarget >= 44, 'coarse targets are at least 44 px: ' + layout.minTarget);
      assert.equal(layout.inSheet, true); assert.ok(layout.overflowX <= 0, 'no horizontal page scroll');
      assert.ok(layout.titleContrast >= 4.5, 'header text contrast ' + layout.titleContrast);
      assert.ok(layout.railContrast >= 4.5, 'rail text stays readable on the dark room glass in light theme: ' + layout.railContrast);
      await phoneShell.tool('position'); await phone.waitForFunction(() => document.querySelector('[data-apt-setup-inspector] .apt-ui-title')?.textContent === 'Fixture position');
      return layout;
    });

    }
    for (const [file, expected] of Object.entries(packHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack bytes stay exact');
    assert.equal(hash(fs.readFileSync(rooms.registrationFile)), registration);
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
}
