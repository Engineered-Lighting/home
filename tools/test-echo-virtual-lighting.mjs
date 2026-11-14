import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';
import Core from '../app/src/home-equipment-registration.js';
import { createEchoVirtualLightingDocument } from '../app/src/home-3d/echo-virtual-lighting.mjs';

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
const light = (id, room_id, pos = [2, 2, 2.35], other = {}) => ({ id, room_id, pos, type: 'light', ...other });
const args = devices => ({ pack, frame, devices, digest: Core.digest });

test('Echo uses four deterministic modeled locations from the seed without retaining linked controls', () => {
    const devices = [
        light('z', 'hall'), light('b', 'living'), light('a', 'living', [2, 2, 2.35], { ha_entity_id: 'light.authored' }),
        light('d', 'kitchen'), light('c', 'kitchen'), light('switch', 'hall', [1, 1, 2.4], { ha_entity_id: 'switch.authored' }),
        light('low', 'hall', [2, 2, .25]), light('outside', 'hall', [6, 2, 2.5]), light('unplaced', 'hall', null),
    ];
    const unchanged = JSON.stringify(devices);
    const doc = createEchoVirtualLightingDocument(args(devices));
    assert.equal(JSON.stringify(devices), unchanged);
    assert.deepEqual(doc, createEchoVirtualLightingDocument(args(devices.slice().reverse())));
    assert.deepEqual(doc.equipment.filter(row => row.kind === 'fixture').map(row => row.id),
        ['echo-virtual-a', 'echo-virtual-c', 'echo-virtual-z', 'echo-virtual-b']);
    assert.equal(doc.binding.manifest_digest, Core.digest(pack));
    assert.equal(doc.digest, Core.sealDocument(doc).digest);
    assert.equal(doc.references.confirmed, false);
    assert.doesNotMatch(JSON.stringify(doc), /ha_entity_id|light\.authored|switch\.authored/);
    assert.equal(createEchoVirtualLightingDocument(args([light('duplicate', 'hall'), light('duplicate', 'living')])), null);
    assert.equal(createEchoVirtualLightingDocument(args([light('unplaced', 'hall', null)])), null);
    assert.equal(createEchoVirtualLightingDocument({ ...args(devices), frame: { ...frame, site: 'victoria' } }), null);
});

test('Echo only admits the scoped modeled document and starts with spotlight power', () => {
    const doc = createEchoVirtualLightingDocument(args([light('a', 'room')]));
    const echo = createEquipmentVirtualFixtures(new THREE.Group(), { siteId: 'echo', binding: doc.binding });
    const victoria = createEquipmentVirtualFixtures(new THREE.Group());
    try {
        assert.equal(victoria.setDocument(doc).status, 'unavailable');
        assert.equal(echo.setDocument({ ...doc, binding: { ...doc.binding, frame_digest: 'c'.repeat(64) } }).status, 'unavailable');
        const receipt = echo.setDocument(doc);
        assert.equal(receipt.status, 'ready');
        assert.equal(receipt.physical_commands, false);
        assert.equal(receipt.fixtures[0].display_only, true);
        assert.equal(receipt.fixtures[0].state.spotlight.on, true);
        assert.equal(receipt.fixtures[0].state.downward.on, false);
        assert.equal(receipt.fixtures[0].state.radials.every(row => !row.on), true);
        const id = receipt.fixtures[0].fixture_id;
        const original = echo.interactionDescriptor(id);
        assert.deepEqual(original.display_anchor_m, [2, 2, 2.35]);
        assert.equal(echo.aimAtTarget(id, { point: [3, 2, 0], normal: [0, 0, 1] }).ok, true);
        assert.deepEqual(echo.interactionDescriptor(id).display_anchor_m, original.display_anchor_m);
        assert.equal(echo.setDocument({ ...doc, scope: { ...doc.scope, site_id: 'victoria' } }).status, 'unavailable');
    } finally {
        echo.dispose();
        victoria.dispose();
    }
});
