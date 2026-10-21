// APT-S06: the room guide against the production key handler, renderer marks
// and toolbar wiring. Authored inputs only; no room assets, household data,
// transport, camera or session is loaded here.
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repo = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(repo, 'app/src', name), 'utf8');
const babel = require(path.join(repo, 'app/src/vendor/babel-7.29.0/babel.min.js'));
function parsed(name) {
  const source = read(name);
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
const text = (file, node) => file.source.slice(node.start, node.end);

// The production module, evaluated as the browser would after Babel.
function loadGuide() {
  const window = {};
  const hook = () => { throw Error('hooks are not used by the pure guide functions'); };
  const React = { useEffect: hook, useId: hook, useLayoutEffect: hook, useRef: hook, useState: hook, useSyncExternalStore: hook };
  const code = babel.transform(read('home-apartment-room-guide.jsx'), { presets: ['react'], sourceType: 'script' }).code;
  // Same realm, so results compare structurally with local arrays.
  vm.runInThisContext(`(function (window, React, ReactDOM) {${code}
})`)(window, React, {});
  return window.HomeApartmentRoomGuide;
}
const Guide = loadGuide();
const apartment = parsed('home-apartment.jsx');

// Every literal a keydown function compares e.key/event.key with, and proof
// that the function reads keys no other way.
function keyLiterals(file, fn) {
  const literals = [], reads = [];
  walk(fn, node => {
    if (node.type === 'MemberExpression' && ['e', 'event'].includes(node.object?.name)
        && ['key', 'code', 'keyCode', 'which'].includes(node.property?.name)) reads.push(node);
    if (node.type === 'BinaryExpression' && ['===', '!=='].includes(node.operator)) {
      const [member, literal] = node.left.type === 'StringLiteral' ? [node.right, node.left] : [node.left, node.right];
      if (member.type === 'MemberExpression' && member.property?.name === 'key' && literal.type === 'StringLiteral') literals.push(literal.value);
    }
  });
  const keyReads = reads.filter(node => node.property.name === 'key');
  assert.equal(reads.length, keyReads.length, 'keyboard handlers read only e.key: ' + text(file, fn).slice(0, 80));
  assert.equal(keyReads.length, literals.length, 'every e.key read is a literal comparison: ' + text(file, fn).slice(0, 80));
  return literals;
}
// Functions a file registers with addEventListener("keydown", name, ...).
function keydownListeners(file) {
  const names = [];
  walk(file.ast, node => {
    if (node.type === 'CallExpression' && node.callee?.property?.name === 'addEventListener'
        && node.arguments[0]?.value === 'keydown' && node.arguments[1]?.type === 'Identifier') names.push(node.arguments[1].name);
  });
  return names;
}
function declarators(file, name) {
  const found = [];
  walk(file.ast, node => { if (node.type === 'VariableDeclarator' && node.id?.name === name) found.push(node.init); });
  return found;
}
const roomKeys = new Set(Guide.KEY_MAP.filter(row => row.owner === 'room').flatMap(row => row.keys));
const allKeys = new Set(Guide.KEY_MAP.flatMap(row => row.keys));

test('help rows are generated from the key map and nothing else', () => {
  for (const home of ['echo', 'victoria']) {
    const rows = Guide.helpRows(home);
    assert.deepEqual(rows.keys, Guide.KEY_MAP.filter(row => !row.homes || row.homes.includes(home)));
    assert.deepEqual(rows.gestures, Guide.GESTURES.filter(row => !row.homes || row.homes.includes(home)));
  }
  assert.deepEqual(Guide.helpRows('echo').keys.map(row => row.id), ['orbit', 'tilt', 'zoom', 'home', 'cycle', 'escape', 'help']);
  assert.deepEqual(Guide.helpRows('victoria').keys.map(row => row.id), ['orbit', 'tilt', 'zoom', 'home', 'cycle', 'aim-mode', 'aim-set', 'aim-history', 'escape', 'help']);
  assert.equal(new Set(Guide.KEY_MAP.flatMap(row => row.keys)).size, Guide.KEY_MAP.flatMap(row => row.keys).length, 'no key is listed twice');
});

test('the room key handler binds exactly the room keys the help lists', () => {
  const handlers = declarators(apartment, 'onKey').filter(fn => /rig\.stepAzimuth/.test(text(apartment, fn)));
  assert.equal(handlers.length, 1, 'one production room key handler');
  const bound = new Set(keyLiterals(apartment, handlers[0]));
  assert.deepEqual([...bound].sort(), [...roomKeys].sort(), 'handler keys equal KEY_MAP room keys in both directions');
  // A and Z are bound only on Victoria with a modeled light selected.
  const source = text(apartment, handlers[0]);
  assert.match(source, /\(e\.key === "a" \|\| e\.key === "z"\)[\s\S]{0,200}virtualFixtureControlsOpen && selectedSiteId === "victoria"/);
  assert.deepEqual(Guide.KEY_MAP.find(row => row.id === 'aim-mode').homes, ['victoria']);
  // Zoom keys keep their existing condition, so the listing says nothing more specific.
  // W2 binds Enter and ⌘Z/Ctrl+Z (Shift to redo) in the aim key handler, so only its rows may name them.
  assert.ok(Guide.KEY_MAP.every(row => !/Enter|⌘|Shift|F\b/.test(row.label) || row.owner === 'aim'), 'unbound keys from the design are not promised');
  const aim = declarators(apartment, 'onAimKey');
  assert.equal(aim.length, 1, 'one aim key handler');
  const aimRows = Guide.KEY_MAP.filter(row => row.owner === 'aim');
  assert.deepEqual([...new Set(keyLiterals(apartment, aim[0]))].sort(), [...aimRows.flatMap(row => row.keys), 'z'].sort(),
    "the aim handler binds exactly the aim rows plus A/Z's z, used with Cmd/Ctrl");
  assert.ok(aimRows.every(row => row.homes?.join() === 'victoria'));
});

test('every other room keydown listener only handles keys the help lists', () => {
  const sources = [apartment, parsed('home-3d/registered-desk-target.mjs')];
  let checked = 0;
  for (const file of sources) {
    for (const name of keydownListeners(file)) {
      for (const fn of declarators(file, name).filter(init => /Function/.test(init?.type))) {
        for (const key of keyLiterals(file, fn)) assert.ok(allKeys.has(key), `${name} binds ${key}, which help must list`);
        checked++;
      }
    }
  }
  assert.ok(checked >= 3, 'room, radial-gesture and desk-target keydown listeners were inspected');
  assert.match(apartment.source, /const handleApartmentEscape = \(\) => \{/, 'Escape remains the room escape ladder');
});

test('"?" is bound by the guide and wired from the toolbar', () => {
  const guide = parsed('home-apartment-room-guide.jsx');
  const hook = declarators(guide, 'onKey');
  assert.equal(hook.length, 1);
  assert.deepEqual(keyLiterals(guide, hook[0]), ['?']);
  assert.match(apartment.source, /Guide\.useHelpShortcut\(!guideDialog, \(\) => openGuide\("help"\)\)/);
});

test('gestures list exactly the bound pointer input, pan and pinch included', () => {
  const rig = read('home-3d/rig.js');
  const attach = rig.slice(rig.indexOf('export function attachInput'));
  assert.match(attach, /addEventListener\('pointerdown', onDown\)/);
  assert.match(attach, /addEventListener\('wheel', onWheel/);
  assert.match(attach, /if \(e\.button !== 0\) return;/, 'only the primary button orbits');
  assert.match(attach, /e\.button === 2 \|\| \(e\.button === 0 && e\.shiftKey\)/, 'Shift-drag and right-drag pan are bound');
  assert.match(attach, /rig\.panStart\?\.\(\)/);
  assert.match(attach, /PINCH_STEP/, 'two-finger pinch is bound to the zoom detents');
  assert.doesNotMatch(rig, /touchstart|gesturestart/i, 'pinch uses pointer events, not touch or gesture events');
  const words = [...Guide.GESTURES, ...Guide.KEY_MAP].map(row => `${row.label} ${row.action} ${row.when || ''}`).join(' ');
  assert.match(words, /Shift-drag or right-drag Pan the room/);
  assert.match(words, /Pinch with two fingers Zoom one step at a time/);
  assert.match(read('home-3d/equipment-virtual-fixtures.mjs'), /interaction_role: 'aim_handle'/, 'the aim handle exists');
});

test('legend marks use the renderer colours they describe', () => {
  for (const item of Guide.LEGEND) {
    const source = read(item.source).toLowerCase();
    assert.ok(source.includes('0x' + item.color.slice(1).toLowerCase()), `${item.id} colour ${item.color} is drawn by ${item.source}`);
  }
  const ids = input => Guide.legendItems(input).map(item => item.id);
  assert.deepEqual(ids({ homeId: 'echo' }), ['light-on', 'light-off', 'preview-beam', 'stale-beam']);
  assert.deepEqual(ids({ homeId: 'victoria' }), ['modeled-light']);
  assert.deepEqual(ids({ homeId: 'victoria', selected: true, hasPerception: true }), ['modeled-light', 'aim-handle', 'body-joints']);
});

test('layer rows: unavailable rows explain why, and fixtures have one setter', () => {
  const setter = () => {};
  const cases = [
    { homeId: 'echo' },
    { homeId: 'victoria', seams: { fixtures: { value: true, set: setter } }, hasPerception: true, fixtureCount: 4 },
    { homeId: 'victoria', hasPerception: false, wide: false },
  ];
  for (const input of cases) {
    const groups = Guide.layerGroups(input), rows = groups.flatMap(group => group.rows);
    assert.deepEqual(groups.map(group => group.id), ['guide', 'light', 'body', 'shadows']);
    for (const row of rows) {
      if (!row.available) assert.ok(typeof row.reason === 'string' && row.reason.length > 3, `${row.id} explains why it is unavailable`);
      else assert.equal(typeof row.set, 'function');
    }
    for (const id of ['pools', 'beams', 'named-aims', 'labels', 'cameras']) assert.ok(rows.some(row => row.id === id), `${id} row is listed`);
    assert.deepEqual(rows.filter(row => row.available).map(row => row.id).filter(id => id !== 'legend'),
      input.seams ? ['fixtures'] : [], 'no layer is enabled without its owner seam');
    const shadows = groups.find(group => group.id === 'shadows');
    assert.equal(shadows.title, 'Shadows and occlusion · simulated');
    assert.ok(shadows.rows.every(row => !row.available && row.reason.length > 3));
    assert.match(shadows.rows.find(row => row.id === 'beam-blocking').reason, /current scan/);
    assert.match(shadows.rows.find(row => row.id === 'body-shadows').reason, /Deferred · skeletal tracking only/);
  }
  const victoria = Guide.layerGroups(cases[1]).flatMap(group => group.rows);
  assert.equal(victoria.find(row => row.id === 'fixtures').set, setter, 'Layers calls the owner setter itself');
  assert.equal(victoria.find(row => row.id === 'fixtures').ariaLabel, 'Show virtual spotlights layer');
  assert.equal(victoria.find(row => row.id === 'follow').reason, 'Needs continuous tracking · planned');
  assert.ok(['body-observed', 'body-inferred', 'body-volume', 'body-uncertainty', 'frame-pose'].every(id => victoria.find(row => row.id === id)?.available === false));
  assert.equal(victoria.find(row => row.id === 'labels').detail, undefined);
  assert.equal(Guide.layerGroups({ ...cases[1], fixtureCount: 7 }).flatMap(group => group.rows).find(row => row.id === 'labels').detail, 'else on hover');
  const narrow = Guide.layerGroups(cases[2]).flatMap(group => group.rows);
  assert.equal(narrow.find(row => row.id === 'legend').available, false);
  assert.match(narrow.find(row => row.id === 'body').reason, /pose data/);
  const pooled = Guide.layerGroups({ homeId: 'echo', seams: { pools: { value: false, set: setter } } }).flatMap(group => group.rows);
  assert.equal(pooled.find(row => row.id === 'pools').available, true, 'a Repair seam enables its row without other changes');
});

test('About uses only existing facts and states the Den as modeled, not commissioned', () => {
  const echo = Guide.aboutFacts({ homeId: 'echo', source: { kind: 'live_ha_model', label: 'Live Home Assistant model', live: true },
    revision: 42, trackerStatus: 'live' });
  assert.deepEqual(echo.map(fact => [fact.group, fact.id]), [['live', 'model'], ['recorded', 'scene'], ['live', 'tracker']]);
  assert.equal(echo[0].title, 'Live Home Assistant model · rev 42');
  const den = Guide.aboutFacts({ homeId: 'victoria', source: { kind: 'equipment_registration', label: 'Private Victoria equipment', live: false },
    trackerStatus: 'connecting', hasVirtualFixtures: true, fixtureCount: 4, hasPerception: true });
  const by = id => den.find(fact => fact.id === id);
  assert.equal(by('model').group, 'authored');
  assert.deepEqual([by('fixtures').group, by('fixtures').title], ['modeled', '4 modeled fixtures · not commissioned']);
  assert.match(by('pools').detail, /not measured/);
  assert.deepEqual([by('perception').group, by('perception').title], ['recorded', 'Den camera results · viewing only']);
  assert.equal(by('tracker').group, 'unavailable');
  const shadow = Guide.aboutFacts({ homeId: 'echo', source: { kind: 'live_shadow', label: 'registered shadow model' }, liveShadow: true, simActive: true });
  assert.deepEqual(shadow.map(fact => fact.group), ['recorded', 'recorded', 'modeled', 'recorded']);
  assert.ok(!/capabilit/i.test(read('home-apartment-room-guide.jsx').replace(/\/\*[\s\S]*?\*\//g, '')), 'no capability object is read');
});

test('toolbar mount passes the single fixture setter and existing facts only', () => {
  const openings = [];
  walk(apartment.ast, node => { if (node.type === 'JSXOpeningElement' && node.name?.name === 'AptUnifiedRoomToolbar') openings.push(node); });
  assert.equal(openings.length, 1);
  const prop = name => { const attr = openings[0].attributes.find(item => item.name?.name === name); return attr && text(apartment, attr.value.expression); };
  assert.equal(prop('onVirtualVisible'), 'setVirtualSpotlightsVisible');
  assert.equal(prop('homeId'), 'selectedSiteId');
  assert.match(prop('about'), /source: sourceMeta/);
  const toolbar = apartment.ast.body.find(node => node.type === 'FunctionDeclaration' && node.id?.name === 'AptUnifiedRoomToolbar');
  const body = text(apartment, toolbar);
  assert.equal((body.match(/onVirtualVisible/g) || []).length, 2, 'the toolbar only forwards the fixture setter as the Layers seam');
  assert.match(body, /seams=\{\{ \.\.\.layerSeams, \.\.\.\(hasVirtualFixtures \? \{ fixtures: \{ value: virtualVisible, set: onVirtualVisible \} \} : \{\}\) \}\}/);
  assert.doesNotMatch(body, /pinch/i, 'gesture help lives in the guide, not in toolbar copy');
});

test('the guide loads with the Apartment, before the view that mounts it', () => {
  const index = read('index.html'), loader = read('home-feature-loader.js');
  const at = (source, entry) => { const i = source.indexOf(entry); assert.ok(i >= 0, entry); return i; };
  assert.ok(at(index, "['home-apartment-ui.jsx', true]") < at(index, "['home-apartment-room-guide.jsx', true]"));
  assert.ok(at(index, "['home-apartment-room-guide.jsx', true]") < at(index, "['home-apartment.jsx', true]"));
  assert.ok(index.includes("'home-apartment-room-guide.jsx': true,"), 'deferred with the Apartment feature');
  assert.ok(at(loader, '["home-apartment-ui.jsx", true]') < at(loader, '["home-apartment-room-guide.jsx", true]'));
  assert.ok(at(loader, '["home-apartment-room-guide.jsx", true]') < at(loader, '["home-apartment.jsx", true]'));
});
