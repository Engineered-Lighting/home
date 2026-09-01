/**
 * Renderer-independent state for the Apartment viewport's outer-world surface.
 *
 * This module deliberately has no DOM, Cesium, provider, or product-shell
 * dependencies.  Keeping intent authority and render holds here makes their
 * cancellation semantics deterministic enough to test without a GPU.
 */

const CAMERA_BAND_DEFINITIONS = {
    parcel: {
        rangeMeters: 420,
        pitchDegrees: -68,
        durationMs: 850,
        representation: 'confirmed-anchor-only',
    },
    city: {
        rangeMeters: 42_000,
        pitchDegrees: -80,
        durationMs: 1_050,
        representation: 'offline-planet',
    },
    country: {
        rangeMeters: 1_650_000,
        pitchDegrees: -88,
        durationMs: 1_250,
        representation: 'offline-planet',
    },
    planet: {
        rangeMeters: 18_000_000,
        pitchDegrees: -90,
        durationMs: 1_450,
        representation: 'offline-planet',
    },
};

export const WORLD_CAMERA_BAND_ORDER = Object.freeze([
    'parcel',
    'city',
    'country',
    'planet',
]);

export const WORLD_CAMERA_BANDS = Object.freeze(Object.fromEntries(
    Object.entries(CAMERA_BAND_DEFINITIONS).map(([name, definition]) => [
        name,
        Object.freeze({ ...definition }),
    ]),
));

export function requireWorldCameraBand(value) {
    if (typeof value !== 'string' || !Object.hasOwn(WORLD_CAMERA_BANDS, value)) {
        throw new TypeError(`world camera band must be one of: ${WORLD_CAMERA_BAND_ORDER.join(', ')}`);
    }
    return value;
}

export function normalizeConfirmedAnchor(value) {
    if (!value || typeof value !== 'object' || value.confirmed !== true) {
        throw new TypeError('world surface requires a confirmed anchor');
    }

    const latitude = Number(value.latitude);
    const longitude = Number(value.longitude);
    const altitudeMeters = value.altitudeMeters == null ? 0 : Number(value.altitudeMeters);

    if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
        throw new RangeError('confirmed anchor latitude is outside the supported range');
    }
    if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
        throw new RangeError('confirmed anchor longitude is outside the supported range');
    }
    if (!Number.isFinite(altitudeMeters) || altitudeMeters < -500 || altitudeMeters > 10_000) {
        throw new RangeError('confirmed anchor altitude is outside the supported range');
    }

    return Object.freeze({ latitude, longitude, altitudeMeters });
}

/**
 * Monotonic navigation authority.  Intent IDs are opaque caller data; recency
 * is determined by call order so a delayed completion can never regain camera
 * ownership.  begin() returns the exact token it replaced for honest promise
 * settlement by the renderer adapter.
 */
export function createLatestIntentAuthority() {
    let sequence = 0;
    let active = null;

    return Object.freeze({
        begin({ intentId, band }) {
            if (typeof intentId !== 'string' || intentId.trim() === '') {
                throw new TypeError('navigation intentId must be a non-empty string');
            }
            requireWorldCameraBand(band);
            const replaced = active;
            const token = Object.freeze({
                sequence: ++sequence,
                intentId,
                band,
            });
            active = token;
            return Object.freeze({ token, replaced });
        },

        isCurrent(token) {
            return active === token;
        },

        complete(token) {
            if (active !== token) return false;
            active = null;
            return true;
        },

        invalidate() {
            const replaced = active;
            active = null;
            sequence += 1;
            return replaced;
        },

        snapshot() {
            return Object.freeze({
                sequence,
                activeIntentId: active?.intentId ?? null,
                activeBand: active?.band ?? null,
            });
        },
    });
}

/**
 * A Set, rather than a counter, makes continuous-render reasons idempotent.
 * The same identity cannot accidentally increment twice, and only the exact
 * identity can release its hold.
 */
export function createIdentityRenderHolds(onChange = () => {}) {
    if (typeof onChange !== 'function') {
        throw new TypeError('render hold change observer must be a function');
    }
    const identities = new Set();

    function notify() {
        onChange(identities.size);
    }

    return Object.freeze({
        acquire(identity) {
            if (identity == null) throw new TypeError('render hold identity is required');
            const before = identities.size;
            identities.add(identity);
            if (identities.size !== before) notify();
            let released = false;
            return () => {
                if (released) return false;
                released = true;
                const didRelease = identities.delete(identity);
                if (didRelease) notify();
                return didRelease;
            };
        },

        release(identity) {
            const didRelease = identities.delete(identity);
            if (didRelease) notify();
            return didRelease;
        },

        clear() {
            if (identities.size === 0) return false;
            identities.clear();
            notify();
            return true;
        },

        has(identity) {
            return identities.has(identity);
        },

        size() {
            return identities.size;
        },
    });
}
