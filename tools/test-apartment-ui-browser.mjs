// Authored component-only proof using the vendored React and real shared UI.
// No room, Home controller, HTTP server, service, household data or WS session.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const uiSource = read('home-apartment-ui.jsx'), cssSource = read('home-tokens.css');
const compiled = babel.transform(uiSource, { presets: ['react'], sourceType: 'script' }).code;
const css = cssSource.replace(/url\("(vendor\/fonts\/[a-z-]+\.woff2)"\)/g,
  (_, name) => `url("data:font/woff2;base64,${fs.readFileSync(path.join(SRC, name)).toString('base64')}")`);
const states = ['connected', 'current', 'saved', 'applied', 'reported', 'live', 'proposed', 'draft',
  'recorded', 'replay', 'authored', 'simulated', 'held', 'stale', 'unsaved', 'changed', 'disconnected',
  'blocked', 'conflict', 'invalid', 'unavailable', 'no-session', 'modeled', 'loading'];

// Extract only the actual mounted adapter callbacks, without initializing Home.
const apartmentSource = read('home-apartment.jsx');
const ast = babel.transform(apartmentSource, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast;
const callbacks = {};
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'VariableDeclarator' && ['showToast', 'dismissToast'].includes(node.id?.name)) {
    const callback = node.init.arguments[0];
    callbacks[node.id.name] = apartmentSource.slice(callback.start, callback.end);
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === 'object') visit(value);
  }
}
visit(ast.program);
assert.ok(callbacks.showToast && callbacks.dismissToast, 'the mounted source callbacks are available');
const aimSource = read('home-apartment-aim.jsx');
const aimAst = babel.transform(aimSource, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast;
const statusSource = aimAst.program.body.filter(node => node.type === 'FunctionDeclaration' && ['AimStatus', 'ProfileStatus'].includes(node.id?.name))
  .map(node => aimSource.slice(node.start, node.end)).join('\n');
assert.ok(statusSource.includes('function AimStatus') && statusSource.includes('function ProfileStatus'));
const compiledStatus = babel.transform('const AIM_MONO="monospace", AIM_SANS="sans-serif";\n' + statusSource, { presets: ['react'] }).code;
for (const [name, content] of [['index.html', read('index.html')], ['home-feature-loader.js', read('home-feature-loader.js')]]) {
  const uiIndex = content.indexOf('home-apartment-ui.jsx');
  assert.ok(uiIndex >= 0 && uiIndex < content.indexOf('home-apartment-cards.jsx') && uiIndex < content.indexOf('home-apartment-aim.jsx')
    && uiIndex < content.indexOf('home-apartment.jsx'), name + ' loads the shared UI before all consumers');
}
assert.match(read('index.html'), /'home-apartment-ui\.jsx': true/, 'new shared UI remains a deferred feature member');

const harness = `
window.fixture = { calls: 0, current: true, lastAction: null };
const e = React.createElement;
function Gallery() {
  return e(React.Fragment, null, ...[false, true].map(scoped => e('section', {key:String(scoped)},
    e('h2', null, scoped ? 'Unified shell consumers' : 'Classic inspector consumers'),
    ...[0,1,2,3].map(surface => e('div', {key:surface,className:'surface '+(scoped?'apt-home-shell':''),
      style:{background:'var(--hg-bg-'+surface+')',height:'auto',containerType:'normal'},'data-surface':surface},
      e('h3',null,'Surface '+surface),e('div',{className:'badges'},...${JSON.stringify(states)}.map(state =>
        e(HomeApartmentUI.Badge,{key:state,state}))))))));
}
function ToastFixture() {
  const [toast,setToast] = React.useState(null);
  const showToast = React.useCallback(${callbacks.showToast},[]);
  const dismissToast = React.useCallback(${callbacks.dismissToast},[]);
  React.useLayoutEffect(() => {
    fixture.show = (text,opts) => ReactDOM.flushSync(() => showToast(text,opts));
    fixture.hide = () => ReactDOM.flushSync(() => setToast(null));
    fixture.present = () => toast;
    fixture.direct = value => ReactDOM.flushSync(() => setToast(value));
  }, [toast,showToast]);
  return e(HomeApartmentUI.Toast,{toast,onDismiss:dismissToast});
}
ReactDOM.flushSync(() => {
  ReactDOM.createRoot(document.getElementById('matrix')).render(e(Gallery));
  ReactDOM.createRoot(document.getElementById('source-status')).render(e(React.Fragment,null,
    ...['verified','calibrated','measured','accepted'].map(status=>e('div',{'data-source-status':status,key:status},e(AimStatus,{label:'Source evidence',status}))),
    e('div',{'data-source-profile':'preview'},e(ProfileStatus,{label:'Source profile',detail:'preview only',tone:'preview'}))));
  ReactDOM.createRoot(document.getElementById('toast-root')).render(e(ToastFixture));
});`;

function contrast(fg, bg) {
  const luminance = text => {
    const channels = text.match(/[\d.]+/g).slice(0, 3).map(Number).map(c => {
      const value = c / 255;
      return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
    });
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
  };
  const a = luminance(fg), b = luminance(bg);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}

test('authored shared UI keeps status contrast and safe native toast actions in both themes', { timeout: 120000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-ui-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const screenshotDirectory = path.join(ROOT, 'docs/design/apartment-unified/evidence-wave1-foundations');
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const receipt = { schema: 'home-apartment-ui-authored-browser-1', authored_components_only: true, room: false,
    household_data: false, model_session: false, physical_commands: 0, http_requests: [], web_sockets: [], errors: [],
    hashes: Object.fromEntries(['home-apartment-ui.jsx', 'home-apartment.jsx', 'home-tokens.css', 'index.html', 'home-feature-loader.js']
      .map(name => [name, sha(read(name))])), matrices: [], screenshots: [], checks: [] };
  const installed = fs.existsSync(chromium.executablePath());
  const browser = await chromium.launch({ headless: true,
    args: ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
    ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
  let page;
  try {
    for (const coarse of [false, true]) {
      for (const theme of ['dark', 'light']) {
      const context = await browser.newContext({ viewport: coarse ? { width: 390, height: 844 } : { width: 1160, height: 900 },
        hasTouch: coarse, isMobile: coarse, offline: true });
      await context.route('**/*', route => { receipt.http_requests.push(route.request().url()); return route.abort(); });
      await context.routeWebSocket('**/*', socket => { receipt.web_sockets.push(socket.url()); socket.close(); });
      page = await context.newPage();
      page.setDefaultTimeout(5000);
      page.on('pageerror', error => receipt.errors.push(error.message));
      await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>
        <style>body{height:auto;min-height:100vh}main{max-width:1120px;margin:auto;padding:16px;box-sizing:border-box}
        h1{font-size:20px}h2{font-size:16px}h3{font:500 12px Geist;margin:0 0 10px}.surface{padding:12px;margin:8px 0;border:1px solid var(--hg-border);box-sizing:border-box}
        .badges{display:flex;flex-wrap:wrap;gap:8px}.authored-label{font-size:13px;color:var(--hg-fg-3)}
        #toast-stage{position:relative;height:220px;border:1px solid var(--hg-border);margin-top:20px}#focus-start{min-height:44px}</style>
        <main><h1>Authored component verification</h1><p class="authored-label">Shared status and notice fixtures. No room, camera, service or device data.</p>
        <div id="matrix"></div><h2>Source evidence labels retain their meaning</h2><div id="source-status"></div>
        <button id="focus-start">Keyboard test start</button><div id="toast-stage"><div id="toast-root"></div></div></main>`);
      await page.addScriptTag({ content: read('vendor/react-18.3.1/react.production.min.js') });
      await page.addScriptTag({ content: read('vendor/react-18.3.1/react-dom.production.min.js') });
      await page.addScriptTag({ content: compiled });
      await page.addScriptTag({ content: compiledStatus });
      await page.addScriptTag({ content: harness });
      await page.evaluate(() => document.fonts.ready);
      assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), coarse);
        await page.evaluate(value => document.documentElement.dataset.theme = value, theme);
        const samples = await page.locator('.surface .apt-ui-badge').evaluateAll(elements => elements.map(element => ({
          state: element.dataset.state, tone: element.dataset.tone, foreground: getComputedStyle(element).color,
          background: getComputedStyle(element.closest('.surface')).backgroundColor,
          fontSize: parseFloat(getComputedStyle(element).fontSize), surface: element.closest('.surface').dataset.surface,
          scoped: element.closest('.surface').classList.contains('apt-home-shell'), text: element.textContent,
          shape: element.querySelector('[aria-hidden="true"]').dataset.shape,
          borderStyle: getComputedStyle(element).borderTopStyle,
        })));
        assert.equal(samples.length, states.length * 8);
        for (const sample of samples) {
          sample.ratio = contrast(sample.foreground, sample.background);
          assert.ok(sample.ratio >= 4.5, `${theme}/${sample.scoped ? 'shell' : 'global'}/${sample.surface}/${sample.state}: ${sample.ratio.toFixed(3)}:1`);
          assert.ok(sample.fontSize >= 11.5, `informative ${sample.state} font must be at least 11.5px`);
          assert.ok(sample.text.trim(), 'every state has a textual signal');
          if (sample.scoped && ['ok', 'proposed', 'attn', 'blocked'].includes(sample.tone)) {
            const global = samples.find(value => !value.scoped && value.state === sample.state && value.surface === sample.surface);
            assert.equal(sample.foreground, global.foreground, `shell must preserve the shared ${sample.tone} semantic color for ${sample.state}`);
          }
          if (['authored', 'simulated'].includes(sample.state)) assert.equal(sample.borderStyle, 'dashed', 'authored evidence retains its dashed badge');
        }
        for (const status of ['verified', 'calibrated', 'measured', 'accepted']) {
          const badge = page.locator(`[data-source-status="${status}"] .apt-ui-badge`);
          assert.equal(await badge.getAttribute('data-state'), 'recorded');
          assert.equal(await badge.textContent(), status, 'source evidence is not promoted to current or reported');
        }
        assert.equal(await page.locator('[data-source-profile="preview"] .apt-ui-badge').getAttribute('data-state'), 'modeled');
        receipt.matrices.push({ theme, coarse, sample_count: samples.length, minimum_contrast: Math.min(...samples.map(sample => sample.ratio)), samples });
        await page.evaluate(() => fixture.show('Authored notice: local preview changed', {
          sticky: true, action: () => fixture.calls++, isCurrent: () => fixture.current,
        }));
        await page.evaluate(() => { fixture.lastAction = fixture.present().action; });
        const dimensions = await page.locator('.apt-ui-toast button').evaluateAll(buttons => buttons.map(button => ({
          tag: button.tagName, width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height,
          minHeight: getComputedStyle(button).minHeight, coarse: matchMedia('(pointer: coarse)').matches,
        })));
        for (const value of dimensions) {
          assert.equal(value.tag, 'BUTTON');
          if (coarse) assert.ok(value.width >= 44 && value.height >= 44, 'coarse toast targets must be 44px: ' + JSON.stringify(value));
        }
        await page.locator('#focus-start').focus();
        await page.keyboard.press('Tab');
        const focus = await page.locator('.apt-ui-toast-action').evaluate(element => {
          const style = getComputedStyle(element), probe = document.createElement('span');
          probe.style.color = style.getPropertyValue('--hg-blue');
          document.body.append(probe);
          const expectedColor = getComputedStyle(probe).color;
          probe.remove();
          return { active: element === document.activeElement, style: style.outlineStyle,
            width: parseFloat(style.outlineWidth), color: style.outlineColor, expectedColor };
        });
        assert.equal(focus.active, true); assert.equal(focus.style, 'solid');
        assert.equal(focus.width, 1, 'toast uses the shared 1px keyboard focus ring');
        assert.equal(focus.color, focus.expectedColor, 'toast uses the shared blue keyboard focus ring');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'gallery and toast stay in viewport');
        await page.locator('#toast-stage').scrollIntoViewIfNeeded();
        const screenshot = `${theme}-${coarse ? 'phone-coarse' : 'desktop'}-authored-components.png`;
        await page.screenshot({ path: path.join(screenshotDirectory, screenshot), fullPage: true, timeout: 10000 });
        receipt.screenshots.push({ file: screenshot, authored_components_only: true, room: false });
        const before = await page.evaluate(() => fixture.calls);
        await page.keyboard.press('Enter');
        await page.waitForFunction(() => !document.querySelector('.apt-ui-toast'));
        assert.equal(await page.evaluate(() => fixture.calls), before + 1, 'native Enter runs the guarded action exactly once');
        assert.equal(await page.evaluate(() => fixture.lastAction.run()), false, 'consumed action cannot run from a retained handle');

      await page.evaluate(() => fixture.show('Unqualified action', { sticky: true, action: () => fixture.calls++ }));
      assert.equal(await page.locator('.apt-ui-toast-action').count(), 0, 'parent refuses callbacks missing currentness');
      assert.equal(await page.evaluate(() => HomeApartmentUI.createToastAction(() => fixture.calls++).run()), false, 'leaf also refuses missing guard');
      receipt.phase = `${coarse}:stale-action`;
      await page.evaluate(() => {
        fixture.current = true;
        fixture.show('Stale source action', { sticky: true, action: () => fixture.calls++, isCurrent: () => fixture.current });
        fixture.current = false;
      });
      const beforeStale = await page.evaluate(() => fixture.calls);
      await page.locator('.apt-ui-toast-action').click();
      assert.equal(await page.evaluate(() => fixture.calls), beforeStale);
      await page.waitForFunction(() => !document.querySelector('.apt-ui-toast'));
      await page.locator('#focus-start').focus();
      await page.mouse.move(0, 0);

      receipt.phase = `${coarse}:expiry`;
      await page.evaluate(() => {
        fixture.current = true;
        fixture.show('Expiring action', { timeoutMs: 120, action: () => fixture.calls++, isCurrent: () => true });
        fixture.lastAction = fixture.present().action;
      });
      await page.waitForFunction(() => !document.querySelector('.apt-ui-toast'));
      assert.equal(await page.evaluate(() => fixture.lastAction.run()), false, 'expiry consumes retained action handle');

      receipt.phase = `${coarse}:replacement`;
      await page.evaluate(() => {
        fixture.show('Replaced action', { sticky: true, action: () => fixture.calls++, isCurrent: () => true });
        fixture.lastAction = fixture.present().action;
        fixture.show('Replacement notice', { sticky: true });
      });
      await page.waitForFunction(() => fixture.lastAction.active === false);
      assert.equal(await page.evaluate(() => fixture.lastAction.run()), false);
      await page.locator('.apt-ui-toast-dismiss').focus();
      await page.keyboard.press('Space');
      await page.waitForFunction(() => !document.querySelector('.apt-ui-toast'));

      receipt.phase = `${coarse}:focus-expiry`;
      await page.evaluate(() => fixture.show('Focus pauses expiry', { timeoutMs: 180, action: () => fixture.calls++, isCurrent: () => true }));
      await page.locator('.apt-ui-toast-action').focus();
      await page.waitForTimeout(260);
      assert.equal(await page.locator('.apt-ui-toast').count(), 1, 'focused action remains available after original timeout');
      await page.locator('#focus-start').focus();
      await page.mouse.move(0, 0);
      await page.waitForFunction(() => !document.querySelector('.apt-ui-toast'));
      receipt.checks.push({ coarse, theme, native_enter: true, native_space_dismiss: true, missing_guard_refused: true,
        stale_guard_refused: true, expiry_cancelled: true, replacement_cancelled: true, focus_pauses_expiry: true });
      await context.close();
      }
    }
    assert.deepEqual(receipt.http_requests, []); assert.deepEqual(receipt.web_sockets, []); assert.deepEqual(receipt.errors, []);
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    console.log('Authored shared UI receipt: ' + path.join(run, 'receipt.json'));
    console.log('Minimum badge contrast: ' + Math.min(...receipt.matrices.map(matrix => matrix.minimum_contrast)).toFixed(3) + ':1');
  } catch (error) {
    receipt.failure = error.stack || error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw error;
  } finally { await browser.close(); }
});
