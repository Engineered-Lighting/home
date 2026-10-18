/* Pure equipment survey records. No I/O, actuator commands, calibration or inferred poses. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeEquipmentRegistration = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'home-equipment-registration-1', VERSION = 1, FRAME = 'z_up_metric_floor0', MAX_EQUIPMENT = 32;
  const SCOPE = Object.freeze({ site_id: 'victoria', map_id: 'victoria-den', room_id: 'den' });
  const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}(?![\s\S])/, SHA = /^[a-f0-9]{64}(?![\s\S])/;
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
  const exact = (x, keys) => object(x) && Object.keys(x).length === keys.length && keys.every(k => Object.hasOwn(x, k));
  const id = x => typeof x === 'string' && ID.test(x);
  const label = x => typeof x === 'string' && x.trim().length > 0 && x.length <= 160;
  const vector = (x, n) => Array.isArray(x) && x.length === n && x.every(finite);
  const unit = (x, n) => vector(x, n) && Math.abs(Math.hypot(...x) - 1) <= 1e-8;
  const dot = (a, b) => a.reduce((sum, x, i) => sum + x * b[i], 0);
  const same = (a, b) => canonicalJson(a) === canonicalJson(b);
  function canonicalJson(value) {
    if (value === null || typeof value === 'string' || typeof value === 'boolean' || finite(value)) return JSON.stringify(value);
    if (Array.isArray(value)) return '[' + Array.from(value, canonicalJson).join(',') + ']';
    if (object(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
      return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonicalJson(value[k])).join(',') + '}';
    }
    throw new TypeError('finite_json_required');
  }
  const copy = x => JSON.parse(canonicalJson(x));
  // Same synchronous canonical UTF-8 SHA-256 convention in Node and classic browser scripts.
  function digest(value) {
    const bytes = new TextEncoder().encode(canonicalJson(value)), words = [], bitLength = bytes.length * 8;
    for (let i = 0; i < bytes.length; i++) words[i >> 2] = (words[i >> 2] || 0) | bytes[i] << (24 - i % 4 * 8);
    words[bytes.length >> 2] = (words[bytes.length >> 2] || 0) | 0x80 << (24 - bytes.length % 4 * 8);
    const total = Math.ceil((bytes.length + 9) / 64) * 16;
    words[total - 2] = Math.floor(bitLength / 4294967296); words[total - 1] = bitLength >>> 0;
    const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const rotr = (x, n) => x >>> n | x << (32 - n), w = new Array(64);
    for (let block = 0; block < total; block += 16) {
      for (let i = 0; i < 64; i++) { const a = w[i - 15], b = w[i - 2]; w[i] = i < 16 ? words[block + i] | 0 : (w[i - 16] + (rotr(a,7)^rotr(a,18)^(a>>>3)) + w[i - 7] + (rotr(b,17)^rotr(b,19)^(b>>>10))) | 0; }
      let [a,b,c,d,e,f,g,z] = h;
      for (let i = 0; i < 64; i++) { const t1 = (z + (rotr(e,6)^rotr(e,11)^rotr(e,25)) + ((e&f)^(~e&g)) + k[i] + w[i]) | 0;
        const t2 = ((rotr(a,2)^rotr(a,13)^rotr(a,22)) + ((a&b)^(a&c)^(b&c))) | 0;
        z=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0; }
      [a,b,c,d,e,f,g,z].forEach((x, i) => { h[i] = (h[i] + x) | 0; });
    }
    return h.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('');
  }
  function sealDocument(doc) {
    const result = copy(doc); delete result.digest;
    return { ...result, digest: digest(result) };
  }
  function bindingValid(b) {
    return exact(b, ['content_revision','registration_version','manifest_digest','frame_digest','canonical_collision_digest'])
      && id(b.content_revision) && Number.isSafeInteger(b.registration_version) && b.registration_version >= 0
      && ['manifest_digest','frame_digest','canonical_collision_digest'].every(k => typeof b[k] === 'string' && SHA.test(b[k]));
  }
  function referencesValid(r) {
    return exact(r, ['frame','units','floor','walls','confirmed','provenance']) && r.frame === FRAME && r.units === 'm'
      && typeof r.confirmed === 'boolean' && object(r.provenance)
      && exact(r.floor, ['id','normal','offset_m']) && id(r.floor.id) && unit(r.floor.normal, 3) && r.floor.normal[2] > 1e-8 && finite(r.floor.offset_m)
      && Array.isArray(r.walls) && r.walls.length >= 3 && r.walls.length <= 32
      && r.walls.every(w => exact(w, ['id','label','normal_xy','offset_m']) && id(w.id) && label(w.label) && unit(w.normal_xy, 2) && finite(w.offset_m))
      && new Set([r.floor.id, ...r.walls.map(w => w.id)]).size === r.walls.length + 1;
  }
  function recordValid(r, refs) {
    const extras = r?.kind === 'mount' ? ['physical_kind'] : r?.kind === 'fixture' ? ['installation_status','mounted_at_ref','mount_offset_m'] : r?.kind === 'camera' ? ['camera'] : [];
    if (!exact(r, ['id','kind','label','measurements','position_m','position_status','mount_type','orientation','origins', ...extras])
      || !id(r.id) || !label(r.label) || !['mount','fixture','camera'].includes(r.kind)) return false;
    const m = r.measurements, o = r.orientation;
    if (!exact(m, ['wall_distances','height_above_floor_m','method','uncertainty_m']) || !Array.isArray(m.wall_distances) || m.wall_distances.length > 2
      || !m.wall_distances.every(w => exact(w, ['wall_id','distance_m']) && refs.walls.some(ref => ref.id === w.wall_id) && (w.distance_m === null || finite(w.distance_m) && w.distance_m >= 0))
      || new Set(m.wall_distances.map(w => w.wall_id)).size !== m.wall_distances.length
      || !(m.height_above_floor_m === null || finite(m.height_above_floor_m) && m.height_above_floor_m >= 0)
      || ![null,'tape','laser','estimated','modeled'].includes(m.method) || !(m.uncertainty_m === null || finite(m.uncertainty_m) && m.uncertainty_m >= 0)) return false;
    if (!(r.position_m === null || vector(r.position_m, 3)) || !['missing','approximate','measured','modeled'].includes(r.position_status)
      || !['unknown','wall','ceiling'].includes(r.mount_type) || !exact(o, ['direction_unit','roll_rad','status'])
      || !(o.direction_unit === null || unit(o.direction_unit, 3)) || !(o.roll_rad === null || finite(o.roll_rad))
      || !['missing','approximate'].includes(o.status) || (o.status === 'missing') !== (o.direction_unit === null && o.roll_rad === null)
      || !exact(r.origins, ['fixture_bottom','optical_pivot']) || !Object.values(r.origins).every(v => v === null || vector(v, 3))) return false;
    if (r.kind === 'mount' && !['unconfirmed','socket','mount_candidate'].includes(r.physical_kind)) return false;
    if (r.kind === 'fixture' && (!['proposed','installed'].includes(r.installation_status) || !id(r.mounted_at_ref) || !(r.mount_offset_m === null || vector(r.mount_offset_m, 3)))) return false;
    if (r.kind === 'camera' && (!exact(r.camera, ['model','intrinsics','extrinsics']) || r.camera.model !== 'OAK-D Pro' || r.camera.intrinsics !== null || r.camera.extrinsics !== null)) return false;
    return true;
  }
  const missing = reason => ({ position_m: null, position_status: 'missing', reason });
  function floorHeight(refs, x, y) {
    const { normal: n, offset_m: d } = refs.floor;
    return (d - n[0] * x - n[1] * y) / n[2];
  }
  function insideRoom(position, refs) {
    return vector(position, 3) && refs.walls.every(w => dot(w.normal_xy, position) >= w.offset_m - 1e-9)
      && position[2] >= floorHeight(refs, position[0], position[1]) - 1e-9;
  }
  function positionStatus(record, refs) {
    if (record.measurements.method === 'modeled') return 'modeled';
    return refs.confirmed && ['tape','laser'].includes(record.measurements.method) && record.measurements.uncertainty_m !== null ? 'measured' : 'approximate';
  }
  function solvePlacement(record, refs, { mount = null } = {}) {
    try {
      if (!referencesValid(refs) || !recordValid(record, refs)) return missing('invalid_measurements_or_references');
      let position;
      if (record.kind === 'fixture') {
        if (record.mount_offset_m === null) return missing('mount_offset_missing');
        if (mount?.kind !== 'mount' || mount.id !== record.mounted_at_ref || !vector(mount.position_m, 3)) return missing('mount_position_missing');
        position = mount.position_m.map((x, i) => x + record.mount_offset_m[i]);
      } else {
        const m = record.measurements;
        if (m.wall_distances.length !== 2 || m.wall_distances.some(w => w.distance_m === null) || m.height_above_floor_m === null) return missing('measurements_incomplete');
        const [a,b] = m.wall_distances.map(w => ({ ...refs.walls.find(ref => ref.id === w.wall_id), distance: w.distance_m }));
        const det = a.normal_xy[0] * b.normal_xy[1] - a.normal_xy[1] * b.normal_xy[0];
        // Numerical conditioning guard, not a measurement accuracy claim.
        if (Math.abs(det) <= 1e-6) return missing('near_parallel_walls');
        const u = a.offset_m + a.distance, v = b.offset_m + b.distance;
        const x = (u * b.normal_xy[1] - a.normal_xy[1] * v) / det;
        const y = (a.normal_xy[0] * v - u * b.normal_xy[0]) / det;
        position = [x, y, floorHeight(refs, x, y) + m.height_above_floor_m];
      }
      if (!insideRoom(position, refs)) return missing('outside_room');
      return { position_m: position, position_status: positionStatus(record, refs), reason: null };
    } catch (_) { return missing('invalid_measurements_or_references'); }
  }
  function solvedRecords(equipment, references) {
    const resolved = new Map();
    for (const r of equipment.filter(r => r.kind !== 'fixture')) resolved.set(r.id, { ...r, ...solvePlacement(r, references) });
    for (const r of equipment.filter(r => r.kind === 'fixture')) resolved.set(r.id, { ...r, ...solvePlacement(r, references, { mount: resolved.get(r.mounted_at_ref) }) });
    return resolved;
  }
  function validateDocument(doc, { binding, verifyDigest = true, verifyPositions = true, allowInvalidPlacements = false } = {}) {
    const errors = [], bad = x => errors.push(x);
    try {
      if (canonicalJson(doc).length > 262144) bad('document_budget');
      if (!exact(doc, ['schema','version','scope','binding','revision','digest','references','equipment']) || doc.schema !== SCHEMA || doc.version !== VERSION
        || !same(doc.scope, SCOPE) || !Number.isSafeInteger(doc.revision) || doc.revision < 0) bad('document_identity');
      if (!bindingValid(doc.binding) || binding && !same(doc.binding, binding)) bad('binding_mismatch');
      if (!referencesValid(doc.references)) bad('references_invalid');
      if (!Array.isArray(doc.equipment) || doc.equipment.length > MAX_EQUIPMENT || doc.equipment.some(r => !recordValid(r, doc.references))
        || new Set(doc.equipment.map(r => r.id)).size !== doc.equipment.length) bad('equipment_invalid');
      else {
        const resolved = solvedRecords(doc.equipment, doc.references);
        for (const r of doc.equipment) {
        if (r.kind === 'fixture' && !doc.equipment.some(m => m.kind === 'mount' && m.id === r.mounted_at_ref)) bad('mount_reference_missing');
        const solved = resolved.get(r.id);
        if (!allowInvalidPlacements && ['outside_room','near_parallel_walls','invalid_measurements_or_references'].includes(solved.reason)) bad('placement_invalid:' + r.id + ':' + solved.reason);
        if (verifyPositions) {
          if (!same(r.position_m, solved.position_m) || r.position_status !== solved.position_status) bad('position_mismatch:' + r.id);
        }
        }
      }
      if (!(doc.digest === null && doc.revision === 0) && !(typeof doc.digest === 'string' && SHA.test(doc.digest))) bad('digest_required');
      else if (verifyDigest && doc.digest !== null && sealDocument(doc).digest !== doc.digest) bad('digest_mismatch');
    } catch (_) { bad('finite_json_document_required'); }
    return { ok: errors.length === 0, errors: [...new Set(errors)] };
  }
  function requireValid(doc, options) {
    const result = validateDocument(doc, options);
    if (!result.ok) throw new TypeError(result.errors.join(','));
  }
  // Draft normalization deliberately preserves the persisted revision/digest CAS pins.
  function normalizeDocument(doc, { allowInvalidPlacements = false } = {}) {
    requireValid(doc, { verifyDigest: false, verifyPositions: false, allowInvalidPlacements });
    const result = copy(doc), resolved = solvedRecords(result.equipment, result.references);
    result.equipment = result.equipment.map(r => {
      const { position_m, position_status } = resolved.get(r.id);
      return { ...r, position_m, position_status };
    });
    requireValid(result, { verifyDigest: false, allowInvalidPlacements });
    return result;
  }
  function starter(idValue, kind, text, extra = {}) {
    return { id: idValue, kind, label: text,
      measurements: { wall_distances: [], height_above_floor_m: null, method: null, uncertainty_m: null },
      position_m: null, position_status: 'missing', mount_type: 'unknown',
      orientation: { direction_unit: null, roll_rad: null, status: 'missing' },
      origins: { fixture_bottom: null, optical_pivot: null }, ...extra };
  }
  function createDocument(binding, references) {
    const equipment = [1,2].map(i => starter('victoria-den-mount-0' + i, 'mount', 'Mount ' + i, { physical_kind: 'unconfirmed' }));
    for (const i of [1,2]) equipment.push(starter('victoria-den-proposed-fixture-0' + i, 'fixture', 'Proposed fixture ' + i,
      { installation_status: 'proposed', mounted_at_ref: 'victoria-den-mount-0' + i, mount_offset_m: null }));
    equipment.push(starter('victoria-den-oak-d-pro-01', 'camera', 'OAK-D Pro', { camera: { model: 'OAK-D Pro', intrinsics: null, extrinsics: null } }));
    const doc = copy({ schema: SCHEMA, version: VERSION, scope: SCOPE, binding, revision: 0, digest: null, references, equipment });
    requireValid(doc); return doc;
  }
  function project(doc, writable) {
    const snapshot = copy(doc), resolved = solvedRecords(snapshot.equipment, snapshot.references);
    return {
      schema_version: 1, revision: snapshot.revision, exists: true, site: SCOPE.site_id,
      meta: { frame: FRAME, site_id: SCOPE.site_id, map_id: SCOPE.map_id, room_id: SCOPE.room_id },
      zones: [], targets: [], devices: snapshot.equipment.map(r => ({
        id: r.id, type: r.kind === 'fixture' ? 'light' : r.kind, name: r.label, label: r.label,
        pos: copy(r.position_m), equipment_record: copy(r), equipment_position_reason: resolved.get(r.id).reason,
      })),
      source_kind: 'equipment_registration',
      source_detail: { ...SCOPE, ...copy(snapshot.binding), read_only: !writable },
      _equipment_registration: snapshot, _equipment_registration_writable: writable,
    };
  }
  function toModel(doc, { writable = true } = {}) {
    if (typeof writable !== 'boolean') throw new TypeError('writable_boolean_required');
    requireValid(doc); return project(doc, writable);
  }
  function toDocument(model) {
    const doc = copy(model?._equipment_registration);
    if (!Array.isArray(model.devices)) throw new TypeError('equipment_model_required');
    doc.equipment = model.devices.filter(d => Object.hasOwn(d, 'equipment_record')).map(d => copy(d.equipment_record));
    return normalizeDocument(doc, { allowInvalidPlacements: true });
  }
  function requireWritable(model) {
    if (model?.source_kind !== 'equipment_registration' || model?.source_detail?.read_only !== false
      || model?._equipment_registration_writable !== true) throw new TypeError('equipment_registration_read_only');
  }
  function mergeRecord(base, patch) {
    if (!object(patch)) throw new TypeError('equipment_patch_required');
    const result = { ...base };
    for (const [key, value] of Object.entries(patch)) {
      if (!Object.hasOwn(base, key)) throw new TypeError('unknown_equipment_field:' + key);
      result[key] = object(base[key]) && object(value) ? mergeRecord(base[key], value) : copy(value);
    }
    return result;
  }
  function updateModelEquipment(model, recordId, patch) {
    requireWritable(model);
    if (!object(patch)) throw new TypeError('equipment_patch_required');
    if (Object.hasOwn(patch, 'id') || Object.hasOwn(patch, 'kind')) throw new TypeError('immutable_equipment_identity');
    const doc = toDocument(model), index = doc.equipment.findIndex(r => r.id === recordId);
    if (index < 0) throw new TypeError('equipment_record_missing');
    const record = mergeRecord(doc.equipment[index], patch);
    if (Object.hasOwn(patch, 'orientation')) record.orientation.status = record.orientation.direction_unit === null && record.orientation.roll_rad === null ? 'missing' : 'approximate';
    doc.equipment[index] = record;
    return project(normalizeDocument(doc, { allowInvalidPlacements: true }), true);
  }
  function updateModelReferences(model, patch) {
    requireWritable(model);
    if (!exact(patch, ['confirmed']) || typeof patch.confirmed !== 'boolean') throw new TypeError('reference_confirmation_only');
    const doc = toDocument(model); doc.references.confirmed = patch.confirmed;
    return project(normalizeDocument(doc, { allowInvalidPlacements: true }), true);
  }
  function addSocket(model) {
    requireWritable(model);
    const doc = toDocument(model);
    if (doc.equipment.length >= MAX_EQUIPMENT) throw new TypeError('equipment_capacity_reached');
    let number = 0;
    for (const record of doc.equipment) {
      const match = /^victoria-den-mount-([0-9]{2,})$/.exec(record.id);
      if (match) {
        const existing = Number(match[1]);
        if (!Number.isSafeInteger(existing)) throw new TypeError('equipment_socket_id_unavailable');
        number = Math.max(number, existing);
      }
    }
    number++;
    if (!Number.isSafeInteger(number)) throw new TypeError('equipment_socket_id_unavailable');
    doc.equipment.push(starter('victoria-den-mount-' + String(number).padStart(2, '0'), 'mount', 'Socket ' + number, { physical_kind: 'socket' }));
    requireValid(doc, { verifyDigest: false, allowInvalidPlacements: true });
    return project(doc, true);
  }
  return { SCHEMA, VERSION, FRAME, MAX_EQUIPMENT, canonicalJson, digest, sealDocument, createDocument, validateDocument, solvePlacement,
    normalizeDocument, toModel, toDocument, updateModelEquipment, updateModelReferences, addSocket };
});
