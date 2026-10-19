// Authored-room audit of the actual unified Home shell (APT-F02, F03, Q02, Q03, S05).
// Text contrast and faint-token text, coarse-pointer targets, surface and type
// fidelity, control motion with and without reduced motion, keyboard focus,
// and the HANDOFF "Phone" example, for both homes in both themes. One page
// load; viewports and the coarse pointer change in place. Read-only: no
// session, camera, household data, physical command or deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { serveAuthoredShell } from './fixtures/apartment-shell-audit-server.mjs';
import { auditText, auditTargets, auditSurfaces, describeFocus } from './fixtures/apartment-shell-audit-probes.mjs';
import { waitForDirectionalReadiness } from './fixtures/apartment-directional-readiness.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'app/src');
const REPORT_ONLY = process.env.HOME_APARTMENT_AUDIT_REPORT === '1';
const STATE_SHOTS = process.env.HOME_APARTMENT_AUDIT_SHOTS === '1';
const PROFILES = [
  { name: 'desktop-1366x768', viewport: { width: 1366, height: 768 }, coarse: false },
  { name: 'phone-390x844', viewport: { width: 390, height: 844 }, coarse: true },
  { name: 'tablet-768x1024', viewport: { width: 768, height: 1024 }, coarse: true },
  { name: 'tablet-820x1180', viewport: { width: 820, height: 1180 }, coarse: true },
];

function launchOptions(platform = process.platform) {
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || platform === 'win32', 'the pinned Playwright Chromium browser must be available on this platform');
  return { headless: true, args, ...(platform === 'linux' ? { channel: 'chromium' } : !installed && platform === 'win32' ? { channel: 'msedge' } : {}) };
}

test('unified shell text, targets, surfaces, motion, focus and phone layout meet the design in both homes and themes', { timeout: 160000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-shell-audit-browser', 'run-' + Date.now());
  fs.mkdirSync(path.join(run, 'evidence'), { recursive: true });
  const receipt = { schema: 'home-apartment-shell-audit-1', authored_room: true, real_household_data: false, live_camera: false,
    model_session_started: false, physical_commands: 0, states: [], known: [], phone_example: null, motion: {}, keyboard: null,
    errors: [], findings: [], phases: [] };
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  let served, browser, page, cdp, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    save(); browser?.close().catch(() => {}); served?.server.closeAllConnections(); reject(new Error('145s shell audit deadline'));
  }, 145000); });
  const findings = receipt.findings;
  const expect = (ok, message, detail) => { if (!ok) findings.push({ message, detail }); };
  const phase = async (name, operation) => {
    const started = Date.now();
    try { return await operation(); } finally { receipt.phases.push({ name, ms: Date.now() - started }); save(); }
  };

  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const waitSite = site => waitForDirectionalReadiness(page, site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready'
    && window.__APT_ENGINE?.siteId === site, site);
  async function selectHome(site) {
    const button = page.getByRole('group', { name: 'Home', exact: true }).getByRole('button', { name: site === 'victoria' ? 'Victoria' : 'Echo Park', exact: true });
    if (await button.getAttribute('aria-pressed') !== 'true') await button.click();
    await waitSite(site); await settle();
  }
  async function setTheme(theme) {
    if (await page.locator('.apt-home-shell').getAttribute('data-theme') === theme) return;
    await page.getByRole('button', { name: 'Home menu', exact: true }).click();
    await page.getByRole('menuitem', { name: theme === 'light' ? 'Light theme' : 'Dark theme', exact: true }).click();
    await waitForDirectionalReadiness(page, theme => document.querySelector('.apt-home-shell')?.getAttribute('data-theme') === theme, theme);
    await settle();
  }
  async function setProfile(profile) {
    await page.setViewportSize(profile.viewport);
    await cdp.send('Emulation.setTouchEmulationEnabled', profile.coarse ? { enabled: true, maxTouchPoints: 5 } : { enabled: false });
    await waitForDirectionalReadiness(page, coarse => matchMedia('(pointer: coarse)').matches === coarse
      && window.__APT_ENGINE?.renderer.domElement.width > 0, profile.coarse);
    await settle();
  }
  async function tab(name) {
    const target = page.locator('.apt-home-tabs').getByRole('tab', { name, exact: true });
    if (await target.isVisible()) { await target.click(); await settle(); return true; }
    return false;
  }

  // Findings another lane owns are recorded, not gated: they stay visible in
  // the receipt and the PR, and the owning lane's own gate closes them.
  const KNOWN = { 'phone-390x844 setup': 'Setup rail lane (APT-U01): the classic editor form renders in the stage under the phone sheet' };
  async function audit(profile, site, theme, surface) {
    const label = [profile.name, site, theme, surface].join(' ');
    const state = { profile: profile.name, site, theme, surface };
    const text = await page.evaluate(auditText);
    state.text_runs = text.length;
    state.min_contrast = Math.min(...text.filter(row => !row.disabled).map(row => row.ratio / row.need * 4.5));
    state.contrast_failures = text.filter(row => !row.disabled && row.ratio < row.need);
    state.fg5_informative = text.filter(row => !row.disabled && row.fg5);
    state.small_text = text.filter(row => !row.disabled && row.size < 11.45);
    expect(text.length >= 5, label + ' audits shell text', text.length);
    expect(!state.contrast_failures.length, 'text contrast ≥ 4.5:1 (3:1 large) · ' + label, state.contrast_failures);
    expect(!state.fg5_informative.length, 'no informative text in --hg-fg-5 · ' + label, state.fg5_informative);
    expect(!state.small_text.length, 'no informative text below 11.5 px · ' + label, state.small_text);
    if (profile.coarse) {
      const targets = await page.evaluate(auditTargets);
      state.targets = targets.length;
      state.small_targets = targets.filter(row => !row.ok);
      expect(targets.length >= 5, label + ' audits shell targets', targets.length);
      const known = KNOWN[profile.name + ' ' + surface];
      if (known && state.small_targets.length) (receipt.known ||= []).push({ owner: known, label, targets: state.small_targets });
      else expect(!state.small_targets.length, 'coarse targets ≥ 44 px · ' + label, state.small_targets);
    }
    state.stage = await page.evaluate(() => getComputedStyle(document.querySelector('.apt-home-stage')).backgroundColor);
    expect(state.stage === 'rgb(0, 0, 0)', 'the room stage stays dark · ' + label, state.stage);
    if (surface === 'room' || surface === 'light') {
      const s = state.surfaces = await page.evaluate(auditSurfaces);
      expect(s.fonts.geist && s.fonts.mono, 'Geist and Geist Mono are loaded', s.fonts);
      expect(s.column.background === s.column.token && s.column.background.endsWith(',255'), 'the column is solid --hg-bg-1 · ' + label, s.column);
      if (!s.narrow) expect(s.column.borderLeft === '1px solid', 'the docked column is separated by a hairline · ' + label, s.column);
      else expect(s.column.radius === '16px' && s.column.borderTop === '1px solid', 'the sheet has a 16 px top radius and a hairline · ' + label, s.column);
      if (s.section) expect(s.section.borderBottomWidth === '1px' && s.section.borderRadius === '0px', 'sections are hairline-separated, not cards · ' + label, s.section);
      if (s.title) expect(s.title.fontSize === '17px' && s.title.fontWeight === '560' && /Geist/.test(s.title.fontFamily), 'subjects are Geist 17/560 · ' + label, s.title);
      if (s.heading) expect(s.heading.fontSize === '13px' && s.heading.fontWeight === '560', 'section heads are 13/560 · ' + label, s.heading);
      if (s.copy) expect(parseFloat(s.copy.fontSize) >= 12.5 && parseFloat(s.copy.fontSize) <= 13.5, 'body is 12.5–13.5 px · ' + label, s.copy);
      if (s.value) expect(/Geist Mono/.test(s.value.fontFamily) && ['11.5px', '12px'].includes(s.value.fontSize), 'values are Geist Mono 11.5–12 · ' + label, s.value);
      if (s.button) expect(s.button.borderTopLeftRadius === '8px', 'controls have 8 px radii · ' + label, s.button);
      if (s.segment) expect(s.segment.borderTopLeftRadius === '10px', 'segmented groups have 10 px radii · ' + label, s.segment);
      if (s.group) expect(s.group.borderTopLeftRadius === '10px', 'cards that own on/off have 10 px radii · ' + label, s.group);
      if (s.badge) expect(!/Mono/.test(s.badge.fontFamily) && s.badge.fontSize === '11.5px', 'badges use the sans UI font at 11.5 px · ' + label, s.badge);
      if (surface === 'light') expect(!!(s.title && s.section && s.button && s.segment && s.group && s.badge), 'the light inspector exposes every audited surface · ' + label, s);
    }
    if (STATE_SHOTS) await page.screenshot({ path: path.join(run, 'evidence', label.replace(/ /g, '-') + '.png') });
    receipt.states.push(state); save();
  }

  async function surfaces(profile, site, theme) {
    await tab('Controls');
    await audit(profile, site, theme, 'room');
    if (site === 'victoria' && (profile.name === 'desktop-1366x768' || profile.name === 'phone-390x844')) {
      const select = page.locator('.apt-home-controls .apt-ui-list-button[aria-label^="Select "]').first();
      await select.scrollIntoViewIfNeeded(); await select.click();
      await page.getByRole('button', { name: 'All lights', exact: true }).waitFor();
      const sections = page.getByRole('tablist', { name: 'Light sections', exact: true });
      await sections.getByRole('tab', { name: 'Light', exact: true }).click(); await settle();
      await audit(profile, site, theme, 'light');
      await sections.getByRole('tab', { name: 'Aim', exact: true }).click(); await settle();
      await audit(profile, site, theme, 'aim');
      await page.getByRole('button', { name: 'All lights', exact: true }).click(); await settle();
    }
    if (profile.name === 'desktop-1366x768' || profile.name === 'phone-390x844') {
      // In-room popovers sit on the dark stage in both themes (Q03).
      for (const [name, dialog] of [['Layers', 'Room layers'], ['Room help', null]]) {
        const trigger = page.locator('.apt-home-stage').getByRole('button', { name, exact: true });
        if (!await trigger.isVisible()) continue;
        await trigger.click(); await settle();
        await audit(profile, site, theme, name === 'Layers' ? 'layers' : 'help');
        if (dialog && !await page.getByRole('dialog', { name: dialog, exact: true }).isVisible()) findings.push({ message: dialog + ' opens · ' + profile.name });
        await page.keyboard.press('Escape'); await settle();
      }
      const setup = page.locator('.apt-home-topbar').getByRole('button', { name: 'Setup', exact: true });
      if (await setup.isVisible() && await setup.isEnabled()) {
        await setup.click(); await settle();
        await audit(profile, site, theme, 'setup');
        if (await setup.getAttribute('aria-pressed') === 'true') { await setup.click(); await settle(); }
        if (await setup.getAttribute('aria-pressed') === 'true') { await page.keyboard.press('Escape'); await settle(); }
      }
    }
    if (await tab('Camera · JEV')) await audit(profile, site, theme, 'camera');
    if (site === 'echo' && await tab('Chat')) await audit(profile, site, theme, 'chat');
    await tab('Controls');
  }

  // HANDOFF › Acceptance examples › Phone: the sheet has a light/camera
  // switch, the toolbar sits above the sheet, and every control is ≥ 44 px.
  async function phoneExample() {
    await setProfile(PROFILES[1]); await setTheme('dark'); await selectHome('victoria'); await tab('Controls');
    const layout = await page.evaluate(() => {
      const box = selector => document.querySelector(selector)?.getBoundingClientRect().toJSON() || null;
      const tabs = [...document.querySelectorAll('.apt-home-tabs [role="tab"]')].filter(tab => tab.getClientRects().length).map(tab => tab.textContent.trim());
      return { tabs, sheet: box('.apt-home-panel'), toolbar: box('.apt-home-shell :is(.apt-home-room-toolbar, .apt-ui-room-toolbar)'),
        grip: box('.apt-home-sheet-grip'), viewport: { width: innerWidth, height: innerHeight },
        overflow: document.documentElement.scrollWidth > innerWidth };
    });
    receipt.phone_example = layout;
    expect(layout.tabs.length >= 2 && layout.tabs.some(name => /Camera/.test(name)), 'the phone sheet offers a light/camera switch', layout.tabs);
    expect(!!(layout.toolbar && layout.sheet) && layout.toolbar.bottom <= layout.sheet.top + .5, 'the room toolbar sits above the sheet', layout);
    expect(!layout.overflow, 'no horizontal page scroll on the phone', layout);
    const phoneTargets = receipt.states.filter(state => state.profile === 'phone-390x844' && !KNOWN['phone-390x844 ' + state.surface]);
    expect(phoneTargets.length >= 8 && phoneTargets.every(state => state.small_targets?.length === 0),
      'every audited phone control is at least 44 px', phoneTargets.map(state => [state.site, state.theme, state.surface, state.small_targets?.length]));
    await page.screenshot({ path: path.join(run, 'evidence', 'phone-390x844-victoria-dark.png') });
    layout.screenshot = 'evidence/phone-390x844-victoria-dark.png';
  }

  // F03/Q02: designed micro-motion, and the snap under reduced motion.
  async function motion() {
    await setProfile(PROFILES[0]);
    for (const reduced of [false, true]) {
      await page.emulateMedia({ reducedMotion: reduced ? 'reduce' : 'no-preference' });
      const values = await page.evaluate(() => {
        const probe = (html, pseudo) => {
          const holder = document.createElement('div'); holder.setAttribute('aria-hidden', 'true');
          holder.style.cssText = 'position:absolute;left:-9999px;top:0';
          holder.innerHTML = html; document.querySelector('.apt-home-panel').append(holder);
          const style = getComputedStyle(holder.querySelector('[data-probe]'), pseudo || null);
          const result = { transition: style.transitionDuration, animation: style.animationName + ' ' + style.animationDuration,
            shadow: style.boxShadow, color: getComputedStyle(holder.firstElementChild).color, size: style.width };
          holder.remove(); return result;
        };
        const press = [...document.styleSheets].flatMap(sheet => { try { return [...sheet.cssRules]; } catch (_) { return []; } })
          .find(rule => rule.selectorText === 'button:not(:disabled):active');
        return {
          press: press ? press.style.transform + ' ' + press.style.transitionDuration : null,
          switchThumb: probe('<button type="button" role="switch" class="apt-ui-switch" data-probe aria-checked="false"></button>', '::after'),
          checkboxSwitch: probe('<label class="apt-ui-switch" data-probe><input type="checkbox"><span>Off</span></label>', '::after'),
          chevron: probe('<details class="apt-ui-details"><summary data-probe>Details</summary></details>', '::before'),
          spinner: probe('<span class="apt-ui-badge" data-state="loading"><span class="apt-ui-badge-marker" data-shape="spinner" data-probe></span><span>Loading</span></span>'),
          live: probe('<span class="apt-ui-badge" data-state="live"><span class="apt-ui-badge-marker" data-shape="live" data-probe></span><span>Live</span></span>'),
          fg0: getComputedStyle(document.querySelector('.apt-home-panel')).getPropertyValue('--hg-fg-0').trim(),
        };
      });
      receipt.motion[reduced ? 'reduced' : 'full'] = values;
      if (!reduced) {
        expect(values.press === 'scale(0.97) 100ms', 'buttons press to 0.97 over 100 ms', values.press);
        for (const key of ['switchThumb', 'checkboxSwitch']) expect(values[key].transition.split(', ').includes('0.16s'), key + ' eases over 0.16 s', values[key]);
        expect(values.chevron.transition.split(', ').includes('0.15s'), 'disclosure chevrons turn over 0.15 s', values.chevron);
        expect(/^apt-ui-spin 0\.8s/.test(values.spinner.animation), 'the loading badge spins', values.spinner);
        expect(values.live.shadow !== 'none' && values.live.size === '7px', 'the live dot glows at 7 px', values.live);
      } else {
        for (const key of ['switchThumb', 'checkboxSwitch', 'chevron']) expect(values[key].transition.split(', ').every(value => value === '1e-05s'), key + ' snaps under reduced motion', values[key]);
        expect(/ 1e-05s$/.test(values.spinner.animation), 'the spinner snaps under reduced motion', values.spinner);
      }
    }
    await page.emulateMedia({ reducedMotion: 'no-preference' });
  }

  // Q02: every Tab stop shows the 1 px ring, in the designed order.
  async function keyboard() {
    await setProfile(PROFILES[0]); await setTheme('dark'); await selectHome('victoria'); await tab('Controls');
    await page.evaluate(() => {
      const start = document.createElement('button'); start.id = 'audit-focus-start'; start.textContent = 'Audit start';
      start.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0'; document.body.prepend(start); start.focus();
    });
    const stops = [];
    let blank = 0;
    for (let i = 0; i < 70 && blank < 3; i++) {
      await page.keyboard.press('Tab');
      const stop = await page.evaluate(describeFocus);
      if (!stop || stop.name === 'Audit start') { blank++; continue; }
      blank = 0; stops.push(stop);
      if (stops.filter(item => item.region === 'tabs').length && stop.region === 'header' && stops.at(-2)?.region !== 'header') break;
    }
    await page.evaluate(() => document.getElementById('audit-focus-start')?.remove());
    receipt.keyboard = stops;
    const unringed = stops.filter(stop => stop.visible && !stop.ring);
    expect(!unringed.length, 'every visible Tab stop shows a focus ring', unringed);
    const order = stops.map(stop => stop.region).filter((region, index, all) => region !== all[index - 1]);
    receipt.keyboard_regions = order;
    expect(order[0] === 'header', 'Tab starts in the header', order);
    expect(order.includes('stage') && order.includes('inspector'), 'Tab reaches the room toolbar and the inspector', order);
  }

  async function exercise() {
    served = await serveAuthoredShell(run, SRC);
    browser = await chromium.launch(launchOptions());
    receipt.browser = browser.version();
    const context = await browser.newContext({ viewport: PROFILES[0].viewport, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: served.origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return ['data:', 'blob:'].includes(url.protocol) || url.origin === served.origin ? route.continue() : route.abort();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => receipt.errors.push(error.message));
    cdp = await context.newCDPSession(page);
    await phase('load', async () => {
      await page.goto(served.origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
      await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
      await waitSite('echo'); await page.evaluate(() => document.fonts.ready.then(() => true));
    });
    for (const site of ['echo', 'victoria']) {
      await phase('select ' + site, () => selectHome(site));
      for (const profile of PROFILES) {
        await phase(site + ' ' + profile.name, async () => {
          await setProfile(profile);
          for (const theme of ['dark', 'light']) { await setTheme(theme); await surfaces(profile, site, theme); }
        });
      }
    }
    await phase('phone example', phoneExample);
    await phase('motion', motion);
    await phase('keyboard', keyboard);
  }

  try {
    await Promise.race([exercise(), deadline]);
    receipt.forbidden = served.forbidden;
    assert.deepEqual(served.forbidden, [], 'the audit sends no command or session request');
    assert.deepEqual(receipt.errors, [], 'no page errors');
    if (REPORT_ONLY) console.log('Report only: ' + findings.length + ' findings');
    else assert.deepEqual(findings.map(finding => finding.message), [], JSON.stringify(findings.slice(0, 4), null, 1).slice(0, 6000));
  } finally {
    clearTimeout(timer); save(); console.log('Shell audit receipt: ' + path.join(run, 'receipt.json'));
    await browser?.close().catch(() => {}); served?.server.closeAllConnections(); served?.server.close();
  }
});
