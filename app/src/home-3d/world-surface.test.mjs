import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
    WORLD_CAMERA_BANDS,
    WORLD_CAMERA_BAND_ORDER,
    createIdentityRenderHolds,
    createLatestIntentAuthority,
    normalizeConfirmedAnchor,
    requireWorldCameraBand,
} from './world-surface-core.mjs';

const surfacePath = fileURLToPath(new URL('./world-surface.js', import.meta.url));
const surfaceSource = readFileSync(surfacePath, 'utf8');

test('camera bands form the reversible parcel-to-planet sequence', () => {
    assert.deepEqual(WORLD_CAMERA_BAND_ORDER, ['parcel', 'city', 'country', 'planet']);
    let previousRange = 0;
    for (const band of WORLD_CAMERA_BAND_ORDER) {
        const definition = WORLD_CAMERA_BANDS[band];
        assert.ok(Object.isFrozen(definition));
        assert.ok(definition.rangeMeters > previousRange);
        assert.ok(definition.durationMs > 0);
        previousRange = definition.rangeMeters;
    }
    assert.equal(WORLD_CAMERA_BANDS.parcel.representation, 'confirmed-anchor-only');
    assert.equal(requireWorldCameraBand('planet'), 'planet');
    assert.throws(() => requireWorldCameraBand('building'), /world camera band/);
});

test('anchors must be explicitly confirmed and geographically valid', () => {
    assert.throws(
        () => normalizeConfirmedAnchor({ latitude: 40, longitude: -75 }),
        /confirmed anchor/,
    );
    assert.throws(
        () => normalizeConfirmedAnchor({ confirmed: true, latitude: 91, longitude: 0 }),
        /latitude/,
    );
    assert.throws(
        () => normalizeConfirmedAnchor({ confirmed: true, latitude: 0, longitude: 181 }),
        /longitude/,
    );

    const input = {
        confirmed: true,
        latitude: 40.25,
        longitude: -74.75,
        altitudeMeters: 12,
    };
    const normalized = normalizeConfirmedAnchor(input);
    input.latitude = 0;
    assert.deepEqual(normalized, {
        latitude: 40.25,
        longitude: -74.75,
        altitudeMeters: 12,
    });
    assert.ok(Object.isFrozen(normalized));
});

test('latest call owns navigation and stale completion cannot retake it', () => {
    const authority = createLatestIntentAuthority();
    const first = authority.begin({ intentId: 'outward-1', band: 'country' });
    assert.equal(first.replaced, null);
    assert.ok(authority.isCurrent(first.token));

    const reverse = authority.begin({ intentId: 'reverse-2', band: 'parcel' });
    assert.equal(reverse.replaced, first.token);
    assert.ok(!authority.isCurrent(first.token));
    assert.ok(authority.isCurrent(reverse.token));
    assert.equal(authority.complete(first.token), false);
    assert.equal(authority.snapshot().activeIntentId, 'reverse-2');
    assert.equal(authority.complete(reverse.token), true);
    assert.equal(authority.snapshot().activeIntentId, null);
});

test('continuous-render holds are identity-keyed and idempotent', () => {
    const sizes = [];
    const holds = createIdentityRenderHolds((size) => sizes.push(size));
    const cameraFlight = {};
    const releaseFirst = holds.acquire(cameraFlight);
    const releaseDuplicate = holds.acquire(cameraFlight);

    assert.equal(holds.size(), 1);
    assert.deepEqual(sizes, [1]);
    assert.equal(releaseDuplicate(), true);
    assert.equal(releaseFirst(), false);
    assert.equal(holds.size(), 0);
    assert.deepEqual(sizes, [1, 0]);

    const providerMutation = Symbol('provider-mutation');
    holds.acquire(providerMutation);
    assert.equal(holds.release({}), false);
    assert.equal(holds.release(providerMutation), true);
});

test('surface module parses as ESM and is pinned to the reviewed implementation lessons', () => {
    const syntax = spawnSync(process.execPath, ['--input-type=module', '--check'], {
        encoding: 'utf8',
        input: surfaceSource,
    });
    assert.equal(syntax.status, 0, syntax.stderr || syntax.stdout);
    assert.match(surfaceSource, /d8f1742783cddd6bbc86033d0db06dc6ec746304/);
    assert.match(surfaceSource, /REQUIRED_CESIUM_VERSION = '1\.144\.0'/);
});

test('surface owns only a canvas layer and never replaces Apartment viewport content', () => {
    assert.match(surfaceSource, /host\.append\(nextSurface\)/);
    assert.match(surfaceSource, /surface\?\.remove\(\)/);
    assert.doesNotMatch(surfaceSource, /replaceChildren|innerHTML|outerHTML/);
    assert.doesNotMatch(surfaceSource, /createElement\(['"](?:button|form|input|select|textarea|nav|aside)['"]\)/);
    assert.match(surfaceSource, /screenSpaceCameraController\.enableInputs = false/);
});

test('default rendering path is local-only and makes no provider request', () => {
    assert.match(surfaceSource, /\.\.\/spatial-spike\/vendor\/cesium\//);
    assert.match(surfaceSource, /\.\.\/spatial-spike\/vendor\/fixtures\/offline-planet\.png/);
    assert.match(surfaceSource, /baseLayer: false/);
    assert.match(surfaceSource, /SingleTileImageryProvider\.fromUrl/);
    assert.doesNotMatch(surfaceSource, /\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource/);
    assert.doesNotMatch(surfaceSource, /https?:\/\//i);
    assert.doesNotMatch(surfaceSource, /OpenStreetMap|BingMaps|GoogleMaps|ArcGis/i);
});

test('parcel rendering explicitly refuses geometry and accuracy claims', () => {
    assert.match(surfaceSource, /parcelGeometry: 'not-provided'/);
    assert.match(surfaceSource, /No parcel outline,[\s\S]*accuracy claim is synthesized/);
    assert.match(surfaceSource, /imagery: 'bundled-offline-planet'/);
    assert.match(surfaceSource, /terrain: 'ellipsoid'/);
});
