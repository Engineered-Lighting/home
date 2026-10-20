/* Private lighting-brain-geometry-1 export for the den lighting brain (AL-1).
 * Reads the owner's private Home downloads (sofa mark, optional desk target)
 * and the saved equipment registration export. The four fixtures' aim origin
 * and horizontal reference come from the same virtual fixture model Home's
 * aimAtTarget uses, so the brain's pan/tilt check matches the renderer. Writes
 * one fresh private file; values never enter the repository and the console
 * prints only digests, ids and counts. No browser, camera, host or device. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { createEquipmentVirtualFixtures } from '../../app/src/home-3d/equipment-virtual-fixtures.mjs';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';

const need = (condition, reason) => { if (!condition) throw Error(reason); };
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const seal = { sealDesk: sealRegisteredDeskTarget, digest: Core.digest };
const CAMERA_ID = 'victoria-den-oak-d-pro-01';

function noLinks(filename) {
  let cursor = path.parse(filename).root;
  for (const part of filename.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    need(!fs.lstatSync(cursor).isSymbolicLink(), 'owner_file_link_refused');
  }
}
function readPrivate(filename, limit = 64 * 1024) {
  need(path.isAbsolute(filename), 'absolute_owner_file_required'); noLinks(filename);
  const stat = fs.lstatSync(filename);
  need(stat.isFile() && stat.size > 0 && stat.size <= limit
    && (process.platform === 'win32' || stat.uid === process.getuid() && !(stat.mode & 0o077)), 'bounded_private_owner_file_required');
  const bytes = fs.readFileSync(filename);
  return { value: JSON.parse(bytes.toString('utf8')), sha256: sha(bytes) };
}

// The desk record is checked by re-sealing its own selection: the same
// geometry and digest the selector produced. Its manifest pin is the desk
// consumer's concern (desk-target.mjs), not this export's.
function checkDesk(desk) {
  const selection = desk?.selection;
  need(selection && Array.isArray(selection.raw_corners), 'closed_desk_selection_required');
  const expected = sealRegisteredDeskTarget({ binding: desk.binding, equipment_ref: desk.equipment_ref,
    corners: selection.raw_corners, confirmed: desk.confirmed,
    ...(selection.surface_ref ? { surface_ref: selection.surface_ref } : {}) }, Core.digest);
  need(Core.canonicalJson(expected) === Core.canonicalJson(desk), 'desk_target_changed');
  return desk;
}

// Home's own descriptors for the saved registration: the values aimAtTarget uses.
export function registeredFixtureGeometry(registration) {
  need(Core.validateDocument(registration).ok, 'valid_saved_equipment_required');
  const preview = createEquipmentVirtualFixtures(new THREE.Group(), { binding: registration.binding });
  try {
    const result = preview.setDocument(registration);
    need(result.status === 'ready', 'registered_virtual_fixtures_unavailable');
    const fixtures = result.fixtures.filter(row => Sofa.FIXTURE_IDS.includes(row.fixture_id)).map(row => {
      const descriptor = preview.interactionDescriptor(row.fixture_id);
      need(descriptor?.truth_role === 'virtual_preview', 'explicit_virtual_fixture_model_required');
      return { fixture_id: row.fixture_id, aim_origin_m: descriptor.display_origin_m,
        horizontal_world_reference: descriptor.horizontal_world_reference, limits: row.limits };
    });
    const camera = registration.equipment.find(record => record.id === CAMERA_ID && record.kind === 'camera');
    return { fixtures, camera_m: camera?.position_m || null };
  } finally { preview.dispose(); }
}

// Fixture ids ordered by horizontal distance from a seat: a check on the
// near-fixture defaults that reveals no coordinates.
function nearestTwo(fixtures, seat) {
  return [...fixtures].sort((a, b) => Math.hypot(a.aim_origin_m[0]-seat[0], a.aim_origin_m[1]-seat[1])
    - Math.hypot(b.aim_origin_m[0]-seat[0], b.aim_origin_m[1]-seat[1])).slice(0, 2).map(row => row.fixture_id);
}

export function buildBrainGeometry({ sofa, desk = null, registration, nearFixtures = {}, generatedAt }) {
  need(Core.validateDocument(registration).ok, 'valid_saved_equipment_required');
  const binding = registration.binding, equipment_ref = { revision: registration.revision, digest: registration.digest };
  const checkedSofa = Sofa.validateSofaTarget(sofa, { binding, equipment_ref }, seal);
  const checkedDesk = desk ? checkDesk(desk) : null;
  const derived = registeredFixtureGeometry(registration);
  const geometry = Sofa.brainGeometryExport({ sofa: checkedSofa, desk: checkedDesk, fixtures: derived.fixtures,
    cameraM: derived.camera_m, nearFixtures, binding, equipment_ref, generatedAt }, Core.digest);
  const check = (zone, seat) => {
    if (!seat) return 'no_seat';
    const nearest = nearestTwo(geometry.fixtures, seat), chosen = geometry.zones[zone].near_fixtures;
    return nearest.every(id => chosen.includes(id)) && chosen.length === nearest.length ? 'two_nearest' : 'differs_from_two_nearest';
  };
  const near_check = { sofa: { chosen: geometry.zones.sofa.near_fixtures, nearest: nearestTwo(geometry.fixtures, geometry.zones.sofa.seat_m),
      status: check('sofa', geometry.zones.sofa.seat_m) },
    desk: { chosen: geometry.zones.desk.near_fixtures, nearest: geometry.zones.desk.seat_m ? nearestTwo(geometry.fixtures, geometry.zones.desk.seat_m) : null,
      status: check('desk', geometry.zones.desk.seat_m) } };
  return { geometry, near_check };
}

export function prepareBrainGeometryFile({ sofaFile, equipmentFile, deskFile, outputFile, nearFixtures = {}, generatedAt = Date.now() / 1000 }) {
  need(path.isAbsolute(outputFile) && ![sofaFile, equipmentFile, deskFile].includes(outputFile), 'fresh_private_output_required');
  noLinks(path.dirname(outputFile));
  const parent = fs.statSync(path.dirname(outputFile));
  need(parent.isDirectory() && (process.platform === 'win32' || parent.uid === process.getuid() && !(parent.mode & 0o077)),
    'private_output_parent_required');
  const sofa = readPrivate(sofaFile), equipment = readPrivate(equipmentFile, 1024 * 1024), desk = deskFile ? readPrivate(deskFile) : null;
  const registration = equipment.value?.ok === true ? equipment.value.registration : equipment.value;
  const { geometry, near_check } = buildBrainGeometry({ sofa: sofa.value, desk: desk?.value || null, registration, nearFixtures, generatedAt });
  const bytes = Buffer.from(JSON.stringify(geometry, null, 2) + '\n');
  const fd = fs.openSync(outputFile, 'wx', 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  return { status: 'lighting_brain_geometry_saved', schema: geometry.schema, digest: geometry.digest, output_sha256: sha(bytes),
    fixture_count: geometry.fixtures.length, camera: geometry.camera_m ? 'registered' : 'unavailable',
    desk_seat: geometry.zones.desk.seat_m ? 'desk_centre' : 'none', near_check,
    input_sha256: { sofa: sofa.sha256, equipment: equipment.sha256, ...(desk ? { desk: desk.sha256 } : {}) }, physical_commands: false };
}

const USAGE = 'usage: --sofa ABSOLUTE_SAVED_SOFA_JSON --equipment ABSOLUTE_EQUIPMENT_JSON --out FRESH_PRIVATE_JSON'
  + ' [--desk ABSOLUTE_SAVED_DESK_JSON] [--sofa-near ID,ID] [--desk-near ID,ID]';
export function parseArgs(args) {
  const flags = new Map();
  for (let i = 0; i < args.length; i += 2) {
    need(['--sofa', '--equipment', '--out', '--desk', '--sofa-near', '--desk-near'].includes(args[i])
      && typeof args[i + 1] === 'string' && !flags.has(args[i]), USAGE);
    flags.set(args[i], args[i + 1]);
  }
  need(flags.has('--sofa') && flags.has('--equipment') && flags.has('--out'), USAGE);
  const list = value => value.split(',').map(item => item.trim()).filter(Boolean);
  return { sofaFile: flags.get('--sofa'), equipmentFile: flags.get('--equipment'), outputFile: flags.get('--out'),
    ...(flags.has('--desk') ? { deskFile: flags.get('--desk') } : {}),
    nearFixtures: { ...(flags.has('--sofa-near') ? { sofa: list(flags.get('--sofa-near')) } : {}),
      ...(flags.has('--desk-near') ? { desk: list(flags.get('--desk-near')) } : {}) } };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.stdout.write(JSON.stringify(prepareBrainGeometryFile(parseArgs(process.argv.slice(2)))) + '\n'); }
  catch (error) { process.stderr.write(error.message + '\n'); process.exitCode = 1; }
}
