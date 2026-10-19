// Actual room-inspector source with authored data, element and hook adapters.
// This is not mounted React, browser, transport or actual-room acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const babel = require(path.join(ROOT, 'app/src/vendor/babel-7.29.0/babel.min.js'));
const read = name => fs.readFileSync(path.join(ROOT, 'app/src', name), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function parsed(name) {
  const source = read(name), ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
  return { source, ast, text: node => source.slice(node.start, node.end) };
}
const sources = { cards: parsed('home-apartment-cards.jsx'), apartment: parsed('home-apartment.jsx'), editor: parsed('home-apartment-edit.jsx') };
const cardProgram = babel.transform(sources.cards.source, { presets: ['react'] }).code;
function owner(file, name) {
  const matches = file.ast.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
  assert.equal(matches.length, 1, 'one actual source owner: ' + name); return matches[0];
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
function cards() {
  const element = (type, props, ...children) => ({ type, props: { ...(props || {}), children: children.flat(Infinity) } });
  const context = vm.createContext({ window: {}, React: { createElement: element,
    useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
    useEffect() {}, useRef: value => ({ current: value }), useCallback: callback => callback } });
  vm.runInContext(cardProgram, context, { timeout: 1000 });
  assert.equal(typeof context.window.aptRoomLightGroups, 'function', 'real grouping function is exported');
  assert.equal(typeof context.window.AptRoomLightGroups, 'function', 'real presentation is exported');
  return context.window;
}

test('authoritative room IDs group lights without inferring unknown membership from names or position', () => {
  const api = cards(), input = freeze({ rooms: [{ id: 'den', name: 'Den' }, { id: 'kitchen', label: 'Kitchen' }], lights: [
    { id: 'one', label: 'Kitchen-looking label', room_id: 'den', power: 'on', pos: [3, 2, 1] },
    { id: 'two', label: 'Den', room_id: 'missing', power: 'off', pos: [0, 0, 0] },
    { id: 'three', label: 'No membership', room_id: null, power: 'on' },
    { id: 'four', label: 'Cook light', room_id: 'kitchen', power: 'off' },
  ] }), before = copy(input), result = api.aptRoomLightGroups(input);
  assert.deepEqual(copy(result.find(group => group.id === 'den').lights.map(light => light.id)), ['one']);
  assert.deepEqual(copy(result.find(group => group.id === 'kitchen').lights.map(light => light.id)), ['four']);
  const unassigned = result.find(group => group.id === null);
  assert.equal(unassigned.label, 'Unassigned'); assert.deepEqual(copy(unassigned.lights.map(light => light.id)), ['two', 'three']);
  assert.deepEqual(copy(result.flatMap(group => group.lights.map(light => light.id))).sort(), ['four', 'one', 'three', 'two']);
  assert.deepEqual(input, before);
});

test('grouping retains opaque light data and the first occurrence of each stable ID', () => {
  const api = cards(), first = { id: 'same', label: 'Original', room_id: 'den', power: 'on', opaque: { generation: 19 }, selected: true };
  const input = freeze({ rooms: [{ id: 'den', name: 'Den' }, { id: 'other', name: 'Other' }], lights: [first,
    { id: 'same', label: 'Duplicate', room_id: 'other', power: 'off' },
    { id: 'next', label: 'Second distinct light', room_id: 'den', power: 'off' }] });
  const groups = api.aptRoomLightGroups(input), lights = groups.flatMap(group => group.lights);
  assert.equal(lights.filter(light => light.id === 'same').length, 1);
  assert.deepEqual(copy(lights.find(light => light.id === 'same')), first);
  assert.deepEqual(copy(groups.find(group => group.id === 'den').lights.map(light => light.id)), ['same', 'next']);
});

for (const [name, powers, expected] of [
  ['all on', ['on', 'on'], 'on'], ['all off', ['off', 'off'], 'off'],
  ['known mixed', ['off', 'on'], 'mixed'], ['unknown alone', ['unknown'], 'unknown'],
  ['unknown among on', ['on', 'unknown'], 'unknown'], ['unknown among mixed', ['off', 'on', 'unknown'], 'unknown'],
  ['missing state', [undefined, 'on'], 'unknown'], ['unrecognized state', ['unavailable', 'off'], 'unknown'],
]) test('room heading reports honest power for ' + name, () => {
  const result = cards().aptRoomLightGroups({ rooms: [{ id: 'room', name: 'Room' }],
    lights: powers.map((power, index) => ({ id: 'light-' + index, label: 'Light', room_id: 'room', power })) });
  assert.equal(result.find(group => group.id === 'room').power, expected);
});

function editorEntry() {
  const file = sources.editor, component = owner(file, 'HomeApartmentEdit'), names = new Set(['entryTargetId', 'tool', 'selectedTargetId', 'dirty', 'undoRef']);
  const declarations = component.body.body.filter(node => node.type === 'VariableDeclaration' && node.declarations.some(declaration =>
    declaration.id.type === 'Identifier' ? names.has(declaration.id.name)
      : declaration.id.type === 'ArrayPattern' && declaration.id.elements.some(item => names.has(item?.name))));
  assert.equal(declarations.length, names.size, 'actual one-time entry and retained state declarations are present');
  const states = [], refs = []; let stateIndex = 0, refIndex = 0;
  const context = vm.createContext({ model: null, initialTargetId: null,
    useState(initial) { const index = stateIndex++; if (!Object.hasOwn(states, index)) states[index] = typeof initial === 'function' ? initial() : initial;
      return [states[index], next => { states[index] = typeof next === 'function' ? next(states[index]) : next; }]; },
    useRef(initial) { const index = refIndex++; return refs[index] ||= { current: initial }; },
  });
  const code = '(() => {' + declarations.map(file.text).join('\n') + '\nreturn {entryTargetId,tool,setTool,selectedTargetId,setSelectedTargetId,dirty,setDirty,undoRef};})()';
  return { render(model, initialTargetId) { stateIndex = 0; refIndex = 0; Object.assign(context, { model, initialTargetId }); return vm.runInContext(code, context, { timeout: 1000 }); } };
}

test('existing named-target entry selects only the existing target tool and target ID without mutating the model', () => {
  const model = freeze({ targets: [{ id: 'desk', name: 'Desk', pos: [1, 2, 0] }], devices: [], zones: [] }), before = copy(model);
  const state = editorEntry().render(model, 'desk');
  assert.equal(state.entryTargetId, 'desk'); assert.equal(state.tool, 'targets'); assert.equal(state.selectedTargetId, 'desk');
  assert.equal(state.dirty, false); assert.deepEqual(copy(state.undoRef.current), { stack: [], redo: [] }); assert.deepEqual(model, before);
});

for (const [name, model, id] of [
  ['unknown target', { targets: [{ id: 'desk' }] }, 'missing'],
  ['wrong identifier type', { targets: [{ id: 'desk' }] }, { id: 'desk' }],
  ['equipment registration mode', { targets: [{ id: 'desk' }], _equipment_registration: { schema_version: 1 } }, 'desk'],
]) test('editor entry refuses ' + name, () => {
  const before = copy(model), state = editorEntry().render(freeze(model), id);
  assert.equal(state.entryTargetId, null); assert.equal(state.tool, 'select'); assert.equal(state.selectedTargetId, null);
  assert.equal(state.dirty, false); assert.deepEqual(model, before);
});

test('entry navigation is consumed once and later props/model changes do not reset user tool, selection, dirty state or undo history', () => {
  const mount = editorEntry(), original = freeze({ targets: [{ id: 'desk' }, { id: 'art' }] }), first = mount.render(original, 'desk');
  first.setTool('measure'); first.setSelectedTargetId('art'); first.setDirty(true);
  first.undoRef.current.stack.push({ authored: 'unsaved model operation' }); first.undoRef.current.redo.push({ authored: 'retained redo' });
  const next = mount.render(freeze({ targets: [{ id: 'other' }], revision: 10 }), 'other');
  assert.equal(next.entryTargetId, 'desk'); assert.equal(next.tool, 'measure'); assert.equal(next.selectedTargetId, 'art'); assert.equal(next.dirty, true);
  assert.equal(next.undoRef, first.undoRef);
  assert.deepEqual(copy(next.undoRef.current), { stack: [{ authored: 'unsaved model operation' }], redo: [{ authored: 'retained redo' }] });
});

function nodes(tree) {
  const result = [];
  function visit(node) { if (!node || typeof node !== 'object') return; result.push(node); (node.props?.children || []).forEach(visit); }
  visit(tree); return result;
}
function text(tree) {
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  return (tree?.props?.children || []).map(text).join(' ');
}
for (const power of ['on', 'off', 'mixed', 'unknown']) test('actual group presentation exposes ' + power + ' and delegates native control intent', () => {
  const api = cards(), calls = [], group = { id: 'den', label: 'Den', power,
    lights: [{ id: 'one', label: 'Light one', ariaLabel: 'Select Light one', power: 'on', selected: true }] };
  const tree = api.AptRoomLightGroups({ groups: [group], onSelect: id => calls.push(['select', id]), onPower: (...args) => calls.push(['power', ...args]) });
  const all = nodes(tree), control = all.find(node => node.type === 'input' && node.props['aria-label'] === 'Den room power');
  assert.ok(control); assert.equal(control.props.type, 'checkbox'); assert.equal(control.props.checked, power === 'on');
  assert.equal(control.props.disabled, power === 'unknown'); assert.match(text(tree), new RegExp(power, 'i'));
  const native = {}; control.props.ref(native); assert.equal(native.indeterminate, ['mixed', 'unknown'].includes(power));
  const row = all.find(node => node.type === 'button' && node.props['aria-label'] === 'Select Light one');
  assert.equal(row.props['aria-current'], 'true'); row.props.onClick();
  assert.deepEqual(calls, [['select', 'one']]);
  if (!control.props.disabled) { control.props.onChange({ target: { checked: true } }); assert.deepEqual(calls[1], ['power', 'den', true]); }
});

test('unavailable power remains disabled with its reason while inspection is available', () => {
  const api = cards(), calls = [], tree = api.AptRoomLightGroups({ groups: [{ id: null, label: 'Unassigned', power: 'off',
    lights: [{ id: 'one', label: 'Light', power: 'off' }] }], disabledReason: 'Home Assistant controls unavailable',
    onSelect: id => calls.push(id), onPower: () => assert.fail('disabled control must not dispatch') });
  const all = nodes(tree); assert.equal(all.find(node => node.type === 'input').props.disabled, true);
  assert.match(text(tree), /Home Assistant controls unavailable/); all.find(node => node.type === 'button').props.onClick();
  assert.deepEqual(calls, ['one']);
});

test('actual named-aim list delegates the existing target ID and honestly represents an empty home', () => {
  const api = cards(), calls = [], tree = api.AptNamedAimList({ targets: [{ id: 'desk', name: 'Desk' }], onEdit: id => calls.push(id) });
  const edit = nodes(tree).find(node => node.type === 'button' && node.props['aria-label'] === 'Edit Desk');
  assert.ok(edit); edit.props.onClick(); assert.deepEqual(calls, ['desk']);
  const unavailable = api.AptNamedAimList({ targets: [{ id: 'desk', name: 'Desk' }], onEdit() {}, disabledReason: 'Registration cannot edit named aims' });
  assert.equal(nodes(unavailable).find(node => node.type === 'button').props.disabled, true);
  assert.match(text(unavailable), /Registration cannot edit named aims/);
  assert.match(text(api.AptNamedAimList({ targets: [], onEdit() {} })), /No named aims/);
});

const apartmentOwner = owner(sources.apartment, 'HomeApartmentLiveView');
const ownerDeclarations = new Map();
for (const node of apartmentOwner.body.body) if (node.type === 'VariableDeclaration') {
  for (const declaration of node.declarations) if (declaration.id.type === 'Identifier') ownerDeclarations.set(declaration.id.name, declaration.init);
}
function actualInitializer(name, context) {
  const initializer = ownerDeclarations.get(name); assert.ok(initializer, 'actual owner declaration: ' + name);
  return vm.runInContext('(' + sources.apartment.text(initializer) + ')', context, { timeout: 1000 });
}
function installCallback(name, context, pending = new Set()) {
  if (Object.hasOwn(context, name)) return context[name];
  const initial = ownerDeclarations.get(name); assert.ok(initial, 'actual callback declaration: ' + name);
  const fn = initial.type === 'CallExpression' && initial.callee?.name === 'useCallback' ? initial.arguments[0] : initial;
  assert.ok(['ArrowFunctionExpression', 'FunctionExpression'].includes(fn.type));
  pending.add(name);
  walk(fn, node => {
    if (node.type !== 'CallExpression' || node.callee?.type !== 'Identifier') return;
    const dependency = node.callee.name;
    if (!Object.hasOwn(context, dependency) && ownerDeclarations.has(dependency) && !pending.has(dependency)) installCallback(dependency, context, pending);
  });
  context[name] = vm.runInContext('(' + sources.apartment.text(fn) + ')', context, { timeout: 1000 }); pending.delete(name); return context[name];
}

// Use the actual existing producer's pure state transition, not a second copy
// of its merge/range logic. Three geometry and WebGL are not instantiated.
const virtualSource = parsed('home-3d/equipment-virtual-fixtures.mjs');
const producerNames = new Set(['PROFILE', 'copy', 'finite', 'VIRTUAL_LIMITS']);
const producerDeclarations = virtualSource.ast.body.filter(node => node.type === 'VariableDeclaration'
  && node.declarations.some(declaration => producerNames.has(declaration.id?.name)));
assert.equal(producerDeclarations.length, 4);
const producerContext = vm.createContext({ Den: require(path.join(ROOT, 'app/src/home-den-fixtures.js')) });
const producerNextState = vm.runInContext('(() => {' + producerDeclarations.map(virtualSource.text).join('\n')
  + '\n' + virtualSource.text(owner(virtualSource, 'nextState')) + '\nreturn nextState;})()', producerContext, { timeout: 1000 });

function callbackFixture(site = 'echo') {
  const state = { calls: [], patches: [], services: [], current: true, controlsCurrent: true, entry: null, afterService: null, afterPatch: null, producerRefuses: false };
  const frame = 'a'.repeat(64), binding = { content_revision: 'authored-victoria-r1', registration_version: 1, frame_digest: frame };
  const channel = (on, brightness) => ({ on, brightness, color_temp_kelvin: 3000 });
  const fixtureStates = new Map(['one', 'two'].map((id, index) => [id, { spotlight: channel(true, .6), downward: channel(false, .25),
    radials: Array.from({ length: 6 }, (_, number) => ({ number: number + 1, ...channel(number % 2 === 0, .1 * (number + 1)) })),
    pan_deg: 17 + index, tilt_deg: 35 + index }]));
  const targets = new Map([['one', { point: [1, 2, 0], normal: [0, 0, 1] }], ['two', null]]);
  const preview = () => ({ status: 'ready', revision: 7, digest: 'b'.repeat(64), visible: true,
    fixtures: [...fixtureStates].map(([fixture_id, value]) => ({ fixture_id, label: fixture_id, room_id: 'den', state: copy(value) })) });
  const overlay = { virtualFixturesReadback: preview, virtualFixtureInteractionDescriptor: id => ({ target: copy(targets.get(id)) }),
    setVirtualFixtureState(id, patch) {
      state.patches.push({ id, patch: copy(patch) }); if (state.producerRefuses) return { ok: false };
      fixtureStates.set(id, producerNextState(fixtureStates.get(id), patch)); state.afterPatch?.(id); return { ok: true };
    } };
  const model = { zones: [{ id: 'den', name: 'Den' }], devices: [
    { id: 'one', name: 'One', room_id: 'den', ha_entity_id: 'light.authored_a' },
    { id: 'duplicate-entity', name: 'Same physical mapping', room_id: 'den', ha_entity_id: 'light.authored_a' },
    { id: 'two', name: 'Two', room_id: 'den', ha_entity_id: 'light.authored_b' },
    { id: 'fan', name: 'Other domain', room_id: 'den', ha_entity_id: 'switch.authored_fan' },
    { id: 'unmapped', name: 'No mapping', room_id: 'den' },
  ], targets: [{ id: 'desk', name: 'Desk', pos: [1, 2, 0] }] };
  const virtualDocument = site === 'victoria' ? { binding, revision: 7, digest: 'b'.repeat(64) } : null;
  if (virtualDocument) model._equipment_registration = { authored: true };
  const engine = { siteId: site, contentRevision: 'authored-' + site + '-r1', overlay,
    sceneIdentity: { siteId: site, contentRevision: 'authored-' + site + '-r1', registrationVersion: 1, frameDigest: frame } };
  const values = { window: { ...cards(), HomeSpatialViewport: { getSelectedSiteId: () => state.selectedSite } }, model,
    selectedSiteId: site, selectedContentRevision: 'authored-' + site + '-r1', open: true, phase: 'ready', editing: false, calibCam: null,
    liveOn: false, rehearsalIsolated: false, spatialScale: 'apartment', connection: 'online', simActive: false,
    apartmentSceneControlsEnabled: true, apartmentDeviceControlsEnabled: site === 'echo', apartmentPlacementControlsEnabled: true,
    apartmentAuthorityKey: 'authored-owner', engineRef: { current: engine }, spatialLifecycleRevisionRef: { current: 5 },
    modelScopeRef: { current: { siteId: site, authorityKey: 'authored-owner', contentRevision: 'authored-' + site + '-r1', frameDigest: frame, registrationVersion: 1 } },
    workspaceAuthorityRef: { current: { key: 'authored-owner' } }, workspaceStateRef: { current: { model } }, modelSiteRef: { current: site },
    viewOpenRef: { current: true }, apartmentSceneControlsEnabledRef: { current: true }, travellingRef: { current: false }, deskTargetCaptureRef: { current: null },
    virtualFixtureDocument: virtualDocument, virtualFixtureItems: preview().fixtures, selectedVirtualFixture: null, virtualFixtureControlsOpen: false, virtualFixtureId: '', cardId: null,
    virtualFixtureStatesRef: { current: { states: new Map(), targets: new Map() } },
    statesRef: { current: { 'light.authored_a': { state: 'on' }, 'light.authored_b': { state: 'off' } } },
    getVirtualFixtureSelectionContext: () => state.current ? { engine, document: virtualDocument, preview: preview() } : null,
    isControlCurrent: () => state.controlsCurrent,
    callSvc: async (...args) => { state.services.push(copy(args)); await state.afterService?.(args); },
    setCardId: id => state.calls.push(['card', id]), setAimMode: value => state.calls.push(['aim', value]),
    setAimDestination: value => state.calls.push(['destination', value]), setEditing: value => state.calls.push(['editing', value]),
    setEditorTargetEntry: request => { state.entry = request; }, setVirtualFixturePreview: result => { state.preview = result; },
    onShowShellControls: () => state.calls.push(['show-controls']),
    setModel: () => assert.fail('navigation/power must not mutate a model'), saveModel: () => assert.fail('navigation/power must not save a model'),
  };
  state.selectedSite = site;
  const context = vm.createContext(values);
  for (const name of ['selectionEngine', 'selectionLifecycle', 'roomInspectorFrame', 'roomInspectorRegistration', 'roomLightGroups', 'roomPowerDisabledReason']) context[name] = actualInitializer(name, context);
  context.aptUnifiedRadialPowerPatch = vm.runInContext('(' + sources.apartment.text(owner(sources.apartment, 'aptUnifiedRadialPowerPatch')) + ')', context, { timeout: 1000 });
  for (const name of ['getRoomInspectorContext', 'selectRoomDevice', 'setRoomLightsPower', 'openNamedAimEditor']) installCallback(name, context);
  return { state, values, context, engine, model, fixtureStates, targets,
    select: id => context.selectRoomDevice(id), power: (room, on) => context.setRoomLightsPower(room, on), edit: id => context.openNamedAimEditor(id) };
}

test('actual Echo room row selects an existing mapped device through its existing card path only', () => {
  const f = callbackFixture(), before = copy(f.model);
  assert.equal(f.select('one'), true); assert.deepEqual(f.state.calls, [['card', 'one'], ['show-controls']]);
  f.state.calls.length = 0; assert.equal(f.select('missing'), false); assert.equal(f.select('unmapped'), false);
  assert.deepEqual(f.state.calls, []); assert.deepEqual(f.state.services, []); assert.deepEqual(f.model, before);
});

test('Echo room power sends deduplicated scalar light entity IDs and the explicit requested service', async () => {
  const f = callbackFixture(), before = copy(f.model);
  assert.equal(await f.power('den', false), true);
  assert.deepEqual(f.state.services, [['light', 'turn_off', { entity_id: 'light.authored_a' }], ['light', 'turn_off', { entity_id: 'light.authored_b' }]]);
  assert.deepEqual(f.state.patches, []); assert.deepEqual(f.model, before);
});

for (const [name, retire] of [
  ['travel starts', f => { f.values.travellingRef.current = true; }],
  ['frame changes', f => { f.values.modelScopeRef.current.frameDigest = 'c'.repeat(64); }],
  ['registration changes', f => { f.values.modelScopeRef.current.registrationVersion++; }],
  ['authority changes', f => { f.values.workspaceAuthorityRef.current.key = 'another-owner'; }],
  ['home changes', f => { f.state.selectedSite = 'victoria'; }],
  ['model is replaced', f => { f.values.workspaceStateRef.current.model = { ...f.model }; }],
  ['engine is replaced', f => { f.values.engineRef.current = { ...f.engine }; }],
  ['lifecycle retires', f => { f.values.spatialLifecycleRevisionRef.current++; }],
  ['command admission retires', f => { f.state.controlsCurrent = false; }],
]) test('an awaited room batch stops remaining Echo commands when ' + name, async () => {
  const f = callbackFixture(); f.state.afterService = async () => retire(f);
  assert.equal(await f.power('den', true), false);
  assert.deepEqual(f.state.services, [['light', 'turn_on', { entity_id: 'light.authored_a' }]]);
});

test('unknown room/state, nonboolean power and unavailable control admission send no command', async () => {
  const f = callbackFixture(); assert.equal(await f.power('missing', true), false); assert.equal(await f.power('den', 'false'), false);
  f.context.roomLightGroups[0].power = 'unknown'; assert.equal(await f.power('den', true), false);
  f.context.roomLightGroups[0].power = 'mixed'; f.context.roomPowerDisabledReason = 'Unavailable'; assert.equal(await f.power('den', false), false);
  assert.deepEqual(f.state.services, []); assert.deepEqual(f.state.patches, []);
});

test('Victoria room power uses the existing modeled patcher and preserves channel levels, temperatures and aims', async () => {
  const f = callbackFixture('victoria'), before = copy([...f.fixtureStates]), targets = copy([...f.targets]);
  assert.equal(await f.power('den', false), true); assert.equal(f.state.patches.length, 2);
  for (const [id, initial] of before) {
    const actual = f.fixtureStates.get(id); assert.equal(actual.pan_deg, initial.pan_deg); assert.equal(actual.tilt_deg, initial.tilt_deg);
    for (const key of ['spotlight', 'downward']) assert.deepEqual(copy(actual[key]), { ...initial[key], on: false });
    assert.deepEqual(copy(actual.radials), initial.radials.map(zone => ({ ...zone, on: false })));
  }
  // Exact patch admission is essential here: the real renderer setter retires
  // a saved target whenever pan/tilt keys are present, even if unchanged.
  const expectedPatch = on => ({ spotlight: { on }, downward: { on },
    radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, on })) });
  assert.deepEqual(f.state.patches, before.map(([id]) => ({ id, patch: expectedPatch(false) })));
  assert.deepEqual(copy([...f.targets]), targets); assert.deepEqual(f.state.services, []);
  assert.equal(await f.power('den', true), true);
  for (const [id, initial] of before) {
    const actual = f.fixtureStates.get(id); assert.deepEqual(copy(actual.radials), initial.radials.map(zone => ({ ...zone, on: true })));
    for (const key of ['spotlight', 'downward']) assert.deepEqual(copy(actual[key]), { ...initial[key], on: true });
  }
  assert.deepEqual(f.state.patches.slice(2), before.map(([id]) => ({ id, patch: expectedPatch(true) })));
});

test('Victoria room power restores all-zero radial levels through the existing modeled power path only', async () => {
  const f = callbackFixture('victoria');
  for (const value of f.fixtureStates.values()) for (const radial of value.radials) { radial.on = false; radial.brightness = 0; }
  const before = copy([...f.fixtureStates]);
  assert.equal(await f.power('den', true), true); assert.equal(f.state.patches.length, 2);
  for (const [id, initial] of before) {
    const actual = f.fixtureStates.get(id);
    assert.deepEqual(copy(actual.radials), initial.radials.map(radial => ({ ...radial, on: true, brightness: .5 })));
    assert.equal(actual.pan_deg, initial.pan_deg); assert.equal(actual.tilt_deg, initial.tilt_deg);
    for (const channel of ['spotlight', 'downward']) assert.deepEqual(copy(actual[channel]), { ...initial[channel], on: true });
  }
  assert.deepEqual(f.state.patches, before.map(([id]) => ({ id, patch: { spotlight: { on: true }, downward: { on: true },
    radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, on: true, brightness: .5 })) } })));
  assert.deepEqual(f.state.services, []);
});

test('Victoria refuses false success from a producer and stops after its existing admission retires', async () => {
  const refused = callbackFixture('victoria'); refused.state.producerRefuses = true;
  assert.equal(await refused.power('den', false), false); assert.equal(refused.state.patches.length, 1);
  const retired = callbackFixture('victoria'); retired.state.afterPatch = () => { retired.state.current = false; };
  assert.equal(await retired.power('den', false), false); assert.equal(retired.state.patches.length, 1); assert.deepEqual(retired.state.services, []);
});

test('named-aim Edit captures the original scope and navigates without changing the model or history', () => {
  const f = callbackFixture(), before = copy(f.model); assert.equal(f.edit('desk'), true);
  assert.equal(f.state.entry.id, 'desk'); assert.equal(f.state.entry.engine, f.engine); assert.equal(f.state.entry.siteId, 'echo');
  assert.equal(f.state.entry.authority, 'authored-owner'); assert.equal(f.state.entry.frame, 'a'.repeat(64)); assert.equal(f.state.entry.registration, 1);
  assert.equal(f.state.entry.revision, 'authored-echo-r1');
  assert.deepEqual(f.state.calls, [['aim', false], ['destination', null], ['card', null], ['editing', true], ['show-controls']]);
  assert.deepEqual(f.model, before); assert.deepEqual(f.state.services, []); assert.deepEqual(f.state.patches, []);
});

for (const [name, change, id] of [
  ['unknown target', () => {}, 'missing'], ['nonstring target ID', () => {}, { id: 'desk' }],
  ['equipment registration', f => { f.model._equipment_registration = { authored: true }; }, 'desk'],
  ['placement denied', f => { f.values.apartmentPlacementControlsEnabled = false; }, 'desk'],
  ['retired authority', f => { f.values.workspaceAuthorityRef.current.key = 'other-owner'; }, 'desk'],
]) test('named-aim navigation refuses ' + name, () => {
  const f = callbackFixture(); change(f); assert.equal(f.edit(id), false);
  assert.equal(f.state.entry, null); assert.deepEqual(f.state.calls, []); assert.deepEqual(f.state.services, []);
});

function editorInitialTarget(context) {
  const candidates = [];
  walk(apartmentOwner, node => {
    if (node.type !== 'JSXOpeningElement' || node.name?.type !== 'JSXMemberExpression'
      || node.name.object?.name !== 'window' || node.name.property?.name !== 'HomeApartmentEdit') return;
    const attribute = node.attributes.find(item => item.name?.name === 'initialTargetId');
    if (attribute?.value?.expression) candidates.push(attribute.value.expression);
  });
  assert.equal(candidates.length, 1, 'one actual editor entry prop');
  return vm.runInContext('(' + sources.apartment.text(candidates[0]) + ')', context, { timeout: 1000 });
}

test('actual editor prop admits the captured target only while its original scope remains current', () => {
  const f = callbackFixture(); assert.equal(f.edit('desk'), true); f.context.editorTargetEntry = f.state.entry;
  assert.equal(editorInitialTarget(f.context), 'desk');
});
for (const [name, retire] of [
  ['engine', f => { f.values.engineRef.current = { ...f.engine }; }],
  ['selected home', f => { f.values.selectedSiteId = 'victoria'; }],
  ['authority', f => { f.values.apartmentAuthorityKey = 'another-owner'; }],
  ['frame', f => { f.values.modelScopeRef.current.frameDigest = 'c'.repeat(64); }],
  ['registration', f => { f.values.modelScopeRef.current.registrationVersion++; }],
  ['content revision', f => { f.values.selectedContentRevision = 'authored-echo-r2'; }],
]) test('actual editor prop refuses a target request after changed ' + name, () => {
  const f = callbackFixture(); assert.equal(f.edit('desk'), true); f.context.editorTargetEntry = f.state.entry; retire(f);
  assert.equal(editorInitialTarget(f.context), null); assert.deepEqual(f.state.services, []); assert.deepEqual(f.state.patches, []);
});

test('the actual generic Setup callback clears target-specific entry without mutating or saving', () => {
  const f = callbackFixture(), before = copy(f.model); assert.equal(f.edit('desk'), true);
  installCallback('openUnifiedSetup', f.context)(); assert.equal(f.state.entry, null);
  assert.deepEqual(f.model, before); assert.deepEqual(f.state.services, []); assert.deepEqual(f.state.patches, []);
});

function retainedSelectionFixture() {
  const f = callbackFixture('victoria'), { context, state, engine } = f;
  const document = context.virtualFixtureDocument;
  state.overlaySelections = [];
  engine.overlay.virtualFixtureInteractionDescriptor = id => f.fixtureStates.has(id) ? {
    fixture_id: id, equipment_revision: document.revision, equipment_digest: document.digest, binding: copy(document.binding),
  } : null;
  engine.overlay.setVirtualFixtureSelected = id => {
    state.overlaySelections.push(id); return engine.overlay.virtualFixtureInteractionDescriptor(id);
  };
  context.virtualFixtureControlsOpen = true; context.virtualFixtureId = 'two'; context.unifiedShell = true;
  context.virtualAimGestureRef = { current: null };
  context.setVirtualFixtureId = id => state.calls.push(['fixture-id', id]);
  context.setVirtualFixtureControlsOpen = open => state.calls.push(['fixture-controls', open]);
  // Restore the actual authority/viewport predicate instead of the room-power
  // adapter's narrower currentness seam for this selection restoration proof.
  delete context.getVirtualFixtureSelectionContext;
  installCallback('getVirtualFixtureSelectionContext', context);
  installCallback('selectVirtualFixture', context);
  return f;
}

function runSelectionRestorationEffect(context) {
  const effects = apartmentOwner.body.body.flatMap(statement => {
    const call = statement.type === 'ExpressionStatement' ? statement.expression : null;
    if (call?.type !== 'CallExpression' || call.callee?.name !== 'useEffect') return [];
    let restores = false;
    walk(call.arguments[0], node => {
      if (node.type === 'CallExpression' && node.callee?.name === 'selectVirtualFixture'
          && node.arguments[1]?.type === 'ObjectExpression'
          && node.arguments[1].properties.some(property => property.key?.name === 'reveal' && property.value?.value === false)) restores = true;
    });
    return restores ? [call.arguments[0]] : [];
  });
  assert.equal(effects.length, 1, 'one actual effect synchronizes retained selection with the admitted renderer');
  return vm.runInContext('(' + sources.apartment.text(effects[0]) + ')()', context, { timeout: 1000 });
}

test('actual restoration effect rehighlights the retained fixture through the admitted callback without opening a panel', () => {
  const f = retainedSelectionFixture(); runSelectionRestorationEffect(f.context);
  assert.deepEqual(f.state.overlaySelections, ['two']); assert.deepEqual(f.state.calls, []);
  assert.equal(f.context.virtualFixtureId, 'two'); assert.equal(f.context.virtualFixtureControlsOpen, true);
  assert.deepEqual(f.state.services, []); assert.deepEqual(f.state.patches, []);
});

for (const [name, change] of [
  ['closed inspector', f => { f.context.virtualFixtureControlsOpen = false; }],
  ['empty retained ID', f => { f.context.virtualFixtureId = ''; }],
  ['unknown fixture', f => { f.context.virtualFixtureId = 'unknown'; }],
  ['retired home', f => { f.state.selectedSite = 'echo'; }],
  ['retired engine', f => { f.values.engineRef.current = { ...f.engine }; }],
  ['retired lifecycle', f => { f.values.spatialLifecycleRevisionRef.current++; }],
  ['retired authority', f => { f.values.workspaceAuthorityRef.current.key = 'another-owner'; }],
  ['retired frame', f => { f.values.modelScopeRef.current.frameDigest = 'c'.repeat(64); }],
  ['retired registration', f => { f.values.modelScopeRef.current.registrationVersion++; }],
  ['active aim gesture', f => { f.context.virtualAimGestureRef.current = { readback: () => ({ active: true, fixture_id: 'one' }) }; }],
  ['wrong canonical descriptor', f => { f.engine.overlay.virtualFixtureInteractionDescriptor = id => ({ fixture_id: id,
    equipment_revision: 7, equipment_digest: 'b'.repeat(64), binding: { ...f.context.virtualFixtureDocument.binding, frame_digest: 'd'.repeat(64) } }); }],
]) test('actual retained-selection restoration refuses ' + name + ' without output or disclosure', () => {
  const f = retainedSelectionFixture(); change(f); runSelectionRestorationEffect(f.context);
  assert.deepEqual(f.state.overlaySelections, []); assert.deepEqual(f.state.calls, []);
  assert.deepEqual(f.state.services, []); assert.deepEqual(f.state.patches, []);
});

test('ordinary selection keeps the existing default inspector reveal behavior', () => {
  const f = retainedSelectionFixture(); assert.equal(f.context.selectVirtualFixture('one'), true);
  assert.deepEqual(f.state.overlaySelections, ['one']);
  assert.deepEqual(f.state.calls, [['fixture-id', 'one'], ['fixture-controls', true], ['show-controls']]);
});
