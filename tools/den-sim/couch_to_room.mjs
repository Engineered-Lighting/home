// Hop 14: the couch into the pipeline's own RoomModel (plan, hop 14). A sibling of
// tools/den-room/brain-targets.mjs. It reads a sealed sofa record, your mark or the public stand-in
// seat run.mjs seals around a DECLARED point, checks its seal and binding with Home's own
// validateSofaTarget, unchanged, and appends one revision to a copy of the fit's room-model.jsonl by
// den-register's own rule (records.py: the latest revision copied, its version's trailing number up by
// one, the changed field replaced, its provenance named). The fit's own file is never touched, so its
// refit-receipt.json still vouches for it and the re-fit cache still finds it. It returns digests and
// labels only: the file it writes holds coordinates, so nothing here prints one.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';

export const SPOT_ABOVE_SEAT_M = .30;   // the spot: 0.30 m above the seat point, the stated rule (an invented number)
export const SURFACE_ID = 'sofa', LANDMARK_ID = 'sofa-seat', SPOT_ID = 'sofa-book', FORM = 'polygon-3d/1';
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const metres = v => Math.round(v * 1e6) / 1e6;   // den-register's records.rounded: micrometres

// den-register's records.next_room_version.
export function nextVersion(version, roomId) {
  const match = /^(.*?)([0-9]+)$/.exec(version || '');
  return match ? `${match[1]}${Number(match[2]) + 1}` : `${version || roomId}-v1`;
}
// Every number keeps the text den-register wrote, so the camera entries this revision copies stay exactly as they were.
const parseExact = text => JSON.parse(text, (_key, value, context) => typeof value === 'number' ? JSON.rawJSON(context.source) : value);
// PowerShell 5.1 writes UTF-16 with `>` and a UTF-8 BOM with Set-Content.
function readJson(file) {
  const bytes = fs.readFileSync(file), text = bytes[0] === 0xff && bytes[1] === 0xfe ? bytes.toString('utf16le') : bytes.toString('utf8');
  return { bytes, doc: JSON.parse(text.replace(/^﻿/, '')) };
}

// The contract: one seat surface, its landmark, and provenance named after the record's digest. The seat's top is
// the seat point's height, because a mark's corners aren't level.
export function couchOf(seat, source) {
  const point = seat.seat.point_m, book = point.map((v, i) => metres(i === 2 ? v + SPOT_ABOVE_SEAT_M : v));
  return {
    surfaces: [{ surface_id: SURFACE_ID, kind: 'seat', geometry: { form: FORM, vertices_m: seat.zone.vertices_m, centre_m: seat.zone.centre_m,
      normal_unit: seat.zone.normal_unit, seat_point_m: point, book_point_m: book }, reflectance: { value: null, status: 'assumed', calibration_id: null }, focal: false }],
    landmarks: [{ landmark_id: LANDMARK_ID, kind: 'seat', surface_id: SURFACE_ID }],
    provenance: { method: source === 'sofa_mark' ? 'manual' : 'assumed', ts: null, confidence: null, mark_digest: seat.digest,
      from: source === 'sofa_mark' ? 'your sofa mark' : 'a public stand-in seat at a DECLARED point, not your sofa mark',
      spot: `${SPOT_ID}: book_point_m, the seat point plus ${SPOT_ABOVE_SEAT_M} m up` },
  };
}

// seatFile: your mark, or the stand-in seat; seatSha256: its digest as the re-fit read it, so the couch is placed from
// those very bytes; registration: the export it names (or the authored one the stand-in is sealed to); from: the fit's
// room-model.jsonl; to: the new file the click reads. Throws Home's reason code on a seal.
export function writeCouch({ seatFile, seatSha256, registration, from, to, source }) {
  const read = readJson(seatFile);
  if (seatSha256 !== undefined && sha256(read.bytes) !== seatSha256) throw Error('seat_changed_since_refit');   // a code: run.mjs prints only codes
  const seat = Sofa.validateSofaTarget(read.doc, { binding: registration.binding, equipment_ref: { revision: registration.revision, digest: registration.digest } },
    { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
  const fitBytes = fs.readFileSync(from), text = fitBytes.toString('utf8').replace(/\r\n/g, '\n');
  const latest = parseExact(text.split('\n').filter(line => line.trim()).at(-1));
  const couch = couchOf(seat, source);
  const revision = { ...latest, version: nextVersion(latest.version, latest.room_id),
    surfaces: [...(latest.surfaces || []).filter(item => item.surface_id !== SURFACE_ID), ...couch.surfaces],
    landmarks: [...(latest.landmarks || []).filter(item => item.landmark_id !== LANDMARK_ID), ...couch.landmarks],
    provenance: { ...(latest.provenance || {}), surfaces: couch.provenance } };
  const bytes = Buffer.from(`${text.endsWith('\n') ? text : `${text}\n`}${JSON.stringify(revision)}\n`, 'utf8');
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.writeFileSync(`${to}.part`, bytes);
  fs.renameSync(`${to}.part`, to);
  return { sha256: sha256(bytes), from_sha256: sha256(fitBytes), seat_sha256: sha256(read.bytes), mark_digest: seat.digest, version: revision.version };
}
