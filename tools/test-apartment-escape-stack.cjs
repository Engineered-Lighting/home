// APT-F04: exercise the production HomeOverlay core, with authored DOM nodes.
// No room assets, household data, transport, camera or session is loaded here.
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../app/src/home-overlay.js'), 'utf8');
const repo = path.resolve(__dirname, '..');
const babel = require(path.join(repo, 'app/src/vendor/babel-7.29.0/babel.min.js'));
function parsed(name) {
  const source = fs.readFileSync(path.join(repo, 'app/src', name), 'utf8');
  return { source, ast: babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program };
}
function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
const apartment = parsed('home-apartment.jsx'), editor = parsed('home-apartment-edit.jsx');
const text = (file, node) => file.source.slice(node.start, node.end);
function local(file, name) {
  const found = [];
  walk(file.ast, node => { if (node.type === 'VariableDeclarator' && node.id?.name === name) found.push(node.init); });
  assert.equal(found.length, 1, 'exactly one production binding for ' + name);
  const value = found[0];
  return value.type === 'CallExpression' && value.callee?.name === 'useCallback' ? value.arguments[0] : value;
}
function layer(file, key) {
  const found = [];
  walk(file.ast, node => {
    if (node.type === 'CallExpression' && node.callee?.property?.name === 'useOverlayLayer') {
      const arg = node.arguments[0];
      if (arg?.type === 'ObjectExpression' && arg.properties.some(item => item.key?.name === 'key' && item.value?.value === key)) found.push(arg);
    }
  });
  assert.equal(found.length, 1, 'exactly one production layer ' + key); return found[0];
}
const property = (object, key) => object.properties.find(item => item.key?.name === key)?.value;
const evaluate = (file, node, bindings) => vm.runInNewContext('(' + text(file, node) + ')', bindings, { timeout: 1000 });

function fixture() {
  const frames = [], listeners = [];
  const document = {
    activeElement: null,
    documentElement: { setAttribute() {}, removeAttribute() {} },
    querySelector: () => null,
  };
  const window = {
    document,
    addEventListener: (name, handler, options) => listeners.push({ name, handler, options }),
    requestAnimationFrame: callback => frames.push(callback),
    getComputedStyle: element => ({ display: element.hidden ? 'none' : 'block', visibility: element.visibility || 'visible' }),
  };
  const context = vm.createContext({ window, document, setTimeout, clearTimeout, console });
  vm.runInContext(source, context, { filename: 'home-overlay.js' });
  const api = window.HomeOverlay;
  function node(name, options = {}) {
    const element = {
      name, tagName: 'BUTTON', isConnected: true, hidden: false, disabled: false, inert: false, ownerDocument: document,
      isContentEditable: false, focused: 0, focusAttempts: 0, children: [], attributes: {}, parentElement: null,
      ...options,
      focus() {
        this.focusAttempts++;
        if (!this.isConnected || this.disabled) return;
        for (let current = this; current; current = current.parentElement) {
          if (current.hidden || current.inert || current.visibility === 'hidden') return;
        }
        this.focused++; document.activeElement = this;
      },
      contains(other) { return this === other || this.children.some(child => child.contains(other)); },
      getClientRects() {
        if (!this.isConnected) return [];
        for (let current = this; current; current = current.parentElement) if (current.hidden) return [];
        return [{}];
      },
      getAttribute(name) { return this.attributes[name] ?? null; },
      hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
      setAttribute(name, value) { this.attributes[name] = value; },
      matches(selector) { return selector === ':disabled' && this.disabled; },
      closest(selector) {
        for (let current = this; current; current = current.parentElement) {
          if ((selector.includes('[inert]') && current.inert) || (selector.includes('[hidden]') && current.hidden)) return current;
        }
        return null;
      },
      querySelectorAll() { return this.children.filter(child => !child.disabled); },
    };
    return element;
  }
  function escape(target = null, extras = {}) {
    const event = { key: 'Escape', target, defaultPrevented: false, stopped: false, ...extras,
      preventDefault() { this.defaultPrevented = true; },
      stopImmediatePropagation() { this.stopped = true; },
    };
    api._handleKeydown(event);
    return event;
  }
  return { api, document, frames, listeners, node, escape };
}

test('production overlay installs one capture listener and claims only the topmost layer', () => {
  const f = fixture(), calls = [];
  assert.equal(f.listeners.length, 1);
  assert.equal(f.listeners[0].name, 'keydown');
  assert.equal(f.listeners[0].options.capture, true);
  const base = f.api.push({ key: 'apartment-embedded', passive: true, initialFocus: 'none', onEscape: () => { calls.push('base'); return false; } });
  const modal = f.api.push({ key: 'authored-modal', initialFocus: 'none', onEscape: () => calls.push('modal') });
  const event = f.escape();
  assert.deepEqual(calls, ['modal']);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.stopped, true);
  assert.equal(f.api.topKey(), 'authored-modal', 'dispatcher does not pop or walk the stack itself');
  modal.pop();
  const idle = f.escape();
  assert.deepEqual(calls, ['modal', 'base']);
  assert.equal(idle.defaultPrevented, false);
  assert.equal(idle.stopped, false);
  assert.equal(f.api.topKey(), 'apartment-embedded', 'idle embedded layer remains open');
  base.pop();
});

for (const editable of ['INPUT', 'TEXTAREA', 'SELECT', 'contenteditable']) {
  test(`top-layer ${editable} owns Escape; an editor behind that layer does not`, () => {
    const f = fixture(), root = f.node('modal', { tagName: 'DIV' });
    const inside = f.node('inside', { tagName: editable === 'contenteditable' ? 'DIV' : editable,
      isContentEditable: editable === 'contenteditable' });
    const outside = f.node('outside', { tagName: 'TEXTAREA' });
    root.children.push(inside);
    let calls = 0;
    const modal = f.api.push({ key: 'modal', getRoot: () => root, initialFocus: 'none', onEscape: () => { calls++; } });
    const innerEvent = f.escape(inside);
    assert.equal(calls, 0);
    assert.equal(innerEvent.defaultPrevented, false);
    const outerEvent = f.escape(outside);
    assert.equal(calls, 1);
    assert.equal(outerEvent.defaultPrevented, true);
    modal.pop();
  });
}

test('a passing or throwing top layer never dispatches to an underlying cancellation owner', () => {
  for (const throwing of [false, true]) {
    const f = fixture(), calls = [];
    const base = f.api.push({ key: 'base', initialFocus: 'none', onEscape: () => calls.push('base') });
    const top = f.api.push({ key: 'top', initialFocus: 'none', onEscape: () => {
      calls.push('top'); if (throwing) throw Error('authored callback failure'); return false;
    } });
    const event = f.escape();
    assert.deepEqual(calls, ['top']);
    assert.equal(event.defaultPrevented, throwing);
    assert.equal(event.stopped, throwing);
    top.pop(); base.pop();
  }
});

test('owned portal editors receive Escape without changing the modal focus-trap root', () => {
  const f = fixture(), root = f.node('modal-root', { tagName: 'DIV' }), first = f.node('first'),
    portal = f.node('portal', { tagName: 'DIV' }), editor = f.node('portal-editor', { tagName: 'TEXTAREA' });
  root.children.push(first); portal.children.push(editor);
  let calls = 0;
  const top = f.api.push({ key: 'modal', getRoot: () => root, getRoots: () => [portal], trap: true,
    initialFocus: 'none', onEscape: () => { calls++; } });
  const editorEscape = f.escape(editor);
  assert.equal(calls, 0); assert.equal(editorEscape.defaultPrevented, false);
  f.document.activeElement = editor;
  let wrapped = false;
  f.api._handleKeydown({ key: 'Tab', target: editor, preventDefault() { wrapped = true; } });
  assert.equal(wrapped, true); assert.equal(f.document.activeElement, first, 'portal must not silently expand the trapping root');
  top.pop();
});

test('held Escape is consumed without advancing an operation, while owned editors keep repeats', () => {
  const f = fixture(), root = f.node('root'), editor = f.node('editor', { tagName: 'INPUT' });
  root.children.push(editor); let calls = 0;
  const top = f.api.push({ key: 'modal', getRoot: () => root, initialFocus: 'none', onEscape: () => { calls++; return false; } });
  const repeat = f.escape(null, { repeat: true });
  assert.equal(calls, 0); assert.equal(repeat.defaultPrevented, true); assert.equal(repeat.stopped, true);
  const editableRepeat = f.escape(editor, { repeat: true });
  assert.equal(calls, 0); assert.equal(editableRepeat.defaultPrevented, false); assert.equal(editableRepeat.stopped, false);
  const discrete = f.escape();
  assert.equal(calls, 1); assert.equal(discrete.defaultPrevented, false, 'normal claim/pass behavior stays intact');
  top.pop();
});

test('public operation focus restoration resolves supported targets and verifies actual focus', () => {
  const f = fixture();
  assert.equal(typeof f.api.restoreFocus, 'function');
  const unusable = f.node('unusable', { hidden: true }), valid = f.node('valid');
  for (const target of [valid, { current: valid }, () => valid, '#authored-target']) {
    f.document.querySelector = selector => selector === '#authored-target' ? valid : null;
    assert.equal(f.api.restoreFocus(unusable, target), true);
    assert.equal(f.document.activeElement, valid);
  }
  const refusing = f.node('refusing'); refusing.focus = function () { this.focusAttempts++; };
  f.document.activeElement = null;
  assert.equal(f.api.restoreFocus(refusing, valid), true, 'a focus() call without activeElement change is not success');
  assert.equal(f.document.activeElement, valid);
  f.api.setFallbackFocus(null);
  assert.equal(f.api.restoreFocus(unusable, null), false);
});

test('an incidental document body does not count as a restored opener', () => {
  const f = fixture(), body = f.node('body', { tagName: 'BODY' }), fallback = f.node('fallback');
  f.document.body = body; f.document.activeElement = body;
  assert.equal(f.api.restoreFocus(body, fallback), true);
  assert.equal(f.document.activeElement, fallback);
  body.setAttribute('tabindex', '-1');
  assert.equal(f.api.restoreFocus(body, fallback), true, 'an explicitly focusable body remains a supported target');
  assert.equal(f.document.activeElement, body);
});

test('closing the top layer restores its still-visible connected enabled opener', () => {
  const f = fixture(), opener = f.node('opener'), fallback = f.node('fallback');
  f.api.setFallbackFocus(() => fallback); f.document.activeElement = opener;
  const top = f.api.push({ key: 'top', initialFocus: 'none' });
  top.pop();
  assert.equal(opener.focused, 1); assert.equal(fallback.focused, 0);
  assert.equal(f.document.activeElement, opener);
  top.pop(); assert.equal(opener.focused, 1, 'double cleanup never steals focus');
});

for (const unavailable of [{ isConnected: false }, { hidden: true }, { disabled: true }, { visibility: 'hidden' }, { inert: true }]) {
  test(`unavailable opener ${JSON.stringify(unavailable)} uses the usable restore target`, () => {
    const f = fixture(), opener = f.node('opener'), restore = f.node('restore');
    f.document.activeElement = opener;
    const top = f.api.push({ key: 'top', initialFocus: 'none', restoreTo: () => restore });
    Object.assign(opener, unavailable);
    top.pop();
    assert.equal(opener.focused, 0, 'a stale opener must not receive focus');
    assert.equal(restore.focused, 1);
    assert.equal(f.document.activeElement, restore, 'restoration reaches a usable target rather than returning after a failed focus()');
  });
}

for (const unavailable of [{ isConnected: false }, { hidden: true }, { disabled: true }, { visibility: 'hidden' }, { inert: true }]) {
  test(`unavailable restore target ${JSON.stringify(unavailable)} falls back to the app target`, () => {
    const f = fixture(), opener = f.node('removed', { isConnected: false }),
      restore = f.node('restore', unavailable), fallback = f.node('fallback');
    f.document.activeElement = opener; f.api.setFallbackFocus(() => fallback);
    const top = f.api.push({ key: 'top', initialFocus: 'none', restoreTo: { current: restore } });
    top.pop();
    assert.equal(restore.focused, 0);
    assert.equal(fallback.focused, 1);
    assert.equal(f.document.activeElement, fallback);
  });
}

for (const unavailable of [{ hidden: true }, { inert: true }]) {
  test(`an opener inside an unavailable ancestor ${JSON.stringify(unavailable)} falls back`, () => {
    const f = fixture(), parent = f.node('ancestor', unavailable), opener = f.node('opener', { parentElement: parent }),
      restore = f.node('restore');
    f.document.activeElement = opener;
    const top = f.api.push({ key: 'top', initialFocus: 'none', restoreTo: () => restore });
    top.pop();
    assert.equal(opener.focused, 0); assert.equal(restore.focused, 1);
    assert.equal(f.document.activeElement, restore);
  });
}

test('an unavailable last-resort target is never focused', () => {
  const f = fixture(), opener = f.node('gone', { isConnected: false }), fallback = f.node('hidden', { hidden: true });
  f.document.activeElement = opener; f.api.setFallbackFocus(() => fallback);
  const top = f.api.push({ key: 'top', initialFocus: 'none' }); top.pop();
  assert.equal(fallback.focused, 0); assert.equal(fallback.focusAttempts, 0);
});

test('removing a non-top layer does not restore its opener over the active modal', () => {
  const f = fixture(), first = f.node('first'), second = f.node('second');
  f.document.activeElement = first;
  const low = f.api.push({ key: 'low', initialFocus: 'none' });
  f.document.activeElement = second;
  const high = f.api.push({ key: 'high', initialFocus: 'none' });
  low.pop(); assert.equal(first.focused, 0); assert.equal(second.focused, 0);
  assert.equal(f.api.topKey(), 'high'); high.pop(); assert.equal(second.focused, 1);
});

test('deferred focus entry cannot steal focus after a newer layer opens', () => {
  const f = fixture(), lowRoot = f.node('low-root'), highRoot = f.node('high-root');
  const low = f.api.push({ key: 'low', getRoot: () => lowRoot });
  const high = f.api.push({ key: 'high', getRoot: () => highRoot });
  while (f.frames.length) f.frames.shift()();
  assert.equal(lowRoot.focused, 0); assert.equal(highRoot.focused, 1);
  high.pop(); low.pop();
});

function apartmentOperation(flags = {}) {
  const calls = [], focus = [];
  const current = { ...flags }, refs = { count: { current: flags.pairs || 0 }, arm: { current: flags.arm || 0 } };
  const bindings = {
    radialGestureOwnerRef: { current: flags.radial ? { cancel: () => { calls.push('radial'); bindings.radialGestureOwnerRef.current = null; } } : null },
    deskTargetCaptureRef: { current: !!flags.desk },
    virtualAimGestureRef: { current: { readback: () => ({ active: !!flags.drag }), cancel: () => calls.push('drag') } },
    mapRehearsalOpen: !!flags.rehearsal,
    window: { HomeMapRehearsalUI: { close: () => calls.push('rehearsal') } },
    travellingRef: { current: !!flags.travel }, spatialCoordinatorRef: { current: { getState: () => ({ phase: flags.phase || 'idle' }) } },
    aimMode: !!flags.tap, calibCam: !!flags.calibration, calibPairCountRef: refs.count, calibEscArmRef: refs.arm,
    engineRef: { current: { rig: { inCameraPose: () => !!flags.camera } } },
    editing: !!flags.setup, cardId: flags.card ? 'authored-card' : null, virtualFixtureControlsOpen: !!flags.selection,
    Date: { now: () => current.now || 10000 },
    cancelDeskTargetSelection: () => calls.push('desk'), cancelSpatialTravel: () => calls.push('travel'),
    setAimMode: value => { assert.equal(value, false); calls.push('tap'); }, setAimDestination: value => assert.equal(value, null),
    showToast: (message, opts) => { assert.match(message, /Esc again discards/); assert.equal(opts.tone, 'error'); calls.push('calibration-warning'); },
    setToast: value => { assert.equal(value, null); calls.push('calibration-confirm'); },
    exitCameraPose: () => calls.push('camera'), setEditing: value => { assert.equal(value, false); calls.push('setup'); },
    setCardId: value => { assert.equal(value, null); calls.push('card'); }, clearVirtualFixtureSelection: () => calls.push('selection'),
    restoreApartmentOperationFocus: key => focus.push(key),
    // W2 owners: the room reticle's drag (step 1), design tap to aim (step 2)
    // and the selected light's pending proposal (step 4).
    aimPresentationRef: { current: { readback: () => ({ dragging: !!flags.reticle }), cancelDrag: () => { calls.push('reticle'); flags.reticle = false; } } },
    tapAim: flags.designTap ? 'authored-light' : null, setTapAim: value => { assert.equal(value, null); calls.push('design-tap'); },
    virtualFixtureId: 'authored-light',
    aimScopeFor: id => { assert.equal(id, 'authored-light'); return { proposal: flags.proposal ? { id: 'authored-proposal' } : null }; },
    discardAimProposal: ({ fixture_id, proposal }) => {
      assert.equal(fixture_id, 'authored-light'); assert.equal(proposal.id, 'authored-proposal');
      calls.push('proposal'); flags.proposal = false; return true;
    },
  };
  const handle = evaluate(apartment, local(apartment, 'handleApartmentEscape'), bindings);
  return { handle, calls, focus, refs, current, bindings };
}

const allLowerOperations = { drag: true, rehearsal: true, travel: true, tap: true, calibration: true, pairs: 3,
  camera: true, setup: true, card: true, selection: true };
for (const row of [
  { name: 'radial drag', flags: { radial: true, desk: true, ...allLowerOperations }, expected: 'radial', focus: 'selection' },
  { name: 'desk capture', flags: { desk: true, ...allLowerOperations }, expected: 'desk', focus: 'desk' },
  { name: 'reticle drag', flags: allLowerOperations, expected: 'drag', focus: 'drag' },
  { name: 'existing rehearsal owner', flags: { rehearsal: true, travel: true, tap: true, setup: true }, expected: 'rehearsal' },
  { name: 'existing travel owner', flags: { travel: true, tap: true, setup: true }, expected: 'travel' },
  { name: 'existing coordinator transition owner', flags: { phase: 'travelling', tap: true, setup: true }, expected: 'travel' },
  { name: 'tap-to-aim', flags: { tap: true, calibration: true, pairs: 3, camera: true, setup: true, card: true, selection: true }, expected: 'tap', focus: 'aim' },
  { name: 'calibration warning before camera exit', flags: { calibration: true, pairs: 3, camera: true, setup: true, card: true }, expected: 'calibration-warning' },
  { name: 'existing camera return owner', flags: { camera: true, setup: true, card: true, selection: true }, expected: 'camera', focus: 'camera' },
  { name: 'setup exit', flags: { setup: true, card: true, selection: true }, expected: 'setup', focus: 'setup' },
  { name: 'control card', flags: { card: true, selection: true }, expected: 'card', focus: 'card' },
  { name: 'virtual fixture selection', flags: { selection: true }, expected: 'selection', focus: 'selection' },
  // Rows for the W2 owners (APT-F04 done[0]); every existing row is unchanged.
  { name: 'room reticle drag cancels before every lower operation', flags: { reticle: true, designTap: true, proposal: true, ...allLowerOperations, drag: false }, expected: 'reticle' },
  { name: 'design tap to aim', flags: { designTap: true, calibration: true, pairs: 3, camera: true, setup: true, card: true, selection: true, proposal: true }, expected: 'design-tap' },
  { name: 'camera view before a pending proposal', flags: { camera: true, proposal: true, selection: true }, expected: 'camera', focus: 'camera' },
  { name: 'control card before a pending proposal', flags: { card: true, proposal: true, selection: true }, expected: 'card', focus: 'card' },
  { name: 'pending proposal discard before selection', flags: { proposal: true, selection: true }, expected: 'proposal' },
]) {
  test('actual Apartment operation table: ' + row.name + ' invokes exactly its existing owner', () => {
    const f = apartmentOperation(row.flags);
    assert.equal(f.handle(), true); assert.deepEqual(f.calls, [row.expected]);
    assert.deepEqual(f.focus, row.focus ? [row.focus] : []);
    // This proves dispatch and owner selection only. The existing owner's
    // lifecycle, geometry or persistence effects are outside this VM seam.
  });
}

test('actual ZIP Escape order: reticle drag, then proposal, then selection, then nothing', () => {
  const f = apartmentOperation({ reticle: true, proposal: true, selection: true });
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['reticle'], 'a drag cancels alone; the proposal is kept');
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['reticle', 'proposal'], 'next, the proposal is discarded');
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['reticle', 'proposal', 'selection']);
  assert.deepEqual(f.focus, ['selection']);
  f.bindings.virtualFixtureControlsOpen = false;
  const idle = evaluate(apartment, local(apartment, 'handleApartmentEscape'), f.bindings);
  assert.equal(idle(), false, 'then nothing happens'); assert.deepEqual(f.calls, ['reticle', 'proposal', 'selection']);
});

test('actual radial Escape cancels only painting and retains the selected fixture for a later Escape', () => {
  const f = apartmentOperation({ radial: true, selection: true });
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['radial']); assert.deepEqual(f.focus, ['selection']);
  assert.equal(f.bindings.radialGestureOwnerRef.current, null);
  assert.equal(f.bindings.virtualFixtureControlsOpen, true, 'the radial cancellation path does not clear fixture selection');
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['radial', 'selection']);
  assert.deepEqual(f.focus, ['selection', 'selection']);
});

test('actual embedded idle callback passes without closing; the separate modal adapter retains its close', () => {
  const f = apartmentOperation(); let closes = 0;
  assert.equal(f.handle(), false); assert.deepEqual(f.calls, []);
  assert.equal(text(apartment, property(layer(apartment, 'apartment-embedded'), 'onEscape')), 'handleApartmentEscape');
  const modal = evaluate(apartment, property(layer(apartment, 'apartment'), 'onEscape'), {
    handleApartmentEscape: f.handle, onClose: () => closes++,
  });
  modal(); assert.equal(closes, 1, 'modal close contract is separate from embedded idle');
  assert.deepEqual(f.calls, []);
});

test('actual calibration guard warns, requires a second discrete press within five seconds and rearms after expiry or capture', () => {
  const f = apartmentOperation({ calibration: true, pairs: 3, camera: true });
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['calibration-warning']);
  assert.equal(f.refs.count.current, 3); assert.equal(f.refs.arm.current, 10000);
  f.current.now = 15001;
  assert.equal(f.handle(), true); assert.deepEqual(f.calls, ['calibration-warning', 'calibration-warning']);
  assert.equal(f.refs.count.current, 3); assert.equal(f.refs.arm.current, 15001);
  f.current.now = 20001;
  assert.equal(f.handle(), false, 'the second press at the existing five-second boundary retains final pass/close');
  assert.equal(f.refs.count.current, 0); assert.equal(f.refs.arm.current, 0);
  assert.deepEqual(f.calls, ['calibration-warning', 'calibration-warning', 'calibration-confirm']);
  const pairNodes = [];
  walk(apartment.ast, node => { if (node.type === 'ObjectProperty' && node.key?.name === 'onPairsChanged') pairNodes.push(node.value); });
  assert.equal(pairNodes.length, 1, 'existing calibration capture callback remains unique');
  let seen;
  const capture = evaluate(apartment, pairNodes[0], { calibPairCountRef: f.refs.count, calibEscArmRef: f.refs.arm, syncCalibMarkers: pairs => { seen = pairs; } });
  f.refs.arm.current = 12345;
  const newPairs = [{ authored: true }]; capture(newPairs);
  assert.equal(f.refs.count.current, 1); assert.equal(f.refs.arm.current, 0); assert.equal(seen, newPairs);
  assert.equal(f.handle(), true, 'a capture disarms confirmation and requires a warning again');
});

for (const draftId of [null, 'authored-draft']) {
  test('actual target Cancel preserves other authored model content with draft=' + draftId, () => {
    const model = { targets: [{ id: 'authored-existing', name: 'Existing' }, { id: 'authored-draft', name: 'Draft' }],
      devices: [{ id: 'authored-device' }], unsaved: { retained: true } }, calls = [];
    const bindings = { targetDraftId: draftId, mutate: callback => { calls.push('mutate'); callback(model); } };
    for (const name of ['setTargetDraftId', 'setSelectedTargetId', 'setMoveTargetId', 'setPlacingTarget']) bindings[name] = value => { assert.equal(value, null); calls.push(name); };
    bindings.setNotice = message => assert.equal(message, 'target placement cancelled');
    const cancel = evaluate(editor, local(editor, 'cancelTargetPlacement'), bindings);
    cancel();
    assert.deepEqual(model.targets.map(item => item.id), draftId ? ['authored-existing'] : ['authored-existing', 'authored-draft']);
    assert.deepEqual(model.devices, [{ id: 'authored-device' }]); assert.deepEqual(model.unsaved, { retained: true });
    assert.equal(calls.filter(item => item === 'mutate').length, draftId ? 1 : 0);
    assert.deepEqual(calls.filter(item => item !== 'mutate'), ['setTargetDraftId', 'setSelectedTargetId', 'setMoveTargetId', 'setPlacingTarget']);
  });
}

test('actual target overlay invokes Cancel only while target placement is active', () => {
  const actual = layer(editor, 'apartment-target-placement'); let calls = 0;
  for (const [tool, placingTarget, targetDraftId, active] of [
    ['targets', null, null, false], ['targets', { label: 'Authored' }, null, true],
    ['targets', null, 'authored-draft', true], ['select', {}, 'authored-draft', false],
  ]) {
    const bindings = { tool, placingTarget, targetDraftId, cancelTargetPlacement: () => calls++ };
    assert.equal(evaluate(editor, property(actual, 'active'), bindings), active);
    const onEscape = evaluate(editor, property(actual, 'onEscape'), bindings), before = calls;
    onEscape(); assert.equal(calls, before + (active ? 1 : 0));
  }
});

for (const row of [
  { tool: 'targets', moveTargetId: 'authored-existing', placing: null, active: true, calls: ['setMoveTargetId'] },
  { tool: 'add', moveTargetId: null, placing: { id: 'authored-device' }, active: true, calls: ['setPlacing'] },
  { tool: 'measure', moveTargetId: null, placing: null, active: true, calls: ['setTool', 'setPlacing', 'setPlacingTarget', 'setMoveTargetId'] },
  { tool: 'targets', moveTargetId: null, placing: null, active: false, calls: [] },
  { tool: 'select', moveTargetId: 'authored-existing', placing: {}, active: false, calls: [] },
]) {
  test('actual armed editor tool ' + row.tool + ' cancels UI state without mutating the model', () => {
    const calls = [], bindings = { tool: row.tool, moveTargetId: row.moveTargetId, placing: row.placing, setNotice() {} };
    for (const name of ['setTool', 'setPlacing', 'setPlacingTarget', 'setMoveTargetId']) bindings[name] = value => {
      assert.equal(value, name === 'setTool' ? 'select' : null); calls.push(name);
    };
    bindings.selectEditorTool = evaluate(editor, local(editor, 'selectEditorTool'), bindings);
    const cancel = evaluate(editor, local(editor, 'cancelEditorToolOperation'), bindings);
    assert.equal(cancel(), row.active); assert.deepEqual(calls, row.calls);
    const actual = layer(editor, 'apartment-editor-tool');
    assert.equal(evaluate(editor, property(actual, 'active'), bindings), row.active);
    assert.equal(text(editor, property(actual, 'onEscape')), 'cancelEditorToolOperation');
    // No model, persistence, geometry, selection or undo API is provided to the
    // production callback; touching one would fail this source-derived proof.
  });
}
