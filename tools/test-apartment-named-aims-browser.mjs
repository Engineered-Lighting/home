// APT-U04/U05: Setup › Named aims in the real Home shell and renderer, on an
// authored Echo room with a 1.70 × 0.60 m desk and two engineered fixtures.
// Runs the HANDOFF "Map a named aim" example with native input, then the
// save checks, Reach and Preview, the in-room handles, edit, Escape and
// Delete. A named aim is geometry only: no command, session or service write.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { installSetupRooms, startSetupServer, openSetupContext, browserOptions, homeShell, deskEchoModel, deskRoomGlb, DESK, hash } from './fixtures/apartment-setup-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Map a named aim on Echo: fit the desk, check, reach, preview, handles, save, edit, Escape and delete', { timeout: 145000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-named-aims-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const glb = deskRoomGlb();
  const rooms = installSetupRooms(run, { echoModel: deskEchoModel, echoOverrides: { 'mesh.glb': glb, 'collision.glb': glb } });
  const packHashes = rooms.packHashes();
  const receipt = { schema: 'home-apartment-named-aims-browser-1', authored_room: true, household_data: false, screenshots: false,
    physical_commands: 0, checks: [], browser: browserOptions() };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let server, browser, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('145s named-aim browser deadline')); }, 145000); });
  const check = async (name, operation) => { receipt.current_stage = name; save(); receipt.checks.push({ name, ...(await operation()) }); save(); };
  async function proof() {
    server = await startSetupServer(rooms);
    browser = await chromium.launch(receipt.browser);
    const { page } = await openSetupContext(browser, server.origin, server.log);
    const shell = homeShell(page, server.origin);
    await shell.boot('1', 'echo');
    const mapper = () => page.locator('[data-named-aims]');
    const button = name => shell.inspector().getByRole('button', { name, exact: true });
    const draftDoc = () => page.evaluate(() => JSON.parse(localStorage.getItem('apartment3d.modelDraft') || 'null'));
    const screen = point => page.evaluate(point => {
      const engine = window.__APT_ENGINE, host = engine.renderer.domElement.parentElement.getBoundingClientRect();
      const world = engine.apartmentRoot.localToWorld(engine.picking.raycaster.ray.origin.clone().set(...point));
      const projected = engine.picking.projectToScreen(world);
      return { x: host.left + projected.x, y: host.top + projected.y };
    }, point);
    const handle = key => page.evaluate(key => { const rect = document.querySelector(`[data-handle="${key}"]`)?.getBoundingClientRect();
      return rect && rect.width ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null; }, key);
    const drag = async (from, to) => { await page.mouse.move(from.x, from.y); await page.mouse.down(); await page.mouse.move(to.x, to.y, { steps: 8 }); await page.mouse.up(); };
    // The draft as the inspector shows it: size inputs and the position line.
    const shown = () => page.evaluate(() => {
      const root = document.querySelector('[data-named-aims="editing"]');
      const number = label => Number(root.querySelector(`input[aria-label="${label}"]`)?.value);
      const position = root.innerText.match(/Position · 5 cm steps · ([-\d.]+), ([-\d.]+), ([-\d.]+) m/).slice(1).map(Number);
      return { name: root.querySelector('input[aria-label="Named aim name"]').value, size: [number('Width'), number('Depth')], pos: position,
        type: root.querySelector('[aria-label="Type"] [aria-pressed="true"]')?.textContent };
    });
    const geometry = () => page.evaluate(() => JSON.parse(document.querySelector('[data-named-aims="editing"]').dataset.draft));
    const camera = () => page.evaluate(() => window.__APT_ENGINE.camera.position.toArray());
    const beams = () => page.evaluate(() => window.__APT_ENGINE.overlay.aimBeamGroup.children.length);

    await check('HANDOFF "Map a named aim": New, then a click on the desk top fits 1.70 × 0.60 m, suggests "Desk" and Table, and Reach lists who can light it', async () => {
      await shell.openSetup(); await shell.tool('targets');
      await mapper().filter({ hasText: 'Surfaces lights can be pointed at by name' }).waitFor();
      await button('New').click(); await page.locator('[data-named-aims="placing"]').waitFor();
      await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'mesh' && !!window.__APT_ENGINE.modes.getMesh());
      const top = await screen([DESK.x0 + 0.4, DESK.y0 + 0.25, DESK.h]);
      await page.mouse.click(top.x, top.y);
      await page.locator('[data-named-aims="editing"]').waitFor();
      const draft = await shown();
      assert.deepEqual(draft, { name: 'Desk', size: [1.7, 0.6], pos: [DESK.x0 + DESK.w / 2, DESK.y0 + DESK.d / 2, DESK.h].map(v => +v.toFixed(2)), type: 'Table' });
      assert.match(await mapper().innerText(), /Surface · Desk · flat/);
      const reach = await page.locator('[data-named-aim-reach]').innerText();
      assert.equal(reach, 'Authored ceiling A can reach it. Authored ceiling B is out of reach.');
      assert.equal(await button('Save named aim').isDisabled(), false);
      return { draft, reach };
    });

    await check('save checks: a duplicate name and a type that disagrees with the surface block Save, with the reason', async () => {
      const name = page.getByRole('textbox', { name: 'Named aim name', exact: true });
      await name.fill('authored wall art');
      assert.match(await page.locator('[data-check="name"]').innerText(), /Another named aim is already called “authored wall art”/);
      assert.equal(await button('Save named aim').isDisabled(), true);
      await name.fill('');
      assert.equal(await button('Save named aim').isDisabled(), true);
      await name.fill('Desk');
      await shell.inspector().getByRole('group', { name: 'Type' }).getByRole('button', { name: 'Art', exact: true }).click();
      assert.match(await page.locator('[data-check="type"]').innerText(), /Art goes on a wall/);
      assert.equal(await button('Save named aim').isDisabled(), true);
      await shell.inspector().getByRole('group', { name: 'Type' }).getByRole('button', { name: 'Table', exact: true }).click();
      assert.equal(await page.locator('[data-check]').count(), 0); assert.equal(await button('Save named aim').isDisabled(), false);
      return { duplicate_blocked: true, empty_blocked: true, art_on_flat_blocked: true };
    });

    await check('Preview from a light draws its beam on the draft and nothing else', async () => {
      assert.equal(await beams(), 0);
      await button('Preview from Authored ceiling A').click();
      await page.waitForFunction(() => window.__APT_ENGINE.overlay.aimBeamGroup.children.length > 0);
      assert.equal(await button('Preview from Authored ceiling A').getAttribute('aria-pressed'), 'true');
      assert.equal(await shell.inspector().getByRole('button', { name: 'Preview from Authored ceiling B', exact: true }).count(), 0, 'only lights that can reach it preview');
      await button('Preview from Authored ceiling A').click();
      await page.waitForFunction(() => window.__APT_ENGINE.overlay.aimBeamGroup.children.length === 0);
      return { preview: 'Authored ceiling A', beam_cleared: true };
    });

    await check('handles stay on the desk plane: a corner resizes with the opposite corner fixed, the centre moves in plane, the arrow lifts with a drop line, the room does not orbit', async () => {
      const cam = await camera(), start = await geometry();
      const corners = draft => page.evaluate(draft => window.AptNamedAims.corners(draft), draft);
      const before = await corners(start), from = await handle('c0'); assert.ok(from, 'corner handle drawn over the room');
      // Pull the corner 0.25 m further out along the aim's own width and 0.15 m along its depth.
      const basis = await page.evaluate(draft => window.AptNamedAims.basis(draft.normal, draft.up), start);
      const goal = [0, 1, 2].map(i => before.c0[i] + basis.right[i] * 0.25 + basis.up[i] * 0.15);
      await drag(from, await screen(goal));
      const resized = await geometry(), after = await corners(resized);
      assert.ok(Math.abs(resized.size_m[0] - (start.size_m[0] + 0.25)) <= 0.03 && Math.abs(resized.size_m[1] - (start.size_m[1] + 0.15)) <= 0.03, JSON.stringify(resized));
      assert.equal(resized.pos[2], DESK.h, 'resizing never leaves the plane'); assert.deepEqual(resized.normal, [0, 0, 1]);
      assert.ok(Math.hypot(after.c2[0] - before.c2[0], after.c2[1] - before.c2[1], after.c2[2] - before.c2[2]) <= 0.015, 'the opposite corner stays put');
      const mid = await handle('mid'), moveTo = await screen([resized.pos[0] - 0.3, resized.pos[1] - 0.2, DESK.h]);
      await drag(mid, moveTo);
      const moved = await geometry();
      assert.ok(Math.abs(moved.pos[0] - (resized.pos[0] - 0.3)) <= 0.03 && Math.abs(moved.pos[1] - (resized.pos[1] - 0.2)) <= 0.03, JSON.stringify(moved));
      assert.equal(moved.pos[2], DESK.h, 'the centre moves within the plane'); assert.deepEqual(moved.size_m, resized.size_m);
      assert.equal(await page.locator('[data-draft-drop]').getAttribute('visibility'), 'visible', 'a dashed line drops to the floor');
      const lift = await handle('lift');
      await drag(lift, { x: lift.x, y: lift.y - 40 });
      const lifted = await geometry();
      assert.ok(lifted.pos[2] > DESK.h + 0.05, 'the arrow raises a flat aim: ' + lifted.pos[2]);
      assert.deepEqual(lifted.pos.slice(0, 2), moved.pos.slice(0, 2), 'lifting changes height only');
      assert.deepEqual(await camera(), cam, 'handle drags do not orbit the room');
      // Keyboard alternatives: the height slider back to the desk, 5 cm nudges in plane.
      await shell.inspector().getByRole('slider', { name: 'Aim height', exact: true }).fill(String(DESK.h));
      await button('Nudge right').click();
      const nudged = await geometry(), step = await page.evaluate(draft => window.AptNamedAims.basis(draft.normal, draft.up).right, moved);
      for (const i of [0, 1, 2]) assert.ok(Math.abs(nudged.pos[i] - (i === 2 ? DESK.h : moved.pos[i] + step[i] * 0.05)) <= 0.001, 'a 5 cm nudge along the aim: ' + JSON.stringify(nudged.pos));
      return { resized: resized.size_m, moved: moved.pos, lifted_z: lifted.pos[2], nudged: nudged.pos };
    });

    await check('Save adds the aim to targets[] with the existing shape; it lists in Setup and in the Room inspector, saved locally with nothing sent', async () => {
      await shell.inspector().getByRole('button', { name: 'Fit', exact: true }).click();
      assert.deepEqual((await shown()).size, [1.7, 0.6]);
      await button('Save named aim').click();
      await page.locator('[data-named-aim-save="offline"]').waitFor();
      assert.match(await page.locator('[data-named-aim-save]').innerText(), /Saved locally/);
      const doc = await draftDoc(), desk = doc.targets.find(target => target.name === 'Desk');
      assert.ok(desk, 'saved in the persisted apartment_model document');
      assert.deepEqual(Object.keys(desk).sort(), ['category', 'confidence', 'id', 'name', 'normal', 'pos', 'room_id', 'shape', 'size_m', 'source', 'up', 'updated_at']);
      assert.deepEqual([desk.category, desk.shape, desk.size_m, desk.pos, desk.normal, desk.room_id],
        ['table', 'surface', [1.7, 0.6], [DESK.x0 + DESK.w / 2, DESK.y0 + DESK.d / 2, DESK.h].map(v => +v.toFixed(3)), [0, 0, 1], 'authored-study']);
      assert.equal(await mapper().getByRole('button', { name: /^Desk/ }).count(), 1);
      await page.locator('[data-apt-setup-inspector] .apt-setup-done').click(); await shell.rail().waitFor({ state: 'detached' });
      await page.getByRole('region', { name: 'Named aims', exact: true }).getByRole('button', { name: 'Edit Desk', exact: true }).waitFor();
      receipt.saved_target_id = desk.id;
      return { target: desk };
    });

    await check('the Room inspector Edit reopens it; a nudge saves as an update of the same aim', async () => {
      await page.getByRole('region', { name: 'Named aims', exact: true }).getByRole('button', { name: 'Edit Desk', exact: true }).click();
      await page.locator('[data-named-aims="editing"]').waitFor();
      assert.match(await mapper().innerText(), /Edit named aim/);
      await button('Nudge away').click(); await button('Save changes').click();
      await page.waitForFunction(id => JSON.parse(localStorage.getItem('apartment3d.modelDraft')).targets.find(target => target.id === id)?.pos[1] > 3.5, receipt.saved_target_id);
      const doc = await draftDoc();
      assert.equal(doc.targets.filter(target => target.name === 'Desk').length, 1, 'edit updates, it does not add');
      return { id: receipt.saved_target_id, pos: doc.targets.find(target => target.id === receipt.saved_target_id).pos };
    });

    await check('Escape leaves placing first, then closes the editor without saving', async () => {
      const before = JSON.stringify((await draftDoc()).targets);
      await button('New').click(); await page.locator('[data-named-aims="placing"]').waitFor();
      await page.keyboard.press('Escape'); await page.locator('[data-named-aims="list"]').waitFor();
      await button('New').click();
      const floor = await screen([0.6, 0.6, 0]); await page.mouse.click(floor.x, floor.y);
      await page.locator('[data-named-aims="editing"]').waitFor();
      assert.equal((await shown()).name, 'Floor spot');
      await page.evaluate(() => document.activeElement?.blur());
      await page.keyboard.press('Escape'); await page.locator('[data-named-aims="list"]').waitFor();
      assert.equal(await shell.rail().count(), 1, 'Escape closed the aim editor, not Setup');
      assert.equal(JSON.stringify((await draftDoc()).targets), before, 'nothing saved');
      return { placing_escape: 'cancelled', editing_escape: 'closed' };
    });

    await check('Delete drops the name; lights keep their position and aim, and nothing is sent', async () => {
      const devices = JSON.stringify((await draftDoc()).devices.map(device => [device.id, device.pos, device.ha_entity_id]));
      await mapper().getByRole('button', { name: /^Desk/ }).click(); await page.locator('[data-named-aims="editing"]').waitFor();
      await button('Delete').click(); await button('Delete aim').click();
      await page.waitForFunction(id => !JSON.parse(localStorage.getItem('apartment3d.modelDraft')).targets.some(target => target.id === id), receipt.saved_target_id);
      assert.equal(JSON.stringify((await draftDoc()).devices.map(device => [device.id, device.pos, device.ha_entity_id])), devices);
      return { deleted: receipt.saved_target_id };
    });

    for (const [file, expected] of Object.entries(packHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack bytes stay exact');
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
