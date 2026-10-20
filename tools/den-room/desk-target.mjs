/* Consume an owner-selected desktop using the SAME registered virtual fixture
 * model and aiming solver as Home. No camera, model or fixture transport. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import Core from '../../app/src/home-equipment-registration.js';
import Aiming from '../../app/src/home-apartment-aiming.js';
import { createEquipmentVirtualFixtures } from '../../app/src/home-3d/equipment-virtual-fixtures.mjs';
import { validateRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';

const clone = value => JSON.parse(JSON.stringify(value));
const need = (value, reason) => { if (!value) throw TypeError(reason); };
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

export function calculateDeskAims(target, registration, { manifest } = {}) {
  need(Core.validateDocument(registration).ok, 'valid_saved_equipment_required');
  const record = validateRegisteredDeskTarget(target, { binding: registration.binding,
    equipment_ref: { revision: registration.revision, digest: registration.digest }, manifest }, Core.digest);
  const preview = createEquipmentVirtualFixtures(new THREE.Group(), { binding: registration.binding });
  try {
    preview.setDocument(registration);
    const initial = preview.readback();
    need(initial.status === 'ready', 'registered_virtual_fixtures_unavailable');
    const fixtures = initial.fixtures.map(item => {
      const descriptor = preview.interactionDescriptor(item.fixture_id);
      need(descriptor?.truth_role === 'virtual_preview', 'explicit_virtual_fixture_model_required');
      const aim = Aiming.solveAim(descriptor.display_origin_m, record.target.centre_m,
        { horizontal_world_reference: descriptor.horizontal_world_reference, limits: descriptor.limits });
      if (!aim.ok || aim.pan_indeterminate || !aim.executable) return {
        fixture_id: item.fixture_id, mount_id: descriptor.mount_id, status: 'unavailable',
        reason: aim.pan_indeterminate ? 'vertical_target_pan_indeterminate' : 'desk_aim_outside_virtual_limits',
        modeled_origin_m: descriptor.display_origin_m, optical_origin_m: null,
        target_m: clone(record.target.centre_m), physical_commands: false };
      // Exercise the same surface-target consumer, rather than duplicate its
      // rendered origin, sign conventions or state update.
      const applied = preview.aimAtTarget(item.fixture_id, { point: record.target.centre_m,
        normal: record.target.normal_unit, kind: 'registered_mesh', surface_id: 'owner-selected-desktop' },
      { expectedGeneration: descriptor.generation });
      need(applied.ok, 'registered_virtual_desk_aim_refused');
      const actual = applied.descriptor;
      need(Math.abs(actual.state.pan_deg-aim.pan_deg) < 1e-9
        && Math.abs(actual.state.tilt_deg-aim.tilt_deg) < 1e-9, 'virtual_aim_solver_disagreement');
      const forward = descriptor.horizontal_world_reference, left = [-forward[1],forward[0],0];
      return { fixture_id: item.fixture_id, mount_id: descriptor.mount_id, status: 'calculated',
        display_anchor_m: descriptor.display_anchor_m, modeled_origin_m: descriptor.display_origin_m,
        optical_origin_m: null, origin_role: descriptor.origin_role,
        horizontal_world_reference: forward,
        // Existing Apartment aim uses clockwise pan and tilt below horizontal.
        preview_angles: { convention: 'apartment_clockwise_pan_tilt_below_horizontal_degrees',
          pan_deg: aim.pan_deg, tilt_deg: aim.tilt_deg },
        // Proper right-handed basis for the current delivery convention.
        delivery_angles: { convention: 'counterclockwise_pan_tilt_from_down_minus_Z_degrees',
          local_to_room: [forward[0],left[0],0,forward[1],left[1],0,0,0,1],
          pan_deg: -aim.pan_deg, tilt_deg: 90-aim.tilt_deg },
        target_m: clone(record.target.centre_m), direction_unit: Aiming.directionFromAim(aim.pan_deg,aim.tilt_deg,
          { horizontal_world_reference: forward }), distance_m: aim.distance_m,
        coverage: 'unassessed', obstruction: 'unassessed', protection: 'unassessed',
        hardware_zero_mapping: null, physical_commands: false };
    });
    const body = { schema: 'home-registered-desk-aim-candidate-1', version: 1,
      scope: clone(record.scope), binding: clone(record.binding), equipment_ref: clone(record.equipment_ref),
      target_ref: { schema: record.schema, digest: record.digest }, target: clone(record.target), fixtures,
      omitted: clone(initial.omitted), truth_role: 'calculated_virtual_aim_candidate',
      qualification: null, allocation: null, operational_geometry: null, physical_commands: false };
    return { ...body, digest: Core.digest(body) };
  } finally { preview.dispose(); }
}

function noLinks(filename) {
  let cursor = path.parse(filename).root;
  for (const part of filename.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor,part);
    need(!fs.lstatSync(cursor).isSymbolicLink(), 'owner_file_link_refused');
  }
}
function readPrivate(filename) {
  need(path.isAbsolute(filename), 'absolute_owner_file_required'); noLinks(filename);
  const stat = fs.lstatSync(filename);
  need(stat.isFile() && stat.nlink === 1 && stat.size > 0 && stat.size <= 64*1024
    && (process.platform === 'win32' || stat.uid === process.getuid() && !(stat.mode & 0o077)),
  'bounded_private_owner_file_required');
  const fd = fs.openSync(filename,fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const opened = fs.fstatSync(fd);
    need(opened.ino === stat.ino && opened.dev === stat.dev, 'owner_file_replaced');
    const bytes = fs.readFileSync(fd), after = fs.fstatSync(fd);
    need(bytes.length === stat.size && after.size === stat.size && after.mtimeMs === stat.mtimeMs,
      'owner_file_changed');
    return { value: JSON.parse(bytes.toString('utf8')), sha256: sha(bytes) };
  } finally { fs.closeSync(fd); }
}
export function prepareDeskAimFile({ targetFile, equipmentFile, outputFile, manifestFile }) {
  need(path.isAbsolute(outputFile) && ![targetFile,equipmentFile,manifestFile].includes(outputFile), 'fresh_private_output_required');
  noLinks(path.dirname(outputFile));
  const parent = fs.statSync(path.dirname(outputFile));
  need(parent.isDirectory() && (process.platform === 'win32' || parent.uid === process.getuid()
    && !(parent.mode & 0o077)), 'private_output_parent_required');
  const target = readPrivate(targetFile), equipment = readPrivate(equipmentFile);
  const manifest = manifestFile === undefined ? null : readPrivate(manifestFile);
  const candidate = calculateDeskAims(target.value, equipment.value.ok === true ? equipment.value.registration : equipment.value,
    { manifest: manifest?.value });
  const body = { ...candidate, input_files: { target_sha256: target.sha256, equipment_sha256: equipment.sha256,
    ...(manifest ? { manifest_sha256: manifest.sha256 } : {}) } };
  delete body.digest;
  const result = { ...body, digest: Core.digest(body) }, bytes = Buffer.from(JSON.stringify(result,null,2)+'\n');
  const fd = fs.openSync(outputFile,'wx',0o600);
  try { fs.writeFileSync(fd,bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  // The file is a private calculation artifact, not an admission/publication.
  return { status: 'virtual_aim_candidate_saved', digest: result.digest, output_sha256: sha(bytes),
    fixture_count: result.fixtures.length, omitted_count: result.omitted.length,
    calculated_count: result.fixtures.filter(fixture => fixture.status === 'calculated').length,
    qualified: false, physical_commands: false };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    need((args.length === 6 || args.length === 8 && args[6] === '--manifest')
      && args[0] === '--target' && args[2] === '--equipment' && args[4] === '--out',
      'usage: --target ABSOLUTE_SAVED_DESK_JSON --equipment ABSOLUTE_EQUIPMENT_JSON --out FRESH_PRIVATE_JSON [--manifest ABSOLUTE_MANIFEST_JSON]');
    process.stdout.write(JSON.stringify(prepareDeskAimFile({ targetFile: args[1], equipmentFile: args[3], outputFile: args[5],
      ...(args.length === 8 ? { manifestFile: args[7] } : {}) }))+'\n');
  } catch (error) { process.stderr.write(error.message+'\n'); process.exitCode = 1; }
}
