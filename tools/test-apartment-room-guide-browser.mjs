// APT-S06: the actual room toolbar with the room guide, shared UI, overlay
// stack and theme CSS, fed authored facts. No renderer, room assets,
// household data, service, camera or model session is loaded.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8'), sha = value => createHash('sha256').update(value).digest('hex');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const source = read('home-apartment.jsx'), guideSource = read('home-apartment-room-guide.jsx'), uiSource = read('home-apartment-ui.jsx');
const css = read('home-tokens.css').replace(/@font-face\s*\{[^}]*\}/g, '');
const ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
const declaration = name => {
  const found = ast.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
  assert.equal(found.length, 1, 'one actual ' + name);
  return source.slice(found[0].start, found[0].end);
};

const homes = {
  echo: { homeName: 'Echo Park', hasVirtualFixtures: false, hasPerception: false, fixtureCount: 0,
    about: { homeId: 'echo', source: { kind: 'live_ha_model', label: 'Live Home Assistant model', live: true }, revision: 7, trackerStatus: 'live' } },
  victoria: { homeName: 'Victoria', hasVirtualFixtures: true, hasPerception: true, fixtureCount: 4,
    about: { homeId: 'victoria', source: { kind: 'equipment_registration', label: 'Authored equipment', live: false }, trackerStatus: 'connecting',
      hasVirtualFixtures: true, fixtureCount: 4, hasPerception: true } },
};

function wrapper(theme) {
  return `(()=>{
    const h=React.createElement,proof=window.__roomGuideProof={visibleCalls:[],home:'echo'};
    const homes=${JSON.stringify(homes)};
    function RoomGuideProof(){
      const [home,setHome]=React.useState('echo'),[visible,setVisible]=React.useState(true),[selected,setSelected]=React.useState(false);
      proof.setHome=value=>ReactDOM.flushSync(()=>setHome(value));proof.select=value=>ReactDOM.flushSync(()=>setSelected(value));
      proof.visible=visible;
      const facts=homes[home];
      return h('section',{className:'apt-home-shell','data-theme':${JSON.stringify(theme)},style:{position:'relative',width:'100%',height:'100%',background:'var(--apt-surface)'}},
        h('div',{className:'apt-home-stage',style:{position:'absolute',inset:0}},
          h(AptUnifiedRoomToolbar,{mode:'points',sourceLabel:'Authored source',sourceDetail:'Authored component proof',view:0,disabled:false,
            onMode:()=>{},onOrbit:()=>{},onFit:()=>{},hasVirtualFixtures:facts.hasVirtualFixtures,virtualVisible:visible,
            onVirtualVisible:value=>{proof.visibleCalls.push(value);setVisible(value)},homeId:home,homeName:facts.homeName,
            fixtureCount:facts.fixtureCount,fixtureSelected:selected,hasPerception:facts.hasPerception,about:facts.about})));
    }
    const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(h(RoomGuideProof)));
    proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
  })()`;
}

test('room guide: help equals the key map, layers explain unavailable rows, legend and About per home', { timeout: 120000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-room-guide-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-room-guide-browser-1', actual_toolbar: true, actual_guide: true, authored_facts: true,
    complete_home_app: false, renderer: false, room_assets: false, household_data: false, physical_commands: 0, model_saves: 0,
    camera_sessions: 0, model_sessions: 0, requests: [], websocket_attempts: [], errors: [], checks: [], screenshots: [],
    sources: Object.fromEntries(['home-apartment.jsx', 'home-apartment-room-guide.jsx', 'home-apartment-ui.jsx', 'home-overlay.js', 'home-tokens.css']
      .map(name => [name, sha(read(name))])) };
  let browser;
  try {
    const installed = fs.existsSync(chromium.executablePath());
    browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
      ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    for (const scenario of [{ theme: 'dark', width: 1366, height: 820, coarse: false }, { theme: 'light', width: 1366, height: 820, coarse: false },
      { theme: 'dark', width: 390, height: 844, coarse: true }]) {
      const label = `${scenario.theme}-${scenario.width}`;
      const context = await browser.newContext({ viewport: { width: scenario.width, height: scenario.height }, hasTouch: scenario.coarse,
        isMobile: scenario.coarse, serviceWorkers: 'block' });
      try {
        await context.route('**/*', route => { receipt.requests.push(route.request().url()); return route.abort(); });
        await context.routeWebSocket('**/*', socket => { receipt.websocket_attempts.push(socket.url()); socket.close(); });
        const page = await context.newPage(); page.setDefaultTimeout(5000);
        page.on('pageerror', error => receipt.errors.push(error.message));
        await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden}</style><div id="root"></div>');
        await page.addStyleTag({ content: css });
        for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js', 'home-overlay.js'])
          await page.addScriptTag({ content: read(name) });
        for (const code of [uiSource, guideSource]) await page.addScriptTag({ content: babel.transform(code, { presets: ['react'] }).code });
        await page.addScriptTag({ content: 'const {useState,useRef,useCallback}=React;\n'
          + babel.transform(declaration('AptUnifiedIcon') + '\n' + declaration('AptUnifiedRoomToolbar'), { presets: ['react'] }).code });
        await page.addScriptTag({ content: babel.transform(wrapper(scenario.theme), { presets: ['react'] }).code });
        const check = (name, detail = true) => receipt.checks.push({ scenario: label, name, detail });
        const shot = async name => { const file = `${label}-${name}.png`; await page.screenshot({ path: path.join(run, file) }); receipt.screenshots.push(file); };
        const dialog = name => page.getByRole('dialog', { name, exact: true });
        const inViewport = async locator => {
          const box = await locator.boundingBox();
          assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= scenario.width + .5 && box.y + box.height <= scenario.height + .5, 'inside the viewport');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal page scroll');
        };

        for (const home of ['echo', 'victoria']) {
          await page.evaluate(value => window.__roomGuideProof.setHome(value), home);
          const expected = await page.evaluate(value => ({
            keys: window.HomeApartmentRoomGuide.helpRows(value).keys.map(row => [row.id, row.label]),
            gestures: window.HomeApartmentRoomGuide.helpRows(value).gestures.map(row => row.id),
            marks: window.HomeApartmentRoomGuide.legendItems({ homeId: value, selected: true, hasPerception: value === 'victoria' }).map(item => item.id),
          }), home);

          // "?" from the room opens help; its key rows are the key map for this home.
          await page.locator('body').click({ position: { x: 5, y: 5 } });
          await page.keyboard.press('?');
          const help = dialog('Gestures and keys');
          await help.waitFor();
          const rows = await help.locator('[data-apt-help-key]').evaluateAll(nodes => nodes.map(node => [node.dataset.aptHelpKey, node.textContent.trim()]));
          assert.deepEqual(rows, expected.keys, `${home}: help key rows equal the key map`);
          assert.deepEqual(await help.locator('[data-apt-help-gesture]').evaluateAll(nodes => nodes.map(node => node.dataset.aptHelpGesture)), expected.gestures);
          assert.deepEqual(await help.locator('[data-apt-help-mark]').evaluateAll(nodes => nodes.map(node => node.dataset.aptHelpMark)), expected.marks);
          const helpText = await help.textContent();
          assert.match(helpText, /pinch/i, `${home}: help names the bound pinch zoom`);
          assert.match(helpText, /Shift-drag or right-drag\s*Pan/i, `${home}: help names the bound Shift/right-drag pan`);
          await page.waitForFunction(() => document.querySelector('[role="dialog"][aria-modal="true"]')?.contains(document.activeElement));
          await inViewport(help); await shot(`${home}-help`);
          await page.keyboard.press('Escape');
          await help.waitFor({ state: 'detached' });
          check(`${home}: ? opens help with exactly the key map rows`, rows.length);

          // Layers: every unavailable row is disabled and says why.
          await page.getByRole('button', { name: 'Layers', exact: true }).click();
          const layers = dialog('Room layers');
          const states = await layers.locator('[data-apt-layer]').evaluateAll(nodes => nodes.map(node => ({
            id: node.dataset.aptLayer, available: node.dataset.available === '1',
            controlsDisabled: [...node.querySelectorAll('input,button')].every(control => control.disabled),
            reason: node.querySelector(':scope > .apt-ui-meta')?.textContent.trim() || '',
            described: [...node.querySelectorAll('[aria-describedby]')].every(control => document.getElementById(control.getAttribute('aria-describedby'))?.textContent.trim()),
          })));
          for (const state of states.filter(row => !row.available)) {
            assert.equal(state.controlsDisabled, true, `${home}/${state.id} is disabled`);
            assert.ok(state.reason.length > 3 && state.described, `${home}/${state.id} explains why: ${state.reason}`);
          }
          const available = states.filter(row => row.available).map(row => row.id);
          assert.deepEqual(available, scenario.width >= 1180 ? (home === 'victoria' ? ['legend', 'fixtures'] : ['legend']) : (home === 'victoria' ? ['fixtures'] : []));
          for (const id of ['pools', 'beams', 'named-aims', 'labels', 'cameras', 'beam-blocking', 'furniture-shadows', 'body-shadows'])
            assert.ok(states.some(row => row.id === id), `${home}: ${id} row listed`);
          if (home === 'victoria') {
            const box = page.getByRole('checkbox', { name: 'Show virtual spotlights layer', exact: true });
            await box.setChecked(false);
            await box.setChecked(true);
            assert.deepEqual(await page.evaluate(() => window.__roomGuideProof.visibleCalls), [false, true], 'Layers calls the owner fixture setter');
            assert.equal(await box.isChecked(), true);
          }
          await inViewport(layers); await shot(`${home}-layers`);
          check(`${home}: layers rows`, states.map(row => `${row.id}:${row.available ? 'on' : row.reason}`));

          // Legend on demand, wide windows only; hidden while Layers is open.
          if (scenario.width >= 1180) {
            const legendBox = layers.locator('[data-apt-layer="legend"] input');
            if (!(await legendBox.isChecked())) await legendBox.check();
            assert.equal(await page.locator('[aria-label="Room legend"]').count(), 0, 'legend never sits under an open popover');
            await layers.getByRole('button', { name: 'Close layers', exact: true }).click();
            await page.evaluate(() => window.__roomGuideProof.select(true));
            const legend = page.getByRole('group', { name: 'Room legend', exact: true });
            await legend.waitFor();
            const marks = await legend.locator('[data-apt-legend]').evaluateAll(nodes => nodes.map(node => node.dataset.aptLegend));
            assert.deepEqual(marks, expected.marks, `${home}: legend items match marks`);
            const [legendRect, toolbarRect] = await Promise.all([legend.boundingBox(), page.getByRole('toolbar', { name: 'Room view' }).boundingBox()]);
            assert.ok(legendRect.y + legendRect.height < toolbarRect.y, 'legend clears the toolbar');
            await shot(`${home}-legend`);
            await page.evaluate(() => window.__roomGuideProof.select(false));
            check(`${home}: legend`, marks);
          } else {
            await layers.getByRole('button', { name: 'Close layers', exact: true }).click();
            assert.equal(await page.locator('[aria-label="Room legend"]').count(), 0);
          }

          // About from the help footer, then from Scene source.
          await page.getByRole('button', { name: 'Gestures and keys', exact: true }).click();
          await dialog('Gestures and keys').getByRole('button', { name: 'About this home', exact: true }).click();
          const about = dialog(`What's real in ${homes[home].homeName}`);
          await about.waitFor();
          const groups = await about.locator('[data-apt-about-group]').evaluateAll(nodes => nodes.map(node => node.dataset.aptAboutGroup));
          const text = await about.textContent();
          if (home === 'echo') {
            assert.deepEqual(groups, ['live', 'recorded']);
            assert.match(text, /Live Home Assistant model · rev 7/);
          } else {
            assert.deepEqual(groups, ['modeled', 'authored', 'recorded', 'unavailable']);
            assert.match(text, /4 modeled fixtures · not commissioned/);
            assert.match(text, /Den camera results · viewing only/);
            assert.match(text, /not measured illumination/);
          }
          await inViewport(about); await shot(`${home}-about`);
          await about.getByRole('button', { name: 'Close About', exact: true }).click();
          if (scenario.width >= 1180) {
            await page.getByRole('button', { name: 'Authored source' }).click();
            await page.getByRole('button', { name: "What's real here", exact: true }).click();
            await about.waitFor(); await page.keyboard.press('Escape'); await about.waitFor({ state: 'detached' });
          }
          check(`${home}: about`, groups);
        }
        // Layer choices are kept when switching homes within the session.
        if (scenario.width >= 1180) {
          await page.evaluate(() => window.__roomGuideProof.setHome('echo'));
          await page.getByRole('button', { name: 'Layers', exact: true }).click();
          assert.equal(await dialog('Room layers').locator('[data-apt-layer="legend"] input').isChecked(), true, 'legend choice survives a home switch');
          await dialog('Room layers').getByRole('button', { name: 'Close layers', exact: true }).click();
          check('legend choice survives a home switch');
        }
        await page.evaluate(() => window.__roomGuideProof.unmount());
      } finally { await context.close(); }
    }
    assert.deepEqual(receipt.requests, []); assert.deepEqual(receipt.websocket_attempts, []); assert.deepEqual(receipt.errors, []);
    receipt.passed = true;
  } catch (error) { receipt.passed = false; receipt.failure = error.stack || String(error); throw error; }
  finally {
    await browser?.close(); fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), passed: receipt.passed, checks: receipt.checks.length, screenshots: receipt.screenshots.length }));
  }
});
