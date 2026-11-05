// Geometry and timelines for the den simulation's real lighting brain. The geometry
// comes from the unmodified tools/den-room/brain-targets.mjs. Your sofa mark and
// registration export are used only together, because the mark names revision 3.
// Without both, a zone is sealed around the couch point and the file says so. If
// the brain finds no clear fixture, the plan's second try is a labelled geometry
// copy with an invented camera, which sets the way the reader faces.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { registerHooks } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
const THREE = pathToFileURL(path.join(ROOT, 'app/src/vendor/three-0.180.0/three.module.js')).href;
// The same vendored copy Home's import map names, so no npm install is needed.
registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'three') return { url: THREE, format: 'module', shortCircuit: true };
  const resolved = next(specifier, context);
  return resolved.url.includes('/vendor/three-') ? { ...resolved, format: 'module' } : resolved;
} });
const { buildBrainGeometry } = await import('../den-room/brain-targets.mjs');

const ZONE_HALF_M = .4;
const round = v => Math.round(v * 1000) / 1000;

function standInSofa(point, registration) {
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
    try { return { geometry: buildBrainGeometry({ sofa: couch.mark, registration: registration.doc, generatedAt: Date.now() / 1000 }).geometry, inputs }; }
    catch (error) { notes.push(`sofa mark refused by brain-targets: ${error.message}. Using its seat point with a stand-in zone.`); }
  }
  const sofa = standInSofa(couch.point, registration.doc);
  return { geometry: relabel(buildBrainGeometry({ sofa, registration: registration.doc, generatedAt: Date.now() / 1000 }).geometry, inputs), inputs };
}

function runBrain(geometryFile, scenario, seat, facing) {
  const output = execFileSync('python', [path.join(HERE, 'brain_run.py')], { encoding: 'utf8', windowsHide: true, timeout: 30000, maxBuffer: 32 * 1024 * 1024,
    input: JSON.stringify({ geometry: geometryFile, scenario, seat_m: seat, facing_xy: facing }) });
  return JSON.parse(output);
}
const litFixture = row => row.state.fixtures.find(item => item.spot.on) || null;
function summarize(timeline, seat) {
  const last = timeline.rows.at(-1), lit = timeline.rows.map(litFixture).find(Boolean), plan = last.spot_plan;
  return { scenario: timeline.scenario, label: timeline.label, events: timeline.events, schemas: timeline.schemas,
    lit: !!lit, refusal: lit ? null : last.refusal, fixture_id: lit?.fixture_id || null, target_m: lit?.spot.target_m || null,
    pan_deg: plan?.ok ? plan.pan_deg : null, tilt_deg: plan?.ok ? plan.tilt_deg : null,
    hand_from_seat_m: lit ? round(Math.hypot(...lit.spot.target_m.map((v, i) => v - seat[i]))) : null,
    facing_source: last.reader?.facing_source || null,
    person_m: last.reader ? last.reader.shoulder_mid.slice(0, 2) : timeline.scripted_reader.head_m.slice(0, 2),
    candidates: (plan?.candidates || []).map(row => ({ fixture_id: row.fixture_id, ok: row.ok, reason: row.reason })) };
}
function nearestOrigin(geometry, seat) {
  return geometry.fixtures.map(row => row.aim_origin_m).filter(Boolean)
    .sort((a, b) => Math.hypot(a[0] - seat[0], a[1] - seat[1]) - Math.hypot(b[0] - seat[0], b[1] - seat[1]))[0];
}

export function prepareBrain({ registration, couch, floorCentre, geometryDirectory, runDirectory, notes }) {
  const { geometry, inputs } = baseGeometry({ registration, couch, notes });
  const seat = geometry.zones.sofa.seat_m;
  // Scripted: person A faces the middle of the floor, or +y when seated on it.
  const toCentre = [floorCentre[0] - seat[0], floorCentre[1] - seat[1]];
  const facing = Math.hypot(...toCentre) > .1 ? toCentre : [0, 1];
  const write = (name, value) => { const file = path.join(geometryDirectory, name); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); return file; };

  let file = write('brain-geometry.json', geometry), inventedCamera = false, firstRefusal = null;
  let reading = runBrain(file, 'reading_on_couch', seat, facing);
  const first = summarize(reading, seat);
  if (!first.lit && reading.rows.at(-1).spot_plan?.ok === false) {
    firstRefusal = first;
    const camera = nearestOrigin(geometry, seat);
    if (camera) {
      inventedCamera = true;
      file = write('brain-geometry-invented-camera.json', relabel({ ...geometry, camera_m: camera }, { ...inputs, camera: 'INVENTED, at the nearest fixture' }));
      reading = runBrain(file, 'reading_on_couch', seat, facing);
    }
  }
  const notReading = runBrain(file, 'not_reading', seat, facing);
  const timelines = {};
  for (const timeline of [reading, notReading]) {
    timeline.summary = summarize(timeline, seat);
    timelines[timeline.scenario] = timeline;
    fs.writeFileSync(path.join(runDirectory, `brain-${timeline.scenario}.json`), JSON.stringify(timeline, null, 2) + '\n');
  }
  return { timelines, invented_camera: inventedCamera, first_try_refused: firstRefusal, camera_m_present: geometry.camera_m != null, seat_m: seat };
}
