// APT-U09: the Setup rail saves exactly what the legacy editor saved.
// The same native edits run once through the legacy toolbar (classic layout)
// and once through the rail (unified layout), each on a fresh copy of the same
// authored rooms with a fixed clock. Echo's persisted apartment_model draft
// and Victoria's registration PUT body and stored bytes must be identical.
// A registration conflict through the rail keeps the owner's session edits.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { installSetupRooms, startSetupServer, openSetupContext, browserOptions, homeShell, hash } from './fixtures/apartment-setup-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CLOCK = '2026-10-19T12:00:00.000Z';

test('rail saves are byte-identical to legacy saves and a registration conflict keeps session edits', { timeout: 220000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-setup-saves-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-setup-saves-browser-1', authored_room: true, household_data: false, screenshots: false,
    physical_commands: 0, checks: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const servers = []; let browser, timer, victoriaRail;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('200s Setup saves browser deadline')); }, 200000); });
  const check = async (name, operation) => { receipt.current_stage = name; save(); receipt.checks.push({ name, ...(await operation()) }); save(); };

  // One fresh authored copy, server and page per pass, so both passes start
  // from the same bytes and neither sees the other's draft or revision.
  async function pass(label, site, layout) {
    const rooms = installSetupRooms(path.join(run, label));
    const server = await startSetupServer(rooms, { allowRegistrationWrites: true }); servers.push(server);
    const { page, context } = await openSetupContext(browser, server.origin, server.log, { site });
    const shell = homeShell(page, server.origin);
    if (layout === 'rail') { await shell.boot('1', site); await shell.openSetup(); }
    else {
      await page.goto(server.origin + '/?apartmentRedesign=0&worldZoom=off', { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Open apartment view', exact: true }).click();
      await page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === site, site, { timeout: 45000 });
      await page.getByRole('button', { name: 'edit', exact: true }).click();
    }
    const toolbar = page.locator('[data-apt-edit-ui="toolbar"]'); await toolbar.waitFor();
    // Edits stamp updated_at; one fixed clock from here on makes both passes comparable.
    await page.clock.setFixedTime(new Date(CLOCK));
    const open = async (rail, legacy) => layout === 'rail' ? shell.tool(rail) : toolbar.getByRole('button', { name: legacy, exact: true }).click();
    return { rooms, server, page, context, toolbar, open, palette: page.locator('[data-apt-edit-ui="targets-and-palette"]'),
      inspector: page.locator('[data-apt-edit-ui="inspector"]') };
  }
  const tape = async (field, value) => { await field.fill(value); await field.press('Enter'); };

  async function echoEdits(layout) {
    const p = await pass('echo-' + layout, 'echo', layout), { page, palette, inspector, open } = p;
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
    await open('targets', '+ target');
    await palette.getByRole('button', { name: /Authored wall art/ }).click();
    await inspector.getByRole('button', { name: 'x +5cm', exact: true }).click();
    await inspector.getByRole('spinbutton', { name: 'target z', exact: true }).fill('1.45');
    await p.toolbar.getByRole('button', { name: 'undo', exact: true }).click();
    await p.toolbar.getByRole('button', { name: 'redo', exact: true }).click();
    const saveButton = p.toolbar.locator('button[title*="Home Assistant"]');
    assert.equal(await saveButton.isDisabled(), false); await saveButton.click();
    await page.waitForFunction(() => !!localStorage.getItem('apartment3d.modelDraft'));
    const draft = await page.evaluate(() => localStorage.getItem('apartment3d.modelDraft'));
    await p.context.close();
    return { draft, writes: p.server.log.requests.filter(request => !['GET', 'HEAD'].includes(request.method)) };
  }

  async function victoriaEdits(layout, { keep = false } = {}) {
    const p = await pass('victoria-' + layout, 'victoria', layout), { page, palette, inspector, open } = p;
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
    const saveButton = p.toolbar.locator('button[title="Save equipment positions for this Victoria Den scan revision"]');
    await saveButton.click();
    await page.waitForFunction(() => /saved/.test(document.querySelector('button[title="Save equipment positions for this Victoria Den scan revision"]')?.textContent || ''));
    const result = { puts: [...p.server.log.registrationPuts], stored: fs.readFileSync(p.rooms.registrationFile, 'utf8'),
      writes: p.server.log.requests.filter(request => !['GET', 'HEAD'].includes(request.method)) };
    if (!keep) await p.context.close(); else result.pass = p;
    return result;
  }

  async function proof() {
    browser = await chromium.launch(receipt.browser);
    await check('Echo: fixture position, rename, zone corner, aim nudge, undo and redo save the same apartment_model document', async () => {
      const legacy = await echoEdits('legacy'), rail = await echoEdits('rail');
      assert.equal(legacy.writes.length, 0); assert.equal(rail.writes.length, 0, 'Echo saves stay a local draft with Home Assistant offline');
      const doc = JSON.parse(rail.draft);
      assert.equal(doc.devices.find(device => device.id === 'authored-ceiling-a').fixture_calibration.status, 'calibrated');
      assert.equal(doc.devices.find(device => device.id === 'authored-ceiling-b').name, 'Reading light');
      assert.equal(doc.zones[0].floor_polygon[2][0], 3.9);
      assert.equal(doc.targets.find(target => target.id === 'authored-wall-art').pos[2], 1.45);
      assert.equal(rail.draft, legacy.draft, 'byte-identical persisted Echo document');
      return { bytes: rail.draft.length, sha256: hash(rail.draft), identical: true };
    });
    await check('Victoria: socket tapes, label, method, Add socket, camera direction and fixture status save the same registration', async () => {
      const legacy = await victoriaEdits('legacy'), rail = await victoriaEdits('rail', { keep: true });
      assert.equal(legacy.puts.length, 1); assert.equal(rail.puts.length, 1);
      assert.equal(rail.puts[0], legacy.puts[0], 'byte-identical registration PUT body');
      assert.equal(rail.stored, legacy.stored, 'byte-identical stored registration');
      const stored = JSON.parse(rail.stored);
      assert.equal(stored.revision, 1); assert.equal(stored.equipment.length, 8);
      assert.equal(stored.equipment.find(item => item.id === 'victoria-den-mount-02').label, 'Authored socket two');
      victoriaRail = rail.pass;
      return { bytes: rail.stored.length, sha256: hash(rail.stored), revision: stored.revision, identical: true };
    });
    await check('a registration conflict through the rail keeps the session edit and overwrites nothing', async () => {
      const { page, inspector, palette, toolbar, rooms } = victoriaRail;
      // Another writer saves first through the same gateway store.
      const other = JSON.parse(fs.readFileSync(rooms.registrationFile, 'utf8'));
      const model = Core.updateModelEquipment(Core.toModel(other), 'victoria-den-mount-01', { label: 'Changed elsewhere' });
      const response = await page.evaluate(async body => { const r = await fetch('/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration', {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Home-Equipment-CSRF': '1' }, body }); return r.status; }, JSON.stringify(Core.toDocument(model)));
      assert.equal(response, 200);
      const elsewhere = fs.readFileSync(rooms.registrationFile, 'utf8');
      await shellTool(page, 'sockets'); await palette.getByRole('button', { name: 'Authored socket 4', exact: false }).first().click();
      await inspector.getByRole('textbox', { name: 'Equipment label', exact: true }).fill('Session edit');
      await toolbar.locator('button[title="Save equipment positions for this Victoria Den scan revision"]').click();
      await page.waitForFunction(() => /conflict/.test(document.querySelector('button[title="Save equipment positions for this Victoria Den scan revision"]')?.textContent || ''));
      assert.equal(await inspector.getByRole('textbox', { name: 'Equipment label', exact: true }).inputValue(), 'Session edit');
      assert.match(await page.locator('[data-apt-setup-inspector] .apt-ui-sub').innerText(), /save conflict/);
      assert.equal(fs.readFileSync(rooms.registrationFile, 'utf8'), elsewhere, 'the other writer\'s revision is not overwritten');
      return { status: 409, session_edit_kept: true, stored_revision: JSON.parse(elsewhere).revision };
    });
    for (const server of servers) {
      assert.deepEqual(server.log.forbidden, []); assert.deepEqual(server.log.missing, []); assert.deepEqual(server.log.errors, []);
      assert.ok(server.log.requests.filter(request => !['GET', 'HEAD'].includes(request.method)).every(request => request.method === 'PUT'
        && request.path === '/api/apartment/v1/sites/victoria/maps/victoria-den/rooms/den/registration'), 'the registration CAS path is the only write');
    }
  }
  const shellTool = (page, id) => page.locator(`.apt-setup-rail [data-setup-tool="${id}"]`).click();
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); save(); throw error; }
  finally {
    clearTimeout(timer);
    for (const close of [() => browser?.close(), ...servers.map(server => () => server.close())]) { try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push(error.message); } }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
