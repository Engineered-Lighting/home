// Chat bound to the room in a real browser: the actual chip strip, About chip
// and unified header from home-app.jsx with the actual room-context module,
// authored rooms and a fake room bridge. Every request and socket is refused
// and recorded; no Home service graph, room asset, model or light is involved.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const source = read('home-app.jsx');
const ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
const declaration = name => {
  const found = ast.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
  assert.equal(found.length, 1, 'one actual declaration for ' + name); return source.slice(found[0].start, found[0].end);
};
const components = ['ConnectionDot', 'HomeHeader', 'ApartmentChatRoomRefs', 'ApartmentChatContextChip'].map(declaration).join('\n');

const harness = `(() => {
  const h = React.createElement, SCHEMA = 'home-apartment-chat-room-1';
  const proof = window.__chipProof = { calls: [], removed: 0 };
  const rooms = {
    echo: { siteId: 'echo', key: 'echo:authored-1', lights: [{ id: 'kitchen', label: 'Kitchen light', entityId: 'light.kitchen' }, { id: 'desk', label: 'Desk lamp', entityId: 'light.desk_lamp' }],
      namedAims: [{ id: 'painting', label: 'Painting' }], selection: { kind: 'light', id: 'kitchen' } },
    victoria: { siteId: 'victoria', key: 'victoria:authored-1', lights: [{ id: 'vic-s4', label: 'Socket 4' }, { id: 'vic-s2', label: 'Socket 2' }],
      namedAims: [], selection: { kind: 'light', id: 'vic-s4' } },
  };
  const strip = ref => ref && { kind: ref.kind, id: ref.id, siteId: ref.siteId };
  const bridgeFor = room => ({ schema: SCHEMA, ...room,
    hover: ref => { proof.calls.push(['hover', strip(ref)]); return room.siteId === 'echo'; },
    select: ref => { proof.calls.push(['select', strip(ref)]); return true; },
    adjust: ref => { proof.calls.push(['adjust', strip(ref)]); return true; },
    readModeledLight: () => { proof.calls.push(['read']); return null; },
    applyModeledPatch: () => { proof.calls.push(['apply']); return null; } });
  const bindings = new Map();
  let setState;
  function Proof() {
    const [state, set] = React.useState({ site: 'echo', key: null, groups: [{ speaker: 'you', events: [{ id: 'u1', kind: 'user', text: 'Authored question', about: 'Kitchen light' }] },
      { speaker: 'home', events: [
        { id: 'r1', kind: 'home', text: 'I turned on Kitchen light and aimed it at the Painting.' },
        { id: 'a1', kind: 'action', title: 'Kitchen light on', service: 'light.turn_on', target: 'light.kitchen', status: 'success' },
        { id: 'r2', kind: 'home', text: 'Partial answer', stopped: true },
        { id: 'r3', kind: 'home', text: 'Kitchen light is still streaming', streaming: true }] }] });
    setState = set;
    const base = rooms[state.site], bridge = bridgeFor(state.key ? { ...base, key: state.key } : base);
    const room = window.HomeApartmentChatContext.normalizeRoom(bridge);
    const about = room.selection;
    return h('section', { className: 'apt-home-shell', 'data-theme': window.__theme, style: { height: '100%', width: '100%', background: 'var(--apt-surface)' } },
      h(HomeHeader, { theme: window.__theme, mobile: window.__mobile, unified: true, voice: { state: 'inactive' }, connection: 'online',
        onExplainLastAnswer: () => {}, onCopyTranscript: () => {}, onNewConversation: () => {}, conversationCopyState: '', conversationCopyDisabledReason: '',
        chatSourceLine: 'authored-model · ' + (state.site === 'victoria' ? 'Victoria · modeled room' : 'Los Angeles · Home Assistant') }),
      h('div', { className: 'apt-ui-chat-feed', role: 'log' }, state.groups.map((group, i) => h('div', { key: i, 'data-group': i },
        h(ApartmentChatRoomRefs, { group, room, bridge, bindings })))),
      about && h(ApartmentChatContextChip, { label: about.label, onRemove: () => { proof.removed++; } }));
  }
  proof.visit = (site, key = null) => ReactDOM.flushSync(() => setState(state => ({ ...state, site, key })));
  proof.reply = event => ReactDOM.flushSync(() => setState(state => ({ ...state, groups: [...state.groups, { speaker: 'home', events: [event] }] })));
  const root = ReactDOM.createRoot(document.getElementById('root'));
  ReactDOM.flushSync(() => root.render(h(Proof)));
})();`;

const scenarios = [
  { theme: 'dark', width: 1366, height: 768, coarse: false },
  { theme: 'light', width: 390, height: 844, coarse: true },
];

test('chips and card buttons point into the current home only and send nothing', { timeout: 90000 }, async () => {
  const receipt = { requests: [], websockets: [], errors: [], checks: [] };
  const installed = fs.existsSync(chromium.executablePath());
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--renderer-process-limit=2'],
    ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
  try {
    for (const scenario of scenarios) {
      const label = `${scenario.theme}-${scenario.width}`;
      const context = await browser.newContext({ viewport: { width: scenario.width, height: scenario.height },
        hasTouch: scenario.coarse, isMobile: scenario.coarse, serviceWorkers: 'block' });
      try {
        await context.route('**/*', route => { receipt.requests.push(route.request().url()); return route.abort(); });
        await context.routeWebSocket('**/*', socket => { receipt.websockets.push(socket.url()); socket.close(); });
        const page = await context.newPage(); page.setDefaultTimeout(5000);
        page.on('pageerror', error => receipt.errors.push(error.message));
        await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;width:100%;height:100%}#root{width:100%;height:100%}</style><div id="root"></div>');
        await page.addStyleTag({ content: read('home-tokens.css').replace(/@font-face\s*\{[^}]*\}/g, '') });
        for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js', 'home-overlay.js', 'home-apartment-chat-context.js']) {
          await page.addScriptTag({ content: read(name) });
        }
        await page.addScriptTag({ content: babel.transform(read('home-chat-header-actions.jsx'), { presets: ['react'], sourceType: 'script' }).code });
        await page.addScriptTag({ content: `window.__theme=${JSON.stringify(scenario.theme)};window.__mobile=${scenario.width < 900};`
          + 'const {useState,useEffect,useRef,useCallback}=React;\n' + babel.transform(components + '\n' + harness, { presets: ['react'] }).code });
        const calls = () => page.evaluate(() => window.__chipProof.calls.splice(0));
        const chip = name => page.locator('.apt-ui-chat-chip', { hasText: name });
        const check = async (name, body) => { await body(); receipt.checks.push(label + ': ' + name); };

        await check('header names the model and per-home source', async () => {
          assert.equal(await page.locator('.apt-ui-chat-model').textContent(), 'authored-model · Los Angeles · Home Assistant');
        });
        await check('a completed reply names its lights and named aims as chips; streaming text has none yet', async () => {
          assert.deepEqual(await page.locator('[data-group="1"] .apt-ui-chat-chip').allTextContents(), ['Kitchen light', 'Painting']);
          assert.equal(await chip('Kitchen light').getAttribute('data-available'), 'true');
          assert.equal(await chip('Painting').getAttribute('aria-disabled'), 'true');
          assert.match(await chip('Painting').getAttribute('aria-description'), /named-aim inspector isn't available yet/);
          assert.equal(await page.locator('.apt-ui-chat-feed .apt-ui-chat-about', { hasText: 'About Kitchen light' }).count(), 1, 'the sent message shows its About');
          assert.equal(await page.getByText('Reply cut short').count(), 1);
          const box = await chip('Kitchen light').boundingBox();
          if (scenario.coarse) assert.ok(box.height >= 44, 'coarse chips are touch-sized');
        });
        await check('hover highlights and click selects through the bridge only', async () => {
          await calls();
          if (scenario.coarse) await chip('Kitchen light').focus(); else await chip('Kitchen light').hover();
          if (scenario.coarse) await chip('Kitchen light').blur(); else await page.mouse.move(1, 1);
          await chip('Kitchen light').click();
          const seen = await calls();
          assert.deepEqual(seen.slice(0, 2), [['hover', { kind: 'light', id: 'kitchen', siteId: 'echo' }], ['hover', null]]);
          assert.deepEqual(seen.filter(call => call[0] === 'select'), [['select', { kind: 'light', id: 'kitchen', siteId: 'echo' }]]);
          await chip('Painting').click({ force: true });
          assert.equal((await calls()).filter(call => call[0] === 'select').length, 0, 'a named-aim chip never selects a light');
        });
        await check('Show highlights briefly and Adjust selects; neither sends', async () => {
          await page.locator('[data-chat-card="show"]').click();
          assert.deepEqual((await calls()).filter(call => call[0] === 'hover' && call[1]), [['hover', { kind: 'light', id: 'kitchen', siteId: 'echo' }]]);
          await page.waitForTimeout(2700);
          assert.deepEqual((await calls()).filter(call => call[0] === 'hover'), [['hover', null]]);
          await page.locator('[data-chat-card="adjust"]').click();
          assert.deepEqual(await calls(), [['adjust', { kind: 'light', id: 'kitchen', siteId: 'echo' }]]);
        });
        await check('in Victoria, a Los Angeles reply reads unavailable and acts on nothing', async () => {
          await page.evaluate(() => window.__chipProof.visit('victoria'));
          assert.equal(await page.locator('.apt-ui-chat-model').textContent(), 'authored-model · Victoria · modeled room');
          assert.equal(await chip('Kitchen light').getAttribute('data-available'), 'false');
          assert.match(await chip('Kitchen light').getAttribute('aria-description'), /about Los Angeles; you're viewing Victoria/);
          await chip('Kitchen light').click({ force: true });
          if (!scenario.coarse) await chip('Kitchen light').hover({ force: true });
          assert.equal(await page.locator('[data-chat-card="adjust"]').isDisabled(), true);
          assert.equal(await page.locator('[data-chat-card="show"]').isDisabled(), true);
          assert.deepEqual((await calls()).filter(call => call[0] !== 'hover' || call[1] !== null), [], 'nothing is hovered or selected in Victoria');
        });
        await check('a Victoria reply selects the Den light; a changed room makes it stale', async () => {
          await page.evaluate(() => window.__chipProof.reply({ id: 'v1', kind: 'home', text: 'Socket 4 is lighting the desk at 55%.' }));
          await chip('Socket 4').click();
          assert.deepEqual((await calls()).filter(call => call[0] === 'select'), [['select', { kind: 'light', id: 'vic-s4', siteId: 'victoria' }]]);
          await page.evaluate(() => window.__chipProof.visit('victoria', 'victoria:authored-2'));
          assert.match(await chip('Socket 4').getAttribute('aria-description'), /room changed/);
          await chip('Socket 4').click({ force: true });
          assert.equal((await calls()).filter(call => call[0] === 'select').length, 0);
        });
        await check('the About chip names the selection and removes on request', async () => {
          assert.equal(await page.locator('[data-chat-context="about"] > span').textContent(), 'About Socket 4');
          await page.getByRole('button', { name: "Don't include Socket 4" }).click();
          assert.equal(await page.evaluate(() => window.__chipProof.removed), 1);
          assert.deepEqual((await calls()).filter(call => ['read', 'apply'].includes(call[0])), [], 'chips never read or change a light');
        });
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
  assert.deepEqual(receipt.errors, []);
  assert.deepEqual(receipt.requests, [], 'zero network requests');
  assert.deepEqual(receipt.websockets, [], 'zero WebSocket attempts');
  assert.equal(receipt.checks.length, scenarios.length * 7);
});
