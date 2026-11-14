import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';
import Core from '../app/src/home-equipment-registration.js';
import { createEchoVirtualLightingBakeDocument, createEchoVirtualLightingDocument } from '../app/src/home-3d/echo-virtual-lighting.mjs';

const renderModule = name => new URL('../app/src/home-3d/' + name, import.meta.url);
const appModule = name => new URL('../app/src/' + name, import.meta.url);
const three = appModule('vendor/three-0.180.0/three.module.js').href;
const moduleSource = url => fs.readFileSync(url, 'utf8').replace("from 'three'", 'from ' + JSON.stringify(three));
const dataModule = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const spotlight = dataModule(moduleSource(renderModule('equipment-spotlight-visual.mjs')));
const source = moduleSource(renderModule('equipment-virtual-fixtures.mjs'))
    .replace('import(`./equipment-spotlight-visual.mjs${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(spotlight) + ')')
    .replace(/import\(`\.\.\/([a-z-]+\.js)\$\{new URL\(import\.meta\.url\)\.search\}`\)/g,
        (_, name) => 'import(' + JSON.stringify(appModule(name).href) + ')');
const { createEquipmentVirtualFixtures } = await import(dataModule(source));

const pack = { siteId: 'echo', schemaVersion: 1, sceneAvailable: true, revision: 'authored-echo-r1', registrationVersion: 1,
    assets: { 'frame.json': { sha256: 'a'.repeat(64) }, 'collision.glb': { sha256: 'b'.repeat(64) } } };
const frame = { frame: 'z_up_metric_floor0', extents_m: [5, 4, 2.5] };
const light = (id, room_id, pos = [2, 2, 2.35], other = {}) => ({ id, name: 'Saved light ' + id, room_id, pos, type: 'light', ...other });
const args = devices => ({ pack, frame, digest: Core.digest, savedModel: { devices, revision: 28,
    source_kind: 'live_ha_model', source_detail: { site_id: 'echo', content_revision: pack.revision, revision: 28 } } });

test('Echo follows all 13 saved light identities, names and positions without retaining linked controls', () => {
    const saved = Array.from({ length: 12 }, (_, i) => light('mapped-' + i, 'living', [i * .3, 2, 2.35],
        { ha_entity_id: 'light.authored' }));
    saved.push(light('switch', 'hall', [1, 1, .8], { name: 'Table lamp', ha_entity_id: 'switch.authored' }));
    const devices = [...saved, light('outside', 'hall', [6, 2, 2.5]), light('unplaced', 'hall', null),
        light('camera', 'living', [2, 2, 2.35], { type: 'camera' }), light('unnamed', 'living', [2, 2, 2.35], { name: ' ' })];
    const unchanged = JSON.stringify(devices);
    const doc = createEchoVirtualLightingDocument(args(devices));
    assert.equal(JSON.stringify(devices), unchanged);
    assert.deepEqual(doc, createEchoVirtualLightingDocument(args(devices.slice().reverse())));
    assert.equal(doc.revision, 28);
    assert.equal(doc.references.provenance.source, 'saved_echo_layout');
    assert.equal(doc.equipment.filter(row => row.kind === 'fixture').length, 13);
    for (const item of saved) {
        const fixture = doc.equipment.find(row => row.id === 'echo-virtual-' + item.id);
        assert.equal(fixture.label, item.name);
        assert.equal(fixture.room_id, item.room_id);
        assert.deepEqual(fixture.position_m, item.pos);
    }
    assert.equal(doc.binding.manifest_digest, Core.digest(pack));
    assert.equal(doc.digest, Core.sealDocument(doc).digest);
    assert.equal(doc.references.confirmed, false);
    assert.doesNotMatch(JSON.stringify(doc), /ha_entity_id|light\.authored|switch\.authored/);
    assert.equal(createEchoVirtualLightingDocument(args([...saved, light('over-budget', 'hall')])), null);
    assert.equal(createEchoVirtualLightingDocument(args([light('duplicate', 'hall'), light('duplicate', 'living')])), null);
    assert.equal(createEchoVirtualLightingDocument(args([light('unplaced', 'hall', null)])), null);
    assert.equal(createEchoVirtualLightingDocument({ ...args(devices), frame: { ...frame, site: 'victoria' } }), null);
});

test('Echo refuses seed, cached, draft and cross-scene model sources', () => {
    const input = args([light('a', 'room')]);
    for (const source_kind of ['seed_model', 'simulation', 'local_draft', 'cached_live_model', 'tracker_live']) {
        assert.equal(createEchoVirtualLightingDocument({ ...input, savedModel: { ...input.savedModel, source_kind } }), null);
    }
    for (const patch of [{ site_id: 'victoria' }, { revision: 27 }, { content_revision: 'stale-r0' }, { isolated: true }]) {
        const source_detail = { ...input.savedModel.source_detail, ...patch };
        assert.equal(createEchoVirtualLightingDocument({ ...input, savedModel: { ...input.savedModel, source_detail } }), null);
    }
});

test('Echo retains virtual state through renames only while the saved origin and authority match', () => {
    const app = fs.readFileSync(appModule('home-apartment.jsx'), 'utf8');
    const start = app.indexOf('function aptRetainEchoFixtureMemory(');
    const end = app.indexOf('\nfunction ', start + 1);
    assert.ok(start >= 0 && end > start);
    const retain = vm.runInNewContext('(' + app.slice(start, end).trim() + ')');
    const memory = layoutKey => ({ layoutKey, origins: new Map(), states: new Map(), targets: new Map() });
    const previous = memory('echo-scope');
    const current = memory('echo-scope');
    const point = [2, 2, .8];
    const state = { spotlight: { brightness: .25 } };
    const target = { point: [3, 2, 0], normal: [0, 0, 1] };
    for (const id of ['renamed', 'moved', 'removed']) {
        previous.origins.set(id, point);
        previous.states.set(id, state);
        previous.targets.set(id, target);
    }
    const fixtures = [{ fixture_id: 'renamed', label: 'New name' }, { fixture_id: 'moved' }, { fixture_id: 'added' }];
    const descriptor = id => ({ display_origin_m: id === 'moved' ? [2, 2, .9] : [2, 2, .8] });
    retain(previous, current, fixtures, descriptor);
    assert.deepEqual([...current.states], [['renamed', state]]);
    assert.deepEqual([...current.targets], [['renamed', target]]);
    const other = memory('other-scope');
    retain(previous, other, fixtures, descriptor);
    assert.equal(other.states.size, 0);
    assert.equal(other.targets.size, 0);
});

test('Echo display, pick and beam originate at the saved position and accept only Spot controls', () => {
    const position = [2, 2, .8];
    const doc = createEchoVirtualLightingDocument(args([light('a', 'room', position, { ha_entity_id: 'switch.authored' })]));
    const echo = createEquipmentVirtualFixtures(new THREE.Group(), { siteId: 'echo', binding: doc.binding });
    const victoria = createEquipmentVirtualFixtures(new THREE.Group());
    try {
        assert.equal(victoria.setDocument(doc).status, 'unavailable');
        const bake = createEchoVirtualLightingBakeDocument(args([]));
        assert.deepEqual(bake.equipment, []);
        assert.equal(echo.setDocument(bake).status, 'unavailable');
        assert.equal(echo.setDocument({ ...doc, binding: { ...doc.binding, frame_digest: 'c'.repeat(64) } }).status, 'unavailable');
        const receipt = echo.setDocument(doc);
        assert.equal(receipt.status, 'ready');
        assert.equal(receipt.physical_commands, false);
        assert.equal(receipt.fixtures[0].display_only, true);
        assert.equal(receipt.fixtures[0].spot_only, true);
        assert.equal(receipt.fixtures[0].room_id, 'room');
        assert.equal(receipt.fixtures[0].state.spotlight.on, true);
        assert.equal(receipt.fixtures[0].state.downward.on, false);
        assert.equal(receipt.fixtures[0].state.radials.every(row => !row.on), true);
        const id = receipt.fixtures[0].fixture_id;
        const original = echo.interactionDescriptor(id);
        assert.deepEqual(original.display_anchor_m, position);
        assert.deepEqual(original.display_origin_m, position);
        assert.equal(original.optical_origin_m, null);
        echo.root.updateMatrixWorld(true);
        const fixture = echo.root.getObjectByName('equipment-virtual-fixture:' + id);
        const world = name => fixture.getObjectByName(name).getWorldPosition(new THREE.Vector3()).toArray();
        assert.deepEqual(world('fixture-orb-core'), position);
        assert.deepEqual(world('virtual-fixture-pick'), position);
        const beam = fixture.getObjectByName('robotic-spotlight').userData;
        assert.deepEqual(beam.beam_origin_m, position);
        assert.ok(Math.abs(beam.reach_m - position[2]) < 1e-6);
        assert.equal(echo.setFixtureState(id, { downward: { on: true } }).ok, false);
        assert.equal(echo.setFixtureState(id, { radials: [{ number: 1, on: true }] }).ok, false);
        assert.equal(echo.setFixtureState(id, { spotlight: { brightness: .25 } }).ok, true);
        assert.equal(echo.aimAtTarget(id, { point: [3, 2, 0], normal: [0, 0, 1] }).ok, true);
        assert.deepEqual(echo.interactionDescriptor(id).display_anchor_m, original.display_anchor_m);
        assert.deepEqual(beam.beam_origin_m, position);
        assert.equal(echo.restoreAim(id, original).ok, true);
        assert.equal(echo.setDocument({ ...doc, scope: { ...doc.scope, site_id: 'victoria' } }).status, 'unavailable');
    } finally {
        echo.dispose();
        victoria.dispose();
    }
});
