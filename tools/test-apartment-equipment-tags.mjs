#!/usr/bin/env node
// Equipment tag sprites and the in-room HTML labels: a mount the HTML layer
// names hides its canvas tag; cameras and unlabelled equipment keep theirs.
// Synthetic devices only: no household data, network, browser or command.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createOverlay } from '../app/src/home-3d/markers.js';

// Fixed before the first run (2026-10-20); never raised to make a run pass.
const DEADLINE_MS = 5000;
const fakeContext = new Proxy({ createRadialGradient: () => ({ addColorStop() {} }), measureText: () => ({ width: 20 }) },
  { get: (target, key) => key in target ? target[key] : () => {}, set: () => true });
globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => fakeContext }) };

const equipment = (id, kind, extra = {}) => ({ id, type: kind === 'camera' ? 'camera' : 'other', pos: [1, 1, 2.3],
  equipment_record: { id, kind, label: 'Synthetic ' + id, physical_kind: kind === 'mount' ? 'socket' : undefined, ...extra } });
const DEVICES = [equipment('syn-mount-1', 'mount'), equipment('syn-mount-2', 'mount'),
  equipment('syn-camera', 'camera', { orientation: { direction_unit: [1, 0, 0] } }), { id: 'syn-light', type: 'light', pos: [2, 2, 2.4] }];
const shown = overlay => Object.fromEntries([...overlay.markersById].filter(([, marker]) => marker.tag).map(([id, marker]) => [id, marker.tag.visible]));

test('a mount named by an HTML label hides its tag; cameras and unnamed equipment keep theirs', { timeout: DEADLINE_MS }, () => {
  const overlay = createOverlay(new THREE.Group());
  overlay.setDevices(DEVICES);
  assert.deepEqual(shown(overlay), { 'syn-mount-1': true, 'syn-mount-2': true, 'syn-camera': true }, 'every equipment tag shows by default');
  assert.equal(overlay.markersById.get('syn-light').tag, null, 'a device without equipment has no tag');
  assert.deepEqual(overlay.setRoomLabelledEquipment(['syn-mount-1', 7, null]), ['syn-mount-1']);
  assert.deepEqual(shown(overlay), { 'syn-mount-1': false, 'syn-mount-2': true, 'syn-camera': true });
  assert.equal(overlay.markersById.get('syn-mount-1').tag.name, 'equipment-tag');
  overlay.setDevices(DEVICES);
  assert.deepEqual(shown(overlay), { 'syn-mount-1': false, 'syn-mount-2': true, 'syn-camera': true }, 'a rebuilt marker keeps the rule');
  assert.ok(overlay.markersById.get('syn-camera').group.getObjectByName('equipment-direction-arrow').visible, 'the camera keeps its direction arrow');
  overlay.setRoomLabelledEquipment([]);
  assert.deepEqual(shown(overlay), { 'syn-mount-1': true, 'syn-mount-2': true, 'syn-camera': true }, 'without HTML labels every tag returns');
  assert.equal(overlay.pickObjects().length, DEVICES.length, 'picks are unchanged');
});

test('where a fixture is drawn, the socket marker under it draws nothing; its pick and tag stay, and it returns with the fixtures hidden', { timeout: DEADLINE_MS }, async () => {
  const { createRequire } = await import('node:module');
  const Core = createRequire(import.meta.url)('../app/src/home-equipment-registration.js');
  const { authoredDocument } = await import('./fixtures/apartment-home-shell-fixtures.mjs');
  const binding = { content_revision: 'authored-tags-r1', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  const document = authoredDocument(binding, Core), model = Core.toModel(document);
  const sockets = model.devices.filter(device => device.equipment_record?.kind === 'mount' && Array.isArray(device.pos));
  assert.ok(sockets.length >= 2, 'the authored room has positioned sockets');
  // a device that isn't registration equipment, at the first socket's place, keeps its marker
  const devices = [...model.devices.filter(device => Array.isArray(device.pos)), { id: 'syn-plain-light', type: 'light', pos: [...sockets[0].pos] }];
  const overlay = createOverlay(new THREE.Group());
  overlay.setDevices(devices);
  const drawn = () => Object.fromEntries([...overlay.markersById].map(([id, marker]) => [id, marker.body.visible]));
  const picks = overlay.pickObjects().length;
  assert.ok(Object.values(drawn()).every(Boolean), 'with no fixtures, every marker draws');
  assert.equal(overlay.setVirtualFixtures(document).status, 'ready');
  const shown = drawn();
  for (const socket of sockets) assert.equal(shown[socket.id], false, socket.id + ': the fixture stands here, so its box and hairline aren’t drawn');
  assert.equal(shown['syn-plain-light'], true, 'a marker that is not registration equipment still draws');
  assert.equal(overlay.pickObjects().length, picks, 'every marker keeps its pick');
  for (const socket of sockets) {
    const marker = overlay.markersById.get(socket.id);
    assert.equal(marker.tag.visible, true, 'its tag follows the label rule, not this one');
    assert.equal(marker.pick.parent, marker.group, 'the pick is outside the hidden body');
  }
  overlay.setDevices(devices);
  assert.deepEqual(drawn(), shown, 'a rebuilt marker keeps the rule');
  overlay.setVirtualFixturesVisible(false);
  assert.ok(Object.values(drawn()).every(Boolean), 'with the fixtures hidden (the survey editor), every marker draws again');
  overlay.setVirtualFixturesVisible(true);
  assert.deepEqual(drawn(), shown);
  overlay.setVirtualFixtures(null);
  assert.ok(Object.values(drawn()).every(Boolean), 'without fixtures, every marker draws');
  // A measured mount offset puts the fixture 12 cm below its socket: both markers it stands for still draw nothing.
  const measured = JSON.parse(JSON.stringify(document));
  const record = measured.equipment.find(item => item.kind === 'fixture' && item.mounted_at_ref === sockets[0].id);
  const mount = measured.equipment.find(item => item.id === sockets[0].id);
  Object.assign(record, { mount_offset_m: [0, 0, -.12], position_m: [mount.position_m[0], mount.position_m[1], mount.position_m[2] - .12],
    position_status: 'measured' });
  assert.equal(overlay.setVirtualFixtures(measured).status, 'ready');
  assert.deepEqual(overlay.virtualFixtureInteractionDescriptor(record.id).display_anchor_m, record.position_m, 'the fixture stands at its measured place');
  overlay.setDevices([...devices, { ...sockets[0], id: record.id, type: 'light', pos: record.position_m, equipment_record: record }]);
  assert.equal(drawn()[sockets[0].id], false, 'its socket’s box and hairline stay hidden away from it');
  assert.equal(drawn()[record.id], false, 'and so does its own record’s ball');
  assert.equal(drawn()['syn-plain-light'], true);
});
