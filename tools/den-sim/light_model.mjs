// Hop 16: the light model (plan, hop 16). Built at page start, after couch_to_room.mjs and before any picture, and the
// same file for every take: Home's own beam formula from each fixture's aim origin to the one spot, through Home's
// lit-model.mjs, unchanged. Stage-1-shaped, read by den-core and den-verify alike, marked fake: true, so den-core takes
// it to fake_light and never to den-light. Its unit is relative: modeled lux on assumed lumens, never lx. The file holds
// coordinates, so it stays private beside the fit; this module returns digests, labels and whole modeled values only.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { LIT_ASSUMED_LUMENS, LIT_DIMMING_CURVE, LIT_LUMEN_SOURCE, candelaFromLumens, litDimmingFraction, spotHalfAngle } from '../../app/src/home-3d/lit-model.mjs';
import { SPOT_ID, SURFACE_ID } from './couch_to_room.mjs';

export const VERSION = 'lm-den-sim-1', UNIT = 'relative', CCT_K = 2700, BEAM_FWHM_DEG = 20;   // the 20 degree beam is the datasheet's
// Step 3: the solve's own brightness values (the gate's proposed 9 steps and the look's 200), its 255 read as Hue's full 254,
// so a solved level reads Home's dimming curve exactly.
export const LEVELS = [1, 28, 57, 85, 113, 142, 170, 198, 200, 227, 254];
const FULL = 254, UP = [0, 0, 1];
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const vec = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
const finite = value => Array.isArray(value) ? value.every(finite) : value && typeof value === 'object' ? Object.values(value).every(finite)
  : typeof value !== 'number' || Number.isFinite(value);

// Step 2: the fixture at full, aimed at the point, so the cone's falloff is 1, on the point's upward normal.
export function spotValue(origin, point, lumens = LIT_ASSUMED_LUMENS.spot) {
  const d = point.map((v, i) => v - origin[i]), d2 = d[0] * d[0] + d[1] * d[1] + d[2] * d[2], r = Math.sqrt(d2);
  return r > 0 ? candelaFromLumens(lumens, spotHalfAngle(BEAM_FWHM_DEG)) * Math.max(0, -d[2] / r) / Math.max(d2, .01) : 0;
}

// F1 to F4 in the geometry's order, the startup line's numbering, to Home's fixture ids: read at run time, never written down.
export function channelTable(geometry) {
  const rows = geometry?.fixtures;
  if (!Array.isArray(rows) || rows.length !== 4 || new Set(rows.map(row => row?.fixture_id)).size !== 4) throw Error('geometry_fixtures_incomplete');
  return rows.map((row, i) => ({ channel: `F${i + 1}.spot`, key: `F${i + 1}`, fixture_id: row.fixture_id }));
}

// The model, in the contract's shape. fixtures: the geometry's rows, in order; lumens is a parameter only so a test can change it.
export function lightModel({ bookPoint, fixtures, lumens = LIT_ASSUMED_LUMENS.spot, sources = {} }) {
  if (!vec(bookPoint)) throw Error('book_point_missing');
  if (!Array.isArray(fixtures) || fixtures.length !== 4) throw Error('geometry_fixtures_incomplete');
  const model = { version: VERSION, fake: true, basis_unit: UNIT, spot_model: null, glare: null, channels: [], cct: {}, dimming: {}, basis: {},
    sample_points: [{ point_id: SPOT_ID, kind: 'task', xyz: bookPoint, normal: UP }], surfaces: { [SPOT_ID]: [SPOT_ID] },
    aim_points: { [SPOT_ID]: { point_m: bookPoint, normal: UP } }, fixtures: {},
    provenance: { method: "Home's lit-model.mjs: candelaFromLumens(lumens, spotHalfAngle(beam)) x max(0, cos incidence) / max(d^2, 0.01), normal up, at full, aimed at the spot",
      lumens: { value: lumens, status: 'assumed', source: LIT_LUMEN_SOURCE.summary, decision: LIT_LUMEN_SOURCE.decision },
      beam_fwhm_deg: { value: BEAM_FWHM_DEG, status: 'datasheet' }, dimming: { status: LIT_DIMMING_CURVE.status, curve: LIT_DIMMING_CURVE.name, levels: "the solve's brightness values" },
      cct_k: { value: CCT_K, status: 'assumed', note: 'one temperature' }, aim_points: { status: 'stated rule', ...sources.spot }, fixtures: sources.fixtures || { status: 'unknown' },
      not_modeled: ['occlusion', 'bounce', 'daylight', 'bulbs'] } };
  fixtures.forEach((row, i) => {
    if (!vec(row?.aim_origin_m) || !vec(row.horizontal_world_reference)) throw Error('fixture_geometry_invalid');
    const key = `F${i + 1}`, channel = `${key}.spot`, basis = `${channel}@${CCT_K}K`;
    model.channels.push({ channel_key: channel, entity_id: channel, fixture_id: key, channel_id: 'spot', kind: 'spot', cct_range_k: [CCT_K, CCT_K] });
    model.basis[basis] = { [SPOT_ID]: spotValue(row.aim_origin_m, bookPoint, lumens) };
    model.cct[channel] = { kelvin: [CCT_K], basis_keys: [basis], entity_id: channel };
    model.dimming[channel] = { levels: LEVELS, g: LEVELS.map(u => litDimmingFraction(u / FULL)), shared_with: null };
    model.fixtures[key] = { aim_origin_m: row.aim_origin_m, horizontal_world_reference: row.horizontal_world_reference, limits: row.limits || null };
  });
  // JSON writes NaN as null, which a model's basis may hold for a point a fit excluded: never let one through unnamed.
  if (!finite(model)) throw Error('light_model_not_finite');
  return model;
}

// Page start: book_point_m from the room model the click reads, the fixture origins from the geometry brain.mjs wrote.
// sockets: 'export' when the geometry came from your registration export. Returns digests, labels and whole values only.
export function writeLightModel({ roomModel, geometry, to, sockets }) {
  const roomBytes = fs.readFileSync(roomModel), geometryBytes = fs.readFileSync(geometry);
  const latest = JSON.parse(roomBytes.toString('utf8').trim().split('\n').at(-1));
  const seats = (latest.surfaces || []).filter(item => item.surface_id === SURFACE_ID && item.kind === 'seat');
  if (seats.length !== 1) throw Error('room_model_holds_no_couch');
  const doc = JSON.parse(geometryBytes.toString('utf8')), channels = channelTable(doc);
  const read = { room_model: sha256(roomBytes), geometry: sha256(geometryBytes) };
  // The fixture rows it used, by their own digest: the geometry file carries the time it was built, so its digest moves at
  // every page start while the rows stay, and the model, which the coordinator's config hashes, must stay with them.
  const rows = doc.fixtures.map(row => ({ aim_origin_m: row.aim_origin_m, horizontal_world_reference: row.horizontal_world_reference, limits: row.limits || null }));
  const model = lightModel({ bookPoint: seats[0].geometry.book_point_m, fixtures: doc.fixtures, sources: {
    spot: { from: `book_point_m of room model ${latest.version}`, room_model_sha256: read.room_model },
    fixtures: { status: sockets === 'export' ? 'your registration export' : "the authored room's public sockets", from: 'the brain geometry', fixtures_sha256: sha256(JSON.stringify(rows)) } } });
  const bytes = Buffer.from(JSON.stringify(model, null, 1) + '\n', 'utf8');
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.writeFileSync(`${to}.part`, bytes);
  fs.renameSync(`${to}.part`, to);
  const atFull = Object.fromEntries(channels.map(row => [row.key, Math.round(model.basis[`${row.channel}@${CCT_K}K`][SPOT_ID])]));
  return { sha256: sha256(bytes), version: VERSION, channels, read, at_full: atFull };
}
