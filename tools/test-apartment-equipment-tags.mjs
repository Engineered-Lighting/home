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
