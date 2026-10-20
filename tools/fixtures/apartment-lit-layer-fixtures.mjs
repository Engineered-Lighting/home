// An authored L3-format lighting layer for the authored fixture packs
// (apartment-home-shell-fixtures.mjs), for the L4 lit splats and points tests.
// Placeholder data only: an analytic basis from the authored sockets, no
// household pack, imagery or coordinates. It follows the layer contract in
// tools/spatial-pipeline/lightbake/README.md (schema, binding, per-field files,
// 40 B per receiver, SHA-256 per file) so the real loader and blend run on it.
import { createHash } from 'node:crypto';
import { toHalf } from '../../app/src/home-3d/lit-bvh-bake.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// The authored document's sockets (apartment-home-shell-fixtures.mjs
// authoredDocument). As in L3 (lightbake/scan.py read_equipment) and the
// virtual fixtures, a socket's fixture is its linked fixture record, else
// '<mount>-virtual-fixture'; sockets 01 and 02 carry proposed fixtures.
export const AUTHORED_SOCKETS = Object.freeze([[1, [.6, .9, 2.3], true], [2, [3, 2.8, 2.2], true], [3, [1, 3, 2.3], false], [4, [3, 1, 2.2], false]]
    .map(([i, position, linked]) => Object.freeze({ mountId: 'victoria-den-mount-0' + i,
        fixtureId: linked ? 'victoria-den-proposed-fixture-0' + i : 'victoria-den-mount-0' + i + '-virtual-fixture', position })));
const EMITTER_DROP_M = 0.14, DOWN_HWHM_DEG = 30, RADIAL_HWHM_DEG = 30, RADIAL_AZIMUTHS = [0, 60, 120, 180, 240, 300];

function canonicalJson(value) {
    if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
    if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonicalJson(value[key])).join(',') + '}';
    return JSON.stringify(value);
}
export function packBinding(manifest) {
    return { content_revision: manifest.revision, registration_version: manifest.registrationVersion,
        manifest_digest: hash(canonicalJson(manifest)), frame_digest: manifest.assets['frame.json'].sha256,
        canonical_collision_digest: manifest.assets['collision.glb'].sha256 };
}

/* The authored room's receivers: the same rows the fixture PLYs carry, with
 * the surface normal of the floor or wall each lies on. */
export function authoredReceivers() {
    const rows = [];
    for (let x = 0; x <= 20; x++) for (let y = 0; y <= 20; y++) rows.push({ p: [x * .2, y * .2, 0], n: [0, 0, 1] });
    for (let z = 1; z <= 12; z++) for (let x = 0; x <= 20; x++) rows.push({ p: [x * .2, 4, z * .2], n: [0, -1, 0] }, { p: [0, x * .2, z * .2], n: [1, 0, 0] });
    return rows;
}

// Irradiance per lumen from a Gaussian lobe (peak cd/lm normalised by its flux).
function lobePeakPerLumen(hwhmDeg, maxThetaDeg) {
    let flux = 0;
    const steps = 4000, h = maxThetaDeg * Math.PI / 180 / steps;
    for (let i = 0; i <= steps; i++) {
        const theta = i * h, w = i === 0 || i === steps ? 0.5 : 1;
        flux += w * Math.exp(-Math.LN2 * (theta * 180 / Math.PI / hwhmDeg) ** 2) * 2 * Math.PI * Math.sin(theta) * h;
    }
    return 1 / flux;
}
function lobeIrradiance(origin, axis, hwhmDeg, maxThetaDeg, peak, p, n) {
    const d = [origin[0] - p[0], origin[1] - p[1], origin[2] - p[2]], r = Math.hypot(...d);
    const w = d.map(v => v / r), ndl = n[0] * w[0] + n[1] * w[1] + n[2] * w[2];
    if (ndl <= 0) return 0;
    const cosTheta = -(w[0] * axis[0] + w[1] * axis[1] + w[2] * axis[2]), theta = Math.acos(Math.min(1, Math.max(-1, cosTheta))) * 180 / Math.PI;
    if (theta > maxThetaDeg) return 0;
    return peak * Math.exp(-Math.LN2 * (theta / hwhmDeg) ** 2) * ndl / Math.max(r * r, 0.01);
}

/* 29 columns as L3 writes them: 4 downs, 24 radials (fixture-major, zones
 * 1-6), then the window. */
export function authoredColumns(sockets = AUTHORED_SOCKETS) {
    const columns = sockets.map((socket, i) => ({ index: i, kind: 'downward', fixture_id: socket.fixtureId, mount_id: socket.mountId, zone: null,
        azimuth_deg: null, unit: 'lx_per_lm', profile: { type: 'gaussian_lobe', status: 'assumed' } }));
    sockets.forEach(socket => RADIAL_AZIMUTHS.forEach((azimuth, z) => columns.push({ index: columns.length, kind: 'radial', fixture_id: socket.fixtureId,
        mount_id: socket.mountId, zone: z + 1, azimuth_deg: azimuth, unit: 'lx_per_lm', profile: { type: 'gaussian_lobe', status: 'assumed' } })));
    columns.push({ index: columns.length, kind: 'window', unit: 'capture_relative_irradiance', colour_rgb: [1, 0.97, 0.92] });
    return columns;
}

const LOBE_PEAKS = Object.freeze({ down: lobePeakPerLumen(DOWN_HWHM_DEG, 90), radial: lobePeakPerLumen(RADIAL_HWHM_DEG, 180) });
function basisRow({ p, n }, sockets) {
    const down = LOBE_PEAKS.down, radial = LOBE_PEAKS.radial, row = [];
    for (const socket of sockets) {
        const o = [socket.position[0], socket.position[1], socket.position[2] - EMITTER_DROP_M];
        row.push(lobeIrradiance(o, [0, 0, -1], DOWN_HWHM_DEG, 90, down, p, n));
    }
    for (const socket of sockets) {
        const o = [socket.position[0], socket.position[1], socket.position[2] - EMITTER_DROP_M];
        for (const azimuth of RADIAL_AZIMUTHS) {
            const a = azimuth * Math.PI / 180;
            row.push(lobeIrradiance(o, [Math.cos(a), Math.sin(a), 0], RADIAL_HWHM_DEG, 180, radial, p, n) + 0.02 * radial);
        }
    }
    // Window daylight from the +x side, brighter near it (capture-relative).
    row.push(0.5 + 0.5 * Math.max(0, 1 - p[0] / 4));
    return row;
}
const halfToFloat = h => {
    const e = (h >>> 10) & 0x1f, m = h & 0x3ff;
    return e === 0 ? m * 2 ** -24 : e === 31 ? Infinity : (1 + m / 1024) * 2 ** (e - 15);
};
function encodeSet(rows, sockets) {
    const K = sockets.length * 7 + 1, N = rows.length, B = rows.map(row => basisRow(row, sockets));
    const columnScale = Array.from({ length: K }, (_, k) => {
        const values = B.map(row => row[k]).filter(v => v > 0).sort((a, b) => a - b);
        return values.length ? values[Math.min(values.length - 1, Math.floor(values.length * 0.95))] : 1;
    });
    const albedo = new Uint8Array(N * 3), normal = new Uint8Array(N * 2), basis = new Uint8Array(N * K), scale = new Uint16Array(N);
    const tint = new Uint8Array(N * 3), flags = new Uint8Array(N);
    rows.forEach(({ p, n }, i) => {
        // An authored two-tone albedo: warm planks on the floor, a pale wall.
        const floor = n[2] > 0.5, plank = Math.floor(p[0] / 0.4) % 2;
        albedo.set(floor ? [150 + plank * 20, 110 + plank * 12, 80] : [205, 200, 190], i * 3);
        const l1 = Math.abs(n[0]) + Math.abs(n[1]) + Math.abs(n[2]), ox = n[0] / l1, oy = n[1] / l1;
        const ex = n[2] >= 0 ? ox : (1 - Math.abs(oy)) * (ox >= 0 ? 1 : -1), ey = n[2] >= 0 ? oy : (1 - Math.abs(ox)) * (oy >= 0 ? 1 : -1);
        normal.set([Math.round((ex * .5 + .5) * 255), Math.round((ey * .5 + .5) * 255)], i * 2);
        const bn = B[i].map((v, k) => v / columnScale[k]), s = Math.max(...bn);
        let s16 = toHalf(s);
        if (halfToFloat(s16) < s) s16 += 1;
        scale[i] = s16;
        const sf = halfToFloat(s16);
        bn.forEach((v, k) => { basis[i * K + k] = sf > 0 ? Math.round(Math.sqrt(Math.min(1, Math.max(0, v / sf))) * 255) : 0; });
        tint.set([255, 250, 240], i * 3);
    });
    return { K, N, columnScale, fields: { albedo, 'normal-oct': normal, 'basis-u8': basis, 'basis-scale': new Uint8Array(scale.buffer), 'bounce-tint': tint, flags } };
}

/* The layer's files for an installed authored pack (installPack's result):
 * { 'lighting-manifest.json': Buffer, '<set>-<field>.bin': Buffer, ... },
 * served from …/<revision>.lighting/. `tamper` edits the manifest for refusal
 * tests. */
export function authoredLightingLayer(pack, { sockets = AUTHORED_SOCKETS, sets = ['splat', 'points'], tamper = null, rows: rowsBySet = null } = {}) {
    const rowsFor = set => (Array.isArray(rowsBySet) ? rowsBySet : rowsBySet?.[set]) || authoredReceivers(), sources = { splat: 'apartment.ply', 'splat-mobile': 'apartment.mobile.ply', points: 'points.ply' };
    const files = {}, receivers = {}, entries = {};
    for (const set of sets) {
        const encoded = encodeSet(rowsFor(set), sockets), names = [];
        for (const [field, bytes] of Object.entries(encoded.fields)) {
            const name = `${set}-${field}.bin`, buffer = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
            files[name] = buffer; entries[name] = { bytes: buffer.length, sha256: hash(buffer), keyed_by: 'vertex index in the source PLY' };
            names.push(name);
        }
        receivers[set] = { source: sources[set], source_sha256: pack.manifest.assets[sources[set]]?.sha256, count: encoded.N,
            keyed_by: 'vertex index in the source PLY', bytes_per_receiver: 40, column_scale: encoded.columnScale, files: names.sort() };
    }
    const assets = Object.fromEntries(['apartment.ply', 'points.ply', 'mesh.glb'].map(name => [name, { sha256: pack.manifest.assets[name].sha256,
        bytes: pack.manifest.assets[name].bytes }]));
    let manifest = { schema: 'home-lighting-layer-1', version: 1, site_id: pack.site, created_at: '2026-10-20T00:00:00Z',
        binding: packBinding(pack.manifest), pack_assets: assets,
        provenance: { status: 'modeled', measured: false, photometry: 'assumed', disclosure: 'Authored placeholder layer for tests.' },
        columns: authoredColumns(sockets), receivers, files: entries };
    if (tamper) { const copy = structuredClone(manifest); manifest = tamper(copy) || copy; }
    files['lighting-manifest.json'] = Buffer.from(JSON.stringify(manifest));
    return { files, manifest, base: '/content-packs/' + pack.site + '/' + pack.revision + '.lighting/' };
}
