// Directional lighting: real Home shell, renderer and native authored input.
// No household assets, service writes, sessions or private preview evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';
import { createDirectionalNativeMouse, assertDirectionalNativeMouseLedger } from './fixtures/apartment-directional-native-mouse.mjs';
import { waitForDirectionalReadiness } from './fixtures/apartment-directional-readiness.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

// Same public-code inventory fence as the retained full Home shell proof.
function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  assert.ok(fs.lstatSync(root).isDirectory() && !fs.lstatSync(root).isSymbolicLink());
  let count = 0, bytes = 0;
  function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      assert.ok(++count <= 10000);
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/'), stat = fs.lstatSync(file);
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/') || stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { visit(file, depth + 1); continue; }
      if (!stat.isFile()) continue;
      const vendor = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !vendor && !rootAsset) continue;
      bytes += stat.size; assert.ok(stat.size <= 32 * 1024 * 1024 && bytes <= 256 * 1024 * 1024);
      result.set('/' + relative, { file, relative });
    }
  }
  visit(root); return result;
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

test('real authored directional controls preserve native input, aim and output boundaries', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-directional-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-directional-authored-browser-1',
    authored_room: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-cards.jsx', 'home-apartment-directional.js', 'home-3d/engine.js',
      'home-3d/apartment-directional-presentation.mjs', 'home-3d/apartment-ui-overlay-bridge.mjs',
      'home-3d/equipment-virtual-fixtures.mjs', 'home-3d/markers.js',
      'home-3d/picking.js', 'home-3d/direct-aim-gesture.mjs'].map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], stage_timings: [], action_timings: [], native_progress: [], modeled_progress: [], keyboard_targets: [],
    native_mouse: { expected: [], dispatched: [], operations: [], observed: [], dragstarts: [], failure: null },
    requests: [], forbidden: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions() };
  let server, handler, origin, browser, context, page, rawPage, nativeMouse, nativeMouseSurface, mouseSession, timer, activeStage, pendingStage;
  const started = performance.now(), elapsed = () => performance.now() - started;
  const save = () => { receipt.elapsed_ms = elapsed(); fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); };
  const progress = value => fs.appendFileSync(path.join(run, 'progress.jsonl'), JSON.stringify({ elapsed_ms: elapsed(), ...value }) + '\n');
  const measure = async (name, operation, detail = {}) => {
    const action = { id: receipt.action_timings.length + 1, stage: receipt.current_stage || 'setup', name, ...detail,
      started_ms: elapsed(), callsite: new Error().stack?.split('\n').slice(3, 6).map(line => line.trim()) };
    receipt.action_timings.push(action); receipt.current_action = action; progress({ event: 'action-start', action }); save();
    try { const result = await operation(); action.result = 'passed'; return result; }
    catch (error) { action.result = 'failed'; action.failure = error.message; throw error; }
    finally { action.elapsed_ms = elapsed() - action.started_ms; progress({ event: 'action-end', action });
      if (receipt.current_action === action) receipt.current_action = null; save(); }
  };
  // Transparent observation only: preserve each existing action, argument,
  // await, pointer sample and readiness predicate. Persist the pending await
  // in Node so a browser deadline cannot erase the diagnostic evidence.
  const observed = new WeakMap();
  const locatorFactories = new Set(['locator', 'getByRole', 'getByLabel', 'filter', 'nth', 'first', 'last']);
  const observedMethods = new Set(['evaluate', 'evaluateAll', 'waitForFunction', 'goto', 'waitFor', 'getAttribute',
    'boundingBox', 'inputValue', 'isDisabled', 'isChecked', 'count', 'innerText', 'click', 'focus', 'check', 'uncheck',
    'move', 'down', 'up', 'dblclick', 'wheel', 'press', 'send']);
  const observe = (target, kind) => {
    if (observed.has(target)) return observed.get(target);
    const proxy = new Proxy(target, { get(object, key) {
      const member = Reflect.get(object, key, object);
      if (kind === 'page' && key === 'mouse') { assert.ok(nativeMouseSurface, 'native mouse owns every pointer primitive'); return observe(nativeMouseSurface, 'mouse'); }
      if (kind === 'page' && key === 'keyboard') return observe(member, key);
      if (typeof member !== 'function') return member;
      if (kind === 'locator' && ['click', 'dblclick', 'hover', 'tap', 'dragTo'].includes(key)) return () => {
        nativeMouse?.retire('mixed_playwright_locator_mouse'); throw Error('locator mouse input cannot bypass the owned native mouse state');
      };
      if (kind === 'keyboard' && key !== 'press') return () => {
        nativeMouse?.retire('unsupported_keyboard_state'); throw Error('only completed simple native keyboard.press is supported');
      };
      if (locatorFactories.has(key)) return (...args) => observe(member.apply(object, args), 'locator');
      if (!observedMethods.has(key)) return member.bind(object);
      return (...args) => measure(kind + '.' + String(key), () => {
        if (kind === 'mouse' || kind === 'keyboard' || kind === 'cdp' && key === 'send' && args[0]?.startsWith('Input.'))
          assert.ok(!activeStage || activeStage.renderer && !pendingStage, 'stage renderer evidence must precede native input');
        if (kind === 'keyboard') {
          assert.equal(args.length, 1, 'keyboard options/held modifiers require a separately qualified input path');
          return nativeMouse.guardKeyboardPress(args[0], () => member.apply(object, args));
        }
        if (kind === 'page' && key === 'waitForFunction') {
          assert.ok(args.length <= 3); return waitForDirectionalReadiness(rawPage, args[0], args[1], args[2]);
        }
        if (kind === 'page' && key === 'evaluate' && pendingStage) {
          const stage = pendingStage; pendingStage = null;
          assert.ok(args.length <= 2 && typeof args[0] === 'function' && args[0].constructor.name === 'Function');
          return member.call(object, ({ source, arg }) => {
            const engine = window.__APT_ENGINE, host = engine?.picking.getHost(), canvas = engine?.renderer.domElement;
            if (!engine || !host || !canvas) throw Error('stage renderer is unavailable before its first observation');
            const renderer = { at: performance.now(), site: engine.siteId, mode: engine.modes.mode,
              renderer_frame: engine.renderer.info.render.frame, draw_calls: engine.renderer.info.render.calls,
              pixel_ratio: engine.renderer.getPixelRatio(), backing: [canvas.width, canvas.height], host: [host.clientWidth, host.clientHeight],
              quality: engine.performanceBudget(), fixtures: engine.overlay.virtualFixturesReadback().fixture_count,
              fixture_display_on: engine.overlay.virtualFixturesReadback().visible };
            // Compile only this existing harness observation. Snapshot the
            // renderer before its callback can focus a native control.
            const value = globalThis.eval('(' + source + ')')(arg);
            if (value && typeof value.then === 'function') throw Error('unsupported async first-stage observation');
            return { renderer, value };
          }, { source: Function.prototype.toString.call(args[0]), arg: args[1] }).then(result => {
            stage.renderer = result.renderer; stage.renderer_folded_into_first_read = true; return result.value;
          });
        }
        return member.apply(object, args);
      }, {
        ...(kind === 'locator' ? { selector: String(object) } : {}),
        ...(['mouse', 'keyboard', 'cdp'].includes(kind) ? { arguments: args } : {}),
      });
    } });
    observed.set(target, proxy); return proxy;
  };
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    receipt.deadline_expired = true; receipt.deadline_stage = receipt.current_stage; receipt.deadline_action = structuredClone(receipt.current_action);
    progress({ event: 'deadline', current_stage: receipt.current_stage, current_action: receipt.current_action }); save();
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s directional browser deadline'));
  }, 145000); });
  const check = async (name, operation) => {
    receipt.current_stage = name; const stage = { name, started_ms: elapsed() }; receipt.stage_timings.push(stage); progress({ event: 'stage-start', stage }); save();
    activeStage = stage;
    if (receipt.stage_timings.length === 1 && rawPage && !rawPage.isClosed()) {
      stage.renderer = await measure('stage.renderer-snapshot', () => rawPage.evaluate(() => {
        const engine = window.__APT_ENGINE, host = engine?.picking.getHost(), canvas = engine?.renderer.domElement;
        return engine && { at: performance.now(), site: engine.siteId, mode: engine.modes.mode,
          renderer_frame: engine.renderer.info.render.frame, draw_calls: engine.renderer.info.render.calls,
          pixel_ratio: engine.renderer.getPixelRatio(), backing: [canvas.width, canvas.height], host: [host.clientWidth, host.clientHeight],
          quality: engine.performanceBudget(), fixtures: engine.overlay.virtualFixturesReadback().fixture_count,
          fixture_display_on: engine.overlay.virtualFixturesReadback().visible };
      }));
    } else pendingStage = stage;
    try { const detail = await operation(); assert.ok(stage.renderer && !pendingStage, 'every stage retains its renderer snapshot');
      receipt.checks.push({ name, ...detail }); stage.result = 'passed'; }
    catch (error) { stage.result = 'failed'; throw error; }
    finally { stage.elapsed_ms = elapsed() - stage.started_ms; activeStage = null; pendingStage = null; progress({ event: 'stage-end', stage }); save(); }
  };
  async function proof() {
    server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost'); receipt.requests.push({ path: url.pathname, method: req.method });
        if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
          receipt.forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
        }
        if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
        let body, type = 'application/octet-stream';
        if (url.pathname.startsWith('/api/perception-lab/v1/')) {
          const route = url.pathname.slice('/api/perception-lab/v1/'.length), value = route === 'capabilities'
            ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
            : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
          if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
        }
        if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
        else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
          if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; } }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry) {
          const relative = path.relative(fs.realpathSync(SRC), fs.realpathSync(entry.file)).split(path.sep).join('/');
          assert.equal(relative, entry.relative); assert.ok(!relative.startsWith('../') && !path.isAbsolute(relative));
          body = fs.readFileSync(entry.file); receipt.served[relative] = { sha256: hash(body), source: 'worktree' };
          type = /\.(?:js|mjs|jsx)$/.test(entry.file) ? 'text/javascript' : entry.file.endsWith('.html') ? 'text/html'
            : entry.file.endsWith('.css') ? 'text/css' : /\.(?:json|webmanifest)$/.test(entry.file) ? 'application/json' : entry.file.endsWith('.svg') ? 'image/svg+xml' : type;
        }
        if (!body) { if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) receipt.missing_code.push(url.pathname); res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) { receipt.errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
    handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
      authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
    browser = await measure('setup.launch', () => chromium.launch(receipt.browser)); context = await measure('setup.context', () => browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, reducedMotion: 'no-preference', serviceWorkers: 'block' }));
    receipt.reduced_motion = 'no-preference'; // Exercise the real rig's ambient hover settling, rather than its reduced-motion bypass.
    receipt.runtime = { node: process.version, playwright: require('playwright/package.json').version, module: require.resolve('playwright'), browser: browser.version() };
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true;
      // Source-grounded DOM observation only. Strict scopes, names and native
      // roles avoid Playwright's multiple element-handle protocol turns; no
      // application callback, click, key event or model setter is dispatched.
      const hiddenForRole = node => {
        for (let current = node; current; current = current.parentElement) {
          if (current.getAttribute('aria-hidden')?.toLowerCase() === 'true') return true;
        }
        return false;
      };
      const visible = node => !!node?.isConnected && !hiddenForRole(node)
        && [...node.getClientRects()].some(rect => Number.isFinite(rect.width) && Number.isFinite(rect.height) && rect.width > 0 && rect.height > 0)
        && getComputedStyle(node).visibility === 'visible';
      // These controls use an explicit aria-label or plain literal text.
      // Reject other naming mechanisms instead of approximating the accessible
      // name algorithm and silently accepting a different name from getByRole.
      const supportedName = node => {
        if (node.hasAttribute('aria-labelledby')) throw Error('unsupported authored aria-labelledby name');
        const label = node.getAttribute('aria-label');
        if (label != null) {
          if (!label.trim()) throw Error('unsupported empty authored aria-label');
          return label.trim().replace(/\s+/g, ' ');
        }
        if (node.localName === 'input') throw Error('authored inputs require an explicit aria-label');
        for (const child of node.querySelectorAll('*')) {
          if (child.hasAttribute('aria-label') || child.hasAttribute('aria-labelledby')) throw Error('unsupported descendant naming in authored control');
          if (child.textContent.trim() && (!visible(child) || ['title', 'script', 'style'].includes(child.localName)))
            throw Error('unsupported hidden text in authored control name');
        }
        return node.textContent.trim().replace(/\s+/g, ' ');
      };
      const scope = selector => { const nodes = document.querySelectorAll(selector);
        if (nodes.length > 1) throw Error('ambiguous authored DOM scope: ' + selector); return nodes[0] || null; };
      const query = spec => {
        const root = scope(spec.scope); if (!root) return null;
        if (hiddenForRole(root)) return null;
        if (root.hasAttribute('aria-labelledby')) throw Error('unsupported authored scope naming');
        const nodes = [...root.querySelectorAll(spec.selector)].filter(node =>
          !hiddenForRole(node) && supportedName(node) === spec.name);
        if (nodes.length > 1) throw Error('ambiguous authored DOM control: ' + spec.name);
        const node = nodes[0]; if (!node) return null;
        const role = node.getAttribute('role') || (node.localName === 'button' ? 'button' : node.localName === 'h2' ? 'heading'
          : node.localName === 'input' && node.type === 'checkbox' ? 'checkbox' : node.localName === 'input' && node.type === 'range' ? 'slider' : null);
        if (node.localName !== spec.tag || role !== spec.role || spec.type && node.type !== spec.type) throw Error('wrong authored control identity: ' + spec.name);
        return node;
      };
      window.__directionalDOM = { visible, scope, query };
      window.__directionalKeyTargets = [];
      window.__directionalNative = [];
      window.__directionalMouseObserved = []; window.__directionalHtmlDragstarts = [];
      for (const type of ['pointermove', 'pointerdown', 'pointerup', 'wheel']) window.addEventListener(type, event => {
        if (type !== 'wheel' && event.pointerType !== 'mouse') return;
        const row = { sequence: window.__directionalMouseObserved.length + 1, type, isTrusted: event.isTrusted,
          at: event.timeStamp, x: event.clientX, y: event.clientY, pointerType: event.pointerType, pointerId: event.pointerId,
          button: event.button, buttons: event.buttons, pressure: event.pressure, deltaX: event.deltaX, deltaY: event.deltaY,
          modifiers: { alt: event.altKey, control: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey } };
        window.__directionalMouseObserved.push(row);
        window.__directionalDiagnostic?.({ kind: 'mouse-native', value: row }).catch(() => {});
      }, true);
      window.addEventListener('dragstart', event => {
        const row = { at: event.timeStamp, isTrusted: event.isTrusted };
        window.__directionalHtmlDragstarts.push(row);
        window.__directionalDiagnostic?.({ kind: 'html-dragstart', value: row }).catch(() => {});
      }, true); // Observe a failure; never intercept or prevent HTML drag-and-drop.
      // Observe the trusted input only. The actual producer wrapper below
      // records its synchronous state/readout result before another frame.
      window.addEventListener('pointermove', event => {
        window.__directionalLastMoveEvent = event;
        window.__directionalLastMove = { at: event.timeStamp, isTrusted: event.isTrusted,
          x: event.clientX, y: event.clientY, pointerId: event.pointerId };
      }, true);
      const drawing = () => {
        const engine = window.__APT_ENGINE, scenes = [];
        engine?.scene.traverse(node => { if (node.name === 'apartment-directional-composer') scenes.push(node); });
        if (scenes.length !== 1) throw Error('expected one actual borrowed directional scene');
        const scene = scenes[0], fill = scene.getObjectByName('directional-polar-fill'), edge = scene.getObjectByName('directional-polar-edge');
        if (!fill?.isMesh || !edge?.isLine) throw Error('missing actual continuous directional drawing');
        if (!window.__directionalDrawObservers) window.__directionalDrawObservers = new Map();
        let witness = window.__directionalDrawObservers.get(fill.uuid);
        if (!witness) {
          witness = { calls: 0, scene: scene.uuid, fill: fill.uuid, last: null };
          window.__directionalDrawObservers.set(fill.uuid, witness);
          const original = fill.onAfterRender;
          fill.onAfterRender = function (...args) {
            witness.calls++; witness.last = { actual_renderer: args[0] === engine.renderer,
              actual_scene: args[1] === engine.scene, actual_camera: args[2] === engine.camera,
              frame: engine.renderer.info.render.frame, at: performance.now() };
            return original.apply(this, args);
          };
        }
        return { engine, scene, fill, edge, witness };
      };
      window.__directionalDrawing = drawing;
      window.__directionalDrawingState = () => {
        const { engine, scene, fill, edge, witness } = drawing();
        const sprites = prefix => [1, 2, 3, 4, 5, 6].map(number => {
          const node = scene.getObjectByName(prefix + number);
          if (!node?.isSprite) throw Error('missing actual directional sprite ' + number);
          return { number, uuid: node.uuid, color: node.material.color.toArray(), visible: node.visible,
            opacity: node.material.opacity, scale: node.scale.toArray(), depthTest: node.material.depthTest, depthWrite: node.material.depthWrite };
        });
        return { scene: scene.uuid, visible: scene.visible, position: scene.position.toArray(),
          borrowed_layer: scene.parent?.name, borrowed_root: scene.parent?.parent === engine.overlay.overlayRoot,
          presentation_only: scene.userData.presentation_only === true,
          fan: { mesh: fill.isMesh, vertices: fill.geometry.attributes.position.count,
            colors: fill.geometry.attributes.zoneTint.count, edge_vertices: edge.geometry.attributes.position.count,
            index: [...fill.geometry.index.array], t: [...fill.geometry.attributes.t.array],
            vertex: fill.material.vertexShader, fragment: fill.material.fragmentShader,
            transparent: fill.material.transparent, additive: fill.material.blending === 2,
            depthTest: fill.material.depthTest, depthWrite: fill.material.depthWrite,
            edge_depthTest: edge.material.depthTest, edge_depthWrite: edge.material.depthWrite },
          nodes: sprites('directional-zone-node-'), ticks: sprites('directional-zone-tick-'),
          guides: scene.children.filter(node => node.isLine && node !== edge).map(node => ({ uuid: node.uuid,
            visible: node.visible, depthTest: node.material.depthTest, depthWrite: node.material.depthWrite })),
          draw: structuredClone(witness), frame: engine.renderer.info.render.frame,
          draw_calls: engine.renderer.info.render.calls };
      };
      for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'lostpointercapture', 'keydown', 'click', 'wheel']) window.addEventListener(type, event => {
        const expected = window.__directionalExpectedKey;
        if (type === 'keydown' && expected && event.key === expected.key) {
          let current = false; try { current = query(expected.spec) === expected.node && scope(expected.spec.scope) === expected.scope; } catch {}
          const value = { id: expected.id, key: event.key, isTrusted: event.isTrusted,
            exact_target: current && event.target === expected.node && expected.node.isConnected,
            site: window.__APT_ENGINE?.siteId };
          window.__directionalKeyTargets.push(value); window.__directionalExpectedKey = null;
          window.__directionalDiagnostic?.({ kind: 'keyboard-target', value }).catch(() => {});
        }
        if (type === 'keydown' && event.key !== 'Escape') return;
        const engine = window.__APT_ENGINE;
        const control = event.target?.closest?.('.apt-home-setup') ? 'Setup' : event.target?.getAttribute?.('aria-label') || event.target?.closest?.('button')?.getAttribute('title');
        const lifecycle = type === 'click' && ['Setup', 'Show virtual spotlights layer', 'Echo Park'].includes(control);
        const descriptors = lifecycle && engine?.overlay.virtualFixturesReadback?.().fixtures
          .map(fixture => engine.overlay.virtualFixtureInteractionDescriptor(fixture.fixture_id));
        window.__directionalNative.push({ type, pointerId: event.pointerId, pointerType: event.pointerType, isTrusted: event.isTrusted,
          key: event.key, at: performance.now(), start: event.target?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start'),
          control, call_count: window.__directionalCalls?.length || 0, descriptors,
          fixtures: type === 'keydown' ? engine?.overlay.virtualFixturesReadback?.().fixtures : undefined });
        window.__directionalDiagnostic?.({ kind: 'native', value: { type, pointerId: event.pointerId, pointerType: event.pointerType,
          key: event.key, at: performance.now(), control, isTrusted: event.isTrusted,
          start: event.target?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start') } }).catch(() => {});
      }, true);
    });
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        const harmless = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && request.method() === 'GET' && url.pathname === '/healthz';
        if (!harmless) receipt.forbidden.push({ origin: url.origin, path: url.pathname, method: request.method() });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      // The existing tracker subscription is a read channel, not a service,
      // save or session-start attempt. It stays blocked in this authored proof.
      if (url.host === new URL(origin).host && url.pathname === '/proxy/tracker/ws/tracks') receipt.blocked_read_subscriptions.push(url.pathname);
      else receipt.forbidden.push({ websocket: socket.url() });
      socket.close();
    });
    rawPage = await measure('setup.page', () => context.newPage());
    mouseSession = await context.newCDPSession(rawPage);
    nativeMouse = createDirectionalNativeMouse({ send: (method, packet) => mouseSession.send(method, packet), onRecord: row => {
      const target = receipt.native_mouse;
      if (row.kind === 'expected' || row.kind === 'dispatched') target[row.kind].push(row.value);
      else if (row.kind === 'operation-start') target.operations.push(row.value);
      else if (row.kind === 'operation-end') Object.assign(target.operations.find(value => value.id === row.value.id), row.value);
      else if (row.kind === 'failure') target.failure = row.value.reason;
      progress({ event: 'mouse-' + row.kind, value: row.value }); save();
    } });
    // A mutable observation facade keeps the proxy compatible with the
    // helper's frozen API; the helper remains the sole owner of input state.
    nativeMouseSurface = Object.fromEntries(['move', 'down', 'up', 'click', 'wheel'].map(method =>
      [method, (...args) => nativeMouse[method](...args)]));
    await rawPage.exposeBinding('__directionalDiagnostic', (_source, event) => {
      if (event.kind === 'native') receipt.native_progress.push(event.value);
      else if (event.kind === 'modeled') receipt.modeled_progress.push(event.value);
      else if (event.kind === 'keyboard-target') receipt.keyboard_targets.push(event.value);
      else if (event.kind === 'mouse-native') receipt.native_mouse.observed.push(event.value);
      else if (event.kind === 'html-dragstart') { receipt.native_mouse.dragstarts.push(event.value); nativeMouse.retire('native_html_dragstart'); }
      progress({ event: 'browser-' + event.kind, value: event.value });
    });
    page = observe(rawPage, 'page'); page.setDefaultTimeout(15000); page.on('pageerror', error => receipt.errors.push(error.message));
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    const waitSite = async site => page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === site, site);
    const INSPECTOR = '[data-victoria-virtual-fixtures="1"][aria-label="Fixture lighting inspector"]', COMPOSER = '[data-apt-directional-composer="1"]';
    const HOME = '[role="group"][aria-label="Home"]', SHELL_TABS = '[role="tablist"][aria-label="Home panels"]';
    const RING = INSPECTOR + ' [role="group"][aria-label="Radial zones"]', CHANNEL = INSPECTOR + ' [aria-label="Radial channel"]';
    const LAYERS = '[role="dialog"][aria-label="Room layers"]', TOOLBAR = '[data-apt-edit-ui="toolbar"]';
    const button = (scope, name, role = 'button') => ({ scope, selector: role === 'tab' ? 'button[role="tab"]' : 'button', name, tag: 'button', role, type: 'button' });
    const input = (scope, name, type) => ({ scope, selector: 'input[type="' + type + '"]', name, tag: 'input', type, role: type === 'range' ? 'slider' : 'checkbox' });
    const zoneButton = number => ({ scope: RING, selector: 'path[role="button"]', name: 'Radial zone ' + number, tag: 'path', role: 'button' });
    const expectedKeys = [];
    // Playwright's focusNode also uses DOM focus twice. Resolve, focus and
    // admit the exact current control in one browser turn, then send the same
    // real keyboard input outside evaluate. A trusted keydown independently
    // witnesses that the admitted scope/node actually received that key.
    const activateStatic = async (spec, key = 'Enter', { readBefore, checkBefore } = {}) => {
      const id = expectedKeys.length + 1, eventKey = key === 'Space' ? ' ' : key;
      const admission = await page.evaluate(({ spec, id, key, readBefore }) => {
        const dom = window.__directionalDOM, node = dom.query(spec), root = dom.scope(spec.scope);
        if (!node) throw Error('missing authored control: ' + spec.name);
        // Only observations at the existing pre-input fence are combined.
        // Capture them before even moving keyboard focus to the next control.
        const observations = {};
        if (readBefore?.pressed) {
          const prior = dom.query(readBefore.pressed); if (!prior) throw Error('missing prior radial zone');
          observations.pressed = prior.getAttribute('aria-pressed');
        }
        if (readBefore?.power) {
          const power = dom.query(readBefore.power.spec); if (!power) throw Error('missing radial power checkbox');
          observations.power = { checked: power.checked, descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(readBefore.power.id) };
        }
        if (readBefore?.history) observations.history = readBefore.history.map(spec => {
          const node = dom.query(spec); if (!node) throw Error('missing actual aim history control'); return node.disabled;
        });
        node.focus(); node.focus();
        const current = node.isConnected && dom.query(spec) === node && dom.scope(spec.scope) === root;
        window.__directionalExpectedKey = { id, key, node, scope: root, spec };
        return { exact_identity: current, focused: document.activeElement === node, visible: dom.visible(node),
          enabled: !node.matches(':disabled') && !node.closest('[aria-disabled="true"]'), inert: !!node.closest('[inert]'), site: window.__APT_ENGINE?.siteId, observations };
      }, { spec, id, key: eventKey, readBefore });
      const { site, observations, ...conditions } = admission;
      assert.deepEqual(conditions, { exact_identity: true, focused: true, visible: true, enabled: true, inert: false });
      checkBefore?.(observations);
      expectedKeys.push({ id, key: eventKey, site });
      await page.keyboard.press(key);
    };
    const selectHome = async site => { await activateStatic(button(HOME, site === 'victoria' ? 'Victoria' : 'Echo Park')); await waitSite(site); };
    const blur = () => page.evaluate(() => document.activeElement?.blur());
    const descriptor = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), id);
    const inventory = () => page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback());
    const expectSelected = async (fixture, mode = null, controls = false) => {
      await page.waitForFunction(({ fixture, scope, mode, modeSpec, controls }) => {
        const dom = window.__directionalDOM, heading = dom.query({ scope, selector: 'h2', name: fixture.label, tag: 'h2', role: 'heading' });
        const ids = []; window.__APT_ENGINE.overlay.virtualFixturesGroup.traverse(node => {
          if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
        });
        const node = mode && dom.query(modeSpec);
        return dom.visible(heading) && ids.length === 1 && ids[0] === fixture.fixture_id
          && (!mode || dom.visible(node) && node.getAttribute('data-apt-directional-mode') === mode && node.getAttribute('aria-pressed') === 'true')
          && (!controls || document.querySelector('#apt-home-tab-controls')?.getAttribute('aria-selected') === 'true');
      }, { fixture, scope: INSPECTOR, mode, modeSpec: mode && button(COMPOSER, mode === 'zones' ? 'Zones' : 'Spot'), controls });
    };
    const expectMode = async (mode, tab = null) => {
      const result = await page.waitForFunction(({ mode, spec, tab }) => {
        const dom = window.__directionalDOM, node = dom.query(spec);
        if (!(dom.visible(node) && node.getAttribute('data-apt-directional-mode') === mode && node.getAttribute('aria-pressed') === 'true')) return false;
        if (!tab) return true;
        const tabNode = dom.query(tab); if (!tabNode) throw Error('missing authored control: ' + tab.name);
        // Capture the original immediate assertion at the first mode success;
        // a false/missing tab state must fail, rather than gain retry grace.
        return 'mode-ready-tab:' + tabNode.getAttribute('aria-selected');
      }, { mode, spec: button(COMPOSER, mode === 'zones' ? 'Zones' : 'Spot'), tab });
      if (tab) assert.equal(result, 'mode-ready-tab:true');
    };
    const waitDOM = (selector, detached = false) => page.waitForFunction(({ selector, detached }) => {
      const node = window.__directionalDOM.scope(selector); return detached ? !node : window.__directionalDOM.visible(node);
    }, { selector, detached });
    const navigationFrom = pose => ({ az: pose.az, el: pose.el, zoom: pose.zoom, curAz: pose.curAz, curEl: pose.curEl, curRadius: pose.curRadius,
      previewAz: pose.previewAz, previewEl: pose.previewEl, overshoot: pose.overshoot, target: pose.target });
    const navigationPose = async () => navigationFrom(await page.evaluate(() => window.__APT_ENGINE.rig.snapshotPose()));
    const setMode = async (mode, tab = null) => {
      const observation = await page.evaluate(spec => { const dom = window.__directionalDOM, node = dom.query(spec);
        if (!node) throw Error('missing native mode pill: ' + spec.name); const rect = node.getBoundingClientRect();
        return { box: dom.visible(node) ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null,
          pose: window.__APT_ENGINE.rig.snapshotPose() }; }, button(COMPOSER, mode === 'zones' ? 'Zones' : 'Spot'));
      const box = observation.box; assert.ok(box); const before = navigationFrom(observation.pose);
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
      assert.equal(await captureActive(), false, 'the native pill press must not leak pointer capture to the orbit rig');
      await page.mouse.move(box.x + box.width / 2 + 2, box.y + box.height / 2);
      assert.deepEqual(await navigationPose(), before, 'a pressed native pill does not start a camera drag preview');
      await page.mouse.up(); await expectMode(mode, tab);
    };
    const waitHUD = () => page.waitForTimeout(180); // More than two existing 80ms HUD opportunities; no new clock or polling loop.
    const writes = () => page.evaluate(() => window.__directionalCalls || []);
    const paintWrites = rows => rows.filter(row => row.method === 'setVirtualFixtureState' && Object.keys(row.patch || {}).length === 1 && Array.isArray(row.patch.radials));
    const retirementRead = async (type, control = null, key = null, pointerType = null) => {
      const result = await page.evaluate(({ type, control, key, pointerType }) => {
        const events = window.__directionalNative, down = [...events].reverse().find(row => row.type === 'pointerdown' && row.pointerType === 'mouse');
        return { marker: [...events].reverse().find(row => row.type === type && (control == null || row.control === control)
          && (key == null || row.key === key) && (pointerType == null || row.pointerType === pointerType)),
          captured: !!down && window.__APT_ENGINE.picking.getHost().hasPointerCapture(down.pointerId), calls: window.__directionalCalls || [] };
      }, { type, control, key, pointerType });
      assert.ok(result.marker?.isTrusted, 'retirement begins with the actual trusted browser event'); return result;
    };
    const withoutRadials = value => { const result = structuredClone(value); delete result.state.radials; return result; };
    const canvasPoint = (kind, id, avoid = null, navigation = false) => page.evaluate(({ kind, id, avoid, navigation }) => {
      const engine = window.__APT_ENGINE, canvas = engine.renderer.domElement, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
      const targets = engine.overlay.virtualFixturePickObjects(), candidates = [];
      if (kind === 'fixture') for (const object of targets.filter(node => node.userData.fixture_id === id && node.userData.interaction_role === 'fixture')) {
        const point = engine.picking.projectToScreen(object.getWorldPosition(engine.camera.position.clone()));
        if (point.visible) candidates.push({ x: rect.left + point.x, y: rect.top + point.y });
      }
      else for (const y of [.2, .35, .5, .65, .8, .9]) for (const x of [.1, .25, .4, .55, .7, .85, .95]) candidates.push({ x: rect.left + rect.width * x, y: rect.top + rect.height * y });
      for (const point of candidates) {
        if (document.elementFromPoint(point.x, point.y) !== canvas || avoid && Math.hypot(point.x - avoid.x, point.y - avoid.y) < 30) continue;
        const hits = engine.picking.pick(targets, point.x, point.y);
        const selected = kind === 'fixture' ? hits[0]?.object.userData.fixture_id === id
          : !hits.length && (kind !== 'surface' || engine.displaySurfaceHit(point.x, point.y));
        if (selected) return { ...point, ...(navigation ? { navigation: { matrix: engine.camera.matrixWorld.toArray(), azimuth: engine.rig.azimuthIndex() } } : {}) };
      }
      return null;
    }, { kind, id, avoid, navigation });
    const planePoint = async (id, number, radius, checkCapture = false) => {
      const result = await page.evaluate(({ id, number, radius }) => {
        const engine = window.__APT_ENGINE, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
        const snapshot = engine.overlay.virtualFixtureInteractionDescriptor(id), radial = snapshot.state.radials.find(zone => zone.number === number);
        const matrix = engine.camera.projectionMatrix.clone().multiply(engine.camera.matrixWorldInverse).multiply(engine.apartmentRoot.matrixWorld);
        const frame = window.HomeApartmentDirectional.createPlaneFrame({ anchor: snapshot.display_anchor_m, clipFromLocal: matrix.elements,
          width: host.clientWidth, height: host.clientHeight });
        const angle = radial.azimuth_deg * Math.PI / 180, point = [radius * Math.cos(angle), radius * Math.sin(angle)];
        const screen = frame?.project(point), hub = frame?.project([0, 0]), tick = frame?.project([1.3 * Math.cos(angle), 1.3 * Math.sin(angle)]);
        const hubElement = document.querySelector('[data-apt-directional-composer="1"] [data-apt-radial-start="hub"]');
        const actualHub = hubElement && { x: +hubElement.getAttribute('cx'), y: +hubElement.getAttribute('cy') };
        const drawing = window.__directionalDrawing(), tickNode = drawing.scene.getObjectByName('directional-zone-tick-' + number);
        const actualTick = tickNode && engine.picking.projectToScreen(tickNode.getWorldPosition(engine.camera.position.clone()));
        const world = engine.camera.position.clone().set(snapshot.display_anchor_m[0] + point[0], snapshot.display_anchor_m[1] + point[1], snapshot.display_anchor_m[2]);
        engine.apartmentRoot.localToWorld(world);
        const independent = engine.picking.projectToScreen(world);
        if (!screen?.visible || !hub?.visible || !actualHub || !actualTick) return { screen, hub, tick, actualHub, actualTick };
        const client = { x: rect.left + screen.x * rect.width / host.clientWidth, y: rect.top + screen.y * rect.height / host.clientHeight };
        const hit = document.elementFromPoint(client.x, client.y);
        const down = [...window.__directionalNative].reverse().find(row => row.type === 'pointerdown' && row.pointerType === 'mouse');
        return { screen, hub, tick, actualHub, actualTick, independent, point, client,
          captured: !!down && engine.picking.getHost().hasPointerCapture(down.pointerId),
          target: hit?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start'), azimuth_deg: radial.azimuth_deg };
      }, { id, number, radius });
      assert.ok(result.client, 'requested authored fixture-height point is visible and its SVG hub and actual Three tick are mounted');
      assert.ok(Math.hypot(result.screen.x - result.independent.x, result.screen.y - result.independent.y) <= .01,
        'plane forward projection agrees with the actual renderer root/camera projection');
      assert.ok(Math.hypot(result.hub.x - result.actualHub.x, result.hub.y - result.actualHub.y) < .5, 'actual SVG hub follows the current renderer');
      assert.ok(result.actualTick.visible && Math.hypot(result.tick.x - result.actualTick.x, result.tick.y - result.actualTick.y) < .5,
        'actual borrowed Three tick follows the actual canonical azimuth plane');
      if (checkCapture) assert.equal(result.captured, true);
      return result;
    };
    const expectedWarmColor = kelvin => {
      // Independent literal prototype color oracle, not the production helper.
      const t = Math.max(1000, Math.min(12000, kelvin)) / 100;
      return [t <= 66 ? 255 : 329.7 * (t - 60) ** -.1332,
        t <= 66 ? 99.47 * Math.log(t) - 161.12 : 288.12 * (t - 60) ** -.0755,
        t <= 19 ? 0 : t <= 66 ? 138.52 * Math.log(t - 10) - 305.04 : 255]
        .map(value => Math.max(0, Math.min(255, value)) / 255);
    };
    const assertNodeColors = (drawing, actual, active = null) => {
      assert.equal(drawing.nodes.length, 6); assert.equal(new Set(drawing.nodes.map(node => node.uuid)).size, 6);
      for (const node of drawing.nodes) {
        if (node.number === active) { assert.deepEqual(node.color, [1, 1, 1]); continue; }
        const initial = expectedWarmColor(originalAim.state.radials.find(zone => zone.number === node.number).color_temp_kelvin);
        const target = expectedWarmColor(actual.state.radials.find(zone => zone.number === node.number).color_temp_kelvin);
        // One keyboard CCT change may still be easing. Every channel must stay
        // on that independently bounded warm-color path, never a white node.
        node.color.forEach((value, index) => assert.ok(value >= Math.min(initial[index], target[index]) - 1e-7
          && value <= Math.max(initial[index], target[index]) + 1e-7));
        assert.equal(node.color[0], 1); assert.ok(node.color[1] < 1 && node.color[2] < 1);
      }
    };
    const verifyContinuousDrawing = async () => {
      await page.waitForFunction(() => window.__directionalDrawingState().draw.calls > 0);
      const observed = await page.evaluate(id => ({ drawing: window.__directionalDrawingState(),
        descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id),
        hits: [...document.querySelectorAll('[data-apt-directional-composer="1"] svg path')].map(path => ({
          fill: path.getAttribute('fill'), number: +path.getAttribute('data-apt-radial-number'), start: path.getAttribute('data-apt-radial-start') })) }), fixture.fixture_id);
      const { drawing, descriptor: actual } = observed, { fan } = drawing;
      assert.equal(drawing.visible, true); assert.equal(drawing.borrowed_layer, 'apartment-ui-overlay-layer');
      assert.equal(drawing.borrowed_root, true); assert.equal(drawing.presentation_only, true);
      assert.deepEqual(drawing.position, actual.display_anchor_m);
      assert.equal(fan.mesh, true); assert.equal(fan.vertices, 242); assert.equal(fan.colors, 242); assert.equal(fan.edge_vertices, 241);
      assert.deepEqual(fan.index, Array.from({ length: 240 }, (_, index) => [0, index + 1, index + 2]).flat());
      assert.deepEqual(fan.t, [0, ...Array(241).fill(1)]);
      assert.match(fan.vertex, /vT=t;vTint=zoneTint/);
      assert.match(fan.fragment.replace(/\s/g, ''), /a=pow\(1\.0-vT,1\.8\)\*0\.85\+0\.04\+0\.3\*smoothstep\(0\.8,1\.0,vT\)/);
      assert.match(fan.fragment.replace(/\s/g, ''), /vec4\(vTint\*a\*0\.7,1\.0\)/);
      // The actual shader has a bright center, soft middle and bright edge.
      // GPU participation is witnessed separately; this is not a pixel claim.
      const middle = .5 ** 1.8 * .85 + .04;
      assert.ok(.85 + .04 > middle && .04 + .3 > middle);
      assert.equal(fan.transparent, true); assert.equal(fan.additive, true);
      for (const key of ['depthTest', 'depthWrite', 'edge_depthTest', 'edge_depthWrite']) assert.equal(fan[key], false);
      assert.equal(drawing.guides.length, 3); assert.ok(drawing.guides.every(guide => !guide.visible && !guide.depthTest && !guide.depthWrite));
      assert.equal(drawing.ticks.length, 6); assert.ok(drawing.ticks.every(tick => tick.visible && !tick.depthTest && !tick.depthWrite));
      assert.ok(drawing.nodes.every(node => !node.depthTest && !node.depthWrite)); assert.equal(drawing.nodes[0].visible, true); assertNodeColors(drawing, actual);
      assert.deepEqual(observed.hits.map(hit => hit.fill), Array(6).fill('transparent'), 'SVG owns hit regions only, with no duplicate drawn wedges');
      for (const hit of observed.hits) {
        const target = actual.state.radials.find(zone => zone.number === hit.number);
        assert.equal(hit.start, target.on && target.brightness > 0 ? 'zone:' + hit.number : null,
          'drawing fade never admits an off logical zone as a new pointer start');
      }
      assert.ok(drawing.draw.calls > 0 && drawing.draw_calls > 0);
      assert.equal(drawing.draw.last.actual_renderer, true); assert.equal(drawing.draw.last.actual_scene, true); assert.equal(drawing.draw.last.actual_camera, true);
      return { continuous_triangles: 240, six_actual_nodes: true, real_renderer_draws: drawing.draw.calls,
        guides_hidden_idle: true, shader_center_fade_and_edge: true, visual_pixels_not_measured: true };
    };
    const hubPoint = async id => {
      const geometry = await planePoint(id, 1, 0);
      assert.equal(geometry.target, 'hub', 'the native pickup point reaches the real transparent hub hit region'); return geometry.client;
    };
    const captureActive = () => page.evaluate(() => {
      const host = window.__APT_ENGINE.picking.getHost(), down = [...window.__directionalNative].reverse().find(row => row.type === 'pointerdown' && row.pointerType === 'mouse');
      return !!down && host.hasPointerCapture(down.pointerId);
    });
    const startHub = async (id, deferCapture = false) => { const hub = await hubPoint(id); await page.mouse.move(hub.x, hub.y); await page.mouse.down(); if (!deferCapture) assert.equal(await captureActive(), true); return hub; };
    const immediatePaint = async (number, level, point) => {
      const row = await page.evaluate(() => [...window.__directionalCalls].reverse().find(call => call.immediate));
      assert.ok(row?.immediate && row.native_move?.isTrusted, 'paint is observed in the actual trusted native move task');
      assert.equal(row.native_dispatch_active, true, 'the actual producer is called during native host dispatch, rather than an 80ms timer');
      assert.equal(row.method, 'setVirtualFixtureState'); assert.equal(row.fixture_id, fixture.fixture_id);
      assert.ok(Math.hypot(row.native_move.x - point.client.x, row.native_move.y - point.client.y) < .01);
      assert.equal(row.immediate.frame, row.frame, 'logical state and numeric readout update before another resident render frame');
      assert.equal(row.immediate.descriptor.state.radials.find(zone => zone.number === number).brightness, level);
      assert.equal(row.immediate.readout_hidden, false); assert.match(row.immediate.readout, new RegExp('Zone ' + number + '.*' + Math.round(level * 100) + '%'));
      return { number, target: level, same_input_task: true, before_next_renderer_frame: true,
        at_ms: row.immediate.at, native_at_ms: row.native_move.at };
    };
    const unchangedAfterRetirement = async (marker, allowReconciliation = false, beforeRead = null) => {
      const before = beforeRead ?? await writes(); await waitHUD(); await page.mouse.up(); await waitHUD();
      const observed = await page.evaluate(() => ({ calls: window.__directionalCalls || [], reconciliations: window.__directionalReconciliations }));
      const after = observed.calls; assert.equal(marker.isTrusted, true); assert.ok(Number.isInteger(marker.call_count));
      const late = after.slice(marker.call_count);
      if (!allowReconciliation) {
        assert.deepEqual(late, [], 'no modeled set, aim or restore call occurs after owner retirement');
        return { all_calls_before_release: before.length, all_calls_after_release: after.length, unexpected_late_calls: 0, exact_reconciliation_calls: 0 };
      }
      // The existing lifecycle effect calls the real setVirtualFixtures and
      // synchronously replays its saved writable channel state and targets.
      // Admit that one transaction only; inspect every call, including aim and
      // malformed/combined patches. Read-only azimuths remain in the complete
      // before/after producer descriptors, rather than its writable patch ABI.
      assert.ok(['Show virtual spotlights layer', 'Setup'].includes(marker.control));
      const original = marker.descriptors; assert.equal(original?.length, 4);
      assert.equal(new Set(original.map(item => item.fixture_id)).size, 4);
      const knownTargets = original.filter(item => item.target != null);
      assert.equal(knownTargets.length, 1, 'the one target created by the native Spot stage is the complete target replay inventory');
      assert.equal(knownTargets[0].fixture_id, fixture.fixture_id);
      const expectedOrder = original.map(item => ({ method: 'setVirtualFixtureState', fixture_id: item.fixture_id }))
        .concat(knownTargets.map(item => ({ method: 'aimVirtualFixtureAtTarget', fixture_id: item.fixture_id })));
      assert.equal(expectedOrder.length, 5);
      assert.deepEqual(late.map(({ method, fixture_id }) => ({ method, fixture_id })), expectedOrder,
        'exactly four state replays and the known non-null target replays are allowed, with no other modeled calls');
      const tokens = [...new Set(late.map(row => row.reconciliation))]; assert.equal(tokens.length, 1); assert.ok(Number.isInteger(tokens[0]));
      const transaction = observed.reconciliations.find(item => item.id === tokens[0]);
      assert.equal(transaction?.completed, true); assert.ok(transaction.at >= marker.at);
      assert.deepEqual(transaction.document, { binding: original[0].binding, revision: original[0].equipment_revision, digest: original[0].equipment_digest });
      assert.deepEqual(transaction.before, original, 'the actual reconciliation starts with the exact pre-click producer descriptors');
      assert.deepEqual(transaction.after, original, 'transaction-final producer descriptors keep every state, target, azimuth and canonical identity exact');
      for (const row of late) {
        assert.equal(row.arguments_length, 2); assert.equal(row.ok, true); assert.ok(row.at >= transaction.at);
        const actual = original.find(item => item.fixture_id === row.fixture_id); assert.ok(actual);
        assert.deepEqual(actual.binding, transaction.document.binding);
        assert.equal(actual.equipment_revision, transaction.document.revision); assert.equal(actual.equipment_digest, transaction.document.digest);
        if (row.method === 'aimVirtualFixtureAtTarget') { assert.deepEqual(row.patch, actual.target); continue; }
        assert.deepEqual(Object.keys(row.patch).sort(), ['downward', 'pan_deg', 'radials', 'spotlight', 'tilt_deg']);
        for (const name of ['downward', 'spotlight']) {
          assert.deepEqual(Object.keys(row.patch[name]).sort(), ['brightness', 'color_temp_kelvin', 'on']);
          assert.deepEqual(row.patch[name], actual.state[name]);
        }
        assert.equal(row.patch.pan_deg, actual.state.pan_deg); assert.equal(row.patch.tilt_deg, actual.state.tilt_deg);
        assert.equal(row.patch.radials.length, 6);
        assert.deepEqual(row.patch.radials.map(zone => zone.number), [1, 2, 3, 4, 5, 6]);
        for (const zone of row.patch.radials) {
          assert.deepEqual(Object.keys(zone).sort(), ['brightness', 'color_temp_kelvin', 'number', 'on']);
          const saved = actual.state.radials.find(item => item.number === zone.number);
          assert.deepEqual(zone, { number: saved.number, on: saved.on, brightness: saved.brightness, color_temp_kelvin: saved.color_temp_kelvin });
        }
      }
      return { all_calls_before_release: before.length, all_calls_after_release: after.length,
        unexpected_late_calls: 0, exact_reconciliation_calls: late.length, reconciliation_token: transaction.id, producer_descriptors_unchanged: true };
    };

    await waitSite('echo'); await selectHome('victoria');
    await page.waitForFunction(() => window.HomeApartmentDirectional && window.__APT_ENGINE?.overlay.virtualFixturesReadback().fixture_count === 4);
    await page.evaluate(async () => { const engine = window.__APT_ENGINE; engine.rig.snapHome(0); await engine.modes.preload(['collision']); });
    const initialInventory = await page.evaluate(() => {
      const overlay = window.__APT_ENGINE.overlay; window.__directionalCalls = []; window.__directionalReconciliations = [];
      const inventory = overlay.virtualFixturesReadback();
      let sequence = 0, activeReconciliation = null;
      const descriptors = () => overlay.virtualFixturesReadback().fixtures.map(item => overlay.virtualFixtureInteractionDescriptor(item.fixture_id));
      const setDocument = overlay.setVirtualFixtures;
      overlay.setVirtualFixtures = (...args) => {
        const document = args[0], transaction = { id: ++sequence, at: performance.now(), before: structuredClone(descriptors()),
          document: document ? { binding: structuredClone(document.binding), revision: document.revision, digest: document.digest } : null, completed: false };
        activeReconciliation = transaction; window.__directionalReconciliations.push(transaction);
        try { return setDocument(...args); }
        finally { queueMicrotask(() => {
          transaction.after = structuredClone(descriptors()); transaction.completed = true;
          if (activeReconciliation === transaction) activeReconciliation = null;
        }); }
      };
      for (const method of ['setVirtualFixtureState', 'aimVirtualFixtureAtTarget', 'restoreVirtualFixtureAim']) {
        const original = overlay[method]; overlay[method] = (...args) => {
          const call = { method, fixture_id: args[0], patch: structuredClone(args[1]), arguments_length: args.length,
            reconciliation: activeReconciliation?.id ?? null, at: performance.now(), frame: window.__APT_ENGINE.renderer.info.render.frame,
            native_move: structuredClone(window.__directionalLastMove || null),
            native_dispatch_active: window.__directionalLastMoveEvent?.isTrusted === true
              && window.__directionalLastMoveEvent.eventPhase !== Event.NONE
              && window.__directionalLastMoveEvent.currentTarget === window.__APT_ENGINE.picking.getHost() };
          window.__directionalCalls.push(call); const result = original(...args); call.ok = result?.ok === true;
          if (method === 'setVirtualFixtureState' && Array.isArray(args[1]?.radials)) queueMicrotask(() => {
            const readout = document.querySelector('[data-apt-directional-composer="1"] [role="status"]');
            call.immediate = { at: performance.now(), frame: window.__APT_ENGINE.renderer.info.render.frame,
              descriptor: structuredClone(overlay.virtualFixtureInteractionDescriptor(args[0])),
              readout: readout?.textContent, readout_hidden: !readout?.isConnected || readout.hidden };
          });
          window.__directionalDiagnostic?.({ kind: 'modeled', value: structuredClone(call) }).catch(() => {}); return result;
        };
      }
      return inventory;
    });
    assert.equal(initialInventory.visible, true); assert.equal(initialInventory.status, 'ready');
    const fixtures = initialInventory.fixtures, fixture = fixtures[0];
    await activateStatic(button('.apt-home-shell[data-home-layout="unified"]', 'Select ' + fixture.label)); await expectSelected(fixture);
    const originalAim = await descriptor(fixture.fixture_id);
    await check('a first selection opens Light and Zones; Aim then shows Spot with the literal compact native pill above the reticle and no fabricated pending-aim dot', async () => {
      // APT-R01: selecting from nothing opens Light, whose room tool is Zones.
      await expectMode('zones', button(INSPECTOR, 'Light', 'tab'));
      await activateStatic(button(INSPECTOR, 'Aim', 'tab'));
      await expectMode('spot');
      // Leaving Zones fades its ambient drawing out; read the Spot state once it has.
      await page.waitForFunction(() => window.__directionalDrawingState().visible === false);
      const mounted = await page.evaluate(({ scope, tab }) => {
        const node = window.__directionalDOM.scope(scope); if (!node) throw Error('missing actual composer');
        const selectedTab = window.__directionalDOM.query(tab); if (!selectedTab) throw Error('missing authored control: ' + tab.name);
        const circles = [...node.querySelectorAll('svg circle')], rect = element => {
          if (!element?.getClientRects().length) return null;
          const value = element.getBoundingClientRect(); return { x: value.x, y: value.y, width: value.width, height: value.height };
        };
        const drawing = window.__directionalDrawingState(), pill = node.querySelector('[role="group"][aria-label="Room lighting tool"]');
        return { selected_tab: selectedTab.getAttribute('aria-selected'), circles: circles.map(circle => ({ x: circle.getAttribute('cx'), y: circle.getAttribute('cy'), r: +circle.getAttribute('r'), fill: circle.getAttribute('fill') })),
          pill: rect(node.querySelector('[role="group"][aria-label="Room lighting tool"]')), hub: rect(circles.at(-1)),
          composer: { ...rect(node), width_css: node.clientWidth, height_css: node.clientHeight },
          pill_label: pill.querySelector('.nm')?.textContent, button_heights: [...pill.querySelectorAll('button')].map(button => rect(button)?.height),
          visible_svg_paths: [...node.querySelectorAll('svg path')].filter(path => path.getAttribute('fill') !== 'transparent').length,
          drawing, text: node.innerText, inventory: window.__APT_ENGINE.overlay.virtualFixturesReadback() };
      }, { scope: COMPOSER, tab: button(INSPECTOR, 'Aim', 'tab') });
      assert.equal(mounted.selected_tab, 'true');
      const circles = mounted.circles;
      assert.deepEqual(circles.map(circle => circle.r), [22, 7]);
      assert.equal(circles[0].x, circles[1].x); assert.equal(circles[0].y, circles[1].y);
      assert.deepEqual(circles.map(circle => circle.fill), ['transparent', 'transparent']);
      const { pill, hub } = mounted;
      assert.ok(pill && hub && pill.width > 0 && hub.height > 0);
      const { composer } = mounted, scaleX = composer.width / composer.width_css, scaleY = composer.height / composer.height_css;
      assert.ok([scaleX, scaleY].every(value => Number.isFinite(value) && value > 0));
      assert.ok(Math.abs(pill.y + pill.height - (composer.y + Math.round(+circles[0].y - 26) * scaleY)) < .01,
        'native pill follows the handoff whole-pixel 26px placement above the exact projected hub');
      assert.ok(Math.abs(pill.x + pill.width / 2 - (composer.x + Math.round(+circles[0].x) * scaleX)) < .01,
        'native pill center follows the handoff whole-pixel projected placement');
      assert.ok(pill.y + pill.height < hub.y, 'compact pill clears the existing registered fixture center');
      assert.deepEqual(mounted.button_heights, [24, 24]); assert.ok(pill.height <= 34);
      assert.equal(mounted.pill_label, fixture.label); assert.equal(mounted.visible_svg_paths, 0);
      assert.equal(mounted.drawing.visible, false, 'Spot mode leaves the borrowed ambient drawing hidden');
      assert.equal(mounted.inventory.fixture_count, 4);
      assert.equal(await page.evaluate(scope => !!window.__directionalDOM.scope(scope)?.querySelector('[data-apt-aim-pending]'), COMPOSER), false,
        'no pending dot without a proposal');
      return { default: 'zones', aim_tab: 'spot', actual_hub_only: true, fixtures: 4 };
    });
    await check('existing native Spot drag still creates an aim and enables its existing Undo', async () => {
      // The reticle is the only aim start (APT-A01); its drop proposes and an explicit Set aims.
      const start = await page.evaluate(() => {
        const node = document.querySelector('[data-apt-aim-reticle]'), rect = node && !node.hidden ? node.getBoundingClientRect() : null;
        return rect && rect.width > 0 ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null;
      }), end = await canvasPoint('surface', null, start); assert.ok(start && end);
      await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 8 }); await page.mouse.up();
      await expectSelected(fixture);
      await page.waitForFunction(() => document.querySelector('[data-apt-aim-bar]')?.hidden === false);
      assert.deepEqual(await descriptor(fixture.fixture_id), originalAim, 'the drop leaves the current aim until Set aim');
      assert.equal(await page.evaluate(scope => !!window.__directionalDOM.scope(scope)?.querySelector('[data-apt-aim-pending]'), COMPOSER), false,
        'Spot is the tool, so the room bar shows instead of the pending dot');
      await page.keyboard.press('Enter');
      await page.waitForFunction(spec => window.__directionalDOM.visible(window.__directionalDOM.query(spec)), button(INSPECTOR, 'Undo aim'));
      const observed = await page.evaluate(({ spec, id }) => {
        const node = window.__directionalDOM.query(spec); if (!node) throw Error('missing authored control: ' + spec.name);
        return { disabled: node.disabled, descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id) };
      }, { spec: button(INSPECTOR, 'Undo aim'), id: fixture.fixture_id });
      assert.equal(observed.disabled, false);
      const actual = observed.descriptor; assert.ok(actual.target); assert.notDeepEqual(actual.target, originalAim.target);
      return { native_drag: true, target_present: true, undo_enabled: true };
    });
    await check('all six modeled ring zones support native keyboard selection and isolated brightness/temperature controls', async () => {
      await activateStatic(button(INSPECTOR, 'Light', 'tab')); await expectMode('zones');
      let previousZone = null;
      for (let number = 1; number <= 6; number++) {
        await activateStatic(zoneButton(number), number % 2 ? 'Enter' : 'Space', { readBefore: { pressed: previousZone },
          checkBefore: observations => { if (previousZone) assert.equal(observations.pressed, 'true'); } });
        previousZone = zoneButton(number);
      }
      await activateStatic(zoneButton(1), 'Enter', { readBefore: { pressed: previousZone },
        checkBefore: observations => assert.equal(observations.pressed, 'true') });
      const power = input(CHANNEL, 'Radial zone 1 power', 'checkbox');
      const readPower = { power: { spec: power, id: fixture.fixture_id } }; let originalPower, after;
      await activateStatic(power, 'Space', { readBefore: readPower, checkBefore: observations => {
        originalPower = observations.power;
        assert.ok(originalPower.descriptor.state.radials[0].brightness > 0, 'power memory is tested from an actual nonzero saved level');
        assert.equal(originalPower.checked, true);
      } });
      const before = originalPower.descriptor;
      await activateStatic(power, 'Space', { readBefore: readPower, checkBefore: observations => {
        const offPower = observations.power; after = offPower.descriptor; assert.equal(after.state.radials[0].on, false);
        assert.equal(after.state.radials[0].brightness, before.state.radials[0].brightness, 'power Off remembers the saved nonzero level');
        assert.equal(offPower.checked, false);
      } });
      await activateStatic(button(CHANNEL, 'Off'), 'Enter', { readBefore: readPower, checkBefore: observations => {
        const onPower = observations.power; after = onPower.descriptor; assert.equal(after.state.radials[0].on, true);
        assert.equal(after.state.radials[0].brightness, before.state.radials[0].brightness, 'power On restores the same saved level');
        assert.deepEqual(after.state.radials.slice(1), before.state.radials.slice(1));
        assert.deepEqual(withoutRadials(after), withoutRadials(before)); assert.equal(onPower.checked, true);
      } });
      const allOffObservation = await page.evaluate(({ spec, id }) => {
        const node = window.__directionalDOM.query(spec); if (!node) throw Error('missing all-zones control');
        return { descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), selected: node.getAttribute('aria-pressed') };
      }, { spec: { scope: RING, selector: 'g[role="button"]', name: 'All radial zones', tag: 'g', role: 'button' }, id: fixture.fixture_id });
      const allOff = allOffObservation.descriptor;
      assert.ok(allOff.state.radials.every(zone => zone.on === false && zone.brightness === 0), 'the literal quick Off preset affects all six zones');
      assert.deepEqual(allOff.state.radials.map(zone => zone.color_temp_kelvin), before.state.radials.map(zone => zone.color_temp_kelvin));
      assert.deepEqual(withoutRadials(allOff), withoutRadials(before));
      assert.equal(allOffObservation.selected, 'true',
        'quick levels clear the individual zone selection');
      await activateStatic(zoneButton(1));
      await activateStatic(input(INSPECTOR, 'Radial brightness', 'range'), 'ArrowRight');
      await activateStatic(input(INSPECTOR, 'Radial color temperature', 'range'), 'ArrowRight');
      after = await descriptor(fixture.fixture_id);
      assert.equal(after.state.radials[0].brightness, .01); assert.equal(after.state.radials[0].on, true);
      assert.equal(after.state.radials[0].color_temp_kelvin, before.state.radials[0].color_temp_kelvin + 100);
      assert.deepEqual(after.state.radials.slice(1), allOff.state.radials.slice(1)); assert.deepEqual(withoutRadials(after), withoutRadials(before));
      await page.keyboard.press('z'); await expectMode('zones'); await page.keyboard.press('a'); await expectMode('zones');
      return { selected_with_enter_and_space: [1, 2, 3, 4, 5, 6], only_zone: 1, power_off_on_kept_saved_level: true,
        explicit_off_preset_all_zones: 0, preset_clears_individual_selection: true, native_brightness_step: .01, focused_field_owns_a_z: true };
    });
    await check('Show in room and unfocused A/Z use native Zones/Spot modes on the actual projected fixture plane', async () => {
      await activateStatic(button(INSPECTOR, 'Aim', 'tab')); await expectMode('spot');
      await activateStatic(button(INSPECTOR, 'Light', 'tab')); await expectMode('zones');
      await activateStatic(button(INSPECTOR, 'Show in room')); await expectMode('zones');
      const geometry = await planePoint(fixture.fixture_id, 1, .7);
      const fade = await verifyContinuousDrawing();
      await setMode('spot', button(INSPECTOR, 'Aim', 'tab'));
      await setMode('zones', button(INSPECTOR, 'Light', 'tab'));
      await blur(); await page.keyboard.press('a'); await expectMode('spot'); await page.keyboard.press('z'); await expectMode('zones');
      return { native_tab_modes: ['Aim:spot', 'Light:zones'], svg_hub: geometry.actualHub, actual_three_tick: geometry.actualTick, renderer_projection_agrees: true, fade, shortcuts: ['a', 'z'] };
    });
    await check('a native current-fixture hub click returns from Chat to Controls without a modeled write or aim change', async () => {
      await activateStatic(button(SHELL_TABS, 'Chat', 'tab'));
      const entry = await page.evaluate(({ id, spec }) => {
        const node = window.__directionalDOM.query(spec); if (!node) throw Error('missing authored control: ' + spec.name);
        return { selected: node.getAttribute('aria-selected'), descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id),
          callCount: window.__directionalCalls.length };
      }, { id: fixture.fixture_id, spec: button(SHELL_TABS, 'Chat', 'tab') });
      assert.equal(entry.selected, 'true');
      const hub = await hubPoint(fixture.fixture_id); await page.mouse.click(hub.x, hub.y);
      await expectSelected(fixture, 'zones', true); await waitHUD();
      const returned = await page.evaluate(id => ({ descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id),
        calls: window.__directionalCalls }), fixture.fixture_id);
      assert.deepEqual(returned.descriptor, entry.descriptor, 'revealing the current selection preserves its state, aim target and canonical binding');
      assert.deepEqual(returned.calls.slice(entry.callCount), [], 'a current-fixture click is selection/navigation only');
      return { restored_tab: 'controls', selected_fixture_id: fixture.fixture_id, descriptor_exact: true, modeled_calls: 0 };
    });
    await check('native hub pickup survives ambient rig settling without a jump; outward, inward and cross-zone motion only paints modeled radials', async () => {
      const entry = await page.evaluate(id => ({ descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id),
        calls: window.__directionalCalls.length }), fixture.fixture_id);
      const before = entry.descriptor, callsBefore = entry.calls;
      await startHub(fixture.fixture_id);
      await page.waitForFunction(scope => {
        const pill = document.querySelector(scope + ' [role="group"][aria-label="Room lighting tool"]');
        return pill?.isConnected && pill.hidden && getComputedStyle(pill).display === 'none' && pill.getClientRects().length === 0;
      }, COMPOSER);
      const settlingBefore = await page.evaluate(() => ({ matrix: window.__APT_ENGINE.camera.matrixWorld.toArray(), pose: window.__APT_ENGINE.rig.snapshotPose() }));
      const navigationBefore = navigationFrom(settlingBefore.pose); await waitHUD();
      const settlingAfter = await page.evaluate(id => {
        const engine = window.__APT_ENGINE, down = [...window.__directionalNative].reverse().find(row => row.type === 'pointerdown' && row.pointerType === 'mouse');
        return { matrix: engine.camera.matrixWorld.toArray(), pose: engine.rig.snapshotPose(),
          captured: !!down && engine.picking.getHost().hasPointerCapture(down.pointerId), descriptor: engine.overlay.virtualFixtureInteractionDescriptor(id) };
      }, fixture.fixture_id);
      assert.notDeepEqual(settlingAfter.pose.pivot, settlingBefore.pose.pivot, 'the actual rig ambient pivot is still settling during pickup');
      assert.notDeepEqual(settlingAfter.matrix, settlingBefore.matrix, 'actual rendered camera movement witnesses the ambient case');
      assert.deepEqual(navigationFrom(settlingAfter.pose), navigationBefore); assert.equal(settlingAfter.captured, true, 'ambient settling does not retire the radial owner');
      assert.deepEqual(settlingAfter.descriptor, before);
      const outward = await planePoint(fixture.fixture_id, 1, 1.184); await page.mouse.move(outward.client.x, outward.client.y, { steps: 4 });
      const outwardImmediate = await immediatePaint(1, .9, outward); await waitHUD();
      const painted = await page.evaluate(({ scope, id }) => {
        const node = window.__directionalDOM.scope(scope); if (!node) throw Error('missing active composer');
        return { descriptor: window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id),
          drawing: window.__directionalDrawingState() };
      }, { scope: COMPOSER, id: fixture.fixture_id });
      assert.equal(painted.descriptor.state.radials[0].brightness, .9);
      assertNodeColors(painted.drawing, painted.descriptor, 1);
      assert.ok(painted.drawing.guides.every(guide => guide.visible), 'only an active gesture shows the actual Three inner, outer and zone guides');
      const inward = await planePoint(fixture.fixture_id, 1, .546); await page.mouse.move(inward.client.x, inward.client.y, { steps: 4 });
      const inwardImmediate = await immediatePaint(1, .35, inward); await waitHUD();
      assert.equal((await descriptor(fixture.fixture_id)).state.radials[0].brightness, .35);
      const cross = await planePoint(fixture.fixture_id, 2, 1.068); await page.mouse.move(cross.client.x, cross.client.y, { steps: 8 });
      const crossImmediate = await immediatePaint(2, .8, cross); await page.mouse.up();
      const after = await descriptor(fixture.fixture_id); assert.equal(after.state.radials[1].brightness, .8);
      await waitDOM(COMPOSER + ' [role="group"][aria-label="Room lighting tool"]');
      const released = await page.evaluate(scope => {
        const node = window.__directionalDOM.scope(scope); if (!node) throw Error('missing released composer');
        return { drawing: window.__directionalDrawingState(), calls: window.__directionalCalls };
      }, COMPOSER);
      assertNodeColors(released.drawing, after);
      assert.ok(released.drawing.guides.every(guide => !guide.visible), 'released native gesture withdraws all actual guides');
      assert.deepEqual(withoutRadials(after), withoutRadials(before));
      assert.deepEqual(after.state.radials.map(zone => zone.color_temp_kelvin), before.state.radials.map(zone => zone.color_temp_kelvin));
      const calls = released.calls.slice(callsBefore); assert.ok(paintWrites(calls).length > 0);
      for (const row of calls) { assert.equal(row.method, 'setVirtualFixtureState'); assert.deepEqual(Object.keys(row.patch), ['radials']);
        for (const zone of row.patch.radials) assert.deepEqual(Object.keys(zone).sort(), ['brightness', 'number', 'on']); }
      await activateStatic(button(INSPECTOR, 'Aim', 'tab'));
      await activateStatic(button(INSPECTOR, 'Light', 'tab'), 'Enter', {
        readBefore: { history: [button(INSPECTOR, 'Undo aim'), button(INSPECTOR, 'Redo aim')] },
        checkBefore: ({ history }) => { assert.equal(history[0], false); assert.equal(history[1], true); }
      });
      assert.deepEqual((await inventory()).fixtures.filter(item => item.fixture_id !== fixture.fixture_id), initialInventory.fixtures.filter(item => item.fixture_id !== fixture.fixture_id));
      return { pickup_exact: true, ambient_camera_movement_observed: true, mode_pill_hidden_during_owned_drag: true,
        immediate_native_updates: [outwardImmediate, inwardImmediate, crossImmediate], outward: .9, inward: .35,
        cross_zone: 2, cross_level: .8, only_radials_written: true, aim_target_and_history_controls_preserved: true };
    });
    await check('native empty-space orbit and another fixture selection retain their existing behavior in Zones', async () => {
      const empty = await canvasPoint('empty', undefined, null, true); assert.ok(empty);
      const before = empty.navigation;
      await page.mouse.move(empty.x, empty.y); await page.mouse.down(); await page.mouse.move(empty.x + 35, empty.y + 8, { steps: 8 }); await page.mouse.up();
      await page.waitForFunction(before => window.__APT_ENGINE.rig.azimuthIndex() !== before.azimuth
        && window.__APT_ENGINE.camera.matrixWorld.toArray().some((value, index) => value !== before.matrix[index]), before);
      await expectSelected(fixture);
      await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await waitHUD();
      const other = await canvasPoint('fixture', fixtures[1].fixture_id); assert.ok(other);
      await page.mouse.click(other.x, other.y); await expectSelected(fixtures[1]);
      const original = await canvasPoint('fixture', fixture.fixture_id); assert.ok(original);
      await page.mouse.click(original.x, original.y); await expectSelected(fixture, 'zones');
      return { empty_space_orbits: true, native_fixture_selection: fixtures[1].fixture_id };
    });
    // Native input/IPC may span the existing 80ms flush. These cases prove
    // active-gesture retirement; deterministic pending-drop proof is in the
    // actual-source integration suite, not inferred from this event sequence.
    const activeMotion = async () => {
      await startHub(fixture.fixture_id, true); const point = await planePoint(fixture.fixture_id, 1, 1.5, true);
      await page.mouse.move(point.client.x, point.client.y); return point;
    };
    await check('Escape and a second native pointer retire active painting without later modeled calls', async () => {
      await activeMotion(); await page.keyboard.press('Escape'); const escaped = await retirementRead('keydown', null, 'Escape');
      assert.equal(escaped.captured, false); const escape = escaped.marker;
      assert.ok(escape?.isTrusted); await unchangedAfterRetirement(escape, false, escaped.calls); await expectSelected(fixture);
      await activeMotion(); const cdp = observe(await context.newCDPSession(rawPage), 'cdp'), second = await hubPoint(fixture.fixture_id);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: second.x, y: second.y, id: 7 }] });
      const touched = await retirementRead('pointerdown', null, null, 'touch'); assert.equal(touched.captured, false);
      const touch = touched.marker;
      assert.ok(touch?.isTrusted); await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach();
      await unchangedAfterRetirement(touch); await expectSelected(fixture);
      return { trusted_escape: true, trusted_second_pointer: true, late_modeled_calls: 0, unflushed_queue_not_inferred: true };
    });
    await check('native wheel cancels painting while retaining the existing zoom action', async () => {
      const before = await page.evaluate(() => window.__APT_ENGINE.rig.zoomIndex()); await activeMotion();
      await page.mouse.wheel(0, -180); const wheeled = await retirementRead('wheel'), marker = wheeled.marker;
      assert.equal(wheeled.captured, false); await unchangedAfterRetirement(marker, false, wheeled.calls);
      await page.waitForFunction(before => window.__APT_ENGINE.rig.zoomIndex() !== before, before);
      await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await waitHUD();
      return { paint_canceled: true, zoom_preserved: true };
    });
    await check('hiding the native fixture layer retires paint before a later pointer release', async () => {
      await activeMotion(); await activateStatic(button('.apt-home-shell[data-home-layout="unified"]', 'Layers'), 'Space');
      const checkbox = input(LAYERS, 'Show virtual spotlights layer', 'checkbox');
      await activateStatic(checkbox, 'Space'); await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().visible === false);
      await waitDOM(COMPOSER, true); const retired = await retirementRead('click', 'Show virtual spotlights layer');
      const reconciliation = await unchangedAfterRetirement(retired.marker, true, retired.calls);
      await activateStatic(checkbox, 'Space'); await activateStatic(button(LAYERS, 'Close layers'));
      await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().visible === true); await expectMode('zones');
      return { hidden_retired: true, ...reconciliation, restored_display_on: true };
    });
    await check('Setup retires active painting with only the exact saved-state reconciliation and no save', async () => {
      await activeMotion(); await activateStatic(button('.apt-home-topbar', 'Setup'), 'Space');
      await waitDOM(TOOLBAR);
      await waitDOM(COMPOSER, true); const retired = await retirementRead('click', 'Setup');
      const reconciliation = await unchangedAfterRetirement(retired.marker, true, retired.calls);
      await activateStatic(button(TOOLBAR, 'done'));
      // Returning from Setup must retain the chosen Zones tool; an old aim
      // notice replayed on inspector remount is not a new mode-selection event.
      await expectSelected(fixture, 'zones'); return { setup_retired: true, ...reconciliation, saves: 0 };
    });
    await check('the existing Spot Undo still restores its original aim after radial editing', async () => {
      await setMode('spot'); await activateStatic(button(INSPECTOR, 'Undo aim'));
      const actual = await descriptor(fixture.fixture_id); assert.deepEqual(actual.target, originalAim.target);
      assert.equal(actual.state.pan_deg, originalAim.state.pan_deg); assert.equal(actual.state.tilt_deg, originalAim.state.tilt_deg);
      await setMode('zones'); return { original_pan_tilt_target_restored: true };
    });
    await check('a native home change retires active painting and preserves the exact authored saves', async () => {
      await activeMotion(); await activateStatic(button(HOME, 'Echo Park'), 'Space'); await waitSite('echo');
      await waitDOM(COMPOSER, true); const retired = await retirementRead('click', 'Echo Park'); await unchangedAfterRetirement(retired.marker, false, retired.calls);
      assert.equal(await page.evaluate(scope => document.querySelectorAll(scope).length, INSPECTOR), 0); await selectHome('victoria'); await expectSelected(fixture, 'zones');
      const restored = await inventory(); assert.equal(restored.visible, true); assert.equal(restored.fixture_count, 4);
      return { home_retired: true, late_modeled_calls: 0, fixture_display_on: true, fixtures: 4 };
    });
    const finalEvents = await page.evaluate(() => ({ native: window.__directionalNative, calls: window.__directionalCalls,
      reconciliations: window.__directionalReconciliations, keyboardTargets: window.__directionalKeyTargets,
      mouse: window.__directionalMouseObserved, dragstarts: window.__directionalHtmlDragstarts }));
    receipt.native_events = finalEvents.native; receipt.modeled_calls = finalEvents.calls; receipt.reconciliations = finalEvents.reconciliations;
    receipt.keyboard_targets = finalEvents.keyboardTargets;
    receipt.native_mouse.observed = finalEvents.mouse; receipt.native_mouse.dragstarts = finalEvents.dragstarts;
    const mouseLedger = nativeMouse.readback(), mouseCalls = {};
    for (const row of mouseLedger.operations) if (row.type !== 'keyboard') mouseCalls[row.type] = (mouseCalls[row.type] || 0) + 1;
    assert.deepEqual(mouseCalls, { move: 28, down: 13, up: 13, click: 3, wheel: 1 }, 'all original native mouse call counts remain exact');
    assert.equal(mouseLedger.expected.filter(row => row.type === 'move').length, 55, 'all original interpolation samples remain exact');
    receipt.native_mouse.proof = assertDirectionalNativeMouseLedger({ ledger: mouseLedger, observed: finalEvents.mouse, dragstarts: finalEvents.dragstarts });
    assert.deepEqual(receipt.keyboard_targets.map(({ id, key, site }) => ({ id, key, site })), expectedKeys,
      'every scheduled native key reaches its freshly admitted home/control in exact order');
    for (const event of receipt.keyboard_targets) { assert.equal(event.isTrusted, true); assert.equal(event.exact_target, true); }
    for (const [file, expected] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack and registration bytes stay exact');
    for (const [file, expected] of Object.entries(receipt.source_sha256)) assert.equal(receipt.served[file]?.sha256, expected, 'served production source matches the captured candidate');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.missing_code, []); assert.deepEqual(receipt.errors, []);
    assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false);
    receipt.authored_data_hashes_unchanged = true; receipt.fixture_display_on = true;
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) {
    receipt.result = 'failed'; receipt.failure = error.stack || String(error); save();
    if (page && !page.isClosed()) {
      const observed = await page.evaluate(() => ({ calls: window.__directionalCalls || [],
        reconciliations: window.__directionalReconciliations || [], native: window.__directionalNative || [] })).catch(() => null);
      if (observed) { receipt.modeled_calls = observed.calls; receipt.reconciliations = observed.reconciliations; receipt.native_events = observed.native; }
      save();
    }
    if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({
      selected: (() => { const ids = []; window.__APT_ENGINE?.overlay.virtualFixturesGroup?.traverse(node => {
        if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
      }); return ids; })(), inspector: document.querySelector('[data-victoria-virtual-fixtures="1"]')?.getAttribute('aria-label'),
      events: window.__directionalNative?.slice(-20), calls: window.__directionalCalls?.slice(-20),
      composer: document.querySelector('[data-apt-directional-composer="1"]')?.outerHTML?.slice(0, 2000),
      overlay: window.HomeOverlay?.topKey?.(), activeElement: document.activeElement?.outerHTML?.slice(0, 600),
      site: window.__HOME_APARTMENT_SITE_API?.readback(), controls: document.querySelector('.apt-home-controls')?.innerText?.slice(0, 1600),
    })).catch(() => null);
    save(); throw error;
  }
  finally {
    clearTimeout(timer); save();
    for (const [name, close] of [['mouse-session', () => mouseSession?.detach()], ['context', () => context?.close()], ['browser', () => browser?.close()],
      ['server', async () => { server?.closeAllConnections(); if (server) await new Promise(resolve => server.close(resolve)); }]]) {
      try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push({ name, message: error.message }); }
    }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
