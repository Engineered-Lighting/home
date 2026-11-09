// The den simulation's honesty and safety test, in a real browser against the real harness, on a
// prepared set: the beam travels, a refusal leaves the spot off, a take with nobody stops at the
// detector, the top line reads "not yet" with step 2's words hop by hop, the picture is the take's
// own frame with no box, no coordinate is printed, and nothing leaves the page. It uses --room
// authored and a scratch data root (DEN_SIM_HOME and LOCALAPPDATA), so no household data or
// private file is involved. It runs on this PC only: it needs a prepared set, the pinned refs in
// tools/den-sim/refs.json and the codec on disk, so no CI job names it.
//
//   set DEN_SIM_SET=<a prepared set> and NODE_PATH=<a node_modules holding playwright>, then
//   node --test tools\den-sim\test-den-sim-browser.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

// LOCALAPPDATA points at scratch here, so Playwright is told where its browsers really are.
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright');
const { chromium } = createRequire(import.meta.url)('playwright');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const HOST = '127.0.0.1', PORT = 18180, ORIGIN = `http://${HOST}:${PORT}`;
const BRAIN_STATE = '/api/lighting-brain/v1/homes/victoria/state';
const ZHA = '/api/services/zha/issue_zigbee_cluster_command', HUE = '/api/services/light/turn_on';
// Home's own bench probe. The page's security policy stops it before any network.
const EXPECTED_CSP_BLOCKS = new Set(['http://127.0.0.1:8765/healthz']);
const FEED_POST = 'POST /__sim/feed';
const SET = process.env.DEN_SIM_SET ? path.resolve(process.env.DEN_SIM_SET) : null;
const SKIP = SET && fs.existsSync(path.join(SET, 'display.json')) ? false : 'needs DEN_SIM_SET=<a prepared set>';
// The plan's step 2 words ("Labels that change"), hop by hop, with no sofa mark saved.
const STEP2 = { 1: 'SCRIPTED', 2: 'SCRIPTED', 3: 'ADAPTER', 4: 'ADAPTER', 5: 'ADAPTER', 6: 'SCRIPTED', 7: 'REAL', 8: 'REAL', 9: 'ADAPTER', 10: 'SCRIPTED',
  11: 'MISSING', 12: 'SCRIPTED', 13: 'ADAPTER', 14: 'SCRIPTED', 15: 'MISSING', 16: 'MISSING', 17: 'ADAPTER', 18: 'ADAPTER', 19: 'SCRIPTED', 20: 'REAL',
  21: 'ADAPTER', 22: 'ADAPTER', 23: 'SCRIPTED', 24: 'MISSING', 25: 'MISSING', 26: 'MISSING' };

const until = async (check, what, ms = 60000) => {
  for (const end = Date.now() + ms; ; await new Promise(done => setTimeout(done, 100))) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw Error('timed out waiting for ' + what);
  }
};
const statusOf = async () => (await fetch(ORIGIN + '/__sim/status')).json();
const phase = name => until(async () => { const status = await statusOf(); return status.feed.phase === name && status.click && status; }, `phase ${name}`, 120000);

function startHarness(root) {
  const child = spawn(process.execPath, [path.join(HERE, 'run.mjs'), '--room', 'authored', '--prepared', SET],
    { env: { ...process.env, DEN_SIM_HOME: root, LOCALAPPDATA: root }, windowsHide: true });
  let output = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  const stopped = new Promise(done => child.once('exit', done));
  return { stop: () => { child.kill(); return stopped; },
    ready: until(() => { if (child.exitCode !== null) throw Error('the harness stopped: ' + output); return output.includes('Ctrl+C'); }, 'the harness', 60000) };
}

// Runs in the page: Home's real overlay, sampled faster than the 10 Hz documents.
const startSampler = page => page.evaluate(() => {
  const run = window.__testRun = { aims: [], everLit: 0 };
  run.timer = setInterval(() => {
    const live = window.__APT_ENGINE.overlay, rows = live.virtualFixturesReadback().fixtures.map(row => live.virtualFixtureInteractionDescriptor(row.fixture_id));
    const lit = rows.find(row => row.state.spotlight.on);
    if (!lit) return;
    run.everLit++;
    const key = `${lit.state.pan_deg.toFixed(1)}/${lit.state.tilt_deg.toFixed(1)}`;
    if (run.aims.at(-1) !== key) run.aims.push(key);
    run.last = { fixture_id: lit.fixture_id, pan_deg: lit.state.pan_deg, tilt_deg: lit.state.tilt_deg, target: lit.target?.point || null };
  }, 25);
});
const stopSampler = page => page.evaluate(() => { const run = window.__testRun; clearInterval(run.timer); return { aims: run.aims, everLit: run.everLit, last: run.last }; });
const hopStatus = (page, n) => page.getAttribute(`[data-den-sim-hop="${n}"]`, 'data-den-sim-status');
const press = (page, label) => page.locator('[data-den-sim-feed]', { hasText: label }).click();
// Home's fixture report on this page: what the panel posts with each click.
const fixtureReport = page => page.evaluate(() => {
  const live = window.__APT_ENGINE.overlay;
  return live.virtualFixturesReadback().fixtures.map(row => live.virtualFixtureInteractionDescriptor(row.fixture_id))
    .map(d => ({ fixture_id: d.fixture_id, display_origin_m: d.display_origin_m, horizontal_world_reference: d.horizontal_world_reference, pan_deg: d.state.pan_deg, tilt_deg: d.state.tilt_deg }));
});

async function openHome(page) {
  await page.goto(ORIGIN + '/');
  await page.waitForFunction(() => {
    const button = document.querySelector('[data-den-sim-feed]');
    return button && !button.disabled && window.__APT_ENGINE?.overlay && document.querySelector('[data-den-sim-hops]');
  }, null, { timeout: 120000 });
  // Fixtures draw lit until the first brain document, and the brain fades them off.
  await page.waitForFunction(() => {
    const live = window.__APT_ENGINE.overlay;
    return live.virtualFixturesReadback().fixtures.every(row => !live.virtualFixtureInteractionDescriptor(row.fixture_id).state.spotlight.on);
  }, null, { timeout: 15000 });
}
// A point written the ways a person would print it.
const coordinatesIn = (text, point) => point.flatMap(value => [3, 2].map(places => value.toFixed(places)))
  .filter(token => new RegExp(`(?<![0-9])${token.replace('.', '\\.')}(?![0-9])`).test(text));

test('den simulation: honest and safe', { skip: SKIP, timeout: 600000 }, async t => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'den-sim-test-'));
  const harness = startHarness(scratch);
  const seen = { external: [], writes: [], sockets: [] }, reports = {};
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  let browser;
  t.after(async () => {
    await browser?.close();
    await harness.stop();
    fs.rmSync(scratch, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  });
  await harness.ready;

  browser = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--disable-background-networking', '--renderer-process-limit=2'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' });
  // Anything that gets past the page's own policy stops here, so a failing test sends nothing either.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['data:', 'blob:'].includes(url.protocol) || url.origin === ORIGIN) return route.continue();
    seen.external.push(url.href);
    return route.abort();
  });
  context.on('request', request => {
    const url = new URL(request.url());
    if (!['GET', 'HEAD'].includes(request.method())) seen.writes.push(`${request.method()} ${url.pathname}`);
  });
  const page = await context.newPage();
  page.on('websocket', socket => seen.sockets.push(socket.url()));

  await t.test('the page says what it is before anything runs', async () => {
    await openHome(page);
    const strip = await page.textContent('[data-den-sim-strip]');
    for (const words of ['SIMULATION', 'rendered takes', 'no inference ran', 'nothing is sent', 'AUTHORED ROOM', 'AUTHORED SOCKETS', 'DECLARED',
      'INVENTED CAMERA: it does not exist', 'Home\'s code from this PC\'s harness, not your deployed Home']) assert.ok(strip.includes(words), `the strip says ${words}: ${strip}`);
    assert.deepEqual(await page.locator('[data-den-sim-feed]').allTextContents(), display.map(scene => scene.label), 'one button per prepared take, labelled from the display file');
    assert.deepEqual(await page.locator('[data-den-sim-feed]').evaluateAll(nodes => nodes.map(node => node.dataset.denSimFeed)), display.map(scene => scene.take), 'a button carries a take id, never a word');
    assert.equal(await page.isVisible('[data-den-sim-top-line]'), false, 'no top line before a click');
    assert.match(await page.textContent('[data-den-sim-hops]'), /No click yet/);
    assert.equal(await page.isVisible('[data-den-sim-not-sent]'), false, 'no command before a run');
    assert.ok(await page.evaluate(() => window.__APT_ENGINE.overlay.calibGroup.children.length > 0), 'the invented camera\'s marker is in Home\'s overlay');
    const state = await (await fetch(ORIGIN + BRAIN_STATE)).json();
    assert.match(state.why, /^SIMULATION · /);
    assert.equal(state.inputs.inference_ran, false);
  });

  await t.test('reading: the beam passes through at least 5 intermediate aims and lands where the brain chose', async () => {
    reports.reading = await fixtureReport(page);
    await startSampler(page);
    await press(page, 'Reading on the couch');
    const held = await phase('holding');
    await page.waitForSelector('[data-den-sim-not-sent]');
    const { aims, last } = await stopSampler(page);
    assert.ok(aims.length - 2 >= 5, `at least 5 aims between start and end: ${aims.join('  ')}`);
    assert.equal(last.fixture_id, held.feed.summary.fixture_id, 'Home lit the fixture the brain chose');
    assert.ok(Math.abs(last.pan_deg - held.feed.summary.pan_deg) < .05 && Math.abs(last.tilt_deg - held.feed.summary.tilt_deg) < .05, 'Home agrees with the brain');
    assert.deepEqual(last.target, held.feed.swing.final_target_m, 'the beam ends on the brain\'s target');
    t.diagnostic(`${aims.length} distinct aims, ${aims[0]} to ${aims.at(-1)}, ${held.feed.summary.fixture_id}`);
  });

  await t.test('the top line reads "not yet", lists why, and every row reads step 2\'s word', async () => {
    assert.equal(await page.textContent('[data-den-sim-top-line-text]'), 'Connected start to finish: not yet');
    assert.equal(await page.textContent('[data-den-sim-count]'), '3 real, 9 adapter (7 real tools with a named stand-in, 2 written for this simulation), 8 scripted, 6 missing');
    assert.ok(await page.locator('[data-den-sim-reason]').count() > 10, 'the reasons are listed');
    for (const [n, word] of Object.entries(STEP2)) assert.equal(await hopStatus(page, n), word, `hop ${n}`);
    assert.deepEqual(await page.locator('[data-den-sim-hops] h4').allTextContents(), ['Recorded once', 'Built when this page started', 'Ran on this click']);
    const names = await page.locator('[data-den-sim-hop]').evaluateAll(nodes => nodes.sort((a, b) => a.dataset.denSimHop - b.dataset.denSimHop).map(node => node.dataset.denSimName));
    assert.deepEqual(names, JSON.parse(fs.readFileSync(path.join(HERE, 'hops.json'), 'utf8')).hops.map(hop => hop.name), 'one row per hop, named as the table names it');
    assert.ok(!names.some(name => /Person to aim|decide v0/.test(name)), '#424\'s two rows left the list');
    assert.match(await page.textContent('[data-den-sim-hop="6"]'), /fake_detector places the figure's true joints from the truth folder/, 'the detector row says the joints are the truth');
    assert.match(await page.textContent('[data-den-sim-hop="3"]'), /re-checked by hash now/);
  });

  await t.test('the picture is the take\'s own frame, read back out of its NV12, and no box is drawn', async () => {
    const size = await page.$eval('[data-den-sim-picture]', img => img.complete && [img.naturalWidth, img.naturalHeight]);
    assert.deepEqual(size, [640, 360]);
    const status = await statusOf(), bytes = Buffer.from(await (await fetch(ORIGIN + '/__sim/picture')).arrayBuffer());
    const written = fs.readFileSync(path.join(status.run, `click-${status.click.click}`, 'picture.png'));
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), crypto.createHash('sha256').update(written).digest('hex'), 'the click\'s own picture');
    assert.equal(await page.locator('#den-sim-panel svg, #den-sim-panel canvas, #den-sim-panel video').count(), 0, 'no box, no figure drawn over it');
    assert.equal(await page.locator('#den-sim-panel img').count(), 1, 'one picture');
  });

  await t.test('no coordinate is printed: the aim target and the hand point show only their distance from the seat', async () => {
    const status = await statusOf(), text = await page.innerText('#den-sim-panel');
    for (const [name, point] of [['camera', status.camera_m], ['target', status.feed.summary.target_m], ['person', status.feed.summary.person_m]]) {
      assert.deepEqual(coordinatesIn(text, point), [], `${name} is not in the panel's text`);
    }
    assert.match(await page.textContent('[data-den-sim-value="hand"]'), /^\d\.\d\d m from the seat$/);
    assert.match(await page.textContent('[data-den-sim-value="target"]'), /m from the brain's hand point, which is \d\.\d\d m from the seat$/);
    assert.ok(coordinatesIn(`${text} ${status.camera_m.map(v => v.toFixed(2)).join(', ')}`, status.camera_m).length > 0, 'control: a printed camera is found');
  });

  await t.test('the command is shown, marked NOT SENT, and nothing can send it', async () => {
    assert.match(await page.textContent('[data-den-sim-not-sent]'), /NOT SENT/);
    const shown = await page.textContent('[data-den-sim-pre="post"]');
    assert.ok(shown.includes('POST ' + ZHA) && shown.includes('"ieee":"UNCOMMISSIONED"'), shown.slice(0, 120));
    const command = await (await fetch(`${ORIGIN}/__sim/command?click=${(await statusOf()).click.click}`)).json();
    assert.equal(command.sent, false); assert.equal(command.send_path, null);
    assert.equal(command.ik.status, 'FICTIONAL'); assert.equal(command.ik.physical_eligible, false);
    const controls = await page.locator('#den-sim-panel button').allTextContents();
    assert.deepEqual(controls.sort(), [...display.map(scene => scene.label), 'Hide details', 'Reset (reload)'].sort(), 'no send control');
    assert.equal(await page.locator('#den-sim-panel form, #den-sim-panel a[href], #den-sim-panel input').count(), 0, 'no other control');
  });

  await t.test('holding a phone: the brain refuses and the spot stays off', async () => {
    await openHome(page);
    reports.phone = await fixtureReport(page);
    await startSampler(page);
    await press(page, 'Holding a phone');
    const refused = await phase('refused');
    await page.waitForSelector('[data-den-sim-not-sent]');
    const { everLit } = await stopSampler(page);
    assert.equal(everLit, 0, 'no spot came on at any sample');
    assert.equal(refused.feed.summary.lit, false);
    assert.equal(refused.feed.summary.refusal, 'Not reading · no spotlight');
    assert.equal(await page.textContent('[data-den-sim-value="spot"]'), 'all four off');
    assert.match(await page.textContent('[data-den-sim-command]'), /No fixture command: the brain refused/);
    assert.equal(await page.textContent('[data-den-sim-top-line-text]'), 'Connected start to finish: not yet');
    assert.equal(await page.getAttribute('[data-den-sim-hop="21"]', 'data-reached'), '0', 'hops past the refusal are not reached');
  });

  await t.test('nobody there: the click stops at the detector, says so, and lights nothing', async () => {
    await openHome(page);
    reports.nobody = await fixtureReport(page);
    await startSampler(page);
    await press(page, 'Nobody there');
    const stopped = await phase('stopped');
    const { everLit } = await stopSampler(page);
    assert.equal(everLit, 0);
    assert.deepEqual(stopped.click.stopped, { hop: 6, why: 'the detector saw nobody, nothing was asked', answer: true });
    await page.waitForFunction(() => /the detector saw nobody, nothing was asked/.test(document.querySelector('[data-den-sim-message]').textContent), null, { timeout: 10000 });
    assert.equal(await hopStatus(page, 6), 'SCRIPTED');
    assert.equal(await page.getAttribute('[data-den-sim-hop="7"]', 'data-reached'), '0');
  });

  await t.test('Home\'s fixture report is the same across scenes on a fresh page', () => {
    assert.ok(reports.reading.length === 4, JSON.stringify(reports.reading));
    assert.deepEqual(reports.phone, reports.reading);
    assert.deepEqual(reports.nobody, reports.reading);
    const moved = structuredClone(reports.nobody);
    moved[0].pan_deg += 1;
    assert.notDeepEqual(moved, reports.reading, 'control: one changed angle differs');
  });

  await t.test('after every run: nothing left the page, and the only write was the feed', async () => {
    assert.deepEqual(seen.external, [], 'zero external requests');
    assert.deepEqual([...new Set(seen.writes)], [FEED_POST], 'zero non-GET except the feed');
    assert.equal(seen.writes.length, 3, 'one feed post per click');
    assert.deepEqual(seen.sockets, [], 'no WebSocket opened');
    const blocked = await page.evaluate(() => (window.__denSimCsp || []).map(row => row.uri));
    assert.deepEqual(blocked.filter(uri => !EXPECTED_CSP_BLOCKS.has(uri)), [], 'nothing unexpected was stopped by the page policy');
    assert.equal(await page.evaluate(() => window.__denSim.external.size), 0, 'the panel agrees');
    const status = await statusOf();
    assert.deepEqual(status.refused.filter(row => !['GET', 'HEAD'].includes(row.method)), [], 'the harness saw no other write');
    assert.equal(status.counters.upgrades_destroyed, 0);
  });

  await t.test('the harness refuses every other path, and listens on loopback only', async () => {
    const post = (route, headers = {}) => fetch(ORIGIN + route, { method: 'POST', headers, body: '{}' });
    assert.equal((await post(ZHA)).status, 405, 'no route to the POST the page shows');
    assert.equal((await post(HUE)).status, 405);
    assert.equal((await fetch(ORIGIN + ZHA)).status, 503);
    assert.equal((await post('/__sim/feed', { 'Content-Type': 'application/json' })).status, 403, 'the feed needs this origin');
    assert.equal((await post('/__sim/feed', { 'Content-Type': 'application/json', Origin: 'http://evil.test' })).status, 403);
    assert.equal(await new Promise((done, fail) => http.get({ host: HOST, port: PORT, path: '/__sim/status', headers: { Host: 'evil.test' } }, response => { response.resume(); done(response.statusCode); }).on('error', fail)), 403, 'wrong Host');
    const closed = await new Promise((done, fail) => {
      const socket = net.connect(PORT, HOST, () => socket.write(`GET /stream HTTP/1.1\r\nHost: ${HOST}:${PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGVzdA==\r\nSec-WebSocket-Version: 13\r\n\r\n`));
      let reply = '';
      socket.on('data', chunk => { reply += chunk; });
      socket.on('close', () => done(reply));
      socket.on('error', () => done(reply));
      setTimeout(() => fail(Error('the upgrade was not dropped')), 3000).unref();
    });
    assert.doesNotMatch(closed, /101/, 'no WebSocket upgrade');
    const addresses = Object.values(os.networkInterfaces()).flat().filter(row => row.family === 'IPv4' && !row.internal).map(row => row.address);
    for (const address of addresses) await assert.rejects(fetch(`http://${address}:${PORT}/__sim/status`), `not listening on ${address}`);
    t.diagnostic(`refused on ${addresses.length} non-loopback address(es)`);
  });

  await t.test('python refuses sockets inside a tool process, and this run left the private folder alone', async () => {
    const probe = path.join(scratch, 'tries-a-socket.py');
    fs.writeFileSync(probe, "import socket\ntry:\n    socket.create_connection(('127.0.0.1', 9), timeout=1)\n    print('NOT REFUSED')\nexcept RuntimeError as error:\n    print('refused: ' + str(error))\n");
    assert.match(execFileSync('python', [path.join(HERE, 'chain.py'), '--guarded', probe], { encoding: 'utf8', windowsHide: true }), /^refused: /);
    assert.deepEqual(fs.readdirSync(path.join(scratch, 'private')), [], 'an authored run writes nothing there');
  });
});
