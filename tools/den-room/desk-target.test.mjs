/* Actual public target/Core/Three/aim consumers; authored geometry only.
 * No browser, device, camera, host or subprocess exists. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import Core from '../../app/src/home-equipment-registration.js';
import Aiming from '../../app/src/home-apartment-aiming.js';
import { createEquipmentVirtualFixtures } from '../../app/src/home-3d/equipment-virtual-fixtures.mjs';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';
import { calculateDeskAims, prepareDeskAimFile } from './desk-target.mjs';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const copy = value => JSON.parse(JSON.stringify(value));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const RAD = Math.PI / 180;
function close(actual, expected, tolerance = 1e-10) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
}
function vectorClose(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => close(value, expected[index]));
}
function unit(values) {
  const length = Math.hypot(...values);
  return values.map(value => value / length);
}
function fixture({ rotation = 0, nonplanar = false, missing = [], vertical = false, display_asset } = {}) {
  const c = Math.cos(rotation), s = Math.sin(rotation);
  const rotate = ([x, y, z]) => [c*x-s*y, s*x+c*y, z];
  const binding = { content_revision: 'authored-desk-room-1', registration_version: 2,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  let manifest, surface_ref;
  if (display_asset !== undefined) {
    manifest = { siteId: 'victoria', revision: binding.content_revision, registrationVersion: binding.registration_version,
      assets: { 'frame.json': { sha256: binding.frame_digest }, 'collision.glb': { sha256: binding.canonical_collision_digest },
        'mesh.glb': { sha256: 'e'.repeat(64) }, 'mesh.mobile.glb': { sha256: 'f'.repeat(64) } } };
    binding.manifest_digest = Core.digest(manifest);
    surface_ref = { asset_name: display_asset, asset_sha256: manifest.assets[display_asset].sha256,
      manifest_digest: binding.manifest_digest };
  }
  const refs = { frame: 'z_up_metric_floor0', units: 'm', confirmed: false,
    floor: { id: 'floor', normal: [0, 0, 1], offset_m: 0 },
    walls: [
      { id: 'u0', label: 'First authored wall', normal_xy: [c, s], offset_m: 0 },
      { id: 'v0', label: 'Second authored wall', normal_xy: [-s, c], offset_m: 0 },
      { id: 'u10', label: 'Opposite authored wall', normal_xy: [-c, -s], offset_m: -10 },
      { id: 'v8', label: 'Last authored wall', normal_xy: [s, -c], offset_m: -8 },
    ], provenance: { method: 'authored_test', evidence_ref: 'independent-literal-room-planes' } };
  const doc = Core.createDocument(binding, refs), template = copy(doc.equipment[0]);
  const uvz = [[1,1,3], [9,1,3.25], [9,7,2.8], [1,7,3.1]];
  if (vertical) uvz[0] = [5,3,3];
  doc.equipment = uvz.map(([u,v,z], index) => {
    const record = copy(template);
    record.id = `victoria-den-mount-0${index+1}`;
    record.label = `Authored socket ${index+1}`;
    record.physical_kind = 'socket';
    record.measurements = { wall_distances: [{ wall_id: 'u0', distance_m: u },
      { wall_id: 'v0', distance_m: v }], height_above_floor_m: missing.includes(index+1) ? null : z,
      method: 'tape', uncertainty_m: .01 };
    return record;
  });
  const registration = Core.sealDocument({ ...Core.normalizeDocument(doc), revision: 2 });
  assert.equal(Core.validateDocument(registration).ok, true);
  const raw = [[4,2,.75], [6,2,.75], [6,4,.75], [4,4,nonplanar ? .79 : .75]]
    .map(point => ({ point_m: rotate(point), normal_unit: [0,0,1] }));
  const target = sealRegisteredDeskTarget({ binding: registration.binding,
    equipment_ref: { revision: registration.revision, digest: registration.digest }, corners: raw, confirmed: true,
    ...(surface_ref ? { surface_ref } : {}) }, Core.digest);
  return { registration, target, raw, uvz, ...(manifest ? { manifest } : {}) };
}

test('version 2 saved display-mesh export reaches all four actual virtual fixture aims', () => {
  const legacy = fixture(), before = calculateDeskAims(legacy.target, legacy.registration);
  for (const display_asset of ['mesh.glb', 'mesh.mobile.glb']) {
    const f = fixture({ display_asset }), exported = JSON.parse(JSON.stringify(f.target));
    const result = calculateDeskAims(exported, f.registration, { manifest: f.manifest });
    assert.equal(result.fixtures.length, 4); assert.equal(result.fixtures.every(row => row.status === 'calculated'), true);
    assert.deepEqual(result.fixtures, before.fixtures, 'the existing aim solver and exact target geometry stay identical');
    assert.deepEqual(result.target_ref, { schema: 'home-registered-desk-target-2', digest: exported.digest });
    assert.deepEqual(result.target.vertices_m, f.raw.map(row => row.point_m));
    vectorClose(result.fixtures[0].direction_unit, unit([4, 2, -2.11]));
    assert.equal(result.qualification, null); assert.equal(result.physical_commands, false);
    assert.throws(() => calculateDeskAims(exported, f.registration), /manifest/);
    const changed = copy(f.manifest); changed.assets[display_asset].sha256 = '0'.repeat(64);
    assert.throws(() => calculateDeskAims(exported, f.registration, { manifest: changed }), /manifest/);
  }
});

test('version 2 file preparation privately pins the independent manifest and refuses missing or changed inputs before output', t => {
  const directory = temporary(t), f = fixture({ display_asset: 'mesh.mobile.glb' });
  const target = ownerFile(directory, 'desk.json', f.target), equipment = ownerFile(directory, 'equipment.json', f.registration);
  const manifest = ownerFile(directory, 'manifest.json', f.manifest), outputFile = path.join(directory, 'aims.json');
  assert.throws(() => prepareDeskAimFile({ targetFile: target.filename, equipmentFile: equipment.filename, outputFile }), /manifest/);
  assert.equal(fs.existsSync(outputFile), false);
  const changed = copy(f.manifest); changed.revision += '-foreign';
  const foreign = ownerFile(directory, 'foreign-manifest.json', changed);
  assert.throws(() => prepareDeskAimFile({ targetFile: target.filename, equipmentFile: equipment.filename,
    manifestFile: foreign.filename, outputFile }), /manifest/);
  assert.equal(fs.existsSync(outputFile), false);
  const summary = prepareDeskAimFile({ targetFile: target.filename, equipmentFile: equipment.filename,
    manifestFile: manifest.filename, outputFile });
  const bytes = fs.readFileSync(outputFile), result = JSON.parse(bytes);
  assert.deepEqual(result.input_files, { target_sha256: sha(target.bytes), equipment_sha256: sha(equipment.bytes), manifest_sha256: sha(manifest.bytes) });
  assert.deepEqual(result.target_ref, { schema: f.target.schema, digest: f.target.digest });
  assert.equal(summary.calculated_count, 4); assert.equal(summary.output_sha256, sha(bytes));
  assert.equal(summary.physical_commands, false); assert.deepEqual(fs.readFileSync(manifest.filename), manifest.bytes);
  assert.throws(() => prepareDeskAimFile({ targetFile: target.filename, equipmentFile: equipment.filename,
    manifestFile: manifest.filename, outputFile: manifest.filename }), /fresh_private_output_required/);
  assert.deepEqual(fs.readFileSync(manifest.filename), manifest.bytes);
});

test('actual saved four sockets and Home aiming reach an independent literal target direction', () => {
  const f = fixture(), before = copy(f), result = calculateDeskAims(f.target, f.registration);
  assert.equal(result.fixtures.length, 4);
  assert.deepEqual(result.omitted, []);
  const one = result.fixtures.find(row => row.mount_id === 'victoria-den-mount-01');
  assert.equal(one.status,'calculated');
  vectorClose(one.modeled_origin_m, [1,1,2.86]);
  vectorClose(result.target.centre_m, [5,3,.75]);
  // Literal target minus the existing modeled display origin, independently
  // calculated, rather than calling the implementation's inverse helper.
  const delta = [4,2,-2.11], distance = Math.sqrt(24.4521);
  vectorClose(one.direction_unit, delta.map(value => value / distance));
  close(one.distance_m, distance);
  close(one.preview_angles.pan_deg, Math.atan2(.8,4.4) / RAD);
  close(one.preview_angles.tilt_deg, Math.atan2(2.11,Math.sqrt(20)) / RAD);
  const preview = createEquipmentVirtualFixtures(new THREE.Group(), { binding: f.registration.binding });
  try {
    assert.equal(preview.setDocument(f.registration).status, 'ready');
    assert.equal(preview.readback().fixtures.length, 4);
    const descriptor = preview.interactionDescriptor(one.fixture_id);
    vectorClose(descriptor.display_origin_m, one.modeled_origin_m);
    const solved = Aiming.solveAim(descriptor.display_origin_m, [5,3,.75],
      { horizontal_world_reference: descriptor.horizontal_world_reference });
    assert.equal(solved.ok, true); assert.equal(solved.executable, true);
    close(solved.pan_deg, one.preview_angles.pan_deg);
    close(solved.tilt_deg, one.preview_angles.tilt_deg);
  } finally { preview.dispose(); }
  assert.deepEqual(f, before);
});

test('four nonidentity bases round-trip CCW/down-Z angles and preserve exact raw corners', () => {
  const f = fixture({ rotation: .37, nonplanar: true }), original = copy(f);
  const result = calculateDeskAims(f.target, f.registration);
  assert.equal(result.fixtures.length, 4);
  assert.deepEqual(f.target.selection.raw_corners, f.raw);
  assert.deepEqual(result.target.vertices_m, f.raw.map(row => row.point_m));
  assert.deepEqual(result.target, f.target.target);
  assert.ok(result.target.planarity.max_distance_m > 0);
  assert.equal(result.target.planarity.qualified, false);
  for (const row of result.fixtures) {
    assert.equal(row.status,'calculated');
    const matrix = row.delivery_angles.local_to_room;
    assert.equal(matrix.length, 9);
    assert.ok(Math.abs(matrix[1]) > .01); // Every fixture basis is nonidentity.
    const x = [matrix[0],matrix[3],matrix[6]], y = [matrix[1],matrix[4],matrix[7]], z = [matrix[2],matrix[5],matrix[8]];
    close(Math.hypot(...x),1); close(Math.hypot(...y),1); vectorClose(z,[0,0,1]);
    close(x.reduce((sum,v,i) => sum+v*y[i],0),0);
    close(matrix[0]*matrix[4]-matrix[1]*matrix[3],1);
    const pan = row.delivery_angles.pan_deg * RAD, tilt = row.delivery_angles.tilt_deg * RAD;
    // Independent spherical vector for CCW pan / angle away from downward-Z.
    const local = [Math.sin(tilt)*Math.cos(pan), Math.sin(tilt)*Math.sin(pan), -Math.cos(tilt)];
    const room = [0,1,2].map(axis => matrix[axis*3]*local[0] + matrix[axis*3+1]*local[1] + matrix[axis*3+2]*local[2]);
    const expected = unit(result.target.centre_m.map((value,i) => value-row.modeled_origin_m[i]));
    vectorClose(room, expected); vectorClose(row.direction_unit, expected);
    close(row.delivery_angles.pan_deg, -row.preview_angles.pan_deg);
    close(row.delivery_angles.tilt_deg, 90-row.preview_angles.tilt_deg);
    assert.equal(row.optical_origin_m, null);
    assert.equal(row.hardware_zero_mapping, null);
    assert.equal(row.physical_commands, false);
    for (const key of ['coverage','obstruction','protection']) assert.equal(row[key], 'unassessed');
  }
  assert.equal(result.qualification, null); assert.equal(result.operational_geometry, null);
  assert.equal(result.allocation, null); assert.equal(result.physical_commands, false);
  assert.equal(result.truth_role, 'calculated_virtual_aim_candidate');
  assert.deepEqual(result.target_ref, { schema: f.target.schema, digest: f.target.digest });
  const { digest, ...body } = result;
  assert.equal(digest, Core.digest(body));
  assert.deepEqual(f, original);
});

test('stale equipment or frame and rehashed forged targets refuse instead of rebinding', () => {
  const f = fixture();
  const stale = Core.sealDocument({ ...copy(f.registration), revision: 3 });
  assert.throws(() => calculateDeskAims(f.target, stale), /registered_desk_target_binding_changed/);
  const foreign = copy(f.registration); foreign.binding.frame_digest = 'd'.repeat(64);
  const changed = Core.sealDocument(foreign);
  assert.throws(() => calculateDeskAims(f.target, changed), /registered_desk_target_binding_changed/);
  const mutations = [
    value => { value.target.centre_m[0] += .25; },
    value => { value.selection.raw_corners[0].point_m[2] += .02; },
    value => { value.frame = 'invented_frame'; },
    value => { value.physical_commands = true; },
  ];
  for (const mutate of mutations) {
    const target = copy(f.target); mutate(target);
    const { digest: _, ...body } = target; target.digest = Core.digest(body);
    assert.throws(() => calculateDeskAims(target, f.registration), /registered_desk_target_changed/);
  }
  const corruptEquipment = copy(f.registration); corruptEquipment.equipment[0].position_m[0] += .1;
  assert.throws(() => calculateDeskAims(f.target, corruptEquipment), /valid_saved_equipment_required/);
});

test('a genuinely missing saved mount is omitted and never receives an invented origin', () => {
  const f = fixture({ missing: [2] });
  assert.equal(f.registration.equipment[1].position_m, null);
  const result = calculateDeskAims(f.target, f.registration);
  assert.equal(result.fixtures.length, 3);
  assert.deepEqual(result.omitted, [{ fixture_id: 'victoria-den-mount-02-virtual-fixture', reason: 'mount_position_missing' }]);
  assert.equal(result.fixtures.some(row => row.mount_id === 'victoria-den-mount-02'), false);
  const absent = fixture({ missing: [1,2,3,4] });
  assert.throws(() => calculateDeskAims(absent.target, absent.registration), /registered_virtual_fixtures_unavailable/);
});

test('one vertical target remains unavailable while the other three independently calculate', () => {
  const f = fixture({ rotation:.37, vertical:true });
  const result = calculateDeskAims(f.target,f.registration);
  assert.equal(result.fixtures.length,4); assert.deepEqual(result.omitted,[]);
  const vertical = result.fixtures.find(row => row.mount_id === 'victoria-den-mount-01');
  assert.equal(vertical.status,'unavailable');
  assert.equal(vertical.reason,'vertical_target_pan_indeterminate');
  vectorClose(vertical.modeled_origin_m,f.target.target.centre_m.map((value,i) => i === 2 ? 2.86 : value));
  assert.equal(vertical.optical_origin_m,null); assert.equal(vertical.physical_commands,false);
  for (const key of ['preview_angles','delivery_angles','direction_unit']) assert.equal(Object.hasOwn(vertical,key),false);
  const other = result.fixtures.filter(row => row.mount_id !== vertical.mount_id);
  assert.equal(other.length,3);
  for (const row of other) {
    assert.equal(row.status,'calculated');
    vectorClose(row.direction_unit,unit(result.target.centre_m.map((value,i) => value-row.modeled_origin_m[i])));
  }
  assert.equal(result.qualification,null); assert.equal(result.physical_commands,false);
});

function temporary(t) {
  const base = fs.realpathSync(os.tmpdir()), directory = fs.mkdtempSync(path.join(base,'home-static-desk-'));
  if (process.platform !== 'win32') fs.chmodSync(directory,0o700);
  t.after(() => {
    // Only delete this test's verified fresh direct child, never an input path.
    assert.equal(path.dirname(fs.realpathSync(directory)), base);
    assert.ok(path.basename(directory).startsWith('home-static-desk-'));
    fs.rmSync(directory,{ recursive:true });
  });
  return directory;
}
function ownerFile(directory, name, body) {
  const filename = path.join(directory,name), bytes = Buffer.from(JSON.stringify(body,null,2)+'\n');
  fs.writeFileSync(filename,bytes,{ flag:'wx',mode:0o600 });
  return { filename,bytes };
}

test('actual private file consumer pins exact inputs and returns coordinate-free CLI aggregate', t => {
  const directory = temporary(t), f = fixture({ rotation:.37 });
  const target = ownerFile(directory,'desk.json',f.target);
  const equipment = ownerFile(directory,'equipment.json',{ ok:true,registration:f.registration });
  const outputFile = path.join(directory,'aims.json');
  const summary = prepareDeskAimFile({ targetFile:target.filename,equipmentFile:equipment.filename,outputFile });
  const bytes = fs.readFileSync(outputFile), result = JSON.parse(bytes);
  assert.deepEqual(result.input_files,{ target_sha256:sha(target.bytes),equipment_sha256:sha(equipment.bytes) });
  assert.equal(summary.output_sha256,sha(bytes)); assert.equal(summary.digest,result.digest);
  const { digest, ...body } = result; assert.equal(digest,Core.digest(body));
  assert.deepEqual(result.target,f.target.target);
  assert.equal(summary.fixture_count,4); assert.equal(summary.omitted_count,0); assert.equal(summary.calculated_count,4);
  assert.deepEqual(Object.keys(summary).sort(), ['status','digest','output_sha256','fixture_count','omitted_count','calculated_count','qualified','physical_commands'].sort());
  // The CLI prints exactly JSON.stringify(prepareDeskAimFile(...)); inspect
  // that actual public return without creating a forbidden child process.
  const stdout = JSON.stringify(summary)+'\n';
  assert.equal(stdout.includes('centre_m'),false); assert.equal(stdout.includes('vertices_m'),false);
  assert.equal(stdout.includes('target_m'),false); assert.equal(stdout.includes('modeled_origin_m'),false);
  assert.equal(summary.status,'virtual_aim_candidate_saved');
  assert.equal(summary.qualified,false); assert.equal(summary.physical_commands,false);
  assert.deepEqual(fs.readFileSync(target.filename),target.bytes);
  assert.deepEqual(fs.readFileSync(equipment.filename),equipment.bytes);
  if (process.platform !== 'win32') assert.equal(fs.statSync(outputFile).mode & 0o077,0);
  assert.throws(() => prepareDeskAimFile({ targetFile:target.filename,equipmentFile:equipment.filename,outputFile }), /EEXIST/);
  assert.deepEqual(fs.readFileSync(outputFile),bytes);
  assert.throws(() => prepareDeskAimFile({ targetFile:target.filename,equipmentFile:equipment.filename,outputFile:target.filename }), /fresh_private_output_required/);
});

test('forged file input refuses before creating any candidate output', t => {
  const directory = temporary(t), f = fixture();
  const forged = copy(f.target); forged.target.centre_m[1] += .4;
  const { digest: _, ...body } = forged; forged.digest = Core.digest(body);
  const target = ownerFile(directory,'forged.json',forged);
  const equipment = ownerFile(directory,'equipment.json',f.registration);
  const outputFile = path.join(directory,'refused.json');
  assert.throws(() => prepareDeskAimFile({ targetFile:target.filename,equipmentFile:equipment.filename,outputFile }), /registered_desk_target_changed/);
  assert.equal(fs.existsSync(outputFile),false);
  assert.equal(fs.readdirSync(directory).length,2);
});
