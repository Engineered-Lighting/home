// Deterministic authored room assets and survey records for the Home shell test.
// This module never reads household assets or opens a camera, service or browser.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

function authoredPositions() {
  const rows = [];
  for (let x = 0; x <= 20; x++) for (let y = 0; y <= 20; y++) rows.push([x * .2, y * .2, 0]);
  for (let z = 1; z <= 12; z++) for (let x = 0; x <= 20; x++) rows.push([x * .2, 4, z * .2], [0, x * .2, z * .2]);
  return rows;
}
function pointsPly(rows, gaussian = false) {
  const names = gaussian ? ['x','y','z','nx','ny','nz','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3'] : ['x','y','z'];
  const header = Buffer.from('ply\nformat binary_little_endian 1.0\n' + (gaussian ? '' : 'comment homeapt-points v1\n') +
    'element vertex ' + rows.length + '\n' + names.map(name => 'property float ' + name + '\n').join('') +
    (gaussian ? '' : 'property uchar intensity\n') + 'end_header\n');
  const stride = names.length * 4 + (gaussian ? 0 : 1), body = Buffer.alloc(rows.length * stride);
  rows.forEach((p, i) => {
    const fields = gaussian ? [...p, 0,0,0, .8,.4,.1, 3, Math.log(.065),Math.log(.065),Math.log(.065), 1,0,0,0] : p;
    fields.forEach((value, j) => body.writeFloatLE(value, i * stride + j * 4));
    if (!gaussian) body[i * stride + 12] = 220;
  });
  return Buffer.concat([header, body]);
}
function authoredGlb() {
  const triangles = [
    [0,0,0, 4,0,0, 4,4,0], [0,0,0, 4,4,0, 0,4,0],
    [0,4,0, 4,4,0, 4,4,2.5], [0,4,0, 4,4,2.5, 0,4,2.5],
    [0,0,0, 0,4,0, 0,4,2.5], [0,0,0, 0,4,2.5, 0,0,2.5],
  ], floats = triangles.flat(), uvs = [];
  for (const [index, triangle] of triangles.entries()) for (let i = 0; i < triangle.length; i += 3) {
    uvs.push(index < 2 ? triangle[i] : index < 4 ? triangle[i] : triangle[i + 1], index < 2 ? triangle[i + 1] : triangle[i + 2]);
  }
  const binary = Buffer.alloc((floats.length + uvs.length) * 4);
  floats.forEach((value, i) => binary.writeFloatLE(value, i * 4));
  uvs.forEach((value, i) => binary.writeFloatLE(value, floats.length * 4 + i * 4));
  // A deterministic authored plank texture makes loss of receiving-surface
  // detail visible in this renderer test without reading household assets.
  function chunk(type, data) {
    const bytes = Buffer.concat([Buffer.from(type), data]), result = Buffer.alloc(bytes.length + 8);
    result.writeUInt32BE(data.length); bytes.copy(result, 4);
    let crc = 0xffffffff;
    for (const value of bytes) { crc ^= value; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); }
    result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4); return result;
  }
  const size = 64, pixels = Buffer.alloc(size * (size * 3 + 1)), pngHeader = Buffer.alloc(13);
  pngHeader.writeUInt32BE(size); pngHeader.writeUInt32BE(size, 4); pngHeader[8] = 8; pngHeader[9] = 2;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const seam = x % 16 === 0 || (y + (Math.floor(x / 16) % 2) * 32) % 64 === 0;
    const grain = Math.round(Math.sin(x * 1.7 + Math.sin(y * .2)) * 8), plank = Math.floor(x / 16) % 2;
    const color = seam ? [45,30,20] : [132 + plank * 22 + grain, 87 + plank * 15 + grain, 50 + plank * 10 + grain];
    color.forEach((value, channel) => { pixels[y * (size * 3 + 1) + 1 + x * 3 + channel] = value; });
  }
  const png = Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk('IHDR', pngHeader), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]);
  const document = { asset: { version: '2.0', generator: 'authored-virtual-fixtures-browser-test' }, scene: 0,
    scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0, TEXCOORD_0: 1 }, material: 0 }] }],
    materials: [{ doubleSided: true, pbrMetallicRoughness: { baseColorFactor: [1,1,1,1], baseColorTexture: { index: 0 }, metallicFactor: 0, roughnessFactor: 1 } }],
    images: [{ uri: 'data:image/png;base64,' + png.toString('base64') }], textures: [{ source: 0, sampler: 0 }], samplers: [{ wrapS: 10497, wrapT: 10497 }],
    accessors: [{ bufferView: 0, componentType: 5126, count: floats.length / 3, type: 'VEC3', min: [0,0,0], max: [4,4,2.5] },
      { bufferView: 1, componentType: 5126, count: uvs.length / 2, type: 'VEC2' }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: floats.length * 4, target: 34962 },
      { buffer: 0, byteOffset: floats.length * 4, byteLength: uvs.length * 4, target: 34962 }], buffers: [{ byteLength: binary.length }] };
  const json = Buffer.from(JSON.stringify(document)), padded = Buffer.concat([json, Buffer.alloc((4 - json.length % 4) % 4, 32)]);
  const head = Buffer.alloc(20), binHead = Buffer.alloc(8);
  head.writeUInt32LE(0x46546c67, 0); head.writeUInt32LE(2, 4); head.writeUInt32LE(28 + padded.length + binary.length, 8);
  head.writeUInt32LE(padded.length, 12); head.writeUInt32LE(0x4e4f534a, 16);
  binHead.writeUInt32LE(binary.length, 0); binHead.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([head, padded, binHead, binary]);
}
function installPack(packs, site) {
  const revision = 'authored-' + site + '-r1', directory = path.join(packs, site, revision), rows = authoredPositions();
  fs.mkdirSync(directory, { recursive: true });
  const seed = { schema_version: 1, revision: 7, exists: true, site, zones: [], devices: site === 'echo' ? [{
    id: 'authored-echo-light', type: 'light', name: 'Authored Echo light', ha_entity_id: 'light.authored_echo', pos: null,
  }] : [], targets: [],
    meta: { frame: 'z_up_metric_floor0', site_id: site, authored_test: true } };
  const files = {
    'frame.json': Buffer.from(JSON.stringify({ site, frame: 'z_up_metric_floor0', units: 'm', extents_m: [4,4,2.5], splat_baked: true })),
    'points.ply': pointsPly(rows), 'apartment.ply': pointsPly(rows, true), 'mesh.glb': authoredGlb(), 'collision.glb': authoredGlb(),
    'floor.json': Buffer.from(JSON.stringify({ walkable_polygon: [[0,0],[4,0],[4,4],[0,4]] })),
    'seed-model.json': Buffer.from(JSON.stringify(seed)),
  };
  const manifest = { schemaVersion: 1, siteId: site, revision, registrationVersion: 1, sceneAvailable: true,
    assets: Object.fromEntries(Object.entries(files).map(([name, bytes]) => [name, { path: name, bytes: bytes.length, sha256: hash(bytes) }])),
    heroStills: [], review: { status: 'research-only', note: 'Authored software fixture. No household content.' } };
  files['content-pack.json'] = Buffer.from(JSON.stringify(manifest));
  for (const [name, bytes] of Object.entries(files)) fs.writeFileSync(path.join(directory, name), bytes);
  fs.writeFileSync(path.join(packs, site, 'ACTIVE'), revision + '\n');
  return { site, revision, manifest, files, seed, base: '/content-packs/' + site + '/' + revision + '/' };
}
function authoredDocument(binding, Core) {
  const references = { frame: 'z_up_metric_floor0', units: 'm', confirmed: true,
    floor: { id: 'floor', normal: [0,0,1], offset_m: 0 },
    walls: [
      { id: 'left', label: 'Authored left wall', normal_xy: [1,0], offset_m: 0 },
      { id: 'bottom', label: 'Authored front wall', normal_xy: [0,1], offset_m: 0 },
      { id: 'right', label: 'Authored right wall', normal_xy: [-1,0], offset_m: -4 },
      { id: 'top', label: 'Authored back wall', normal_xy: [0,-1], offset_m: -4 },
    ], provenance: { method: 'authored_test', evidence_ref: 'software-room-planes-1' } };
  let model = Core.toModel(Core.createDocument(binding, references));
  model = Core.addSocket(Core.addSocket(model));
  for (const [i, xyz] of [[1,[.6,.9,2.3]], [2,[3,2.8,2.2]], [3,[1,3,2.3]], [4,[3,1,2.2]]]) {
    model = Core.updateModelEquipment(model, 'victoria-den-mount-0' + i, {
      label: 'Authored socket ' + i, physical_kind: 'socket', mount_type: 'ceiling',
      measurements: { wall_distances: [{ wall_id: 'left', distance_m: xyz[0] }, { wall_id: 'bottom', distance_m: xyz[1] }],
        height_above_floor_m: xyz[2], method: 'laser', uncertainty_m: .01 },
    });
  }
  return Core.toDocument(model);
}

export { installPack, authoredDocument };
