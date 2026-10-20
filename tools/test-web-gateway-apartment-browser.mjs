// The real authenticated gateway, not a fixture static server, serves every
// module and precompiled file used by the mounted Apartment renderer.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { installSetupRooms, browserOptions, homeShell, hash } from './fixtures/apartment-setup-fixtures.mjs';
import { buildPrecompiledJsx } from './lib/precompiled-jsx.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { chromium } = createRequire(import.meta.url)('playwright');

test('authenticated production gateway executes Apartment modules, renders both authored homes and loads its private manifest', { timeout: 145000 }, async () => {
  fs.mkdirSync(path.join(ROOT, '.tmp'), { recursive: true });
  const run = fs.mkdtempSync(path.join(ROOT, '.tmp/gateway-apartment-browser-run-'));
  const rooms = installSetupRooms(run), originalRegistration = hash(fs.readFileSync(rooms.registrationFile));
  const receipt = { schema: 'home-gateway-apartment-browser-1', authored_room: true, actual_gateway: true,
    real_household_data: false, saved_edits: 0, physical_commands: 0, node: process.version,
    checks: [], forbidden: [], page_errors: [] };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let child, browser, context, tlsProxy, timer, diagnostics = '';
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => { browser?.close().catch(() => {}); reject(Error('145s real-gateway Apartment deadline')); }, 145000); });
  async function proof() {
    // An isolated serving tree keeps this test's production build independent
    // of other workers and excludes every private runtime asset prefix.
    const servingRoot = path.join(run, 'source'), appSource = path.join(ROOT, 'app/src');
    fs.mkdirSync(servingRoot);
    fs.cpSync(appSource, path.join(servingRoot, 'app/src'), { recursive: true, filter: file => {
      // Node18's Windows cp filter receives extended-length paths; compare
      // both sides in that same form before applying the private-prefix fence.
      const relative = path.relative(path.toNamespacedPath(appSource), path.toNamespacedPath(file)).split(path.sep).join('/');
      if (path.isAbsolute(relative) || relative === '..' || relative.startsWith('../')) return false;
      return !fs.lstatSync(file).isSymbolicLink() && !relative.split('/').some(part => part.startsWith('.'))
        && !/^(?:precompiled|private|data)(?:\/|$)/.test(relative)
        && !/^(?:assets\/apartment|simulation\/cameras)(?:\/|$)/.test(relative);
    } });
    fs.cpSync(path.join(ROOT, 'web-gateway'), path.join(servingRoot, 'web-gateway'), { recursive: true,
      filter: file => !fs.lstatSync(file).isSymbolicLink() && (fs.statSync(file).isDirectory() || /\.(?:mjs|js)$/.test(file)) });
    fs.mkdirSync(path.join(servingRoot, 'tools'));
    for (const name of ['package.json', 'tools/content-packs.mjs']) fs.copyFileSync(path.join(ROOT, name), path.join(servingRoot, name));
    const build = buildPrecompiledJsx({ srcDir: path.join(servingRoot, 'app/src') });
    assert.deepEqual(build.failed, []);
    receipt.precompiled_files = build.compiled.length;
    receipt.source_hashes = Object.fromEntries(['web-gateway/server.mjs', 'app/src/index.html', 'app/src/home-3d/engine.js',
      'app/src/precompiled/manifest.json'].map(name => [name, hash(fs.readFileSync(path.join(servingRoot, name)))])); save();
    const reservation = http.createServer(); reservation.listen(0, '127.0.0.1'); await once(reservation, 'listening');
    const port = reservation.address().port; await new Promise(resolve => reservation.close(resolve));
    const gatewayOrigin = 'http://127.0.0.1:' + port;
    // Preserve production's Secure owner cookie through an authored local TLS
    // terminator, as the deployed gateway sits behind its HTTPS ingress.
    const key = path.join(run, 'authored-key.pem'), cert = path.join(run, 'authored-cert.pem');
    const openssl = process.platform === 'win32' ? 'C:/Program Files/Git/usr/bin/openssl.exe' : 'openssl';
    execFileSync(openssl, ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=authored-gateway.invalid',
      '-addext', 'subjectAltName=IP:127.0.0.1', '-keyout', key, '-out', cert], { timeout: 10000, stdio: 'pipe', windowsHide: true });
    tlsProxy = https.createServer({ key: fs.readFileSync(key), cert: fs.readFileSync(cert) }, (req, res) => {
      const upstream = http.request(gatewayOrigin + req.url, { method: req.method, headers: { ...req.headers, 'x-forwarded-proto': 'https' } }, response => {
        res.writeHead(response.statusCode, response.headers); response.pipe(res);
      });
      upstream.on('error', () => res.destroy()); req.pipe(upstream);
    });
    await new Promise(resolve => tlsProxy.listen(0, '127.0.0.1', resolve));
    const origin = 'https://127.0.0.1:' + tlsProxy.address().port;
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('HOME_WEB_') && key !== 'HOME_CONTENT_PACKS_DIR'));
    // Pin every declared proxy target to this fixture origin as an additional
    // fence. Browser proxy/API reads below are intercepted before forwarding.
    const gatewaySource = fs.readFileSync(path.join(ROOT, 'web-gateway/server.mjs'), 'utf8');
    const targets = Object.fromEntries([...gatewaySource.matchAll(/env:\s*"(HOME_WEB_[A-Z_]+)"/g)].map(match => [match[1], gatewayOrigin]));
    child = spawn(process.execPath, ['web-gateway/server.mjs'], { cwd: servingRoot, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: {
      ...env, ...targets, NODE_ENV: 'test', HOME_WEB_HOST: '127.0.0.1', HOME_WEB_PORT: String(port),
      HOME_WEB_BASIC_AUTH: 'fixture-owner:fixture-password', HOME_WEB_AUTH_REQUIRED: '1', HOME_WEB_ENABLE_LEGACY_HA_PROXY: '1',
      HOME_WEB_AUTH_FILE: path.join(run, 'absent-auth.json'), HOME_WEB_HA_TOKEN_FILE: path.join(run, 'absent-ha-token'),
      HOME_WEB_STACK_TOKEN_FILE: path.join(run, 'absent-stack-token'), HOME_CONTENT_PACKS_DIR: rooms.packs,
      HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1', HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin,
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: rooms.privateRoot, HOME_WEB_AGENT_ORIGINS: 'http://agent.fixture',
      HOME_WEB_ALLOW_INSECURE_TEST_AGENT_ORIGINS: '1',
    } });
    const collect = data => { diagnostics = (diagnostics + data.toString()).slice(-8000); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    const startup = Date.now() + 15000;
    for (;;) {
      try { if ((await fetch(gatewayOrigin + '/healthz', { signal: AbortSignal.timeout(1000) })).ok) break; } catch {}
      if (Date.now() >= startup || child.exitCode !== null) assert.fail('fixture gateway startup failed: ' + diagnostics);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.equal((await fetch(gatewayOrigin + '/home-3d/adaptive-performance.mjs', { signal: AbortSignal.timeout(3000) })).status, 401, 'module authentication stays required');
    browser = await chromium.launch(browserOptions());
    context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block', ignoreHTTPSErrors: true }); // Only this fenced, ephemeral certificate.
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'victoria'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin || !['GET', 'HEAD'].includes(request.method())) {
        receipt.forbidden.push({ origin: url.origin, path: url.pathname, method: request.method() }); return route.abort();
      }
      if (url.pathname.startsWith('/proxy/')) {
        const value = /\/(?:model|apartment_model)$/.test(url.pathname) ? rooms.echo.seed
          : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
      }
      if (url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/apartment/v1/')) {
        return route.fulfill({ status: 503, contentType: 'application/json', body: '{"ok":false,"error":"authored_service_unavailable"}' });
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => { if (new URL(socket.url()).host !== new URL(origin).host) receipt.forbidden.push({ websocket: 'foreign origin' }); socket.close(); });
    const login = await context.request.post(origin + '/auth/login', { data: { username: 'fixture-owner', password: 'fixture-password' } });
    assert.equal(login.status(), 200);
    assert.equal((await (await context.request.get(origin + '/auth/status')).json()).authenticated, true);
    const precompiled = await (await context.request.get(origin + '/precompiled/manifest.json')).json();
    assert.equal(precompiled.verified, true, 'the actual gateway verifies the isolated production build');
    assert.equal(Object.keys(precompiled.files).length, receipt.precompiled_files);
    receipt.checks.push({ name: 'production precompiled manifest verified', passed: true });
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => receipt.page_errors.push(error.message));
    await page.goto(origin + '/auth', { waitUntil: 'domcontentloaded' });
    const imported = await page.evaluate(async () => {
      const module = await import('/home-3d/adaptive-performance.mjs');
      return { selector: typeof module.selectClientPerformanceTarget, controller: typeof module.createAdaptivePerformanceController };
    });
    assert.deepEqual(imported, { selector: 'function', controller: 'function' }, 'Chrome actually executes the ES module served by the gateway');
    receipt.checks.push({ name: 'authenticated browser ES module import', passed: true }); save();
    const moduleResponse = await context.request.get(origin + '/home-3d/adaptive-performance.mjs');
    assert.match(moduleResponse.headers()['content-type'], /^(?:text|application)\/javascript\b/);
    const shell = homeShell(page, origin);
    await shell.boot('1', 'victoria');
    for (const site of ['victoria', 'echo']) {
      if (site === 'echo') await shell.selectHome(site);
      const state = await page.evaluate(async () => { const engine = window.__APT_ENGINE; await engine.pointsPromise; return { site: engine.siteId, mode: engine.modes.mode, hasCanvas: !!window.__APT_CANVAS?.isConnected }; });
      assert.deepEqual(state, { site, mode: 'points', hasCanvas: true });
      receipt.checks.push({ name: 'actual gateway mounted ' + site + ' renderer', passed: true, state }); save();
    }
    const session = await context.newCDPSession(page);
    const manifest = await session.send('Page.getAppManifest');
    assert.equal(manifest.errors.filter(item => item.critical).length, 0, 'native manifest loader keeps the authenticated owner cookie');
    assert.equal(JSON.parse(manifest.data).name, 'Home');
    receipt.checks.push({ name: 'native private webmanifest fetch', passed: true });
    assert.equal(hash(fs.readFileSync(rooms.registrationFile)), originalRegistration, 'registration unchanged');
    assert.deepEqual(receipt.forbidden, []);
    assert.deepEqual(receipt.page_errors, []);
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); throw error; }
  finally {
    clearTimeout(timer);
    for (const close of [() => context?.close(), () => browser?.close()]) { try { await close(); } catch {} }
    if (tlsProxy) { tlsProxy.closeAllConnections(); await new Promise(resolve => tlsProxy.close(resolve)); }
    if (child && child.exitCode === null) { child.kill(); await Promise.race([once(child, 'exit').catch(() => {}), new Promise(resolve => setTimeout(resolve, 2000))]); if (child.exitCode === null) child.kill('SIGKILL'); }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
