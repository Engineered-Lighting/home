/* Echo's modeled spotlights use the immutable pack's mapped fixture positions.
 * This document is an input to the renderer and offline bake. It carries no
 * Home Assistant entities, stored settings, or equipment write authority. */
const SCHEMA = 'home-echo-virtual-lighting-1';
const FRAME = 'z_up_metric_floor0';
const MAX_FIXTURES = 4;
const FIXTURE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/;
const HASH = /^[a-f0-9]{64}$/;
const finiteVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);

export function createEchoVirtualLightingDocument({ pack, frame, devices, digest } = {}) {
    if (pack?.siteId !== 'echo' || pack.schemaVersion !== 1 || pack.sceneAvailable !== true
        || typeof pack.revision !== 'string' || !pack.revision || !Number.isSafeInteger(pack.registrationVersion)
        || pack.registrationVersion < 0 || typeof digest !== 'function' || !Array.isArray(devices)
        || frame?.frame !== FRAME || (frame.site !== undefined && frame.site !== 'echo')
        || (frame.units !== undefined && frame.units !== 'm') || !finiteVector(frame.extents_m)) return null;
    const frameDigest = pack.assets?.['frame.json']?.sha256;
    const collisionDigest = pack.assets?.['collision.glb']?.sha256;
    if (!HASH.test(frameDigest || '') || !HASH.test(collisionDigest || '')) return null;
    const [width, depth, height] = frame.extents_m;
    if (width <= 0 || depth <= 0 || height < 1.8 || Math.hypot(width, depth) > 100) return null;
    const eligible = devices.filter(device => {
        if (device?.type !== 'light' || typeof device.id !== 'string' || !FIXTURE_ID.test(device.id)
            || String(device.ha_entity_id || '').startsWith('switch.') || !finiteVector(device.pos)) return false;
        const [x, y, z] = device.pos;
        return x >= 0 && x <= width && y >= 0 && y <= depth && z >= 1.8 && z <= height + .2
            && (device.height_preset === 'ceiling' || z >= 2);
    }).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    const distinct = new Map();
    for (const device of eligible) {
        if (distinct.has(device.id)) return null;
        distinct.set(device.id, device);
    }
    if (!distinct.size) return null;

    // Spread the four modeled spots over mapped rooms before filling spare
    // slots. The same immutable input gives the bake and the browser the same
    // fixture IDs, coordinates and order.
    const perRoom = new Map();
    for (const device of distinct.values()) {
        const roomId = device.room_id || null;
        if (!perRoom.has(roomId)) perRoom.set(roomId, device);
    }
    const roomLeaders = new Set(perRoom.values());
    const selected = [...roomLeaders, ...eligible.filter(device => !roomLeaders.has(device))].slice(0, MAX_FIXTURES);
    const equipment = selected.flatMap((device, index) => {
        const mountId = 'echo-mount-' + device.id;
        const label = 'Virtual spotlight ' + (index + 1);
        return [
            { id: mountId, kind: 'mount', label, position_m: device.pos.slice(), position_status: 'modeled',
                orientation: { status: 'missing', direction_unit: null } },
            { id: 'echo-virtual-' + device.id, kind: 'fixture', label, mounted_at_ref: mountId,
                position_m: null, position_status: 'missing', mount_offset_m: null,
                orientation: { status: 'approximate', direction_unit: [0, 0, -1] } },
        ];
    });
    const document = {
        schema: SCHEMA, version: 1, revision: 0,
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
            confirmed: false, provenance: { status: 'modeled', source: 'pack_seed_ceiling_fixtures' },
        },
        equipment,
    };
    return { ...document, digest: digest(document) };
}
