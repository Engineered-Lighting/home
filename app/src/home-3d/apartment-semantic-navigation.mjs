/*
 * Renderer-independent navigation for the Apartment accessibility surface.
 *
 * The WebGL overlay is intentionally not an accessibility tree. This module
 * projects the Apartment model into a small, immutable semantic snapshot that
 * a native DOM control can consume without knowing anything about THREE.js.
 * Rooms/devices come only from model.zones and model.devices. Known model site
 * ids are read solely as an exclusion set: homes/sites, aiming targets, Home
 * Assistant entity ids, and arbitrary provider metadata cannot become device
 * navigation targets by accident.
 */

const BIDI_AND_CONTROL_CHARACTERS = /[\u0000-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/gu;
const MARKUP = /<[^>]*>/gu;
const ANGLE_BRACKETS = /[<>]/gu;
const WHITESPACE = /\s+/gu;
const SITE_KINDS = new Set(['site', 'home site', 'home-site']);

function safeAccessibleText(value, { fallback = '', maxLength = 96 } = {}) {
    if (typeof value !== 'string' && typeof value !== 'number') return fallback;
    const plain = String(value)
        .normalize('NFKC')
        .replace(BIDI_AND_CONTROL_CHARACTERS, ' ')
        .replace(MARKUP, ' ')
        .replace(ANGLE_BRACKETS, ' ')
        .replace(WHITESPACE, ' ')
        .trim()
        .slice(0, Math.max(0, maxLength));
    return plain || fallback;
}

/* IDs remain data, never announcement copy. Keeping the model id intact is
 * what lets a semantic selection resolve to the same device/card as a canvas
 * pick. Invalid or duplicate ids cannot form stable targets and are omitted. */
function modelId(value) {
    if (typeof value !== 'string' && typeof value !== 'number') return null;
    const id = String(value).trim();
    return id && id.length <= 256 ? id : null;
}

function displayType(value) {
    return safeAccessibleText(value, { fallback: 'device', maxLength: 48 })
        .replace(/[_.-]+/gu, ' ')
        .replace(WHITESPACE, ' ')
        .trim()
        .toLocaleLowerCase('en-US') || 'device';
}

function titleCaseFirst(value) {
    if (!value) return value;
    return `${value.charAt(0).toLocaleUpperCase('en-US')}${value.slice(1)}`;
}

function stateCollection(states) {
    if (!states || typeof states !== 'object') return null;
    /* Passing the full registry is tolerated, while the documented API keeps
     * accepting its states map directly. */
    if (!(states instanceof Map) && !Array.isArray(states)
        && states.states && typeof states.states === 'object') {
        return states.states;
    }
    return states;
}

function stateForEntity(states, entityId) {
    if (!entityId || !states) return null;
    if (states instanceof Map) return states.get(entityId) ?? null;
    if (Array.isArray(states)) {
        return states.find((candidate) => candidate?.entity_id === entityId) ?? null;
    }
    return Object.prototype.hasOwnProperty.call(states, entityId) ? states[entityId] : null;
}

function hasUsablePosition(position) {
    if (!Array.isArray(position) || position.length < 2) return false;
    if (!Number.isFinite(position[0]) || !Number.isFinite(position[1])) return false;
    return position.length < 3 || position[2] == null || Number.isFinite(position[2]);
}

function isSiteRecord(candidate) {
    if (!candidate || typeof candidate !== 'object') return false;
    const kind = safeAccessibleText(
        candidate.type ?? candidate.kind ?? candidate.target_kind,
        { maxLength: 32 },
    ).toLocaleLowerCase('en-US');
    return SITE_KINDS.has(kind);
}

function siteIdsFromModel(source) {
    const ids = new Set();
    const selected = modelId(source?.selectedSiteId ?? source?.selected_site_id);
    if (selected) ids.add(selected);
    for (const site of (Array.isArray(source?.sites) ? source.sites : [])) {
        const id = modelId(site?.id ?? site?.siteId ?? site?.site_id);
        if (id) ids.add(id);
    }
    return ids;
}

function makeRoomTargets(zones) {
    const rooms = [];
    const namesById = new Map();
    const seen = new Set();

    for (const [index, zone] of (Array.isArray(zones) ? zones : []).entries()) {
        if (!zone || typeof zone !== 'object') continue;
        const id = modelId(zone.id);
        if (!id || seen.has(id)) continue;
        seen.add(id);

        const name = safeAccessibleText(zone.name, {
            fallback: `Room ${index + 1}`,
            maxLength: 80,
        });
        const room = Object.freeze({
            id,
            name,
            ariaLabel: safeAccessibleText(`Room: ${name}`, {
                fallback: `Room ${index + 1}`,
                maxLength: 128,
            }),
        });
        rooms.push(room);
        namesById.set(id, name);
    }

    return { rooms: Object.freeze(rooms), namesById };
}

function makeDeviceLabel({ name, room, type, state, controllable, positioned }) {
    return safeAccessibleText([
        name,
        `${type} in ${room}`,
        `state ${state}`,
        controllable ? 'controllable' : 'read only',
        positioned ? 'positioned in the apartment' : 'not positioned in the apartment',
    ].join('. '), { fallback: 'Apartment device', maxLength: 360 });
}

/**
 * Build the complete non-canvas navigation model.
 *
 * Order is the authoritative model order. That keeps focus stable across state
 * refreshes and makes next/previous selection deterministic without sorting on
 * mutable display names. A device needs a stable model id, but it does not need
 * a position: unpositioned devices remain reachable and are labelled honestly.
 *
 * @param {object} model Apartment model containing zones and devices arrays.
 * @param {object|Map|Array} states HA state map, full registry, Map, or state array.
 * @returns {{rooms: ReadonlyArray<object>, devices: ReadonlyArray<object>}}
 */
export function buildApartmentSemanticNavigation(model, states = {}) {
    const source = model && typeof model === 'object' ? model : {};
    const { rooms, namesById } = makeRoomTargets(source.zones);
    const siteIds = siteIdsFromModel(source);
    const collection = stateCollection(states);
    const devices = [];
    const seen = new Set();

    for (const [index, device] of (Array.isArray(source.devices) ? source.devices : []).entries()) {
        if (!device || typeof device !== 'object' || isSiteRecord(device)) continue;
        const id = modelId(device.id);
        if (!id || seen.has(id) || siteIds.has(id)) continue;
        seen.add(id);

        const entityId = typeof device.ha_entity_id === 'string'
            ? device.ha_entity_id.trim()
            : '';
        const entityState = stateForEntity(collection, entityId);
        const type = displayType(device.type);
        const name = safeAccessibleText(
            device.name ?? entityState?.attributes?.friendly_name,
            { fallback: `${titleCaseFirst(type)} ${index + 1}`, maxLength: 96 },
        );
        const roomId = modelId(device.room_id);
        const room = (roomId && namesById.get(roomId)) || 'Unassigned room';
        const state = safeAccessibleText(entityState?.state, {
            fallback: 'unknown',
            maxLength: 80,
        }).toLocaleLowerCase('en-US');
        const domain = entityId.includes('.') ? entityId.split('.', 1)[0] : '';
        const controllable = device.controllable === true
            || domain === 'light'
            || domain === 'switch'
            || domain === 'media_player'
            || type === 'camera';
        const positioned = hasUsablePosition(device.pos);

        /* This allowlist is a security boundary as well as a convenient view
         * model. In particular, ha_entity_id, siteId/site_id, coordinates, and
         * provider records can never escape into the DOM-facing snapshot. */
        const target = {
            id,
            name,
            room,
            type,
            state,
            controllable,
            positioned,
        };
        target.ariaLabel = makeDeviceLabel(target);
        devices.push(Object.freeze(target));
    }

    return Object.freeze({
        rooms,
        devices: Object.freeze(devices),
    });
}

function navigationIds(devices) {
    const ids = [];
    const seen = new Set();
    for (const device of Array.isArray(devices) ? devices : []) {
        const id = modelId(device?.id);
        if (!id || seen.has(id)) continue;
        seen.add(id);
        ids.push(id);
    }
    return ids;
}

/**
 * Select the adjacent device id in model order, wrapping at both ends.
 * Missing selection starts at the first item for next and the last for
 * previous. A negative number, "previous", or "prev" moves backward; all
 * other direction values move forward.
 */
export function nextApartmentDeviceId(devices, currentId, direction = 1) {
    const ids = navigationIds(devices);
    if (!ids.length) return null;

    const normalizedDirection = typeof direction === 'string'
        ? direction.trim().toLocaleLowerCase('en-US')
        : direction;
    const step = normalizedDirection === 'previous'
        || normalizedDirection === 'prev'
        || (typeof normalizedDirection === 'number' && normalizedDirection < 0)
        ? -1
        : 1;
    const current = modelId(currentId);
    const index = current == null ? -1 : ids.indexOf(current);
    if (index < 0) return step < 0 ? ids.at(-1) : ids[0];
    return ids[(index + step + ids.length) % ids.length];
}

/** Plain, bounded live-region copy for the current semantic selection. */
export function apartmentDeviceStatusText(devices, currentId) {
    const list = Array.isArray(devices) ? devices : [];
    if (!list.length) return 'No apartment devices available.';
    const current = modelId(currentId);
    const index = current == null ? -1 : list.findIndex((device) => modelId(device?.id) === current);
    if (index < 0) {
        return `${list.length} apartment device${list.length === 1 ? '' : 's'} available. No device selected.`;
    }
    const label = safeAccessibleText(list[index]?.ariaLabel, {
        fallback: 'Apartment device',
        maxLength: 360,
    });
    return safeAccessibleText(
        `Device ${index + 1} of ${list.length}. ${label}.`,
        { fallback: 'Apartment device selected.', maxLength: 420 },
    );
}
