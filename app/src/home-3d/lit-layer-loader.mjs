/* home-3d/lit-layer-loader.mjs — L4: load L3's additive lighting layer and
 * refuse it on any mismatch ("Hyperreal browser room lighting" L3/L4;
 * tools/spatial-pipeline/lightbake/README.md).
 *
 * The layer sits beside the accepted content pack, never inside it:
 * <revision>.lighting/lighting-manifest.json and one file per field. It is
 * private Den data like the pack. Pack bytes are never read here or changed
 * anywhere; the layer is additive. The load rule mirrors the Python twin
 * (lightbake/binding.py verify_layer):
 *
 *   - the manifest's binding equals the active pack's binding, computed the
 *     way the equipment registration computes it (content revision,
 *     registration version, the canonical-JSON SHA-256 of content-pack.json,
 *     and the frame.json and collision.glb digests);
 *   - every pack_assets[*].sha256 equals the active pack's declared digest;
 *   - every layer file this view reads has the declared size and SHA-256.
 *     Files of other receiver sets (and the mesh layer) are not read at all.
 *
 * No three.js, no DOM: fetch and crypto.subtle only, so Node tests run it.
 */

export const LIT_LAYER_SCHEMA = 'home-lighting-layer-1';
export const LIT_LAYER_MANIFEST = 'lighting-manifest.json';
// Receiver sets by the pack asset they are keyed to (vertex index in the PLY).
export const LIT_LAYER_SETS = Object.freeze({ 'apartment.ply': 'splat', 'apartment.mobile.ply': 'splat-mobile', 'points.ply': 'points' });
const FIELDS = Object.freeze([['albedo', 3], ['normal-oct', 2], ['basis-u8', null], ['basis-scale', 2], ['bounce-tint', 3], ['flags', 1]]);
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024;
const MAX_RECEIVERS = 16 * 1024 * 1024;
const HEX64 = /^[a-f0-9]{64}$/;

class LayerRefused extends Error {
    constructor(reason) { super(reason); this.name = 'LitLayerRefused'; this.reason = reason; }
}
const refuse = reason => { throw new LayerRefused(reason); };

async function sha256Hex(bytes, subtle) {
    const digest = new Uint8Array(await subtle.digest('SHA-256', bytes));
    let hex = '';
    for (const byte of digest) hex += byte.toString(16).padStart(2, '0');
    return hex;
}
const canonicalEqual = (a, b) => JSON.stringify(sortKeys(a)) === JSON.stringify(sortKeys(b));
function sortKeys(value) {
    if (Array.isArray(value)) return value.map(sortKeys);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, sortKeys(value[key])]));
    return value;
}

/* The active pack's binding, as web-gateway/equipment-registration.mjs
 * readActiveBinding computes it. `digest` is the canonical-JSON SHA-256
 * (HomeEquipmentRegistration.digest). The frame and collision digests are
 * the pack manifest's own, which the pack service verifies on install. */
export function activePackBinding(pack, digest) {
    if (!pack || typeof digest !== 'function') refuse('lighting_layer_pack_unavailable');
    const frame = pack.assets?.['frame.json']?.sha256, collision = pack.assets?.['collision.glb']?.sha256;
    if (!HEX64.test(frame || '') || !HEX64.test(collision || '')) refuse('lighting_layer_pack_unavailable');
    return { content_revision: pack.revision, registration_version: pack.registrationVersion, manifest_digest: digest(pack),
        frame_digest: frame, canonical_collision_digest: collision };
}

/* The layer folder for a pack served from `packBaseUrl`
 * (…/content-packs/<site>/<revision>/): its sibling <revision>.lighting/. */
export function layerBaseUrl(packBaseUrl, revision) {
    const base = new URL(packBaseUrl);
    if (!base.pathname.endsWith('/' + revision + '/')) refuse('lighting_layer_url_invalid');
    return new URL('../' + encodeURIComponent(revision) + '.lighting/', base).href;
}

/* Load and verify one receiver set ('splat', 'splat-mobile' or 'points').
 * Resolves to the decoded arrays; rejects with a LitLayerRefused naming the
 * first mismatch. */
export async function loadLightingLayer({ baseUrl, pack, digest, set, fetchImpl = globalThis.fetch?.bind(globalThis),
    subtle = globalThis.crypto?.subtle, signal = null } = {}) {
    if (typeof fetchImpl !== 'function' || !subtle) refuse('lighting_layer_platform_unavailable');
    if (!Object.values(LIT_LAYER_SETS).includes(set)) refuse('lighting_layer_set_unknown');
    const read = async (name, limit) => {
        const response = await fetchImpl(new URL(name, baseUrl).href, { cache: 'no-store', credentials: 'same-origin', signal });
        if (!response?.ok) refuse(name === LIT_LAYER_MANIFEST ? 'lighting_layer_absent' : 'lighting_layer_file_unavailable:' + name);
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.byteLength > limit) refuse('lighting_layer_file_too_large:' + name);
        return bytes;
    };
    let manifest;
    try { manifest = JSON.parse(new TextDecoder().decode(await read(LIT_LAYER_MANIFEST, MAX_MANIFEST_BYTES))); }
    catch (error) { if (error instanceof LayerRefused || error?.name === 'AbortError') throw error; refuse('lighting_layer_manifest_invalid'); }
    if (manifest?.schema !== LIT_LAYER_SCHEMA || manifest.provenance?.status !== 'modeled' || manifest.provenance?.measured !== false)
        refuse('lighting_layer_schema');
    if (!canonicalEqual(manifest.binding, activePackBinding(pack, digest))) refuse('lighting_layer_binding_mismatch');
    for (const [name, asset] of Object.entries(manifest.pack_assets || {})) {
        if (!HEX64.test(asset?.sha256 || '') || pack.assets?.[name]?.sha256 !== asset.sha256) refuse('lighting_layer_pack_asset_mismatch:' + name);
    }
    const receivers = manifest.receivers?.[set];
    const columns = Array.isArray(manifest.columns) ? manifest.columns : [];
    const K = columns.length, count = receivers?.count;
    if (!receivers || !Number.isSafeInteger(count) || count <= 0 || count > MAX_RECEIVERS) refuse('lighting_layer_set_absent:' + set);
    if (K < 1 || K > 32 || !Array.isArray(receivers.column_scale) || receivers.column_scale.length !== K
        || !receivers.column_scale.every(value => Number.isFinite(value) && value > 0)) refuse('lighting_layer_columns_invalid');
    const source = receivers.source;
    if (LIT_LAYER_SETS[source] !== set || pack.assets?.[source]?.sha256 !== receivers.source_sha256
        || manifest.pack_assets?.[source]?.sha256 !== receivers.source_sha256) refuse('lighting_layer_source_mismatch:' + set);
    const out = { set, source, sourceSha256: receivers.source_sha256, count, columns: K, columnScale: Float64Array.from(receivers.column_scale),
        columnsMeta: columns.map(column => Object.freeze({ index: column.index, kind: column.kind, fixture_id: column.fixture_id ?? null,
            zone: column.zone ?? null, unit: column.unit ?? null })),
        binding: Object.freeze({ ...manifest.binding }), provenance: Object.freeze({ status: 'modeled', measured: false,
            photometry: manifest.provenance.photometry ?? 'assumed' }), files: 0, bytes: 0 };
    for (const [field, width] of FIELDS) {
        const name = `${set}-${field}.bin`, entry = manifest.files?.[name], size = count * (width ?? K);
        if (!entry || entry.bytes !== size || !HEX64.test(entry.sha256 || '') || !(receivers.files || []).includes(name)) refuse('lighting_layer_file_mismatch:' + name);
        const bytes = await read(name, size);
        if (bytes.byteLength !== size || await sha256Hex(bytes, subtle) !== entry.sha256) refuse('lighting_layer_file_mismatch:' + name);
        out[field] = bytes;
        out.files++; out.bytes += size;
    }
    return Object.freeze({
        ...out,
        albedo: out.albedo, normalOct: out['normal-oct'], basis: out['basis-u8'], tint: out['bounce-tint'], flags: out.flags,
        scale: new Uint16Array(out['basis-scale'].buffer.slice(out['basis-scale'].byteOffset, out['basis-scale'].byteOffset + count * 2)),
    });
}
export const isLayerRefusal = error => error instanceof LayerRefused || error?.name === 'LitLayerRefused';

/* Octahedral u8 normals (lightbake/encode.py decode_normal_oct) to unit
 * vectors, in the receivers' own frame. */
export function decodeNormalsOct(oct, count, out = new Float32Array(count * 3)) {
    for (let i = 0; i < count; i++) {
        let x = oct[i * 2] / 255 * 2 - 1, y = oct[i * 2 + 1] / 255 * 2 - 1;
        const z = 1 - Math.abs(x) - Math.abs(y), t = Math.max(-z, 0);
        x += x >= 0 ? -t : t; y += y >= 0 ? -t : t;
        const length = Math.hypot(x, y, z) || 1;
        out[i * 3] = x / length; out[i * 3 + 1] = y / length; out[i * 3 + 2] = z / length;
    }
    return out;
}
