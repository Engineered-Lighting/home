/* Echo's virtual spotlights follow the authenticated, saved Apartment
 * layout. The renderer gets positions and labels without any control links. */
const SCHEMA = 'home-echo-virtual-lighting-1';
const FRAME = 'z_up_metric_floor0';
const MAX_FIXTURES = 13;
const FIXTURE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/;
const HASH = /^[a-f0-9]{64}$/;
const finiteVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);

function createDocument(pack, frame, digest, revision, source, equipment) {
    if (pack?.siteId !== 'echo' || pack.schemaVersion !== 1 || pack.sceneAvailable !== true
        || typeof pack.revision !== 'string' || !pack.revision || !Number.isSafeInteger(pack.registrationVersion)
        || pack.registrationVersion < 0 || typeof digest !== 'function'
        || frame?.frame !== FRAME || (frame.site !== undefined && frame.site !== 'echo')
        || (frame.units !== undefined && frame.units !== 'm') || !finiteVector(frame.extents_m)) return null;
    const frameDigest = pack.assets?.['frame.json']?.sha256;
    const collisionDigest = pack.assets?.['collision.glb']?.sha256;
    if (!HASH.test(frameDigest || '') || !HASH.test(collisionDigest || '')) return null;
    const [width, depth, height] = frame.extents_m;
    if (width <= 0 || depth <= 0 || height <= 0 || Math.hypot(width, depth) > 100) return null;
    const document = {
        schema: SCHEMA, version: 1, revision,
        scope: { site_id: 'echo', map_id: 'echo-apartment', room_id: 'apartment' },
        binding: {
            content_revision: pack.revision, registration_version: pack.registrationVersion,
            manifest_digest: digest(pack), frame_digest: frameDigest, canonical_collision_digest: collisionDigest,
        },
        references: {
            frame: FRAME, units: 'm', floor: { normal: [0, 0, 1], offset_m: 0 },
            walls: [
                { id: 'west', normal_xy: [1, 0], offset_m: 0 },
                { id: 'east', normal_xy: [-1, 0], offset_m: -width },
                { id: 'south', normal_xy: [0, 1], offset_m: 0 },
                { id: 'north', normal_xy: [0, -1], offset_m: -depth },
            ],
            confirmed: false, provenance: { status: 'modeled', source },
        },
        equipment,
    };
    return { ...document, digest: digest(document) };
}

export function createEchoVirtualLightingDocument({ pack, frame, savedModel, digest } = {}) {
    const source = savedModel?.source_detail;
    if (savedModel?.source_kind !== 'live_ha_model' || savedModel.exists === false || savedModel.seeded
        || savedModel.offline_draft || savedModel.remote_cached || source?.isolated
        || !Number.isSafeInteger(savedModel.revision) || savedModel.revision < 0
        || source?.revision !== savedModel.revision || source.site_id !== 'echo' || source.content_revision !== pack?.revision
        || [savedModel.site, savedModel.site_id, savedModel.meta?.site_id].some(site => site != null && site !== 'echo')
        || !Array.isArray(savedModel.devices) || !finiteVector(frame?.extents_m)) return null;
    const [width, depth, height] = frame.extents_m;
    const lights = savedModel.devices.filter(device => {
        if (device?.type !== 'light' || typeof device.id !== 'string' || !FIXTURE_ID.test(device.id)
            || typeof device.name !== 'string' || !device.name.trim() || !finiteVector(device.pos)) return false;
        const [x, y, z] = device.pos;
        return x >= 0 && x <= width && y >= 0 && y <= depth && z >= 0 && z <= height;
    }).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    if (!lights.length || lights.length > MAX_FIXTURES || new Set(lights.map(light => light.id)).size !== lights.length) return null;

    const equipment = lights.flatMap(device => {
        const mountId = 'echo-mount-' + device.id;
        const roomId = typeof device.room_id === 'string' ? device.room_id : null;
        return [
            { id: mountId, kind: 'mount', label: device.name, position_m: device.pos.slice(), position_status: 'modeled',
                orientation: { status: 'missing', direction_unit: null } },
            { id: 'echo-virtual-' + device.id, kind: 'fixture', label: device.name, mounted_at_ref: mountId, room_id: roomId,
                position_m: device.pos.slice(), position_status: 'modeled', mount_offset_m: [0, 0, 0],
                orientation: { status: 'approximate', direction_unit: [0, 0, -1] } },
        ];
    });
    return createDocument(pack, frame, digest, savedModel.revision, 'saved_echo_layout', equipment);
}

// Offline neutral albedo has no dependency on private placements. Echo spot
// lighting is computed against the saved positions in the active session.
export function createEchoVirtualLightingBakeDocument({ pack, frame, digest } = {}) {
    return createDocument(pack, frame, digest, 0, 'pack_neutral_capture', []);
}
