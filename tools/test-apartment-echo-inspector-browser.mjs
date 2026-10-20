// APT-L04/R03: the actual Echo Light inspector, Quick controls, Toast and Quick
// module in an authored host. The HA adapter is in-page and recording only:
// no Home graph, room, client, service, network or physical output.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const jsx = name => babel.transform(read(name), { presets: ['react'], sourceType: 'script' }).code;
const css = read('home-tokens.css').replace(/url\("(vendor\/fonts\/[a-z-]+\.woff2)"\)/g,
  (_, name) => `url("data:font/woff2;base64,${fs.readFileSync(path.join(SRC, name)).toString('base64')}")`);
const scenarios = [
  { theme: 'dark', width: 1366, height: 768, mobile: false },
  { theme: 'light', width: 390, height: 844, mobile: true },
];

// The authored host follows home-apartment.jsx's Quick contract: plan, apply
// through the caller's dispatch, then a guarded Toast action that restores.
const HOST = `(() => {
  const h = React.createElement, Q = window.HomeApartmentQuick, UI = window.HomeApartmentUI;
  const proof = window.__echoProof = { calls: [], forbidden: [] };
  Object.defineProperty(window, '__hav_haClient', { get() { proof.forbidden.push('HA client'); throw Error('Forbidden HA client'); } });
  const reported = new Date(2026, 9, 19, 20, 58, 30).toISOString();
  const ct = (brightness, kelvin) => ({ state: 'on', last_reported: reported, attributes: { friendly_name: 'Front left', brightness,
    color_mode: 'color_temp', color_temp_kelvin: kelvin, supported_color_modes: ['color_temp'], min_color_temp_kelvin: 2200, max_color_temp_kelvin: 6500 } });
  const states = proof.states = {
    'light.front_left': ct(178, 2700),
    'light.front_right': { state: 'off', last_reported: reported, attributes: { supported_color_modes: ['color_temp'] } },
    'switch.desk_lamp': { state: 'on', last_reported: reported, attributes: { friendly_name: 'Desk lamp' } },
    'scene.evening': { state: 'unknown', attributes: { friendly_name: 'Evening', entity_id: ['light.front_left', 'light.front_right', 'switch.desk_lamp'] } },
    'script.wind_down': { state: 'off', attributes: { friendly_name: 'Wind down' } },
  };
  const devices = [
    { id: 'front-left', type: 'light', name: 'Front left', ha_entity_id: 'light.front_left', room_id: 'living', fixture_kind: 'engineered_gimbal_v1' },
    { id: 'front-right', type: 'light', name: 'Front right', ha_entity_id: 'light.front_right', room_id: 'living' },
    { id: 'desk-lamp', type: 'plug', name: 'Desk lamp', ha_entity_id: 'switch.desk_lamp', room_id: 'living' },
  ];
  const scene = { 'light.front_left': ct(64, 2200), 'light.front_right': ct(128, 2400), 'switch.desk_lamp': { state: 'off', attributes: {} } };
  let bump = () => {};
  // Recording adapter with Home Assistant service semantics.
  const dispatch = async (domain, service, data) => {
    proof.calls.push({ domain, service, data: JSON.parse(JSON.stringify(data)) });
    const id = data.entity_id, previous = states[id], at = new Date(2026, 9, 19, 21, 4).toISOString();
    if (domain === 'scene') for (const [target, next] of Object.entries(scene)) states[target] = { ...next, last_reported: at };
    else if (domain === 'script') {}
    else if (service === 'turn_off') states[id] = { ...previous, state: 'off', last_reported: at,
      attributes: domain === 'light' ? { supported_color_modes: previous.attributes.supported_color_modes } : {} };
    else if (domain === 'switch') states[id] = { ...previous, state: 'on', last_reported: at };
    else {
      const attributes = { ...previous.attributes, min_color_temp_kelvin: 2200, max_color_temp_kelvin: 6500 };
      if (data.brightness != null) attributes.brightness = data.brightness;
      if (data.brightness_pct != null) attributes.brightness = Math.round(data.brightness_pct * 2.55);
      if (data.color_temp_kelvin != null) Object.assign(attributes, { color_mode: 'color_temp', color_temp_kelvin: data.color_temp_kelvin });
      if (attributes.brightness == null) attributes.brightness = 255;
      if (!attributes.color_mode) Object.assign(attributes, { color_mode: 'color_temp', color_temp_kelvin: 4000 });
      states[id] = { ...previous, state: 'on', last_reported: at, attributes };
    }
    // HA state changes land before the call settles, as readback would see them.
    bump(); await new Promise(resolve => setTimeout(resolve, 5)); return true;
  };
  function Host() {
    const [connection, setConnection] = React.useState('offline');
    const [deviceId, setDeviceId] = React.useState('front-left');
    const [toast, setToast] = React.useState(null);
    const [busy, setBusy] = React.useState(false);
    const [, setTick] = React.useState(0);
    bump = () => setTick(value => value + 1);
    proof.set = patch => ReactDOM.flushSync(() => {
      if (patch.connection) setConnection(patch.connection);
      if (patch.deviceId) setDeviceId(patch.deviceId);
      setTick(value => value + 1);
    });
    const current = () => connection === 'online';
    const block = Q.quickBlock({ connection });
    const run = async action => {
      const plan = Q.planQuick(action, { devices, states });
      if (!plan || block.all || plan.energizing && block.energizing) return;
      setBusy(true);
      const result = await Q.applyQuick(plan, { states, dispatch, isCurrent: current }).finally(() => setBusy(false));
      if (!result.sent) return;
      setToast({ text: plan.label + (plan.note ? '. ' + plan.note : ''), actionLabel: 'Undo', timeoutMs: 6000,
        action: UI.createToastAction(() => { void Q.undoQuick(result.before, { dispatch, isCurrent: current })
          .then(undone => setToast({ text: undone.exact ? plan.label + ' undone' : plan.label + ' not exactly undone', timeoutMs: 6000 })); },
          { isCurrent: current }) });
    };
    const device = devices.find(item => item.id === deviceId);
    return h('aside', { className: 'apt-home-panel', 'aria-label': 'Authored workspace host' },
      h('div', { className: 'apt-home-controls' },
        h(window.AptEchoLightInspector, { homeName: 'Echo Park', device, state: states[device.ha_entity_id], roomName: 'Living room',
          connection, sim: false, engineered: device.fixture_kind === 'engineered_gimbal_v1', onService: dispatch,
          onBack: () => {}, onAim: () => { proof.aimOpened = (proof.aimOpened || 0) + 1; }, onSetup: () => {}, canSetup: true }),
        h(window.AptQuickControls, { chips: Q.quickChips(states), block, busy,
          onAll: on => { void run({ kind: 'all', on }); }, onChip: id => { void run({ kind: 'chip', entity_id: id }); } })),
      h(UI.Toast, { toast, onDismiss: dismissed => setToast(value => value === dismissed ? null : value) }));
  }
  const root = ReactDOM.createRoot(document.getElementById('root'));
  ReactDOM.flushSync(() => root.render(h(Host)));
})();`;

test('Echo light inspector and Quick: HA offline example, guarded controls and exact Undo with zero real dispatch', { timeout: 120000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-echo-inspector-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-echo-inspector-browser-1', authored_component: true, complete_home_app: false,
    authored_ha_adapter: 'in-page recording', physical_commands: 0, http_requests: [], websocket_attempts: [], errors: [], checks: [],
    sources: Object.fromEntries(['home-apartment-echo.jsx', 'home-apartment-states.js', 'home-apartment-quick.js', 'home-apartment-ui.jsx', 'home-tokens.css'].map(name => [name, sha(read(name))])) };
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
      await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%}#root{position:relative;height:100%;overflow:auto}</style><div id="root" class="apt-home-shell"></div>');
      await page.addStyleTag({ content: css });
      await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, scenario.theme);
      for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js', 'home-apartment-states.js', 'home-apartment-quick.js'])
        await page.addScriptTag({ content: read(name) });
      await page.addScriptTag({ content: jsx('home-apartment-ui.jsx') });
      await page.addScriptTag({ content: jsx('home-apartment-echo.jsx') });
      await page.addScriptTag({ content: HOST });
      const inspector = page.locator('[data-apt-echo-light-inspector="1"]'), quick = page.locator('[data-apt-quick="1"]');
      const calls = () => page.evaluate(() => window.__echoProof.calls.length);
      const exact = ids => page.evaluate(ids => ids.map(id => window.HomeApartmentQuick.snapshot(window.__echoProof.states, [id])[0]), ids);
      const check = async (name, operation) => { const detail = await operation(); receipt.checks.push({ scenario: label, name, ...detail }); save(); };
      await inspector.waitFor();

      await check('HA offline (Echo): controls disabled, "Last reported 20:58" shown, nothing queued', async () => {
        const blocked = inspector.locator('[data-apt-echo-blocked="offline"]');
        assert.match(await blocked.innerText(), /Home Assistant is offline\. Nothing is queued\./);
        assert.match(await inspector.locator('[data-apt-echo-last-reported="1"]').innerText(), /^Last reported 20:58 · 70% · 2700 K$/);
        assert.match(await inspector.locator('.apt-ui-badge').first().innerText(), /Home Assistant offline/);
        const power = inspector.getByRole('checkbox', { name: 'Front left power', exact: true });
        const brightness = inspector.getByRole('slider', { name: 'Front left brightness', exact: true });
        const temperature = inspector.getByRole('slider', { name: 'Front left color temperature', exact: true });
        for (const control of [power, brightness, temperature]) assert.equal(await control.isDisabled(), true);
        await power.click({ force: true }); await brightness.dispatchEvent('pointerup'); await temperature.dispatchEvent('keyup');
        for (const name of ['All on', 'All off']) assert.equal(await quick.getByRole('button', { name, exact: true }).isDisabled(), true);
        for (const name of ['Apply Evening', 'Run Wind down']) assert.equal(await quick.getByRole('button', { name, exact: true }).isDisabled(), true);
        assert.match(await quick.locator('[data-apt-quick-reason="1"]').innerText(), /Home Assistant is offline\. Quick controls return when it reconnects\./);
        await page.waitForTimeout(50);
        assert.equal(await calls(), 0, 'offline control never queues or sends');
        return { last_reported: '20:58', disabled_controls: 3, quick_disabled: 4, calls: 0 };
      });

      await page.evaluate(() => window.__echoProof.set({ connection: 'online' }));
      await check('online Echo light: same inspector with entity line, HA capabilities and one guarded call per settled value', async () => {
        assert.equal(await inspector.locator('[data-apt-echo-blocked]').count(), 0);
        assert.equal(await inspector.locator('[data-apt-echo-entity="1"]').innerText(), 'light.front_left');
        assert.equal(await inspector.locator('[data-apt-echo-calls="1"]').innerText(), 'In Home this calls light.turn_on for light.front_left.');
        assert.equal(await inspector.getByRole('heading', { name: 'Front left', exact: true }).count(), 1);
        assert.match(await inspector.innerText(), /Engineered · simulated preview/);
        for (const tab of ['Light', 'Aim', 'Setup']) assert.equal(await inspector.getByRole('tab', { name: tab, exact: true }).count(), 1);
        const temperature = inspector.getByRole('slider', { name: 'Front left color temperature', exact: true });
        assert.deepEqual([await temperature.getAttribute('min'), await temperature.getAttribute('max')], ['2200', '6500']);
        const brightness = inspector.getByRole('slider', { name: 'Front left brightness', exact: true });
        await brightness.focus(); await brightness.press('ArrowRight');
        await page.waitForFunction(() => window.__echoProof.calls.length === 1);
        assert.deepEqual(await page.evaluate(() => window.__echoProof.calls[0]),
          { domain: 'light', service: 'turn_on', data: { entity_id: 'light.front_left', brightness_pct: 71 } });
        await inspector.getByRole('tab', { name: 'Aim', exact: true }).click();
        await inspector.getByRole('button', { name: 'Open engineered aim', exact: true }).click();
        assert.equal(await page.evaluate(() => window.__echoProof.aimOpened), 1, 'the retained engineered aim panel stays reachable');
        await inspector.getByRole('tab', { name: 'Light', exact: true }).click();
        await page.evaluate(() => window.__echoProof.set({ deviceId: 'desk-lamp' }));
        assert.equal(await inspector.locator('[data-apt-echo-entity="1"]').innerText(), 'switch.desk_lamp');
        assert.equal(await inspector.getByRole('slider').count(), 0, 'a plug offers power only');
        await inspector.getByRole('checkbox', { name: 'Desk lamp power', exact: true }).click({ force: true });
        await page.waitForFunction(() => window.__echoProof.calls.length === 2);
        assert.deepEqual(await page.evaluate(() => window.__echoProof.calls[1]), { domain: 'switch', service: 'turn_off', data: { entity_id: 'switch.desk_lamp' } });
        await page.evaluate(() => window.__echoProof.set({ deviceId: 'front-left' }));
        return { brightness_calls: 1, plug_calls: 1, cct_range: [2200, 6500] };
      });

      const lights = ['light.front_left', 'light.front_right', 'switch.desk_lamp'];
      const undoRoundTrip = async (button, toastText) => {
        const before = await exact(lights), start = await calls();
        await quick.getByRole('button', { name: button, exact: true }).click();
        const toast = page.locator('.apt-ui-toast');
        await toast.getByText(toastText).waitFor();
        const changed = await exact(lights);
        assert.notDeepEqual(changed, before);
        await toast.getByRole('button', { name: 'Undo', exact: true }).click();
        await page.locator('.apt-ui-toast').getByText(toastText + ' undone').waitFor();
        assert.deepEqual(await exact(lights), before, `${button} Undo restores the exact previous state`);
        return { calls: await calls() - start };
      };
      await check('All off then Undo restores the exact previous state', async () => {
        const { calls: sent } = await undoRoundTrip('All off', 'All off');
        assert.equal(sent, 6);
        return { calls: sent };
      });
      await check('a scene chip applies once and Undo restores its lights exactly', async () => {
        const start = await calls(), { calls: sent } = await undoRoundTrip('Apply Evening', 'Evening');
        const recorded = await page.evaluate(start => window.__echoProof.calls.slice(start), start);
        assert.equal(recorded.filter(call => call.domain === 'scene').length, 1);
        return { calls: sent };
      });
      await check('scripts are marked to run and Travel Mode refuses energizing Quick controls', async () => {
        assert.match(await quick.getByRole('button', { name: 'Run Wind down', exact: true }).innerText(), /▶/);
        assert.match(await quick.innerText(), /runs a Home Assistant script once/);
        await page.evaluate(() => { window.__HOME_TRAVEL_MODE_STATE = 'on'; window.__echoProof.set({}); });
        assert.equal(await quick.getByRole('button', { name: 'All on', exact: true }).isDisabled(), true);
        assert.equal(await quick.getByRole('button', { name: 'All off', exact: true }).isDisabled(), false);
        assert.equal(await quick.getByRole('button', { name: 'Apply Evening', exact: true }).isDisabled(), true);
        assert.match(await quick.locator('[data-apt-quick-reason="1"]').innerText(), /Travel Mode is on/);
        await page.evaluate(() => { delete window.__HOME_TRAVEL_MODE_STATE; window.__echoProof.set({}); });
        return { travel_refuses: ['All on', 'Apply Evening', 'Run Wind down'] };
      });
      await check('Quick and inspector stay inside the viewport', async () => {
        const bounds = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth,
          widest: Math.max(...[...document.querySelectorAll('[data-apt-quick="1"] button,[data-apt-echo-light-inspector="1"] button')].map(node => node.getBoundingClientRect().right)) }));
        assert.ok(bounds.scroll <= bounds.width && bounds.widest <= bounds.width, JSON.stringify(bounds));
        await page.screenshot({ path: path.join(run, label + '.png') });
        return bounds;
      });
      assert.deepEqual(await page.evaluate(() => window.__echoProof.forbidden), []);
      await context.close();
    }
    assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.http_requests, []); assert.deepEqual(receipt.websocket_attempts, []);
    receipt.result = 'passed';
  } catch (error) { receipt.result = 'failed'; receipt.failure = error.stack || String(error); throw error; }
  finally { save(); await browser?.close(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length })); }
});
