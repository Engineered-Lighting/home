// The den simulation's honesty and safety test, in a real browser against the real
// harness: the beam travels, a refusal leaves the spot off, nothing leaves the page, and
// no row claims what did not run. It uses --room authored and a scratch output folder, so
// no household data or private file is involved. It runs on this PC only: it needs the
// pinned refs in tools/den-sim/refs.json and the codec on disk, so no CI job names it.
//
//   node --test tools\den-sim\test-den-sim-browser.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const HOST = '127.0.0.1', PORT = 18180, ORIGIN = `http://${HOST}:${PORT}`;
const BRAIN_STATE = '/api/lighting-brain/v1/homes/victoria/state';
const ZHA = '/api/services/zha/issue_zigbee_cluster_command', HUE = '/api/services/light/turn_on';
// Home's own bench probe. The page's security policy stops it before any network.
const EXPECTED_CSP_BLOCKS = new Set(['http://127.0.0.1:8765/healthz']);
// A later real Qwen look would write this. Only then may the Qwen row stop reading SCRIPTED.
const QWEN_RECEIPT = 'qwen-reply-receipt.json';
const FEED_POST = 'POST /__sim/feed';
const SCRIPTED_HOPS = ['Input image', 'Frames', 'Detector and box', 'Qwen reply'];
const MISSING_HOPS = ['Camera registration', 'Bundle to Qwen', 'Home Assistant/ZHA envelope', 'Aim verification'];
const REAL_HOPS = ['Observations', 'Tracks', 'Bundles and router', 'Room-level need', 'decide v0', 'Spot decision and aim'];

const until = async (check, what, ms = 60000) => {
  for (const end = Date.now() + ms; ; await new Promise(done => setTimeout(done, 100))) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw Error('timed out waiting for ' + what);
  }
};
const statusOf = async () => (await fetch(ORIGIN + '/__sim/status')).json();
const phase = name => until(async () => { const status = await statusOf(); return status.feed.phase === name && status; }, `phase ${name}`);

function startHarness(localAppData) {
  const child = spawn(process.execPath, [path.join(HERE, 'run.mjs'), '--room', 'authored'], { env: { ...process.env, DEN_SIM_HOME: localAppData, LOCALAPPDATA: localAppData }, windowsHide: true });
  let output = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  const stopped = new Promise(done => child.once('exit', done));
  return { stop: () => { child.kill(); return stopped; },
    ready: until(() => { if (child.exitCode !== null) throw Error('the harness stopped: ' + output); return output.includes('Ctrl+C'); }, 'the harness', 30000) };
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
const hopStatus = (page, name) => page.getAttribute(`[data-den-sim-hop="${name}"]`, 'data-den-sim-status');
const hopRun = (page, name) => page.getAttribute(`[data-den-sim-pipeline-hop="${name}"]`, 'data-den-sim-status');

async function openHome(page) {
  await page.goto(ORIGIN + '/');
  await page.waitForFunction(() => {
    const button = document.querySelector('[data-den-sim-feed="reading_on_couch"]');
    return button && !button.disabled && window.__APT_ENGINE?.overlay && document.querySelector('[data-den-sim-hop="Qwen reply"]');
  }, null, { timeout: 120000 });
  // Fixtures draw lit until the first brain document, and the brain fades them off.
  await page.waitForFunction(() => {
    const live = window.__APT_ENGINE.overlay;
    return live.virtualFixturesReadback().fixtures.every(row => !live.virtualFixtureInteractionDescriptor(row.fixture_id).state.spotlight.on);
  }, null, { timeout: 15000 });
}

test('den simulation: honest and safe', { timeout: 420000 }, async t => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'den-sim-test-'));
  const harness = startHarness(scratch);
  const seen = { external: [], writes: [], sockets: [] };
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
    for (const words of ['SIMULATION', 'scripted scene', 'no inference ran', 'nothing is sent', 'AUTHORED ROOM', 'AUTHORED SOCKETS', 'DECLARED', 'INVENTED CAMERA']) assert.ok(strip.includes(words), `the strip says ${words}: ${strip}`);
    assert.equal(await page.isVisible('[data-den-sim-not-sent]'), false, 'no command before a run');
    const state = await (await fetch(ORIGIN + BRAIN_STATE)).json();
    assert.match(state.why, /^SIMULATION · /);
    assert.equal(state.inputs.inference_ran, false);
  });

  await t.test('no row claims what did not run', async () => {
    for (const name of SCRIPTED_HOPS) assert.equal(await hopStatus(page, name), 'SCRIPTED', name);
    for (const name of MISSING_HOPS) assert.equal(await hopStatus(page, name), 'MISSING', name);
    for (const name of REAL_HOPS) assert.equal(await hopStatus(page, name), 'REAL', name);
    assert.equal(await hopStatus(page, 'Person to aim'), 'ADAPTER');
    assert.equal(await hopStatus(page, 'Couch'), 'SCRIPTED', 'a DECLARED point is not a sofa mark');
    assert.equal(await hopStatus(page, 'Sockets'), 'SCRIPTED', 'authored sockets are not an export');
  });

  await t.test('reading: the beam passes through at least 5 intermediate aims and lands where the brain chose', async () => {
    await startSampler(page);
    await page.click('[data-den-sim-feed="reading_on_couch"]');
    const held = await phase('holding');
    await page.waitForSelector('[data-den-sim-not-sent]');
    const { aims, last } = await stopSampler(page);
    assert.ok(aims.length - 2 >= 5, `at least 5 aims between start and end: ${aims.join('  ')}`);
    assert.equal(last.fixture_id, held.feed.summary.fixture_id, 'Home lit the fixture the brain chose');
    assert.ok(Math.abs(last.pan_deg - held.feed.summary.pan_deg) < .05 && Math.abs(last.tilt_deg - held.feed.summary.tilt_deg) < .05, 'Home agrees with the brain');
    assert.deepEqual(last.target, held.feed.swing.final_target_m, 'the beam ends on the brain\'s target');
    t.diagnostic(`${aims.length} distinct aims, ${aims[0]} to ${aims.at(-1)}, ${held.feed.summary.fixture_id}`);
  });

  await t.test('the Qwen row reads SCRIPTED unless a reply receipt exists', async () => {
    const receipt = fs.existsSync(path.join(scratch, 'private', QWEN_RECEIPT));
    assert.equal(receipt, false, 'this test has no receipt');
    for (const status of [await hopStatus(page, 'Qwen reply'), await hopRun(page, 'Qwen reply')]) assert.equal(status, 'SCRIPTED');
    assert.match(await page.textContent('[data-den-sim-pipeline-hop="Qwen reply"]'), /typed.*No model ran/);
  });

  await t.test('the pipeline rows match the run, and the figure has no picture behind it', async () => {
    for (const name of ['Observations', 'Tracks', 'Bundles and router', 'Room-level need', 'decide v0']) assert.equal(await hopRun(page, name), 'REAL', name);
    for (const name of ['Input image', 'Frames', 'Detector and box']) assert.equal(await hopRun(page, name), 'SCRIPTED', name);
    assert.equal(await hopRun(page, 'Bundle to Qwen'), 'MISSING');
    assert.equal(await hopRun(page, 'Person to aim'), 'ADAPTER');
    assert.match(await page.textContent('[data-den-sim-pipeline-hop="Skipped"]'), /den-take \(capture\), den-register, den-label, den-n2, den-verify/);
    assert.equal(await page.locator('[data-den-sim-figure] line').count(), 16, 'the stick figure');
    assert.equal(await page.locator('#den-sim-panel img, #den-sim-panel video, #den-sim-panel canvas').count(), 0, 'no picture in the panel');
  });

  await t.test('the command is shown, marked NOT SENT, and nothing can send it', async () => {
    assert.match(await page.textContent('[data-den-sim-not-sent]'), /NOT SENT/);
    const shown = await page.textContent('[data-den-sim-pre="post"]');
    assert.ok(shown.includes('POST ' + ZHA) && shown.includes('"ieee":"UNCOMMISSIONED"'), shown.slice(0, 120));
    const command = await (await fetch(ORIGIN + '/__sim/command?scenario=reading_on_couch')).json();
    assert.equal(command.sent, false); assert.equal(command.send_path, null);
    assert.equal(command.ik.status, 'FICTIONAL'); assert.equal(command.ik.physical_eligible, false);
    const controls = await page.locator('#den-sim-panel button').allTextContents();
    assert.deepEqual(controls.sort(), ['Feed: person A on the couch, not reading', 'Feed: person A reading on the couch', 'Hide details', 'Reset (reload)'], 'no send control');
    assert.equal(await page.locator('#den-sim-panel form, #den-sim-panel a[href], #den-sim-panel input').count(), 0, 'no other control');
  });

  await t.test('not reading: the brain refuses and the spot stays off', async () => {
    await openHome(page);
    await startSampler(page);
    await page.click('[data-den-sim-feed="not_reading"]');
    const refused = await phase('refused');
    await page.waitForSelector('[data-den-sim-not-sent]');
    const { everLit } = await stopSampler(page);
    assert.equal(everLit, 0, 'no spot came on at any sample');
    assert.equal(refused.feed.summary.lit, false);
    assert.equal(refused.feed.summary.refusal, 'Not reading · no spotlight');
    assert.equal(await page.textContent('[data-den-sim-value="spot"]'), 'all four off');
    assert.match(await page.textContent('[data-den-sim-command]'), /No fixture command: the brain refused/);
    const command = await (await fetch(ORIGIN + '/__sim/command?scenario=not_reading')).json();
    assert.equal(command.brain, null); assert.equal(command.wire, null); assert.equal(command.sent, false);
  });

  await t.test('after both runs: nothing left the page, and the only write was the feed', async () => {
    assert.deepEqual(seen.external, [], 'zero external requests');
    assert.deepEqual([...new Set(seen.writes)], [FEED_POST], 'zero non-GET except the feed');
    assert.equal(seen.writes.length, 2, 'one feed post per click');
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
