// Authored-room audit of the actual unified Home shell (APT-F02, F03, Q02, Q03, S02, S05).
// Text contrast and faint-token text, coarse-pointer targets, surface and type
// fidelity, control motion with and without reduced motion, keyboard focus,
// status rows, unsaved-work isolation, the sheet handle and the HANDOFF
// "Phone" example, for both homes in both themes. Three parts, each its own
// test file, page load and 145 s deadline, so no part rides a slow runner
// alone: Echo surfaces, Victoria surfaces (with the Phone example and Tab
// order), and shell behaviour. Viewports and the coarse pointer change in
// place. Read-only: no session, camera, household data, physical command or
// deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { serveAuthoredShell } from './apartment-shell-audit-server.mjs';
import { auditText, auditTargets, auditSurfaces, describeFocus } from './apartment-shell-audit-probes.mjs';
import { waitForDirectionalReadiness } from './apartment-directional-readiness.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
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

const PARTS = {
  echo: 'Echo Park shell text, targets and surfaces meet the design at every size in both themes',
  victoria: 'Victoria shell text, targets and surfaces, the Phone example and the designed Tab order meet the design',
  behaviour: 'shell motion, status rows, unsaved-work isolation and the sheet handle behave as designed',
};

export function shellAudit(part) {
assert.ok(Object.hasOwn(PARTS, part), 'known shell audit part');
test(PARTS[part], { timeout: 160000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-shell-audit-browser', part + '-run-' + Date.now());
  fs.mkdirSync(path.join(run, 'evidence'), { recursive: true });
  const receipt = { schema: 'home-apartment-shell-audit-1', part, authored_room: true, real_household_data: false, live_camera: false,
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
    if (surface === 'room') {
      const overlays = state.overlays = await page.evaluate(() => {
        const stage = document.querySelector('.apt-home-stage').getBoundingClientRect();
        const named = { toolbar: '.apt-ui-room-toolbar', zoom: '[aria-label="apartment zoom controls"]', toast: '.apt-ui-toast',
          legend: '.apt-room-guide-legend', badge: '[data-apartment-approximate-badge="1"]' };
        const boxes = Object.entries(named).map(([name, selector]) => [name, document.querySelector(selector)])
          .filter(([, element]) => element && element.getClientRects().length && getComputedStyle(element).visibility === 'visible')
          .map(([name, element]) => [name, element.getBoundingClientRect().toJSON()]);
        const overlaps = [];
        for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
          const [a, ra] = boxes[i], [b, rb] = boxes[j];
          const x = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left), y = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
          if (x > .5 && y > .5) overlaps.push([a, b, Math.round(x * y)]);
        }
        const outside = boxes.filter(([name, r]) => name !== 'badge' && (r.left < stage.left - .5 || r.right > stage.right + .5 || r.top < stage.top - .5 || r.bottom > stage.bottom + .5)).map(([name]) => name);
        const can = label => [...document.querySelectorAll('.apt-home-stage button')].some(button => button.getAttribute('aria-label') === label
          && button.getClientRects().length && getComputedStyle(button).visibility === 'visible' && !button.closest('[hidden]'));
        return { names: boxes.map(([name]) => name), overlaps, outside,
          nonDrag: { zoomIn: can('zoom in'), zoomOut: can('zoom out'), orbitLeft: can('Orbit left'), orbitRight: can('Orbit right'), fit: can('Fit room') } };
      });
      expect(!overlays.overlaps.length && !overlays.outside.length, 'room overlays never collide and stay on the stage · ' + label, overlays);
      expect(Object.values(overlays.nonDrag).every(Boolean), 'the room can be zoomed, turned and refit without dragging · ' + label, overlays.nonDrag);
    }
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

  // APT-S02: the status popover says, per system, what this home is
  // connected to, from this home's own facts. Victoria never shows Echo's
  // Home Assistant state.
  async function statusRows() {
    receipt.status_rows = {};
    for (const [profile, theme] of [[PROFILES[0], 'dark'], [PROFILES[1], 'light']]) {
      await setProfile(profile); await setTheme(theme);
      for (const site of ['echo', 'victoria']) {
        await selectHome(site);
        const summary = page.getByRole('group', { name: 'Home', exact: true }).locator('xpath=..').locator('.apt-home-status > summary');
        await summary.click(); await settle();
        const rows = await page.evaluate(() => [...document.querySelectorAll('.apt-home-status-row')].map(row => ({
          system: row.dataset.system, text: row.querySelector('.apt-home-status-value').textContent.trim(),
          badge: row.querySelector('.apt-ui-badge')?.dataset.state || null, detail: row.querySelector('.apt-home-status-detail')?.textContent.trim() || '' })));
        receipt.status_rows[[profile.name, theme, site].join(' ')] = rows;
        const lights = rows.find(row => row.system === 'lights');
        expect(['room', 'lights', 'save', 'camera', 'capabilities'].every(key => rows.some(row => row.system === key)), 'status names each system · ' + site, rows);
        expect(rows.every(row => row.system === 'camera' && site === 'victoria' || row.badge), 'status rows use the shared badge vocabulary · ' + site, rows);
        if (site === 'echo') expect(/^Home Assistant (connected|not connected)$/.test(lights?.text), 'Echo names its Home Assistant connection', lights);
        else expect(/^No light entities for \S.* fixtures$/.test(lights?.text) && !rows.some(row => /Home Assistant/.test(row.text)),
          'Victoria says its fixtures have no light entities and never shows Echo\'s Home Assistant state', rows);
        expect(rows.find(row => row.system === 'capabilities')?.badge === 'unavailable', 'capability rows wait on the agreed contract · ' + site, rows);
        await audit(profile, site, theme, 'status');
        await summary.click(); await settle();
      }
    }
  }

  // APT-S02 Home isolation: parked unsaved Echo work keeps its mark while
  // Victoria is shown, switching says it was kept, and Victoria shows nothing
  // of it. The authored parked workspace stands in for an Echo draft.
  async function unsavedRoundTrip() {
    await setProfile(PROFILES[0]); await setTheme('dark'); await selectHome('echo');
    const echoScene = await page.evaluate(() => JSON.parse(JSON.stringify(window.__APT_ENGINE.sceneIdentity || window.__APT_ENGINE.workspaceScope)));
    await selectHome('victoria');
    expect(!(await page.evaluate(() => window.HomeApartmentData.dirtySites())).length, 'no home holds unsaved work before the draft', null);
    // Park an authored Echo draft at Echo's own scene scope, as leaving Echo
    // with unsaved edits does; Echo then restores it through the real path.
    const parked = await page.evaluate(async scope => {
      const seed = await fetch('/proxy/ha/api/apartment_model').then(response => response.json()).catch(() => null);
      return !!window.HomeApartmentData.captureWorkspace(scope, { model: { ...seed, site: 'echo' }, dirty: true });
    }, echoScene);
    expect(parked, 'the authored Echo draft is parked', parked);
    await selectHome('echo');
    const active = await page.evaluate(() => [...document.querySelectorAll('.apt-home-switcher > button[aria-pressed="true"] .apt-home-site-dot')].map(dot => dot.dataset.state));
    expect(active.join() === 'unsaved', 'the active home shows its restored unsaved work', active);
    await selectHome('victoria');
    const marks = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.apt-home-switcher > button')]
      .map(button => [button.textContent.replace(', unsaved work kept', '').trim(), button.querySelector('.apt-home-site-dot').dataset.state])));
    const toast = await page.locator('.apt-ui-toast-message').textContent().catch(() => null);
    receipt.unsaved_round_trip = { marks, toast };
    expect(marks['Echo Park'] === 'unsaved' && marks.Victoria !== 'unsaved', 'the Echo segment keeps its unsaved mark while Victoria is shown', marks);
    expect(toast === 'Echo Park · your unsaved work here is kept', 'switching away says the unsaved work was kept', toast);
    const victoriaRows = await page.evaluate(() => [...document.querySelectorAll('.apt-home-status-row')].map(row => row.textContent));
    expect(!victoriaRows.some(text => /Home Assistant connected|Unsaved changes/.test(text)), 'Victoria shows none of Echo\'s state', victoriaRows);
  }

  // APT-S05: drag the handle between detents by touch, and the non-drag
  // alternatives (tap, Arrow keys) reach every detent too.
  async function sheetGrip() {
    await setProfile(PROFILES[1]); await setTheme('dark'); await selectHome('victoria');
    const detent = () => page.locator('.apt-home-shell').getAttribute('data-sheet');
    const grip = page.getByRole('button', { name: 'Resize Home sheet', exact: true });
    const drag = async dy => {
      const box = await grip.boundingBox(), x = box.x + box.width / 2, y = box.y + box.height / 2, steps = 6;
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y, id: 1 }] });
      for (let i = 1; i <= steps; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y + dy * i / steps, id: 1 }] });
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await page.waitForTimeout(300); await settle();
    };
    const path = [await detent()];
    await drag(-260); path.push(await detent());
    await drag(520); path.push(await detent());
    await drag(-60); path.push(await detent());
    await grip.focus(); await page.keyboard.press('ArrowUp'); await settle(); path.push(await detent());
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowDown'); await settle(); path.push(await detent());
    await grip.click(); await settle(); path.push(await detent());
    const motion = await page.evaluate(() => getComputedStyle(document.querySelector('.apt-home-panel')).transitionDuration);
    receipt.sheet_grip = { path, motion };
    expect(JSON.stringify(path) === JSON.stringify(['half', 'full', 'peek', 'half', 'full', 'peek', 'half']),
      'the handle drags and steps through peek, half and full', path);
    expect(motion.split(', ').includes('0.22s'), 'the sheet eases over 0.22 s', motion);
    const bounds = await page.evaluate(() => ({ panel: document.querySelector('.apt-home-panel').getBoundingClientRect().toJSON(),
      padding: getComputedStyle(document.querySelector('.apt-home-panel')).paddingBottom, viewport: innerHeight }));
    expect(Math.abs(bounds.panel.bottom - bounds.viewport) <= .5, 'the sheet sits on the bottom edge with its safe-area padding', bounds);
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
    const first = region => order.indexOf(region);
    expect(first('header') === 0 && first('stage') > first('header') && first('inspector') > first('stage') && first('dock') > first('inspector'),
      'Tab moves header → room → inspector → dock', order);
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
    if (part === 'behaviour') {
      await phase('motion', motion);
      await phase('status rows', statusRows);
      await phase('unsaved work kept', unsavedRoundTrip);
      await phase('sheet grip', sheetGrip);
      return;
    }
    await phase('select ' + part, () => selectHome(part));
    for (const profile of PROFILES) {
      await phase(part + ' ' + profile.name, async () => {
        await setProfile(profile);
        for (const theme of ['dark', 'light']) { await setTheme(theme); await surfaces(profile, part, theme); }
      });
    }
    if (part === 'victoria') {
      await phase('phone example', phoneExample);
      await phase('keyboard', keyboard);
    }
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
}
