// APT-U09: the Setup rail saves exactly what the legacy editor saved.
// The same native edits run once through the legacy toolbar (classic layout)
// and once through the rail (unified layout), each pass starting from the same
// authored rooms with a fixed clock. Echo's persisted apartment_model draft
// and Victoria's registration PUT body and stored bytes must be identical.
// A registration conflict through the rail keeps the owner's session edits.
// One page load per pass: each Home boot compiles the app in the browser,
// which dominates hosted-CI time.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { installSetupRooms, resetRegistration, startSetupServer, openSetupContext, browserOptions, homeShell, hash } from './fixtures/apartment-setup-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLOCK = '2026-10-19T12:00:00.000Z';
const REGISTRATION = '/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration';
const SAVE_EQUIPMENT = 'button[title="Save equipment positions for this Victoria Den scan revision"]';

test('rail saves are byte-identical to legacy saves and a registration conflict keeps session edits', { timeout: 200000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-setup-saves-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const rooms = installSetupRooms(run), initialRegistration = fs.readFileSync(rooms.registrationFile, 'utf8');
  const receipt = { schema: 'home-apartment-setup-saves-browser-1', authored_room: true, household_data: false, screenshots: false,
    physical_commands: 0, checks: [], stages: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let server, browser, timer;
  const started = Date.now();
  const stage = name => { receipt.current_stage = name; receipt.stages.push({ name, at: Math.round((Date.now() - started) / 100) / 10 }); save(); };
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('180s Setup saves browser deadline at: '
    + receipt.current_stage + ' · ' + JSON.stringify(receipt.stages))); }, 180000); });
  const check = async (name, operation) => { stage(name); receipt.checks.push({ name, ...(await operation()) }); save(); };
  const tape = async (field, value) => { await field.fill(value); await field.press('Enter'); };

  // One page per layout, Echo first then Victoria in the same page.
  async function openPass(layout) {
    const { page, context } = await openSetupContext(browser, server.origin, server.log);
    const shell = homeShell(page, server.origin);
    const toolbar = page.locator('[data-apt-edit-ui="toolbar"]');
    const enter = async () => {
      if (layout === 'rail') await shell.openSetup();
      else await page.getByRole('button', { name: 'edit', exact: true }).click();
      await toolbar.waitFor();
      // Edits stamp updated_at; one fixed clock makes both passes comparable.
      await page.clock.setFixedTime(new Date(CLOCK));
    };
    if (layout === 'rail') await shell.boot('1', 'echo');
    else {
      await page.goto(server.origin + '/?apartmentRedesign=0&worldZoom=off', { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Open apartment view', exact: true }).click({ timeout: 45000 });
      await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'echo', null, { timeout: 45000 });
    }
    const toVictoria = async () => {
      // Real time again while the next home loads, then the same fixed clock.
      await page.clock.setFixedTime(new Date());
      if (layout === 'rail') { await page.locator('[data-apt-setup-inspector] .apt-setup-done').click(); await shell.selectHome('victoria'); }
      else {
        await toolbar.getByRole('button', { name: 'done', exact: true }).click();
        await page.evaluate(() => window.__HOME_APARTMENT_SITE_API.selectHome('victoria'));
        await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'victoria', null, { timeout: 45000 });
      }
      await enter();
    };
    const open = async (rail, legacy) => layout === 'rail' ? shell.tool(rail) : toolbar.getByRole('button', { name: legacy, exact: true }).click();
    await enter();
    return { page, context, shell, toolbar, open, toVictoria, palette: page.locator('[data-apt-edit-ui="targets-and-palette"]'),
      inspector: page.locator('[data-apt-edit-ui="inspector"]') };
  }

  async function echoEdits(p, layout) {
    const { page, palette, inspector, open } = p;
    await open('position', 'fixture position');
    await palette.getByRole('button', { name: /Authored ceiling A/ }).click();
    const walls = inspector.locator('select:has(option[value="west"])');
    await walls.nth(0).selectOption('west'); await walls.nth(1).selectOption('south');
    await tape(inspector.getByRole('textbox', { name: 'distance', exact: true }).nth(0), '1.25 m');
    await tape(inspector.getByRole('textbox', { name: 'distance', exact: true }).nth(1), '98 cm');
    await tape(inspector.getByRole('textbox', { name: 'floor → ceiling', exact: true }), `8' 2"`);
    await tape(inspector.getByRole('textbox', { name: 'ceiling → fixture bottom', exact: true }), '15 cm');
    await page.waitForFunction(() => /calibrated/.test(document.querySelector('[data-apt-edit-ui="inspector"]').innerText));
    // The rename goes through Names in the rail and through the device field
    // in the legacy inspector: the same name field either way.
    if (layout === 'rail') { await open('names'); await page.getByRole('textbox', { name: 'Name for Authored ceiling B', exact: true }).fill('Reading light'); }
    else { await palette.getByRole('button', { name: /Authored ceiling B/ }).click(); await inspector.locator('input:not([type])').first().fill('Reading light'); }
    await open('zones', 'zones');
    await palette.getByRole('button', { name: /Authored study/ }).click();
    await inspector.getByRole('spinbutton', { name: 'zone authored-study corner 3 x', exact: true }).fill('3.9');
    // Named aims are redesigned in the rail (APT-U04) and saved through their
    // own tests; the re-homed editors are what must save identically here.
    await p.toolbar.getByRole('button', { name: 'undo', exact: true }).click();
    await p.toolbar.getByRole('button', { name: 'redo', exact: true }).click();
    const saveButton = p.toolbar.locator('button[title*="Home Assistant"]');
    assert.equal(await saveButton.isDisabled(), false); await saveButton.click();
    await page.waitForFunction(() => !!localStorage.getItem('apartment3d.modelDraft'));
    return page.evaluate(() => localStorage.getItem('apartment3d.modelDraft'));
  }

  async function victoriaEdits(p, layout) {
    const { page, palette, inspector, open } = p;
    const record = async (rail, legacy, name) => { await open(rail, legacy); await palette.getByRole('button', { name, exact: false }).first().click(); };
    await record('sockets', 'select', 'Authored socket 2');
    await inspector.getByRole('textbox', { name: 'Equipment label', exact: true }).fill('Authored socket two');
    await tape(inspector.getByRole('textbox', { name: 'wall 1 → mounting point', exact: true }), '0.95 m');
    await tape(inspector.getByRole('textbox', { name: 'floor → mounting point', exact: true }), '2.31 m');
    await inspector.getByRole('combobox', { name: 'Measurement method', exact: true }).selectOption('tape');
    await palette.getByRole('button', { name: 'Add socket', exact: true }).click();
    await inspector.getByRole('combobox', { name: 'Mounting type', exact: true }).selectOption('wall');
    if (layout === 'rail') await record('cameras', null, 'OAK-D Pro'); else await palette.getByRole('button', { name: /OAK-D Pro/ }).click();
    await inspector.getByRole('combobox', { name: 'Camera pointing direction', exact: true }).selectOption('left');
    if (layout === 'rail') await record('position', null, 'proposed fixture');
    else { await palette.locator('summary', { hasText: 'Fixtures at sockets' }).click(); await palette.getByRole('button', { name: /proposed fixture/ }).first().click(); }
    await inspector.getByRole('combobox', { name: 'Fixture installation status', exact: true }).selectOption('installed');
    await p.toolbar.locator(SAVE_EQUIPMENT).click();
    await page.waitForFunction(selector => /saved/.test(document.querySelector(selector)?.textContent || ''), SAVE_EQUIPMENT);
    return { puts: server.log.registrationPuts.splice(0), stored: fs.readFileSync(rooms.registrationFile, 'utf8') };
  }

  async function proof() {
    server = await startSetupServer(rooms, { allowRegistrationWrites: true });
    browser = await chromium.launch(receipt.browser);
    stage('legacy pass: boot'); const legacyPass = await openPass('legacy');
    stage('legacy pass: Echo'); const legacy = { echo: await echoEdits(legacyPass, 'legacy') };
    stage('legacy pass: Victoria'); await legacyPass.toVictoria(); legacy.victoria = await victoriaEdits(legacyPass, 'legacy');
    await legacyPass.context.close();
    resetRegistration(rooms);
    assert.equal(fs.readFileSync(rooms.registrationFile, 'utf8'), initialRegistration, 'the rail pass starts from the same registration');
    stage('rail pass: boot'); const railPass = await openPass('rail');
    stage('rail pass: Echo'); const rail = { echo: await echoEdits(railPass, 'rail') };
    stage('rail pass: Victoria'); await railPass.toVictoria(); rail.victoria = await victoriaEdits(railPass, 'rail');

    await check('Echo: fixture position, rename, zone corner, undo and redo save the same apartment_model document', async () => {
      const doc = JSON.parse(rail.echo);
      assert.equal(doc.devices.find(device => device.id === 'authored-ceiling-a').fixture_calibration.status, 'calibrated');
      assert.equal(doc.devices.find(device => device.id === 'authored-ceiling-b').name, 'Reading light');
      assert.equal(doc.zones[0].floor_polygon[2][0], 3.9);
      assert.equal(rail.echo, legacy.echo, 'byte-identical persisted Echo document');
      return { bytes: rail.echo.length, sha256: hash(rail.echo), identical: true };
    });
    await check('Victoria: socket tapes, label, method, Add socket, camera direction and fixture status save the same registration', async () => {
      assert.equal(legacy.victoria.puts.length, 1); assert.equal(rail.victoria.puts.length, 1);
      assert.equal(rail.victoria.puts[0], legacy.victoria.puts[0], 'byte-identical registration PUT body');
      assert.equal(rail.victoria.stored, legacy.victoria.stored, 'byte-identical stored registration');
      const stored = JSON.parse(rail.victoria.stored);
      assert.equal(stored.revision, 1); assert.equal(stored.equipment.length, 8);
      assert.equal(stored.equipment.find(item => item.id === 'victoria-den-mount-02').label, 'Authored socket two');
      return { bytes: rail.victoria.stored.length, sha256: hash(rail.victoria.stored), revision: stored.revision, identical: true };
    });
    await check('a registration conflict through the rail keeps the session edit and overwrites nothing', async () => {
      const { page, inspector, palette, toolbar, shell } = railPass;
      // Another writer saves first through the same gateway store.
      const other = JSON.parse(fs.readFileSync(rooms.registrationFile, 'utf8'));
      const model = Core.updateModelEquipment(Core.toModel(other), 'victoria-den-mount-01', { label: 'Changed elsewhere' });
      const status = await page.evaluate(async ({ url, body }) => (await fetch(url, { method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-Home-Equipment-CSRF': '1' }, body })).status, { url: REGISTRATION, body: JSON.stringify(Core.toDocument(model)) });
      assert.equal(status, 200);
      const elsewhere = fs.readFileSync(rooms.registrationFile, 'utf8');
      await shell.tool('sockets'); await palette.getByRole('button', { name: 'Authored socket 4', exact: false }).first().click();
      await inspector.getByRole('textbox', { name: 'Equipment label', exact: true }).fill('Session edit');
      await toolbar.locator(SAVE_EQUIPMENT).click();
      await page.waitForFunction(selector => /conflict/.test(document.querySelector(selector)?.textContent || ''), SAVE_EQUIPMENT);
      assert.equal(await inspector.getByRole('textbox', { name: 'Equipment label', exact: true }).inputValue(), 'Session edit');
      assert.match(await page.locator('[data-apt-setup-inspector] .apt-ui-sub').innerText(), /save conflict/);
      assert.equal(fs.readFileSync(rooms.registrationFile, 'utf8'), elsewhere, 'the other writer\'s revision is not overwritten');
      return { status: 409, session_edit_kept: true, stored_revision: JSON.parse(elsewhere).revision };
    });
    assert.deepEqual(server.log.forbidden, []); assert.deepEqual(server.log.missing, []); assert.deepEqual(server.log.errors, []);
    assert.ok(server.log.requests.filter(request => !['GET', 'HEAD'].includes(request.method)).every(request => request.method === 'PUT'
      && request.path === REGISTRATION), 'the registration CAS path is the only write');
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); save(); throw error; }
  finally {
    clearTimeout(timer);
    for (const close of [() => browser?.close(), () => server?.close()]) { try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push(error.message); } }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
