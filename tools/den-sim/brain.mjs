// The den simulation's fixture geometry, from the unmodified tools/den-room/brain-targets.mjs: the four fixtures' aim
// origins and references, which hop 16's light model and hop 21's inverse kinematics read. Your sofa mark and registration
// export are used only together, because the mark names revision 3. Without both, a zone is sealed around a DECLARED
// point and the file says so. Nothing here decides: the coordinator does, on a click (click.mjs).
import fs from 'node:fs';
import path from 'node:path';
import { registerHooks } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';
import { sha256 } from './receipt.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const THREE = pathToFileURL(path.join(ROOT, 'app/src/vendor/three-0.180.0/three.module.js')).href;
// The same vendored copy Home's import map names, so no npm install is needed.
registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'three') return { url: THREE, format: 'module', shortCircuit: true };
  const resolved = next(specifier, context);
  return resolved.url.includes('/vendor/three-') ? { ...resolved, format: 'module' } : resolved;
} });
const { buildBrainGeometry } = await import('../den-room/brain-targets.mjs');

const ZONE_HALF_M = .4;

// Why a private file was refused, safe to print: a validator's reason code as it is, anything else by
// its error's name only. A JSON.parse message quotes the file's text, which can hold a coordinate.
export const privateReason = error => /^[a-z][a-z0-9_]*$/.test(String(error?.message)) ? error.message : `unreadable (${error?.name || 'Error'})`;

export function standInSofa(point, registration) {
  const [x, y, z] = point;
  const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([dx, dy]) => ({ point_m: [x + dx * ZONE_HALF_M, y + dy * ZONE_HALF_M, z], normal_unit: [0, 0, 1] }));
  return Sofa.sealSofaTarget({ binding: registration.binding, equipment_ref: { revision: registration.revision, digest: registration.digest }, corners, confirmed: true },
    { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
}
// brain-targets stamps owner-marked truth on what it builds. A stand-in must not keep that.
function relabel(geometry, den_sim) {
  const { digest: _replaced, ...body } = geometry;
  const labelled = { ...body, synthetic: true, truth_role: 'den_sim_stand_in_not_owner_marked', den_sim };
  return { ...labelled, digest: Core.digest(labelled) };
}
function baseGeometry({ registration, couch, notes }) {
  const inputs = { couch: couch.label, sockets: registration.label };
  if (registration.source === 'export' && couch.source === 'sofa_mark') {
    try { return buildBrainGeometry({ sofa: couch.mark, registration: registration.doc, generatedAt: Date.now() / 1000 }).geometry; }
    catch (error) { notes.push(`sofa mark refused by brain-targets: ${privateReason(error)}. Using a stand-in zone around its seat point.`); }
  }
  const sofa = couch.standIn || standInSofa(couch.point, registration.doc);
  return relabel(buildBrainGeometry({ sofa, registration: registration.doc, generatedAt: Date.now() / 1000 }).geometry, inputs);
}

// Page start: the geometry, written once into the folder given (private on your real scan), with the digest taken as it was
// written, which hop 16's row and every click's hop 18 and 21 rows hold it to. Returns its path, digest, rows and the seat.
export function writeGeometry({ registration, couch, directory, notes }) {
  const geometry = baseGeometry({ registration, couch, notes }), file = path.join(directory, 'brain-geometry.json');
  fs.writeFileSync(file, JSON.stringify(geometry, null, 2) + '\n');
  return { path: file, sha256: sha256(file), fixtures: geometry.fixtures, seat_m: geometry.zones.sofa.seat_m };
}
