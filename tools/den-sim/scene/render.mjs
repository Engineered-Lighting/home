// Hop 1: render the scenes in Playwright's chrome-headless-shell, software GL, on 127.0.0.1 only.
//
//   node tools\den-sim\scene\render.mjs --job <job.json>
//
// prepare.py writes the job: {shots, seed, items: [{name, person, marks, poses}], out, truth, scratch}.
// Item names are prepare's random words, so a scene's name lives only in the file names here, the
// display file and the truth folder. Pictures go to `out`; true joints go to `truth` only. Chromium's
// profile and TEMP stay under `scratch`, the browser closes in a finally, and the scratch folder goes.
// Prints one JSON line: renderer, three's revision, the files read with their digests, timings, and
// how many requests left the loopback origin (refused, and it should be zero). Needs Playwright, found
// through NODE_PATH or a node_modules above this file; installs nothing.
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../../..');
const VENDOR = path.join(ROOT, 'app/src/vendor/three-0.180.0');
const SHELL = path.join(os.homedir(), 'AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe');
const NAME = /^[a-z0-9]{1,32}\.p[0-9]{1,2}\.(?:rgb\.rgba|left\.rgba|right\.rgba|depth\.f32)$/;
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const at = process.argv.indexOf('--job');
if (at < 2) { console.error('usage: node render.mjs --job <job.json>'); process.exit(2); }
const job = JSON.parse(fs.readFileSync(process.argv[at + 1], 'utf8'));
const scratch = path.resolve(job.scratch), tmp = path.join(scratch, 'tmp');
fs.mkdirSync(tmp, { recursive: true }); fs.mkdirSync(job.out, { recursive: true }); fs.mkdirSync(job.truth, { recursive: true });
// Playwright makes its browser profile in os.tmpdir(), which reads TEMP and TMP; the browser inherits them.
process.env.TEMP = process.env.TMP = process.env.TMPDIR = tmp;
const { installPack } = await import('../../fixtures/apartment-home-shell-fixtures.mjs');
const ROOM = installPack(path.join(scratch, 'pack'), 'den-sim-scene').files['collision.glb'];   // the authored room, built in scratch

const read = {};                                  // every file served to the page, with its digest
const serveFile = file => { const bytes = fs.readFileSync(file); read[path.relative(ROOT, file).split(path.sep).join('/')] = sha256(bytes); return bytes; };
const types = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json' };
let finish, fail;
const done = new Promise((resolve, reject) => { finish = resolve; fail = reject; });
let written = 0;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (req.method === 'POST') {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks), name = path.basename(url.pathname);
      if (url.pathname === '/done') finish(JSON.parse(body.toString('utf8')));
      else if (url.pathname === '/failed') fail(Error('the page failed: ' + body.toString('utf8').slice(0, 400)));
      else if (url.pathname.startsWith('/out/') && NAME.test(name)) { fs.writeFileSync(path.join(job.out, name), body); written++; }
      else if (url.pathname.startsWith('/truth/') && /^[a-z0-9]{1,32}\.json$/.test(name)) {
        const folder = path.join(job.truth, name.slice(0, -5));
        fs.mkdirSync(folder, { recursive: true }); fs.writeFileSync(path.join(folder, 'poses.json'), body);
      } else { res.writeHead(404); return res.end(); }
      res.writeHead(200); res.end();
    });
    return;
  }
  let body = null, file = null;
  if (url.pathname === '/') file = path.join(HERE, 'page.html');
  else if (url.pathname === '/figure.js') file = path.join(HERE, 'figure.js');
  else if (url.pathname === '/job.json') body = Buffer.from(JSON.stringify({ shots: JSON.parse(serveFile(job.shots).toString('utf8')), seed: job.seed, items: job.items }));
  else if (url.pathname === '/room.glb') { body = ROOM; read['collision.glb (authored pack, built here)'] = sha256(ROOM); }
  else if (url.pathname.startsWith('/vendor/')) {
    const candidate = path.resolve(VENDOR, '.' + url.pathname.slice(7));
    if (candidate.startsWith(VENDOR + path.sep)) file = candidate;
  }
  if (file && fs.existsSync(file)) body = serveFile(file);
  if (!body) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'Content-Type': types[path.extname(file || url.pathname)] || 'application/octet-stream' });
  res.end(body);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port, external = [], started = Date.now();
let browser = null, code = 0;
try {
  const { chromium } = createRequire(import.meta.url)('playwright');
  const args = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--disable-background-networking', '--disable-component-update'];
  browser = await chromium.launch({ headless: true, args, ...(fs.existsSync(SHELL) ? { executablePath: SHELL } : { channel: 'chrome' }) });
  const page = await browser.newPage();
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(origin + '/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
    external.push(url); return route.abort();
  });
  const logs = [];
  page.on('pageerror', error => logs.push('pageerror: ' + error.message.slice(0, 300)));
  await page.goto(origin + '/');
  const result = await Promise.race([done, new Promise((_, reject) => setTimeout(() => reject(Error('timed out after 180 s')), 180000))]);
  console.log(JSON.stringify({ ok: external.length === 0, renderer: result.renderer, three: result.three, wall_s: (Date.now() - started) / 1000,
    files_written: written, external_requests: external.length, read, timings_ms: result.timings_ms, errors: logs }));
  if (external.length) code = 1;
} catch (error) {
  console.log(JSON.stringify({ ok: false, reason: error.message.slice(0, 400), external_requests: external.length }));
  code = 1;
} finally {
  if (browser) await browser.close();           // removes Playwright's temporary profile
  server.close();
  fs.rmSync(scratch, { recursive: true, force: true });
}
process.exit(code);
