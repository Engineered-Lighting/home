// The honesty test through the real harness (plan, "Done means"): test 1's outcomes on all five takes with
// a fake vLLM's replies in a scratch store, judged by what the coordinator lit; test 2's searches over everything
// a click read, wrote or was served, its Decisions, surfaces, Home's documents and the aim check among them; test 2's
// delete at page level (the Decisions byte-identical); the document's flags on a model's words; and test 19 on a
// click. Test 2's patched-chain control, 3, 5's body-hash half, 8 and 10 are in test_ask.py, which also files the fake
// replies here: prepare.py --owed into the scratch data root, then ask_la.py through a local transport against a fake
// vLLM on 127.0.0.1. Nothing reaches LA.
//
//   set DEN_SIM_SET=<a prepared set>, DEN_SIM_HOME, LOCALAPPDATA, TEMP and TMP to scratch folders, then
//   node --test tools\den-sim\test-den-sim-honesty.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import Client from '../../app/src/home-lighting-brain-client.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SET = process.env.DEN_SIM_SET ? path.resolve(process.env.DEN_SIM_SET) : null;
const SKIP = SET && fs.existsSync(path.join(SET, 'display.json')) ? false : 'needs DEN_SIM_SET=<a prepared set>';
const ORIGIN = 'http://127.0.0.1:18180';
const sleep = ms => new Promise(done => setTimeout(done, ms));
const status = async () => (await fetch(ORIGIN + '/__sim/status')).json();

function harness(home, set = SET) {
  const child = spawn(process.execPath, [path.join(HERE, 'run.mjs'), '--room', 'authored', '--prepared', set], { env: { ...process.env, DEN_SIM_HOME: home, LOCALAPPDATA: home }, windowsHide: true });
  let output = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  const stopped = new Promise(done => child.once('exit', done));
  const ready = (async () => {
    for (const end = Date.now() + 180000; Date.now() < end; await sleep(200)) {
      if (output.includes('Ctrl+C')) return;
      if (child.exitCode !== null) throw Error('the harness stopped: ' + output);
    }
    throw Error('the harness did not start: ' + output);
  })();
  return { ready, output: () => output, stop: () => { child.kill(); return stopped; } };
}
// Home's report of the four fixtures, as the page posts it, from the authored room's public geometry this harness wrote
// into its run folder: each fixture aimed straight down its reference, so a lit Decision swings and lands.
function report(run) {
  const geometry = JSON.parse(fs.readFileSync(path.join(run, 'brain-geometry.json'), 'utf8'));
  return geometry.fixtures.map(row => ({ fixture_id: row.fixture_id, display_origin_m: row.aim_origin_m, horizontal_world_reference: row.horizontal_world_reference, pan_deg: 0, tilt_deg: 45 }));
}
// Home's read-back at landing, as the page samples it: pan and tilt toward the point, as Home's aimAtTarget computes them.
function readbackAt(fixture, point) {
  const d = point.map((v, i) => v - fixture.display_origin_m[i]), n = Math.hypot(...d), u = d.map(v => v / n), ref = fixture.horizontal_world_reference;
  const r = [ref[0], ref[1]].map(v => v / Math.hypot(ref[0], ref[1])), tilt = Math.asin(-u[2]), flat = Math.cos(tilt);
  return { fixture_id: fixture.fixture_id, display_origin_m: fixture.display_origin_m, target_point: point,
    pan_deg: Math.atan2((u[0] * r[1] - u[1] * r[0]) / flat, (u[0] * r[0] + u[1] * r[1]) / flat) * 180 / Math.PI, tilt_deg: tilt * 180 / Math.PI };
}
async function click(take, fixtures = []) {
  const reply = await (await fetch(ORIGIN + '/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: ORIGIN }, body: JSON.stringify({ take, fixtures }) })).json();
  assert.ok(reply.ok, JSON.stringify(reply));
  for (const end = Date.now() + 120000; Date.now() < end; await sleep(200)) {
    const now = await status();
    if (now.click) return { ...now, receipt: await (await fetch(ORIGIN + '/__sim/receipt')).json() };
  }
  throw Error('the click did not finish');
}
// The coordinator lit a spot on this click: a Decision on hop 17's row lit a channel.
const lit = done => (done.receipt.rows[16].decisions || []).some(view => view.lit.length > 0);
const TEXT = /\.(json|jsonl|csv|md|log|txt)$/;
function filesUnder(folder) {
  const out = [];
  (function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(file);
      else out.push([path.relative(folder, file), TEXT.test(entry.name) ? fs.readFileSync(file, 'utf8') : '']);
    }
  })(folder);
  return out;
}
// Every place a needle, or a key named scene or scenario, appears: in names and in text.
function search(entries, needles, keys = ['scene', 'scenario']) {
  const hits = [];
  const walk = (value, label) => {
    if (Array.isArray(value)) value.forEach(item => walk(item, label));
    else if (value && typeof value === 'object') for (const [key, item] of Object.entries(value)) { if (keys.includes(key)) hits.push(`${label}: key ${key}`); walk(item, label); }
  };
  for (const [label, text] of entries) {
    for (const needle of needles) if (label.includes(needle) || text.includes(needle)) hits.push(`${label}: ${needle}`);
    for (const line of label.endsWith('.jsonl') ? text.split('\n').filter(Boolean) : [text]) { try { walk(JSON.parse(line), label); } catch { /* not JSON */ } }
  }
  return hits;
}

test('tests 1, 2 and 19 through the harness, on a fake vLLM\'s replies', { skip: SKIP, timeout: 900000 }, async t => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'den-sim-honesty-'));
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  const take = label => display.find(scene => scene.label === label).take;
  const reading = take('Reading on the couch'), phone = take('Holding a phone');
  // The fake paste: reading's bodies answered "reading", phone's "phone use". Nothing else is asked.
  const filled = JSON.parse(execFileSync('python', ['-B', '-X', 'utf8', path.join(HERE, 'test_ask.py'), '--fill', home, '--set', SET, '--facts', `reading=${reading},phone=${phone}`],
    { encoding: 'utf8', windowsHide: true, timeout: 600000 }).trim().split('\n').at(-1));
  assert.deepEqual([filled.code, filled.still_needed, filled.root], [0, 0, 'fake-vllm']);
  const run = harness(home);
  t.after(async () => { await run.stop(); fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); });
  await run.ready;
  const first = await status();
  assert.deepEqual([first.qwen.owed, first.qwen.last_ask.still_needed, first.qwen.last_ask.root], [0, 0, 'fake-vllm'], 'the page reads the paste\'s record');
  const clicks = {}, fixtures = report(first.run);
  for (const scene of display) clicks[scene.label] = await click(scene.take, fixtures);
  // Reading's beam lands, and the aim check is posted as the page would post it: aim_check.json joins test 2's search.
  const readingDone = clicks['Reading on the couch'];
  if (lit(readingDone)) {
    await clickAgainAndAim(readingDone);
  }
  async function clickAgainAndAim(done) {
    const again = await click(done.click.take, fixtures);
    for (const end = Date.now() + 60000; (await status()).feed.phase !== 'holding' && Date.now() < end;) await sleep(200);
    const summary = (await status()).feed.summary, home = readbackAt(fixtures.find(row => row.fixture_id === summary.fixture_id), summary.target_m);
    const aimed = await (await fetch(ORIGIN + '/__sim/aim', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: ORIGIN }, body: JSON.stringify({ click: again.click.click, home }) })).json();
    clicks['Reading on the couch'] = { ...again, aimed };
  }

  await t.test('test 1: reading lights, phone and standing stay off, nobody and blank light nothing, and the table says who was asked', () => {
    assert.ok(lit(clicks['Reading on the couch']), clicks['Reading on the couch'].receipt.rows[16].summary);
    for (const label of ['Holding a phone', 'Standing by the couch with a book', 'Nobody there', 'Blank frame']) assert.ok(!lit(clicks[label]), label);
    const asked = Object.fromEntries(Object.entries(clicks).map(([label, done]) => [label, done.click.rows[11].reached ? done.click.rows[11].word : 'not asked']));
    assert.deepEqual(asked, { 'Reading on the couch': 'SCRIPTED', 'Holding a phone': 'SCRIPTED', 'Standing by the couch with a book': 'not asked', 'Nobody there': 'not asked', 'Blank frame': 'not asked' });
    assert.match(clicks['Holding a phone'].receipt.rows[16].summary, /and wrote no Decision: nobody is reading \(trk-\d+: engagement none/, 'phone stays off because of what the words said');
    assert.deepEqual([clicks['Holding a phone'].click.stopped.hop, clicks['Holding a phone'].click.stopped.answer], [17, true]);
    // Reading's aim check, posted as the page posts it from Home's read-back, holds within 0.05 degrees.
    assert.deepEqual([clicks['Reading on the couch'].aimed?.ok, clicks['Reading on the couch'].aimed?.within_tolerance], [true, true], JSON.stringify(clicks['Reading on the couch'].aimed));
    assert.match(clicks['Holding a phone'].click.qwen.said, /phone_use, holding phone/);
    for (const label of ['Nobody there', 'Blank frame', 'Standing by the couch with a book']) {
      assert.deepEqual(clicks[label].click.stopped, { hop: 6, why: 'the detector saw nobody, nothing was asked', answer: true }, label);
      assert.equal(clicks[label].click.qwen, null, `${label}: Qwen was not asked`);
    }
    // A fake root reads SCRIPTED, so the top line can't read yes on it.
    assert.equal(clicks['Reading on the couch'].click.top_line.line, 'Connected start to finish: not yet');
    assert.ok(clicks['Reading on the couch'].click.top_line.reasons.some(reason => reason.condition === 3 && reason.hop === 12));
  });

  await t.test('test 1: one command line, differing only by folder id, and the bodies the click built are the ones --owed filed', () => {
    const argvs = Object.values(clicks).map(done => path.join(done.run, `click-${done.click.click}`, 'argv.jsonl')).filter(file => fs.existsSync(file)).map(file => fs.readFileSync(file, 'utf8'));
    assert.equal(argvs.length, 2, 'reading and phone ran the tools');
    const shape = text => text.replace(/[0-9a-f]{12}/g, '<id>').replace(/click-\d+/g, 'click-N');
    assert.equal(new Set(argvs.map(shape)).size, 1);
    assert.notEqual(shape(argvs[0]), shape(argvs[0].replace('--detections', '--scene')), 'control: another word shows');
    const owed = JSON.parse(fs.readFileSync(path.join(home, 'test-fill.json'), 'utf8'));
    for (const label of ['Reading on the couch', 'Holding a phone']) {
      const asks = fs.readFileSync(path.join(clicks[label].run, `click-${clicks[label].click.click}`, 'asks.jsonl'), 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
      assert.deepEqual(asks.map(row => row.body_sha256), owed[clicks[label].click.take], `${label}: the page's re-fit and --owed's set fit build the same bodies`);
    }
  });

  await t.test('test 1: the page\'s "chosen from N tries" is tries.jsonl\'s count', () => {
    const lines = fs.readFileSync(path.join(path.dirname(SET), 'tries.jsonl'), 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
    const tries = lines.filter(row => typeof row.root === 'string' && /^[0-9a-f]{64}$/.test(row.body_sha256 || ''));
    assert.deepEqual(first.tries, { count: tries.length, reading: tries.filter(row => row.facts?.activity_hypothesis === 'reading').length });
    assert.ok(lines.length > tries.length, 'control: prepare\'s lines are on file and not counted');
  });

  // Each control rewrites replies in the scratch store, and puts every byte back after.
  const store = path.join(home, 'prepared', 'replies');
  const rewritten = async (names, change, during) => {
    const kept = Object.fromEntries(names.map(name => [name, fs.readFileSync(path.join(store, `${name}.json`))]));
    try {
      for (const name of names) fs.writeFileSync(path.join(store, `${name}.json`), JSON.stringify(change(JSON.parse(kept[name].toString('utf8')))));
      return await during();
    } finally { for (const [name, bytes] of Object.entries(kept)) fs.writeFileSync(path.join(store, `${name}.json`), bytes); }
  };
  const owedOf = () => JSON.parse(fs.readFileSync(path.join(home, 'test-fill.json'), 'utf8'));

  await t.test('test 1\'s control: reading\'s facts filed under phone\'s body hashes make phone light', async () => {
    const owed = owedOf(), readingRaw = JSON.parse(fs.readFileSync(path.join(store, `${owed[reading][1]}.json`), 'utf8')).raw;
    const swapped = await rewritten(owed[phone], reply => ({ ...reply, raw: readingRaw }), () => click(phone, fixtures));
    assert.ok(lit(swapped), 'phone lights on reading\'s words: the words decide, so test 1 would fail on them');
    assert.ok(swapped.click.top_line.reasons.some(reason => reason.condition === 2 && reason.hop === 12), 'and the replies no longer match the digests last-ask.json holds');
  });

  await t.test('the honesty flags on a model\'s words: inference ran on the activity, and the occupancy is the track list\'s', async () => {
    // Phone: no Decision, so Home is left the off document, which still says whose words the click used.
    const real = await rewritten(owedOf()[phone], reply => ({ ...reply, root: 'test-root-not-a-model' }), async () => {
      const done = await click(phone);
      return { ...done, state: await (await fetch(ORIGIN + Client.BASE + '/state')).json() };
    });
    assert.deepEqual([real.click.rows[11].word, real.click.qwen.reply], ['ADAPTER', 'real']);
    const { inputs, activity, occupancy } = real.state;
    assert.deepEqual([inputs.inference_ran, inputs.activity_scripted, activity.source], [true, false, 'den_sim_la_direct'], 'the activity is the model\'s');
    assert.deepEqual([inputs.scripted, occupancy.source], [false, 'den_sim_den_track'], 'and with occupancy from the track list, nothing in its inputs is typed');
    assert.match(real.state.why, /Qwen's recorded words/);
    const fake = await click(phone), state = await (await fetch(ORIGIN + Client.BASE + '/state')).json();
    assert.deepEqual([fake.click.qwen.reply, state.inputs.inference_ran, state.inputs.activity_scripted, state.activity.source], ['fake', false, true, 'SIMULATION-scripted'], 'control: a fake vLLM\'s words are no inference');
  });

  await t.test('test 2: no scene word or display string, and no key scene or scenario, in anything a click read, wrote or was served', async () => {
    const needles = [...display.map(scene => scene.word), ...display.map(scene => scene.label)];
    const takeFiles = display.flatMap(scene => ['take.json', 'frames.csv', 'check-1.json', 'detections.jsonl', 'detector-run.json', 'prepare-receipt.json']
      .map(name => [`${scene.take}/${name}`, fs.readFileSync(path.join(SET, scene.take, name), 'utf8')]));
    const info = JSON.parse(fs.readFileSync(path.join(SET, 'set.json'), 'utf8'));
    const roomModels = [[`${info.fit.folder}/room-model.jsonl`, fs.readFileSync(path.join(SET, info.fit.folder, 'room-model.jsonl'), 'utf8')],
      ...filesUnder(path.join(home, 'fits')).filter(([label]) => label.endsWith('room-model.jsonl'))];
    const prepared = filesUnder(path.join(home, 'prepared')).map(([label, text]) => [`prepared/${label}`, text]);
    assert.ok(['owed.json', 'last-ask.json'].every(name => prepared.some(([label]) => label.endsWith(name))), 'the owed list and last-ask.json are searched');
    assert.ok(prepared.filter(([label]) => /replies/.test(label)).length >= 10, 'the reply store is searched');
    const written = filesUnder(path.join(home, 'runs')).map(([label, text]) => [`runs/${label}`, text]);
    assert.ok(written.some(([label]) => /asks\.jsonl$/.test(label)) && written.some(([label]) => /argv\.jsonl$/.test(label)));
    // Step 5's outputs join the search: the person states, the couch's room-model revision and the person card's served words.
    assert.ok(written.filter(([label]) => /person_states\.jsonl$/.test(label)).length >= 2, 'reading\'s and phone\'s person states are searched');
    // Step 6's: the coordinator's log, Decisions and surfaces, Home's documents, the occupancy and the aim check.
    for (const name of ['decide[\\\\/]log\\.jsonl', 'decide[\\\\/]decisions\\.jsonl', 'decide[\\\\/]surfaces\\.json', 'documents\\.jsonl', 'occupancy\\.jsonl', 'aim_check\\.json'])
      assert.ok(written.some(([label]) => new RegExp(`${name}$`).test(label)), `${name} is searched`);
    assert.ok(roomModels.some(([label, text]) => /couch[\\/]room-model\.jsonl$/.test(label) && text.includes('"surface_id":"sofa"')), 'the couch\'s revision is searched');
    const cards = Object.values(clicks).filter(done => done.click.person).map(done => [`person card ${done.click.take}`, done.click.person.lines.join('\n')]);
    assert.equal(cards.length, 2, 'reading\'s and phone\'s person cards are searched');
    const served = [['status', JSON.stringify({ ...(await status()), scenes: '(the display file, for the buttons)' })],
      ...Object.values(clicks).map(done => [`receipt ${done.click.take}`, JSON.stringify(done.receipt)]), ...cards,
      ['brain state', await (await fetch(ORIGIN + Client.BASE + '/state')).text()]];
    const everything = [...takeFiles, ...roomModels, ...prepared, ...written, ...served];
    assert.deepEqual(search(everything, needles), []);
    const hits = search([['runs/planted.json', JSON.stringify({ note: display[1].word, scenario: 1 })], ['runs/name-' + display[0].label, '']], needles);
    assert.equal(hits.length, 3, `control: a planted word, a planted key and a planted name are found: ${hits}`);
  });

  await t.test('test 19 on a click: every Python process the click ran refused sockets, and nothing was served from beyond loopback', async () => {
    const now = await status();
    assert.equal(now.counters.wrong_host, 0);
    assert.deepEqual(now.refused.filter(row => row.method !== 'GET'), []);
    assert.ok(now.counters.feed_posts >= display.length && now.counters.aim_posts >= 1, 'the only writes were the feed and the aim check');
    // The click's Python processes: chain.py (its tool processes run under its --guarded refusal, the ask, person
    // and coordinator steps are chain.py itself) and dry_run.py. Each installs the refusal before it imports
    // anything; test-den-sim.mjs proves the hook refuses. Here: no process of the click ran anything else.
    const argv = fs.readFileSync(path.join(clicks['Reading on the couch'].run, `click-${clicks['Reading on the couch'].click.click}`, 'argv.jsonl'), 'utf8')
      .split('\n').filter(Boolean).map(line => JSON.parse(line).argv);
    assert.ok(argv.length >= 3 && argv.every(([guarded, file]) => guarded === '--guarded' && /tools[\\/]den-(observe|track|bundle)[\\/]/.test(file)), JSON.stringify(argv));
    for (const name of ['chain.py', 'dry_run.py', 'ask_one.py']) assert.match(fs.readFileSync(path.join(HERE, name), 'utf8'), /sys\.addaudithook\(/, name);
    const routes = /import ask_la|ask_la\.main|\[["'](ssh|scp)["']|http\.client|urllib|socket\.create_connection/;
    for (const name of ['chain.py', 'brain.mjs', 'click.mjs', 'run.mjs']) assert.doesNotMatch(fs.readFileSync(path.join(HERE, name), 'utf8'), routes, `${name}: no route to LA from a click`);
    assert.match("subprocess.run(['ssh', 'x'])", routes, 'control: a planted route is found');
  });

  await t.test('test 2\'s delete through the harness: with the truth folder and the display file gone, the Decisions are byte-identical', async () => {
    await run.stop();
    // A copy of hard links, without the truth folder or the display file: nothing in it is written through.
    const copy = path.join(home, 'no-truth', path.basename(SET));
    (function link(from, to, top) {
      fs.mkdirSync(to, { recursive: true });
      for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
        if (top && ['truth', 'display.json'].includes(entry.name)) continue;
        if (entry.isDirectory()) link(path.join(from, entry.name), path.join(to, entry.name), false); else fs.linkSync(path.join(from, entry.name), path.join(to, entry.name));
      }
    })(SET, copy, true);
    assert.ok(!fs.existsSync(path.join(copy, 'truth')) && !fs.existsSync(path.join(copy, 'display.json')));
    const second = harness(home, copy);
    try {
      await second.ready;
      const folder = done => path.join(done.run, `click-${done.click.click}`);
      const files = ['observe/observations.jsonl', 'track/tracks.jsonl', 'requests.jsonl', 'bundles.jsonl', 'asks.jsonl', 'facts.jsonl', 'occupancy.jsonl', 'person_states.jsonl',
        'decide/decisions.jsonl', 'decide/surfaces.json'];
      const decisions = {}, again = report((await status()).run);
      for (const label of ['Reading on the couch', 'Holding a phone']) {
        const before = clicks[label], after = await click(before.click.take, again);
        for (const name of files) assert.ok(fs.readFileSync(path.join(folder(before), name)).equals(fs.readFileSync(path.join(folder(after), name))), `${label}: ${name}`);
        decisions[label] = fs.readFileSync(path.join(folder(after), 'decide', 'decisions.jsonl'), 'utf8');
      }
      assert.ok(decisions['Reading on the couch'].includes('"actuator_domain": "virtual-den-4"') || decisions['Reading on the couch'].includes('"actuator_domain":"virtual-den-4"'), 'reading wrote a spot Decision');
      assert.notEqual(decisions['Reading on the couch'], decisions['Holding a phone'], 'control: the comparison tells reading\'s Decisions from phone\'s none');
    } finally { await second.stop(); }
  });
});
