// PROPOSED fields, pending the owner's approval (APT-U02, APT-U06, APT-U07).
// With the Setup proposal flag off, Names has no Label and Cameras has no
// placer, so nothing new is written. With it on: Names labels, Reset and the
// duplicate flag; a hand-placed camera pose that leaves calibrated intrinsics
// and extrinsics byte-identical; an mmWave sensor that reads "Ordered · not
// connected". Authored rooms only, one Home boot per flag state, no commands.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { installSetupRooms, authoredEchoModel, startSetupServer, openSetupContext, browserOptions, homeShell, deskRoomGlb } from './fixtures/apartment-setup-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Authored, synthetic calibration values: only their bytes matter here.
const INTRINSICS = { K: [[600, 0, 320], [0, 600, 180], [0, 0, 1]], dist: [0, 0, 0, 0, 0], image_size: [640, 360], rms_px: 0.4 };
const EXTRINSICS = { R: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], t: [0.2, 0.2, 2.2], rms_px: 1.1, pairs: 9 };
const calibratedEcho = seed => {
  const model = authoredEchoModel(seed);
  model.devices = model.devices.map(device => device.type === 'camera'
    ? { ...device, camera: { ...device.camera, intrinsics: INTRINSICS, extrinsics: EXTRINSICS } } : device);
  return model;
};

test('proposed Setup fields stay behind the flag; with it, labels, hand placement and mmWave save without touching calibration', { timeout: 200000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-setup-proposal-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const glb = deskRoomGlb();
  const rooms = installSetupRooms(run, { echoModel: calibratedEcho, echoOverrides: { 'mesh.glb': glb, 'collision.glb': glb } });
  const receipt = { schema: 'home-apartment-setup-proposal-browser-1', authored_room: true, household_data: false, screenshots: false,
    physical_commands: 0, proposed_fields_pending_owner: true, checks: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let server, browser, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('170s proposal browser deadline at: ' + receipt.current_stage)); }, 170000); });
  const check = async (name, operation) => { receipt.current_stage = name; save(); receipt.checks.push({ name, ...(await operation()) }); save(); };
  async function proof() {
    server = await startSetupServer(rooms);
    browser = await chromium.launch(receipt.browser);
    const off = await openSetupContext(browser, server.origin, server.log), offShell = homeShell(off.page, server.origin);
    await offShell.boot('1', 'echo');
    await check('flag off: Names shows no Label and Cameras has no placer, so nothing new can be written', async () => {
      await offShell.openSetup(); await offShell.tool('names');
      assert.equal(await off.page.getByRole('textbox', { name: /^Room label for / }).count(), 0);
      assert.equal(await off.page.locator('[data-setup-proposal]').count(), 0);
      await offShell.tool('cameras');
      assert.equal(await off.page.locator('[data-device-placer]').count(), 0);
      await off.context.close();
      return { label: false, placer: false };
    });

    const { page, context } = await openSetupContext(browser, server.origin, server.log);
    const shell = homeShell(page, server.origin);
    await page.goto(server.origin + '/?apartmentRedesign=1&worldZoom=off&setupProposal=1', { waitUntil: 'domcontentloaded' });
    await shell.waitSite('echo');
    const draftDoc = () => page.evaluate(() => JSON.parse(localStorage.getItem('apartment3d.modelDraft') || 'null'));
    const modelNow = () => page.evaluate(() => window.__havApartmentDebug.modelSnapshot());
    const screen = point => page.evaluate(point => {
      const engine = window.__APT_ENGINE, host = engine.renderer.domElement.parentElement.getBoundingClientRect();
      const projected = engine.picking.projectToScreen(engine.apartmentRoot.localToWorld(engine.picking.raycaster.ray.origin.clone().set(...point)));
      return { x: host.left + projected.x, y: host.top + projected.y };
    }, point);

    await check('Names: label, Was and Reset, duplicate flagged but allowed; Home Assistant names untouched', async () => {
      await shell.openSetup(); await shell.tool('names');
      await page.locator('[data-setup-proposal="names"]').waitFor();
      const label = page.getByRole('textbox', { name: 'Room label for Authored ceiling A', exact: true });
      await label.fill('Reading lamp, long'); assert.equal(await label.inputValue(), 'Reading la', 'at most 10 characters: ' + await label.inputValue());
      await label.fill('Desk');
      const name = page.getByRole('textbox', { name: 'Name for Authored ceiling A', exact: true });
      await name.fill('Authored ceiling B');
      assert.match(await page.locator('[data-setup-name="authored-ceiling-a"] [data-name-note]').innerText(), /Another light has this name/);
      await name.fill('Desk light');
      assert.match(await page.locator('[data-setup-name="authored-ceiling-a"] [data-name-note]').innerText(), /Was “Authored ceiling A”/);
      const device = (await modelNow()).devices.find(item => item.id === 'authored-ceiling-a');
      assert.deepEqual([device.name, device.label, device.ha_entity_id], ['Desk light', 'Desk', 'light.authored_ceiling_a'], 'renames never touch the Home Assistant link');
      await page.getByRole('button', { name: 'Reset Authored ceiling A', exact: true }).click();
      const reset = (await modelNow()).devices.find(item => item.id === 'authored-ceiling-a');
      assert.equal(reset.name, 'Authored ceiling A'); assert.equal('label' in reset, false);
      await label.fill('Desk');
      return { label: 'Desk', reset: true, duplicate_flagged: true };
    });

    await check('a hand-placed camera saves pose and placement; calibrated intrinsics and extrinsics stay byte-identical', async () => {
      const before = JSON.stringify((await modelNow()).devices.find(item => item.id === 'authored-camera').camera);
      await shell.tool('cameras');
      await page.locator('[data-device-placer="list"]').waitFor();
      assert.match(await page.locator('[data-placer-device="authored-camera"]').innerText(), /Calibrated/);
      await page.getByRole('button', { name: 'Place Authored camera by hand', exact: true }).click();
      await page.locator('[data-device-placer="editing"]').getByRole('button', { name: 'Move', exact: true }).click();
      await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'mesh' && !!window.__APT_ENGINE.modes.getMesh());
      const wall = await screen([0.02, 2.2, 1.9]); await page.mouse.click(wall.x, wall.y);
      await page.locator('[data-device-placer="editing"]').waitFor();
      let draft = JSON.parse(await page.locator('[data-device-placer="editing"]').getAttribute('data-draft'));
      assert.ok(Math.abs(draft.pos[0] - 0.04) < 0.03 && Math.abs(draft.pos[2] - 1.9) < 0.05, JSON.stringify(draft));
      await page.getByRole('button', { name: 'Point at a spot', exact: true }).click();
      const desk = await screen([2.05, 3.5, 0.74]); await page.mouse.click(desk.x, desk.y);
      await page.locator('[data-device-placer="editing"]').waitFor();
      draft = JSON.parse(await page.locator('[data-device-placer="editing"]').getAttribute('data-draft'));
      assert.ok(draft.pitch_deg < 0 && draft.yaw_rad > 0, 'points down toward the desk: ' + JSON.stringify(draft));
      await page.getByRole('slider', { name: 'Lens', exact: true }).fill('110');
      assert.match(await page.locator('[data-placer-covers]').innerText(), /Authored wall art|Authored reading point|Authored ceiling/);
      await page.getByRole('button', { name: 'Save changes', exact: true }).click();
      await page.waitForFunction(() => !!JSON.parse(localStorage.getItem('apartment3d.modelDraft') || 'null')?.devices?.find(device => device.id === 'authored-camera')?.placement);
      const camera = (await draftDoc()).devices.find(item => item.id === 'authored-camera');
      assert.equal(JSON.stringify(camera.camera), before, 'calibration bytes unchanged');
      assert.deepEqual(Object.keys(camera.placement).sort(), ['calibration_ref', 'hfov_deg', 'method', 'mount', 'pitch_deg', 'placed_at', 'vfov_deg']);
      assert.equal(camera.placement.hfov_deg, 110); assert.equal(camera.placement.mount, 'wall');
      await page.locator('[data-placer-device="authored-camera"]').waitFor();
      assert.match(await page.locator('[data-placer-device="authored-camera"]').innerText(), /Moved since calibration · recalibrate/);
      return { placement: camera.placement, calibration_identical: true };
    });

    await check('an ordered mmWave sensor is placed with its field and range and reads "Ordered · not connected"', async () => {
      await page.getByRole('button', { name: '+ mmWave sensor', exact: true }).click();
      await page.locator('[data-device-placer="placing"]').getByRole('group', { name: 'Mounted on' }).getByRole('button', { name: 'Ceiling', exact: true }).click();
      const floor = await screen([3.2, 1, 0]); await page.mouse.click(floor.x, floor.y);
      await page.locator('[data-device-placer="editing"]').waitFor();
      const draft = JSON.parse(await page.locator('[data-device-placer="editing"]').getAttribute('data-draft'));
      assert.ok(Math.abs(draft.pos[2] - 2.45) < 0.01 && draft.mount === 'ceiling', JSON.stringify(draft));
      await page.getByRole('slider', { name: 'Range', exact: true }).fill('8');
      await page.getByRole('textbox', { name: 'Sensor name', exact: true }).fill('Study mmWave');
      await page.getByRole('button', { name: 'Save sensor', exact: true }).click();
      await page.waitForFunction(() => JSON.parse(localStorage.getItem('apartment3d.modelDraft') || 'null')?.devices?.some(device => device.type === 'presence_sensor'));
      const sensor = (await draftDoc()).devices.find(item => item.type === 'presence_sensor');
      assert.deepEqual([sensor.name, sensor.ordered, sensor.ha_entity_ids, sensor.placement.range_m, sensor.placement.mount], ['Study mmWave', true, [], 8, 'ceiling']);
      assert.match(await page.locator(`[data-placer-device="${sensor.id}"]`).innerText(), /Ordered · not connected/);
      assert.equal((await draftDoc()).devices.find(item => item.id === 'authored-ceiling-a').label, 'Desk', 'the label saved with the model');
      await context.close();
      return { sensor: sensor.id, status: 'Ordered · not connected' };
    });
    assert.deepEqual(server.log.forbidden, []); assert.deepEqual(server.log.missing, []); assert.deepEqual(server.log.errors, []);
    assert.equal(server.log.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false, 'no command, session or service write');
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); save(); throw error; }
  finally {
    clearTimeout(timer);
    for (const close of [() => browser?.close(), () => server?.close()]) { try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push(error.message); } }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
