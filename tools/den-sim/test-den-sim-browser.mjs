// The den simulation's honesty and safety test, in a real browser against the real harness, on a
// prepared set: with no reply on file, reading stops at hop 12 and the page shows what Qwen still owes
// and the paste line with a Copy button; after a fake vLLM's replies are filed (test_ask.py --fill, a
// local transport, nothing reaches LA) the page shows the last paste; the coordinator's Decision moves
// the beam through Home's real overlay to its target_m, the aim check holds Home to it within 0.05 degrees
// and a beam moved 0.3 m or 1 m off fails it (test 16); the command, NOT SENT, comes from the Decision
// (test 17); a phone click writes no Decision and the card says why; a take with nobody and a blank one
// stop at the detector with no box; "Run every scene" fills its table from the coordinator; the top line
// reads "not yet" with step 6's words hop by hop, and no sentence says the older brain decided; the
// picture is the take's own frame with the model's box and joints from that frame's row, the person
// card says how far from the seat point, on the couch or not and which spot, in words with no coordinate,
// no coordinate is printed, and nothing leaves the page. It uses --room authored and a scratch data root
// (DEN_SIM_HOME and LOCALAPPDATA), so no household data or private file is involved. It runs on this PC
// only: it needs a prepared set, the pinned refs in tools/den-sim/refs.json and the codec on disk, so no
// CI job names it.
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
const FEED_POST = 'POST /__sim/feed', AIM_POST = 'POST /__sim/aim';
const SET = process.env.DEN_SIM_SET ? path.resolve(process.env.DEN_SIM_SET) : null;
const SKIP = SET && fs.existsSync(path.join(SET, 'display.json')) ? false : 'needs DEN_SIM_SET=<a prepared set>';
// The plan's step 6 words ("Labels that change"), hop by hop, with no sofa mark saved, on a fake vLLM's
// replies: hop 12 reads SCRIPTED, because a fake root is no model; 14 SCRIPTED on the DECLARED stand-in; 10, 16, 17, 18
// and 24 ADAPTER; 19 and 23 SCRIPTED on purpose; 25 and 26, step 7's, MISSING.
const STEP6 = { 1: 'SCRIPTED', 2: 'SCRIPTED', 3: 'ADAPTER', 4: 'ADAPTER', 5: 'ADAPTER', 6: 'ADAPTER', 7: 'REAL', 8: 'REAL', 9: 'ADAPTER', 10: 'ADAPTER',
  11: 'ADAPTER', 12: 'SCRIPTED', 13: 'ADAPTER', 14: 'SCRIPTED', 15: 'ADAPTER', 16: 'ADAPTER', 17: 'ADAPTER', 18: 'ADAPTER', 19: 'SCRIPTED', 20: 'REAL',
  21: 'ADAPTER', 22: 'ADAPTER', 23: 'SCRIPTED', 24: 'ADAPTER', 25: 'MISSING', 26: 'MISSING' };
// Sentences that said the older brain decided. None may come back to the page (plan, step 6).
const OLDER_BRAIN = /older brain|older lighting brain|the brain (chose|refused|decides|lit|waits)|Brain says|Brain chose|Home vs brain|Events into brain|brain's hand point|jev|deep_stack|1\.5 m rule/i;
const PASTE = `python -B ${path.join(HERE, 'ask_la.py')}`;
// The fake paste, in stages: the owed bodies first, then a fake vLLM's replies, through test_ask.py.
const fill = (root, stage, facts) => JSON.parse(execFileSync('python', ['-B', '-X', 'utf8', path.join(HERE, 'test_ask.py'), '--fill', root, '--set', SET, '--facts', facts, '--stage', stage],
  { encoding: 'utf8', windowsHide: true, timeout: 600000 }).trim().split('\n').at(-1));

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
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  const facts = ['Reading on the couch', 'Holding a phone'].map((label, i) => `${['reading', 'phone'][i]}=${display.find(scene => scene.label === label).take}`).join(',');
  const owed = Object.values(fill(scratch, 'owed', facts).owed).flat().length;
  const harness = startHarness(scratch);
  const seen = { external: [], writes: [], sockets: [] }, reports = {};
  let browser;
  t.after(async () => {
    await browser?.close();
    await harness.stop();
    fs.rmSync(scratch, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  });
  await harness.ready;

  browser = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--disable-background-networking', '--renderer-process-limit=2'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block', permissions: ['clipboard-read', 'clipboard-write'] });
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
    for (const words of ['SIMULATION', 'rendered takes', 'a real detector\'s recorded rows', 'no Qwen reply on file yet', 'nothing is sent', 'AUTHORED ROOM', 'AUTHORED SOCKETS', 'DECLARED',
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
    // What Qwen still owes, and the owner's one paste, with a Copy button that copies exactly that line.
    assert.equal(await page.textContent('[data-den-sim-owed-text]'), `Qwen still owes ${owed} replies. Paste this line into a new PowerShell window (run.mjs holds this one), then press a scene again:`);
    assert.equal(await page.textContent('[data-den-sim-paste]'), PASTE);
    await page.click('[data-den-sim-copy]');
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), PASTE);
    assert.equal(await page.isVisible('[data-den-sim-last-ask]'), false, 'no paste yet');
  });

  await t.test('reading with no reply on file: hop 12 reads MISSING, need gets no input and the beam stays off', async () => {
    await startSampler(page);
    await press(page, 'Reading on the couch');
    const broke = await phase('pipeline_failed');
    const { everLit } = await stopSampler(page);
    assert.equal(everLit, 0);
    assert.deepEqual([broke.click.stopped.hop, broke.click.stopped.answer], [12, false]);
    assert.equal(await hopStatus(page, 12), 'MISSING');
    assert.equal(await hopStatus(page, 11), 'ADAPTER', 'the bodies were built');
    assert.match(await page.textContent('[data-den-sim-hop="12"]'), /Qwen has not seen these exact frames: 5 of 5 bodies have no reply on file/);
    assert.equal(await page.getAttribute('[data-den-sim-hop="13"]', 'data-reached'), '0', 'no need input');
    await page.waitForFunction(() => /Qwen has not seen these exact frames/.test(document.querySelector('[data-den-sim-message]').textContent), null, { timeout: 10000 });
    assert.equal(await page.textContent('[data-den-sim-top-line-text]'), 'Connected start to finish: not yet');
    assert.equal(await page.isVisible('[data-den-sim-owed]'), true, 'the paste line stays in view');
    assert.equal(await page.locator('[data-den-sim-ask]').count(), 5, 'each request, its stills and "no reply on file"');
    assert.match(await page.textContent('[data-den-sim-ask="rq-00002"]'), /^rq-00002: frames \d+, \d+, \d+ \([0-9.]+, [0-9.]+, 0 s before the newest\): no reply on file$/);
    // No Decision card: the coordinator never ran, because Qwen hasn't seen the frames, and the message says so.
    assert.equal(await page.isVisible('[data-den-sim-decision]'), false);
    assert.match(await page.textContent('[data-den-sim-message]'), /the coordinator has nothing to decide on, and all four spots stay off/);
    // The stills each body would send, shown as the very JPEGs: rq-00001 has 2, the others 3, each 640x360, served by their sha256.
    const asks = (await statusOf()).click.qwen.asks;
    const stills = page.locator('[data-den-sim-still]');
    assert.equal(await stills.count(), asks.reduce((n, ask) => n + ask.jpegs.length, 0));
    await page.waitForFunction(() => [...document.querySelectorAll('[data-den-sim-still]')].every(img => img.complete && img.naturalWidth === 640 && img.naturalHeight === 360), null, { timeout: 15000 });
    const sha = await stills.first().getAttribute('data-den-sim-still');
    const bytes = Buffer.from(await (await fetch(`${ORIGIN}/__sim/still?sha=${sha}`)).arrayBuffer());
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), sha, 'a still is the JPEG its hash names');
    assert.equal((await fetch(`${ORIGIN}/__sim/still?sha=${'0'.repeat(64)}`)).status, 404, 'control: a hash the click did not send is not served');
  });

  await t.test('after the fake paste the page shows what it said, and owes nothing', async () => {
    const paste = fill(scratch, 'paste', facts);
    assert.deepEqual([paste.code, paste.answered, paste.still_needed], [0, owed, 0]);
    await openHome(page);
    await page.waitForSelector('[data-den-sim-last-ask]:not([hidden])');
    assert.match(await page.textContent('[data-den-sim-last-ask]'), new RegExp(`^Your last paste, .+: ${owed} of ${owed} replies came back, 0 still needed; model root fake-vllm\\.$`));
    assert.equal(await page.isVisible('[data-den-sim-owed]'), false);
    await page.waitForFunction(() => document.querySelector('[data-den-sim-strip]').textContent.includes("a fake vLLM's scripted words on file, not Qwen's"), null, { timeout: 10000 });
    assert.doesNotMatch(await page.textContent('[data-den-sim-strip]'), /no Qwen reply on file yet|Qwen's words recorded once/, 'the strip says whose words are on file');
  });

  await t.test('test 16, reading: the beam passes through at least 5 intermediate aims and lands on the Decision\'s target_m, held to it within 0.05 degrees', async () => {
    reports.reading = await fixtureReport(page);
    await startSampler(page);
    await press(page, 'Reading on the couch');
    const held = await phase('holding');
    await page.waitForSelector('[data-den-sim-not-sent]');
    const { aims, last } = await stopSampler(page);
    assert.ok(aims.length - 2 >= 5, `at least 5 aims between start and end: ${aims.join('  ')}`);
    assert.equal(last.fixture_id, held.feed.summary.fixture_id, 'Home lit the fixture the Decision lit');
    assert.deepEqual(last.target, held.feed.summary.target_m, 'Home\'s target is the Decision\'s target_m');
    assert.deepEqual(held.feed.swing.final_target_m, held.feed.summary.target_m, 'and the swing ends on it');
    // The aim check at landing, from Home's real overlay: both numbers within 0.05 degrees, and its row ADAPTER.
    const checked = await until(async () => (await statusOf()).click?.aim_check, 'the aim check', 30000);
    assert.deepEqual([checked.within_tolerance, checked.decision_deg <= .05, checked.ik_deg <= .05, checked.tolerance_deg], [true, true, true, .05], JSON.stringify(checked));
    await page.waitForFunction(() => /^Home followed the Decision: Decision against Home 0\.0\d\d°, inverse kinematics against Home 0\.0\d\d°, tolerance 0\.05°\.$/.test(document.querySelector('[data-den-sim-aim-text]')?.textContent || ''), null, { timeout: 15000 });
    assert.equal(await hopStatus(page, 24), 'ADAPTER');
    const work = path.join((await statusOf()).run, `click-${held.click.click}`);
    assert.equal(JSON.parse(fs.readFileSync(path.join(work, 'aim_check.json'), 'utf8')).within_tolerance, true, 'the harness wrote aim_check.json');
    // The Decision card, from hop 17's row: the fixture it chose and the level against the target.
    const card = await page.locator('[data-den-sim-decision-line]').allTextContents();
    assert.match(card[0], /^Chose F\d \((fixture|mount)-0\d\): level [01](\.\d+)?, predicts \d+ of 300 asked, modeled lux, assumed lumens\.$/);
    await page.waitForFunction(() => /^The beam is on the spot: 0\.30 m from the DECLARED seat/.test(document.querySelector('[data-den-sim-message]').textContent), null, { timeout: 10000 });
    assert.match(await page.textContent('[data-den-sim-message]'), /The words were a fake vLLM's, scripted for a test: reading, holding book, people one on 5 of 5\./);
    assert.doesNotMatch(await page.innerText('#den-sim-panel'), OLDER_BRAIN, 'no sentence says the older brain decided');
    // The honesty flags follow the reply used: a fake vLLM's words are no inference. The occupancy is the track list's.
    const state = await (await fetch(ORIGIN + BRAIN_STATE)).json();
    assert.deepEqual([state.inputs.inference_ran, state.inputs.scripted, state.inputs.activity_scripted, state.activity.source, state.occupancy.source],
      [false, true, true, 'SIMULATION-scripted', 'den_sim_den_track']);
    assert.match(state.why, /a fake vLLM's scripted words · reading_look: F\d\.spot lit for trk-\d+/);
    assert.equal(state.reading_evidence.bound, true, 'the evidence joins the Decision to its request, bundle, track and reply');
    // The Qwen card: each reply is a plain file, said so, and its words come with every field.
    assert.match(await page.textContent('[data-den-sim-plain-file]'), /plain file with no signature, so a hand-typed one would pass/);
    assert.equal(await page.locator('[data-den-sim-ask="rq-00002"] [data-den-sim-fields] dt').count(), 10, 'all ten fields');
    assert.match(await page.textContent('[data-den-sim-ask="rq-00002"] [data-den-sim-fields]'), /activity hypothesisreading/);
    t.diagnostic(`${aims.length} distinct aims, ${aims[0]} to ${aims.at(-1)}, ${held.feed.summary.fixture_id}`);
  });

  await t.test('the top line reads "not yet", lists why, and every row reads step 6\'s word', async () => {
    await page.waitForFunction(() => document.querySelector('[data-den-sim-hop="24"]')?.dataset.denSimStatus === 'ADAPTER', null, { timeout: 15000 });
    assert.equal(await page.textContent('[data-den-sim-top-line-text]'), 'Connected start to finish: not yet');
    assert.equal(await page.textContent('[data-den-sim-count]'), '3 real, 15 adapter (8 real tools with a named stand-in, 7 written for this simulation), 6 scripted, 2 missing');
    assert.ok(await page.locator('[data-den-sim-reason]').count() > 5, 'the reasons are listed');
    for (const [n, word] of Object.entries(STEP6)) assert.equal(await hopStatus(page, n), word, `hop ${n}`);
    assert.deepEqual(await page.locator('[data-den-sim-hops] h4').allTextContents(), ['Recorded once', 'Built when this page started', 'Ran on this click', 'Not built yet, or not reached on this click']);
    // Which rows sit under each heading: a row that didn't run is never under "Ran on this click". Hop 11's ask is recorded.
    const underHeadings = await page.locator('[data-den-sim-hops] h4').evaluateAll(nodes => nodes.map(node => [node.textContent, [...node.nextElementSibling.querySelectorAll('[data-den-sim-hop]')].map(item => Number(item.dataset.denSimHop))]));
    assert.deepEqual(underHeadings, [['Recorded once', [1, 2, 3, 4, 5, 6, 11]], ['Built when this page started', [14, 16]], ['Ran on this click', [7, 8, 9, 10, 12, 13, 15, 17, 18, 19, 20, 21, 22, 23, 24]],
      ['Not built yet, or not reached on this click', [25, 26]]]);
    // Hops 17 to 24 say what ran, from the Decision: the coordinator's words, one mapping, the swing, the command and the aim check.
    assert.match(await page.textContent('[data-den-sim-hop="17"]'), /rung 9's coordinator in spot mode \(virtual-den-4, synthetic\) ran once on \d+ events in \d+ ms, check\.py: \d+ lines replayed byte-identically\. 1 Decision: reading_look: F\d\.spot lit for trk-\d+ at level/);
    for (const stand of ["rung 9's coordinator with a spot mode written for this simulation, the brain's fixture rule imported", "plumbing: its own score.py refuses it"])
      assert.ok((await page.textContent('[data-den-sim-hop="17"]')).includes(`Stand-in: ${stand}.`), `hop 17 names: ${stand}`);
    assert.match(await page.textContent('[data-den-sim-hop="18"]'), /decision_doc\.mjs mapped 1 Decision to Home's activity-lighting-state-1, one mapping: F\d\.spot \((fixture|mount)-0\d\) lit at its target_m, reading evidence bound to rq-\d+/);
    assert.match(await page.textContent('[data-den-sim-hop="21"]'), /on the Decision's target_m and level/);
    assert.match(await page.textContent('[data-den-sim-hop="23"]'), /from the Decision.*NOT SENT, sent false, send_path null/);
    assert.match(await page.textContent('[data-den-sim-hop="24"]'), /Home followed the Decision: the Decision's target against Home's target point, 0\.0\d\d degrees apart at the fixture; inverse kinematics against Home's read-back, 0\.0\d\d degrees apart/);
    assert.ok((await page.textContent('[data-den-sim-hop="22"]')).includes('Stand-in: the codec is read by path, on no git ref: the release origin/main pins, with LF line endings where its pins hash CRLF.'));
    // Hop 16 says what it built, when, and what stands in: Home's formula on assumed numbers, and on authored sockets, whose origins.
    const sixteenText = await page.textContent('[data-den-sim-hop="16"]');
    assert.match(sixteenText, /light_model\.mjs built lm-den-sim-1 at page start, before any picture, the same file for every take: Home's own beam formula .*marked fake: true, sha256 [0-9a-f]{12}\./);
    for (const stand of ["Home's beam formula on assumed numbers, marked fake: true", "the fixture origins are the authored room's public sockets, not your registration export"])
      assert.ok(sixteenText.includes(`Stand-in: ${stand}.`), `hop 16 names: ${stand}`);
    // Hops 13 to 15: need once per track, the couch in the RoomModel, the person state the coordinator reads.
    assert.match(await page.textContent('[data-den-sim-hop="13"]'), /need v0 once per track, in person\.py \(hop 15\), on the replies its bundle was safe to take: trk-\d+ took 4 of 5 replies, 1 held as not safe to tie to one person: starting then engaged\. Nothing else runs need on a click\./);
    assert.match(await page.textContent('[data-den-sim-hop="14"]'), /A DECLARED point \(the middle of the floor\) with a stand-in seat sealed around it, not your sofa mark, written by couch_to_room\.mjs into the pipeline's own RoomModel as revision \S+: surface sofa, landmark sofa-seat, and the spot sofa-book 0\.30 m above the seat point/);
    const fifteen = await page.textContent('[data-den-sim-hop="15"]');
    assert.match(fifteen, /person\.py wrote \d+ person states in canon's names.*The coordinator reads them \(hop 17\)\./);
    for (const stand of ['engagement is room-level words joined to the only track; rung 6 isn\'t built', 'geometry real and unscored']) assert.ok(fifteen.includes(`Stand-in: ${stand}.`), `hop 15 names: ${stand}`);
    const names = await page.locator('[data-den-sim-hop]').evaluateAll(nodes => nodes.sort((a, b) => a.dataset.denSimHop - b.dataset.denSimHop).map(node => node.dataset.denSimName));
    assert.deepEqual(names, JSON.parse(fs.readFileSync(path.join(HERE, 'hops.json'), 'utf8')).hops.map(hop => hop.name), 'one row per hop, named as the table names it');
    assert.ok(!names.some(name => /Person to aim|decide v0/.test(name)), '#424\'s two rows left the list');
    const six = await page.textContent('[data-den-sim-hop="6"]');
    assert.match(six, /rows checked against the model on .*YOLO26s found a person on \d+ of \d+ usable frames, and RTMPose-m placed joints on \d+ of them/, 'the detector row says a real model found the person');
    for (const stand of ['YOLO26s and RTMPose-m on this PC\'s CPU, not the live host', 'available_ts counts compute only', 'detect.py is new', 'authorize is answered yes'])
      assert.ok(six.includes(stand), `hop 6 names its stand-in: ${stand}`);
    assert.doesNotMatch(six, /truth|fake_detector/);
    assert.match(await page.textContent('[data-den-sim-hop="10"]'), /One occupancy event, on via den-track, at the first confirmed Track line \(trk-\d+\).*Stand-in: occupancy from the track list, not Frigate on Hailo\./);
    assert.match(await page.textContent('[data-den-sim-hop="12"]'), /Scripted words from a fake vLLM \(root fake-vllm\), not Qwen: rq-00001 reading, holding book/);
    assert.match(await page.textContent('[data-den-sim-hop="11"]'), /recorded \S+, bodies rebuilt now.*5 of 5 requests built into exact bodies/);
    assert.match(await page.textContent('[data-den-sim-qwen]'), /^Qwen \(SCRIPTED: a fake vLLM, not Qwen\): model root fake-vllm, ran now\./);
    assert.match(await page.textContent('[data-den-sim-hop="3"]'), /re-checked by hash now/);
  });

  await t.test('the picture is the take\'s own frame, with the model\'s box, score and joints from that frame\'s row', async () => {
    const size = await page.$eval('[data-den-sim-picture]', img => img.complete && [img.naturalWidth, img.naturalHeight]);
    assert.deepEqual(size, [640, 360]);
    const status = await statusOf(), bytes = Buffer.from(await (await fetch(ORIGIN + '/__sim/picture')).arrayBuffer());
    const written = fs.readFileSync(path.join(status.run, `click-${status.click.click}`, 'picture.png'));
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), crypto.createHash('sha256').update(written).digest('hex'), 'the click\'s own picture');
    assert.equal(await page.locator('[data-den-sim-picture]').count(), 1, 'one picture');
    assert.equal(await page.locator('#den-sim-panel img:not([data-den-sim-picture]):not([data-den-sim-still])').count(), 0, 'and besides it only the stills Qwen was sent');
    assert.equal(await page.locator('#den-sim-panel canvas, #den-sim-panel video').count(), 0);
    // The box drawn is the one in the recorded row of the very frame the picture shows.
    const receipt = await (await fetch(ORIGIN + '/__sim/receipt')).json(), { rgb_seq: seq, boxes } = receipt.picture;
    const rowsFile = path.join(SET, receipt.take, 'detections.jsonl');
    const row = fs.readFileSync(rowsFile, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)).find(item => item.frame.rgb_seq === seq);
    assert.deepEqual(boxes.map(box => box.box_xyxy), row.detections.filter(d => d.class === 'person').map(d => d.box_xyxy.map(v => Math.round(v * 10) / 10)));
    const drawn = await page.$$eval('[data-den-sim-boxes] rect[data-den-sim-box]', rects => rects.map(r => ['x', 'y', 'width', 'height'].map(k => Number(r.getAttribute(k)))));
    assert.deepEqual(drawn, boxes.map(({ box_xyxy: [x0, y0, x1, y1] }) => [x0, y0, x1 - x0, y1 - y0]));
    assert.match(await page.textContent('[data-den-sim-boxes] text'), /^person 0\.\d\d$/, 'the box\'s score');
    // Legible: the score's letters stand at least 10 px tall on the page, and the note repeats it.
    const scorePx = await page.$eval('[data-den-sim-boxes] text', node => node.getBoundingClientRect().height);
    assert.ok(scorePx >= 10, `the score is ${scorePx.toFixed(1)} px tall`);
    assert.ok(await page.locator('[data-den-sim-boxes] [data-den-sim-joint]').count() >= 5, 'its supported joints');
    assert.match(await page.textContent('[data-den-sim-picture-note]'), /YOLO26s's person box and score \(person 0\.\d\d\) and RTMPose-m's supported joints from this frame's recorded row/);
    assert.ok((await page.textContent('[data-den-sim-picture-note]')).includes(`person ${boxes[0].score.toFixed(2)}`), 'the note repeats the score drawn');
    // The person card, which replaced the torso line: from this click's hop 15 row, in words and distances.
    const card = await page.locator('[data-den-sim-person-line]').allTextContents();
    assert.match(card[0], /^Track trk-\d+: \d\.\d\d m from the seat point, across the floor\.$/);
    assert.deepEqual(card.slice(1), ['On the couch: yes.', 'The spot: sofa-book, the book spot 0.30 m above the seat point. Its marker in the room comes at step 7.',
      'Engagement: engaged, support observed, from need v0 on this track\'s replies.', 'On the picture\'s couch, which this simulation placed on a DECLARED stand-in seat, not your sofa mark.',
      'The coordinator decides the beam from this state (hop 17): no target unless the spot is sofa-book.']);
    assert.equal(await page.locator('[data-den-sim-torso]').count(), 0, 'the torso line is gone');
  });

  await t.test('no coordinate is printed: the aim target shows only its distance from the Decision\'s target and the seat', async () => {
    const status = await statusOf(), text = await page.innerText('#den-sim-panel');
    for (const [name, point] of [['camera', status.camera_m], ['target', status.feed.summary.target_m], ['person', status.feed.summary.person_m]]) {
      assert.deepEqual(coordinatesIn(text, point), [], `${name} is not in the panel's text`);
    }
    assert.match(await page.textContent('[data-den-sim-value="target"]'), /^0\.000 m from the Decision's target, which is \d\.\d\d m from the seat$/);
    assert.ok(coordinatesIn(`${text} ${status.camera_m.map(v => v.toFixed(2)).join(', ')}`, status.camera_m).length > 0, 'control: a printed camera is found');
  });

  await t.test('test 16\'s control: a beam moved 0.3 m and 1 m off through Home\'s overlay fails the aim check, and the top line says Home did not follow', async () => {
    const aimed = (await statusOf()).feed.summary;
    // Home's own overlay aims the lit fixture elsewhere, and the page reads it back at once, before the next document re-aims it.
    const moveAndCheck = offset => page.evaluate(async ({ id, point }) => {
      const result = window.__APT_ENGINE.overlay.aimVirtualFixtureAtTarget(id, { point, normal: [0, 0, 1], kind: 'lighting_brain_target' });
      return { aimed: result?.ok === true, check: await window.__denSim.aimCheck() };
    }, { id: aimed.fixture_id, point: [aimed.target_m[0] + offset, aimed.target_m[1], aimed.target_m[2]] });
    for (const offset of [0.3, 1]) {
      const { aimed: moved, check } = await moveAndCheck(offset);
      assert.ok(moved, 'Home\'s overlay took the moved aim');
      assert.deepEqual([check.ok, check.within_tolerance, check.why], [true, false, 'Home did not follow the Decision'], `${offset} m: ${JSON.stringify(check)}`);
      assert.ok(check.decision_deg > .05 && check.ik_deg > .05, JSON.stringify(check));
      await page.waitForFunction(() => document.querySelector('[data-den-sim-top-line-text]')?.textContent === 'Connected start to finish: not yet: Home did not follow the Decision', null, { timeout: 10000 });
      assert.match(await page.textContent('[data-den-sim-hop="24"]'), /Home did not follow the Decision: the Decision's target against Home's target point, \d+\.\d{3} degrees apart/);
    }
    // Back on the Decision's target, the check holds again (control: the failure was the move's).
    const { check } = await moveAndCheck(0);
    assert.deepEqual([check.ok, check.within_tolerance], [true, true], JSON.stringify(check));
    await page.waitForFunction(() => document.querySelector('[data-den-sim-top-line-text]')?.textContent === 'Connected start to finish: not yet', null, { timeout: 10000 });
  });

  await t.test('test 17: the command is the Decision\'s, shown, marked NOT SENT, and nothing can send it', async () => {
    assert.match(await page.textContent('[data-den-sim-not-sent]'), /NOT SENT/);
    const shown = await page.textContent('[data-den-sim-pre="post"]');
    assert.ok(shown.includes('POST ' + ZHA) && shown.includes('"ieee":"UNCOMMISSIONED"'), shown.slice(0, 120));
    const status = await statusOf(), command = await (await fetch(`${ORIGIN}/__sim/command?click=${status.click.click}`)).json();
    assert.equal(command.sent, false); assert.equal(command.send_path, null);
    assert.equal(command.ik.status, 'FICTIONAL'); assert.equal(command.ik.physical_eligible, false);
    assert.deepEqual([command.decision.fixture_id, command.decision.target_m, command.decision.level], [status.feed.summary.fixture_id, status.feed.summary.target_m, status.feed.summary.level],
      'the command is compiled from the Decision\'s fixture, target and level');
    assert.match(await page.textContent('[data-den-sim-command]'), /From.*the Decision decide-\d+-\d+: F\d\.spot, its target_m and level/);
    assert.doesNotMatch(await page.textContent('[data-den-sim-command]'), /Home's angles/, 'the brain\'s numbers are gone from the command');
    const controls = await page.locator('#den-sim-panel button').allTextContents();
    assert.deepEqual(controls.sort(), [...display.map(scene => scene.label), 'Run every scene', 'Copy', 'Hide details', 'Reset (reload)'].sort(), 'no send control');
    assert.equal(await page.locator('#den-sim-panel form, #den-sim-panel a[href], #den-sim-panel input').count(), 0, 'no other control');
  });

  await t.test('holding a phone: the coordinator writes no Decision, the card reads the person state for why, and the spot stays off', async () => {
    await openHome(page);
    reports.phone = await fixtureReport(page);
    await startSampler(page);
    await press(page, 'Holding a phone');
    const stopped = await phase('stopped');
    const { everLit } = await stopSampler(page);
    assert.equal(everLit, 0, 'no spot came on at any sample');
    assert.deepEqual([stopped.click.stopped.hop, stopped.click.stopped.answer, stopped.feed.summary], [17, true, null]);
    assert.match(stopped.click.stopped.why, /^nobody is reading \(trk-\d+: engagement none, on the couch yes\)$/);
    assert.equal(await page.textContent('[data-den-sim-value="spot"]'), 'all four off');
    assert.deepEqual(await page.locator('[data-den-sim-decision-line]').allTextContents(), [`No Decision: ${stopped.click.stopped.why}. Every spot stays off.`]);
    assert.equal(await page.isVisible('[data-den-sim-command]'), false, 'no Decision, no command');
    await page.waitForFunction(() => /^The click stopped: nobody is reading/.test(document.querySelector('[data-den-sim-message]').textContent), null, { timeout: 10000 });
    assert.equal(await page.textContent('[data-den-sim-top-line-text]'), 'Connected start to finish: not yet');
    assert.equal(await hopStatus(page, 17), 'ADAPTER', 'the coordinator ran, and answered');
    assert.match(await page.textContent('[data-den-sim-hop="17"]'), /and wrote no Decision: nobody is reading/);
    assert.equal(await page.getAttribute('[data-den-sim-hop="21"]', 'data-reached'), '0', 'hops past the stop are not reached');
    // On the couch, not reading: the card says so, from hop 15's row.
    const card = await page.locator('[data-den-sim-person-line]').allTextContents();
    assert.deepEqual([card[1], card[3]], ['On the couch: yes.', 'Engagement: none, support observed, from need v0 on this track\'s replies.']);
    assert.doesNotMatch(await page.innerText('#den-sim-panel'), OLDER_BRAIN);
  });

  await t.test('a second scene pressed without Reset speaks for itself, though a status from the last click lands after the press', async () => {
    // Every status poll is held back 600 ms, so the polls sent just before the press land after it, still holding
    // phone's refusal and its words. The panel must wait for this click's own run.
    await page.evaluate(() => {
      const real = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const response = await real(input, init);
        if (String(input).startsWith('/__sim/status')) await new Promise(done => setTimeout(done, 600));
        return response;
      };
    });
    // Polls go out every 250 ms: after a second, two or three held-back ones are always on their way at the press.
    await page.waitForTimeout(1000);
    await press(page, 'Reading on the couch');
    await phase('holding');
    await page.waitForFunction(() => /^The beam is on the spot/.test(document.querySelector('[data-den-sim-message]').textContent), null, { timeout: 30000 });
    const text = await page.textContent('[data-den-sim-message]');
    assert.match(text, /The words were a fake vLLM's, scripted for a test: reading, holding book, people one on 5 of 5\./);
    assert.doesNotMatch(text, /phone_use|nobody is reading/, 'none of the last click\'s words');
    await page.waitForFunction(() => /Fixture angles/.test(document.querySelector('[data-den-sim-command]').textContent), null, { timeout: 15000 });
    assert.doesNotMatch(await page.textContent('[data-den-sim-command]'), /lit nothing/, 'the command is this click\'s');
  });

  for (const label of ['Nobody there', 'Blank frame']) {
    await t.test(`${label.toLowerCase()}: the click stops at the detector, says so, draws no box and lights nothing`, async () => {
      await openHome(page);
      reports[label] = await fixtureReport(page);
      await startSampler(page);
      await press(page, label);
      const stopped = await phase('stopped');
      const { everLit } = await stopSampler(page);
      assert.equal(everLit, 0);
      assert.deepEqual(stopped.click.stopped, { hop: 6, why: 'the detector saw nobody, nothing was asked', answer: true });
      await page.waitForFunction(() => /the detector saw nobody, nothing was asked/.test(document.querySelector('[data-den-sim-message]').textContent), null, { timeout: 10000 });
      assert.equal(await hopStatus(page, 6), 'ADAPTER', 'the detector ran and saw nobody');
      assert.match(await page.textContent('[data-den-sim-hop="6"]'), /YOLO26s found no person on any of the \d+ usable frames/);
      assert.equal(await page.getAttribute('[data-den-sim-hop="7"]', 'data-reached'), '0');
      // The couch was built at page start on the DECLARED stand-in: greyed, it keeps SCRIPTED, never the table's ADAPTER (hop 15, a click hop, does take it).
      assert.deepEqual([await hopStatus(page, 14), await page.getAttribute('[data-den-sim-hop="14"]', 'data-reached'), await hopStatus(page, 15)], ['SCRIPTED', '0', 'ADAPTER']);
      assert.match(await page.textContent('[data-den-sim-hop="14"]'), /built at page start; not reached on this click/);
      await page.waitForFunction(() => document.querySelector('[data-den-sim-picture-note]')?.textContent.includes('holds no person'), null, { timeout: 10000 });
      assert.equal(await page.locator('[data-den-sim-boxes]').count(), 0, 'no box');
      assert.equal(await page.isVisible('[data-den-sim-person]'), false, 'no person card');
      assert.equal(stopped.feed.summary, null, 'no person point reached the page, so no person marker on the floor');
      assert.equal(await page.isVisible('[data-den-sim-decision]'), false, 'no Decision card: the coordinator was not reached');
    });
  }

  await t.test('Run every scene: all five with no reload, a plain table, one root, no meter, and the top line left alone', async () => {
    const before = await page.textContent('[data-den-sim-top-line-text]'), shown = await page.getAttribute('[data-den-sim-top-line]', 'data-verdict');
    await page.click('[data-den-sim-every-run]');
    await until(async () => { const now = await statusOf(); return now.every.rows.length === display.length && !now.every.running && ['holding', 'refused', 'stopped'].includes(now.feed.phase); }, 'every scene', 400000);
    await page.waitForFunction(n => document.querySelectorAll('[data-den-sim-every-table] tr').length === n + 1, display.length, { timeout: 10000 });
    const table = await page.$$eval('[data-den-sim-every-table] tr', rows => rows.map(row => [...row.children].map(cell => cell.textContent)));
    assert.deepEqual(table[0], ['Scene', 'Box', 'Qwen', 'Person', 'Beam', 'Why it stopped']);
    const byScene = Object.fromEntries(table.slice(1).map(([scene, ...cells]) => [scene, cells]));
    assert.deepEqual(Object.keys(byScene), display.map(scene => scene.label));
    assert.match(byScene['Reading on the couch'].join(' | '), /^person 0\.\d\d \| fake vLLM, scripted: reading x5 \| on the couch \| lit, F\d \((fixture|mount)-0\d\), level [01](\.\d+)?, predicts \d+ of 300 asked, modeled lux, assumed lumens \| –$/);
    assert.match(byScene['Holding a phone'].join(' | '), /^person 0\.\d\d \| fake vLLM, scripted: phone_use x5 \| on the couch \| off \| hop 17: nobody is reading \(trk-\d+: engagement none, on the couch yes\)$/);
    for (const label of ['Standing by the couch with a book', 'Nobody there', 'Blank frame']) {
      assert.deepEqual(byScene[label], ['none', 'not asked', 'none', 'off', 'hop 6: the detector saw nobody, nothing was asked'], label);
    }
    assert.equal(await page.textContent('[data-den-sim-every-note]'), 'No meter on this run. The top line stays as the last single click set it. Every reply on this table came from one model root, fake-vllm.');
    assert.equal(await page.textContent('[data-den-sim-top-line-text]'), before, 'the top line is the last single click\'s');
    assert.equal(await page.getAttribute('[data-den-sim-top-line]', 'data-verdict'), shown);
  });

  await t.test('Home\'s fixture report is the same across scenes on a fresh page', () => {
    assert.ok(reports.reading.length === 4, JSON.stringify(reports.reading));
    assert.deepEqual(reports.phone, reports.reading);
    assert.deepEqual(reports['Nobody there'], reports.reading);
    assert.deepEqual(reports['Blank frame'], reports.reading);
    const moved = structuredClone(reports['Nobody there']);
    moved[0].pan_deg += 1;
    assert.notDeepEqual(moved, reports.reading, 'control: one changed angle differs');
  });

  await t.test('after every run: nothing left the page, and the only writes were the feed and the aim check', async () => {
    assert.deepEqual(seen.external, [], 'zero external requests');
    assert.deepEqual([...new Set(seen.writes)].sort(), [AIM_POST, FEED_POST], 'zero non-GET except the feed and the aim check');
    assert.equal(seen.writes.filter(write => write === FEED_POST).length, 6 + display.length, 'one feed post per click: six single clicks, and one per scene in Run every scene');
    // The aim check posts once per lit single click (two readings), plus test 16's three: never on Run every scene.
    assert.equal(seen.writes.filter(write => write === AIM_POST).length, 5);
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
    assert.equal((await post('/__sim/aim', { 'Content-Type': 'application/json' })).status, 403, 'the aim check needs this origin too');
    assert.equal((await post('/__sim/aim', { 'Content-Type': 'application/json', Origin: 'http://evil.test' })).status, 403);
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
