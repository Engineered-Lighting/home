// Authored-room browser acceptance for the actual Home Apartment shell.
// Uses the production renderer and private local test registration, never a
// household pack, live camera, model session, physical command or deployment.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';
import { waitForDirectionalReadiness } from './apartment-directional-readiness.mjs';
import { createDirectionalNativeMouse, assertDirectionalNativeMouseLedger } from './apartment-directional-native-mouse.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const screenshotsEnabled = process.env.HOME_APARTMENT_BROWSER_SCREENSHOTS !== '0';

// Source assets are served only from this public-code inventory. Authored pack
// assets have their own fixture routes and never come from app runtime data.
function staticSources(root) {
  const sources = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  const rootStat = fs.lstatSync(root);
  assert.ok(rootStat.isDirectory() && !rootStat.isSymbolicLink(), 'static source root must be an ordinary directory');
  let entries = 0, bytes = 0;
  function walk(directory, depth = 0) {
    assert.ok(depth <= 12, 'static source depth is bounded');
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      assert.ok(++entries <= 10000, 'static source inventory is bounded');
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/');
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/')) continue;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { walk(file, depth + 1); continue; }
      if (!stat.isFile()) continue;
      const trustedAsset = /^(?:vendor|fonts|icons)\//.test(relative)
        && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !trustedAsset && !rootAsset) continue;
      bytes += stat.size;
      assert.ok(stat.size <= 32 * 1024 * 1024 && bytes <= 256 * 1024 * 1024, 'static source bytes are bounded');
      sources.set('/' + relative, { selected: file, relative });
    }
  }
  walk(root); return sources;
}

function worktreeSources() {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'standalone browser proof must use this worktree; candidate source overlays are forbidden');
  return staticSources(SRC);
}

function browserLaunchOptions(platform = process.platform) {
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || platform === 'win32', 'the pinned Playwright Chromium browser must be available on this platform');
  // Use the full pinned browser that executablePath() verifies, rather than
  // Playwright's separate legacy headless shell on CPU-only Linux runners.
  return { headless: true, args, ...(platform === 'linux' ? { channel: 'chromium' }
    : !installed && platform === 'win32' ? { channel: 'msedge' } : {}) };
}

// Part 1: the Chat presenter proof, then boot, Echo, Victoria fixtures,
// radial, drags and the command ladder. Part 2: camera, metrics, the classic
// escape hatch and the unified URL override, from the same booted baseline.
// Part 3: the classic URL override and the classic card from a fresh page.
export async function runShellBrowserProof({ part }) {
  assert.ok([1, 2, 3].includes(part), 'shell proof part 1, 2 or 3');
  const run = path.join(ROOT, '.tmp/apartment-home-shell-browser', 'run-' + Date.now() + '-part' + part);
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  const initial = initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = worktreeSources(), requests = [], forbidden = [], missingResources = [], missingCodeSources = [],
    errors = [], checks = [], screenshots = [], served = {};
  const launchOptions = browserLaunchOptions();
  const receipt = { schema: 'home-apartment-shell-authored-browser-1', part, authored_room: true, real_renderer: true,
    real_household_data: false, live_camera: false, model_session_started: false, physical_commands: 0, survey_writes: 0,
    source_provenance: { mode: 'worktree_only', root: fs.realpathSync(SRC), candidate_overlay: false },
    browser_launch: { platform: process.platform, ...launchOptions },
    screenshots_enabled: screenshotsEnabled,
    checks, screenshots, requests, forbidden, missing_resources: missingResources, missing_code_sources: missingCodeSources, errors, served };
  let server, browser, page, origin, handler, timer;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    receipt.deadline_at = Date.now(); save();
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(new Error('145s authored shell browser deadline (part ' + part + ')'));
  }, 145000); });

  async function chatPresenterMountProof() {
    const source = name => {
      const selected = sources.get('/' + name)?.selected;
      assert.ok(selected, 'Chat proof code belongs to the bounded worktree source inventory: ' + name);
      const bytes = fs.readFileSync(selected);
      return { selected, sha256: hash(bytes), text: bytes.toString('utf8') };
    };
    const assets = Object.fromEntries(['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js',
      'vendor/babel-7.29.0/babel.min.js', 'home-events.jsx', 'home-app.jsx'].map(name => [name, source(name)]));
    const babel = require(assets['vendor/babel-7.29.0/babel.min.js'].selected);
    const app = assets['home-app.jsx'].text;
    const presenter = babel.transform(app, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program.body
      .find(node => node.type === 'FunctionDeclaration' && node.id.name === 'ApartmentChatTurn');
    assert.ok(presenter, 'actual Chat presenter is available for the stateful React proof');
    const proofContext = await browser.newContext({ serviceWorkers: 'block' }), network = [];
    await proofContext.route('**/*', route => { network.push(route.request().url()); return route.abort(); });
    await proofContext.routeWebSocket('**/*', socket => socket.close());
    const proofPage = await proofContext.newPage(); proofPage.setDefaultTimeout(5000);
    proofPage.on('pageerror', error => errors.push('authored actual Chat proof: ' + error.message));
    try {
      await proofPage.setContent('<main id="authored-chat-root"></main>');
      for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js']) {
        await proofPage.addScriptTag({ content: assets[name].text });
      }
      await proofPage.addScriptTag({ content: babel.transform(assets['home-events.jsx'].text, { presets: ['react'] }).code });
      await proofPage.addScriptTag({ content: babel.transform(app.slice(presenter.start, presenter.end), { presets: ['react'] }).code });
      const snapshots = [];
      for (const conditionalTypeMutation of [false, true]) {
        await proofPage.evaluate(conditional => {
          const state = window.__chatActualProof = { mounts: 0, cleanups: 0, loads: 0, undoCalls: [], conditional };
          const mountReview = slot => {
            state.mounts++;
            const frame = document.createElement('iframe'); frame.title = 'Authored private review';
            frame.srcdoc = '<script>parent.__chatActualProof.loads += 1</' + 'script>';
            state.originalFrame ||= frame; state.currentFrame = frame; slot.append(frame);
            return () => { state.cleanups++; frame.remove(); };
          };
          state.group = { speaker: 'home', time: '12:00:00', events: [
            { id: 'authored-stream', kind: 'home', text: 'Authored streaming partial', streaming: true },
            { id: 'authored-external', kind: 'external', text: 'Authored external partial', streaming: true },
            { id: 'authored-action', kind: 'action', title: 'Authored local action', service: 'light.turn_on', target: 'authored.only', status: 'success' },
            { id: 'authored-review', kind: 'personal-memory-review', text: 'Authored private review', mountReview },
          ] };
          state.root = ReactDOM.createRoot(document.getElementById('authored-chat-root'));
          window.__renderActualChatProof = unified => ReactDOM.flushSync(() => state.root.render(React.createElement(
            conditional && !unified ? window.TurnBlock : ApartmentChatTurn,
            { group: state.group, density: 'compact', unified, onUndoAction: async action => { state.undoCalls.push(action); } })));
          window.__renderActualChatProof(true);
        }, conditionalTypeMutation);
        await proofPage.waitForFunction(() => window.__chatActualProof.loads === 1);
        await proofPage.getByRole('button', { name: 'undo', exact: true }).click();
        await proofPage.waitForFunction(() => window.__chatActualProof.undoCalls.length === 1
          && ![...document.querySelectorAll('button')].some(button => button.textContent === 'undo'));
        for (const unified of conditionalTypeMutation ? [false] : [false, true]) {
          await proofPage.evaluate(mode => window.__renderActualChatProof(mode), unified);
          if (conditionalTypeMutation) await proofPage.waitForFunction(() => window.__chatActualProof.loads === 2);
          const observed = await proofPage.evaluate(() => {
            const state = window.__chatActualProof;
            return { mounts: state.mounts, cleanups: state.cleanups, loads: state.loads, undoCalls: state.undoCalls.length,
              undoButtons: [...document.querySelectorAll('button')].filter(button => button.textContent === 'undo').length,
              streamingCarets: document.querySelectorAll('.hg-caret').length,
              sameReviewFrame: document.querySelector('iframe') === state.originalFrame };
          });
          snapshots.push({ conditional_type_mutation: conditionalTypeMutation, unified, ...observed });
          assert.deepEqual(observed, conditionalTypeMutation
            ? { mounts: 2, cleanups: 1, loads: 2, undoCalls: 1, undoButtons: 1, streamingCarets: 2, sameReviewFrame: false }
            : { mounts: 1, cleanups: 0, loads: 1, undoCalls: 1, undoButtons: 0, streamingCarets: 2, sameReviewFrame: true });
        }
        if (!conditionalTypeMutation) {
          await proofPage.evaluate(() => {
            const state = window.__chatActualProof;
            state.group.events[0] = { ...state.group.events[0], text: 'Authored streaming completion', streaming: false };
            window.__renderActualChatProof(true);
          });
          await proofPage.getByText('Authored streaming completion', { exact: true }).waitFor();
          const updated = await proofPage.evaluate(() => ({ carets: document.querySelectorAll('.hg-caret').length,
            mounts: window.__chatActualProof.mounts, loads: window.__chatActualProof.loads }));
          assert.deepEqual(updated, { carets: 1, mounts: 1, loads: 1 });
        }
        await proofPage.evaluate(() => ReactDOM.flushSync(() => window.__chatActualProof.root.unmount()));
      }
      assert.deepEqual(network, [], 'the stateful Chat proof performs no URL requests');
      receipt.chat_presenter = { actual_react: true, actual_event_content: true, snapshots, network_requests: network,
        sources: Object.fromEntries(Object.entries(assets).map(([name, value]) => [name, { file: value.selected, sha256: value.sha256 }])) };
      checks.push({ stage: 'actual_react_chat_undo_review_and_stream_survive_layout_switch' });
    } finally { await proofContext.close(); }
  }

  async function exercise() {
    server = http.createServer(async (req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost');
        requests.push({ path: url.pathname, method: req.method });
        if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
          forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
        }
        if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
        let body, type = 'application/octet-stream';
        if (url.pathname.startsWith('/api/perception-lab/v1/')) {
          const route = url.pathname.slice('/api/perception-lab/v1/'.length);
          const value = route === 'capabilities' ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {},
            recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
            : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] }
              : route === 'recordings' ? { items: [] } : null;
          if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
        }
        if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
        else {
          const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
          if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; }
        }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry && !url.pathname.startsWith('/assets/apartment/')) {
          const relative = path.relative(fs.realpathSync(SRC), fs.realpathSync(entry.selected)).split(path.sep).join('/');
          assert.ok(relative === entry.relative && !relative.startsWith('../') && !path.isAbsolute(relative), 'served source stays in this worktree');
          body = fs.readFileSync(entry.selected);
          served[entry.relative] = { sha256: hash(body), source: 'worktree', path: 'app/src/' + entry.relative };
          type = /\.(?:js|mjs|jsx)$/.test(entry.selected) ? 'text/javascript' : entry.selected.endsWith('.html') ? 'text/html'
            : entry.selected.endsWith('.css') ? 'text/css' : /\.(?:json|webmanifest)$/.test(entry.selected) ? 'application/json'
              : entry.selected.endsWith('.svg') ? 'image/svg+xml' : type;
        }
        if (!body) {
          const missing = { path: url.pathname, method: req.method, status: 404 };
          missingResources.push(missing);
          if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) missingCodeSources.push(missing);
          res.writeHead(404); res.end(); return;
        }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' });
        res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) { errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = 'http://127.0.0.1:' + server.address().port;
    handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
      authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
    browser = await chromium.launch(launchOptions);
    const graphicsSession = await browser.newBrowserCDPSession();
    try {
      const { gpu } = await graphicsSession.send('SystemInfo.getInfo');
      let executable = null;
      if (process.platform === 'linux') {
        const { processInfo } = await graphicsSession.send('SystemInfo.getProcessInfo');
        const browserProcess = processInfo.find(item => item.type === 'browser');
        assert.ok(Number.isSafeInteger(browserProcess?.id) && browserProcess.id > 0, 'the Linux browser process is identified');
        executable = fs.realpathSync('/proc/' + browserProcess.id + '/exe');
        assert.equal(executable, fs.realpathSync(chromium.executablePath()),
          'Linux acceptance uses the full pinned Chromium binary, not a system browser or headless shell');
      }
      receipt.browser_graphics = { version: browser.version(), feature_status: gpu.featureStatus,
        executable: executable && path.basename(executable), channel: launchOptions.channel || null };
    } finally { await graphicsSession.detach(); }
    if (part === 1) await chatPresenterMountProof();
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        const harmlessProbe = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && route.request().method() === 'GET' && url.pathname === '/healthz';
        if (!harmlessProbe) forbidden.push({ origin: url.origin, path: url.pathname, method: route.request().method(), external: true });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    receipt.native_drag_mouse = [];

    async function capture(name) {
      if (!screenshotsEnabled) {
        // Functional-only runs leave visual review to the CUA browser. Retain
        // the real renderer-advancement assertion without producing a still.
        const frame = await page.evaluate(() => document.querySelector('.apt-home-shell[data-home-layout="unified"]')
          ? window.__APT_ENGINE?.renderer.info.render.frame : null);
        if (frame != null) await waitForDirectionalReadiness(page, previous => window.__APT_ENGINE?.renderer.info.render.frame > previous, frame);
        (receipt.captures ||= []).push({ name, screenshot: false, renderer_advancing: frame != null });
        return;
      }
      await page.evaluate(() => {
        if (document.getElementById('authored-shell-proof-note')) return;
        const note = document.createElement('aside'); note.id = 'authored-shell-proof-note';
        note.textContent = 'AUTHORED ROOM · REAL RENDERER · NO LIVE CONNECTION';
        Object.assign(note.style, { position: 'fixed', left: '6px', bottom: '4px', zIndex: '9999', pointerEvents: 'none',
          color: '#fff', background: '#111c', padding: '3px 5px', font: '8px system-ui,sans-serif', letterSpacing: '.06em' });
        document.body.append(note);
      });
      // Continuous WebGL draws can starve Chromium's screenshot compositor on
      // CI's software GPU. Pause only for the still, using the existing engine
      // interface; all navigation, picking and drag assertions run resumed.
      const engine = await page.evaluateHandle(() => document.querySelector('.apt-home-shell[data-home-layout="unified"]')
        ? window.__APT_ENGINE : null);
      const capture = { name, started_at: Date.now(), completed: false };
      (receipt.captures ||= []).push(capture);
      capture.graphics = await engine.evaluate(value => {
        if (!value) return null;
        const renderer = value.renderer, gl = renderer.getContext();
        const info = gl.getExtension('WEBGL_debug_renderer_info');
        return { renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
          version: gl.getParameter(gl.VERSION), context_lost: gl.isContextLost(),
          pixel_ratio: renderer.getPixelRatio(), width: gl.drawingBufferWidth, height: gl.drawingBufferHeight,
          canvas_width: renderer.domElement.width, canvas_height: renderer.domElement.height };
      });
      if (capture.graphics) {
        assert.equal(capture.graphics.context_lost, false, 'the real renderer retains its WebGL context');
        assert.ok(capture.graphics.width > 0 && capture.graphics.height > 0, 'the real WebGL framebuffer remains allocated');
        assert.equal(capture.graphics.width, capture.graphics.canvas_width);
        assert.equal(capture.graphics.height, capture.graphics.canvas_height);
        if (process.platform === 'linux') assert.match(capture.graphics.renderer, /SwiftShader/i,
          'Linux acceptance still renders the production scene through SwiftShader WebGL');
      }
      await engine.evaluate(value => value?.setRunning?.(false));
      capture.pause_ms = Date.now() - capture.started_at;
      try {
        const file = name + '.png';
        const shotStarted = Date.now();
        await page.screenshot({ path: path.join(run, file), timeout: 30000 });
        capture.screenshot_ms = Date.now() - shotStarted;
        screenshots.push(file); capture.completed = true;
      } finally {
        capture.duration_ms = Date.now() - capture.started_at;
        const resumeStarted = Date.now();
        const frame = await engine.evaluate(value => {
          if (value && value === window.__APT_ENGINE && document.querySelector('.apt-home-shell[data-home-layout="unified"]')) {
            const frame = value.renderer.info.render.frame;
            value.setRunning(true);
            return frame;
          }
          return null;
        });
        if (frame !== null) {
          await page.waitForFunction(previous => window.__APT_ENGINE?.renderer.info.render.frame > previous, frame, { timeout: 10000 });
          capture.renderer_resumed = true;
        }
        await engine.dispose();
        capture.resume_ms = Date.now() - resumeStarted;
      }
    }
    async function tab(name) {
      const tabs = page.locator('.apt-home-tabs');
      const semantic = tabs.getByRole('tab', { name, exact: true });
      await (await semantic.count() ? semantic : tabs.getByRole('button', { name, exact: true })).click();
    }
    async function timedPhase(name, operation) {
      const phase = { name, started_at: Date.now(), completed: false };
      (receipt.phases ||= []).push(phase);
      save();
      try { const result = await operation(); phase.completed = true; return result; }
      finally { phase.duration_ms = Date.now() - phase.started_at; save(); }
    }
    async function observeWithin15s(name, operation, argument) {
      const observation = { name, started_at: Date.now(), completed: false };
      (receipt.observations ||= []).push(observation); save();
      let observationTimer;
      try {
        const value = await Promise.race([page.evaluate(operation, argument), new Promise((_, reject) => {
          observationTimer = setTimeout(() => reject(Error('15s browser observation deadline: ' + name)), 15000);
        })]);
        observation.completed = true; return value;
      } finally { clearTimeout(observationTimer); observation.duration_ms = Date.now() - observation.started_at; save(); }
    }
    async function selectedTab(key) {
      await waitForDirectionalReadiness(page, key => document.activeElement?.id === 'apt-home-tab-' + key
        && document.getElementById('apt-home-tab-' + key)?.getAttribute('aria-selected') === 'true', key);
    }
    async function keyboardTabs(narrow) {
      await page.getByRole('tab', { name: 'Controls', exact: true }).focus();
      await page.keyboard.press('Home'); await selectedTab('controls');
      await page.keyboard.press('ArrowRight'); await selectedTab(narrow ? 'camera' : 'chat');
      await page.keyboard.press('ArrowRight'); await selectedTab(narrow ? 'chat' : 'controls');
      await page.keyboard.press('End'); await selectedTab('chat');
      await page.keyboard.press('ArrowRight'); await selectedTab('controls');
      await page.keyboard.press('ArrowLeft'); await selectedTab('chat');
      assert.equal(await page.locator('#apt-home-tab-camera').isVisible(), narrow);
      checks.push({ stage: narrow ? 'phone_keyboard_visible_tabs' : 'desktop_keyboard_skips_hidden_camera_tab' });
    }
    async function phoneComposerBounds() {
      const controls = [page.locator('.apt-home-chat'), page.getByRole('combobox', { name: 'Command input', exact: true }),
        page.getByRole('button', { name: 'Send', exact: true }), page.getByRole('button', { name: 'Start voice mode', exact: true })];
      // Read the four independent rectangles in one actual layout frame. The
      // named controls below still use the same strict role locators for focus.
      const bounds = await page.evaluate(() => ['.apt-home-chat', 'input[aria-label="Command input"]',
        'button[aria-label="Send"]', 'button[aria-label="Start voice mode"]'].map((selector, index) => {
        const nodes = document.querySelectorAll(selector);
        if (nodes.length !== 1) throw Error('expected one phone composer control: ' + selector);
        const node = nodes[0], rect = node.getBoundingClientRect();
        if (index) {
          for (let current = node; current; current = current.parentElement)
            if (current.getAttribute('aria-hidden')?.toLowerCase() === 'true') throw Error('hidden phone composer control');
          if (node.hasAttribute('aria-labelledby')) throw Error('unsupported renamed phone composer control');
          if (index > 1 && node.hasAttribute('role') && node.getAttribute('role') !== 'button') throw Error('wrong phone composer button role');
        }
        if (index === 1 && node.getAttribute('role') !== 'combobox') throw Error('wrong Command input role');
        if (!node.getClientRects().length || getComputedStyle(node).visibility !== 'visible') return null;
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
      }));
      const viewport = page.viewportSize();
      receipt.phone_composer = { viewport, bounds };
      for (const [index, rect] of bounds.entries()) {
        assert.ok(rect, 'phone composer control ' + index + ' is visible');
        assert.ok(rect.width > 0 && rect.height > 0 && rect.x >= -.5 && rect.y >= -.5
          && rect.x + rect.width <= viewport.width + .5 && rect.y + rect.height <= viewport.height + .5,
        'phone composer control ' + index + ' stays inside viewport: ' + JSON.stringify(rect));
      }
      for (const control of controls.slice(1)) {
        await control.focus(); assert.equal(await control.evaluate(element => element === document.activeElement), true);
      }
      checks.push({ stage: 'phone_chat_composer_send_and_mic_inside_viewport', viewport, bounds });
    }
    async function waitSite(site) {
      await waitForDirectionalReadiness(page, site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready'
        && window.__APT_ENGINE?.siteId === site, site);
    }
    async function selectHome(site) {
      const homes = page.getByRole('group', { name: 'Home', exact: true });
      const button = homes.getByRole('button', { name: site === 'victoria' ? 'Victoria' : 'Echo Park', exact: true });
      await button.click(); await waitSite(site);
      assert.equal(await button.getAttribute('aria-pressed'), 'true', 'segmented Home control reflects the selected site');
    }
    async function echoCardBounds() {
      const results = [];
      for (const [width, height] of [[1366,768], [390,844]]) {
        await page.setViewportSize({ width, height }); await tab('Controls');
        const select = page.locator('[data-apt-semantic-device-select="1"]');
        await select.focus(); await select.selectOption('authored-echo-light');
        const open = page.locator('[data-apt-semantic-open="1"]');
        await open.focus(); await open.press('Enter');
        // APT-L04: an Echo light opens the shared Light inspector, not the legacy card.
        const close = page.locator('[data-apt-echo-light-inspector="1"]').getByRole('button', { name: 'Close light', exact: true });
        await close.waitFor();
        const bounds = await close.evaluate(button => {
          const inspector = button.closest('[data-apt-echo-light-inspector="1"]'), card = inspector.querySelector('.apt-ui-header');
          const panel = document.querySelector('.apt-home-controls');
          return { card: card.getBoundingClientRect().toJSON(), panel: panel.getBoundingClientRect().toJSON(),
            close: button.getBoundingClientRect().toJSON(), inControls: !!button.closest('.apt-home-controls'),
            name: card.innerText, entity: inspector.querySelector('[data-apt-echo-entity="1"]')?.textContent,
            blocked: inspector.querySelector('[data-apt-echo-blocked]')?.innerText || null,
            powerDisabled: inspector.querySelector('input[type="checkbox"]')?.disabled === true,
            panelScrollWidth: panel.scrollWidth, panelClientWidth: panel.clientWidth };
        });
        assert.match(bounds.name, /Authored Echo light/); assert.equal(bounds.entity, 'light.authored_echo');
        assert.ok(bounds.blocked && bounds.powerDisabled, 'without a connected Home Assistant the Echo controls are disabled with a reason');
        const inside = (inner, outer) => inner.width > 0 && inner.height > 0 && inner.left >= outer.left - 1
          && inner.right <= outer.right + 1 && inner.top >= outer.top - 1 && inner.bottom <= outer.bottom + 1;
        const result = { stage: 'echo_generic_card_within_controls', viewport: { width, height }, ...bounds,
          cardInside: inside(bounds.card, bounds.panel), closeInside: inside(bounds.close, bounds.panel) };
        checks.push(result); results.push(result);
        await capture('unified-echo-card-' + width + 'x' + height);
        await close.focus(); await close.press('Enter'); await close.waitFor({ state: 'hidden' });
        assert.equal(requests.some(row => !['GET', 'HEAD'].includes(row.method)), false, 'opening and closing the generic card sends no service command');
      }
      await page.setViewportSize({ width: 1366, height: 768 });
      assert.deepEqual(results.filter(result => !result.inControls || !result.cardInside || !result.closeInside
        || result.panelScrollWidth > result.panelClientWidth + 1), [],
        'the actual generic Echo card and close control must fit their control pane at desktop and phone widths');
    }
    async function identityCheck(stage) {
      const state = await page.evaluate(() => ({
        engine: window.__APT_ENGINE === window.__shellProof.engine,
        canvas: window.__APT_ENGINE?.renderer.domElement === window.__shellProof.canvas,
        controller: window.__havUiDebug === window.__shellProof.controller,
        input: document.querySelector('input[aria-label="Command input"]') === window.__shellProof.input,
        inputs: document.querySelectorAll('input[aria-label="Command input"]').length,
        draft: document.querySelector('input[aria-label="Command input"]')?.value,
        canvases: document.querySelectorAll('.apt-home-stage canvas').length,
        overflow: document.documentElement.scrollWidth > innerWidth,
        stage: (() => { const rect = document.querySelector('.apt-home-stage').getBoundingClientRect(); return { width: rect.width, height: rect.height }; })(),
      }));
      for (const field of ['engine', 'canvas', 'controller', 'input']) assert.equal(state[field], true, stage + ' retains ' + field);
      assert.equal(state.inputs, 1); assert.equal(state.canvases, 1, 'one renderer canvas throughout shell navigation');
      assert.equal(state.draft, 'Authored unsent shell draft'); assert.equal(state.overflow, false, stage + ' has no document overflow');
      assert.ok(state.stage.width > 0 && state.stage.height > 0, stage + ' retains an allocated room viewport');
      checks.push({ stage, ...state });
    }
    const command = page.getByRole('combobox', { name: 'Command input', exact: true });
    if (part === 1) {
      await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
      await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
      await waitSite('echo');
      await echoCardBounds();
      await selectHome('victoria');
      await waitForDirectionalReadiness(page, () => window.__APT_ENGINE?.overlay.virtualFixturesGroup?.children.length === 4);
      const overviewFixtures = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures);
      assert.equal(overviewFixtures.length, 4);
      await page.getByRole('button', { name: 'All off', exact: true }).click();
      await waitForDirectionalReadiness(page, () => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.every(item =>
        item.state.spotlight.on === false && item.state.downward.on === false && item.state.radials.length === 6
          && item.state.radials.every(zone => zone.on === false)));
      await page.getByRole('button', { name: 'All on', exact: true }).click();
      await waitForDirectionalReadiness(page, () => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.every(item =>
        item.state.spotlight.on === true && item.state.downward.on === true && item.state.radials.every(zone => zone.on === true)));
      assert.deepEqual(await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures), overviewFixtures,
        'modeled batch on/off restores the exact four authored fixtures without changing their channels or registration');
      assert.equal(requests.some(row => !['GET', 'HEAD'].includes(row.method)), false, 'modeled batch controls send no output command');
      checks.push({ stage: 'four_fixture_modeled_batch_has_no_output_and_restores_initial_state' });
      await tab('Chat');
      await command.fill('Authored unsent shell draft');
      await page.evaluate(() => { window.__shellProof = { engine: window.__APT_ENGINE, canvas: window.__APT_ENGINE.renderer.domElement,
        controller: window.__havUiDebug, input: document.querySelector('input[aria-label="Command input"]') }; });
      await tab('Controls'); await tab('Chat'); await identityCheck('desktop_tabs_keep_unsent_chat');
      await keyboardTabs(false);
      await tab('Controls');
      for (const [width, height] of [[1366,768], [820,1180], [390,844]]) {
        await page.setViewportSize({ width, height });
        await waitForDirectionalReadiness(page, () => window.__APT_ENGINE?.renderer.domElement.width > 0);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await identityCheck('viewport_' + width + 'x' + height);
        await capture('unified-controls-' + width + 'x' + height);
      }
      const lastRoomFixture = overviewFixtures.at(-1);
      const roomFixtureButton = page.getByRole('button', { name: 'Select ' + (lastRoomFixture.label || lastRoomFixture.fixture_id), exact: true });
      await roomFixtureButton.scrollIntoViewIfNeeded();
      const roomScrollBefore = await page.locator('#apt-home-panel-controls').evaluate(panel => panel.scrollTop);
      assert.ok(roomScrollBefore > 0, 'the phone room list genuinely scrolls before selecting its fourth fixture');
      await roomFixtureButton.click();
      await page.getByRole('button', { name: 'All lights', exact: true }).waitFor();
      const selectedPhoneBounds = await page.locator('[aria-label="Fixture lighting inspector"] .apt-ui-title').evaluate(title => {
        const panel = document.getElementById('apt-home-panel-controls');
        return { title: title.getBoundingClientRect().toJSON(), panel: panel.getBoundingClientRect().toJSON(), scrollTop: panel.scrollTop };
      });
      assert.equal(selectedPhoneBounds.scrollTop, 0, 'phone list selection starts the inspector at its header');
      assert.ok(selectedPhoneBounds.title.top >= selectedPhoneBounds.panel.top - .5
        && selectedPhoneBounds.title.bottom <= selectedPhoneBounds.panel.bottom + .5, 'the selected phone fixture title is visible inside Controls');
      await page.getByRole('button', { name: 'All lights', exact: true }).click();
      assert.equal(await page.locator('#apt-home-panel-controls').evaluate(panel => panel.scrollTop), 0, 'returning to the room starts at its header');
      checks.push({ stage: 'phone_scrolled_room_list_selection_starts_at_inspector_top', room_scroll_before: roomScrollBefore,
        selected_fixture_id: lastRoomFixture.fixture_id, ...selectedPhoneBounds });
      await keyboardTabs(true);
      await tab('Chat'); await identityCheck('phone_chat_draft'); await phoneComposerBounds(); await capture('unified-chat-390x844');
      await page.setViewportSize({ width: 1366, height: 768 }); await tab('Controls');
      await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0));

      async function waitAimViewport(stage) {
        // Wait for actual host/projection readiness after ResizeObserver refits.
        // Ambient hover parallax may keep moving the camera; the pointer-up
        // observer below binds its expected ray to that actual input frame.
        const settled = await page.evaluate(async () => {
          const engine = window.__APT_ENGINE, identity = engine.sceneIdentity, started = performance.now();
          let previous = null, stableFrames = 0, sample = null;
          while (performance.now() - started < 12000) {
            await new Promise(resolve => requestAnimationFrame(resolve));
            if (window.__APT_ENGINE !== engine || engine.siteId !== 'victoria' || engine.sceneIdentity !== identity
                || !document.querySelector('.apt-home-shell[data-home-layout="unified"]')) throw Error('aim view scope changed while settling');
            const canvas = engine.renderer.domElement, rect = canvas.getBoundingClientRect(), host = canvas.parentElement.getBoundingClientRect();
            const pivot = engine.rig.state.pivot;
            const pivotError = Math.max(Math.abs(pivot.tx - pivot.cx), Math.abs(pivot.ty - pivot.cy),
              Math.abs(pivot.cx - pivot.dx), Math.abs(pivot.cy - pivot.dy));
            sample = { frame: engine.renderer.info.render.frame, rect: rect.toJSON(), host: host.toJSON(),
              aspect: engine.camera.aspect, pivot: { ...pivot }, pivot_error: pivotError,
              camera: [...engine.camera.matrixWorld.elements], projection: [...engine.camera.projectionMatrix.elements],
              root: [...engine.apartmentRoot.matrixWorld.elements] };
            const values = [rect.x, rect.y, rect.width, rect.height, ...sample.projection];
            const allocated = rect.width > 0 && rect.height > 0 && Math.abs(rect.width - host.width) < .5
              && Math.abs(rect.height - host.height) < .5 && Math.abs(engine.camera.aspect - rect.width / rect.height) < 1e-6;
            if (previous && sample.frame > previous.frame) {
              const unchanged = values.every((value, index) => Math.abs(value - previous.values[index]) < 1e-4);
              stableFrames = allocated && unchanged ? stableFrames + 1 : 0;
              if (stableFrames >= 3) return { ...sample, stable_frames: stableFrames, elapsed_ms: performance.now() - started };
            }
            if (!previous || sample.frame > previous.frame) previous = { frame: sample.frame, values };
          }
          throw Error('12s aim viewport/projection did not settle: ' + JSON.stringify(sample));
        });
        checks.push({ stage, ...settled });
        return settled;
      }
      await waitAimViewport('desktop_viewport_ready_before_picking');
      const aimDescriptor = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), id);
      async function fixturePoint(id) {
        return page.evaluate(id => {
          const engine = window.__APT_ENGINE, descriptor = engine.overlay.virtualFixtureInteractionDescriptor(id);
          engine.apartmentRoot.updateWorldMatrix(true, true);
          const point = engine.apartmentRoot.position.clone().set(...descriptor.display_anchor_m); point.z -= .08;
          engine.apartmentRoot.localToWorld(point); point.project(engine.camera);
          const rect = engine.renderer.domElement.getBoundingClientRect();
          return { x: rect.left + (point.x + 1) * rect.width / 2, y: rect.top + (1 - point.y) * rect.height / 2 };
        }, id);
      }
      async function targetPoint(point) {
        return page.evaluate(async point => {
          const engine = window.__APT_ENGINE; await engine.modes.preload(['collision']);
          engine.apartmentRoot.updateWorldMatrix(true, true);
          const projected = engine.apartmentRoot.localToWorld(engine.apartmentRoot.position.clone().set(...point)).project(engine.camera);
          const rect = engine.renderer.domElement.getBoundingClientRect();
          const x = rect.left + (projected.x + 1) * rect.width / 2, y = rect.top + (1 - projected.y) * rect.height / 2;
          const hit = engine.picking.surfaceHit(engine.apartmentRoot, [engine.modes.getCollision()], x, y);
          const displayHit = engine.displaySurfaceHit(x, y);
          return { x, y, hit: hit && { point: hit.point, normal: hit.normal },
            display_hit: displayHit && { point: displayHit.point, normal: displayHit.normal },
            frame: engine.renderer.info.render.frame, rect: rect.toJSON(),
            camera: [...engine.camera.matrixWorld.elements], projection: [...engine.camera.projectionMatrix.elements] };
        }, point);
      }
      const fixtureIds = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.map(item => item.fixture_id));
      assert.equal(fixtureIds.length, 4);
      const before = await page.evaluate(ids => Object.fromEntries(ids.map(id =>
        [id, window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)])), fixtureIds);
      for (const id of fixtureIds) {
        const point = await fixturePoint(id); await page.mouse.click(point.x, point.y);
        await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Setup', exact: true }).click();
        const selectedFixture = await observeWithin15s('native_fixture_selection:' + id, async id => {
          const started = performance.now();
          while (performance.now() - started < 15000) {
            if (document.querySelector('select[aria-label="Virtual fixture"]')?.value === id)
              return window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id);
            await new Promise(resolve => requestAnimationFrame(resolve));
          }
          throw Error('15s native fixture selector readiness');
        }, id);
        assert.deepEqual(selectedFixture.display_anchor_m, before[id].display_anchor_m);
      }
      const channelId = fixtureIds.at(-1), channelsBefore = await aimDescriptor(channelId);
      await page.getByRole('tab', { name: 'Light', exact: true }).click();
      const spotBrightness = page.getByRole('slider', { name: 'Brightness', exact: true });
      await spotBrightness.focus(); await spotBrightness.press('Home');
      await waitForDirectionalReadiness(page, id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.spotlight.brightness === 0, channelId);
      const channelsChanged = await aimDescriptor(channelId);
      assert.deepEqual(channelsChanged.state.downward, channelsBefore.state.downward, 'Spot brightness leaves Down independent');
      assert.deepEqual(channelsChanged.state.radials, channelsBefore.state.radials, 'Spot brightness leaves all six radial zones independent');
      assert.deepEqual(channelsChanged.state.spotlight, { ...channelsBefore.state.spotlight, brightness: 0 });
      await spotBrightness.press('End');
      await waitForDirectionalReadiness(page, ({ id, brightness }) => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.spotlight.brightness === brightness,
        { id: channelId, brightness: channelsBefore.state.spotlight.brightness });
      assert.deepEqual(await aimDescriptor(channelId), channelsBefore, 'native Spot control restores the exact original virtual descriptor');
      assert.equal(requests.some(row => !['GET', 'HEAD'].includes(row.method)), false, 'channel controls send no output command');
      checks.push({ stage: 'spot_brightness_preserves_down_and_six_radial_channels' });
      await timedPhase('radial_controls_and_native_restoration', async () => {
      const radialPanel = page.locator('section[aria-label="Radial channel"]');
      const radialOutput = (id, expected = null) => observeWithin15s('radial_drawing:' + id, async ({ id, expected }) => {
        const overlay = window.__APT_ENGINE.overlay, started = performance.now();
        while (performance.now() - started < 15000) {
          const descriptor = overlay.virtualFixtureInteractionDescriptor(id);
          const fixture = overlay.virtualFixturesGroup.getObjectByName('equipment-virtual-fixture:' + id);
          const output = descriptor.state.radials.map(zone => {
            const group = fixture.getObjectByName('radial-zone-' + zone.number);
            const sector = group.getObjectByName('radial-diffuser-sector-' + zone.number);
            const patch = group.getObjectByName('radial-receiver-patch-' + zone.number);
            return { number: zone.number, channel: { ...group.userData.channel }, sector_visible: sector.visible,
              sector_opacity: sector.material.opacity, patch_visible: patch.visible, patch_opacity: patch.material.opacity,
              receiver_kind: patch.userData.receiver_kind, illumination_role: patch.userData.illumination_role,
              receiver_vertices: patch.geometry.drawRange.count, truth_role: patch.userData.truth_role,
              metric_geometry: patch.userData.metric_geometry, physical_commands: patch.userData.physical_commands };
          });
          if (!expected || expected.every(zone => {
            const value = output.find(item => item.number === zone.number), active = zone.on && zone.brightness > 0;
            return value?.sector_visible === active && value?.patch_visible === active;
          })) return { descriptor, output };
          await new Promise(resolve => requestAnimationFrame(resolve));
        }
        throw Error('15s actual radial drawing visibility readiness');
      }, { id, expected });
      await radialPanel.getByRole('button', { name: 'Off', exact: true }).click();
      await waitForDirectionalReadiness(page, id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.radials
        .every(zone => !zone.on && zone.brightness === 0), channelId);
      await page.getByRole('checkbox', { name: 'Radial power', exact: true }).check();
      await waitForDirectionalReadiness(page, id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.radials.every(zone => zone.on), channelId);
      const masterObservation = await radialOutput(channelId), radialMasterOn = masterObservation.descriptor;
      receipt.radial_power = { fixture_id: channelId, original: channelsBefore.state.radials,
        master_on_after_off: radialMasterOn.state.radials, modeled_output: masterObservation.output };
      assert.deepEqual(radialMasterOn.state.radials, channelsBefore.state.radials.map(zone => ({ ...zone, on: true, brightness: .5 })),
        'Radial Off then master On starts all six zero-brightness zones at 50%');
      const radialCases = receipt.radial_power.cases = [];
      async function assertRadials(stage, id, expected) {
        await waitForDirectionalReadiness(page, ({ id, expected }) => JSON.stringify(window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.radials)
          === JSON.stringify(expected), { id, expected });
        // Logical channels are immediate; the specified drawing fade settles
        // separately. Keep exact visible-output checks after that native frame.
        const { descriptor, output } = await radialOutput(id, expected);
        assert.deepEqual(descriptor.state.radials, expected, stage + ' preserves every zone number, direction and color temperature');
        assert.deepEqual(descriptor.state.spotlight, before[id].state.spotlight, stage + ' leaves Spot unchanged');
        assert.deepEqual(descriptor.state.downward, before[id].state.downward, stage + ' leaves Down unchanged');
        for (const [index, modeled] of output.entries()) {
          const channel = expected[index], active = channel.on && channel.brightness > 0;
          assert.equal(modeled.number, channel.number); assert.deepEqual(modeled.channel, channel);
          assert.equal(modeled.sector_visible, active, stage + ' drives the modeled diffuser sector');
          assert.equal(modeled.patch_visible, active, stage + ' drives the registered-reference receiver patch');
          assert.equal(modeled.illumination_role, 'modeled_outward_strip');
          assert.ok(['floor', 'wall'].includes(modeled.receiver_kind) && modeled.receiver_vertices >= 3,
            stage + ' output belongs to an authored registered reference surface');
          assert.equal(modeled.truth_role, 'virtual_preview'); assert.equal(modeled.metric_geometry, false);
          assert.equal(modeled.physical_commands, false);
          if (active) assert.ok(modeled.sector_opacity > 0 && modeled.patch_opacity > 0, stage + ' has positive modeled output');
        }
        radialCases.push({ stage, fixture_id: id, radials: descriptor.state.radials, modeled_output: output });
      }
      const halfRadials = channelsBefore.state.radials.map(zone => ({ ...zone, on: true, brightness: .5 }));
      await assertRadials('all_zero_radial_master_on', channelId, halfRadials);
      // Pointer activation is covered above; repeated scenarios also exercise
      // native keyboard activation without another compositor-stability wait.
      await radialPanel.getByRole('button', { name: 'Off', exact: true }).press('Enter');
      await page.getByRole('button', { name: 'All on', exact: true }).press('Enter');
      await assertRadials('all_zero_fixture_all_on', channelId, halfRadials);

      // Author a mixed pattern through the real per-zone controls. A group power
      // operation must preserve its zero, partial and full levels independently.
      for (const [number, keys] of [[2, ['Home']], [3, ['Home', 'PageUp', 'PageUp', ...Array(5).fill('ArrowRight')]], [5, ['End']]]) {
        await radialPanel.getByRole('button', { name: 'Radial zone ' + number, exact: true }).press('Enter');
        // The supplied quick presets deliberately select all six zones. Author
        // mixed values through the selected zone's real native range instead.
        const range = page.getByRole('slider', { name: 'Radial brightness', exact: true });
        await range.focus();
        assert.equal(await range.evaluate(element => element === document.activeElement), true);
        for (const key of keys) await page.keyboard.press(key);
      }
      const mixedRadials = (await aimDescriptor(channelId)).state.radials.map(zone => ({ ...zone, on: true }));
      assert.deepEqual(mixedRadials.map(zone => zone.brightness), [.5, 0, .25, .5, 1, .5]);
      await page.getByRole('checkbox', { name: 'Radial power', exact: true }).uncheck();
      await page.getByRole('checkbox', { name: 'Radial power', exact: true }).check();
      await assertRadials('mixed_levels_radial_master_on', channelId, mixedRadials);
      assert.equal(await radialPanel.getByRole('button', { name: 'Radial zone 5', exact: true }).getAttribute('aria-pressed'), 'true',
        'group power preserves the selected zone');
      await page.getByRole('button', { name: 'All off', exact: true }).press('Enter');
      await page.getByRole('button', { name: 'All on', exact: true }).press('Enter');
      await assertRadials('mixed_levels_fixture_all_on', channelId, mixedRadials);

      const roomZeroId = fixtureIds[0];
      async function openFixtureFromRoom(id) {
        if (await page.getByRole('button', { name: 'All lights', exact: true }).isVisible()) {
          await page.getByRole('button', { name: 'All lights', exact: true }).click();
        }
        const fixture = overviewFixtures.find(item => item.fixture_id === id);
        await page.getByRole('button', { name: 'Select ' + (fixture.label || id), exact: true }).click();
        await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Light', exact: true }).click();
      }
      await openFixtureFromRoom(roomZeroId);
      await radialPanel.getByRole('button', { name: 'Off', exact: true }).press('Enter');
      await page.getByRole('button', { name: 'All lights', exact: true }).click();
      await page.getByRole('button', { name: 'All off', exact: true }).press('Enter');
      await page.getByRole('button', { name: 'All on', exact: true }).press('Enter');
      for (const id of fixtureIds) {
        const expected = id === roomZeroId ? before[id].state.radials.map(zone => ({ ...zone, on: true, brightness: .5 }))
          : id === channelId ? mixedRadials : before[id].state.radials;
        await assertRadials('room_all_on_per_fixture_levels', id, expected);
      }

      // Restore through native range-key interactions; no overlay setter or
      // hidden application state is used to prepare or undo these cases.
      for (const id of [roomZeroId, channelId]) {
        await openFixtureFromRoom(id);
        const original = before[id].state.radials;
        assert.ok(original.every(zone => zone.on && zone.brightness === .75), 'authored fixture restoration precondition');
        const range = page.getByRole('slider', { name: 'Radial brightness', exact: true });
        await range.focus();
        assert.equal(await range.evaluate(element => element === document.activeElement), true, 'native radial restoration starts on the actual focused range');
        for (const key of ['End', 'PageDown', 'PageDown', ...Array(5).fill('ArrowLeft')]) await page.keyboard.press(key);
        await assertRadials('native_restoration', id, original);
      }
      assert.deepEqual(await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures), overviewFixtures,
        'radial regression restores all four authored fixtures exactly');
      assert.equal(requests.some(row => !['GET', 'HEAD'].includes(row.method)), false, 'radial controls send no output commands');
      checks.push({ stage: 'radial_master_fixture_and_room_power_preserve_levels_identity_and_modeled_output', cases: radialCases.length });
      });
      // Selecting the already-selected cylinder must reveal its existing Controls
      // panel even when no fixture ID or details-open state changes.
      await tab('Chat');
      const selectedCylinder = await fixturePoint(fixtureIds.at(-1));
      await page.mouse.click(selectedCylinder.x, selectedCylinder.y);
      await waitForDirectionalReadiness(page, () => document.getElementById('apt-home-tab-controls')?.getAttribute('aria-selected') === 'true'
        && document.getElementById('apt-home-panel-controls')?.hidden === false);
      // Light now owns Zones. Select the native Spot tool before testing an
      // aim drag with Setup's fixture selector retaining keyboard focus.
      const spotTool = page.locator('[data-apt-directional-mode="spot"]');
      await spotTool.click();
      assert.equal(await spotTool.getAttribute('aria-pressed'), 'true');
      await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Setup', exact: true }).click();
      const fixtureSelect = page.getByRole('combobox', { name: 'Virtual fixture', exact: true });
      await fixtureSelect.focus(); await page.keyboard.press('Home'); await page.keyboard.press('ArrowDown');
      assert.equal(await fixtureSelect.evaluate(element => element === document.activeElement), true);
      assert.equal(await page.getByRole('tab', { name: 'Controls', exact: true }).getAttribute('aria-selected'), 'true');
      assert.equal(await page.locator('#apt-home-panel-controls').isVisible(), true);
      checks.push({ stage: 'same_fixture_click_from_chat_and_native_keyboard_selection_reveal_controls',
        keyboard_selected_fixture: await fixtureSelect.inputValue() });
      const directId = fixtureIds[2];
      const rigPose = () => page.evaluate(() => { const pose = window.__APT_ENGINE.rig.snapshotPose();
        return { az: pose.az, el: pose.el, zoom: pose.zoom, previewAz: pose.previewAz, previewEl: pose.previewEl, overshoot: pose.overshoot }; });
      // APT-R01/A01: only the reticle starts an aim. A press on the fixture body
      // selects it at pointer-down and never aims. This proof stays inside its
      // retained 145 s deadline, so the native, ledger-witnessed body drag
      // (no aim, no orbit, no proposal) runs in test-apartment-aim-wiring-browser.mjs.
      async function bodyPressSelects() {
        const aimBefore = await aimDescriptor(directId), poseBefore = await rigPose();
        const cylinder = await fixturePoint(directId);
        assert.equal(await page.evaluate(({ x, y }) => {
          const engine = window.__APT_ENGINE;
          return engine.picking.pick(engine.overlay.virtualFixturePickObjects(), x, y)[0]?.object.userData.fixture_id;
        }, cylinder), directId, 'the settled pointer is over the intended fixture before the actual drag');
        await page.mouse.move(cylinder.x, cylinder.y); await page.mouse.down();
        const selectedAfterDown = await waitForDirectionalReadiness(page, id => {
          const label = document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent;
          const matches = window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.filter(item => (item.label || item.fixture_id) === label);
          return matches.length === 1 && matches[0].fixture_id === id ? id : false;
        }, directId);
        assert.equal(selectedAfterDown, directId, 'pointer-down selected the fixture being dragged');
        await page.mouse.up();
        assert.deepEqual(await aimDescriptor(directId), aimBefore, 'a body press selects but never moves the aim');
        assert.deepEqual(await rigPose(), poseBefore, 'a body press never starts an orbit');
      }
      // The reticle's projected centre, from the overlay's actual DOM.
      const reticlePoint = () => page.evaluate(() => {
        const node = document.querySelector('[data-apt-aim-reticle]'), rect = node && !node.hidden ? node.getBoundingClientRect() : null;
        return rect && rect.width > 0 ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null;
      });
      async function dragAndUndo(focusCase, expectedFocus) {
        // A press on the body selects the dragged fixture when it isn't already selected.
        if (await page.getByRole('combobox', { name: 'Virtual fixture', exact: true }).count()
            && await page.getByRole('combobox', { name: 'Virtual fixture', exact: true }).inputValue() !== directId) await bodyPressSelects();
        // A canvas press deliberately ends inspector focus. Give it back to the
        // focus case's control, so the reticle drag below starts from it as the
        // original drag did.
        await page.locator(`[aria-label="${expectedFocus}"]`).focus();
        const scope = { id: receipt.native_drag_mouse.length + 1, focus_case: focusCase, native_witness_status: 'pending', observed: [], dragstarts: [] };
        receipt.native_drag_mouse.push(scope);
        const session = await context.newCDPSession(page), mouse = createDirectionalNativeMouse({
          send: (method, packet) => session.send(method, packet), onRecord: () => { scope.ledger = mouse.readback(); save(); } });
        let drag, released, scopeError, observerClosed = false;
        try {
          const focusBefore = await page.evaluate(() => {
            if (window.__shellNativeMouseScope) throw Error('overlapping authored native drag scope');
            const observed = [], dragstarts = [], types = ['pointermove', 'pointerdown', 'pointerup', 'dragstart'];
            const witness = event => {
              if (event.type !== 'dragstart' && event.pointerType !== 'mouse') return;
              const kind = event.type === 'dragstart' ? 'dragstarts' : 'observed', events = kind === 'dragstarts' ? dragstarts : observed;
              const row = { sequence: events.length + 1, type: event.type, isTrusted: event.isTrusted,
                x: event.clientX, y: event.clientY, pointerType: event.pointerType, pointerId: event.pointerId,
                button: event.button, buttons: event.buttons, pressure: event.pressure,
                modifiers: { alt: event.altKey, control: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey } };
              events.push(row);
            };
            for (const type of types) window.addEventListener(type, witness, { capture: true });
            window.__shellNativeMouseScope = { observed, dragstarts, cleanup: () => {
              for (const type of types) window.removeEventListener(type, witness, { capture: true });
              window.__shellNativeMouseScope = null;
            } };
            return { tag: document.activeElement?.tagName, label: document.activeElement?.getAttribute('aria-label') };
          });
          assert.equal(focusBefore.label, expectedFocus, 'the real inspector control retains focus before the drag');
          // The reticle is the only aim start (APT-A01). The body-press case
          // above keeps the fixture-pick readiness assertion for this fixture.
          let reticle = await reticlePoint();
          assert.ok(reticle, 'the selected fixture shows its reticle on the Spot tool');
          await mouse.move(reticle.x, reticle.y);
          await waitAimViewport('fixture_hover_viewport_ready_before_drag');
          reticle = await reticlePoint();
          await mouse.move(reticle.x, reticle.y);
          await waitAimViewport('final_fixture_hover_viewport_ready_before_drag');
          assert.equal(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('[data-apt-aim-reticle]') ? 'reticle' : null,
            reticle), 'reticle', 'the settled pointer is over the selected fixture reticle before the actual drag');
          const target = await targetPoint([2.4,2.1,0]);
          assert.ok(target.hit, 'direct aim target comes from the actual registered authored collision mesh');
          assert.ok(target.display_hit && Math.hypot(...target.display_hit.point.map((value, axis) => value - target.hit.point[axis])) < 1e-6,
            'the expected collision hit is also a visible canonical display surface');
          drag = { fixture_id: directId, focus_case: focusCase, focus_before: focusBefore, reticle, aim_before: await aimDescriptor(directId), expected: target };
          receipt[focusCase === 'setup_keyboard' ? 'drag' : 'focused_spot_drag'] = drag;
          // Observe the actual input frame before the element's production capture
          // handler. Ambient camera motion is legitimate; the committed target must
          // bind the qualified visible collision ray at pointer-up in that same turn.
          await page.evaluate(() => {
            const engine = window.__APT_ENGINE;
            window.__shellDragPointerUp = null;
            window.__shellDragFocusCleanup?.();
            window.__shellDragFocusEvents = [];
            const observeFocus = event => {
              window.__shellDragFocusEvents.push({ type: event.type, target: event.target?.tagName || (event.target === window ? 'window' : null),
                target_label: event.target?.getAttribute?.('aria-label'), active: document.activeElement?.tagName,
                active_label: document.activeElement?.getAttribute?.('aria-label'), frame: engine.renderer.info.render.frame });
            };
            const focusEventTypes = ['pointerdown', 'blur', 'pointercancel', 'lostpointercapture'];
            for (const type of focusEventTypes) window.addEventListener(type, observeFocus, { capture: true });
            window.__shellDragFocusCleanup = () => {
              for (const type of focusEventTypes) window.removeEventListener(type, observeFocus, { capture: true });
              window.__shellDragFocusCleanup = null;
            };
            window.addEventListener('pointerup', event => {
              const raw = engine.picking.surfaceHit(engine.apartmentRoot, [engine.modes.getCollision()], event.clientX, event.clientY);
              const visible = engine.displaySurfaceHit(event.clientX, event.clientY);
              // Setup's native picker legitimately unmounts when a real drag reveals
              // Aim. Bind identity to the visible selected inspector in this input
              // frame; labels must resolve uniquely to actual registered fixtures.
              const selectedLabel = document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent;
              const selectedMatches = engine.overlay.virtualFixturesReadback().fixtures.filter(item => (item.label || item.fixture_id) === selectedLabel);
              window.__shellDragPointerUp = { x: event.clientX, y: event.clientY, frame: engine.renderer.info.render.frame,
                current_engine: window.__APT_ENGINE === engine, site: engine.siteId, scene_identity: engine.sceneIdentity,
                selected_fixture_id: selectedMatches.length === 1 ? selectedMatches[0].fixture_id : null,
                selected_fixture_label: selectedLabel, selected_fixture_matches: selectedMatches.length,
                visible_hit: visible && { point: [...visible.point], normal: [...visible.normal] },
                raw_hit: raw && { point: [...raw.point], normal: [...raw.normal] },
                rect: engine.renderer.domElement.getBoundingClientRect().toJSON(),
                host: engine.renderer.domElement.parentElement.getBoundingClientRect().toJSON(),
                camera: [...engine.camera.matrixWorld.elements], projection: [...engine.camera.projectionMatrix.elements],
                root: [...engine.apartmentRoot.matrixWorld.elements] };
            }, { capture: true, once: true });
          });
          await mouse.down();
          const selectedAfterDown = await waitForDirectionalReadiness(page, id => {
            const label = document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent;
            const matches = window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.filter(item => (item.label || item.fixture_id) === label);
            return matches.length === 1 && matches[0].fixture_id === id ? id : false;
          }, directId);
          drag.selected_after_down = selectedAfterDown;
          assert.equal(drag.selected_after_down, directId, 'pointer-down selected the fixture being dragged');
          await mouse.move(target.x, target.y, { steps: 12 }); await mouse.up();
          released = await page.evaluate(() => {
            const native = window.__shellNativeMouseScope;
            if (!native) throw Error('missing authored native drag scope');
            const value = { pointer_up: window.__shellDragPointerUp, focus_events: window.__shellDragFocusEvents,
              observed: native.observed, dragstarts: native.dragstarts };
            native.cleanup(); window.__shellDragFocusCleanup?.(); return value;
          });
          observerClosed = true; scope.observed = released.observed; scope.dragstarts = released.dragstarts; scope.ledger = mouse.readback();
          scope.native_witness_status = 'captured';
          assert.deepEqual(scope.ledger.operations.map(row => row.type), ['move', 'move', 'down', 'move', 'up']);
          assert.equal(scope.ledger.expected.filter(row => row.type === 'move').length, 14);
          assert.equal(scope.ledger.expected.length, 16);
          scope.proof = assertDirectionalNativeMouseLedger({ ledger: scope.ledger, observed: scope.observed, dragstarts: scope.dragstarts });
          scope.native_witness_status = 'qualified';
        } catch (error) { scopeError = error; scope.failure = error.message; throw error; }
        finally {
          let cleanupError;
          if (!observerClosed && !page.isClosed()) try {
            const witness = await page.evaluate(() => { const native = window.__shellNativeMouseScope;
              if (!native) return null; const value = { observed: native.observed, dragstarts: native.dragstarts }; native.cleanup(); return value; });
            if (witness) { Object.assign(scope, witness); scope.native_witness_status = 'captured'; }
          } catch (error) { cleanupError = error; }
          try { await session.detach(); } catch (error) { cleanupError ||= error; }
          scope.ledger = mouse.readback(); if (cleanupError) scope.cleanup_error = cleanupError.message; save();
          if (cleanupError && !scopeError) throw cleanupError;
        }
        drag.pointer_up = released.pointer_up; drag.focus_events = released.focus_events;
        assert.equal(drag.pointer_up?.current_engine, true, 'pointer-up belongs to the same actual engine');
        assert.equal(drag.pointer_up.selected_fixture_matches, 1, 'the rendered selected inspector identifies exactly one registered fixture');
        assert.equal(drag.pointer_up.selected_fixture_id, directId, 'pointer-up retains the fixture being dragged');
        // The drop only proposes (APT-A01, A04); the aim moves at an explicit
        // Set. Enter on the reticle, which kept focus from its press, is that Set.
        drag.proposed = await waitForDirectionalReadiness(page, () => document.querySelector('[data-apt-aim-bar]')?.hidden === false
          && document.querySelector('[data-apt-aim-bar]').getAttribute('data-state'));
        assert.equal(drag.proposed, 'valid', 'the drop leaves a valid proposal with Set aim');
        assert.deepEqual(await aimDescriptor(directId), drag.aim_before, 'the current aim stays exactly in place until Set aim');
        assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-apt-aim-reticle')), true, 'the reticle keeps focus after the drop');
        await page.keyboard.press('Enter');
        await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Setup', exact: true }).click();
        assert.equal(await page.getByRole('combobox', { name: 'Virtual fixture', exact: true }).inputValue(), directId);
        await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Aim', exact: true }).click();
        assert.ok(drag.pointer_up.visible_hit, 'actual pointer-up has a qualified visible canonical collision hit');
        await waitForDirectionalReadiness(page, id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.target, directId);
        const aimed = await aimDescriptor(directId);
        drag.aimed = aimed;
        drag.after = await targetPoint([2.4,2.1,0]);
        assert.deepEqual(aimed.display_anchor_m, before[directId].display_anchor_m, 'aiming leaves the saved socket fixed');
        assert.ok(Math.hypot(...aimed.target.point.map((value, axis) => value - drag.pointer_up.visible_hit.point[axis])) < .03,
          'committed aim stays within 3cm of the actual pointer-up visible canonical collision hit');
        assert.equal(aimed.physical_commands, false);
        await page.getByRole('button', { name: 'Undo aim', exact: true }).click();
        const undo = await observeWithin15s('native_undo:' + focusCase, async ({ id, pan }) => {
          const started = performance.now();
          while (performance.now() - started < 15000) {
            const descriptor = window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id);
            if (descriptor?.state.pan_deg === pan) return descriptor;
            await new Promise(resolve => requestAnimationFrame(resolve));
          }
          throw Error('15s native Undo pan restoration readiness');
        }, { id: directId, pan: before[directId].state.pan_deg });
        assert.deepEqual(undo.target, before[directId].target);
        checks.push({ stage: focusCase === 'setup_keyboard' ? 'four_actual_fixture_clicks_surface_drag_and_undo' : 'focused_spot_slider_surface_drag_and_undo',
          fixtureIds, focus_case: focusCase, aimed, undo });
      }
      await timedPhase('canonical_drag_setup', () => dragAndUndo('setup_keyboard', 'Virtual fixture'));
      // The focused-range aiming case belongs to Aim/Spot; Light/Brightness
      // intentionally selects Zones, whose isolated channel edits were tested above.
      await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Aim', exact: true }).click();
      assert.equal(await spotTool.getAttribute('aria-pressed'), 'true');
      await page.locator('[data-apt-aim-fine-tune] > summary').click();
      await page.getByRole('slider', { name: 'Pan', exact: true }).focus();
      await timedPhase('canonical_drag_spot', () => dragAndUndo('spot_pan_slider', 'Pan'));
      assert.deepEqual(receipt.native_drag_mouse.map(scope => scope.proof.samples), [16, 16]);

      // APT-A06: one collapsed, modeled-only command ladder in the Victoria Aim tab; reading it sends nothing.
      const ladder = page.locator('#apt-ui-virtual-aim [data-apt-command-ladder="modeled"]'), ladderRequests = requests.length;
      assert.equal(await ladder.count(), 1, 'one command ladder in the Victoria Aim tab');
      assert.equal(await ladder.locator('details').first().evaluate(node => node.open), false, 'the ladder starts collapsed');
      assert.equal(await ladder.locator('[data-apt-command-status]').innerText(), 'Modeled · nothing sent');
      await ladder.locator('summary').first().click();
      assert.equal(await ladder.locator('[data-apt-command-step="accepted"] .apt-ui-meta').innerText(), 'not available · not commissioned');
      assert.match(await ladder.locator('[data-apt-command-step="modeled"] .apt-ui-meta').innerText(), / · travel limits not commissioned$/);
      await ladder.getByText('Exact output', { exact: true }).click();
      assert.equal(await ladder.getByRole('button', { name: 'Copy request', exact: true }).isDisabled(), true);
      assert.doesNotMatch(await ladder.innerText(), /within/i, 'Victoria never claims within-limit pan or tilt');
      assert.equal(requests.slice(ladderRequests).some(row => !['GET', 'HEAD'].includes(row.method)), false, 'the ladder sends nothing');
      await ladder.locator('summary').first().click();
      checks.push({ stage: 'victoria_command_ladder_modeled_only' });

      await capture('unified-fixture-controls-1366x768');
    } else if (part === 2) {
      // Part 2 starts from the same booted shell, Victoria inventory, unsent
      // Chat draft and identity baseline that part 1 establishes first.
      await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
      await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
      await waitSite('echo'); await selectHome('victoria');
      await waitForDirectionalReadiness(page, () => window.__APT_ENGINE?.overlay.virtualFixturesGroup?.children.length === 4);
      await tab('Chat'); await command.fill('Authored unsent shell draft');
      await page.evaluate(() => { window.__shellProof = { engine: window.__APT_ENGINE, canvas: window.__APT_ENGINE.renderer.domElement,
        controller: window.__havUiDebug, input: document.querySelector('input[aria-label="Command input"]') }; });
      await tab('Controls'); await tab('Chat'); await identityCheck('desktop_tabs_keep_unsent_chat'); await tab('Controls');

      await page.setViewportSize({ width: 390, height: 844 });
      await tab('Camera · JEV');
      assert.equal(requests.some(row => !['GET', 'HEAD'].includes(row.method)), false, 'opening camera information never starts acquisition');
      await identityCheck('camera_tab_preserves_controller_and_draft');
      await capture('unified-camera-unavailable-390x844');
      await tab('Chat');
      await command.fill('/help');
      await page.getByRole('listbox', { name: 'Slash commands', exact: true }).waitFor();
      await command.fill('Authored unsent shell draft');

      await page.getByRole('button', { name: 'Home menu', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Metrics', exact: true }).click();
      const metrics = page.getByRole('dialog', { name: 'Home metrics', exact: true });
      await metrics.waitFor(); await page.keyboard.press('Escape');
      await metrics.waitFor({ state: 'hidden' });
      assert.equal(await page.evaluate(() => {
        const element = document.activeElement;
        return !!element?.isConnected && /^(BUTTON|INPUT|SELECT|TEXTAREA)$/.test(element.tagName)
          && element.getClientRects().length > 0 && !element.closest('[role="dialog"]');
      }), true, 'closing Metrics restores focus to a visible shell control');
      checks.push({ stage: 'metrics_escape_closes_dialog_and_restores_focus' });

      // The classic escape hatch is a UI action, then the public URL override can
      // re-enable the reversible shell without changing household configuration.
      const previousHome = page.getByText('Previous Home', { exact: true });
      if (!await previousHome.isVisible()) {
        await page.getByRole('button', { name: 'Home menu', exact: true }).click();
      }
      await previousHome.click();
      await waitForDirectionalReadiness(page, () => !document.querySelector('.apt-home-shell[data-home-layout="unified"]'));
      assert.equal(await page.getByRole('combobox', { name: 'Command input', exact: true }).inputValue(), 'Authored unsent shell draft');
      checks.push({ stage: 'classic_ui_escape_hatch_keeps_chat_draft' });
      await capture('previous-home-classic-390x844');
      await timedPhase('url_override_unified_reload', async () => {
        await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
        await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 }); await waitSite('echo');
      });
      // The unified override's own assertions end part 2; part 3 proves the
      // classic override and the classic card from a fresh page.
      checks.push({ stage: 'explicit_url_override_reenables_shell' });
    } else {
      // The classic steps ran at the phone width the earlier stages left set.
      await page.setViewportSize({ width: 390, height: 844 });
      await timedPhase('url_override_classic_reload', async () => {
        await page.goto(origin + '/?apartmentRedesign=0&worldZoom=off', { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Open apartment view', exact: true }).waitFor();
        assert.equal(await page.locator('.apt-home-shell[data-home-layout="unified"]').count(), 0);
      });
      checks.push({ stage: 'explicit_url_override_rolls_back_shell' });

      // The unified-only portal wrapper must not change the incumbent classic
      // card's absolute canvas placement or its actual close action.
      await timedPhase('classic_apartment_mount', async () => {
        await page.getByRole('button', { name: 'Open apartment view', exact: true }).click();
        await waitSite('echo');
      });
      const classicClose = page.getByRole('button', { name: 'close card', exact: true });
      await timedPhase('classic_generic_card_open', async () => {
        const classicSelect = page.locator('[data-apt-semantic-device-select="1"]');
        await classicSelect.focus(); await classicSelect.selectOption('authored-echo-light');
        const classicOpen = page.locator('[data-apt-semantic-open="1"]');
        await classicOpen.focus(); await classicOpen.press('Enter');
        await classicClose.waitFor();
      });
      const classicCard = await classicClose.evaluate(button => ({
        position: getComputedStyle(button.parentElement.parentElement).position,
        inUnifiedWrapper: !!button.closest('.apt-home-card'), inControls: !!button.closest('.apt-home-controls'),
        name: button.parentElement.parentElement.innerText,
      }));
      assert.equal(classicCard.position, 'absolute'); assert.equal(classicCard.inUnifiedWrapper, false); assert.equal(classicCard.inControls, false);
      await capture('classic-echo-card-390x844');
      await timedPhase('classic_generic_card_close', async () => {
        await classicClose.focus(); await classicClose.press('Enter'); await classicClose.waitFor({ state: 'hidden' });
      });
      checks.push({ stage: 'classic_generic_card_retains_canvas_positioning_and_closes', ...classicCard });
    }

    assert.deepEqual(readRegistration(privateRoot, { contentPacksDir: packs }), initial);
    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes);
    assert.deepEqual(forbidden, []); assert.deepEqual(errors, []); assert.deepEqual(missingCodeSources, []);
    for (const file of ['home-app.jsx', 'home-apartment.jsx', 'home-tokens.css', 'home-3d/engine.js']) {
      assert.equal(served[file]?.source, 'worktree', 'required production code was served from this worktree: ' + file);
    }
    for (const [relative, record] of Object.entries(served)) {
      assert.equal(record.source, 'worktree');
      assert.equal(record.sha256, hash(fs.readFileSync(path.join(SRC, relative))), 'served code stayed stable during this browser run: ' + relative);
    }
    receipt.registration_digest = initial.digest; receipt.protected_asset_hashes = protectedHashes; receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored Apartment shell browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([exercise(), deadline]); }
  catch (error) {
    receipt.failure = error.message;
    if (page && !page.isClosed()) {
      receipt.body = (await page.locator('body').innerText().catch(() => '')).slice(-6000);
      receipt.layout = await page.evaluate(() => ['html','body','#root','.apt-home-shell','.apt-home-stage'].map(selector => {
        const element = document.querySelector(selector), rect = element?.getBoundingClientRect();
        return { selector, rect: rect?.toJSON(), display: element && getComputedStyle(element).display,
          visibility: element && getComputedStyle(element).visibility };
      })).catch(() => []);
      if (screenshotsEnabled) {
        await page.evaluate(() => window.__APT_ENGINE?.setRunning?.(false)).catch(() => {});
        await page.screenshot({ path: path.join(run, 'failure.png'), timeout: 5000 }).catch(() => {});
      }
    }
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nAuthored shell failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
}
