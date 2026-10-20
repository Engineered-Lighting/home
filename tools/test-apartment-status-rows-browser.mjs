// APT-S02 / APT-F06: the actual top-bar status rows over the shared state
// module, in an authored host with Den's synthetic contract fixtures (test
// copies; see tools/fixtures/den-contracts/README.md). No Home graph, client,
// network, session start or physical output.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const TOOLS = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(TOOLS, '..'), SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const fixture = name => JSON.parse(fs.readFileSync(path.join(TOOLS, 'fixtures/den-contracts', name), 'utf8'));
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const jsx = name => babel.transform(read(name), { presets: ['react'], sourceType: 'script' }).code;
const css = read('home-tokens.css').replace(/url\("(vendor\/fonts\/[a-z-]+\.woff2)"\)/g,
  (_, name) => `url("data:font/woff2;base64,${fs.readFileSync(path.join(SRC, name)).toString('base64')}")`);
const scenarios = [
  { theme: 'dark', width: 1366, height: 768, mobile: false },
  { theme: 'light', width: 390, height: 844, mobile: true },
];
const FIXTURES = { vocabulary: fixture('reason-vocabulary.json'), capabilities: fixture('home-den-capabilities-victoria.json'),
  health: fixture('activity-health-events.json').events };

// The host mirrors ApartmentHomeBar: a top bar with the status disclosure
// open, and the shell state the Apartment emits.
const HOST = `(() => {
  const h = React.createElement;
  const proof = window.__statusProof = { forbidden: [] };
  Object.defineProperty(window, '__hav_haClient', { get() { proof.forbidden.push('HA client'); throw Error('Forbidden HA client'); } });
  const sites = [{ id: 'echo', shortName: 'Echo Park', sceneAvailable: true }, { id: 'victoria', shortName: 'Victoria', sceneAvailable: true }];
  const base = { sites, phase: 'ready', saveState: 'saved', physicalControls: false, haConnection: 'offline',
    haLastReportedAt: new Date(2026, 9, 19, 20, 58, 30).toISOString() };
  function Host() {
    const [state, setState] = React.useState({ ...base, siteId: 'echo', sourceLabel: 'Echo Park scan', hasPerception: false });
    proof.set = patch => ReactDOM.flushSync(() => setState(value => ({ ...value, ...patch })));
    return h('header', { className: 'apt-home-topbar' },
      h('span', { className: 'apt-home-wordmark' }, 'Home'),
      h('details', { className: 'apt-home-status', open: true },
        h('summary', { 'aria-label': 'Home status' }, h('span', { className: 'apt-home-status-summary' }, state.sourceLabel)),
        h(window.HomeApartmentStatusRows, { state })));
  }
  const root = ReactDOM.createRoot(document.getElementById('root'));
  ReactDOM.flushSync(() => root.render(h(Host)));
})();`;

test('status rows: HA offline (Echo), No session, Den contract rows and home isolation with zero requests', { timeout: 120000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-status-rows-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-status-rows-browser-1', authored_component: true, complete_home_app: false,
    synthetic_fixtures: 'tools/fixtures/den-contracts', physical_commands: 0, http_requests: [], websocket_attempts: [], errors: [], checks: [],
    sources: Object.fromEntries(['home-apartment-states.js', 'home-apartment-status.jsx', 'home-apartment-ui.jsx', 'home-tokens.css'].map(name => [name, sha(read(name))])) };
  let browser;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  try {
    const installed = fs.existsSync(chromium.executablePath());
    browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--renderer-process-limit=2'],
      ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    for (const scenario of scenarios) {
      const label = `${scenario.theme}-${scenario.mobile ? 'phone' : 'desktop'}`;
      const context = await browser.newContext({ viewport: { width: scenario.width, height: scenario.height },
        hasTouch: scenario.mobile, isMobile: scenario.mobile, serviceWorkers: 'block' });
      await context.route('**/*', route => { receipt.http_requests.push(route.request().url()); return route.abort(); });
      await context.routeWebSocket('**/*', socket => { receipt.websocket_attempts.push(socket.url()); socket.close(); });
      const page = await context.newPage(); page.setDefaultTimeout(5000);
      page.on('pageerror', error => receipt.errors.push(error.message));
      await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%}#root{position:relative;height:100%}</style><div id="root" class="apt-home-shell"></div>');
      await page.addStyleTag({ content: css });
      await page.evaluate(theme => { document.documentElement.dataset.theme = theme; document.getElementById('root').dataset.theme = theme; }, scenario.theme);
      for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js', 'home-apartment-states.js'])
        await page.addScriptTag({ content: read(name) });
      await page.addScriptTag({ content: jsx('home-apartment-ui.jsx') });
      await page.addScriptTag({ content: jsx('home-apartment-status.jsx') });
      await page.addScriptTag({ content: HOST });
      const status = page.locator('[role="status"][data-apt-status-site]');
      const row = key => status.locator(`[data-system="${key}"]`);
      const rowText = async key => (await row(key).innerText()).replace(/\s+/g, ' ').trim();
      const check = async (name, operation) => { const detail = await operation(); receipt.checks.push({ scenario: label, name, ...detail }); save(); };
      await status.waitFor();

      await check('HANDOFF "HA offline (Echo)": the status names Home Assistant, shows "Last reported 20:58" and queues nothing', async () => {
        assert.equal(await status.getAttribute('aria-label'), 'Echo Park · what is connected');
        assert.equal(await rowText('lights'), 'Lights Home Assistant not connected Light controls are disabled and nothing is queued. Last reported 20:58');
        assert.equal(await row('lights').locator('.apt-ui-badge').getAttribute('data-state'), 'disconnected');
        assert.equal(await status.locator('button').count(), 0, 'status offers no control that could queue output');
        await page.evaluate(() => window.__statusProof.set({ haConnection: 'online', physicalControls: true }));
        assert.equal(await rowText('lights'), 'Lights Home Assistant connected Output guards apply.');
        await page.evaluate(() => window.__statusProof.set({ haConnection: 'offline', physicalControls: false }));
        return { last_reported: '20:58', buttons: 0 };
      });

      await check('Victoria never inherits Echo\'s status, with or without a report of its own', async () => {
        await page.evaluate(() => window.__statusProof.set({ siteId: 'victoria', sourceLabel: 'Victoria Den', hasPerception: true }));
        const bare = await status.innerText();
        assert.doesNotMatch(bare, /20:58|Home Assistant|Light controls are disabled/);
        await page.evaluate(fixtures => {
          window.HomeApartmentStates.reports.setVocabulary(fixtures.vocabulary);
          if (window.HomeApartmentStates.reports.set('echo', { capabilities: fixtures.capabilities })) throw Error('a Den report filed under Echo');
          window.HomeApartmentStates.reports.set('victoria', { capabilities: fixtures.capabilities, activityHealth: fixtures.health });
        }, FIXTURES);
        await status.locator('[data-system="lighting-brain"]').waitFor();
        assert.equal(await status.getAttribute('aria-label'), 'Victoria · what is connected');
        assert.equal(await rowText('lights'), 'Lights No light entities for Den fixtures 4 modeled fixtures · nothing is sent to hardware.');
        assert.equal(await rowText('home-assistant'), 'Home Assistant Home Assistant connected', 'its own report, not the offline Echo client');
        assert.doesNotMatch(await status.innerText(), /20:58/);
        return { victoria_rows: await status.locator('[data-system]').count() };
      });

      await check('Den rows: brain, den ops, activity loop and capability sections through the vocabulary', async () => {
        assert.equal(await rowText('lighting-brain'), 'Lighting brain Brain online Reading light · Reading at sofa · spotlight on Details');
        assert.equal(await rowText('den-ops'), 'Den ops Monitoring normal Details');
        assert.match(await rowText('activity-loop'), /^Activity loop Deep sensing has no recent output JEV: OK · /);
        assert.equal(await row('capability-engineered').locator('.apt-ui-badge').getAttribute('data-state'), 'modeled');
        assert.match(await rowText('capability-engineered'), /Modeled · Supported · available 4 fixtures · physical output off/);
        assert.match(await rowText('capability-bench'), /^Bench Not supported here Unavailable Details$/);
        const details = row('capability-bench').locator('details.apt-home-status-codes');
        await details.locator('summary').click();
        assert.equal((await details.locator('li').innerText()).trim(), 'Unavailable · not_supplied', 'the raw code is in Details');
        assert.doesNotMatch(await status.innerText(), /permit|allowed|authori[sz]ed/i, 'capabilities never imply permission');
        return { capability_rows: await status.locator('[data-group="capabilities"]').count() };
      });

      await check('HANDOFF "No session": the status reads No session, with Start and Stop kept in the dock', async () => {
        await page.evaluate(fixtures => {
          const capabilities = JSON.parse(JSON.stringify(fixtures.capabilities));
          capabilities.perception.analysis = 'idle'; capabilities.perception.frame = null;
          window.HomeApartmentStates.reports.set('victoria', { capabilities });
        }, FIXTURES);
        // The store notifies React; wait for that render rather than racing it.
        await status.locator('[data-system="camera"] .apt-ui-badge[data-state="no-session"]').waitFor();
        assert.equal(await row('camera').locator('.apt-ui-badge').getAttribute('data-state'), 'no-session');
        assert.equal(await rowText('camera'), 'Camera No session No analysis session is running for this home. Start and Stop are in Camera · JEV.');
        return { camera: 'no-session' };
      });

      await check('Echo never reads the Den\'s report, and the rows stay inside the viewport at the text floor', async () => {
        await page.evaluate(() => window.__statusProof.set({ siteId: 'echo', sourceLabel: 'Echo Park scan', hasPerception: false }));
        const echo = await status.innerText();
        assert.doesNotMatch(echo, /Den|Brain|modeled/i);
        assert.match(await rowText('capabilities'), /^Capabilities Not reported/);
        await page.evaluate(() => window.__statusProof.set({ siteId: 'victoria', sourceLabel: 'Victoria Den', hasPerception: true }));
        const bounds = await page.evaluate(() => {
          const panel = document.querySelector('[data-apt-status-site]').getBoundingClientRect();
          const sizes = [...document.querySelectorAll('[data-apt-status-site] *')].filter(node => node.childNodes.length && [...node.childNodes].some(child => child.nodeType === 3 && child.textContent.trim()))
            .map(node => parseFloat(getComputedStyle(node).fontSize));
          return { left: panel.left, right: panel.right, width: innerWidth, scroll: document.documentElement.scrollWidth, smallest: Math.min(...sizes) };
        });
        assert.ok(bounds.left >= 0 && bounds.right <= bounds.width && bounds.scroll <= bounds.width, JSON.stringify(bounds));
        assert.ok(bounds.smallest >= 11.5, `text floor: ${bounds.smallest}px`);
        if (scenario.mobile) {
          const summary = await page.locator('details.apt-home-status-codes summary').first().boundingBox();
          assert.ok(summary.height >= 44, `Details is a 44 px target on touch: ${summary.height}`);
        }
        await page.screenshot({ path: path.join(run, label + '.png') });
        return bounds;
      });
      assert.deepEqual(await page.evaluate(() => window.__statusProof.forbidden), []);
      await context.close();
    }
    assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.http_requests, []); assert.deepEqual(receipt.websocket_attempts, []);
    receipt.result = 'passed';
  } catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); throw error; }
  finally { save(); await browser?.close(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length })); }
});
