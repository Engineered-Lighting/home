#!/usr/bin/env node
// Echo fixture glyphs (APT-L04 › glyphs) on the real marker overlay.
// Synthetic devices only: no household data, network, browser or command.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createOverlay } from '../app/src/home-3d/markers.js';
import { createEchoGlyph, echoGlyphKind, echoGlyphTint, ECHO_GLYPH_NAMES } from '../app/src/home-3d/apartment-echo-glyphs.mjs';
import { kelvinColor } from '../app/src/home-3d/apartment-light-pools.mjs';

// Fixed before the first run (2026-10-20); never raised to make a run pass.
const DEADLINE_MS = 5000;

const fakeContext = new Proxy({ createRadialGradient: () => ({ addColorStop() {} }), measureText: () => ({ width: 20 }) },
  { get: (target, key) => key in target ? target[key] : () => {}, set: () => true });
globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => fakeContext }) };

const DEVICES = [
  { id: 'syn-ceiling', type: 'light', name: 'Synthetic ceiling', ha_entity_id: 'light.syn_ceiling', pos: [1, 1, 2.4], height_preset: 'ceiling' },
  { id: 'syn-high', type: 'light', name: 'Synthetic high light', ha_entity_id: 'light.syn_high', pos: [2, 1, 2.1] },
  { id: 'syn-plug', type: 'light', name: 'Synthetic plug lamp', ha_entity_id: 'switch.syn_plug', pos: [3, 1, .8] },
  { id: 'syn-engineered', type: 'light', name: 'Synthetic engineered', ha_entity_id: 'light.syn_engineered', pos: [1, 3, 2.24],
    height_preset: 'ceiling', fixture_kind: 'engineered_gimbal_v1' },
  { id: 'syn-floor', type: 'light', name: 'Synthetic floor lamp', ha_entity_id: 'light.syn_floor', pos: [3, 3, 1.2] },
  { id: 'syn-speaker', type: 'speaker', name: 'Synthetic speaker', pos: [2, 2, .5] },
];
const glyphOf = overlay => id => overlay.markersById.get(id).group.getObjectByName(ECHO_GLYPH_NAMES.root) || null;

test('each Echo light kind draws its DIRECTION glyph; other devices keep their marker', { timeout: DEADLINE_MS }, () => {
  assert.deepEqual(DEVICES.map(echoGlyphKind), ['ceiling', 'ceiling', 'plug', 'ceiling', null, null]);
  assert.equal(echoGlyphKind({ type: 'light', ha_entity_id: 'light.x', pos: null }), null, 'no position, no glyph');
  const overlay = createOverlay(new THREE.Group()), glyph = glyphOf(overlay);
  overlay.setDevices(DEVICES);
  for (const device of DEVICES) {
    const marker = overlay.markersById.get(device.id), drawn = glyph(device.id), kind = echoGlyphKind(device);
    assert.equal(!!drawn, !!kind, device.id);
    assert.equal(marker.core.visible, !kind, device.id + ': the glyph replaces the marker body');
    assert.equal(marker.ghost.visible, !kind, device.id + ': and its x-ray copy');
    if (!drawn) continue;
    assert.equal(drawn.userData.glyph_kind, kind);
    assert.deepEqual(drawn.getWorldPosition(new THREE.Vector3()).toArray(), device.pos, 'centred on the device position');
  }
  const ceiling = glyph('syn-ceiling');
  assert.equal(ceiling.getObjectByName(ECHO_GLYPH_NAMES.disc).geometry.parameters.radius, .075);
  assert.deepEqual([ceiling.getObjectByName(ECHO_GLYPH_NAMES.rim).geometry.parameters.innerRadius,
    ceiling.getObjectByName(ECHO_GLYPH_NAMES.rim).geometry.parameters.outerRadius], [.075, .09]);
  assert.equal(ceiling.getObjectByName(ECHO_GLYPH_NAMES.engineered), undefined, 'only an Engineered profile adds the cylinder');
  const engineered = glyph('syn-engineered'), body = engineered.getObjectByName(ECHO_GLYPH_NAMES.engineered);
  assert.deepEqual([body.geometry.type, body.geometry.parameters.radiusTop, body.geometry.parameters.height], ['CylinderGeometry', .04, .1]);
  assert.ok(body.position.z < 0, 'the engineered body hangs below the disc');
  const tag = engineered.getObjectByName(ECHO_GLYPH_NAMES.simulated);
  assert.equal(tag.userData.text, 'simulated'); assert.equal(engineered.userData.engineered, 'simulated');
  assert.equal(glyph('syn-plug').getObjectByName(ECHO_GLYPH_NAMES.plug).geometry.parameters.radius, .07);
});

test('glyphs never change picking: same pick spheres, and no glyph part is a hit target', { timeout: DEADLINE_MS }, () => {
  const overlay = createOverlay(new THREE.Group());
  overlay.setDevices(DEVICES);
  const picks = overlay.pickObjects();
  assert.equal(picks.length, DEVICES.length);
  for (const pick of picks) {
    assert.equal(pick.geometry.parameters.radius, .18); assert.deepEqual(pick.position.toArray(), [0, 0, 0]);
  }
  overlay.overlayRoot.updateMatrixWorld(true);
  for (const device of DEVICES.filter(echoGlyphKind)) {
    const from = new THREE.Vector3(...device.pos).add(new THREE.Vector3(.7, -.5, 1.2));
    const ray = new THREE.Raycaster(from, new THREE.Vector3(...device.pos).sub(from).normalize());
    assert.deepEqual(ray.intersectObject(glyphOf(overlay)(device.id), true), [], device.id + ': the glyph is never hit');
    assert.equal(ray.intersectObject(overlay.markersById.get(device.id).pick, false).length, 1, device.id + ': the pick sphere still is');
  }
});

test('reported state tints the disc by colour temperature and brightness and the plug warm; off is grey', { timeout: DEADLINE_MS }, () => {
  const overlay = createOverlay(new THREE.Group()), glyph = glyphOf(overlay);
  overlay.setDevices(DEVICES);
  const disc = glyph('syn-ceiling').getObjectByName(ECHO_GLYPH_NAMES.disc), plug = glyph('syn-plug').getObjectByName(ECHO_GLYPH_NAMES.plug);
  const srgb = material => material.color.getStyle(THREE.SRGBColorSpace);
  const offDisc = srgb(disc.material);
  overlay.setDeviceState('syn-ceiling', { state: 'on', attributes: { brightness: 255, color_temp_kelvin: 2700 } });
  const warmFull = srgb(disc.material);
  overlay.setDeviceState('syn-ceiling', { state: 'on', attributes: { brightness: 64, color_temp_kelvin: 2700 } });
  const warmDim = srgb(disc.material);
  overlay.setDeviceState('syn-ceiling', { state: 'on', attributes: { brightness: 255, color_temp_kelvin: 6000 } });
  const cool = srgb(disc.material);
  overlay.setDeviceState('syn-ceiling', { state: 'off', attributes: {} });
  assert.equal(srgb(disc.material), offDisc, 'off returns to the dim disc');
  assert.notEqual(warmFull, warmDim, 'brightness changes the tint'); assert.notEqual(warmFull, cool, 'colour temperature changes the tint');
  const [r, g, b] = echoGlyphTint('ceiling', { state: 'on', attributes: { brightness: 255, color_temp_kelvin: 2700 } });
  assert.ok(r > g && g > b, 'a 2700 K disc reads warm');
  assert.deepEqual(echoGlyphTint('ceiling', null), [.18, .18, .18], 'unknown state reads off');
  const white = kelvinColor(6000), cold = echoGlyphTint('ceiling', { state: 'on', attributes: { brightness: 255, color_temp_kelvin: 6000 } });
  assert.ok(Math.abs(cold[2] - Math.min(1, .18 + white[2] * (.45 + 100 / 160))) < 1e-12, 'the prototype curve: .18 + white × (.45 + bri/160)');
  const offPlug = srgb(plug.material);
  overlay.setDeviceState('syn-plug', { state: 'on', attributes: {} });
  assert.notEqual(srgb(plug.material), offPlug, 'the plug glows when on');
  const [pr, pg, pb] = echoGlyphTint('plug', { state: 'on', attributes: {} });
  assert.ok(pr > pg && pg > pb, 'a plug with no colour temperature glows warm (2700 K)');
});

test('glyphs are released with the markers they belong to', { timeout: DEADLINE_MS }, () => {
  const overlay = createOverlay(new THREE.Group());
  overlay.setDevices(DEVICES);
  const released = new Set(), owned = new Set();
  for (const device of DEVICES.filter(echoGlyphKind)) glyphOf(overlay)(device.id).traverse(node => {
    for (const item of [node.geometry, node.material, node.material?.map].filter(Boolean)) {
      owned.add(item); item.addEventListener('dispose', () => released.add(item));
    }
  });
  overlay.setDevices([]);
  assert.equal(released.size, owned.size); assert.equal(overlay.markersById.size, 0);
  const lone = createEchoGlyph(DEVICES[3]), parent = new THREE.Group(); parent.add(lone.root);
  lone.dispose(); assert.equal(parent.children.length, 0);
  assert.equal(createEchoGlyph(DEVICES[4]), null);
});
